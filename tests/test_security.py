import json
from pathlib import Path
import secrets
import pytest
from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from lanbridge.gateway import create_gateway, signed_pass, valid_pass, PASS_COOKIE
from lanbridge.service import Service, lan_address, pinned_origin
from lanbridge.store import password_hash, password_check


def admin_client(service):
    client = TestClient(create_admin(service), base_url="http://127.0.0.1:8890", headers={"Origin": "http://127.0.0.1:8890"})
    client.post("/api/setup", json={"username": "admin", "password": "correct horse battery"})
    login = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"})
    client.headers["X-CSRF-Token"] = login.json()["csrf"]
    return client


def test_credentials_validation_is_atomic(service):
    client = admin_client(service)
    original = service.store.secret("cf_write_token")
    response = client.post("/api/credentials", json={"cf_write_token": "new-valid-write-token", "turnstile_secret": "short"})
    assert response.status_code == 400
    assert service.store.secret("cf_write_token") == original
    assert not service.store.secret("turnstile_secret")
    response = client.post("/api/credentials", json={"cf_write_token": ["not-a-token"]})
    assert response.status_code == 400
    assert service.store.secret("cf_write_token") == original


def test_site_passcode_rejects_non_string_values_without_mutation(service):
    client=admin_client(service)
    previous=service.sites()
    for value in (False,123,[],{},None):
        response=client.post('/api/sites',json={'name':'invalid','hostname':'invalid.example.com',
                            'origin':'http://127.0.0.1:9300','human_check':False,'passcode':value})
        assert response.status_code==400
        assert response.json()['detail']=='网站访问口令格式无效'
        assert service.sites()==previous


def test_optional_read_token_can_be_removed_without_changing_business_token(service):
    client = admin_client(service)
    service.store.set_secret("cf_read_token", "optional-read-token")
    original = service.store.secret("cf_write_token")
    response = client.post("/api/credentials", json={"remove_cf_read_token": True})
    assert response.status_code == 200
    assert response.json()["credentials"]["cf_read_token"] is False
    assert service.store.secret("cf_write_token") == original


def test_validation_errors_name_fields_without_echoing_raw_input(service):
    client = admin_client(service)
    response = client.post("/api/settings", json=service.settings() | {"account_id": "invalid-private-example"})
    assert response.status_code == 400
    assert "Account ID" in response.json()["detail"]
    assert "invalid-private-example" not in response.text


def test_cloudflare_ids_normalize_pasted_whitespace_and_case(service):
    client = admin_client(service)
    response = client.post("/api/settings", json=service.settings() | {"account_id": " " + "A" * 32 + "\n"})
    assert response.status_code == 200
    assert service.settings()["account_id"] == "a" * 32


def test_changed_widget_persists_policy_version_and_invalidates_visitor_grants(service):
    site = add_site(service)
    client = admin_client(service)
    old_signing_key = service.store.secret("signing_key")
    response = client.post("/api/settings", json=service.settings() | {"turnstile_sitekey": "changed-widget"})
    assert response.status_code == 200
    assert response.json()["settings"]["turnstile_sitekey"] == "changed-widget"
    assert service.sites()[0]["policy_version"] != site["policy_version"]
    assert service.store.secret("signing_key") != old_signing_key


def test_permission_error_context_and_replacement_are_reported_without_false_current_failure(service, monkeypatch):
    import httpx
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(403, json={"success": False, "errors": [{"code": 10000}]}))
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=transport, **kw))
    with pytest.raises(RuntimeError):
        service.cf.request("POST", "/accounts/" + "a" * 32 + "/cfd_tunnel", {})
    assert service.permission_issues()[0]["status"] == "last_failure"
    client = admin_client(service)
    response = client.post("/api/credentials", json={"cf_write_token": "replacement-business-token"})
    assert response.status_code == 200 and response.json()["credentials"]["cf_write_token"] is True
    issue = service.permission_issues()[0]
    assert issue["status"] == "needs_recheck" and "credential_digest" not in issue
    service.store.set("settings", service.settings() | {"account_id": "c" * 32})
    assert service.permission_issues() == []


