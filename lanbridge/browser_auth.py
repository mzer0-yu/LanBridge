"""Browser consent via the official Cloudflare CLI; secrets never enter UI state."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time

from .store import protect_directory
from .token_manager import TokenManager
from .service import digest

VERSION = "1.0.0-beta.12"
SCOPES = ["account:read", "account_api_tokens:create", "argotunnel.write", "teams-connector-cloudflared.write", "dns.write", "zone.read"]


class BrowserAuth:
    def __init__(self, service):
        self.service = service
        self.thread = None
        self.process = None
        self.gate = threading.Lock()
        self.cancelled = threading.Event()
        previous = service.store.get("browser_auth_job", {})
        if previous.get("phase") in ("preparing", "authorizing", "creating"):
            self.update("error", "上次浏览器授权被服务重启中断。若已有创建结果未知记录，请先在 Cloudflare 核对。")

    def update(self, phase, message):
        self.service.store.set("browser_auth_job", {"phase": phase, "message": message, "updated_at": time.time()})

    def status(self):
        return self.service.store.get("browser_auth_job", {"phase": "idle", "message": "在 Cloudflare 官方页面登录并授权后，自动创建并加密保存写入令牌。"})

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
                   CLOUDFLARE_ACCOUNT_ID=account, DO_NOT_TRACK="1", CI="1", NO_COLOR="1")
        return env

    def run(self, command, env, cwd, args, timeout=45):
        if self.cancelled.is_set():
            raise ValueError("浏览器授权已取消。")
        self.process = subprocess.Popen(command + args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            out, err = self.process.communicate(timeout=timeout)
            if self.process.returncode or self.cancelled.is_set():
                raise ValueError("Cloudflare 授权或 API 操作失败，请检查浏览器授权、账户的令牌创建权限及网络后重试。")
            return out.decode("utf-8", errors="replace").strip()
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate()
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

    def stop(self):
        self.cancelled.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()

    def worker(self, cfg, human_check):
        store = self.service.store
        command = None
        try:
            command = self.command()
            auth_root = store.root / "browser-auth"
            protect_directory(auth_root)
            with tempfile.TemporaryDirectory(prefix="session-", dir=auth_root) as name:
                directory = Path(name)
                env = self.environment(directory, cfg["account_id"])
                scopes = SCOPES
                try:
                    self.update("authorizing", "已请求打开系统浏览器。请在 Cloudflare 官方页面登录并确认所列权限；完成后会自动创建令牌。")
                    self.run(command, env, directory, ["auth", "create", "lanbridge", "--no-device", "--scopes", *scopes], timeout=180)
                    self.update("creating", "授权已完成，正在核对权限并创建账户令牌…")
                    groups = self.payload(self.run(command, env, directory, ["accounts", "tokens", "permission-groups", "list", "--profile", "lanbridge"]))
                    try:
                        policies = TokenManager.policies(groups, cfg, human_check)
                    except ValueError as exc:
                        raise ValueError(str(exc) + " 浏览器登录成功不代表所需权限已授予；请重新授权并核对 Tunnel、DNS、Zone 及 Account API Token Provisioning，同时确认账户角色允许创建令牌。") from None
                    with self.service.lock:
                        current = self.service.settings()
                        if any(current[k] != cfg[k] for k in ("account_id", "zone_id", "zone_name")) or digest(store.secret("cf_write_token")) != cfg["credential_digest"]:
                            raise ValueError("账户或域名已变化，已停止创建；请重新授权。")
                        if store.get("pending_browser_token") or store.get("pending_business_token"):
                            raise ValueError("已有创建结果待核对，已停止自动创建。")
                        token_name = "LanBridge-browser-" + secrets.token_hex(8)
                        store.set("pending_browser_token", {"name": token_name, "account_id": cfg["account_id"], "requested_at": time.time()})
                        result = self.payload(self.run(command, env, directory, ["accounts", "tokens", "create", "--profile", "lanbridge", "--name", token_name, "--policies", json.dumps(policies)], timeout=60))
                        if not isinstance(result, dict) or not re.fullmatch(r"[a-fA-F0-9]{32}", str(result.get("id", ""))) or not isinstance(result.get("value"), str) or not 10 <= len(result["value"]) <= 4096:
                            raise ValueError("未收到完整账户令牌，请在 Cloudflare 核对创建结果。")
                        owned = {"id": result["id"], "name": token_name, "kind": "account", "account_id": cfg["account_id"], "zone_id": cfg["zone_id"], "human_check": human_check, "updated_at": time.time(), "credential_digest": digest(result["value"])}
                        with store.lock, store.db:
                            store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", ("cf_write_token", store.cipher.encrypt(result["value"].encode()).decode()))
                            store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("managed_business_token", json.dumps(owned)))
                            store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("pending_browser_token", "null"))
                            store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("credential_updated_at", json.dumps(store.get("credential_updated_at", {}) | {"cf_write_token": time.time()})))
                        store.audit("browser_token_created", {"id": result["id"], "account_id": cfg["account_id"], "human_check": human_check})
                        store.set("token_management_error", None)
                    self.update("done", "写入 API Token 已通过浏览器授权创建并加密保存。请重试创建 Tunnel 核验实际权限。")
                finally:
                    # Revoke only the isolated CLI profile; never touch the user's existing CLI login.
                    try:
                        self.run(command, env, directory, ["auth", "logout", "--profile", "lanbridge"], timeout=15)
                    except (ValueError, OSError):
                        pass
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "浏览器授权失败，请检查 Node.js、官方 CLI 安装和网络后重试。"
            if store.get("pending_browser_token"):
                message += " 创建结果未知：请在 Cloudflare 账户 API Tokens 核对 " + store.get("pending_browser_token")["name"] + "。"
            self.update("error", message)
