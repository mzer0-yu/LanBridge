import asyncio
import sqlite3
import threading

import httpx
import pytest

from lanbridge.admin import create_admin
from lanbridge.gateway import signed_pass, valid_pass
from lanbridge.store import password_check
from test_security import service, admin_client, add_site
from test_atomic_configuration import reject_write


def test_password_change_and_session_revocation_rollback_together(service):
    owner = admin_client(service)
    previous = service.store.get("admin")
    cookie = owner.cookies.get("lb_admin")
    with service.store.lock, service.store.db:
        service.store.db.execute("CREATE TRIGGER reject_revocation BEFORE DELETE ON sessions "
                                 "BEGIN SELECT RAISE(ABORT, 'revocation rejected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        owner.post("/api/password", json={"current": "correct horse battery", "password": "new administrator password"})
    assert service.store.get("admin") == previous
    assert password_check("correct horse battery", service.store.get("admin")["password_hash"])
    assert service.store.session(cookie) is not None


def test_tunnel_identity_commit_rolls_back_and_recovers_without_duplicate_create(service, monkeypatch):
    previous = service.settings()
    created = []
    identity = {"id": "11111111-1111-1111-1111-111111111111", "name": "LanBridge-test"}
    monkeypatch.setattr(service.cf, "zone", lambda: {})
    def request(method, path, body=None):
        if path.endswith("/token"):
            return "isolated-connector-token"
        if method == "POST":
            created.append(body["name"])
            return identity | {"name": body["name"]}
        return [identity | {"name": created[0]}]
    monkeypatch.setattr(service.cf, "request", request)
    reject_write(service.store, "owned_tunnel")
    with pytest.raises(sqlite3.IntegrityError):
        service.cf.create_tunnel()
    assert service.settings() == previous
    assert service.store.get("owned_tunnel") is None
    assert service.store.get("pending_tunnel_create")["name"] == created[0]
    with service.store.lock, service.store.db:
        service.store.db.execute("DROP TRIGGER reject_configuration")
    result = service.cf.create_tunnel()
    assert result["token_saved"]
    assert len(created) == 1
    assert service.store.get("owned_tunnel") == identity["id"]
    assert service.store.get("pending_tunnel_create") is None


def test_cli_widget_change_invalidates_existing_visitor_grant(service, monkeypatch, tmp_path):
    import run
    site = add_site(service)
    grant = signed_pass(service, site, "visitor")
    assert valid_pass(service, site, "visitor", grant)
    config = tmp_path / "settings.json"
    import json
    config.write_text(json.dumps(service.settings() | {"turnstile_sitekey": "new-widget"}), encoding="utf-8")
    monkeypatch.setattr(run, "Service", lambda root: service)
    monkeypatch.setattr(run, "acquire_runtime", lambda root: open(tmp_path / "lock", "wb"))
    monkeypatch.setattr("sys.argv", ["run.py", "configure", str(config)])
    assert run.main() == 0
    assert not valid_pass(service, service.sites()[0], "visitor", grant)


@pytest.mark.parametrize("path,data", [
    ("/api/setup", {"username": "admin", "password": "correct horse battery"}),
    ("/api/password", {"current": "correct horse battery", "password": "new administrator password"}),
    ("/api/settings", {}),
    ("/api/credentials", {}),
    ("/api/gateway-port", {"port": 8891}),
])
def test_waiting_for_business_lock_does_not_block_admin_event_loop(service, path, data):
    owner = admin_client(service)
    if path == "/api/settings":
        data = service.settings()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def holder():
        with service.lock:
            entered.set()
            release.wait(2)
        finished.set()
    thread = threading.Thread(target=holder)
    app = create_admin(service)
    thread.start()
    assert entered.wait(5)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8890",
                                     cookies=dict(owner.cookies), headers=dict(owner.headers)) as client:
            pending = asyncio.create_task(client.post(path, json=data))
            try:
                await asyncio.sleep(.02)
                response = await client.get("/api/bootstrap")
                assert response.status_code == 200
                assert not finished.is_set(), "waiting for the business lock blocked other requests"
            finally:
                release.set()
                await pending
    try:
        asyncio.run(check())
    finally:
        release.set()
        thread.join(timeout=5)
