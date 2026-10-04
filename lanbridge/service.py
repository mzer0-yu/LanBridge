from __future__ import annotations
from copy import deepcopy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

import httpx
from .models import Settings, Site
from .store import Store, password_hash


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tunnel_config_equal(actual, expected):
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return actual == expected
    # Cloudflare materializes this default when the first config is written.
    left, right = deepcopy(actual), deepcopy(expected)
    left.setdefault("warp-routing", {"enabled": False})
    right.setdefault("warp-routing", {"enabled": False})
    return left == right


def lan_address(host):
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
        except OSError:
            raise ValueError("无法解析局域网源站地址") from None
    nets = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128", "fc00::/7")]
    if not addresses or any(not any(a.version == n.version and a in n for n in nets) for a in addresses):
        raise ValueError("源站必须解析到局域网或本机地址（不支持公网、链路本地地址）")
    return str(addresses[0])


def pinned_origin(site, settings):
    u = urlsplit(site["origin"])
    port = u.port or (443 if u.scheme == "https" else 80)
    if port in (settings["admin_port"], settings["gateway_port"]):
        raise ValueError("不能转发管理台或网关自身端口")
    address = lan_address(u.hostname)
    authority = f"[{address}]" if ":" in address else address
    return f"{u.scheme}://{authority}:{port}", u.netloc, u.hostname


