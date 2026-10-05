import asyncio

import pytest
from starlette.requests import Request
from fastapi.testclient import TestClient

from lanbridge.gateway import bounded_body, BufferBudget, Limiter, ResourceLimits, create_gateway
from test_security import service
from test_streaming import streaming_gateway


def test_verify_body_stops_reading_at_limit_and_rejects_declared_size():
    async def run():
        calls = 0
        async def receive():
            nonlocal calls
            calls += 1
            return {"type": "http.request", "body": b"x" * 3000, "more_body": True}
        scope = {"type": "http", "headers": []}
        with pytest.raises(OverflowError):
            await bounded_body(Request(scope, receive))
        assert calls == 2
        scope["headers"] = [(b"content-length", b"999999999")]
        with pytest.raises(OverflowError):
            await bounded_body(Request(scope, receive))
        assert calls == 2
    asyncio.run(run())


def test_verify_body_absolute_deadline():
    async def run():
        async def receive():
            await asyncio.sleep(1)
        with pytest.raises(TimeoutError):
            await bounded_body(Request({"type": "http", "headers": []}, receive), seconds=.02)
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["http", "websocket", "verify"])
def test_concurrent_work_rejects_without_queue_and_recovers_after_cancel(kind):
    async def run():
        entered = asyncio.Event()
        async def app(scope, receive, send):
            entered.set()
            await asyncio.Event().wait()
        guard = ResourceLimits(app, http=1, websocket=1, verify=1)
        scope = {"type": "http" if kind == "verify" else kind, "path": "/.lanbridge/verify" if kind == "verify" else "/",
                 "headers": [], "client": ("127.0.0.1", 1), "method": "GET"}
        messages = []
        async def send(message): messages.append(message)
        async def receive(): return {"type": "http.disconnect"}
        task = asyncio.create_task(guard(scope, receive, send))
        await entered.wait()
        await guard(scope, receive, send)
        assert messages[0].get("status", messages[0].get("code")) == (1013 if kind == "websocket" else 503)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert not guard.peers and not any(guard.active.values())
    asyncio.run(run())


def test_rate_limiter_storage_does_not_grow_with_request_count():
    limiter = Limiter()
    for _ in range(10000): assert limiter.allow("one", 10000)
    assert not limiter.allow("one", 10000)
    assert len(limiter.buckets["one"]) == 2


def test_buffer_budget_exhaustion_preserves_existing_reservations():
    budget = BufferBudget(total=10, single=8)
    assert budget.reserve(6)
    assert not budget.reserve(5)
    assert not budget.reserve(9)
    assert budget.used == 6


def test_identity_rotation_does_not_evict_active_rate_buckets():
    limiter = Limiter()
    for i in range(10000): assert limiter.allow(i, 1)
    assert not limiter.allow("new identity", 1)
    assert not limiter.allow(0, 1)


def test_global_rate_budget_precedes_site_and_body_processing():
    async def run():
        calls = 0
        async def app(scope, receive, send):
            nonlocal calls
            calls += 1
        guard = ResourceLimits(app, rate=0, burst=2)
        statuses = []
        async def send(message):
            if message["type"] == "http.response.start": statuses.append(message["status"])
        async def receive(): raise AssertionError("body must not be read")
        scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}
        for _ in range(5): await guard(scope, receive, send)
        assert calls == 2 and statuses == [429] * 3
    asyncio.run(run())


def test_buffer_rejection_then_full_download_recovers(streaming_gateway, monkeypatch):
    import httpx
    url, headers, payload, _, _ = streaming_gateway
    reserve = BufferBudget.reserve
    monkeypatch.setattr(BufferBudget, "reserve", lambda self, size: False)
    with httpx.Client(headers=headers, trust_env=False, timeout=30) as client:
        assert client.get(url + "/json").status_code == 503
        monkeypatch.setattr(BufferBudget, "reserve", reserve)
        response = client.get(url + "/json")
        assert response.status_code == 200 and response.content == payload


def test_parallel_large_downloads_preserve_complete_bytes(streaming_gateway):
    import httpx
    url, headers, payload, _, _ = streaming_gateway
    async def run():
        async with httpx.AsyncClient(headers=headers, trust_env=False, timeout=30) as client:
            responses = await asyncio.gather(*(client.get(url + "/json") for _ in range(8)))
        assert all(r.status_code == 200 and r.content == payload for r in responses)
        assert len({r.headers["x-lanbridge-request-id"] for r in responses}) == 8
    asyncio.run(run())


def test_websocket_cross_site_origin_rejected(service):
    from starlette.websockets import WebSocketDisconnect
    service.save_site({"name": "socket", "hostname": "app.example.com", "origin": "http://127.0.0.1:9300", "human_check": False})
    with TestClient(create_gateway(service), base_url="https://app.example.com") as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("wss://app.example.com/ws", headers={"Origin": "https://evil.example"}):
                pass
        assert error.value.code == 1008
