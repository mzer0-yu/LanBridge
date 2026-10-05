import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from lanbridge.gateway import create_gateway, TokenBudget
from lanbridge.upstream import UpstreamPools
from lanbridge.gateway_log import transfer_logger
from test_security import service


def test_keepalive_reuses_socket_without_cookie_leak_and_drains_on_route_change(service):
    seen = []
    writes = []
    class Origin(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args): pass
        def do_GET(self):
            seen.append(self.client_address[1])
            body = json.dumps({"cookie": self.headers.get("Cookie"), "port": self.client_address[1]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", "private_session=visitor-A; Path=/")
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            writes.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"incomplete")
            self.wfile.flush()
            self.close_connection = True
    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Origin) for _ in range(2)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads: thread.start()
    try:
        site = service.save_site({"name": "pool", "hostname": "app.example.com", "origin": f"http://127.0.0.1:{servers[0].server_port}", "human_check": False})
        with TestClient(create_gateway(service), base_url="https://app.example.com") as client:
            a = client.get("/a", headers={"Cookie": "visitor=A"}).json()
            # Supplying an empty header also prevents the test browser's own cookie jar.
            b = client.get("/b", headers={"Cookie": ""}).json()
            assert a["port"] == b["port"]
            assert a["cookie"] == "visitor=A" and b["cookie"] is None
            service.save_site(site | {"origin": f"http://127.0.0.1:{servers[1].server_port}"})
            c = client.get("/c", headers={"Cookie": "visitor=C"}).json()
            assert c["port"] != a["port"] and c["cookie"] == "visitor=C"
            assert client.post("/command", content=b"move-once").status_code == 502
            assert writes == [b"move-once"]
    finally:
        for server in servers: server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=3)


def test_pool_capacity_is_atomic_and_recovered_after_release():
    async def run():
        pool = UpstreamPools(maximum=1, connections=1)
        key = ("site", "https://127.0.0.1:443", "one.example", "one.example")
        async with pool.lifespan(None):
            results = await asyncio.gather(pool.borrow(key), pool.borrow(key), return_exceptions=True)
            leases = [r for r in results if not isinstance(r, Exception)]
            assert len(leases) == 1
            assert sum(isinstance(r, httpx.PoolTimeout) for r in results) == 1
            with pytest.raises(httpx.PoolTimeout): await pool.borrow(("other", *key[1:]))
            await leases[0].aclose()
            await leases[0].aclose()
            replacement = await pool.borrow(("other", *key[1:]))
            assert len(pool.entries) == 1
            await replacement.aclose()
        assert not pool.entries
    asyncio.run(run())


def test_pool_tls_identity_and_dns_change_are_isolated_until_old_request_finishes():
    async def run():
        pool = UpstreamPools()
        first = ("site", "https://127.0.0.1:443", "one.example", "one.example")
        changed = ("site", "https://127.0.0.2:443", "two.example", "two.example")
        async with pool.lifespan(None):
            a = await pool.borrow(first)
            b = await pool.borrow(changed)
            assert a.entry["transport"] is not b.entry["transport"]
            assert a.entry["retired"] and a.entry["active"] == 1
            await a.aclose()
            assert first not in pool.entries
            await b.aclose()
    asyncio.run(run())


def test_idle_pool_expires_and_connection_bound_auth_uses_isolated_transport():
    async def run():
        pool = UpstreamPools(idle_seconds=.02)
        key = ("site", "https://127.0.0.1:443", "one.example", "one.example")
        async with pool.lifespan(None):
            lease = await pool.borrow(key)
            separate = await pool.borrow(key, isolated=True)
            assert separate is not lease.entry["transport"]
            await separate.aclose()
            await lease.aclose()
            for _ in range(20):
                if not pool.entries: break
                await asyncio.sleep(.05)
            assert not pool.entries
    asyncio.run(run())


def test_websocket_message_and_byte_budget_refill_and_burst_limits():
    budget = TokenBudget(rate=2, burst=8)
    assert budget.allow(8)
    assert not budget.allow(1)
    budget.updated -= 1
    assert budget.allow(2)
    assert not budget.allow(9)


def test_websocket_closes_when_message_budget_is_exceeded(service, monkeypatch):
    import lanbridge.gateway as gateway
    from websockets.asyncio.server import serve
    ready, stop = threading.Event(), threading.Event()
    ports = []
    async def echo(ws):
        async for message in ws: await ws.send(message)
    async def run():
        async with serve(echo, "127.0.0.1", 0) as server:
            ports.append(server.sockets[0].getsockname()[1]); ready.set()
            while not stop.is_set(): await asyncio.sleep(.02)
    thread = threading.Thread(target=lambda: asyncio.run(run()), daemon=True)
    thread.start()
    assert ready.wait(5)
    original = gateway.TokenBudget
    monkeypatch.setattr(gateway, "TokenBudget", lambda rate, burst: original(0, 2) if rate == 200 else original(rate, burst))
    try:
        service.save_site({"name": "socket", "hostname": "app.example.com", "origin": f"http://127.0.0.1:{ports[0]}", "human_check": False})
        with TestClient(create_gateway(service), base_url="https://app.example.com") as client:
            with client.websocket_connect("wss://app.example.com/ws", headers={"Origin": "https://app.example.com"}) as ws:
                for _ in range(2):
                    ws.send_text("正常消息"); assert ws.receive_text() == "正常消息"
                ws.send_text("超过限额")
                with pytest.raises(WebSocketDisconnect) as error: ws.receive_text()
                assert error.value.code == 1008
    finally:
        stop.set();thread.join(timeout=5)


def test_rotated_transfer_logs_remain_bounded_and_do_not_propagate(tmp_path):
    logger, handler = transfer_logger(tmp_path)
    handler.maxBytes = 1000
    for i in range(100): logger.warning("event %d %s", i, "x" * 250)
    handler.close()
    files = list(tmp_path.glob("gateway.log*"))
    assert len(files) == 4 and sum(f.stat().st_size for f in files) < 4000
    assert not logger.propagate
