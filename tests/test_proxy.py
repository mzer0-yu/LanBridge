import asyncio
import httpx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import pytest
from fastapi.testclient import TestClient
from lanbridge.gateway import create_gateway, PASS_COOKIE, signed_pass
from test_security import service


@pytest.fixture
def origin():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == "/bytes":
                body = b"\x00\x01\xffbinary"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
            elif self.path == "/latin-header":
                body = b"<html>header compatibility</html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; note=\"caf\xe9\"")
            elif self.path == "/utf8-header":
                body = b"<html>header compatibility</html>"
                self.send_response(200)
                self.send_header("Content-Type", (b'text/html; note="\xe4\xb8\xad\xe6\x96\x87"').decode("latin1"))
            elif self.path == "/cookie-attributes":
                body = b"cookie-check"
                self.send_response(200)
                self.send_header("Set-Cookie", "private_session=abc; dOmAiN = .example.com; Path=/; HttpOnly")
                self.send_header("Set-Cookie", "second=keep; Domain=.example.com; Max-Age=120; SameSite=Lax")
                self.send_header("Set-Cookie", "domain=legitimate; Path=/; Secure")
            elif self.path == "/redirect":
                body = b""
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/target")
            else:
                body = json.dumps({"path": self.path, "cookie": self.headers.get("Cookie"), "host": self.headers.get("Host"), "forwarded": self.headers.get("X-Forwarded-Host")}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "source_session=abc; Domain=127.0.0.1; Path=/; HttpOnly")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_http_proxy_paths_binary_cookies_redirects(service, origin):
    site = service.save_site({"name": "origin", "hostname": "app.example.com", "origin": f"http://127.0.0.1:{origin}", "human_check": False, "passcode_required": True, "passcode": "visitor long password"})
    client = TestClient(create_gateway(service), base_url="https://app.example.com")
    client.cookies.set(PASS_COOKIE, signed_pass(service, site, "testclient"))
    client.cookies.set("lb_admin", "must-not-leak")
    client.cookies.set("app_cookie", "preserved")
    result = client.get("/deep/path?a=one%20two")
    assert result.status_code == 200
    assert result.json()["path"] == "/deep/path?a=one%20two"
    assert result.json()["cookie"] == "app_cookie=preserved"
    assert result.json()["forwarded"] == "app.example.com"
    assert "Domain=" not in result.headers["set-cookie"]
    result = client.get("/bytes")
    assert result.content == b"\x00\x01\xffbinary"
    redirect = client.get("/redirect", follow_redirects=False)
    assert redirect.headers["location"] == "https://app.example.com/target"
    service.save_site(site | {"enabled": False})
    assert client.get("/bytes").status_code == 404


