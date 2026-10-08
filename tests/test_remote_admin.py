import pytest
from fastapi.testclient import TestClient
from lanbridge.gateway import create_gateway, PASS_COOKIE, signed_pass
from lanbridge.admin import create_admin
from lanbridge.service import pinned_origin
from test_security import service, admin_client


def remote_site(service, **kwargs):
    admin_client(service)
    return service.save_site((dict(name="LanBridge", hostname="lb.example.com", target="lanbridge", human_check=False) | kwargs))


def remote_client(service):
    return TestClient(create_gateway(service), base_url="https://lb.example.com", headers={"Origin": "https://lb.example.com"})


def test_remote_admin_and_client_authentication_and_csrf(service):
    site = remote_site(service)
    assert site["protocols"] == ["http"]
    assert site["origin"] == "http://127.0.0.1:8890"
    with remote_client(service) as client:
        assert client.get("/").status_code == 200
        assert client.get("/client").status_code == 200
        assert client.get("/api/client/routes").json()["routes"][0]["origin"] == "http://127.0.0.1:8890"
        assert client.get("/admin").status_code == 200
        assert client.get("/api/bootstrap").json()["remote"] is True
        assert client.get("/api/state").status_code == 401
        assert client.post("/api/login", json={"username": "admin", "password": "wrong"}).status_code == 401
        login = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"})
        assert login.status_code == 200
        cookie = login.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert client.get("/api/state").status_code == 200
        assert client.post(f'/api/sites/{site["id"]}/pause', json={"paused": False}).status_code == 403
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        assert client.post('/api/password',json={}).status_code==403
        assert client.post('/api/cloudflare/browser-authorize-refresh',json={}).status_code==403
        issued=client.post('/api/temporary-tokens',json={'name':'remote maintenance'})
        assert issued.status_code==200
        assert client.get('/api/temporary-tokens').status_code==200
        assert client.post('/api/temporary-tokens/'+issued.json()['id']+'/revoke',json={}).status_code==200
        assert client.post(f'/api/sites/{site["id"]}/pause', json={"paused": False}, headers={"Origin": "https://evil.example.com"}).status_code == 403
        assert client.post(f'/api/sites/{site["id"]}/pause', json={"paused": False}).status_code == 200
        assert client.post("/api/logout", json={}).status_code == 200
        assert client.get("/api/state").status_code == 401


def test_local_operations_never_open_through_remote_admin(service):
    remote_site(service)
    with remote_client(service) as client:
        for path in ["/api/setup", "/api/local-login/start", "/api/local-login/open", "/api/local-login/poll", "/api/local-login/approve", "/api/cloudflare/browser-authorize", "/api/cloudflare/browser-authorize-restart", "/api/cloudflare/browser-authorize-refresh", "/api/shutdown", "/api/launcher/control", "/api/admin-port", "/api/connector-auto-start", "/api/restart", "/api/gateway-port", "/api/gateway-retry"]:
            assert client.post(path, json={}).status_code == 403
        assert client.get("/api/local-login/browsers").status_code == 403
        assert client.get("/api/state", headers={"Host": "127.0.0.1:8890"}).status_code == 404
    local = TestClient(create_admin(service), base_url="http://127.0.0.1:8890")
    assert local.get("/admin", headers={"Host": "lb.example.com"}).status_code == 403


def test_self_target_keeps_gate_pause_and_loop_protection(service):
    site = remote_site(service, human_check=True)
    service.store.set("settings", service.settings() | {"turnstile_sitekey": "public-test-key"})
    service.store.set_secret("turnstile_secret", "test-secret")
    with remote_client(service) as client:
        assert client.get("/admin", headers={"Accept": "text/html"}).text.find("cf-turnstile") >= 0
        assert client.get("/api/state").status_code == 401
        client.cookies.set(PASS_COOKIE, signed_pass(service, site, "testclient"))
        assert client.get("/admin").status_code == 200
        service.set_site_paused(site["id"], True)
        assert client.get("/admin").status_code == 503
    for port in [8890, 8891]:
        with pytest.raises(ValueError, match="不能转发"):
            pinned_origin({"origin": f"http://127.0.0.1:{port}"}, service.settings())
    with pytest.raises(ValueError):
        pinned_origin(site | {"origin": "http://127.0.0.1:8891"}, service.settings())


def test_self_target_requires_local_admin_and_preserves_target_on_edits(service):
    with pytest.raises(ValueError, match="本机管理员"):
        service.save_site({"name": "LB", "hostname": "lb.example.com", "target": "lanbridge", "human_check": False})
    site = remote_site(service)
    update = dict(site); update.pop("target")
    assert service.save_site(update)["target"] == "lanbridge"


