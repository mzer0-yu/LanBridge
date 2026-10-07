import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from lanbridge.service import Service
from run import acquire_runtime


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.mark.parametrize('change_gateway', [False, True])
def test_authenticated_shutdown_exits_servers_and_releases_runtime_lock(tmp_path, change_gateway):
    service = Service(tmp_path)
    admin_port, gateway_port = unused_port(), unused_port()
    while admin_port == gateway_port:
        gateway_port = unused_port()
    cfg = service.settings() | {"admin_port": admin_port, "gateway_port": gateway_port}
    service.store.set("settings", cfg)
    if change_gateway:
        replacement = unused_port()
        while replacement in (admin_port, gateway_port):
            replacement = unused_port()
        service.queue_gateway_port(replacement)
        gateway_port = replacement
    service.store.set_secret("cf_write_token", "test-preserved-token")
    service.store.db.close()
    project = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, str(project / "run.py"), "--data-dir", str(tmp_path), "serve"],
                               cwd=project, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{admin_port}"
    try:
        with httpx.Client(base_url=url, headers={"Origin": url}, timeout=2, trust_env=False) as client:
            deadline = time.monotonic() + 15
            while True:
                try:
                    assert client.get("/api/bootstrap").status_code == 200
                    with httpx.Client(trust_env=False) as gateway_client:
                        assert gateway_client.get(f"http://127.0.0.1:{gateway_port}/").status_code < 500
                    break
                except httpx.TransportError:
                    if time.monotonic() >= deadline or process.poll() is not None:
                        raise AssertionError("test server failed to start")
                    time.sleep(0.1)
            assert client.post("/api/shutdown", json={}).status_code == 401
            account = {"username": "admin", "password": "isolated test password"}
            assert client.post("/api/setup", json=account).status_code == 200
            login = client.post("/api/login", json=account)
            assert login.status_code == 200
            assert client.post("/api/shutdown", json={}).status_code == 403
            client.headers["X-CSRF-Token"] = login.json()["csrf"]
            response = client.post("/api/shutdown", json={})
            assert response.status_code == 200 and response.json()["stopping"]
        assert process.wait(timeout=15) == 0
        for port in (admin_port, gateway_port):
            with socket.socket() as connection:
                connection.settimeout(1)
                assert connection.connect_ex(("127.0.0.1", port)) != 0
        lock = acquire_runtime(tmp_path)
        lock.close()
        restored = Service(tmp_path)
        try:
            assert restored.settings() == cfg | {"gateway_port": gateway_port}
            assert restored.store.get("pending_gateway_port") is None
            assert restored.store.secret("cf_write_token") == "test-preserved-token"
            assert any(row["action"] == "platform_shutdown_requested" for row in restored.store.audit_list())
        finally:
            restored.store.db.close()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
