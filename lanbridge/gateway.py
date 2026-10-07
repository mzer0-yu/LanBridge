from __future__ import annotations
import asyncio
import base64
from collections import OrderedDict
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import secrets
import tempfile
import socket
import threading
import time
from urllib.parse import urlsplit
from pathlib import Path

import httpx
import anyio
from fastapi import FastAPI, Request, WebSocket
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
import websockets

from .service import pinned_origin
from .store import password_check
from .upstream import UpstreamPools
from .gateway_log import transfer_logger
from contextlib import asynccontextmanager

def disabled_client_response(path):
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}
    if path in {"/client", "/client/"}:
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        return FileResponse(Path(__file__).resolve().parent.parent / "ui" / "client-disabled.html", status_code=404, media_type="text/html", headers=headers)
    return JSONResponse({"detail": "公网转发列表未启用"}, status_code=404, headers=headers)


PASS_COOKIE = "__Host-lanbridge-pass"
PREFIX = "/.lanbridge"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}


class TransferLog:
    """Correlate response framing failures without logging cookies or query strings."""
    def __init__(self, app, logger=None):
        self.app = app
        self.logger = logger or logging.getLogger("uvicorn.error")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        transfer = {"id": secrets.token_hex(8), "sent": 0, "read": 0, "status": None,
                    "length": None, "encoding": None, "complete": False}
        started = time.monotonic()
        scope.setdefault("state", {})["transfer"] = transfer
        failure = None
        async def tracked_send(message):
            if message["type"] == "http.response.start":
                headers = dict(message["headers"])
                transfer.update(status=message["status"], length=headers.get(b"content-length", b"").decode(),
                                encoding=headers.get(b"content-encoding", b"identity").decode())
                message = dict(message, headers=[*message["headers"], (b"x-lanbridge-request-id", transfer["id"].encode())])
            await send(message)
            if message["type"] == "http.response.body":
                transfer["sent"] += len(message.get("body", b""))
                transfer["complete"] = not message.get("more_body", False)
        try:
            await self.app(scope, receive, tracked_send)
        except BaseException as exc:
            failure = type(exc).__name__
            raise
        finally:
            if failure or transfer.get("upstream_error") or transfer["sent"] >= 1024 * 1024 or (transfer["status"] and (transfer["status"] >= 500 or not transfer["complete"])):
                self.logger.warning("gateway_transfer %s", json.dumps({
                    "request_id": transfer["id"], "method": scope["method"], "path": scope["path"][:1024],
                    "status": transfer["status"], "content_length": transfer["length"],
                    "content_encoding": transfer["encoding"], "upstream_bytes": transfer["read"],
                    "upstream_length": transfer.get("upstream_length"), "upstream_error": transfer.get("upstream_error"),
                    "downstream_bytes": transfer["sent"], "complete": transfer["complete"], "error": failure,
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                    "upstream_headers_ms": transfer.get("upstream_headers_ms")}))


class PolicyMiddleware:
    """Apply access checks without BaseHTTPMiddleware's response-stream relay."""
    def __init__(self, app, check):
        self.app, self.check = app, check

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            response = await self.check(Request(scope, receive))
            if response is not None:
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


class OwnedStreamingResponse(StreamingResponse):
    """Release resources even when sending fails or the consumer disconnects."""
    def __init__(self, *args, close, **kwargs):
        super().__init__(*args, **kwargs)
        self.close = close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.close()


class Limiter:
    def __init__(self):
        self.buckets = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key, count, window=60):
        now = time.monotonic()
        with self.lock:
            bucket = self.buckets.get(key)
            if bucket is None and len(self.buckets) >= 10000:
                oldest = next(iter(self.buckets))
                if now - self.buckets[oldest][0] < window:
                    return False
                self.buckets.pop(oldest)
            if bucket is None or now - bucket[0] >= window:
                bucket = [now, 0]
                self.buckets[key] = bucket
            self.buckets.move_to_end(key)
            while len(self.buckets) > 10000:
                self.buckets.popitem(last=False)
            if bucket[1] >= count:
                return False
            bucket[1] += 1
            return True


