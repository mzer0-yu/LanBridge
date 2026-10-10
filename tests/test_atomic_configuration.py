import asyncio
import sqlite3
import threading

import httpx
import pytest

from lanbridge.admin import create_admin
from lanbridge.gateway import signed_pass, valid_pass
from lanbridge.store import password_check
from test_security import service, admin_client, add_site


def reject_write(store, key, table="kv"):
    with store.lock, store.db:
        store.db.execute(f"CREATE TRIGGER reject_configuration BEFORE INSERT ON {table} "
                         f"WHEN NEW.key='{key}' BEGIN SELECT RAISE(ABORT, 'write rejected'); END")


@pytest.mark.parametrize("failure", ["policy", "passcode"])
def test_failed_site_write_keeps_passcode_policy_and_grants(service, failure):
    site = add_site(service, passcode_required=True, passcode="original visitor password")
    before = service.sites()
    secret = service.store.secret("passcode_" + site["id"])
    grant = signed_pass(service, site, "visitor")
    logs = service.store.audit_list()
    reject_write(service.store, "sites" if failure == "policy" else "passcode_" + site["id"],
                 "kv" if failure == "policy" else "secrets")
    with pytest.raises(sqlite3.IntegrityError, match="write rejected"):
        service.save_site(site | {"name": "New name", "passcode": "replacement visitor password"})
    assert service.sites() == before
    assert service.store.secret("passcode_" + site["id"]) == secret
    assert password_check("original visitor password", secret)
    assert valid_pass(service, service.sites()[0], "visitor", grant)
    assert service.store.audit_list() == logs


@pytest.mark.parametrize("failure", ["database", "encryption"])
def test_failed_widget_save_keeps_settings_policy_and_signing_key(service, monkeypatch, failure):
    add_site(service)
    client = admin_client(service)
    service.store.set("owned_widget", "existing-widget")
    before = service.settings()
    sites = service.sites()
    signing_key = service.store.secret("signing_key")
    logs = service.store.audit_list()
    if failure == "database":
        reject_write(service.store, "settings")
        expected = sqlite3.IntegrityError
    else:
        def reject_encryption(value):
            raise RuntimeError("encryption unavailable")
        monkeypatch.setattr(service.store.cipher, "encrypt", reject_encryption)
    if failure == "database":
        with pytest.raises(expected):
            client.post("/api/settings", json=before | {"turnstile_sitekey": "new-widget"})
    else:
        assert client.post("/api/settings", json=before | {"turnstile_sitekey": "new-widget"}).status_code == 400
    assert service.settings() == before
    assert service.sites() == sites
    assert service.store.get("owned_widget") == "existing-widget"
    assert service.store.secret("signing_key") == signing_key
    assert service.store.audit_list() == logs


def test_slow_probe_resolution_does_not_block_other_admin_requests(service, monkeypatch):
    site = add_site(service)
    owner = admin_client(service)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow_resolution(*args):
        entered.set()
        try:
            release.wait(2)
            return "http://127.0.0.1:9300", "app.example.com", "app.example.com"
        finally:
            finished.set()

    monkeypatch.setattr("lanbridge.admin.pinned_origin", slow_resolution)
    original = httpx.AsyncClient

    async def check():
        async with original(transport=httpx.ASGITransport(app=create_admin(service)),
                            base_url="http://127.0.0.1:8890", cookies=dict(owner.cookies),
                            headers=dict(owner.headers)) as client:
            monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
                transport=httpx.MockTransport(lambda request: httpx.Response(200)), **kw))
            probe = asyncio.create_task(client.post(f'/api/sites/{site["id"]}/probe', json={}))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                response = await client.get("/api/bootstrap")
                assert response.status_code == 200
                assert not finished.is_set(), "slow resolution blocked the admin event loop"
            finally:
                release.set()
                response = await probe
            assert response.status_code == 200
            assert response.json()["reachable"]

    asyncio.run(check())


def test_probe_closes_upstream_without_reading_response_body(service, monkeypatch):
    site = add_site(service)
    owner = admin_client(service)
    original = httpx.AsyncClient

    class UnboundedBody(httpx.AsyncByteStream):
        read = False
        closed = False
        async def __aiter__(self):
            self.read = True
            raise AssertionError("a status probe must not download the response body")
            yield b""
        async def aclose(self):
            self.closed = True

    stream = UnboundedBody()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)), **kw))
    response = owner.post(f'/api/sites/{site["id"]}/probe', json={})
    assert response.status_code == 200 and response.json()["reachable"]
    assert not stream.read and stream.closed
    assert service.store.get("probe_" + site["id"])["http_status"] == 200