def test_cloudflare_forbidden_names_operation_without_echoing_upstream_secrets(service, monkeypatch):
    import httpx
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(403, json={"success": False, "errors": [{"code": 10000, "message": "sensitive-upstream-value"}]}))
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=transport, **kw))
    with pytest.raises(RuntimeError) as failure:
        service.cf.request("POST", "/accounts/" + "a" * 32 + "/cfd_tunnel", {"name": "example"})
    text = str(failure.value)
    assert "HTTP 403" in text and "创建 Tunnel" in text and "写入 API Token" in text
    assert "10000" in text and "Cloudflare Tunnel Write" in text
    assert "sensitive-upstream-value" not in text and "test-only-cloudflare-token" not in text
    assert len(text) < 400
    issues = service.store.get("cloudflare_permission_issues")
    assert len(issues) == 1 and next(iter(issues.values()))["detail"] == text


def test_permission_warning_survives_reload_and_unrelated_success_until_retry_succeeds(service, monkeypatch):
    import httpx
    original = httpx.Client
    forbidden = True
    def respond(request):
        if request.method == "POST" and forbidden:
            return httpx.Response(403, json={"success": False, "errors": [{"code": 10000}]})
        return httpx.Response(200, json={"success": True, "result": {"id": "test"}})
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    path = "/accounts/" + "a" * 32 + "/cfd_tunnel"
    with pytest.raises(RuntimeError):
        service.cf.request("POST", path, {})
    reloaded = Service(service.store.root)
    assert reloaded.store.get("cloudflare_permission_issues")
    reloaded.store.db.close()
    service.cf.request("GET", path)
    assert service.store.get("cloudflare_permission_issues")
    forbidden = False
    service.cf.request("POST", path, {})
    assert service.store.get("cloudflare_permission_issues") == {}


def test_cloudflare_zone_error_identifies_optional_read_token(service, monkeypatch):
    import httpx
    service.store.set_secret("cf_read_token", "read-test-token")
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(403, text="not-json"))
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=transport, **kw))
    with pytest.raises(RuntimeError, match="读取域名.*只读 API Token"):
        service.cf.zone()


@pytest.fixture
def service(tmp_path):
    result = Service(tmp_path / "data")
    cfg = result.settings()
    cfg.update(zone_name="example.com", zone_id="b" * 32, account_id="a" * 32)
    result.store.set("settings", cfg)
    result.store.set_secret("cf_write_token", "test-only-cloudflare-token")
    yield result
    result.store.db.close()


def add_site(service, **kwargs):
    return service.save_site(dict(name="LAN app", hostname="app.example.com", origin="http://127.0.0.1:9300", human_check=True, **kwargs))


@pytest.mark.parametrize("missing", ["account_id", "zone_id", "zone_name", "cf_write_token"])
def test_new_site_requires_cloudflare_setup_without_side_effects(service, missing):
    if missing == "cf_write_token":
        service.store.set_secret(missing, "")
    else:
        cfg = service.settings()
        cfg[missing] = ""
        service.store.set("settings", cfg)
    assert service.cloudflare_setup()["ready"] is False
    before = service.store.audit_list()
    with pytest.raises(ValueError, match="Cloudflare 尚未配置完整"):
        add_site(service, passcode_required=True, passcode="long visitor password")
    assert service.sites() == []
    assert service.store.audit_list() == before
    assert not any(r[0].startswith("passcode_") for r in service.store.db.execute("SELECT key FROM secrets"))


def test_missing_credentials_do_not_prevent_disabling_existing_site(service):
    site = add_site(service)
    service.store.set_secret("cf_write_token", "")
    assert service.save_site(site | {"enabled": False})["enabled"] is False


def test_encrypted_secrets_and_password_hash(service):
    token = "sensitive-token-" + secrets.token_hex(12)
    service.store.set_secret("cf_write_token", token)
    assert service.store.secret("cf_write_token") == token
    rows = service.store.db.execute("SELECT value FROM secrets").fetchall()
    assert all(token not in row[0] for row in rows)
    hashed = password_hash("correct horse battery")
    assert password_check("correct horse battery", hashed)
    assert not password_check("wrong", hashed)
    assert "correct horse" not in hashed


