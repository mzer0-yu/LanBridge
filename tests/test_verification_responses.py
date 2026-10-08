import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from lanbridge.gateway import ResourceLimits, create_gateway
from test_security import service, add_site


def test_verify_body_errors_are_json_and_not_cached(service, monkeypatch):
    add_site(service)
    client = TestClient(create_gateway(service), base_url="https://app.example.com")
    headers = {"Origin": "https://app.example.com"}
    for failure, status in [(TimeoutError(), 408), (OverflowError(), 413)]:
        async def read(*args, error=failure, **kwargs):
            raise error
        monkeypatch.setattr("lanbridge.gateway.bounded_body", read)
        response = client.post("/.lanbridge/verify", json={"token": "fake"}, headers=headers)
        assert response.status_code == status
        assert response.json()["detail"]
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize("mode,status", [("missing", 404), ("paused", 503), ("denied", 403), ("rate", 429)])
def test_verify_policy_errors_are_json(service, mode, status):
    options = {"allowed_ips": ["203.0.113.0/24"]} if mode == "denied" else {}
    site = add_site(service, **options)
    if mode == "paused":
        service.set_site_paused(site["id"], True)
    client = TestClient(create_gateway(service), base_url="https://app.example.com")
    headers = {"Origin": "https://app.example.com"}
    if mode == "missing":
        headers["Host"] = "missing.example.com"
    if mode == "rate":
        for _ in range(site["requests_per_minute"]):
            client.get("/api/private")
    response = client.post("/.lanbridge/verify", json={}, headers=headers)
    assert response.status_code == status
    assert response.json()["detail"]
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("mode,status", [("rate", 429), ("busy", 503)])
def test_verify_resource_rejection_is_json_without_reading_body(mode, status):
    async def run():
        async def app(*args):
            raise AssertionError("rejection must precede application")
        guard = ResourceLimits(app, rate=0, burst=0) if mode == "rate" else ResourceLimits(app, verify=0)
        messages = []
        async def send(message):
            messages.append(message)
        async def receive():
            raise AssertionError("body must not be read")
        scope = {"type": "http", "method": "POST", "path": "/.lanbridge/verify", "headers": [], "client": ("127.0.0.1", 1)}
        await guard(scope, receive, send)
        assert messages[0]["status"] == status
        assert dict(messages[0]["headers"])[b"cache-control"] == b"no-store"
        assert json.loads(messages[1]["body"])["detail"]
    asyncio.run(run())
