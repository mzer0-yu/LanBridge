from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from lanbridge.gateway import create_gateway, PASS_COOKIE, signed_pass, valid_pass
from test_security import service, admin_client
from test_proxy import origin
import pytest
from starlette.websockets import WebSocketDisconnect


def test_pause_and_resume_keep_routes_and_other_sites_working(service, origin):
    site = service.save_site({"name": "Motor", "hostname": "motor.example.com", "origin": f"http://127.0.0.1:{origin}", "human_check": False})
    other = service.save_site({"name": "Other", "hostname": "other.example.com", "origin": f"http://127.0.0.1:{origin}", "human_check": False})
    service.store.set("published_hosts", [site["hostname"], other["hostname"]])
    admin = admin_client(service)
    gateway = TestClient(create_gateway(service), base_url="https://motor.example.com")
    assert gateway.get("/").status_code == 200
    old_pass = signed_pass(service, site, "testclient")
    # Pausing does not require Cloudflare connectivity or credentials.
    service.store.set_secret("cf_write_token", "")
    assert admin.post(f'/api/sites/{site["id"]}/pause', json={"paused": True}).status_code == 200
    paused = gateway.get("/", headers={"Accept": "text/html"})
    assert paused.status_code == 503
    assert paused.headers["content-type"] == "text/html; charset=utf-8"
    assert "网站暂时不可访问" in paused.content.decode("utf-8")
    assert "motor.example.com" in paused.text
    assert "Motor" not in paused.text
    assert '<a href="">重新尝试</a>' in paused.text
    assert "default-src 'none'" in paused.headers["content-security-policy"]
    api_response = gateway.get("/api/data", headers={"Accept": "application/json"})
    assert api_response.status_code == 503
    assert api_response.headers["content-type"] == "text/plain; charset=utf-8"
    assert api_response.text == "网站转发已暂停"
    assert paused.headers["cache-control"] == "no-store"
    with pytest.raises(WebSocketDisconnect):
        with gateway.websocket_connect("wss://motor.example.com/ws"):
            pass
    assert gateway.get("/", headers={"Host": other["hostname"]}).status_code == 200
    routes = admin.get("/api/client/routes").json()["routes"]
    assert next(row for row in routes if row["hostname"] == site["hostname"])["status"] == "已暂停"
    assert service.store.get("published_hosts") == [site["hostname"], other["hostname"]]
    # Editing unrelated fields must not silently resume the website.
    current = next(row for row in service.sites() if row["id"] == site["id"])
    current.pop("paused")
    assert service.save_site(current | {"name": "Renamed"})["paused"]
    assert admin.post(f'/api/sites/{site["id"]}/pause', json={"paused": False}).status_code == 200
    assert gateway.get("/").status_code == 200
    restored = next(row for row in service.sites() if row["id"] == site["id"])
    assert not valid_pass(service, restored, "testclient", old_pass)
    assert restored["origin"] == site["origin"]


