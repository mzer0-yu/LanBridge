"""Browser consent via the official Cloudflare CLI; secrets never enter UI state."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from datetime import datetime
import httpx

from .local_login import available_browsers, open_browser
from .store import protect_directory
from .service import digest
from .models import Settings

VERSION = "1.0.0-beta.12"
SCOPES = ["account:read", "argotunnel.write", "teams-connector-cloudflared.write", "dns.write", "zone.read", "zone.write", "challenge-widgets.write"]

WAF_SCOPE = "zone-waf.write"

REFRESH_REASONS = {
    "invalid_grant": "Cloudflare 拒绝了刷新凭据，需要重新授权。",
    "invalid_client": "Cloudflare 拒绝了授权客户端，需要重新授权。",
    "network": "续期请求遇到网络连接错误，可稍后重试。",
    "timeout": "续期请求超时，可稍后重试。",
    "cli_failure": "官方授权工具执行失败，尚不能判断刷新凭据是否失效。",
    "profile_invalid": "官方工具未返回有效的新授权，尚不能判断刷新凭据是否失效。",
}

class OAuthContextChanged(ValueError):
    pass


class BrowserAuth:
    def __init__(self, service):
        self.service = service
        self.browser = "default"
        self.require_waf = False
        self.thread = None
        self.process = None
        self.gate = threading.Lock()
        self.cancelled = threading.Event()
        self.selection_ready = threading.Event()
        self.zone_choices = []
        self.selected_zone = None
        self.setup_context = service.store.get('browser_auth_setup_context')
        previous = service.store.get("browser_auth_job", {})
        if previous.get('authorization_saved') and previous.get('phase') == 'creating' and self.setup_context == self._setup_context():
            self.update('error', 'Cloudflare 授权已保存，自动配置尚未完成，可继续配置。',
                        authorization_saved=True, next_action='configure_tunnel')
        elif previous.get("phase") in ("preparing", "authorizing", "choosing_zone", "creating", "cancelling"):
            self.update("error", "上次浏览器授权被服务重启中断。若已有创建结果未知记录，请先在 Cloudflare 核对。")
        elif previous.get("next_action") == "token_authority":
            self.update("error", "旧流程在令牌转授阶段失败。现在直接使用浏览器授权，请重新授权一次完成接入，无需另行提供授权令牌。", next_action="reauthorize")

    def update(self, phase, message, **diagnostics):
        self.service.store.set("browser_auth_job", {"phase": phase, "message": message, "updated_at": time.time(), "browser": self.browser, "require_waf": self.require_waf, **diagnostics})

    def status(self):
        return self.service.store.get("browser_auth_job", {"phase": "idle", "message": "在 Cloudflare 官方页面授权一次，自动接入并刷新凭据，无需粘贴令牌。"})

    @staticmethod
    def node_runtime(project):
        candidates = [project / "bin" / "node-runtime" / ("node.exe" if os.name == "nt" else "node")]
        system = shutil.which("node")
        if system:
            candidates.append(Path(system))
        if os.name == "nt":
            for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
                if os.environ.get(key):
                    candidates.append(Path(os.environ[key]) / ("Programs/nodejs" if key == "LOCALAPPDATA" else "nodejs") / "node.exe")
        env = {k:v for k,v in os.environ.items() if k not in ("NODE_OPTIONS", "NODE_PATH")}
        found = False
        for node in dict.fromkeys(candidates):
            if not node.is_file():
                continue
            found = True
            try:
                result = subprocess.run([str(node), "--version"], capture_output=True, timeout=10,
                    env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                match = re.fullmatch(rb"v(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
                if result.returncode == 0 and match and tuple(map(int, match.groups())) >= (22, 18, 0):
                    return str(node)
            except (OSError, subprocess.TimeoutExpired):
                continue
        if found:
            raise ValueError("未找到可运行的 Node.js 22.18 或更高版本，请更新 Node.js，或在 bin/node-runtime 放置兼容运行时后重试。")
        raise ValueError("未找到 Node.js 运行时，请安装 Node.js 22.18 或更高版本，或在 bin/node-runtime 放置兼容运行时后重试。")

    def command(self):
        project = Path(__file__).resolve().parents[1]
        node = self.node_runtime(project)
        root = project / "bin" / "cf-runtime"
        entry = root / "node_modules" / "cf" / "bin" / "cf"
        if not entry.is_file():
            root.mkdir(parents=True, exist_ok=True)
            npm = shutil.which("npm.cmd") or shutil.which("npm")
            pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
            fallback = Path(node).parents[2] / "bin" / "fallback" / "pnpm.cmd"
            if not pnpm and fallback.is_file():
                pnpm = str(fallback)
            if npm:
                args = [npm, "install", "--prefix", str(root), "--ignore-scripts", "--registry=https://registry.npmjs.org", "cf@" + VERSION]
            elif pnpm:
                args = [pnpm, "--dir", str(root), "add", "cf@" + VERSION, "--ignore-scripts", "--registry=https://registry.npmjs.org"]
            else:
                raise ValueError("未找到 npm/pnpm，请安装 Node.js 工具链后重试。")
            result = subprocess.run(args, capture_output=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode or not entry.is_file():
                raise ValueError("官方 Cloudflare CLI 安装失败，请检查到 npm 官方源的网络后重试。")
        metadata = json.loads((entry.parents[1] / "package.json").read_text(encoding="utf-8"))
        if metadata.get("name") != "cf" or metadata.get("version") != VERSION:
            raise ValueError("Cloudflare CLI 版本不匹配，请重新安装本平台工具。")
        return [node, str(entry)]

    @staticmethod
    def environment(directory, account):
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(("CLOUDFLARE_", "WRANGLER_", "CF_")) or key in ("NODE_OPTIONS", "DEBUG"):
                env.pop(key, None)
        env.update(XDG_CONFIG_HOME=str(directory), XDG_CACHE_HOME=str(directory / "cache"),
                   APPDATA=str(directory), LOCALAPPDATA=str(directory),
                   CLOUDFLARE_AUTH_USE_KEYRING="false", CLOUDFLARE_ACCOUNT_ID=account, DO_NOT_TRACK="1", CI="1", NO_COLOR="1")
        if not account:
            env.pop("CLOUDFLARE_ACCOUNT_ID", None)
        return env

    def communicate_browser(self, process, browser, timeout):
        """Consume CLI output privately, opening only its official OAuth URL."""
        output = [bytearray(), bytearray()]
        errors = []
        opened = threading.Event()
        launch_lock = threading.Lock()

        def read(stream, index):
            try:
                for line in iter(stream.readline, b""):
                    output[index].extend(line[:max(0, 65536-len(output[index]))])
                    match = re.search(rb"https://dash\.cloudflare\.com/oauth2/auth\?[^\s\x1b]+", line)
                    if match:
                        with launch_lock:
                            if self.cancelled.is_set() or opened.is_set():
                                continue
                            opened.set()
                            if not open_browser(match.group().decode("utf-8"), browser):
                                errors.append("所选浏览器未能打开，请检查安装后重新授权。")
                                process.kill()
            except (OSError, ValueError, UnicodeError):
                errors.append("无法打开授权页，请重新授权。")
                try:
                    process.kill()
                except OSError:
                    pass
            finally:
                stream.close()

        readers = [threading.Thread(target=read, args=(stream, i), daemon=True) for i, stream in enumerate((process.stdout, process.stderr))]
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            for reader in readers:
                reader.join(timeout=2)
            raise
        for reader in readers:
            reader.join(timeout=2)
        if errors:
            raise ValueError(errors[0])
        if process.returncode == 0 and not opened.is_set() and not self.cancelled.is_set():
            raise ValueError("官方工具未返回授权地址，请重新授权。")
        return bytes(output[0]), bytes(output[1])

    def run(self, command, env, cwd, args, timeout=45):
        if self.cancelled.is_set():
            raise ValueError("浏览器授权已取消。")
        self.process = subprocess.Popen(command + args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            if self.cancelled.is_set():
                self.process.kill()
            if args[:2] == ["auth", "create"] and "--no-browser" in args:
                out, err = self.communicate_browser(self.process, self.browser, timeout)
            else:
                out, err = self.process.communicate(timeout=timeout)
            if getattr(self, 'refresh_diagnostics', False):
                self.refresh_reason = self.classify_refresh_error(out + err)
            if self.process.returncode or self.cancelled.is_set():
                if args[:2] == ["auth", "create"] and not self.cancelled.is_set():
                    output = (out + err).decode("utf-8", errors="replace").lower()
                    if "timed out waiting for authorization code" in output:
                        raise ValueError("浏览器授权已超时：官方 CLI 的本机回调等待 2 分钟，8877 端口已关闭。未创建令牌；请关闭旧授权页，回到本平台重新发起授权，勿刷新旧回调地址。")
                raise ValueError("Cloudflare 授权或 API 操作失败，请检查浏览器授权、账户角色及网络后重试。")
            return out.decode("utf-8", errors="replace").strip()
        except subprocess.TimeoutExpired:
            if getattr(self, 'refresh_diagnostics', False):
                self.refresh_reason = 'timeout'
            self.process.kill()
            self.process.communicate()
            if args[:2] == ["auth", "create"]:
                raise ValueError("浏览器授权等待已结束，本机回调端口已关闭。未创建令牌；请回到本平台重新发起授权，勿刷新旧回调地址。") from None
            raise ValueError("Cloudflare 操作超时；若处于创建阶段，请先在 Cloudflare 核对结果，勿重复创建。") from None
        except BaseException:
            # A pipe/reader failure must not leave an untracked authorization process.
            if self.process.returncode is None:
                try:
                    self.process.kill()
                    self.process.wait(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
            raise
        finally:
            process, self.process = self.process, None
            if process.returncode is not None:
                for stream in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass

    @staticmethod
    def classify_refresh_error(output):
        text = output.decode('utf-8', errors='replace').lower()
        for code in ('invalid_grant', 'invalid_client'):
            if re.search(r'oauth error:\s*' + code + r'\b', text):
                return code
        if any(code in text for code in ('fetch failed', 'enotfound', 'econnreset', 'econnrefused', 'certificate')):
            return 'network'
        if any(code in text for code in ('etimedout', 'connect timeout', 'request timed out')):
            return 'timeout'
        return None

    @staticmethod
    def payload(output):
        try:
            value = json.loads(output)
        except ValueError:
            raise ValueError("Cloudflare CLI 响应格式异常，请在 Cloudflare 核对结果。") from None
        if isinstance(value, dict) and "result" in value:
            value = value["result"]
        return value

    def start(self, browser="default", *, require_waf=False):
        if type(require_waf) is not bool:
            raise ValueError("WAF 授权选项必须为布尔值")
        if not isinstance(browser, str) or browser not in {item["id"] for item in available_browsers()}:
            raise ValueError("请选择已安装的浏览器")
        with self.gate, self.service.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("浏览器授权正在进行，请完成当前操作。")
            if self.service.store.get("pending_browser_token") or self.service.store.get("pending_business_token"):
                raise ValueError("先前令牌创建结果未知，请在 Cloudflare 核对并通过手动配置接入，平台不会重复创建。")
            cfg = self.service.settings()
            self.browser = browser
            self.require_waf = require_waf
            self.cancelled.clear()
            self.selection_ready.clear()
            self.zone_choices = []
            self.selected_zone = None
            self.setup_context = None
            self.service.store.set('browser_auth_setup_context', None)
            self.update("preparing", "正在准备官方 Cloudflare 授权工具…")
            cfg = cfg | {"credential_digest": digest(self.service.store.secret("cf_write_token"))}
            self.thread = threading.Thread(target=self.worker, args=(cfg,), daemon=True)
            self.thread.start()
            return self.status()

    def cancel(self):
        with self.gate:
            phase = self.status().get("phase")
            if phase not in ("authorizing", "choosing_zone"):
                if phase in ("idle", "done", "error", "cancelled"):
                    return self.status()
                raise ValueError("当前正在准备或核验授权，请稍候；浏览器等待阶段可以立即取消。")
            self.cancelled.set()
            self.selection_ready.set()
            self.update("cancelling", "正在结束本次授权并释放本机回调端口…")
            process = self.process
            if process and process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            if self.thread and self.thread is not threading.current_thread():
                self.thread.join(timeout=3)
            if not self.thread or not self.thread.is_alive():
                self.update("cancelled", "已取消本次授权，原凭据保持不变。现在可以立即重新授权，无需等待超时。")
            return self.status()

    def restart(self, browser=None):
        browser = self.browser if browser is None else browser
        if not isinstance(browser, str) or browser not in {item["id"] for item in available_browsers()}:
            raise ValueError("请选择已安装的浏览器")
        if self.status().get("phase") == "authorizing":
            self.cancel()
        return self.start(browser, require_waf=self.require_waf)

    def stop(self):
        self.cancelled.set()
        self.selection_ready.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()

    def _setup_context(self):
        cfg = self.service.settings()
        return digest([cfg['account_id'], cfg['zone_id'], self.service.store.secret('cf_write_token')])

    def continue_setup(self):
        with self.gate, self.service.lock:
            job = self.status()
            if self.thread and self.thread.is_alive():
                raise ValueError("自动配置正在进行，请稍候")
            if job.get('next_action') != 'configure_tunnel' or not self.setup_context or self.setup_context != self._setup_context():
                raise ValueError("授权或账户已变化，请重新授权或使用隧道令牌中的配置入口")
            self.update('creating', '授权已保存，正在继续配置隧道…', authorization_saved=True)
            self.thread = threading.Thread(target=self.complete_setup, daemon=True)
            self.thread.start()
            return self.status()

    def complete_setup(self):
        try:
            with self.service.lock:
                if not self.setup_context or self.setup_context != self._setup_context():
                    raise ValueError('账户或授权已变化，已停止自动配置')
                cfg = self.service.settings()
                if not cfg['tunnel_id'] or not self.service.store.secret('tunnel_token'):
                    self.update('creating', '授权已保存，正在配置专用隧道并保存连接令牌…', authorization_saved=True)
                    self.service.cf.create_tunnel()
                if any(site['enabled'] and site['human_check'] for site in self.service.sites()):
                    self.update('creating', '隧道已配置，正在同步人类验证…', authorization_saved=True)
                    self.service.cf.create_widget()
            self.update('done', '本次授权已完成，隧道连接令牌已保存。', authorization_saved=True, tunnel_ready=True)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '请检查网络及本机存储后重试。'
            self.update('error', 'Cloudflare 授权已保存，但自动配置未完成：' + detail,
                        authorization_saved=True, next_action='configure_tunnel')

    def worker(self, cfg):
        store = self.service.store
        command = None
        diagnostics = {}
        try:
            command = self.command()
            auth_root = store.root / "browser-auth"
            protect_directory(auth_root)
            with tempfile.TemporaryDirectory(prefix="session-", dir=auth_root) as name:
                directory = Path(name)
                env = self.environment(directory, cfg["account_id"])
                scopes = SCOPES + ([WAF_SCOPE] if self.require_waf or WAF_SCOPE in (store.get("managed_business_token") or {}).get("scopes", []) else [])
                self.update("authorizing", "正在打开所选浏览器，请确认 Cloudflare 授权。误关页面可重新打开授权页。")
                self.run(command, env, directory, ["auth", "create", "lanbridge", "--no-device", *(["--no-browser"] if self.browser != "default" else []), "--scopes", *scopes], timeout=180)
                self.update("creating", "授权已完成，正在核对权限和域名归属…")
                identity = self.payload(self.run(command, env, directory, ["auth", "whoami", "--profile", "lanbridge"]))
                granted = identity.get("scopes") if isinstance(identity, dict) else None
                if not isinstance(granted, list) or any(not isinstance(scope, str) for scope in granted):
                    raise ValueError("无法核对浏览器实际授予的权限范围；请重新发起浏览器授权。")
                # Retain only our known scope names, never the identity/email or raw CLI output.
                diagnostics = {"granted_scopes": [scope for scope in SCOPES + [WAF_SCOPE] if scope in granted]}
                needed = {"dns.write", "zone.read", "zone.write", "challenge-widgets.write"}
                if self.require_waf:
                    needed.add(WAF_SCOPE)
                missing = needed.difference(granted)
                if not any(scope in granted for scope in ("argotunnel.write", "teams-connector-cloudflared.write")):
                    missing.add("Tunnel Write")
                if missing:
                    diagnostics["next_action"] = "reauthorize"
                    raise ValueError("浏览器实际未授予所需范围：" + "、".join(sorted(missing)) + "。未创建令牌；请仅核对这些权限后重新授权。")
                snapshot = self.read_profile(directory)
                if self.require_waf and WAF_SCOPE not in snapshot["profile"]["scopes"]:
                    raise ValueError("授权凭据未包含 zone-waf.write，原授权保持不变；请重新补充 WAF 授权。")
                original = cfg.copy()
                if not all(re.fullmatch(r"[a-fA-F0-9]{32}", cfg[k]) for k in ("account_id", "zone_id")) or not cfg["zone_name"]:
                    choices = self.discover_zones(snapshot["profile"]["oauth_token"])
                    choices = [z for z in choices if (not cfg["account_id"] or z["account_id"] == cfg["account_id"]) and (not cfg["zone_id"] or z["zone_id"] == cfg["zone_id"]) and (not cfg["zone_name"] or z["zone_name"] == cfg["zone_name"]) and all(s["hostname"] == z["zone_name"] or s["hostname"].endswith('.'+z["zone_name"]) for s in self.service.sites())]
                    if not choices:
                        raise ValueError("没有匹配当前配置的 Active 域名，请在 Cloudflare 接入域名或核对授权资源。")
                    if len(choices) == 1:
                        selected = choices[0]
                    else:
                        with self.gate:
                            self.zone_choices = choices
                            self.update("choosing_zone", "授权已完成，请选择用于网站转发的域名。", zones=choices)
                        if not self.selection_ready.wait(timeout=600) or self.cancelled.is_set():
                            raise ValueError("域名选择已取消或超时，请重新授权。")
                        selected = self.selected_zone
                        if selected is None:
                            raise ValueError("域名选择无效，请重新授权。")
                    cfg = cfg | {k: selected[k] for k in ("account_id", "zone_id", "zone_name")}
                    self.update("creating", "正在自动保存账户与域名配置…")
                self.validate_zone(snapshot["profile"]["oauth_token"], cfg)
                with self.service.lock:
                    if self.cancelled.is_set():
                        raise ValueError("浏览器授权已取消，原凭据保持不变。")
                    current = self.service.settings()
                    if any(current[k] != original[k] for k in ("account_id", "zone_id", "zone_name")) or digest(store.secret("cf_write_token")) != cfg["credential_digest"]:
                        raise ValueError("账户或凭据已变化，已停止配置；请重新授权。")
                    if original["account_id"] and cfg["account_id"] != original["account_id"] and current["tunnel_id"]:
                        raise ValueError("已有隧道属于其他账户，已停止配置。")
                    settings = Settings(**(current | {k: cfg[k] for k in ("account_id", "zone_id", "zone_name")})).model_dump()
                    self.save_profile(snapshot, settings, settings=settings)
                    store.audit("browser_oauth_connected", {"account_id": cfg["account_id"], "zone_id": cfg["zone_id"]})
                    self.setup_context = self._setup_context()
                    store.set('browser_auth_setup_context', self.setup_context)
                self.complete_setup()
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, sqlite3.Error) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "浏览器授权失败，请检查 Node.js、官方 CLI 安装和网络后重试。"
            if store.get("pending_browser_token"):
                message += " 创建结果未知：请在 Cloudflare 账户 API Tokens 核对 " + store.get("pending_browser_token")["name"] + "。"
            if self.cancelled.is_set():
                self.update("cancelled", "已取消本次授权，原凭据保持不变。现在可以立即重新授权，无需等待超时。")
            else:
                self.update("error", message, **diagnostics)

    def choose_zone(self, zone_id):
        with self.gate:
            if self.status().get("phase") != "choosing_zone" or not self.thread or not self.thread.is_alive():
                raise ValueError("当前没有等待选择的域名，请刷新状态。")
            choice = next((z for z in self.zone_choices if z["zone_id"] == zone_id), None)
            if not choice:
                raise ValueError("请选择已授权的域名。")
            self.selected_zone = choice
            self.update("creating", "正在保存所选域名…")
            self.selection_ready.set()
            return self.status()

    @staticmethod
    def discover_zones(token):
        zones = []
        try:
            with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
                for page in range(1, 101):
                    response = client.get("https://api.cloudflare.com/client/v4/zones", params={"status":"active", "per_page":50, "page":page}, headers={"Authorization":"Bearer " + token})
                    payload = response.json()
                    if response.status_code != 200 or not isinstance(payload, dict) or not payload.get("success") or not isinstance(payload.get("result"), list):
                        raise ValueError("授权无法读取域名列表，请核对 Zone Read 权限和账户角色。")
                    for zone in payload["result"]:
                        account = zone.get("account") or {}
                        if zone.get("status") == "active" and re.fullmatch(r"[a-fA-F0-9]{32}", zone.get("id", "")) and re.fullmatch(r"[a-fA-F0-9]{32}", account.get("id", "")) and isinstance(zone.get("name"), str):
                            zones.append({"zone_id":zone["id"], "zone_name":zone["name"], "account_id":account["id"], "account_name":str(account.get("name") or "")[:160]})
                    total_pages = (payload.get("result_info") or {}).get("total_pages")
                    if (isinstance(total_pages, int) and page >= total_pages) or len(payload["result"]) < 50:
                        return sorted(zones, key=lambda z:(z["zone_name"],z["account_id"]))
                raise ValueError("域名数量过多，请先限定账户再授权。")
        except (httpx.HTTPError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
            raise ValueError("读取授权域名失败，请检查网络后重新授权。") from None

    @staticmethod
    def read_profile(directory):
        candidates = []
        for path in directory.rglob("lanbridge.json"):
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                continue
            try:
                profile = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if isinstance(profile, dict) and isinstance(profile.get("oauth_token"), str):
                candidates.append({"path": path.relative_to(directory).as_posix(), "profile": profile})
        if len(candidates) != 1:
            raise ValueError("无法读取官方 CLI 的独立授权配置；原凭据保持不变。")
        result = candidates[0]
        profile = result["profile"]
        if not all(isinstance(profile.get(k), str) and profile[k] for k in ("oauth_token", "refresh_token", "expiration_time")):
            raise ValueError("官方 CLI 未返回完整的可刷新授权；请重新授权。")
        scopes = profile.get("scopes")
        if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
            raise ValueError("官方 CLI 授权权限格式无效，请重新授权。")
        if not {"dns.write", "zone.read"}.issubset(scopes) or not any(s in scopes for s in ("argotunnel.write", "teams-connector-cloudflared.write")):
            raise ValueError("授权配置缺少 Tunnel、DNS 或 Zone 权限，请重新授权。")
        try:
            if datetime.fromisoformat(profile["expiration_time"].replace("Z", "+00:00")).timestamp() <= time.time():
                raise ValueError("授权已过期")
        except ValueError:
            raise ValueError("官方 CLI 授权已过期或有效期无效，请重新授权。") from None
        result["profile"] = {k: profile[k] for k in ("oauth_token", "refresh_token", "expiration_time", "scopes")}
        return result

    @staticmethod
    def validate_zone(token, cfg):
        try:
            with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
                response = client.get("https://api.cloudflare.com/client/v4/zones/" + cfg["zone_id"], headers={"Authorization": "Bearer " + token})
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("授权核验响应无效，请重试浏览器授权。")
            result = payload.get("result") or {}
            if not isinstance(result, dict) or not isinstance(result.get("account", {}), dict):
                raise ValueError("授权核验响应无效，请重试浏览器授权。")
            if response.status_code != 200 or not payload.get("success"):
                raise ValueError("浏览器授权无法读取配置的 Zone，请核对授权资源和账户角色。")
            if result.get("name") != cfg["zone_name"] or result.get("account", {}).get("id") != cfg["account_id"] or result.get("status") != "active":
                raise ValueError("配置的 Zone 名称、账户归属或 Active 状态不匹配；原凭据保持不变。")
        except (httpx.HTTPError, KeyError, TypeError):
            raise ValueError("授权核验网络异常，请重试浏览器授权。") from None

    def save_profile(self, snapshot, cfg, *, settings=None):
        store = self.service.store
        token = snapshot["profile"]["oauth_token"]
        owned = {"kind": "oauth", "account_id": cfg["account_id"], "zone_id": cfg["zone_id"], "updated_at": time.time(), "credential_digest": digest(token), "scopes": [s for s in SCOPES + [WAF_SCOPE] if s in snapshot["profile"]["scopes"]]}
        with store.lock:
            values = {"managed_business_token": owned,
                      "credential_updated_at": store.get("credential_updated_at", {}) | {"cf_write_token": time.time()},
                      "oauth_refresh_issue": None}
            if settings is not None:
                values.update(settings=settings, token_management_error=None)
            store.set_many(values, secret_values={"cf_write_token": token, "cf_oauth_profile": json.dumps(snapshot)})

    def access_token(self, *, force_refresh=False):
        with self.service.lock:
            store = self.service.store
            owned = store.get("managed_business_token") or {}
            cfg = self.service.settings()
            if any(owned.get(k) != cfg[k] for k in ("account_id", "zone_id")):
                raise ValueError("浏览器授权绑定的账户或 Zone 已变化，请重新授权。")
            try:
                snapshot = json.loads(store.secret("cf_oauth_profile"))
                expires = datetime.fromisoformat(snapshot["profile"]["expiration_time"].replace("Z", "+00:00")).timestamp()
            except (ValueError, KeyError, TypeError):
                raise ValueError("浏览器授权配置无效，请重新授权。") from None
            if not force_refresh and expires > time.time() + 90:
                return snapshot["profile"]["oauth_token"]
            issue = store.get('oauth_refresh_issue') or {}
            if not force_refresh and issue.get('credential_digest') == digest(snapshot['profile']['oauth_token']) and issue.get('context') == [cfg['account_id'], cfg['zone_id']] and issue.get('retry_after', 0) > time.time():
                if expires > time.time() + 30:
                    return snapshot['profile']['oauth_token']
                raise ValueError(issue['message'])
            # Independent runner: refresh cannot overwrite/cancel an ongoing login process.
            runner = BrowserAuth.__new__(BrowserAuth)
            runner.service, runner.process, runner.cancelled = self.service, None, threading.Event()
            runner.refresh_diagnostics, runner.refresh_reason = True, None
            root = store.root / "browser-auth"
            protect_directory(root)
            try:
                with tempfile.TemporaryDirectory(prefix="refresh-", dir=root) as name:
                    directory = Path(name)
                    path = directory / snapshot["path"]
                    if not path.resolve().is_relative_to(directory.resolve()) or path.name != "lanbridge.json":
                        raise ValueError("授权配置路径无效")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    # whoami refreshes only expired profiles. Expire the isolated copy
                    # so early renewal and an explicit retry cannot silently reuse it.
                    profile = snapshot['profile'] | {'expiration_time':'1970-01-01T00:00:00Z'}
                    path.write_text(json.dumps(profile), encoding="utf-8")
                    env = runner.environment(directory, cfg['account_id'])
                    env['DEBUG'] = '1'  # Private capture; retain only fixed reason codes.
                    runner.run(runner.command(), env, directory, ["auth", "whoami", "--profile", "lanbridge"])
                    runner.refresh_reason = runner.refresh_reason or 'profile_invalid'
                    refreshed = runner.read_profile(directory)
                    expiry = datetime.fromisoformat(refreshed["profile"]["expiration_time"].replace("Z", "+00:00")).timestamp()
                    if expiry <= time.time() + 30:
                        raise ValueError("授权未刷新")
                    with store.lock:
                        current = self.service.settings()
                        if json.loads(store.secret('cf_oauth_profile') or '{}') != snapshot or any(current[k] != cfg[k] for k in ('account_id','zone_id')):
                            raise OAuthContextChanged('续期期间授权或账户已变化，请使用当前授权重试')
                        self.save_profile(refreshed, cfg)
                    return refreshed["profile"]["oauth_token"]
            except OAuthContextChanged:
                raise
            except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
                # A failed early renewal does not invalidate a still usable access token.
                usable = expires > time.time() + 30
                message = ("Cloudflare 管理授权暂未续期，当前授权仍可使用。请检查网络后重试续期，仍失败再重新授权。"
                           if usable else "Cloudflare 管理授权未能自动续期，暂时无法执行云端管理操作。请先检查网络并重试续期，仍失败再重新授权。")
                message += " LanBridge 管理员登录不受影响，已运行的转发不会因此停止。"
                reason = runner.refresh_reason or 'cli_failure'
                message += ' ' + REFRESH_REASONS[reason]
                with store.lock:
                    current = self.service.settings()
                    if json.loads(store.secret('cf_oauth_profile') or '{}') != snapshot or any(current[k] != cfg[k] for k in ('account_id','zone_id')):
                        raise OAuthContextChanged('续期期间授权或账户已变化，请使用当前授权重试') from None
                    store.set("oauth_refresh_issue", {"message": message, "expires_at": expires,
                              "reason": reason, "retry_after": time.time()+30,
                              "checked_at": time.time(), "retry_failed": bool(force_refresh or (store.get("oauth_refresh_issue") or {}).get("retry_failed")), "context": [cfg["account_id"], cfg["zone_id"]],
                              "credential_digest": digest(snapshot["profile"]["oauth_token"])})
                    store.audit('oauth_refresh_failed', {'reason':reason, 'manual_retry':force_refresh})
                if usable and not force_refresh:
                    return snapshot["profile"]["oauth_token"]
                raise ValueError(message) from None
