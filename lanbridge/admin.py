from __future__ import annotations
from pathlib import Path
import secrets
import time

import httpx
from fastapi import FastAPI, Request
from starlette.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from .gateway import Limiter
from .models import Settings
from .service import pinned_origin
from .store import password_check, password_hash
from pydantic import ValidationError


def create_admin(service, shutdown=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    limiter = Limiter()
    ui = Path(__file__).resolve().parent.parent / "ui"

    @app.middleware("http")
    async def guard(request, call_next):
        cfg = service.settings()
        allowed = {f'127.0.0.1:{cfg["admin_port"]}', f'localhost:{cfg["admin_port"]}'}
        host = request.headers.get("host", "")
        if host not in allowed:
            return JSONResponse({"detail": "管理台只接受本机访问"}, 403)
        if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            return JSONResponse({"detail": "管理台仅监听本机"}, 403)
        if request.method not in ("GET", "HEAD") and request.headers.get("origin") != "http://" + host:
            return JSONResponse({"detail": "请求来源校验失败"}, 403)
        public = request.url.path in ("/", "/app.js", "/style.css", "/favicon.svg", "/api/bootstrap", "/api/setup", "/api/login")
        session = service.store.session(request.cookies.get("lb_admin", ""))
        if not public and not session:
            return JSONResponse({"detail": "请登录管理员账户"}, 401)
        if not public and request.method not in ("GET", "HEAD") and request.headers.get("x-csrf-token") != session["csrf"]:
            return JSONResponse({"detail": "CSRF 校验失败，请刷新管理台"}, 403)
        request.state.session = session
        try:
            response = await call_next(request)
        except (ValueError, RuntimeError, OSError) as exc:
            # Exceptions never include raw Cloudflare response or secret payloads.
            detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "本机服务操作失败，请检查路径、权限或网络"
            if isinstance(exc, ValidationError):
                labels = {"account_id": "Account ID", "zone_id": "Zone ID", "zone_name": "Zone 名称", "hostname": "公网域名", "origin": "局域网地址", "name": "网站名称", "allowed_ips": "IP 范围", "allowed_countries": "国家范围", "session_minutes": "会话时长", "requests_per_minute": "请求速率"}
                fields = [labels.get(str(error["loc"][0]), str(error["loc"][0])) for error in exc.errors() if error.get("loc")]
                detail = "请检查以下字段的格式或范围：" + "、".join(dict.fromkeys(fields))
            if detail.startswith("{") or len(detail) > 400:
                detail = "输入格式不正确，请检查配置"
            response = JSONResponse({"detail": detail}, 400)
        response.headers.update({"Cache-Control": "no-store", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"})
        return response

    async def body(request, limit=20000):
        raw = await request.body()
        if len(raw) > limit:
            raise ValueError("请求内容过大")
        import json
        try:
            value = json.loads(raw)
        except ValueError:
            raise ValueError("请求格式错误") from None
        if not isinstance(value, dict):
            raise ValueError("请求格式错误")
        return value

    @app.get("/")
    def index():
        return FileResponse(ui / "index.html")

    @app.get("/app.js")
    def script():
        return FileResponse(ui / "app.js")

    @app.get("/style.css")
    def style():
        return FileResponse(ui / "style.css")

    @app.get("/favicon.svg")
    def favicon():
        return FileResponse(ui / "favicon.svg", media_type="image/svg+xml")

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        return {"initialized": bool(service.store.get("admin")), "authenticated": bool(request.state.session),
                "csrf": request.state.session["csrf"] if request.state.session else ""}

    @app.post("/api/setup")
    async def setup(request: Request):
        data = await body(request)
        with service.lock:
            if service.store.get("admin"):
                raise ValueError("管理员已经初始化")
            user = str(data.get("username", "admin")).strip()
            if not 1 <= len(user) <= 64:
                raise ValueError("用户名长度无效")
            service.store.set("admin", {"username": user, "password_hash": password_hash(str(data.get("password", "")))})
            service.store.audit("admin_initialized", {})
        return {"initialized": True}

    @app.post("/api/login")
    async def login(request: Request):
        if not limiter.allow("admin_login", 5):
            return JSONResponse({"detail": "登录失败次数过多，请 60 秒后重试"}, 429)
        data = await body(request)
        user = service.store.get("admin", {})
        if not user or data.get("username") != user["username"] or not password_check(str(data.get("password", "")), user["password_hash"]):
            return JSONResponse({"detail": "用户名或密码错误"}, 401)
        token, csrf = service.store.login()
        response = JSONResponse({"csrf": csrf})
        response.set_cookie("lb_admin", token, httponly=True, samesite="strict", max_age=28800)
        service.store.audit("admin_login", {})
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        service.store.logout(request.cookies.get("lb_admin", ""))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("lb_admin")
        return response

    @app.post("/api/shutdown")
    def shutdown_platform():
        if shutdown is None:
            raise ValueError("当前运行方式不支持网页退出，请在启动终端按 Ctrl+C")
        service.store.audit("platform_shutdown_requested", {})
        return JSONResponse({"stopping": True}, background=BackgroundTask(shutdown))

    @app.post("/api/password")
    async def change_password(request: Request):
        data = await body(request)
        with service.lock:
            user = service.store.get("admin")
            if not password_check(str(data.get("current", "")), user["password_hash"]):
                raise ValueError("当前密码错误")
            user["password_hash"] = password_hash(str(data.get("password", "")))
            service.store.set("admin", user)
            with service.store.lock, service.store.db:
                service.store.db.execute("DELETE FROM sessions")
            service.store.audit("admin_password_changed", {})
        return {"login_required": True}

    @app.get("/api/state")
    def state():
        with service.store.lock:
            publication = service.store.db.execute("SELECT action FROM audit WHERE action IN ('publish_verified','publish_incomplete') ORDER BY id DESC LIMIT 1").fetchone()
        managed = service.store.get("managed_business_token")
        if managed:
            managed = {k: v for k, v in managed.items() if k != "credential_digest"}
        managed_read = service.store.get("managed_read_token")
        if managed_read:
            managed_read = {k: v for k, v in managed_read.items() if k != "credential_digest"}
        return {"settings": service.settings(), "sites": service.sites(), "connector": service.connector.status(), "cloudflare_setup": service.cloudflare_setup(),
                "published_hosts": service.store.get("published_hosts", []),
                "publication_needs_review": bool(publication and publication[0] == "publish_incomplete"),
                "site_probes": {site["id"]: service.store.get("probe_" + site["id"]) for site in service.sites()},
                "cloudflare": service.store.get("cloudflare_status"), "audit": service.store.audit_list(),
                "cloudflare_permission_issues": service.permission_issues(),
                "token_management": {"authority_saved": bool(service.store.secret("cf_token_authority")), "managed": managed, "managed_read": managed_read, "pending": service.store.get("pending_business_token"), "pending_read": service.store.get("pending_read_token"), "error": service.store.get("token_management_error")},
                "credentials": {k: bool(service.store.secret(k)) for k in ("cf_read_token", "cf_write_token", "turnstile_secret", "tunnel_token")}}

    @app.post("/api/settings")
    async def settings(request: Request):
        data = await body(request)
        with service.lock:
            old = service.settings()
            # Ports and tunnel identity are CLI/runtime-owned; never silently change listeners.
            for k in ("admin_port", "gateway_port", "tunnel_id"):
                data[k] = old[k]
            cfg = Settings(**data).model_dump()
            if old["tunnel_id"] and any(old[k] != cfg[k] for k in ("account_id", "zone_id", "zone_name")):
                raise ValueError("Tunnel 已绑定账户和 Zone，不能直接切换；请使用独立数据目录")
            if service.sites() and old["zone_name"] != cfg["zone_name"]:
                raise ValueError("已有网站时不能切换 Zone")
            if cfg["turnstile_sitekey"] != old["turnstile_sitekey"]:
                service.store.set("owned_widget", "")
                sites = service.sites()
                for site in sites:
                    site["policy_version"] = secrets.token_hex(8)
                service.store.set("sites", sites)
                # Invalidate existing visitor grants when widget credentials change.
                service.store.set_secret("signing_key", secrets.token_urlsafe(48))
            service.store.set("settings", cfg)
            service.store.audit("settings_saved", {})
        return {"saved": True, "settings": cfg, "cloudflare_setup": service.cloudflare_setup(), "cloudflare_permission_issues": service.permission_issues()}

    @app.post("/api/credentials")
    async def credentials(request: Request):
        data = await body(request)
        with service.lock:
            values = {}
            remove_read = data.get("remove_cf_read_token", False)
            if not isinstance(remove_read, bool):
                raise ValueError("移除只读令牌选项格式无效")
            for k in ("cf_read_token", "cf_write_token", "turnstile_secret"):
                if not isinstance(data.get(k, ""), str):
                    raise ValueError("令牌格式无效")
                value = data.get(k, "").strip()
                if value:
                    if not 10 <= len(value) <= 4096:
                        raise ValueError("令牌长度无效")
                    values[k] = value
            if remove_read:
                if values.get("cf_read_token"):
                    raise ValueError("不能同时填写和移除只读令牌")
                values["cf_read_token"] = ""
            # Validate and encrypt every value before a single atomic database commit.
            encrypted = {k: service.store.cipher.encrypt(v.encode()).decode() for k, v in values.items()}
            import json
            changed = service.store.get("credential_updated_at", {})
            changed.update({k: time.time() for k in values})
            with service.store.lock, service.store.db:
                for k, value in encrypted.items():
                    service.store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (k, value))
                service.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("credential_updated_at", json.dumps(changed)))
                if "cf_write_token" in values:
                    for key in ("managed_business_token", "pending_business_token"):
                        service.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, "null"))
                if "cf_read_token" in values:
                    for key in ("managed_read_token", "pending_read_token"):
                        service.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, "null"))
            service.store.audit("credentials_updated", {})
        return {"saved": True, "cloudflare_setup": service.cloudflare_setup(),
                "cloudflare_permission_issues": service.permission_issues(),
                "credentials": {k: bool(service.store.secret(k)) for k in ("cf_read_token", "cf_write_token", "turnstile_secret", "tunnel_token")}}

    @app.post("/api/sites")
    async def sites(request: Request):
        return service.save_site(await body(request))

    @app.post("/api/sites/{site_id}/probe")
    async def probe(site_id: str):
        site = next((s for s in service.sites() if s["id"] == site_id), None)
        if not site:
            raise ValueError("网站不存在")
        base, host, sni = pinned_origin(site, service.settings())
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
                response = await client.get(base + "/", headers={"Host": host}, extensions={"sni_hostname": sni})
            result = {"http_status": response.status_code, "reachable": True, "checked_at": time.time()}
        except httpx.HTTPError:
            result = {"reachable": False, "checked_at": time.time()}
        result["origin"] = site["origin"]
        service.store.set("probe_" + site_id, result)
        return result

    @app.post("/api/cloudflare/{action}")
    async def cloudflare(action: str, request: Request):
        import asyncio
        data = await body(request)
        if action == "provision-token":
            from .token_manager import TokenManager
            if not isinstance(data.get("authority", ""), str) or not isinstance(data.get("remember", False), bool) or not isinstance(data.get("human_check", True), bool) or data.get("target", "write") not in ("write", "read") or not isinstance(data.get("repair_existing", False), bool) or not isinstance(data.get("force_new", False), bool):
                raise ValueError("授权令牌或选项格式无效")
            return await asyncio.to_thread(TokenManager(service).provision, data.get("authority", ""), data.get("remember", False), data.get("human_check", True), data.get("target", "write"), data.get("repair_existing", False), data.get("force_new", False))
        if action == "forget-token-authority":
            service.store.set_secret("cf_token_authority", "")
            return {"removed": True}
        if action == "create-tunnel":
            return await asyncio.to_thread(service.cf.create_tunnel)
        if action == "turnstile":
            return await asyncio.to_thread(service.cf.create_widget)
        if action == "preview":
            return await asyncio.to_thread(service.cf.plan)
        if action == "apply":
            return await asyncio.to_thread(service.cf.apply, data.get("revision", ""))
        if action == "check":
            result = await asyncio.to_thread(service.cf.status)
            service.store.set("cloudflare_status", result)
            return result
        raise ValueError("操作不存在")

    @app.post("/api/connector/{action}")
    async def connector(action: str, request: Request):
        import asyncio
        data = await body(request)
        if action == "ensure":
            path = data.get("path")
            if path is not None and not isinstance(path, str):
                raise ValueError("路径必须为文本")
            return await asyncio.to_thread(service.connector.ensure, path)
        if action == "start":
            return await asyncio.to_thread(service.connector.start)
        if action == "stop":
            return await asyncio.to_thread(service.connector.stop)
        raise ValueError("操作不存在")

    return app