def test_pause_requires_admin_csrf_and_valid_state(service):
    site = service.save_site({"name": "Motor", "hostname": "motor.example.com", "origin": "http://127.0.0.1:8765", "human_check": False})
    url = f'/api/sites/{site["id"]}/pause'
    public = TestClient(create_admin(service), base_url="http://127.0.0.1:8890", headers={"Origin": "http://127.0.0.1:8890"})
    assert public.post(url, json={"paused": True}).status_code == 401
    admin = admin_client(service)
    assert admin.post(url, json={"paused": True}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    for value in (None, "true", 1):
        assert admin.post(url, json={"paused": value}).status_code == 400
    assert admin.post('/api/sites/missing/pause', json={"paused": True}).status_code == 400
    assert not service.sites()[0].get("paused")


def test_paused_page_hides_name_and_preserves_verify_json(service):
    site = service.save_site({"name": '<img src=x onerror="alert(1)">', "hostname": "motor.example.com", "origin": "http://127.0.0.1:8765", "human_check": False})
    service.set_site_paused(site["id"], True)
    client = TestClient(create_gateway(service), base_url="https://motor.example.com")
    page = client.get("/", headers={"Accept": "text/html"})
    assert page.status_code == 503
    assert '<img src=x' not in page.text
    assert '&lt;img src=x' not in page.text
    assert 'motor.example.com' in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-content-type-options"] == "nosniff"
    response = client.post("/.lanbridge/verify", json={}, headers={"Origin": "https://motor.example.com", "Accept": "text/html"})
    assert response.status_code == 503
    assert response.json()["detail"] == "网站转发已暂停"


def test_pause_precedes_human_verification_and_resume_restores_gate(service):
    service.store.set("settings", service.settings() | {"turnstile_sitekey": "test-only-sitekey"})
    service.store.set_secret("turnstile_secret", "test-only-secret")
    site = service.save_site({"name": "Private motor control", "hostname": "motor.example.com", "origin": "http://127.0.0.1:8765", "human_check": True})
    client = TestClient(create_gateway(service), base_url="https://motor.example.com")
    headers = {"Accept": "text/html"}
    assert "challenges.cloudflare.com" in client.get("/", headers=headers).text
    service.set_site_paused(site["id"], True)
    for path in ["/", "/dashboard", "/.lanbridge/verify"]:
        paused = client.get(path, headers=headers)
        assert paused.status_code == 503
        assert "challenges.cloudflare.com" not in paused.text
        assert "Private motor control" not in paused.text
    verification = client.post("/.lanbridge/verify", json={"token": "test-only-token"}, headers={"Origin": "https://motor.example.com"})
    assert verification.status_code == 503
    assert verification.json()["detail"] == "网站转发已暂停"
    assert "set-cookie" not in verification.headers
    service.set_site_paused(site["id"], False)
    restored = client.get("/", headers=headers)
    assert restored.status_code == 200
    assert "challenges.cloudflare.com" in restored.text


def test_paused_requests_share_quota_and_escalate_without_reading_body(service, monkeypatch):
    async def forbidden_read(*args, **kwargs):
        raise AssertionError("paused request must not read its body")
    monkeypatch.setattr("lanbridge.gateway.bounded_body", forbidden_read)
    site = service.save_site({"name": "Private", "hostname": "motor.example.com", "origin": "http://127.0.0.1:8765", "human_check": True, "requests_per_minute": 10})
    service.set_site_paused(site["id"], True)
    client = TestClient(create_gateway(service), base_url="https://motor.example.com")
    for index in range(10):
        assert client.get(f"/asset-{index}.js").status_code == 503
    limited = client.post("/.lanbridge/verify", json={"token": "fake"}, headers={"Origin": "https://motor.example.com"})
    assert limited.status_code == 429
    assert limited.json()["detail"]
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    for index in range(59):
        assert client.get(f"/page-{index}").status_code == 429
    assert service.visitor_risk.remaining(site["id"], "testclient") > 0
    blocked = client.get("/", headers={"Accept": "text/html"})
    assert blocked.status_code == 429
    assert "challenges.cloudflare.com" not in blocked.text
    assert all("Max-Age=0" in cookie for cookie in blocked.headers.get_list("set-cookie"))
    audit = service.store.audit_list()
    assert any(row["action"] == "visitor_restricted" for row in audit)
    other_visitor = TestClient(client.app, base_url="https://motor.example.com", client=("198.51.100.2", 123))
    assert other_visitor.get("/", headers={"Accept": "text/html"}).status_code == 503


def test_expected_pause_logs_are_sampled_but_failures_are_not(monkeypatch):
    import asyncio
    import json
    from unittest.mock import Mock
    from lanbridge.gateway import TransferLog
    now = [1000.0]
    monkeypatch.setattr("lanbridge.gateway.time.monotonic", lambda: now[0])
    async def app(scope, receive, send):
        scope["state"]["transfer"]["expected_pause"] = True
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"paused"})
    logger = Mock()
    middleware = TransferLog(app, logger)
    async def request():
        async def receive(): return {"type": "http.request", "body": b""}
        async def send(message): pass
        await middleware({"type": "http", "method": "GET", "path": "/", "state": {}}, receive, send)
    async def run():
        for _ in range(100): await request()
        assert logger.warning.call_count == 1
        now[0] += 60
        await request()
        assert logger.warning.call_count == 2
        assert json.loads(logger.warning.call_args.args[1])["similar_pause_responses_suppressed"] == 99
        async def failing(scope, receive, send):
            scope["state"]["transfer"]["expected_pause"] = True
            raise RuntimeError("test failure")
        middleware.app = failing
        for _ in range(2):
            with pytest.raises(RuntimeError): await request()
        assert logger.warning.call_count == 4
    asyncio.run(run())
