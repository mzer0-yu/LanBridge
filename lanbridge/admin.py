from __future__ import annotations
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
import time

import httpx
from fastapi import FastAPI, Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask
from .gateway import Limiter, OwnedStreamingResponse, disabled_client_response
from .models import Settings
from .service import pinned_origin
from .store import password_check, password_hash
from pydantic import ValidationError


PUBLIC_CLIENT_PATHS = frozenset({"/client", "/client/", "/client.js", "/client.css", "/api/client/routes"})


def create_admin(service, shutdown=None, *, remote=False, restart=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    from .browser_auth import BrowserAuth
    browser_auth = None if remote else BrowserAuth(service)
    if not remote:
        service.browser_auth = browser_auth
    limiter = Limiter()
    from .local_login import COOKIE, LIFETIME, LocalLogin, open_browser, available_browsers, BROWSER_NAMES
    local_login = LocalLogin(service.store)
    app.state.local_login = local_login
    ui = Path(__file__).resolve().parent.parent / "ui"

    @app.middleware("http")
    async def guard(request, call_next):
        cfg = service.settings()
        host = request.headers.get("host", "")
        if remote:
            allowed = {s["hostname"] for s in service.sites() if s["enabled"] and not s.get("paused") and s.get("target") == "lanbridge"}
            if host not in allowed or request.url.scheme != "https":
                return JSONResponse({"detail": "远程管理入口不可用"}, 403)
            if not service.store.get("admin"):
                return JSONResponse({"detail": "请先在本机创建管理员"}, 503)
            if request.url.path in {"/api/setup", "/api/shutdown", "/api/restart", "/api/password", "/api/admin-port", "/api/connector-auto-start", "/api/gateway-port", "/api/gateway-retry"} or request.url.path.startswith("/api/domain-onboarding") or request.url.path.startswith("/api/local-login/") or request.url.path.startswith("/api/cloudflare/browser-authorize"):
                return JSONResponse({"detail": "此操作仅支持本机访问"}, 403)
            if not service.store.get("public_client_enabled", True) and request.url.path in PUBLIC_CLIENT_PATHS:
                return disabled_client_response(request.url.path)
            expected_origin = "https://" + host
        else:
            allowed = {f'127.0.0.1:{cfg["admin_port"]}', f'localhost:{cfg["admin_port"]}'}
            if host not in allowed:
                return JSONResponse({"detail": "管理台只接受本机访问"}, 403)
            if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
                return JSONResponse({"detail": "管理台仅监听本机"}, 403)
            expected_origin = "http://" + host
        if request.url.path == "/api/launcher/control":
            token = getattr(service, "launcher_control_token", "")
            if remote or request.method != "POST" or request.headers.get("origin") is not None or not token or not secrets.compare_digest(request.headers.get("x-lanbridge-control", "").encode("utf-8"), token.encode("utf-8")) or request.headers.get("x-lanbridge-instance") != getattr(service, "runtime_id", None):
                return JSONResponse({"detail": "启动器控制身份校验失败"}, 403)
            if getattr(service, "restart_plan", None):
                return JSONResponse({"detail": "平台正在重启"}, 409)
            try:
                response = await call_next(request)
            except (ValueError, RuntimeError, OSError):
                response = JSONResponse({"detail": "实例操作未完成，请检查实例状态和待生效端口"}, 400)
            response.headers["Cache-Control"] = "no-store"
            return response
        authorization = request.headers.get("authorization", "")
        bearer = authorization.startswith("Bearer ")
        grant = service.store.temporary_token(authorization[7:]) if bearer else None
        if bearer and not grant:
            return JSONResponse({"detail": "临时管理 Token 无效、已到期或已撤销"}, 401)
        if request.method not in ("GET", "HEAD") and (not bearer or request.headers.get("origin")) and request.headers.get("origin") != expected_origin:
            return JSONResponse({"detail": "请求来源校验失败"}, 403)
        public = request.url.path in ("/", "/admin", "/admin/", "/client", "/client/", "/client.js", "/client.css", "/api/client/routes", "/app.js", "/domain-onboarding.js", "/local-login.js", "/style.css", "/favicon.svg", "/api/bootstrap", "/api/setup", "/api/login", "/api/token-login", "/api/local-login/browsers", "/api/local-login/start", "/api/local-login/open", "/api/local-login/poll", "/api/local-login/cancel")
        session = service.store.session(request.cookies.get("lb_admin", ""))
        if grant:
            session = grant | {"csrf": "", "access_token_id": grant["id"]}
        from .temporary_access import allowed as temporary_allowed
        if session and session.get("scope") == "sites" and not public and not temporary_allowed(request.method, request.url.path, session.get("permissions", ["sites"])):
            return JSONResponse({"detail": "此临时管理 Token 未获授此操作权限"}, 403)
        if not public and not session:
            return JSONResponse({"detail": "请登录管理员账户"}, 401)
        if not bearer and not public and request.method not in ("GET", "HEAD") and request.headers.get("x-csrf-token") != session["csrf"]:
            return JSONResponse({"detail": "CSRF 校验失败，请刷新管理台"}, 403)
        request.state.session = session
        if getattr(service, "restart_plan", None) and request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "平台正在重启，请稍候"}, 409)
        if session and session.get("access_token_id") and request.url.path.startswith("/api/"):
            service.store.touch_temporary_token(session["access_token_id"])
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
        import anyio
        raw = bytearray()
        try:
            with anyio.fail_after(10):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > limit:
                        raise ValueError("请求内容过大")
                    raw.extend(chunk)
        except TimeoutError:
            raise ValueError("请求接收超时，请重试") from None
        import json
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError):
            raise ValueError("请求格式错误") from None
        if not isinstance(value, dict):
            raise ValueError("请求格式错误")
        return value

    @app.get("/")
    def index():
        return RedirectResponse("/admin" if remote and not service.store.get("public_client_enabled", True) else "/client", status_code=307)

    @app.get("/admin")
    def admin_page():
        return FileResponse(ui / "index.html")

    @app.get("/admin/")
    def admin_slash():
        return RedirectResponse("/admin", status_code=307)

    @app.get("/client")
    def client_page():
        return FileResponse(ui / "client.html")

    @app.get("/client/")
    def client_slash():
        return RedirectResponse("/client", status_code=307)

    @app.get("/client.js")
    def client_script():
        return FileResponse(ui / "client.js")

    @app.get("/client.css")
    def client_style():
        return FileResponse(ui / "client.css")

    @app.get("/api/client/routes")
    def client_routes():
        with service.lock:
            published = set(service.store.get("published_hosts", []))
            with service.store.lock:
                publication = [service.store.get("last_publication_action")]
            review = bool(publication and publication[0] == "publish_incomplete")
            routes = [{"name": site["name"], "hostname": site["hostname"], "origin": site["origin"],
                       "published": site["hostname"] in published,
                       "status": "已暂停" if site.get("paused") else "未发布" if site["hostname"] not in published else "待核验" if review else "已发布"}
                      for site in service.sites() if site["enabled"]]
            return {"routes": routes, "updated_at": time.time()}

    @app.get("/app.js")
    def script():
        return FileResponse(ui / "app.js")

    @app.get("/domain-onboarding.js")
    def domain_onboarding_script():
        return FileResponse(ui / "domain-onboarding.js")

    def require_domain_admin(request):
        session = request.state.session or {}
        if remote or session.get("scope", "admin") != "admin" or session.get("access_token_id"):
            raise ValueError("域名迁移仅支持本机管理员")

    @app.get("/api/domain-onboarding")
    def domain_onboarding_status(request: Request):
        require_domain_admin(request)
        return service.domain_onboarding.status()

    @app.get("/api/domain-onboarding/help")
    def domain_onboarding_help(request: Request):
        require_domain_admin(request)
        return FileResponse(ui.parent / "docs" / "domain-onboarding.md", media_type="text/plain; charset=utf-8")

    @app.get("/api/domain-onboarding/connected-domains")
    async def aliyun_connected_domains(request: Request):
        require_domain_admin(request)
        result = await asyncio.to_thread(service.domain_onboarding.connected_domains, refresh=request.query_params.get('refresh') == '1')
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/domain-onboarding/backup")
    def domain_onboarding_backup(request: Request):
        require_domain_admin(request)
        return JSONResponse(service.domain_onboarding.backup(), headers={"Content-Disposition": 'attachment; filename="domain-dns-backup.json"'})

    @app.post("/api/domain-onboarding/{action}")
    async def domain_onboarding_action(action: str, request: Request):
        require_domain_admin(request)
        values = await body(request)
        manager = service.domain_onboarding
        if action == "oauth":
            return await asyncio.to_thread(manager.authorize_aliyun)
        if action == "credentials":
            return await asyncio.to_thread(manager.save_credentials, values)
        if action == "dismiss-error":
            return await asyncio.to_thread(manager.dismiss_prepare_failure, values.get("checked_at"))
        if action == "prepare":
            return await asyncio.to_thread(manager.prepare, values.get("domain"))
        if action in {"confirm", "finish"}:
            fn = manager.confirm if action == "confirm" else manager.finish_tracking
            return await asyncio.to_thread(fn, values.get("id"), values.get("confirmed_domain"))
        if action == "check":
            return await asyncio.to_thread(manager.check)
        if action == "cancel":
            return await asyncio.to_thread(manager.cancel, values.get("id"))
        raise ValueError("未知域名接入操作")

    @app.get("/style.css")
    def style():
        return FileResponse(ui / "style.css")

    @app.get("/local-login.js")
    def local_login_script():
        return FileResponse(ui / "local-login.js")

    @app.get("/favicon.svg")
    def favicon():
        return FileResponse(ui / "favicon.svg", media_type="image/svg+xml")

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        return {"initialized": bool(service.store.get("admin")), "authenticated": bool(request.state.session),
                "instance": getattr(service, "runtime_id", None) if not remote else None,
                "csrf": request.state.session["csrf"] if request.state.session else "", "remote": remote,
                "public_client_enabled": service.store.get("public_client_enabled", True),
                "scope": request.state.session.get("scope", "admin") if request.state.session else None,
                "permissions": request.state.session.get("permissions", []) if request.state.session else []}

    @app.post("/api/temporary-tokens")
    async def issue_temporary_token(request: Request):
        data = await body(request)
        return service.store.issue_temporary_token(data.get("name", ""), data.get("hours", 24), data.get("permissions"))

    @app.get("/api/temporary-tokens")
    def list_temporary_tokens():
        return {"tokens": service.store.temporary_tokens()}

    @app.post("/api/temporary-tokens/{token_id}/revoke")
    def revoke_temporary_token(token_id: str):
        service.store.revoke_temporary_token(token_id)
        return {"revoked": True}

    @app.post("/api/temporary-tokens/{token_id}/reveal")
    def reveal_temporary_token(token_id: str):
        return {"id": token_id, "token": service.store.reveal_temporary_token(token_id)}

    @app.post("/api/token-login")
    async def token_login(request: Request):
        if not limiter.allow(("temporary_login", request.client.host if request.client else ""), 10):
            return JSONResponse({"detail": "尝试过于频繁，请稍后重试"}, 429)
        data = await body(request)
        try:
            token, csrf, grant = service.store.temporary_login(data.get("token", ""))
        except ValueError:
            return JSONResponse({"detail": "临时管理 Token 无效、已到期或已撤销"}, 401)
        response = JSONResponse({"csrf": csrf, "scope": "sites", "permissions": grant["permissions"], "expires": grant["expires"]})
        response.set_cookie("lb_admin", token, httponly=True, secure=remote, samesite="strict", max_age=max(1, min(2147483647, int(grant["expires"]-time.time()))))
        service.store.audit("temporary_token_login", {"id": grant["id"]})
        return response

    @app.post("/api/setup")
    async def setup(request: Request):
        data = await body(request)
        def save(data):
            with service.lock:
                if service.store.get("admin"):
                    raise ValueError("管理员已经初始化")
                user = str(data.get("username", "admin")).strip()
                if not 1 <= len(user) <= 64:
                    raise ValueError("用户名长度无效")
                service.store.set("admin", {"username": user, "password_hash": password_hash(str(data.get("password", "")))})
                service.store.audit("admin_initialized", {})
            return {"initialized": True}
        return await asyncio.to_thread(save, data)

    @app.post("/api/login")
    async def login(request: Request):
        if not limiter.allow(("remote_admin_login", request.client.host if request.client else "") if remote else "admin_login", 5):
            return JSONResponse({"detail": "登录失败次数过多，请 60 秒后重试"}, 429)
        if remote and not limiter.allow("remote_admin_login_total", 30):
            return JSONResponse({"detail": "登录请求过于频繁，请稍后重试"}, 429)
        import asyncio
        data = await body(request)
        user = service.store.get("admin", {})
        if not user or data.get("username") != user["username"] or not await asyncio.to_thread(password_check, str(data.get("password", "")), user["password_hash"]):
            return JSONResponse({"detail": "用户名或密码错误"}, 401)
        try:
            token, csrf = service.store.login(expected_password_hash=user["password_hash"])
        except ValueError:
            return JSONResponse({"detail": "管理员凭据已变化，请重新登录"}, 401)
        response = JSONResponse({"csrf": csrf})
        response.set_cookie("lb_admin", token, httponly=True, secure=remote, samesite="strict", max_age=28800)
        service.store.audit("admin_login", {})
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        service.store.logout(request.cookies.get("lb_admin", ""))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("lb_admin")
        return response

    @app.get("/api/local-login/browsers")
    def local_login_browsers():
        return {"browsers": available_browsers()}

    @app.post("/api/local-login/start")
    async def start_local_login(request: Request):
        if not service.store.get("admin"):
            raise ValueError("请先创建管理员账户")
        data = await body(request)
        browser = data.get("browser", "default")
        if not isinstance(browser, str) or browser not in {item["id"] for item in available_browsers()}:
            raise ValueError("所选浏览器不可用，请重新选择")
        if not limiter.allow("local_login_start", 10):
            return JSONResponse({"detail": "请求过于频繁，请稍后重试"}, 429)
        request_id, proof, details = local_login.start(request.cookies.get(COOKIE, ""), browser)
        url = f'http://{request.headers["host"]}/admin#local-login={request_id}'
        response = JSONResponse(details | {"request_id": request_id, "approval_url": url, "browser_opened": False, "browser_name": BROWSER_NAMES[browser]})
        response.set_cookie(COOKIE, proof, httponly=True, samesite="strict", max_age=LIFETIME, path="/api/local-login")
        return response

    @app.post("/api/local-login/open")
    async def open_local_login(request: Request):
        import asyncio
        data = await body(request)
        request_id = str(data.get("request_id", ""))
        browser = local_login.browser_for_open(request_id, request.cookies.get(COOKIE, ""))
        if not limiter.allow("local_login_open", 10):
            return JSONResponse({"detail": "请求过于频繁，请稍后重试"}, 429)
        url = f'http://{request.headers["host"]}/admin#local-login={request_id}'
        return {"browser_opened": await asyncio.to_thread(open_browser, url, browser)}

    @app.post("/api/local-login/poll")
    @app.post("/api/local-login/cancel")
    async def poll_local_login(request: Request):
        data = await body(request)
        result, token = local_login.poll(str(data.get("request_id", "")), request.cookies.get(COOKIE, ""), request.url.path.endswith("/cancel"),
                                        existing_token=request.cookies.get("lb_admin", ""))
        response = JSONResponse(result)
        if token:
            response.set_cookie("lb_admin", token, httponly=True, secure=remote, samesite="strict", max_age=28800)
        if result["phase"] != "pending":
            response.delete_cookie(COOKIE, path="/api/local-login")
        return response

    @app.get("/api/local-login/request/{request_id}")
    def local_login_request(request_id: str):
        return local_login.details(request_id)

    @app.post("/api/local-login/approve")
    async def approve_local_login(request: Request):
        data = await body(request)
        if not isinstance(data.get("allow"), bool):
            raise ValueError("请选择允许或拒绝")
        local_login.decide(str(data.get("request_id", "")), data.get("code", ""), data["allow"])
        return {"approved": data["allow"]}

    @app.post("/api/shutdown")
    def shutdown_platform():
        if shutdown is None:
            raise ValueError("当前运行方式不支持网页退出，请在启动终端按 Ctrl+C")
        service.store.audit("platform_shutdown_requested", {})
        return JSONResponse({"stopping": True}, background=BackgroundTask(shutdown))

    @app.post("/api/restart")
    def restart_platform():
        if restart is None or shutdown is None:
            raise ValueError("当前运行方式不支持网页重启，请通过启动器重新启动")
        result = restart()
        service.store.audit("platform_restart_requested", {"admin_port": result["port"]})
        return JSONResponse(result, background=BackgroundTask(shutdown))

    @app.post("/api/launcher/control")
    async def launcher_control(request: Request):
        operation = (await body(request)).get("operation")
        if operation == "restart":
            return await asyncio.to_thread(restart_platform)
        if operation == "stop":
            return await asyncio.to_thread(shutdown_platform)
        raise ValueError("启动器操作无效")

    @app.post("/api/password")
    async def change_password(request: Request):
        data = await body(request)
        def save(data):
            with service.lock:
                user = service.store.get("admin")
                if not password_check(str(data.get("current", "")), user["password_hash"]):
                    raise ValueError("当前密码错误")
                user["password_hash"] = password_hash(str(data.get("password", "")))
                with service.store.lock, service.store.db:
                    service.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("admin", json.dumps(user)))
                    service.store.db.execute("DELETE FROM sessions")
                service.store.audit("admin_password_changed", {})
            return {"login_required": True}
        return await asyncio.to_thread(save, data)

    @app.get("/api/state")
    def state(request: Request):
        limited = request.state.session.get("scope") == "sites"
        with service.store.lock:
            publication = [service.store.get("last_publication_action")]
        managed = service.store.get("managed_business_token")
        if managed:
            managed = {k: v for k, v in managed.items() if k != "credential_digest"}
        managed_read = service.store.get("managed_read_token")
        if managed_read:
            managed_read = {k: v for k, v in managed_read.items() if k != "credential_digest"}
        configuration = service.configuration_snapshot()
        sites_snapshot = configuration['sites']
        result = {**configuration, "pending_admin_port": service.store.get("pending_admin_port"), "restart_warning": service.store.get("restart_warning"), "pending_gateway_port": service.store.get("pending_gateway_port"), "connector": service.connector.status(),
                "agent_skill_path": str((ui.parent / "skills" / "lanbridge" / "SKILL.md").resolve()) if not remote else None,
                "tunnel_pending": bool(service.store.get("pending_tunnel_create")),
                "published_hosts": service.store.get("published_hosts", []),
                "publication_needs_review": bool(publication and publication[0] == "publish_incomplete"),
                "publication_error": service.store.get("publication_error"),
                "site_probes": {site["id"]: service.store.get("probe_" + site["id"]) for site in sites_snapshot},
                "cloudflare": service.store.get("cloudflare_status"), "audit": [] if limited else service.store.audit_list(), "audit_storage": {} if limited else service.store.audit_stats(),
                "cloudflare_permission_issues": service.permission_issues(),
                "oauth_refresh": service.oauth_refresh_status(),
                "browser_auth": browser_auth.status() if browser_auth else {"phase": "local_only", "message": "浏览器授权请在本机完成"},
                "token_management": {"authority_saved": bool(service.store.secret("cf_token_authority")), "managed": managed, "managed_read": managed_read, "pending": service.store.get("pending_business_token") or service.store.get("pending_browser_token"), "pending_read": service.store.get("pending_read_token"), "error": service.store.get("token_management_error")},
                }
        result["site_publication"] = service.site_publication.status()
        result["domain_onboarding"] = None
        if not remote and not limited:
            job = service.domain_onboarding.status()["job"]
            if job:
                result["domain_onboarding"] = {key: job[key] for key in ("domain", "phase")}
        updates = getattr(service, "update_controller", None)
        result["update_status"] = updates.status() if updates else None
        result["public_client_enabled"] = service.store.get("public_client_enabled", True)
        result["connector_auto_start"] = service.store.get("connector_auto_start", True)
        result["connector_auto_start_supported"] = True
        result["connector_startup_warning"] = None if result["connector"]["running"] else getattr(service, "connector_startup_warning", None)
        result["admin_port_supported"] = True
        result["restart_supported"] = bool(restart and shutdown) and not remote
        runtime = getattr(service, "gateway_runtime", None)
        result["gateway"] = runtime.status() if runtime else None
        result["access_scope"] = request.state.session.get("scope", "admin")
        result["access_permissions"] = request.state.session.get("permissions", [])
        result["access_expires"] = request.state.session.get("expires")
        if result["access_scope"] == "sites":
            result.update(audit=[], audit_storage={}, agent_skill_path=None, pending_gateway_port=None, pending_admin_port=None, restart_warning=None)
            if "account" not in result["access_permissions"]:
                result.update(token_management={}, cloudflare_permission_issues=[], oauth_refresh=None, browser_auth={"phase": "local_only"})
                result["settings"]["cloudflared_path"] = ""
        return result

    @app.post("/api/public-client")
    async def public_client(request: Request):
        data = await body(request)
        return await asyncio.to_thread(service.set_public_client_enabled, data.get("enabled"))

    @app.post("/api/connector-auto-start")
    async def connector_auto_start(request: Request):
        data = await body(request)
        return await asyncio.to_thread(service.set_connector_auto_start, data.get("enabled"))

    @app.post("/api/gateway-port")
    async def gateway_port(request: Request):
        if remote:
            raise ValueError("网关端口请在本机管理台修改")
        data = await body(request)
        result = await asyncio.to_thread(service.queue_gateway_port, data.get("port"))
        runtime = getattr(service, "gateway_runtime", None)
        if runtime and not runtime.status()["running"]:
            status = await asyncio.to_thread(runtime.start, data.get("port"))
            if not status["running"]:
                raise ValueError(status["error"])
            result.update(restart_required=False, recovered=True, gateway=status)
        return result

    @app.post("/api/admin-port")
    async def admin_port(request: Request):
        data = await body(request)
        return await asyncio.to_thread(service.queue_admin_port, data.get("port"))

    @app.post("/api/gateway-retry")
    async def gateway_retry(request: Request):
        if remote:
            raise ValueError("转发网关请在本机管理台启动")
        runtime = getattr(service, "gateway_runtime", None)
        if not runtime:
            raise ValueError("当前实例不支持网关恢复，请重新启动 LanBridge")
        status = await asyncio.to_thread(runtime.start)
        if not status["running"]:
            raise ValueError(status["error"])
        return status

    @app.get("/api/audit/export")
    def audit_export():
        filename = "lanbridge-log-" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S") + ".jsonl"
        snapshot = service.store.audit_export()
        async def close_snapshot():
            await asyncio.to_thread(snapshot.close)
        return OwnedStreamingResponse(iter(lambda: snapshot.read(65536), b""), close=close_snapshot,
                                      media_type="application/x-ndjson",
                                      headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/api/audit")
    def audit_page(before: int | None = None):
        return {"records": service.store.audit_list(before), "storage": service.store.audit_stats()}

    @app.post("/api/audit/settings")
    async def audit_settings(request: Request):
        data = await body(request)
        try:
            return service.store.set_audit_limit(data.get("limit_mb"))
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.post("/api/settings")
    async def settings(request: Request):
        data = await body(request)
        def save(data):
            with service.lock:
                old = service.settings()
                if request.state.session.get("scope") == "sites":
                    account_fields = {"account_id", "zone_id", "zone_name", "tunnel_name"}
                    if any(k not in account_fields and v != old.get(k) for k, v in data.items()):
                        return JSONResponse({"detail": "此 Token 只能修改账户与域名配置"}, 403)
                    data = old | {k: v for k, v in data.items() if k in account_fields}
                # Ports and tunnel identity are CLI/runtime-owned; never silently change listeners.
                for k in ("admin_port", "gateway_port", "tunnel_id"):
                    data[k] = old[k]
                # Domains are verified by the dedicated endpoint, never replaced by a partial form.
                if "zones" in data and data["zones"] != old["zones"]:
                    raise ValueError("请通过域名管理添加或移除域名")
                data["zones"] = old["zones"]
                cfg = Settings(**data).model_dump()
                if old["account_id"] and old["zones"] and old["account_id"] != cfg["account_id"]:
                    raise ValueError("已接入域名属于当前账户，跨账户请使用独立数据目录")
                if old["tunnel_id"] and any(old[k] != cfg[k] for k in ("account_id", "zone_id", "zone_name")):
                    raise ValueError("Tunnel 已绑定账户和 Zone，不能直接切换；请使用独立数据目录")
                if service.sites() and old["zone_name"] != cfg["zone_name"]:
                    raise ValueError("已有网站时不能切换 Zone")
                service.commit_settings(cfg)
                service.store.audit("settings_saved", {})
            return {"saved": True, "settings": cfg, "cloudflare_setup": service.cloudflare_setup(), "cloudflare_permission_issues": service.permission_issues()}
        return await asyncio.to_thread(save, data)

    @app.post("/api/cloudflare/token-discover")
    async def token_discover(request: Request):
        from .token_access import TokenAccess
        data = await body(request)
        return await asyncio.to_thread(TokenAccess(service).discover, data.get("token"))

    @app.post("/api/cloudflare/token-connect")
    async def token_connect(request: Request):
        from .token_access import TokenAccess
        data = await body(request)
        return await asyncio.to_thread(TokenAccess(service).connect, data)

    @app.post("/api/cloudflare/zones")
    async def available_zones(request: Request):
        await body(request)
        return {"zones": await asyncio.to_thread(service.cf.available_zones)}

    @app.post("/api/zones")
    async def add_zone(request: Request):
        data = await body(request)
        cfg = await asyncio.to_thread(service.add_zone, data)
        return {"settings": cfg, "saved": True}

    @app.post("/api/zones/{zone_id}/remove")
    async def remove_zone(zone_id: str, request: Request):
        await body(request)
        cfg = await asyncio.to_thread(service.remove_zone, zone_id)
        return {"settings": cfg, "saved": True}

    @app.post("/api/zones/{zone_id}/default")
    async def set_default_zone(zone_id: str, request: Request):
        await body(request)
        cfg = await asyncio.to_thread(service.set_default_zone, zone_id)
        return {"settings": cfg, "saved": True}

    @app.post("/api/credentials")
    async def credentials(request: Request):
        data = await body(request)
        def save(data):
            if request.state.session.get("scope") == "sites" and any(k not in {"cf_write_token", "cf_read_token", "remove_cf_read_token"} and v for k, v in data.items()):
                return JSONResponse({"detail": "此 Token 只能配置 Cloudflare 接入令牌"}, 403)
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
                        service.store.db.execute("DELETE FROM secrets WHERE key=?", ("cf_oauth_profile",))
                        for key in ("managed_business_token", "pending_business_token", "pending_browser_token"):
                            service.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, "null"))
                    if "cf_read_token" in values:
                        for key in ("managed_read_token", "pending_read_token"):
                            service.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, "null"))
                service.store.audit("credentials_updated", {})
            return {"saved": True, "cloudflare_setup": service.cloudflare_setup(),
                    "cloudflare_permission_issues": service.permission_issues(),
                    "credentials": {k: bool(service.store.secret(k)) for k in ("cf_read_token", "cf_write_token", "turnstile_secret", "tunnel_token")}}
        return await asyncio.to_thread(save, data)

    @app.post("/api/sites")
    async def sites(request: Request):
        import asyncio
        data = await body(request)
        background = data.pop('background', False)
        retry = data.pop('retry_publication', False)
        if type(background) is not bool or type(retry) is not bool:
            raise ValueError('后台保存选项格式无效')
        if background:
            if retry and data:
                raise ValueError('重试发布不能同时修改网站配置')
            return await asyncio.to_thread(service.site_publication.submit, None if retry else data)
        if retry:
            raise ValueError('重试发布需要后台模式')
        return await asyncio.to_thread(service.save_site, data, synchronize_verification=True, auto_publish=True)

    @app.post("/api/sites/{site_id}/probe")
    async def probe(site_id: str):
        site = next((s for s in service.sites() if s["id"] == site_id), None)
        if not site:
            raise ValueError("网站不存在")
        if site.get("target") == "lanbridge":
            return {"http_status": 200, "reachable": True, "checked_at": time.time(), "origin": site["origin"]}
        base, host, sni = await asyncio.to_thread(pinned_origin, site, service.settings())
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
                response = await client.get(base + "/", headers={"Host": host}, extensions={"sni_hostname": sni})
            result = {"http_status": response.status_code, "reachable": True, "checked_at": time.time()}
        except httpx.HTTPError:
            result = {"reachable": False, "checked_at": time.time()}
        result["origin"] = site["origin"]
        service.store.set("probe_" + site_id, result)
        return result

    @app.post("/api/sites/{site_id}/pause")
    async def pause_site(site_id: str, request: Request):
        import asyncio
        data = await body(request)
        return await asyncio.to_thread(service.set_site_paused, site_id, data.get("paused"))

    @app.post("/api/cloudflare/{action}")
    async def cloudflare(action: str, request: Request):
        import asyncio
        data = await body(request)
        if action == "browser-authorize":
            return browser_auth.start(data.get("browser", "default"))
        if action == "browser-authorize-refresh":
            if remote or request.state.session.get("scope", "admin") != "admin" or request.state.session.get("access_token_id"):
                raise ValueError("请在本机使用管理员账户重试续期")
            if (service.store.get("managed_business_token") or {}).get("kind") != "oauth":
                raise ValueError("当前未使用 Cloudflare 浏览器授权")
            await asyncio.to_thread(browser_auth.access_token, force_refresh=True)
            return {"saved": True}
        if action == "browser-authorize-setup":
            return browser_auth.continue_setup()
        if action == "browser-authorize-cancel":
            return await asyncio.to_thread(browser_auth.cancel)
        if action == "browser-authorize-restart":
            return await asyncio.to_thread(browser_auth.restart, data.get("browser"))
        if action == "browser-authorize-select-zone":
            if not isinstance(data.get("zone_id"), str):
                raise ValueError("请选择域名")
            return browser_auth.choose_zone(data["zone_id"])
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
        if action == "turnstile-auto":
            if not service.cf.widgets_authorized():
                if remote:
                    raise ValueError("请在本机浏览器授权补充 Turnstile 权限")
                return browser_auth.start()
            return await asyncio.to_thread(service.cf.create_widget)
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
        if action == "check-update":
            return await asyncio.to_thread(service.connector.check_update)
        if action == "update":
            if remote:
                raise ValueError("更新连接器请使用本机管理台，避免中断公网管理连接")
            tag = data.get("version")
            if not isinstance(tag, str):
                raise ValueError("请先检查更新")
            return await asyncio.to_thread(service.connector.update, tag)
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
