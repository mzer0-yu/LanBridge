import asyncio
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
    finally:
        done.set()
        thread.join(timeout=5)