class Cloudflare:
    def __init__(self, service):
        self.service = service

    def request(self, method, path, body=None):
        if "/challenges/widgets" in path:
            kind = (self.service.store.get("managed_business_token") or {}).get("kind")
            if kind == "account":
                raise ValueError("Cloudflare 账户令牌不支持 Turnstile API，请使用浏览器授权自动配置人类验证。")
            if kind == "oauth" and not self.widgets_authorized():
                raise ValueError("当前浏览器授权缺少 Turnstile 权限，请点击“自动配置人类验证”补充授权，随后自动创建并保存密钥。")
        sensitive_read = path.endswith("/token") or "/challenges/widgets" in path
        credential = "cf_read_token" if method == "GET" and not sensitive_read else "cf_write_token"
        token = self.service.store.secret(credential)
        if not token and method == "GET":
            credential = "cf_write_token"
            token = self.service.store.secret("cf_write_token")
        if (self.service.store.get("managed_business_token") or {}).get("kind") == "oauth":
            credential = "cf_write_token"
            auth = getattr(self.service, "browser_auth", None)
            if auth is None:
                from .browser_auth import BrowserAuth
                auth = BrowserAuth(self.service)
            token = auth.access_token()
        if not token:
            raise ValueError("请先配置 Cloudflare API 令牌")
        try:
            with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
                response = client.request(method, "https://api.cloudflare.com/client/v4" + path,
                                          headers={"Authorization": "Bearer " + token}, json=body)
            if response.status_code >= 400:
                detail = self.failure_hint(method, path, response, "oauth" if (self.service.store.get("managed_business_token") or {}).get("kind") == "oauth" else credential)
                if response.status_code in (401, 403):
                    with self.service.lock:
                        issues = self.service.store.get("cloudflare_permission_issues", {})
                        cfg = self.service.settings()
                        issues[digest([method, path])] = {"detail": detail, "http_status": response.status_code, "credential": credential, "checked_at": time.time(), "context": [cfg["account_id"], cfg["zone_id"]], "credential_digest": digest(token)}
                        self.service.store.set("cloudflare_permission_issues", issues)
                raise RuntimeError(detail)
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("success"):
                raise RuntimeError("Cloudflare API 拒绝操作，请检查账户权限和配置")
            if payload.get("result") is None and method != "DELETE":
                raise RuntimeError("Cloudflare API 未返回有效结果，请核对操作结果后重试")
            with self.service.lock:
                issues = self.service.store.get("cloudflare_permission_issues", {})
                if issues.pop(digest([method, path]), None):
                    self.service.store.set("cloudflare_permission_issues", issues)
            return payload.get("result")
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("请"):
                raise
            raise RuntimeError("Cloudflare API 网络连接或响应异常") from None

    @staticmethod
    def failure_hint(method, path, response, credential):
        # Do not echo upstream messages, request bodies, headers or credentials.
        try:
            errors = response.json().get("errors", [])
            codes = [str(e["code"]) for e in errors if isinstance(e, dict) and isinstance(e.get("code"), int)]
        except (ValueError, AttributeError, TypeError):
            codes = []
        if "/cfd_tunnel" in path:
            operation = "创建 Tunnel" if method == "POST" else "获取 Tunnel 连接令牌" if path.endswith("/token") else "访问 Tunnel"
            permission = "账户 → Cloudflare Tunnel → 编辑（API 权限名 Cloudflare Tunnel Write）；账户资源需包含配置的 Account ID"
        elif "/challenges/widgets" in path:
            operation, permission = "访问 Turnstile", "账户 → Turnstile → 编辑，账户资源需包含配置的 Account ID"
        elif "/dns_records" in path:
            operation, permission = "访问 DNS", "区域 → DNS → 编辑，区域资源需包含配置的 Zone"
        else:
            operation, permission = "读取域名", "区域 → Zone → 读取，区域资源需包含配置的 Zone"
        name = "浏览器 OAuth 授权" if credential == "oauth" else "只读 API Token" if credential == "cf_read_token" else "写入 API Token"
        prefix = f"Cloudflare API HTTP {response.status_code}：{operation}失败（使用{name}"
        prefix += ("，错误码 " + ",".join(codes[:3]) if codes else "") + "）。"
        if credential == "oauth" and response.status_code in (401, 403):
            return prefix + "请重新浏览器授权并核对资源范围及账户角色；无需提供 API Tokens Write 令牌。"
        if response.status_code == 403:
            return prefix + "请核对令牌权限：" + permission + "。若已授予，请检查令牌有效期及客户端 IP 限制；更新后在设置中更换令牌再重试。"
        if response.status_code == 401:
            return prefix + "令牌认证失败，请在设置中更换有效的 API Token。"
        return prefix + "请核对资源 ID 和令牌资源范围后重试。"

    def tunnel_path(self):
        cfg = self.service.settings()
        if not cfg["account_id"] or not cfg["tunnel_id"]:
            raise ValueError("请配置账户并创建独立 Tunnel")
        return f'/accounts/{cfg["account_id"]}/cfd_tunnel/{cfg["tunnel_id"]}'

    def zone(self):
        cfg = self.service.settings()
        if not cfg["zone_id"]:
            raise ValueError("请配置 Zone ID")
        result = self.request("GET", f'/zones/{cfg["zone_id"]}')
        if result.get("name") != cfg["zone_name"] or result.get("account", {}).get("id") != cfg["account_id"]:
            raise ValueError("域名、Zone ID 和账户 ID 不匹配")
        if result.get("status") != "active":
            raise ValueError("Cloudflare 域名尚未 Active，请先完成 nameserver 接入")
        return result

    def create_tunnel(self):
        with self.service.lock:
            cfg = self.service.settings()
            if cfg["tunnel_id"]:
                # Retry token retrieval after a partial create; never duplicate a recorded tunnel.
                result = {"id": cfg["tunnel_id"]}
            else:
                self.zone()
                pending = self.service.store.get("pending_tunnel_create")
                if pending:
                    if pending["account_id"] != cfg["account_id"]:
                        raise ValueError("先前 Tunnel 创建结果尚未核对，不能切换账户")
                    matches = self.request("GET", f'/accounts/{cfg["account_id"]}/cfd_tunnel?name={pending["name"]}&is_deleted=false')
                    matches = [t for t in matches if t.get("name") == pending["name"]]
                    if len(matches) != 1:
                        raise ValueError("先前 Tunnel 创建结果未知，请在 Cloudflare 核对该唯一名称；平台不会重复创建")
                    result = matches[0]
                else:
                    # Persist an intent before POST so a timeout does not produce duplicate tunnels.
                    label = re.sub(r"[^A-Za-z0-9_-]", "-", cfg["tunnel_name"]).strip("-")[:50] or "lanbridge"
                    name = label + "-" + secrets.token_hex(8)
                    self.service.store.set("pending_tunnel_create", {"name": name, "account_id": cfg["account_id"], "requested_label": cfg["tunnel_name"]})
                    try:
                        result = self.request("POST", f'/accounts/{cfg["account_id"]}/cfd_tunnel',
                                              {"name": name, "config_src": "cloudflare"})
                    except RuntimeError as exc:
                        if re.search(r"Cloudflare API HTTP 4\d\d", str(exc)):
                            self.service.store.set("pending_tunnel_create", None)
                        raise
                cfg["tunnel_id"] = result["id"]
                cfg["tunnel_name"] = result.get("name", cfg["tunnel_name"])
                self.service.store.set("settings", cfg)
                self.service.store.set("owned_tunnel", result["id"])
                self.service.store.set("pending_tunnel_create", None)
                self.service.store.audit("tunnel_created", {"id": result["id"]})
            token = self.request("GET", self.tunnel_path() + "/token")
            if not isinstance(token, str) or not 10 <= len(token) <= 4096:
                raise RuntimeError("Cloudflare 未返回有效的连接令牌，请重试获取令牌")
            self.service.store.set_secret("tunnel_token", token)
            return {"id": result["id"], "token_saved": True}

    def widgets_authorized(self):
        if (self.service.store.get("managed_business_token") or {}).get("kind") != "oauth":
            return (self.service.store.get("managed_business_token") or {}).get("kind") != "account"
        try:
            profile = json.loads(self.service.store.secret("cf_oauth_profile"))["profile"]
            return "challenge-widgets.write" in profile.get("scopes", [])
        except (ValueError, KeyError, TypeError):
            return False

    def create_widget(self):
        with self.service.lock:
            store = self.service.store
            cfg = self.service.settings()
            hosts = sorted({s["hostname"] for s in self.service.sites() if s["enabled"] and s["human_check"]})
            if not hosts:
                raise ValueError("请先添加需要人类验证的网站")
            if not self.widgets_authorized():
                raise ValueError("请点击“自动配置人类验证”补充浏览器授权，无需手工填写密钥")
            self.zone()
            existing = cfg["turnstile_sitekey"]
            if existing and not store.get("owned_widget"):
                raise ValueError("当前 Widget 为手工配置，请在 Cloudflare 管理允许域名")
            path = f'/accounts/{cfg["account_id"]}/challenges/widgets'
            pending = store.get("pending_widget_create")
            if pending:
                if pending.get("account_id") != cfg["account_id"]:
                    raise ValueError("待核对 Widget 属于其他账户，停止自动创建")
                if pending.get("sitekey"):
                    existing = pending["sitekey"]
                else:
                    matches = []
                    for page in range(1, 21):
                        rows = self.request("GET", path + f"?page={page}&per_page=100")
                        if not isinstance(rows, list):
                            raise RuntimeError("无法核对先前 Widget 创建结果，未重复创建")
                        matches.extend(r for r in rows if r.get("name") == pending["name"])
                        if len(rows) < 100:
                            break
                    if len(matches) != 1:
                        raise ValueError("先前 Widget 创建结果待核对，未重复创建；请稍后重试自动配置")
                    existing = matches[0]["sitekey"]
            if existing:
                result = self.request("GET", path + "/" + existing)
                domains = sorted(set(result.get("domains", [])) | set(hosts))
                if not all(any(h == d or h.endswith("." + d) for d in result.get("domains", [])) for h in hosts):
                    result = self.request("PUT", path + "/" + existing, {"name": result.get("name", "LanBridge"), "domains": domains, "mode": result.get("mode", "managed")})
            else:
                name = store.get("turnstile_widget_name") or "LanBridge-" + secrets.token_hex(8)
                store.set("turnstile_widget_name", name)
                store.set("pending_widget_create", {"name": name, "account_id": cfg["account_id"], "requested_at": time.time()})
                try:
                    result = self.request("POST", path, {"name": name, "domains": hosts, "mode": "managed"})
                except ValueError:
                    store.set("pending_widget_create", None)
                    raise
                except RuntimeError as exc:
                    if re.search(r"Cloudflare API HTTP 4\d\d", str(exc)):
                        store.set("pending_widget_create", None)
                    raise
                if not isinstance(result, dict) or not result.get("sitekey"):
                    raise RuntimeError("Widget 创建结果不完整，请重试核对")
                existing = result["sitekey"]
                store.set("pending_widget_create", store.get("pending_widget_create") | {"sitekey": existing})
            secret = result.get("secret") or (store.secret("turnstile_secret") if existing == cfg["turnstile_sitekey"] else "")
            if not secret:
                result = self.request("GET", path + "/" + existing)
                secret = result.get("secret")
            if not isinstance(secret, str) or not 10 <= len(secret) <= 4096:
                raise RuntimeError("未收到有效 Turnstile 密钥，请重试自动配置；不会关闭人类验证")
            cfg["turnstile_sitekey"] = existing
            with store.lock, store.db:
                store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", ("turnstile_secret", store.cipher.encrypt(secret.encode()).decode()))
                for key, value in (("settings", cfg), ("owned_widget", existing), ("pending_widget_create", None)):
                    store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))
            store.audit("turnstile_updated", {"hostnames": hosts})
            return {"sitekey": existing, "hostnames": hosts, "saved": True}

    def plan(self):
        self.zone()
        cfg = self.service.settings()
        if self.service.store.get("owned_tunnel") != cfg["tunnel_id"]:
            raise ValueError("只管理本平台创建的独立 Tunnel")
        all_sites = self.service.sites()
        sites = [s for s in all_sites if s["enabled"]]
        for site in sites:
            self.service.validate_site(site)
            if site["human_check"] and (not cfg["turnstile_sitekey"] or not self.service.store.secret("turnstile_secret")):
                raise ValueError("网站启用了人类验证，请先配置或创建 Turnstile Widget")
        human_hosts = [s["hostname"] for s in sites if s["human_check"]]
        if human_hosts:
            widget = self.request("GET", f'/accounts/{cfg["account_id"]}/challenges/widgets/{cfg["turnstile_sitekey"]}')
            domains = widget.get("domains", [])
            if not all(any(host == domain or host.endswith("." + domain) for domain in domains) for host in human_hosts):
                raise ValueError("Turnstile Widget 未包含全部网站域名，请先同步 Widget 域名")
        remote = self.request("GET", self.tunnel_path() + "/configurations")
        before = remote.get("config") or {"ingress": [{"service": "http_status:404"}]}
        version = remote.get("version")
        owned = set(self.service.store.get("published_hosts", [])) | {s["hostname"] for s in all_sites}
        for rule in before.get("ingress", []):
            if rule.get("hostname") in owned and (rule.get("path") or rule.get("service") != f'http://127.0.0.1:{cfg["gateway_port"]}'):
                raise ValueError("远端路由与平台登记不一致，请检查 Cloudflare 中的手工变更")
        after = deepcopy(before)
        keep = [r for r in before.get("ingress", []) if r.get("hostname") and r.get("hostname") not in owned]
        # Keep unrelated ingress settings; platform owns only its own hostname rules and final deny.
        after["ingress"] = keep + [{"hostname": s["hostname"], "service": f'http://127.0.0.1:{cfg["gateway_port"]}'} for s in sites] + [{"service": "http_status:404"}]
        dns = []
        for site in sites:
            records = self.request("GET", f'/zones/{cfg["zone_id"]}/dns_records?name={site["hostname"]}')
            target = cfg["tunnel_id"] + ".cfargotunnel.com"
            if records and (len(records) != 1 or records[0].get("type") != "CNAME" or records[0].get("content", "").rstrip(".") != target or not records[0].get("proxied")):
                raise ValueError(f'{site["hostname"]} 已有冲突 DNS，平台不会覆盖')
            dns.append({"hostname": site["hostname"], "existing": [{"id": r["id"], "type": r["type"], "content": r["content"], "proxied": r["proxied"]} for r in records],
                        "create": not records, "target": target})
        full = {"settings": cfg, "sites": all_sites, "remote": before, "version": version, "dns": dns, "after": after}
        revision = digest(full)
        # Store snapshot locally, return only reviewable non-sensitive data.
        self.service.store.set("plan", full | {"revision": revision})
        return {"revision": revision, "tunnel_id": cfg["tunnel_id"], "before": before, "after": after,
                "dns": dns, "routes_changed": before != after, "atomic": False}

    def apply(self, revision):
        with self.service.lock:
            if not revision:
                raise ValueError("请先预览配置")
            latest = self.plan()
            if latest["revision"] != revision:
                raise ValueError("本机或 Cloudflare 配置已经变化，请重新预览")
            cfg = self.service.settings()
            created = []
            try:
                # DNS is prepared first; no active ingress for a new host until config succeeds.
                for item in latest["dns"]:
                    if item["create"]:
                        result = self.request("POST", f'/zones/{cfg["zone_id"]}/dns_records', {"type": "CNAME", "name": item["hostname"], "content": item["target"], "proxied": True, "ttl": 1})
                        created.append(result["id"])
                if latest["routes_changed"]:
                    self.request("PUT", self.tunnel_path() + "/configurations", {"config": latest["after"]})
                actual = self.request("GET", self.tunnel_path() + "/configurations")
                if not tunnel_config_equal(actual.get("config"), latest["after"]):
                    raise RuntimeError("远端配置写后核验失败，请重新预览并检查远端")
                for item in latest["dns"]:
                    rows = self.request("GET", f'/zones/{cfg["zone_id"]}/dns_records?name={item["hostname"]}')
                    if not any(r.get("type") == "CNAME" and r.get("content", "").rstrip(".") == item["target"] and r.get("proxied") for r in rows):
                        raise RuntimeError("DNS 写后核验失败")
                self.service.store.set("published_hosts", [s["hostname"] for s in self.service.sites() if s["enabled"]])
                self.service.store.audit("publish_verified", {"revision": revision, "created_dns": created})
                return {"verified": True, "restart_required": False}
            except Exception:
                # API timeouts may have committed writes. Avoid destructive blind rollback.
                self.service.store.audit("publish_incomplete", {"revision": revision, "created_dns": created, "reconcile_required": True})
                raise RuntimeError("发布未完成或无法核验，可能已有部分变更；请重新预览以核对并继续") from None

    def status(self):
        cfg = self.service.settings()
        tunnel = self.request("GET", self.tunnel_path())
        return {"checked_at": time.time(), "edge_status": tunnel.get("status", "unknown"),
                "connections": len(tunnel.get("connections") or []), "tunnel_id": cfg["tunnel_id"]}