def test_mcp_bridge_uses_admin_login_and_csrf(service, monkeypatch):
    from mcp_server import Bridge
    client = TestClient(create_admin(service), base_url="http://127.0.0.1:8890", headers={"Origin": "http://127.0.0.1:8890"})
    assert client.post("/api/connector/ensure", json={}).status_code == 401
    password = "test-only-admin-password"
    assert client.post("/api/setup", json={"username": "admin", "password": password}).status_code == 200
    monkeypatch.setenv("LANBRIDGE_ADMIN_PASSWORD", password)
    monkeypatch.setattr(service.connector, "ensure", lambda path=None: {"path": "test-path", "source": "local"})
    bridge = Bridge()
    bridge.client.close()
    bridge.client = client
    assert bridge.call("lanbridge_status", {})["cloudflare_setup"]["ready"]
    assert bridge.call("lanbridge_prepare_connector", {})["source"] == "local"
    with pytest.raises(ValueError, match="参数无效"):
        bridge.call("lanbridge_prepare_connector", {"password": password})
    assert client.post("/api/connector/ensure", json={}).status_code == 403
    assert client.post("/api/connector/ensure", json={"path": 123}, headers={"X-CSRF-Token": bridge.csrf}).status_code == 400


def test_admin_host_origin_auth_and_csrf(service):
    client = TestClient(create_admin(service), base_url="http://127.0.0.1:8890")
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/setup", json={"password": "safe long password"}).status_code == 403
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
    headers = {"Origin": "http://127.0.0.1:8890"}
    assert client.post("/api/setup", json={"username": "admin", "password": "safe long password"}, headers=headers).status_code == 200
    assert client.post("/api/setup", json={"username": "other", "password": "safe long password"}, headers=headers).status_code == 400
    result = client.post("/api/login", json={"username": "admin", "password": "safe long password"}, headers=headers)
    assert result.status_code == 200
    assert client.post("/api/settings", json=service.settings(), headers=headers).status_code == 403
    headers["X-CSRF-Token"] = result.json()["csrf"]
    secret = "private-token-test-12345"
    assert client.post("/api/credentials", json={"cf_write_token": secret}, headers=headers).status_code == 200
    state = client.get("/api/state")
    assert secret not in state.text
    assert state.json()["credentials"]["cf_write_token"]
    assert client.post("/api/logout", json={}, headers=headers).status_code == 200
    assert client.get("/api/state").status_code == 401


def test_session_bound_to_host_ip_policy(service):
    site = add_site(service)
    token = signed_pass(service, site, "203.0.113.4")
    assert valid_pass(service, site, "203.0.113.4", token)
    assert not valid_pass(service, site, "203.0.113.5", token)
    assert not valid_pass(service, site | {"hostname": "other.example.com"}, "203.0.113.4", token)
    assert not valid_pass(service, site | {"policy_version": "new"}, "203.0.113.4", token)
    assert not valid_pass(service, site, "203.0.113.4", token + "x")


def test_gateway_redirects_public_http_and_keeps_strict_https_origin(service):
    add_site(service)
    app = create_gateway(service)
    client = TestClient(app, base_url="http://app.example.com", client=("127.0.0.1", 12345), follow_redirects=False)
    redirect = client.get("/deep/path?a=1", headers={"CF-Visitor": '{"scheme":"http"}'})
    assert redirect.status_code == 308
    assert redirect.headers["location"] == "https://app.example.com/deep/path?a=1"
    edge = {"CF-Visitor": '{"scheme":"https"}'}
    assert client.get("/", headers=edge | {"Accept": "text/html"}).status_code == 503
    for origin in ("http://app.example.com", "https://evil.example.com", "null"):
        result = client.post("/.lanbridge/verify", json={}, headers=edge | {"Origin": origin})
        assert result.status_code == 403 and result.json()["detail"] == "来源校验失败"
    external = TestClient(app, base_url="http://app.example.com", follow_redirects=False)
    assert external.get("/", headers=edge).status_code == 308


def test_unconfigured_turnstile_and_api_cannot_bypass(service):
    add_site(service)
    client = TestClient(create_gateway(service), base_url="https://app.example.com")
    assert client.get("/", headers={"Accept": "text/html"}).status_code == 503
    assert client.get("/api/private").status_code == 401
    assert client.post("/write", json={}).status_code == 401
    assert client.get("/", headers={"Host": "unknown.example.com"}).status_code == 404
    assert client.post("/.lanbridge/verify", json={"token": "fake"}, headers={"Origin": "https://app.example.com"}).status_code == 403
    with pytest.raises(Exception):
        with client.websocket_connect("/socket"):
            pass


def test_country_ip_and_gateway_reserved_port(service):
    site = add_site(service, allowed_countries=["CN"], allowed_ips=["203.0.113.0/24"])
    client = TestClient(create_gateway(service), base_url="https://app.example.com")
    # Non-connector peers cannot assert trusted Cloudflare headers.
    assert client.get("/", headers={"Accept": "text/html", "CF-IPCountry": "CN", "CF-Connecting-IP": "203.0.113.1"}).status_code == 403
    for host in ("169.254.169.254", "8.8.8.8", "0.0.0.0", "224.0.0.1"):
        with pytest.raises(ValueError):
            lan_address(host)
    for port in (8890, 8891):
        with pytest.raises(ValueError):
            pinned_origin(site | {"origin": f"http://127.0.0.1:{port}"}, service.settings())