def test_saved_origin_change_takes_effect_without_restarting_gateway(service, origin):
    class Replacement(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *args): pass
        def do_GET(self):
            assert self.headers.get("Transfer-Encoding") is None
            body = b"replacement-origin"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            assert self.headers.get("Transfer-Encoding") is None
            body = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Replacement)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        site = service.save_site({"name": "origin", "hostname": "app.example.com", "origin": f"http://127.0.0.1:{origin}", "human_check": False})
        with TestClient(create_gateway(service), base_url="https://app.example.com") as client:
            assert client.get("/before").json()["host"] == f"127.0.0.1:{origin}"
            service.save_site(site | {"origin": f"http://127.0.0.1:{server.server_port}"})
            assert client.get("/after").content == b"replacement-origin"
            assert client.post("/legacy-api", content=b"motor-command").content == b"motor-command"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_websocket_proxy_requires_grant_and_relays(service):
    from websockets.asyncio.server import serve
    ready = threading.Event()
    done = threading.Event()
    box = {}
    async def echo(ws):
        async for message in ws:
            await ws.send(message)
    async def run():
        async with serve(echo, "127.0.0.1", 0) as server:
            box["port"] = server.sockets[0].getsockname()[1]
            ready.set()
            while not done.is_set():
                await asyncio.sleep(.05)
    thread = threading.Thread(target=lambda: asyncio.run(run()), daemon=True)
    thread.start()
    assert ready.wait(5)
    try:
        site = service.save_site({"name": "socket", "hostname": "app.example.com", "origin": f'http://127.0.0.1:{box["port"]}', "human_check": False, "passcode_required": True, "passcode": "visitor long password"})
        client = TestClient(create_gateway(service), base_url="https://app.example.com")
        with pytest.raises(Exception):
            with client.websocket_connect("wss://app.example.com/ws"):
                pass
        client.cookies.set(PASS_COOKIE, signed_pass(service, site, "testclient"))
        with client.websocket_connect("wss://app.example.com/ws") as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "hello"
            ws.send_bytes(b"\x00\xff")
            assert ws.receive_bytes() == b"\x00\xff"
        site = service.save_site(site | {"protocols": ["http"]})
        with pytest.raises(Exception):
            with client.websocket_connect("wss://app.example.com/ws"):
                pass
        site = service.save_site(site | {"protocols": ["websocket"]})
        client.cookies.set(PASS_COOKIE, signed_pass(service, site, "testclient"))
        with client.websocket_connect("wss://app.example.com/ws") as ws:
            ws.send_text("socket-only")
            assert ws.receive_text() == "socket-only"
    finally:
        done.set()
        thread.join(timeout=5)


def test_upstream_cookie_domain_whitespace_remains_host_only(service, origin):
    service.save_site({"name":"cookies", "hostname":"app.example.com",
                       "origin":f"http://127.0.0.1:{origin}", "human_check":False})
    with TestClient(create_gateway(service), base_url="https://app.example.com") as client:
        response = client.get("/cookie-attributes")
        assert response.status_code == 200
        cookies = response.headers.get_list("set-cookie")
        assert len(cookies) == 3
        assert cookies[2].startswith("domain=legitimate;")
        assert all("domain" not in [part.partition("=")[0].strip().lower() for part in value.split(";")[1:]] for value in cookies)
        assert "HttpOnly" in cookies[0] and "Max-Age=120" in cookies[1] and "SameSite=Lax" in cookies[1]
        assert all(cookie.domain == "app.example.com" and not cookie.domain_specified for cookie in client.cookies.jar)


def test_duplicate_connection_headers_filter_all_nominated_hop_headers():
    from starlette.datastructures import Headers
    from lanbridge.gateway import filtered_headers
    headers = Headers(raw=[(b"connection",b"keep-alive, X-First"),
                           (b"connection",b"X-Second"), (b"x-first",b"private-first"),
                           (b"x-second",b"private-second"), (b"x-end-to-end",b"keep"),
                           (b"set-cookie",b"a=1"), (b"set-cookie",b"b=2")])
    remaining = filtered_headers(headers)
    assert not any(key.lower() in {"connection","x-first","x-second"} for key, _ in remaining)
    assert ("x-end-to-end", "keep") in remaining
    assert [value for key,value in remaining if key.lower()=="set-cookie"] == ["a=1","b=2"]


@pytest.mark.parametrize("path,value", [("/latin-header", b'text/html; note="caf\xe9"'),
                                        ("/utf8-header", b'text/html; note="\xe4\xb8\xad\xe6\x96\x87"')])
def test_response_header_bytes_do_not_break_transfer(service, origin, path, value):
    site = service.save_site({"name": "legacy origin", "hostname": "app.example.com",
                       "origin": f"http://127.0.0.1:{origin}", "human_check": False})
    async def check():
        app = create_gateway(service)
        # TestClient converts header bytes to strings, which breaks non-ASCII fields.
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="https://app.example.com") as client:
                response = await client.get(path)
                assert response.status_code == 200
                assert response.content == b"<html>header compatibility</html>"
                assert (b"content-type", value) in response.headers.raw
                assert service.visitor_risk.snapshot([site["id"]])["sites"][site["id"]]["page_views"] == 1
    asyncio.run(check())