class ResourceLimits:
    """Bound active work before reading bodies; hold slots until sending finishes."""
    def __init__(self, app, http=128, websocket=32, verify=8, per_ip=16, rate=100, burst=200):
        self.app = app
        self.limits = {"http": http, "websocket": websocket, "verify": verify}
        self.active = {kind: 0 for kind in self.limits}
        self.peers = {}
        self.per_ip = per_ip
        self.rate, self.burst, self.tokens, self.updated = rate, burst, burst, time.monotonic()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < 1:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            else:
                await Response("请求过于频繁，请稍后重试", 429, headers={"Retry-After": "1", "Cache-Control": "no-store"})(scope, receive, send)
            return
        self.tokens -= 1
        kind = "verify" if scope["type"] == "http" and scope["path"] == PREFIX + "/verify" else scope["type"]
        ip, _ = visitor(Request(scope) if scope["type"] == "http" else WebSocket(scope, receive, send))
        key = (kind, ip)
        peer_limit = min(self.per_ip, 2 if kind == "verify" else 4 if kind == "websocket" else self.per_ip)
        if self.active[kind] >= self.limits[kind] or self.peers.get(key, 0) >= peer_limit:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            else:
                await Response("网关繁忙，请稍后重试", 503, headers={"Retry-After": "5", "Cache-Control": "no-store"})(scope, receive, send)
            return
        self.active[kind] += 1
        self.peers[key] = self.peers.get(key, 0) + 1
        async def bounded_send(message):
            with anyio.fail_after(120):
                await send(message)
        try:
            await self.app(scope, receive, bounded_send)
        finally:
            self.active[kind] -= 1
            self.peers[key] -= 1
            if not self.peers[key]:
                del self.peers[key]


class BufferBudget:
    """Reserve declared response bytes, including buffers held by slow consumers."""
    def __init__(self, total=512 * 1024 * 1024, single=256 * 1024 * 1024):
        self.total, self.single, self.used = total, single, 0

    def reserve(self, size):
        if size > self.single or self.used + size > self.total:
            return False
        self.used += size
        return True


class TokenBudget:
    def __init__(self, rate, burst):
        self.rate, self.burst, self.tokens, self.updated = rate, burst, burst, time.monotonic()

    def allow(self, cost=1):
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if cost > self.tokens:
            return False
        self.tokens -= cost
        return True


def bounded_length(value, maximum):
    # Bound decimal conversion as well as the body; leading zeros are harmless.
    if not value.isascii() or not value.isdecimal():
        raise OverflowError()
    normalized = value.lstrip("0") or "0"
    if len(normalized) > len(str(maximum)) or int(normalized) > maximum:
        raise OverflowError()
    return int(normalized)


async def bounded_body(request, maximum=4096, seconds=10):
    length = request.headers.get("content-length")
    if length is not None:
        bounded_length(length, maximum)
    body = bytearray()
    with anyio.fail_after(seconds):
        async for chunk in request.stream():
            if len(body) + len(chunk) > maximum:
                raise OverflowError()
            body.extend(chunk)
    return bytes(body)


