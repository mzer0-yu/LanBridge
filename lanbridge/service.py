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
from .models import Settings, Site, Zone
from .store import Store, password_hash

LAN_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128", "fc00::/7"))


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
    if not addresses or any(not any(a.version == n.version and a in n for n in LAN_NETWORKS) for a in addresses):
        raise ValueError("源站必须解析到局域网或本机地址（不支持公网、链路本地地址）")
    return str(addresses[0])


def hostname_in_zone(hostname, zone_name):
    return bool(zone_name) and (hostname == zone_name or hostname.endswith("." + zone_name))


def pinned_origin(site, settings):
    u = urlsplit(site["origin"])
    port = u.port or (443 if u.scheme == "https" else 80)
    if site.get("target") == "lanbridge":
        if u.scheme != "http" or u.hostname != "127.0.0.1" or port != settings["admin_port"]:
            raise ValueError("LanBridge 入口源站由平台自动设置")
        return site["origin"], u.netloc, u.hostname
    if port in (settings["admin_port"], settings["gateway_port"]):
        raise ValueError("不能转发管理台或网关自身端口")
    address = lan_address(u.hostname)
    authority = f"[{address}]" if ":" in address else address
    return f"{u.scheme}://{authority}:{port}", u.netloc, u.hostname


class Cloudflare:
    def __init__(self, service):
        self.service = service

    def request(self, method, path, body=None, *, force_write=False, allow_missing=False):
        if "/challenges/widgets" in path:
            kind = (self.service.store.get("managed_business_token") or {}).get("kind")
            if kind == "account":
                raise ValueError("Cloudflare 账户令牌不支持 Turnstile API，请使用浏览器授权自动配置人类验证。")
            if kind == "oauth" and not self.widgets_authorized():
                raise ValueError("当前浏览器授权缺少 Turnstile 权限，请点击“自动配置人类验证”补充授权，随后自动创建并保存密钥。")
        sensitive_read = path.endswith("/token") or "/challenges/widgets" in path
        credential = "cf_read_token" if method == "GET" and not sensitive_read and not force_write else "cf_write_token"
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
            if response.status_code == 404 and allow_missing:
                return None
            if response.status_code >= 400:
                detail = self.failure_hint(method, path, response, "oauth" if (self.service.store.get("managed_business_token") or {}).get("kind") == "oauth" else credential)
                if response.status_code in (401, 403):
                    with self.service.lock:
                        issues = self.service.store.get("cloudflare_permission_issues", {})
                        cfg = self.service.settings()
                        match = re.match(r"/zones/([a-f0-9]{32})(?:/|$)", path)
                        issues[digest([method, path])] = {"detail": detail, "http_status": response.status_code, "credential": credential, "checked_at": time.time(), "context": [cfg["account_id"], match[1] if match else cfg["zone_id"]], "credential_digest": digest(token)}
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
        elif "/rulesets" in path:
            operation, permission = "设置网站云端阻断", "区域 → Zone WAF → 编辑，资源范围需包含此网站所属域名"
        elif "/dns_records" in path:
            operation, permission = "访问 DNS", "区域 → DNS → 编辑，区域资源需包含配置的 Zone"
        elif method == "POST" and path == "/zones":
            operation, permission = "创建域名区域", "区域 → Zone → 编辑，资源范围需包含新域名和目标账户"
        else:
            operation, permission = "读取域名", "区域 → Zone → 读取，区域资源需包含配置的 Zone"
        name = "浏览器 OAuth 授权" if credential == "oauth" else "只读 API Token" if credential == "cf_read_token" else "写入 API Token"
        prefix = f"Cloudflare API HTTP {response.status_code}：{operation}失败（使用{name}"
        prefix += ("，错误码 " + ",".join(codes[:3]) if codes else "") + "）。"
        if "/rulesets" in path and response.status_code in (401, 403):
            return prefix + "需要此域名的 Zone WAF 编辑权限；请在暂停设置中补充 WAF 浏览器授权并授予 zone-waf.write；若账户角色无法授予，可改用具备此权限的 API Token。本机暂停仍保留。"
        if credential == "oauth" and response.status_code in (401, 403):
            if response.status_code == 403 and method == 'POST' and path == '/zones':
                return prefix + '创建新域名需要目标账户的域名创建权限。若使用未包含 Zone 编辑（zone.write）的旧版浏览器授权，请在“修改接入方式”重新浏览器授权以申请新增权限。续期不会扩大权限；若仍失败，请核对账户角色与资源范围。'
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

    def zone(self, selected=None, *, force_write=False):
        cfg = self.service.settings()
        selected = selected or {"zone_id": cfg["zone_id"], "zone_name": cfg["zone_name"]}
        if not selected["zone_id"]:
            raise ValueError("请配置 Zone ID")
        result = self.request("GET", f'/zones/{selected["zone_id"]}', force_write=True) if force_write else self.request("GET", f'/zones/{selected["zone_id"]}')
        if result.get("name") != selected["zone_name"] or result.get("account", {}).get("id") != cfg["account_id"]:
            raise ValueError("域名、Zone ID 和账户 ID 不匹配")
        if result.get("status") != "active":
            raise ValueError("Cloudflare 域名尚未 Active，请先完成 nameserver 接入")
        return result

    def available_zones(self):
        account = self.service.settings()["account_id"]
        if not account:
            raise ValueError("请先配置 Cloudflare 账户")
        zones = []
        for page in range(1, 101):
            rows = self.request("GET", f'/zones?account.id={account}&status=active&per_page=50&page={page}')
            if not isinstance(rows, list):
                raise ValueError("Cloudflare 域名列表格式异常")
            for row in rows:
                if row.get("status") == "active" and row.get("account", {}).get("id") == account:
                    zones.append(Zone(zone_id=row["id"], zone_name=row["name"]).model_dump())
            if len(rows) < 50:
                return sorted(zones, key=lambda z: z["zone_name"])
        raise ValueError("账户域名过多，请手动填写要接入的域名")

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
                cfg = Settings(**cfg).model_dump()
                self.service.store.set_many({"settings": cfg, "owned_tunnel": result["id"], "pending_tunnel_create": None})
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

    def create_widget(self, sites=None):
        with self.service.lock:
            store = self.service.store
            cfg = self.service.settings()
            hosts = sorted({s["hostname"] for s in (self.service.sites() if sites is None else sites) if s["enabled"] and s["human_check"]})
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
                        matches.extend(r for r in rows if isinstance(r, dict) and r.get("name") == pending["name"])
                        if len(rows) < 100:
                            break
                    if len(matches) != 1:
                        raise ValueError("先前 Widget 创建结果待核对，未重复创建；请稍后重试自动配置")
                    existing = matches[0].get("sitekey")
                if not isinstance(existing, str) or not existing.strip():
                    raise RuntimeError("Widget 核对结果不完整，未重复创建")
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
                if not isinstance(result, dict) or not isinstance(result.get("sitekey"), str) or not result["sitekey"].strip():
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
        gateway_services = {f'http://127.0.0.1:{cfg["gateway_port"]}'}
        previous_port = self.service.store.get("previous_gateway_port")
        if previous_port:
            gateway_services.add(f'http://127.0.0.1:{previous_port}')
        for rule in before.get("ingress", []):
            if rule.get("hostname") in owned and (rule.get("path") or rule.get("service") not in gateway_services):
                raise ValueError("远端路由与平台登记不一致，请检查 Cloudflare 中的手工变更")
        after = deepcopy(before)
        keep = [r for r in before.get("ingress", []) if r.get("hostname") and r.get("hostname") not in owned]
        # Keep unrelated ingress settings; platform owns only its own hostname rules and final deny.
        after["ingress"] = keep + [{"hostname": s["hostname"], "service": f'http://127.0.0.1:{cfg["gateway_port"]}'} for s in sites] + [{"service": "http_status:404"}]
        dns = []
        verified_zones = {cfg["zone_id"]}
        for site in sites:
            selected = self.service.site_zone(site)
            if selected["zone_id"] not in verified_zones:
                self.zone(selected)
                verified_zones.add(selected["zone_id"])
            records = self.request("GET", f'/zones/{selected["zone_id"]}/dns_records?name={site["hostname"]}')
            target = cfg["tunnel_id"] + ".cfargotunnel.com"
            if records and (len(records) != 1 or records[0].get("type") != "CNAME" or records[0].get("content", "").rstrip(".") != target or not records[0].get("proxied")):
                raise ValueError(f'{site["hostname"]} 已有冲突 DNS，平台不会覆盖')
            dns.append({"hostname": site["hostname"], "zone_id": selected["zone_id"], "zone_name": selected["zone_name"], "existing": [{"id": r["id"], "type": r["type"], "content": r["content"], "proxied": r["proxied"]} for r in records],
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
                        result = self.request("POST", f'/zones/{item["zone_id"]}/dns_records', {"type": "CNAME", "name": item["hostname"], "content": item["target"], "proxied": True, "ttl": 1})
                        created.append(result["id"])
                if latest["routes_changed"]:
                    self.request("PUT", self.tunnel_path() + "/configurations", {"config": latest["after"]})
                actual = self.request("GET", self.tunnel_path() + "/configurations")
                if not tunnel_config_equal(actual.get("config"), latest["after"]):
                    raise RuntimeError("远端配置写后核验失败，请重新预览并检查远端")
                for item in latest["dns"]:
                    rows = self.request("GET", f'/zones/{item["zone_id"]}/dns_records?name={item["hostname"]}')
                    if not any(r.get("type") == "CNAME" and r.get("content", "").rstrip(".") == item["target"] and r.get("proxied") for r in rows):
                        raise RuntimeError("DNS 写后核验失败")
                self.service.store.set("published_hosts", [s["hostname"] for s in self.service.sites() if s["enabled"]])
                self.service.store.set("publication_error", None)
                self.service.store.set("previous_gateway_port", None)
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
        self.lock = threading.RLock()
        self.install_lock = threading.RLock()
        self.version_lock = threading.Lock()
        self.version_cache = (None, "")

    def check_update(self):
        from .tools import check_cloudflared_update
        with self.install_lock:
            return check_cloudflared_update(self.service.settings()["cloudflared_path"])

    def update(self, tag):
        from .tools import ensure_cloudflared
        with self.lock, self.install_lock:
            checked = self.check_update()
            if not checked["available"] or checked["latest"] != tag:
                raise ValueError("没有匹配的可用更新，请重新检查版本")
            old_path = self.service.settings()["cloudflared_path"]
            running = self.status()["running"]
            result = ensure_cloudflared(update_tag=tag)
            with self.service.lock:
                if self.service.settings()["cloudflared_path"] != old_path:
                    raise ValueError("连接器路径已改变，请重新检查更新")
                try:
                    if running:
                        self.stop()
                    cfg = self.service.settings()
                    cfg["cloudflared_path"] = result["path"]
                    self.service.store.set("settings", cfg)
                    if running:
                        self.start()
                        time.sleep(1)
                        if not self.status()["running"]:
                            raise ValueError("新版本连接器启动失败")
                except Exception as exc:
                    self.stop()
                    cfg = self.service.settings()
                    cfg["cloudflared_path"] = old_path
                    self.service.store.set("settings", cfg)
                    if running:
                        try:
                            self.start()
                        except Exception:
                            raise ValueError("更新失败，旧版本已保留，但恢复运行失败，请手动启动连接器") from exc
                    raise ValueError("更新失败，已恢复原配置和运行状态") from exc
                self.service.store.audit("connector_updated", {"version": result["version"], "running_restored": running})
            return result | {"running_restored": running}

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
        from .tools import TOOL_DIR, version
        cfg = self.service.settings()
        path = cfg["cloudflared_path"] or shutil.which("cloudflared") or str(TOOL_DIR / "cloudflared.exe")
        installed = bool(path and Path(path).is_file())
        with self.version_lock:
            try:
                stat = Path(path).stat() if installed else None
                key = (path, stat.st_mtime_ns, stat.st_size) if stat else None
                if key != self.version_cache[0]:
                    self.version_cache = (key, "")
                    self.version_cache = (key, version(path) if installed else "")
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            current_version = self.version_cache[1]
        running = self.process is not None and self.process.poll() is None
        return {"installed": installed, "version": current_version, "running": running,
                "pid": self.process.pid if running else None, "started_at": self.started_at,
                "last_exit": self.process.poll() if self.process else None}

    def start(self):
        with self.lock:
            if self.status()["running"]:
                return self.status()
            runtime = getattr(self.service, "gateway_runtime", None)
            if runtime and not runtime.status()["running"]:
                raise ValueError("转发网关未启动，请先在本机与安全中恢复网关")
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
        try:
            with self.store.lock, self.store.db:
                self.store.db.execute("BEGIN IMMEDIATE")
                if self.store.get("settings") is None:
                    defaults = Settings().model_dump()
                    binary = Path(__file__).resolve().parents[1] / "bin" / "cloudflared.exe"
                    if binary.is_file():
                        defaults["cloudflared_path"] = str(binary)
                    self.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("settings", json.dumps(defaults)))
                existing = self.store.get("settings", {})
                if existing.get("tunnel_name") == "lanbridge-windows" and not existing.get("tunnel_id") and not self.store.get("owned_tunnel") and not self.store.get("pending_tunnel_create"):
                    self.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("settings", json.dumps(existing | {"tunnel_name": "LanBridge"})))
                if not self.store.secret("signing_key"):
                    encrypted = self.store.cipher.encrypt(secrets.token_urlsafe(48).encode()).decode()
                    self.store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", ("signing_key", encrypted))
        except BaseException:
            self.store.db.close()
            raise
        from .visitor_risk import VisitorRisk
        self.visitor_risk = VisitorRisk(self.store)
        self.cf = Cloudflare(self)
        self.connector = Connector(self)
        from .domain_onboarding import DomainOnboarding
        self.domain_onboarding = DomainOnboarding(self)
        from .site_publication import SitePublication
        self.site_publication = SitePublication(self)
        from .site_pause import SitePause
        self.site_pause = SitePause(self)

    def set_public_client_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("公网转发列表开关必须为布尔值")
        with self.lock:
            self.store.set("public_client_enabled", enabled)
            self.store.audit("public_client_changed", {"enabled": enabled})
        return {"enabled": enabled, "saved": True}

    def set_connector_auto_start(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("自动连接开关必须为布尔值")
        with self.lock:
            if getattr(self, "restart_plan", None):
                raise ValueError("平台正在重启")
            self.store.set("connector_auto_start", enabled)
            self.store.audit("connector_auto_start_changed", {"enabled": enabled})
        return {"enabled": enabled, "saved": True}

    def start_connector_on_launch(self, resume=None):
        """Attempt once; an explicit restart state overrides the normal launch preference."""
        self.connector_startup_warning = None
        wanted = self.store.get("connector_auto_start", True) if resume is None else resume
        if not wanted:
            return
        runtime = getattr(self, "gateway_runtime", None)
        cfg = self.settings()
        try:
            reason = None
            if runtime and not runtime.status()["running"]:
                reason = "转发网关未启动，请先在本机与安全中恢复网关。"
            elif not cfg["tunnel_id"] or not self.store.secret("tunnel_token"):
                reason = "隧道尚未配置完成，请先配置 Tunnel 和连接令牌。"
            elif not self.connector.status().get("installed"):
                reason = "尚未安装 Cloudflared，请先安装连接器。"
            if reason:
                self.connector_startup_warning = "自动连接未完成：" + reason
                return
            self.connector.start()
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
            self.connector_startup_warning = "自动连接失败，请到网站转发检查并重新启动连接器。"


    def settings(self):
        return Settings(**self.store.get("settings", {})).model_dump()

    def commit_settings(self, cfg, *, turnstile_secret=None):
        """Keep visitor invalidation consistent for CLI and API configuration changes."""
        with self.lock:
            cfg = Settings(**cfg).model_dump()
            values, secret_values = {"settings": cfg}, {}
            if turnstile_secret is not None:
                if not isinstance(turnstile_secret, str) or not 10 <= len(turnstile_secret.strip()) <= 4096:
                    raise ValueError("Secret Key 长度或格式无效")
                secret_values["turnstile_secret"] = turnstile_secret.strip()
                values["credential_updated_at"] = self.store.get("credential_updated_at", {}) | {"turnstile_secret": time.time()}
            if cfg["turnstile_sitekey"] != self.settings()["turnstile_sitekey"]:
                sites = self.sites()
                for site in sites:
                    site["policy_version"] = secrets.token_hex(8)
                values.update(owned_widget="", sites=sites)
                secret_values["signing_key"] = secrets.token_urlsafe(48)
            self.store.set_many(values, secret_values=secret_values)

    def queue_gateway_port(self, port):
        import socket
        with self.lock:
            if getattr(self, "restart_plan", None):
                raise ValueError("平台正在重启，请稍后修改端口")
            cfg = self.settings()
            if isinstance(port, bool) or not isinstance(port, int):
                raise ValueError("端口必须为 1024–65535 的整数")
            cfg = Settings(**(cfg | {"gateway_port": port})).model_dump()
            Settings(**(cfg | {"admin_port": self.store.get("pending_admin_port") or cfg["admin_port"]}))
            for site in self.sites():
                if site.get("target") != "lanbridge":
                    pinned_origin(site, cfg)
            pending = port if port != self.settings()["gateway_port"] else None
            if pending:
                try:
                    with socket.socket() as listener:
                        listener.bind(("127.0.0.1", port))
                except OSError:
                    raise ValueError("该端口已被占用，请选择其他端口") from None
            self.store.set("pending_gateway_port", pending)
            self.store.audit("gateway_port_scheduled", {"port": port})
            return {"saved": True, "pending_gateway_port": pending, "restart_required": bool(pending)}

    def queue_admin_port(self, port):
        import socket
        import os
        with self.lock:
            if getattr(self, "restart_plan", None):
                raise ValueError("平台正在重启，请稍后修改端口")
            if isinstance(port, bool) or not isinstance(port, int):
                raise ValueError("端口必须为 1024–65535 的整数")
            current = self.settings()
            candidate = Settings(**(current | {"admin_port": port})).model_dump()
            Settings(**(candidate | {"gateway_port": self.store.get("pending_gateway_port") or current["gateway_port"]}))
            for site in self.sites():
                if site.get("target") != "lanbridge":
                    pinned_origin(site, candidate)
            pending = port if port != current["admin_port"] else None
            if pending:
                try:
                    with socket.socket() as listener:
                        if os.name == "nt":
                            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                        listener.bind(("127.0.0.1", port))
                except OSError:
                    raise ValueError("该管理端口已被占用或被系统保留，请选择其他端口") from None
            self.store.set("pending_admin_port", pending)
            self.store.audit("admin_port_scheduled", {"port": port})
            return {"saved": True, "pending_admin_port": pending, "restart_required": bool(pending)}

    def oauth_refresh_status(self):
        owned = self.store.get("managed_business_token") or {}
        issue = self.store.get("oauth_refresh_issue")
        cfg = self.settings()
        if owned.get("kind") != "oauth" or not issue or issue.get("context") != [cfg["account_id"], cfg["zone_id"]] or issue.get("credential_digest") != digest(self.store.secret("cf_write_token")):
            return None
        usable = issue.get("expires_at", 0) > time.time() + 30
        message = ("自动续期暂未完成，当前管理授权仍可使用。" if usable else "自动续期未完成，Cloudflare 云端管理操作暂不可用。")
        message += "添加域名、发布配置等云端管理操作需要有效授权。管理员登录和已运行的转发不受此次续期失败影响。"
        message += "请检查网络并重试续期；仍失败时再重新授权。"
        from .browser_auth import REFRESH_REASONS
        reason = issue.get('reason') if issue.get('reason') in REFRESH_REASONS else None
        if reason:
            message += ' ' + REFRESH_REASONS[reason]
        return {"phase": "warning" if usable else "error", "message": message, "retry_failed": bool(issue.get("retry_failed")),
                "reason": reason,
                "checked_at": issue["checked_at"]}

    def permission_issues(self):
        cfg = self.settings()
        result = []
        changed = self.store.get("credential_updated_at", {})
        for issue in self.store.get("cloudflare_permission_issues", {}).values():
            if issue.get("context") and issue["context"] not in [[cfg["account_id"], z["zone_id"]] for z in cfg["zones"]] + [[cfg["account_id"], cfg["zone_id"]]]:
                continue
            credential = issue.get("credential", "cf_write_token")
            replaced = changed.get(credential, 0) > issue["checked_at"]
            if issue.get("credential_digest"):
                replaced = replaced or issue["credential_digest"] != digest(self.store.secret(credential))
            result.append({k: issue[k] for k in ("detail", "http_status", "credential", "checked_at")} | {"status": "needs_recheck" if replaced else "last_failure"})
        return result

    def sites(self, cfg=None):
        cfg = self.settings() if cfg is None else cfg
        return [dict(site, human_check_mode=site.get("human_check_mode", "always"), protocols=site.get("protocols", ["http", "websocket"]),
                     zone_id=site.get("zone_id") or (cfg["zone_id"] if hostname_in_zone(site["hostname"], cfg["zone_name"]) else ""))
                for site in self.store.get("sites", [])]

    def site_zone(self, site):
        zones = self.settings()["zones"]
        # Exact label boundary and most specific zone prevent overlapping-zone ambiguity.
        matches = [z for z in zones if hostname_in_zone(site["hostname"], z["zone_name"])]
        selected = next((z for z in matches if z["zone_id"] == site.get("zone_id")), None) if site.get("zone_id") else max(matches, key=lambda z: len(z["zone_name"]), default=None)
        if not selected:
            requested = next((z for z in zones if z["zone_id"] == site.get("zone_id")), None)
            if site.get("zone_id") and not requested:
                raise ValueError("所选域名已不在已接入列表中，请刷新页面后重新选择所属域名")
            if requested:
                raise ValueError("公网域名 " + site["hostname"] + " 不属于所选域名 " + requested["zone_name"] + "，请核对所属域名与公网域名")
            raise ValueError("公网域名必须属于已接入的域名；请在账户与配置中添加域名")
        return selected

    def add_zone(self, data, *, force_write=False):
        selected = Zone(**data).model_dump()
        with self.lock:
            cfg = self.settings()
            previous = dict(cfg)
            if not cfg["account_id"]:
                raise ValueError("请先配置 Cloudflare 账户")
            if force_write:
                self.cf.zone(selected, force_write=True)
            else:
                self.cf.zone(selected)
            existing = next((z for z in cfg["zones"] if z["zone_id"] == selected["zone_id"] or z["zone_name"] == selected["zone_name"]), None)
            if existing and existing != selected:
                raise ValueError("域名或 Zone ID 与已有配置冲突")
            if not existing:
                if len(cfg["zones"]) >= 100:
                    raise ValueError("最多接入 100 个域名")
                cfg["zones"].append(selected)
                if not cfg["zone_id"]:
                    cfg.update(selected)
                cfg = Settings(**cfg).model_dump()
                self._save_zone_settings(cfg, previous)
                self.store.audit("zone_added", selected)
            return cfg

    def _save_zone_settings(self, cfg, previous, extra_values=None):
        # The default is a local selection, not a change to remote token permissions.
        values = {"settings": cfg, **(extra_values or {})}
        if cfg["zone_id"] != previous["zone_id"]:
            for key in ("managed_business_token", "managed_read_token"):
                owned = self.store.get(key) or {}
                if owned and owned.get("account_id") == cfg["account_id"] and owned.get("zone_id") == previous["zone_id"]:
                    values[key] = owned | {"zone_id": cfg["zone_id"]}
        self.store.set_many(values)

    def set_default_zone(self, zone_id):
        with self.lock:
            previous = self.settings()
            selected = next((z for z in previous["zones"] if z["zone_id"] == zone_id), None)
            if not selected:
                raise ValueError("域名不在已接入列表中，请刷新后重试")
            if zone_id == previous["zone_id"]:
                return previous
            # Pin old implicit associations before changing the compatibility default.
            extra = {}
            if any(not s.get("zone_id") for s in self.store.get("sites", [])):
                sites = self.sites()
                for site in sites:
                    site["zone_id"] = self.site_zone(site)["zone_id"]
                extra["sites"] = sites
            cfg = Settings(**(previous | selected)).model_dump()
            self._save_zone_settings(cfg, previous, extra)
            self.store.audit("default_zone_changed", selected)
            return cfg

    def remove_zone(self, zone_id):
        with self.lock:
            cfg = self.settings()
            selected = next((z for z in cfg["zones"] if z["zone_id"] == zone_id), None)
            if not selected:
                raise ValueError("域名不存在")
            sites = [s["hostname"] for s in self.sites() if self.site_zone(s)["zone_id"] == zone_id]
            routes = [host for host in self.store.get("published_hosts", []) if hostname_in_zone(host, selected["zone_name"])]
            if sites or routes:
                linked = list(dict.fromkeys(sites + routes))
                raise ValueError("域名仍被网站或已发布路由使用，不能移除：" + "、".join(linked[:5]) + ("等" if len(linked) > 5 else ""))
            previous = dict(cfg)
            cfg["zones"] = [z for z in cfg["zones"] if z["zone_id"] != zone_id]
            if zone_id == cfg["zone_id"]:
                cfg.update(cfg["zones"][0] if cfg["zones"] else {"zone_id": "", "zone_name": ""})
            cfg = Settings(**cfg).model_dump()
            self._save_zone_settings(cfg, previous)
            self.store.audit("zone_removed", selected)
            return cfg

    def configuration_snapshot(self):
        """Read connection inputs once, without returning decrypted credentials."""
        with self.store.lock:
            cfg = self.settings()
            credentials = {key: bool(self.store.secret(key)) for key in
                           ('cf_read_token', 'cf_write_token', 'turnstile_secret', 'tunnel_token')}
            return {'settings': cfg, 'sites': self.sites(cfg), 'credentials': credentials,
                    'cloudflare_setup': self.cloudflare_setup(cfg, credentials['cf_write_token'])}

    def cloudflare_setup(self, cfg=None, has_write=None):
        cfg = self.settings() if cfg is None else cfg
        missing = [label for key, label in (("account_id", "Account ID"), ("zone_id", "Zone ID"), ("zone_name", "Zone 名称")) if not cfg[key]]
        if not (bool(self.store.secret('cf_write_token')) if has_write is None else has_write):
            missing.append("写入 API Token")
        return {"ready": not missing, "missing": missing}

    def validate_site(self, site):
        cfg = self.settings()
        self.site_zone(site)
        pinned_origin(site, cfg)
        if site["passcode_required"] and not self.store.secret("passcode_" + site["id"]):
            raise ValueError("请设置至少 12 位的网站访问口令")

    def save_site(self, body, *, synchronize_verification=False, auto_publish=False):
        with self.lock:
            # Editing existing policies must remain possible even if credentials are unavailable.
            if not body.get("id"):
                setup = self.cloudflare_setup()
                if not setup["ready"]:
                    raise ValueError("Cloudflare 尚未配置完整，请先到“账户与配置”填写并保存：" + "、".join(setup["missing"]))
            body = dict(body)
            existing = next((s for s in self.sites() if s["id"] == body.get("id")), None)
            body.setdefault("target", (existing or {}).get("target", "website"))
            hostname_changed = existing and isinstance(body.get("hostname"), str) and body["hostname"].strip().lower().rstrip(".") != existing["hostname"]
            body.setdefault("zone_id", "" if hostname_changed else (existing or {}).get("zone_id", ""))
            body.setdefault("human_remember_days", (existing or {}).get("human_remember_days", 1))
            body.setdefault("human_check_mode", (existing or {}).get("human_check_mode", "always"))
            if body["target"] == "lanbridge":
                body["origin"] = f'http://127.0.0.1:{self.settings()["admin_port"]}'
                body["protocols"] = ["http"]
                if not self.store.get("admin"):
                    raise ValueError("请先创建本机管理员，再开启远程管理")
            passcode = body.pop("passcode", "")
            if not isinstance(passcode, str):
                raise ValueError("网站访问口令格式无效")
            site = Site(**body).model_dump()
            current = self.sites()
            old = next((s for s in current if s["id"] == site["id"]), None)
            if site["id"] and not old:
                raise ValueError("网站 ID 不存在")
            if old:
                self.site_pause.guard_edit(old["id"])
            if old and "paused" not in body:
                site["paused"] = old.get("paused", False)
            if old and "protocols" not in body:
                site["protocols"] = old.get("protocols", ["http", "websocket"])
            site["id"] = site["id"] or secrets.token_hex(8)
            if any(s["hostname"] == site["hostname"] and s["id"] != site["id"] for s in current):
                raise ValueError("域名已被另一个网站使用")
            if passcode:
                hashed = password_hash(passcode)
            else:
                hashed = self.store.secret("passcode_" + site["id"])
            # Validate before changing either policy or secret.
            cfg = self.settings()
            site["zone_id"] = self.site_zone(site)["zone_id"]
            pinned_origin(site, cfg)
            if site["passcode_required"] and not hashed:
                raise ValueError("需要设置网站口令")
            proposed = [s for s in current if s["id"] != site["id"]] + [site]
            if synchronize_verification and site["enabled"] and site["human_check"]:
                try:
                    self.cf.create_widget(sites=proposed)
                except (ValueError, RuntimeError, OSError) as exc:
                    raise ValueError("人类验证配置未完成，网站修改未保存：" + str(exc)) from None
            # Cosmetic edits and no-op saves must not revoke visitor grants.
            policy_keys = set(Site.model_fields) - {'id', 'name', 'zone_id', 'policy_version'}
            old_policy = Site(**old).model_dump() if old else {}
            unchanged = old and not passcode and all(site[key] == old_policy[key] for key in policy_keys)
            site["policy_version"] = old["policy_version"] if unchanged and old.get("policy_version") else secrets.token_hex(8)
            values = {"sites": proposed}
            if old and old.get("paused", False) != site["paused"]:
                attempts = self.store.get("paused_auto_attempts", {})
                attempts.pop(site["id"], None)
                values["paused_auto_attempts"] = attempts
            self.store.set_many(values, secret_values={"passcode_" + site["id"]: hashed} if passcode else None)
            self.store.audit("site_saved", {"id": site["id"], "hostname": site["hostname"], "enabled": site["enabled"]})
            if auto_publish:
                desired = sorted(s["hostname"] for s in proposed if s["enabled"])
                published = sorted(self.store.get("published_hosts", []))
                if desired != published or self.store.get("publication_error"):
                    try:
                        preview = self.cf.plan()
                        self.cf.apply(preview["revision"])
                        return site | {"publication": {"status": "published"}}
                    except (ValueError, RuntimeError, OSError) as exc:
                        message = str(exc)
                        self.store.set("publication_error", message)
                        self.store.audit("publish_incomplete", {"site_id": site["id"], "automatic": True, "reconcile_required": True})
                        return site | {"publication": {"status": "failed", "message": message}}
                return site | {"publication": {"status": "unchanged"}}
            return site

    def set_site_paused(self, site_id, paused, *, cloud_internal=False, cloud_status=None):
        if not isinstance(paused, bool):
            raise ValueError("暂停状态必须为布尔值")
        if cloud_status is not None and not cloud_internal:
            raise ValueError("云端完成状态仅支持内部提交")
        with self.lock:
            if not cloud_internal:
                self.site_pause.guard_edit(site_id)
            sites = self.sites()
            old = next((site for site in sites if site["id"] == site_id), None)
            if not old:
                raise ValueError("网站不存在")
            if not old["enabled"]:
                raise ValueError("网站已停用，请先编辑并启用网站")
            if old.get("paused", False) == paused:
                if cloud_status is not None:
                    self.store.set("site_pause", cloud_status)
                return old
            site = old | {"paused": paused, "policy_version": secrets.token_hex(8)}
            attempts = self.store.get("paused_auto_attempts", {})
            attempts.pop(site_id, None)
            values = {"sites": [site if row["id"] == site_id else row for row in sites],
                      "paused_auto_attempts": attempts}
            if cloud_status is not None:
                values["site_pause"] = cloud_status
            self.store.set_many(values)
            self.store.audit("site_paused" if paused else "site_resumed", {"id": site_id, "hostname": site["hostname"]})
            return site