class Connector:
    def __init__(self, service):
        self.service, self.process = service, None
        self.started_at = None
        self.lock = threading.Lock()
        self.install_lock = threading.Lock()

    def ensure(self, configured=None):
        from .tools import ensure_cloudflared
        with self.install_lock:
            result = ensure_cloudflared(self.service.settings()["cloudflared_path"] if configured is None else configured)
            with self.service.lock:
                cfg = self.service.settings()
                cfg["cloudflared_path"] = result["path"]
                self.service.store.set("settings", cfg)
                self.service.store.audit("connector_prepared", {"source": result["source"], "version": result["version"]})
            return result

    def status(self):
        cfg = self.service.settings()
        path = cfg["cloudflared_path"] or shutil.which("cloudflared")
        running = self.process is not None and self.process.poll() is None
        return {"installed": bool(path and Path(path).is_file()), "running": running,
                "pid": self.process.pid if running else None, "started_at": self.started_at,
                "last_exit": self.process.poll() if self.process else None}

    def start(self):
        with self.lock:
            if self.status()["running"]:
                return self.status()
            cfg = self.service.settings()
            if not self.service.store.secret("tunnel_token"):
                raise ValueError("请先创建 Tunnel 并保存连接令牌")
            path = self.ensure()["path"]
            # Ensure both listeners are running before creating public reachability.
            for port in (cfg["gateway_port"], cfg["admin_port"]):
                with socket.create_connection(("127.0.0.1", port), timeout=2):
                    pass
            env = dict(os.environ, TUNNEL_TOKEN=self.service.store.secret("tunnel_token"))
            self.process = subprocess.Popen([str(path), "tunnel", "--no-autoupdate", "--protocol", "http2", "run"],
                                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.started_at = time.time()
            self.service.store.audit("connector_started", {"pid": self.process.pid})
            return self.status()

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
                self.service.store.audit("connector_stopped", {})
            return self.status()


class Service:
    def __init__(self, root: Path):
        self.store = Store(root)
        self.lock = threading.RLock()
        if self.store.get("settings") is None:
            defaults = Settings().model_dump()
            binary = Path(__file__).resolve().parents[1] / "bin" / "cloudflared.exe"
            if binary.is_file():
                defaults["cloudflared_path"] = str(binary)
            self.store.set("settings", defaults)
        existing = self.store.get("settings", {})
        if existing.get("tunnel_name") == "lanbridge-windows" and not existing.get("tunnel_id") and not self.store.get("owned_tunnel") and not self.store.get("pending_tunnel_create"):
            self.store.set("settings", existing | {"tunnel_name": "LanBridge"})
        if not self.store.secret("signing_key"):
            self.store.set_secret("signing_key", secrets.token_urlsafe(48))
        self.cf = Cloudflare(self)
        self.connector = Connector(self)

    def settings(self):
        return Settings(**self.store.get("settings", {})).model_dump()

    def permission_issues(self):
        cfg = self.settings()
        result = []
        changed = self.store.get("credential_updated_at", {})
        for issue in self.store.get("cloudflare_permission_issues", {}).values():
            if issue.get("context") and issue["context"] != [cfg["account_id"], cfg["zone_id"]]:
                continue
            credential = issue.get("credential", "cf_write_token")
            replaced = changed.get(credential, 0) > issue["checked_at"]
            if issue.get("credential_digest"):
                replaced = replaced or issue["credential_digest"] != digest(self.store.secret(credential))
            result.append({k: issue[k] for k in ("detail", "http_status", "credential", "checked_at")} | {"status": "needs_recheck" if replaced else "last_failure"})
        return result

    def sites(self):
        return self.store.get("sites", [])

    def cloudflare_setup(self):
        cfg = self.settings()
        missing = [label for key, label in (("account_id", "Account ID"), ("zone_id", "Zone ID"), ("zone_name", "Zone 名称")) if not cfg[key]]
        if not self.store.secret("cf_write_token"):
            missing.append("写入 API Token")
        return {"ready": not missing, "missing": missing}

    def validate_site(self, site):
        cfg = self.settings()
        if not cfg["zone_name"] or not site["hostname"].endswith("." + cfg["zone_name"]):
            raise ValueError("公网域名必须是所配置 Zone 的子域名")
        pinned_origin(site, cfg)
        if site["passcode_required"] and not self.store.secret("passcode_" + site["id"]):
            raise ValueError("请设置至少 12 位的网站访问口令")

    def save_site(self, body):
        with self.lock:
            # Editing existing policies must remain possible even if credentials are unavailable.
            if not body.get("id"):
                setup = self.cloudflare_setup()
                if not setup["ready"]:
                    raise ValueError("Cloudflare 尚未配置完整，请先到“账户与配置”填写并保存：" + "、".join(setup["missing"]))
            passcode = body.pop("passcode", "")
            site = Site(**body).model_dump()
            current = self.sites()
            old = next((s for s in current if s["id"] == site["id"]), None)
            if site["id"] and not old:
                raise ValueError("网站 ID 不存在")
            if old and old["hostname"] != site["hostname"]:
                raise ValueError("已有网站不可更改域名，请停用旧网站后新建")
            site["id"] = site["id"] or secrets.token_hex(8)
            if any(s["hostname"] == site["hostname"] and s["id"] != site["id"] for s in current):
                raise ValueError("域名已被另一个网站使用")
            if passcode:
                hashed = password_hash(passcode)
            else:
                hashed = self.store.secret("passcode_" + site["id"])
            # Validate before changing either policy or secret.
            cfg = self.settings()
            if not cfg["zone_name"] or not site["hostname"].endswith("." + cfg["zone_name"]):
                raise ValueError("域名必须为配置 Zone 下的子域名")
            pinned_origin(site, cfg)
            if site["passcode_required"] and not hashed:
                raise ValueError("需要设置网站口令")
            if passcode:
                self.store.set_secret("passcode_" + site["id"], hashed)
            site["policy_version"] = secrets.token_hex(8)
            self.store.set("sites", [s for s in current if s["id"] != site["id"]] + [site])
            self.store.audit("site_saved", {"id": site["id"], "hostname": site["hostname"], "enabled": site["enabled"]})
            return site