def signed_pass(service, site, ip):
    payload = base64.urlsafe_b64encode(json.dumps({"id": site["id"], "host": site["hostname"], "v": site["policy_version"],
                                                "ip": ip, "exp": time.time() + site["session_minutes"] * 60}).encode()).decode().rstrip("=")
    signature = hmac.new(service.store.secret("signing_key").encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def valid_pass(service, site, ip, token):
    try:
        payload, signature = token.split(".")
        expected = hmac.new(service.store.secret("signing_key").encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data["id"] == site["id"] and data["host"] == site["hostname"] and data["v"] == site["policy_version"] and data["ip"] == ip and data["exp"] > time.time()
    except (ValueError, KeyError, TypeError):
        return False


def visitor(request):
    peer = request.client.host if request.client else ""
    # Uvicorn proxy_headers=False. Only the loopback connector can supply edge headers.
    if peer in ("127.0.0.1", "::1"):
        ip = request.headers.get("cf-connecting-ip", peer)
        country = request.headers.get("cf-ipcountry", "").upper()
    else:
        ip, country = peer, ""
    try:
        return str(ipaddress.ip_address(ip)), country
    except ValueError:
        return peer, ""


def find_site(service, request):
    host = request.headers.get("host", "").split(":")[0].lower().rstrip(".")
    return next((s for s in service.sites() if s["enabled"] and s["hostname"] == host), None)


def public_scheme(request):
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1"):
        try:
            scheme = json.loads(request.headers.get("cf-visitor", "{}" )).get("scheme")
        except (ValueError, AttributeError):
            scheme = None
        if scheme in ("http", "https"):
            return scheme
        scheme = request.headers.get("x-forwarded-proto", "")
        if scheme in ("http", "https"):
            return scheme
    return request.url.scheme


def denied_policy(site, ip, country):
    if site["allowed_countries"] and country not in site["allowed_countries"]:
        return True
    if site["allowed_ips"]:
        try:
            address = ipaddress.ip_address(ip)
            return not any(address in ipaddress.ip_network(n) for n in site["allowed_ips"])
        except ValueError:
            return True
    return False


def filtered_headers(headers):
    items = list(headers.multi_items() if hasattr(headers, "multi_items") else headers.items())
    connection = {h.strip().lower() for k, v in items if k.lower() == "connection"
                  for h in v.split(",") if h.strip()}
    excluded = HOP | connection
    return [(k, v) for k, v in items if k.lower() not in excluded]


def gate_page(service, site):
    key = service.settings()["turnstile_sitekey"]
    if site["human_check"] and (not key or not service.store.secret("turnstile_secret")):
        return HTMLResponse("人类验证尚未配置，访问暂时关闭。", 503)
    title = html.escape(site["name"])
    widget = f'<div class="cf-turnstile" data-sitekey="{html.escape(key, quote=True)}" data-action="lanbridge" data-callback="onHumanVerified" data-expired-callback="onHumanExpired"></div>' if site["human_check"] else ""
    password = '<label>访问口令<input id="passcode" type="password" autocomplete="current-password" required maxlength="256"></label>' if site["passcode_required"] else ""
    automatic = site["human_check"] and not site["passcode_required"]
    button = '<button id="continue" hidden>重试验证</button>' if automatic else '<button id="continue">验证并继续</button>'
    description = "完成人类验证后将自动进入网站。" if automatic else "完成验证并输入访问口令后，继续访问该网站。"
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>访问验证</title>
<style>body{background:#f3f5f7;font:15px system-ui;color:#233347;margin:0;display:grid;place-items:center;min-height:100vh}.card{background:white;border:1px solid #dce2e9;border-radius:18px;padding:36px;width:min(340px,80vw)}h1{font-size:25px}p{color:#63758b;line-height:1.7}input,button{box-sizing:border-box;width:100%;padding:12px;border-radius:8px;border:1px solid #ccd5df;margin:10px 0}button{background:#1c6657;color:white;cursor:pointer}.cf-turnstile{margin:16px 0}#error{color:#ad3b3b}</style>
<div class="card"><small>LANBRIDGE · 安全访问</small><h1>''' + title + '''</h1><p>''' + description + '''</p><form id="verify">''' + password + widget + button + '''</form><p id="verify-status" role="status"></p><p id="error" role="alert"></p></div>
<script>
const form=document.querySelector('#verify'),button=document.querySelector('#continue'),message=document.querySelector('#verify-status'),error=document.querySelector('#error');
const automatic=!document.querySelector('#passcode');
let humanToken='',submitting=false;
async function submitVerification(){
  if(submitting||!form.reportValidity())return;
  submitting=true;button.disabled=true;error.textContent='';message.textContent='正在确认验证结果…';
  try{
    const r=await fetch('/.lanbridge/verify',{method:'POST',signal:AbortSignal.timeout(30000),headers:{'Content-Type':'application/json'},body:JSON.stringify({token:humanToken||document.querySelector('[name="cf-turnstile-response"]')?.value||'',passcode:document.querySelector('#passcode')?.value||''})});
    if(!r.ok){const d=await r.json();throw Error(d.detail||'验证失败')}
    message.textContent='验证通过，正在进入网站…';location.reload();
  }catch(err){
    error.textContent=err.name==='TimeoutError'?'验证请求超时，请重试。':err.message;message.textContent='';humanToken='';
    button.hidden=false;
    if(window.turnstile)window.turnstile.reset();
  }finally{submitting=false;button.disabled=false}
}
window.onHumanVerified=token=>{humanToken=token;if(automatic)submitVerification();else message.textContent='人类验证已通过，请输入口令并继续。'};
window.onHumanExpired=()=>{humanToken='';message.textContent='验证已过期，请重新验证。'};
form.onsubmit=e=>{e.preventDefault();submitVerification()};
</script>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script></html>'''
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff"})


class RemoteAdminResponse(Response):
    """Dispatch only an explicitly configured LanBridge target, without a proxy loop."""
    def __init__(self, app, ip):
        super().__init__(content=b"")
        self.remote_app, self.ip = app, ip

    async def __call__(self, scope, receive, send):
        remote_scope = dict(scope, scheme="https", client=(self.ip, 0), state=dict(scope.get("state", {})))
        await self.remote_app(remote_scope, receive, send)


def create_gateway(service):
    from .admin import create_admin, PUBLIC_CLIENT_PATHS
    remote_admin = create_admin(service, remote=True)
    pools = UpstreamPools()
    logger, log_handler = transfer_logger(service.store.root)
    @asynccontextmanager
    async def lifespan(app):
        try:
            async with pools.lifespan(app):
                yield
        finally:
            log_handler.close()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    limiter = Limiter()
    budget = BufferBudget()

    async def policy(request):
        site = find_site(service, request)
        if not site:
            return Response("Not found", 404)
        if site.get("paused"):
            return Response("网站转发已暂停", 503, headers={"Cache-Control": "no-store"})
        if public_scheme(request) == "http":
            return RedirectResponse(str(request.url.replace(scheme="https", netloc=site["hostname"])), status_code=308)
        ip, country = visitor(request)
        request.state.site, request.state.ip = site, ip
        if denied_policy(site, ip, country):
            return Response("Access denied", 403)
        if not limiter.allow((site["id"], ip, "request"), site["requests_per_minute"]):
            return Response("请求过于频繁，请稍后重试", 429, headers={"Retry-After": "60"})
        if request.url.path == PREFIX + "/verify":
            return None
        if request.url.path.startswith(PREFIX):
            return Response("Not found", 404)
        protected = site["human_check"] or site["passcode_required"]
        temporary_access = False
        if site.get("target") == "lanbridge":
            if request.url.path in PUBLIC_CLIENT_PATHS and not service.store.get("public_client_enabled", True):
                return disabled_client_response(request.url.path)
            from .temporary_access import allowed
            authorization = request.headers.get("authorization", "")
            if authorization.startswith("Bearer "):
                grant = service.store.temporary_token(authorization[7:])
                temporary_access = bool(grant and allowed(request.method, request.url.path, grant["permissions"]))
            # A token-authenticated browser still uses the normal same-origin/CSRF guard.
            session = service.store.session(request.cookies.get("lb_admin", ""))
            if session and session.get("scope") == "sites":
                temporary_access = allowed(request.method, request.url.path, session["permissions"]) or request.url.path in {"/admin", "/admin/", "/app.js", "/style.css", "/local-login.js", "/favicon.svg"}
        if protected and not temporary_access and not valid_pass(service, site, ip, request.cookies.get(PASS_COOKIE, "")):
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return gate_page(service, site)
            return JSONResponse({"detail": "需要先在浏览器中完成访问验证", "verification_required": True}, 401, headers={"Cache-Control": "no-store"})
        if "http" not in site.get("protocols", ["http", "websocket"]):
            return Response("此网站未启用 HTTP 转发", 403, headers={"Cache-Control": "no-store"})
        return None

    @app.post(PREFIX + "/verify")
    async def verify(request: Request):
        site, ip = request.state.site, request.state.ip
        if request.headers.get("origin") != "https://" + site["hostname"]:
            return JSONResponse({"detail": "来源校验失败"}, 403)
        if not limiter.allow((site["id"], ip, "verify"), 5):
            return JSONResponse({"detail": "验证过于频繁，请稍后重试"}, 429, headers={"Retry-After": "60"})
        try:
            raw = await bounded_body(request)
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
        except OverflowError:
            return Response(status_code=413)
        except TimeoutError:
            return Response(status_code=408)
        except (ValueError, RecursionError):
            return JSONResponse({"detail": "验证请求无效"}, 400)
        if site["passcode_required"] and not await anyio.to_thread.run_sync(password_check, str(body.get("passcode", "")), service.store.secret("passcode_" + site["id"])):
            return JSONResponse({"detail": "口令不正确"}, 403)
        if site["human_check"]:
            token = body.get("token", "")
            secret = service.store.secret("turnstile_secret")
            if not secret or not isinstance(token, str) or not 1 <= len(token) <= 2048:
                return JSONResponse({"detail": "请完成人类验证"}, 403)
            try:
                async with httpx.AsyncClient(timeout=12, trust_env=False) as client:
                    result = await client.post("https://challenges.cloudflare.com/turnstile/v0/siteverify", data={"secret": secret, "response": token, "remoteip": ip})
                data = result.json()
                if not isinstance(data, dict):
                    raise ValueError("invalid_verification_response")
                if result.status_code != 200 or data.get("success") is not True or data.get("hostname") != site["hostname"] or data.get("action") != "lanbridge":
                    return JSONResponse({"detail": "人类验证未通过，请重试"}, 403)
            except (httpx.HTTPError, ValueError, RecursionError):
                return JSONResponse({"detail": "人类验证服务暂不可用"}, 503)
        response = JSONResponse({"verified": True}, headers={"Cache-Control": "no-store"})
        response.set_cookie(PASS_COOKIE, signed_pass(service, site, ip), max_age=site["session_minutes"] * 60, httponly=True, secure=True, samesite="lax", path="/")
        return response

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def proxy(request: Request, path: str):
        site = request.state.site
        if site.get("target") == "lanbridge":
            return RemoteAdminResponse(remote_admin, request.state.ip)
        try:
            length = bounded_length(request.headers.get("content-length", "0"), 32 * 1024 * 1024)
        except OverflowError:
            return Response("请求内容过大", 413)
        try:
            base, host, sni = await anyio.to_thread.run_sync(pinned_origin, site, service.settings())
        except ValueError:
            return Response("源站配置不可用", 502)
        raw_path = request.scope.get("raw_path", request.url.path.encode()).decode("ascii")
        query = request.scope.get("query_string", b"").decode("ascii")
        target = base + raw_path + ("?" + query if query else "")
        headers = [(k, v) for k, v in filtered_headers(request.headers) if k.lower() not in {"host", "content-length", "cookie", "forwarded"} and not k.lower().startswith(("x-forwarded-", "cf-"))]
        cookies = "; ".join(part.strip() for part in request.headers.get("cookie", "").split(";") if part.strip() and part.strip().split("=", 1)[0] not in {PASS_COOKIE, "lb_admin"})
        headers += [("host", host), ("x-forwarded-host", site["hostname"]), ("x-forwarded-proto", "https"), ("x-forwarded-for", request.state.ip)]
        if cookies:
            headers.append(("cookie", cookies))
        # Keep known body lengths; do not manufacture chunked uploads for bodyless requests.
        if "content-length" in request.headers:
            headers.append(("content-length", str(length)))
        try:
            connection_auth = request.headers.get("authorization", "").strip().lower().startswith(("ntlm ", "negotiate "))
            transport = await pools.borrow((site["id"], base, host, sni), isolated=connection_auth)
        except httpx.PoolTimeout:
            return Response("源站连接繁忙，请稍后重试", 503, headers={"Retry-After": "1", "Cache-Control": "no-store"})
        client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(120, connect=10, pool=1), follow_redirects=False, trust_env=False)
        async def limited_body():
            size = 0
            with anyio.fail_after(120):
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 32 * 1024 * 1024:
                        raise ValueError("body_too_large")
                    yield chunk
        try:
            has_body = length > 0 or bool(request.headers.get("transfer-encoding"))
            started = time.monotonic()
            upstream = await client.send(client.build_request(request.method, target, headers=headers, content=limited_body() if has_body else None, extensions={"sni_hostname": sni}), stream=True)
            request.state.transfer["upstream_headers_ms"] = round((time.monotonic() - started) * 1000, 2)
        except ValueError:
            await client.aclose()
            return Response("请求内容过大", 413)
        except httpx.PoolTimeout:
            await client.aclose()
            return Response("源站连接繁忙，请稍后重试", 503, headers={"Retry-After": "1", "Cache-Control": "no-store"})
        except httpx.HTTPError:
            await client.aclose()
            return Response("局域网源站暂不可用", 502)
        except TimeoutError:
            await client.aclose()
            return Response("上传超时", 408)
        except BaseException:
            with anyio.CancelScope(shield=True):
                await client.aclose()
            raise
        response_headers = []
        for k, v in filtered_headers(upstream.headers):
            if k.lower() == "location" and (v.startswith(site["origin"] + "/") or v == site["origin"]):
                v = "https://" + site["hostname"] + v[len(site["origin"]):]
            if k.lower() == "set-cookie":
                if v.split("=", 1)[0].strip() in {PASS_COOKIE, "lb_admin"}:
                    continue
                # Make explicit upstream-domain cookies host-only on the public domain.
                parts = v.split(";")
                v = ";".join([parts[0], *(part for part in parts[1:] if part.partition("=")[0].strip().lower() != "domain")])
            response_headers.append((k.encode("latin1"), v.encode("latin1")))
        async def close():
            try:
                await upstream.aclose()
            finally:
                await client.aclose()
        async def tracked_body():
            async for chunk in upstream.aiter_raw():
                request.state.transfer["read"] += len(chunk)
                yield chunk
        declared = upstream.headers.get("content-length")
        no_body = request.method == "HEAD" or upstream.status_code in (204, 304) or upstream.status_code < 200
        request.state.transfer["upstream_length"] = declared
        if declared is not None and not no_body:
            if not declared.isdigit():
                request.state.transfer["upstream_error"] = "InvalidContentLength"
                await close()
                return Response("源站响应长度无效", 502, headers={"Cache-Control": "no-store"})
            if not budget.reserve(int(declared)):
                await close()
                return Response("响应缓冲容量不足，请稍后重试或缩小下载", 503, headers={"Retry-After": "5", "Cache-Control": "no-store"})
            reserved = int(declared)
            released = False
            def release_buffer():
                nonlocal released
                if not released:
                    released = True
                    budget.used -= reserved
            # Verify the raw encoded representation before forwarding a successful status.
            # Memory is bounded to 1 MiB; larger downloads spill to the protected data directory.
            try:
                spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=service.store.root)
            except BaseException:
                release_buffer()
                await close()
                raise
            try:
                if not declared.isdigit():
                    raise ValueError("invalid_response_length")
                disconnected, stream_error = False, None
                with anyio.fail_after(300):
                    async with anyio.create_task_group() as tasks:
                        async def watch_disconnect():
                            nonlocal disconnected
                            while True:
                                if (await request.receive())["type"] == "http.disconnect":
                                    disconnected = True
                                    tasks.cancel_scope.cancel()
                                    return
                        tasks.start_soon(watch_disconnect)
                        try:
                            async for chunk in tracked_body():
                                if request.state.transfer["read"] > reserved:
                                    raise ValueError("invalid_response_length")
                                await anyio.to_thread.run_sync(spool.write, chunk)
                        except (httpx.HTTPError, ValueError, OSError) as exc:
                            stream_error = exc
                        finally:
                            tasks.cancel_scope.cancel()
                if disconnected:
                    request.state.transfer["upstream_error"] = "ClientDisconnect"
                    spool.close()
                    release_buffer()
                    return Response(status_code=499)
                if stream_error:
                    raise stream_error
                if request.state.transfer["read"] != int(declared):
                    raise ValueError("incomplete_response")
                await anyio.to_thread.run_sync(spool.seek, 0)
            except (httpx.HTTPError, ValueError, TimeoutError, OSError) as exc:
                request.state.transfer["upstream_error"] = type(exc).__name__
                spool.close()
                release_buffer()
                return Response("源站响应未完整接收，请重试", 502, headers={"Cache-Control": "no-store"})
            except BaseException:
                spool.close()
                release_buffer()
                raise
            finally:
                with anyio.CancelScope(shield=True):
                    await close()
            async def buffered_body():
                while chunk := await anyio.to_thread.run_sync(spool.read, 65536):
                    yield chunk
            async def close_spool():
                spool.close()
                release_buffer()
            response = OwnedStreamingResponse(buffered_body(), close=close_spool, status_code=upstream.status_code)
        else:
            response = OwnedStreamingResponse(tracked_body(), close=close, status_code=upstream.status_code)
        response.raw_headers = response_headers
        return response

    @app.websocket("/{path:path}")
    async def websocket_proxy(ws: WebSocket, path: str):
        site = find_site(service, ws)
        ip, country = visitor(ws)
        if not site or site.get("target") == "lanbridge" or site.get("paused") or "websocket" not in site.get("protocols", ["http", "websocket"]) or denied_policy(site, ip, country) or ws.url.path.startswith(PREFIX):
            await ws.close(code=1008)
            return
        if ws.headers.get("origin") and ws.headers["origin"] != "https://" + site["hostname"]:
            await ws.close(code=1008)
            return
        if not limiter.allow((site["id"], ip, "request"), site["requests_per_minute"]):
            await ws.close(code=1008)
            return
        if (site["human_check"] or site["passcode_required"]) and not valid_pass(service, site, ip, ws.cookies.get(PASS_COOKIE, "")):
            await ws.close(code=1008)
            return
        sock = None
        try:
            base, host, sni = await anyio.to_thread.run_sync(pinned_origin, site, service.settings())
            u = urlsplit(base)
            sock = await anyio.to_thread.run_sync(socket.create_connection, (u.hostname, u.port), 10)
            sock.setblocking(False)
            raw_path = ws.scope.get("raw_path", ws.url.path.encode()).decode("ascii")
            query = ws.scope.get("query_string", b"").decode("ascii")
            uri = ("wss://" if u.scheme == "https" else "ws://") + host + raw_path + ("?" + query if query else "")
            cookies = "; ".join(p.strip() for p in ws.headers.get("cookie", "").split(";") if p.strip() and p.strip().split("=", 1)[0] not in {PASS_COOKIE, "lb_admin"})
            headers = {k: v for k, v in filtered_headers(ws.headers) if k.lower() not in {"host", "cookie", "origin", "forwarded"} and not k.lower().startswith(("sec-websocket-", "x-forwarded-", "cf-"))}
            headers.update({"X-Forwarded-Host": site["hostname"], "X-Forwarded-Proto": "https", "X-Forwarded-For": ip})
            if cookies:
                headers["Cookie"] = cookies
            protocols = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
            async with websockets.connect(uri, sock=sock, additional_headers=headers, origin=ws.headers.get("origin"),
                                          subprotocols=protocols or None, max_size=8 * 1024 * 1024, max_queue=2,
                                          **({"server_hostname": sni} if u.scheme == "https" else {})) as remote:
                await ws.accept(subprotocol=remote.subprotocol)
                message_budgets = [TokenBudget(200, 400), TokenBudget(200, 400)]
                byte_budgets = [TokenBudget(2 * 1024 * 1024, 8 * 1024 * 1024), TokenBudget(2 * 1024 * 1024, 8 * 1024 * 1024)]
                def allowed_message(value, direction):
                    size = len(value if isinstance(value, bytes) else value.encode())
                    return size <= 8 * 1024 * 1024 and message_budgets[direction].allow() and byte_budgets[direction].allow(size)
                async def to_remote():
                    while True:
                        message = await ws.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        value = message.get("bytes") if message.get("bytes") is not None else message.get("text", "")
                        if not allowed_message(value, 0):
                            logger.warning("websocket_limit %s", json.dumps({"hostname": site["hostname"], "direction": "visitor"}))
                            await ws.close(code=1008)
                            return
                        await remote.send(value)
                async def to_browser():
                    async for message in remote:
                        if not allowed_message(message, 1):
                            logger.warning("websocket_limit %s", json.dumps({"hostname": site["hostname"], "direction": "upstream"}))
                            await ws.close(code=1008)
                            return
                        if isinstance(message, bytes):
                            await ws.send_bytes(message)
                        else:
                            await ws.send_text(message)
                async def watch_policy():
                    while True:
                        await asyncio.sleep(2)
                        current = find_site(service, ws)
                        if not current or current.get("paused") or current["policy_version"] != site["policy_version"]:
                            return
                        if (current["human_check"] or current["passcode_required"]) and not valid_pass(service, current, ip, ws.cookies.get(PASS_COOKIE, "")):
                            return
                tasks = [asyncio.create_task(to_remote()), asyncio.create_task(to_browser()), asyncio.create_task(watch_policy())]
                try:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                await ws.close(code=1000)
        except Exception:
            try:
                await ws.close(code=1011)
            except RuntimeError:
                pass
        finally:
            if sock:
                sock.close()
    app.add_middleware(PolicyMiddleware, check=policy)
    app.add_middleware(ResourceLimits)
    app.add_middleware(TransferLog, logger=logger)
    return app
