import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from lanbridge.service import Service
from run import acquire_runtime


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_authenticated_shutdown_exits_servers_and_releases_runtime_lock(tmp_path):
    service = Service(tmp_path)
    admin_port, gateway_port = unused_port(), unused_port()
    while admin_port == gateway_port:
        gateway_port = unused_port()
    cfg = service.settings() | {"admin_port": admin_port, "gateway_port": gateway_port}
    service.store.set("settings", cfg)
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
            assert restored.settings() == cfg
            assert restored.store.secret("cf_write_token") == "test-preserved-token"
            assert any(row["action"] == "platform_shutdown_requested" for row in restored.store.audit_list())
        finally:
            restored.store.db.close()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