def test_default_tunnel_name_migration_preserves_created_pending_and_custom_names(tmp_path):
    from lanbridge.service import Service
    cases = [
        ({"tunnel_name": "lanbridge-windows"}, None, None, "LanBridge"),
        ({"tunnel_name": "lanbridge-windows", "tunnel_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}, None, None, "lanbridge-windows"),
        ({"tunnel_name": "lanbridge-windows"}, {"name": "existing-create"}, None, "lanbridge-windows"),
        ({"tunnel_name": "lanbridge-windows"}, None, "a" * 32, "lanbridge-windows"),
        ({"tunnel_name": "LanBridge-家用电脑"}, None, None, "LanBridge-家用电脑"),
    ]
    for i, (settings, pending, owned, expected) in enumerate(cases):
        root = tmp_path / str(i)
        service = Service(root)
        assert service.settings()["tunnel_name"] == "LanBridge"
        service.store.set("settings", service.settings() | settings)
        service.store.set("pending_tunnel_create", pending)
        service.store.set("owned_tunnel", owned)
        service.store.db.close()
        reopened = Service(root)
        assert reopened.settings()["tunnel_name"] == expected
        reopened.store.db.close()


def test_concurrent_first_store_initialization_keeps_one_decryptable_vault_key(tmp_path, monkeypatch):
    import threading
    from cryptography.fernet import Fernet
    from lanbridge.store import Store

    original_generate = Fernet.generate_key
    both_generating = threading.Barrier(2)
    first_saved = threading.Event()
    errors, values = [], []
    def delayed_key():
        key = original_generate()
        both_generating.wait(timeout=10)
        if threading.current_thread().name == "second-vault-initializer":
            assert first_saved.wait(timeout=10)
        return key
    monkeypatch.setattr(Fernet, "generate_key", staticmethod(delayed_key))
    def initialize(first):
        store = None
        try:
            store = Store(tmp_path / "data")
            if first:
                store.set_secret("isolated_marker", "saved-before-second-initializer")
            values.append(store.secret("isolated_marker"))
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            if first:
                first_saved.set()
            if store:
                store.db.close()
    first = threading.Thread(target=initialize, args=(True,), name="first-vault-initializer")
    second = threading.Thread(target=initialize, args=(False,), name="second-vault-initializer")
    first.start(); second.start()
    first.join(timeout=15); second.join(timeout=15)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert values == ["saved-before-second-initializer"] * 2
    monkeypatch.setattr(Fernet, "generate_key", original_generate)
    reopened = Store(tmp_path / "data")
    try:
        assert reopened.secret("isolated_marker") == "saved-before-second-initializer"
        assert not list((tmp_path / "data").glob(".vault-*.tmp"))
    finally:
        reopened.db.close()


def test_vault_install_failure_cleans_temporary_key_and_can_retry(tmp_path, monkeypatch):
    import os
    from lanbridge.store import Store
    operation = "rename" if os.name == "nt" else "link"
    original = getattr(os, operation)
    def fail(*args, **kwargs):
        raise PermissionError("isolated install failure")
    monkeypatch.setattr(os, operation, fail)
    import pytest
    with pytest.raises(PermissionError):
        Store(tmp_path / "data")
    assert not (tmp_path / "data/vault.key").exists()
    assert not list((tmp_path / "data").glob(".vault-*.tmp"))
    monkeypatch.setattr(os, operation, original)
    store = Store(tmp_path / "data")
    try:
        store.set_secret("marker", "retry-success")
        assert store.secret("marker") == "retry-success"
    finally:
        store.db.close()


def test_existing_vault_key_is_not_regenerated(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from lanbridge.store import Store
    first = Store(tmp_path / "data")
    first.set_secret("marker", "keep-existing")
    key = (tmp_path / "data/vault.key").read_bytes()
    first.db.close()
    def fail():
        raise AssertionError("existing key must be reused")
    monkeypatch.setattr(Fernet, "generate_key", staticmethod(fail))
    second = Store(tmp_path / "data")
    try:
        assert second.secret("marker") == "keep-existing"
        assert (tmp_path / "data/vault.key").read_bytes() == key
    finally:
        second.db.close()


def test_concurrent_legacy_schema_upgrade_is_serialized(tmp_path, monkeypatch):
    import sqlite3
    import threading
    from lanbridge.store import Store
    root = tmp_path / "data"
    initial = Store(root)
    initial.set_secret("marker", "keep-during-upgrade")
    initial.db.execute("ALTER TABLE sessions DROP COLUMN access_token_id")
    initial.db.commit()
    initial.db.close()
    original_connect = sqlite3.connect
    first_read = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    errors = []
    class PausedConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            cursor = super().execute(sql, *args, **kwargs)
            if sql == "PRAGMA table_info(sessions)" and threading.current_thread().name == "first-schema-upgrade":
                rows = list(cursor)
                first_read.set()
                assert release_first.wait(5)
                return rows
            return cursor
    def connect(*args, **kwargs):
        kwargs["factory"] = PausedConnection
        return original_connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    def initialize(second=False):
        store = None
        try:
            store = Store(root)
            assert store.secret("marker") == "keep-during-upgrade"
        except Exception as exc:
            errors.append(str(exc))
        finally:
            if store:
                store.db.close()
            if second:
                second_done.set()
    first = threading.Thread(target=initialize, name="first-schema-upgrade")
    second = threading.Thread(target=initialize, args=(True,), name="second-schema-upgrade")
    first.start()
    assert first_read.wait(5)
    second.start()
    try:
        second_done.wait(.5)
    finally:
        release_first.set()
        first.join(10)
        second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    reopened = Store(root)
    try:
        columns = [row[1] for row in reopened.db.execute("PRAGMA table_info(sessions)")]
        assert columns.count("access_token_id") == 1
        assert reopened.secret("marker") == "keep-during-upgrade"
    finally:
        reopened.db.close()


def test_schema_upgrade_failure_rolls_back_and_releases_connection(tmp_path, monkeypatch):
    import sqlite3
    from lanbridge.store import Store
    root = tmp_path / "data"
    initial = Store(root)
    initial.set_secret("marker", "keep-after-failure")
    initial.db.execute("ALTER TABLE sessions DROP COLUMN access_token_id")
    initial.db.execute("ALTER TABLE temporary_tokens DROP COLUMN permissions")
    initial.db.commit()
    initial.db.close()
    original_connect = sqlite3.connect
    closed = []
    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith("ALTER TABLE temporary_tokens ADD COLUMN permissions"):
                raise sqlite3.OperationalError("isolated schema failure")
            return super().execute(sql, *args, **kwargs)
        def close(self):
            closed.append(True)
            super().close()
    def connect(*args, **kwargs):
        kwargs["factory"] = FailingConnection
        return original_connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="isolated schema failure"):
        Store(root)
    assert closed
    monkeypatch.setattr(sqlite3, "connect", original_connect)
    db = original_connect(root / "state.sqlite")
    try:
        assert "access_token_id" not in [row[1] for row in db.execute("PRAGMA table_info(sessions)")]
    finally:
        db.close()
    retried = Store(root)
    try:
        assert retried.secret("marker") == "keep-after-failure"
        assert "access_token_id" in [row[1] for row in retried.db.execute("PRAGMA table_info(sessions)")]
        assert "permissions" in [row[1] for row in retried.db.execute("PRAGMA table_info(temporary_tokens)")]
    finally:
        retried.db.close()


def test_concurrent_service_initialization_preserves_signing_key(tmp_path, monkeypatch):
    import threading
    from lanbridge.store import Store
    initial = Service(tmp_path / "data")
    initial.store.set_secret("signing_key", "")
    initial.store.db.close()
    original_secret = Store.secret
    first_read = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    seen, errors = {}, []
    def paused_secret(self, key):
        result = original_secret(self, key)
        if key == "signing_key" and threading.current_thread().name == "first-service-init" and not first_read.is_set():
            first_read.set()
            assert release_first.wait(5)
        return result
    monkeypatch.setattr(Store, "secret", paused_secret)
    def initialize(second=False):
        service = None
        try:
            service = Service(tmp_path / "data")
            seen["second" if second else "first"] = original_secret(service.store, "signing_key")
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            if service:
                service.store.db.close()
            if second:
                second_done.set()
    first = threading.Thread(target=initialize, name="first-service-init")
    second = threading.Thread(target=initialize, args=(True,), name="second-service-init")
    first.start()
    assert first_read.wait(5)
    second.start()
    try:
        second_done.wait(.5)
    finally:
        release_first.set()
        first.join(10)
        second.join(10)
    assert not errors and not first.is_alive() and not second.is_alive()
    assert seen["first"] and seen["first"] == seen["second"]
    reopened = Service(tmp_path / "data")
    try:
        assert original_secret(reopened.store, "signing_key") == seen["second"]
    finally:
        reopened.store.db.close()
