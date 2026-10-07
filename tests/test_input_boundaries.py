import json

import pytest
from fastapi.testclient import TestClient

from lanbridge.admin import create_admin
from lanbridge.gateway import create_gateway
from test_security import service, admin_client, add_site
from test_local_login import browsers, start


@pytest.mark.parametrize("code", ["１２３４５６", "确认码", "é" * 6])
def test_non_ascii_confirmation_code_is_rejected_without_consuming_request(browsers, code):
    app, approver, requester, _, _ = browsers
    job = start(requester)
    data = {"request_id": job["request_id"], "code": code, "allow": True}
    result = approver.post("/api/local-login/approve", json=data)
    assert result.status_code == 400
    assert requester.post("/api/local-login/poll", json={"request_id": job["request_id"]}).json()["phase"] == "pending"
    assert approver.post("/api/local-login/approve", json=data | {"code": job["code"]}).status_code == 200
    assert requester.post("/api/local-login/poll", json={"request_id": job["request_id"]}).json()["phase"] == "done"


@pytest.mark.parametrize("gateway", [False, True])
def test_deep_json_request_returns_validation_error_without_grant(service, gateway):
    # Exceeds native decoder depth while staying under the admin body limit.
    payload = '{"token":' + '[' * 3000 + '0' + ']' * 3000 + '}'
    if gateway:
        add_site(service)
        service.store.set("settings", service.settings() | {"turnstile_sitekey": "isolated-key"})
        service.store.set_secret("turnstile_secret", "isolated-secret")
        app, base, endpoint = create_gateway(service), "https://app.example.com", "/.lanbridge/verify"
    else:
        admin_client(service)
        app, base, endpoint = create_admin(service), "http://127.0.0.1:8890", "/api/token-login"
    with TestClient(app, base_url=base) as client:
        response = client.post(endpoint, content=payload, headers={"Origin": base, "Content-Type": "application/json"})
        # The gateway's existing 4 KiB limit rejects this before decoding.
        assert response.status_code == (413 if gateway else 400)
        if not gateway:
            assert response.json()["detail"] == "请求格式错误"
        assert "set-cookie" not in response.headers
        assert client.get("/admin" if not gateway else "/", headers={"Accept": "text/html"}).status_code == 200


def test_recursion_in_upstream_verification_response_fails_closed(service, monkeypatch):
    add_site(service)
    service.store.set_secret("turnstile_secret", "isolated-secret")
    class Reply:
        status_code = 200
        def json(self):
            return json.loads('[' * 3000 + '0' + ']' * 3000)
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): return Reply()
    monkeypatch.setattr("lanbridge.gateway.httpx.AsyncClient", Client)
    with TestClient(create_gateway(service), base_url="https://app.example.com") as client:
        response = client.post("/.lanbridge/verify", json={"token": "test-token"}, headers={"Origin": "https://app.example.com"})
    assert response.status_code == 503
    assert "set-cookie" not in response.headers and "isolated-secret" not in response.text


@pytest.mark.parametrize("verify", [False, True])
def test_extremely_long_content_length_is_rejected_before_upstream_work(service, monkeypatch, verify):
    service.save_site({"name": "input", "hostname": "app.example.com", "origin": "http://127.0.0.1:9300", "human_check": False})
    def forbidden(*args):
        pytest.fail("Oversized request must not resolve the upstream address")
    monkeypatch.setattr("lanbridge.gateway.pinned_origin", forbidden)
    with TestClient(create_gateway(service), base_url="https://app.example.com") as client:
        response = client.post("/.lanbridge/verify" if verify else "/upload", content=b"", headers={"Origin": "https://app.example.com", "Content-Length": "9" * 5000})
    assert response.status_code == 413
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("body", [b"", b"hello"])
def test_leading_zero_content_length_is_normalized_without_altering_body(service, monkeypatch, body):
    import httpx
    from lanbridge.upstream import UpstreamPools
    service.save_site({"name": "input", "hostname": "app.example.com", "origin": "http://127.0.0.1:9300", "human_check": False})
    observed = []
    async def handler(request):
        observed.append((request.headers["content-length"], await request.aread()))
        return httpx.Response(200, headers={"Content-Length": "2"}, stream=httpx.ByteStream(b"ok"))
    async def borrow(self, *args, **kwargs):
        return httpx.MockTransport(handler)
    monkeypatch.setattr(UpstreamPools, "borrow", borrow)
    with TestClient(create_gateway(service), base_url="https://app.example.com") as client:
        response = client.post("/upload", content=body, headers={"Content-Length": "0" * 5000 + str(len(body))})
    assert response.status_code == 200 and response.content == b"ok"
    assert observed == [(str(len(body)), body)]


def test_existing_numeric_confirmation_code_remains_supported(browsers, monkeypatch):
    _, approver, requester, _, _ = browsers
    monkeypatch.setattr("lanbridge.local_login.secrets.randbelow", lambda maximum: 123456)
    job = start(requester)
    assert job["code"] == "123456"
    assert approver.post("/api/local-login/approve", json={"request_id": job["request_id"], "code": 123456, "allow": True}).status_code == 200
    assert requester.post("/api/local-login/poll", json={"request_id": job["request_id"]}).json()["phase"] == "done"