def test_turnstile_validates_hostname_action_and_passcode(service, monkeypatch):
    site = add_site(service, passcode_required=True, passcode="safe visitor password")
    service.store.set_secret("turnstile_secret", "private-turnstile-secret")
    cfg = service.settings()
    cfg["turnstile_sitekey"] = "public-key"
    service.store.set("settings", cfg)
    result = {"success": True, "hostname": "wrong.example.com", "action": "lanbridge"}
    calls = []
    class FakeResponse:
        status_code = 200
        def json(self): return result
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, data):
            calls.append(data)
            return FakeResponse()
    monkeypatch.setattr("lanbridge.gateway.httpx.AsyncClient", FakeClient)
    client = TestClient(create_gateway(service), base_url="https://app.example.com")
    headers = {"Origin": "https://app.example.com"}
    body = {"token": "test-token", "passcode": "safe visitor password"}
    assert client.post("/.lanbridge/verify", json=body, headers=headers).status_code == 403
    result["hostname"] = "app.example.com"
    result["action"] = "wrong-action"
    assert client.post("/.lanbridge/verify", json=body, headers=headers).status_code == 403
    result["action"] = "lanbridge"
    response = client.post("/.lanbridge/verify", json=body, headers=headers)
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
    assert calls[-1]["secret"] == "private-turnstile-secret"
    assert valid_pass(service, site, "testclient", client.cookies.get(PASS_COOKIE))


def test_rate_limit_applies_even_with_visitor_cookie(service):
    site = add_site(service, requests_per_minute=10)
    client = TestClient(create_gateway(service), base_url="https://app.example.com")
    for _ in range(10):
        assert client.get("/api/private").status_code == 401
    assert client.get("/api/private").status_code == 429


def test_password_change_rejects_in_flight_old_password_login(service, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import lanbridge.admin as admin
    owner = admin_client(service)
    outsider = TestClient(create_admin(service), base_url="http://127.0.0.1:8890", headers={"Origin":"http://127.0.0.1:8890"})
    checked, release = threading.Event(), threading.Event()
    original = admin.password_check
    def delayed(value, encoded):
        valid = original(value, encoded)
        if not checked.is_set():
            checked.set()
            assert release.wait(5)
        return valid
    monkeypatch.setattr(admin, "password_check", delayed)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(outsider.post, "/api/login", json={"username":"admin","password":"correct horse battery"})
        try:
            assert checked.wait(5)
            response = owner.post("/api/password", json={"current":"correct horse battery","password":"new administrator password"})
            assert response.status_code == 200
        finally:
            release.set()
        response = future.result(timeout=5)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert service.store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert outsider.post("/api/login", json={"username":"admin","password":"new administrator password"}).status_code == 200


def test_lan_origin_rechecks_dns_after_private_address_changes(monkeypatch):
    import socket
    answers = iter([
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 0))],
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 0)),
         (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]])
    monkeypatch.setattr(socket,"getaddrinfo",lambda *args,**kwargs:next(answers))
    assert lan_address("changing-origin.local") == "192.168.1.10"
    with pytest.raises(ValueError,match="不支持公网"):
        lan_address("changing-origin.local")
    with pytest.raises(ValueError,match="不支持公网"):
        lan_address("changing-origin.local")


@pytest.mark.parametrize("payload", [None, [], True, "private-upstream-message"])
def test_malformed_turnstile_result_fails_safely_without_grant(service, monkeypatch, payload):
    add_site(service)
    service.store.set_secret("turnstile_secret", "private-turnstile-secret")
    class FakeResponse:
        status_code = 200
        def json(self):
            return payload
    class FakeClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, *args, **kwargs):
            return FakeResponse()
    monkeypatch.setattr("lanbridge.gateway.httpx.AsyncClient", FakeClient)
    with TestClient(create_gateway(service), base_url="https://app.example.com") as client:
        response = client.post("/.lanbridge/verify", json={"token": "test-token"},
                               headers={"Origin": "https://app.example.com"})
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert "private-upstream-message" not in response.text
    assert "private-turnstile-secret" not in response.text
