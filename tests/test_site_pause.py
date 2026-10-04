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
    assert gateway.get("/").status_code == 503
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