def test_remote_login_limits_and_bounded_body(service):
    remote_site(service)
    with remote_client(service) as client:
        assert client.post("/api/login", content=b"x" * 21000).status_code == 400
        for _ in range(4):
            assert client.post("/api/login", json={"username": "admin", "password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"username": "admin", "password": "wrong"}).status_code == 429


def test_public_client_switch_blocks_remote_page_and_data_but_preserves_admin_and_local(service):
    from lanbridge.service import Service
    remote_site(service)
    owner = admin_client(service)
    with remote_client(service) as client:
        assert client.get("/api/bootstrap").json()["public_client_enabled"] is True
        assert client.get("/client").status_code == 200
        assert client.post("/api/public-client", json={"enabled": False}).status_code == 401
        assert owner.post("/api/public-client", json={"enabled": False}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
        login = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"})
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        assert client.post("/api/public-client", json={"enabled": False}).status_code == 200
        for path in ("/client", "/client/", "/client.js", "/client.css", "/api/client/routes"):
            response = client.get(path)
            assert response.status_code == 404
            assert response.headers["cache-control"] == "no-store"
            assert "routes" not in response.text
        assert client.get("/admin").status_code == 200
        assert client.get("/", follow_redirects=False).headers["location"] == "/admin"
        assert client.get("/api/state").json()["public_client_enabled"] is False
        assert owner.get("/client").status_code == 200
        assert owner.get("/api/client/routes").status_code == 200
        reopened = Service(service.store.root)
        try:
            assert reopened.store.get("public_client_enabled") is False
        finally:
            reopened.store.db.close()
        assert client.post("/api/public-client", json={"enabled": True}).status_code == 200
        assert client.get("/client").status_code == 200
        assert client.get("/api/client/routes").status_code == 200
        assert client.get("/", follow_redirects=False).headers["location"] == "/client"


@pytest.mark.parametrize("enabled", [None, 0, "false", [], {}])
def test_public_client_switch_rejects_invalid_value(service, enabled):
    owner = admin_client(service)
    assert owner.post("/api/public-client", json={"enabled": enabled}).status_code == 400
    assert owner.get("/api/bootstrap").json()["public_client_enabled"] is True


def test_temporary_account_token_cannot_change_public_client_switch(service):
    remote_site(service)
    owner = admin_client(service)
    issued = owner.post("/api/temporary-tokens", json={"permissions": ["sites", "account"]}).json()
    with remote_client(service) as client:
        response = client.post("/api/public-client", json={"enabled": False}, headers={"Authorization": "Bearer " + issued["token"]})
        assert response.status_code == 403
    assert service.store.get("public_client_enabled", True) is True


def test_disabled_public_client_does_not_offer_entry_verification(service):
    site = remote_site(service, human_check=True)
    service.store.set("settings", service.settings() | {"turnstile_sitekey": "public-test-key"})
    service.store.set_secret("turnstile_secret", "test-secret")
    owner = admin_client(service)
    assert owner.post("/api/public-client", json={"enabled": False}).status_code == 200
    with remote_client(service) as client:
        for path in ("/client", "/api/client/routes"):
            response = client.get(path, headers={"Accept": "text/html"})
            assert response.status_code == 404
            assert "turnstile" not in response.text.lower()
            if path == "/client":
                assert response.headers["content-type"] == "text/html; charset=utf-8"
                assert "转发列表未启用" in response.content.decode("utf-8")
                assert "此页面已由管理员关闭。" in response.text
                assert 'href="/admin"' in response.text
                assert 'default-src' in response.headers["content-security-policy"]
                assert "<script" not in response.text
            else:
                assert response.headers["content-type"] == "application/json"
                assert response.json() == {"detail": "公网转发列表未启用"}
        assert "cf-turnstile" in client.get("/admin", headers={"Accept": "text/html"}).text
        client.cookies.set(PASS_COOKIE, signed_pass(service, site, "testclient"))
        assert client.get("/admin").status_code == 200


def test_state_sites_and_probes_share_one_read(service, monkeypatch):
    client = admin_client(service)
    site = service.save_site(dict(name="Snapshot", hostname="snapshot.example.com", origin="http://127.0.0.1:9300", human_check=False))
    service.store.set("probe_" + site["id"], {"ok": True})
    original = service.sites
    calls = []
    def read_sites(cfg=None):
        calls.append(True)
        return original(cfg)
    monkeypatch.setattr(service, "sites", read_sites)
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert len(calls) == 1
    assert data["site_probes"][site["id"]] == {"ok": True}
    assert set(data["site_probes"]) == {site["id"] for site in data["sites"]}
