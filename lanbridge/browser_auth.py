"""Browser consent via the official Cloudflare CLI; secrets never enter UI state."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
import httpx

from .store import protect_directory
from .service import digest

VERSION = "1.0.0-beta.12"
SCOPES = ["account:read", "argotunnel.write", "teams-connector-cloudflared.write", "dns.write", "zone.read"]


class BrowserAuth:
    def __init__(self, service):
        self.service = service
        self.thread = None
        self.process = None
        self.gate = threading.Lock()
        self.cancelled = threading.Event()
        previous = service.store.get("browser_auth_job", {})
        if previous.get("phase") in ("preparing", "authorizing", "creating", "cancelling"):
            self.update("error", "上次浏览器授权被服务重启中断。若已有创建结果未知记录，请先在 Cloudflare 核对。")
        elif previous.get("next_action") == "token_authority":
            self.update("error", "旧流程在令牌转授阶段失败。现在直接使用浏览器授权，请重新授权一次完成接入，无需另行提供授权令牌。", next_action="reauthorize")

    def update(self, phase, message, **diagnostics):
        self.service.store.set("browser_auth_job", {"phase": phase, "message": message, "updated_at": time.time(), **diagnostics})

    def status(self):
        return self.service.store.get("browser_auth_job", {"phase": "idle", "message": "在 Cloudflare 官方页面授权一次，自动接入并刷新凭据，无需粘贴令牌。"})

    def command(self):
        node = shutil.which("node")
        if not node:
            raise ValueError("浏览器授权需要 Node.js 22.18 或更高版本，请安装后重试。")
        root = Path(__file__).resolve().parents[1] / "bin" / "cf-runtime"
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
        return env

    def run(self, command, env, cwd, args, timeout=45):
        if self.cancelled.is_set():
            raise ValueError("浏览器授权已取消。")
        self.process = subprocess.Popen(command + args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            if self.cancelled.is_set():
                self.process.kill()
            out, err = self.process.communicate(timeout=timeout)
            if self.process.returncode or self.cancelled.is_set():
                if args[:2] == ["auth", "create"] and not self.cancelled.is_set():
                    output = (out + err).decode("utf-8", errors="replace").lower()
                    if "timed out waiting for authorization code" in output:
                        raise ValueError("浏览器授权已超时：官方 CLI 的本机回调等待 2 分钟，8877 端口已关闭。未创建令牌；请关闭旧授权页，回到本平台重新发起授权，勿刷新旧回调地址。")
                raise ValueError("Cloudflare 授权或 API 操作失败，请检查浏览器授权、账户角色及网络后重试。")
            return out.decode("utf-8", errors="replace").strip()
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate()
            if args[:2] == ["auth", "create"]:
                raise ValueError("浏览器授权等待已结束，本机回调端口已关闭。未创建令牌；请回到本平台重新发起授权，勿刷新旧回调地址。") from None
            raise ValueError("Cloudflare 操作超时；若处于创建阶段，请先在 Cloudflare 核对结果，勿重复创建。") from None
        finally:
            self.process = None

    @staticmethod
    def payload(output):
        try:
            value = json.loads(output)
        except ValueError:
            raise ValueError("Cloudflare CLI 响应格式异常，请在 Cloudflare 核对结果。") from None
        if isinstance(value, dict) and "result" in value:
            value = value["result"]
        return value

    def start(self):
        with self.gate, self.service.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("浏览器授权正在进行，请完成当前操作。")
            if self.service.store.get("pending_browser_token") or self.service.store.get("pending_business_token"):
                raise ValueError("先前令牌创建结果未知，请在 Cloudflare 核对并通过手动配置接入，平台不会重复创建。")
            cfg = self.service.settings()
            if not all(re.fullmatch(r"[a-fA-F0-9]{32}", cfg[k]) for k in ("account_id", "zone_id")) or not cfg["zone_name"]:
                raise ValueError("请先保存 Account ID、Zone ID 和 Zone 名称。")
            self.cancelled.clear()
            self.update("preparing", "正在准备官方 Cloudflare 授权工具…")
            cfg = cfg | {"credential_digest": digest(self.service.store.secret("cf_write_token"))}
            self.thread = threading.Thread(target=self.worker, args=(cfg, False), daemon=True)
            self.thread.start()
            return self.status()

    def cancel(self):
        with self.gate:
            phase = self.status().get("phase")
            if phase != "authorizing":
                if phase in ("idle", "done", "error", "cancelled"):
                    return self.status()
                raise ValueError("当前正在准备或核验授权，请稍候；浏览器等待阶段可以立即取消。")
            self.cancelled.set()
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

    def restart(self):
        if self.status().get("phase") == "authorizing":
            self.cancel()
        return self.start()

    def stop(self):
        self.cancelled.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()

    def worker(self, cfg, human_check):
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
                scopes = SCOPES
                try:
                    self.update("authorizing", "已请求打开系统浏览器。请在浏览器确认权限。误关页面可点击“重新打开授权页”，或随时取消；无需等待超时。")
                    self.run(command, env, directory, ["auth", "create", "lanbridge", "--no-device", "--scopes", *scopes], timeout=180)
                    self.update("creating", "授权已完成，正在核对权限和域名归属…")
                    identity = self.payload(self.run(command, env, directory, ["auth", "whoami", "--profile", "lanbridge"]))
                    granted = identity.get("scopes") if isinstance(identity, dict) else None
                    if not isinstance(granted, list) or any(not isinstance(scope, str) for scope in granted):
                        raise ValueError("无法核对浏览器实际授予的权限范围；请重新发起浏览器授权。")
                    # Retain only our known scope names, never the identity/email or raw CLI output.
                    diagnostics = {"granted_scopes": [scope for scope in SCOPES if scope in granted]}
                    needed = {"dns.write", "zone.read"}
                    missing = needed.difference(granted)
                    if not any(scope in granted for scope in ("argotunnel.write", "teams-connector-cloudflared.write")):
                        missing.add("Tunnel Write")
                    if missing:
                        diagnostics["next_action"] = "reauthorize"
                        raise ValueError("浏览器实际未授予所需范围：" + "、".join(sorted(missing)) + "。未创建令牌；请仅核对这些权限后重新授权。")
                    snapshot = self.read_profile(directory)
                    self.validate_zone(snapshot["profile"]["oauth_token"], cfg)
                    with self.service.lock:
                        if self.cancelled.is_set():
                            raise ValueError("浏览器授权已取消，原凭据保持不变。")
                        current = self.service.settings()
                        if any(current[k] != cfg[k] for k in ("account_id", "zone_id", "zone_name")) or digest(store.secret("cf_write_token")) != cfg["credential_digest"]:
                            raise ValueError("账户或凭据已变化，已停止配置；请重新授权。")
                        self.save_profile(snapshot, cfg)
                        store.set("token_management_error", None)
                        store.audit("browser_oauth_connected", {"account_id": cfg["account_id"], "zone_id": cfg["zone_id"]})
                    self.update("done", "浏览器授权已接入并加密保存，凭据将自动刷新。Tunnel、DNS 与 Zone 操作直接使用此授权，无需再提供 API Tokens Write 令牌。")
                finally:
                    # Delete the isolated temporary files without revoking the persisted grant.
                    pass
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "浏览器授权失败，请检查 Node.js、官方 CLI 安装和网络后重试。"
            if store.get("pending_browser_token"):
                message += " 创建结果未知：请在 Cloudflare 账户 API Tokens 核对 " + store.get("pending_browser_token")["name"] + "。"
            if self.cancelled.is_set():
                self.update("cancelled", "已取消本次授权，原凭据保持不变。现在可以立即重新授权，无需等待超时。")
            else:
                self.update("error", message, **diagnostics)

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
        if not {"dns.write", "zone.read"}.issubset(profile.get("scopes", [])) or not any(s in profile.get("scopes", []) for s in ("argotunnel.write", "teams-connector-cloudflared.write")):
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

    def save_profile(self, snapshot, cfg):
        store = self.service.store
        token = snapshot["profile"]["oauth_token"]
        owned = {"kind": "oauth", "account_id": cfg["account_id"], "zone_id": cfg["zone_id"], "updated_at": time.time(), "credential_digest": digest(token)}
        with store.lock, store.db:
            for key, value in (("cf_write_token", token), ("cf_oauth_profile", json.dumps(snapshot))):
                store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (key, store.cipher.encrypt(value.encode()).decode()))
            store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("managed_business_token", json.dumps(owned)))
            store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("credential_updated_at", json.dumps(store.get("credential_updated_at", {}) | {"cf_write_token": time.time()})))

    def access_token(self):
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
            if expires > time.time() + 90:
                return snapshot["profile"]["oauth_token"]
            # Independent runner: refresh cannot overwrite/cancel an ongoing login process.
            runner = BrowserAuth.__new__(BrowserAuth)
            runner.service, runner.process, runner.cancelled = self.service, None, threading.Event()
            root = store.root / "browser-auth"
            protect_directory(root)
            try:
                with tempfile.TemporaryDirectory(prefix="refresh-", dir=root) as name:
                    directory = Path(name)
                    path = directory / snapshot["path"]
                    if not path.resolve().is_relative_to(directory.resolve()) or path.name != "lanbridge.json":
                        raise ValueError("授权配置路径无效")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(snapshot["profile"]), encoding="utf-8")
                    runner.run(runner.command(), runner.environment(directory, cfg["account_id"]), directory, ["auth", "whoami", "--profile", "lanbridge"])
                    refreshed = runner.read_profile(directory)
                    expiry = datetime.fromisoformat(refreshed["profile"]["expiration_time"].replace("Z", "+00:00")).timestamp()
                    if expiry <= time.time() + 30:
                        raise ValueError("授权未刷新")
                    self.save_profile(refreshed, cfg)
                    return refreshed["profile"]["oauth_token"]
            except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
                raise ValueError("浏览器授权自动刷新失败，请重新浏览器授权；无需提供 API Tokens Write 令牌。") from None
