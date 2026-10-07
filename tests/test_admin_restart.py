import json
from contextlib import closing
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from lanbridge.admin import create_admin
from lanbridge.service import Service
from run import acquire_runtime
from test_security import service, admin_client


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_admin_port_schedule_is_deferred_atomic_and_cancellable(service):
    client = admin_client(service)
    old = service.settings()["admin_port"]
    port = free_port()
    response = client.post("/api/admin-port", json={"port": port})
    assert response.status_code == 200
    assert service.settings()["admin_port"] == old
    assert client.get("/api/state").json()["pending_admin_port"] == port
    for invalid in (True, "9900", 80, 65536, service.settings()["gateway_port"]):
        assert client.post("/api/admin-port", json={"port": invalid}).status_code == 400
        assert service.store.get("pending_admin_port") == port
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        assert client.post("/api/admin-port", json={"port": occupied.getsockname()[1]}).status_code == 400
    assert client.post("/api/admin-port", json={"port": old}).json()["pending_admin_port"] is None
    gateway = free_port()
    service.queue_gateway_port(gateway)
    with pytest.raises(ValueError):
        service.queue_admin_port(gateway)
    service.queue_gateway_port(service.settings()["gateway_port"])
    service.queue_admin_port(port)
    with pytest.raises(ValueError):
        service.queue_gateway_port(port)


def test_restart_requires_authenticated_csrf_and_supported_runtime(service):
    calls = []
    client = TestClient(create_admin(service, shutdown=lambda: calls.append("stop"), restart=lambda: {"port":8890}), base_url="http://127.0.0.1:8890", headers={"Origin":"http://127.0.0.1:8890"})
    assert client.post("/api/restart", json={}).status_code == 401
    client.post("/api/setup",json={"username":"admin","password":"isolated test password"})
    login = client.post("/api/login",json={"username":"admin","password":"isolated test password"})
    assert client.post("/api/restart",json={}).status_code == 403
    client.headers["X-CSRF-Token"] = login.json()["csrf"]
    assert client.post("/api/restart",json={}).status_code == 200
    assert calls == ["stop"]
    token = service.store.issue_temporary_token("isolated", 1, ["sites"])
    limited=TestClient(create_admin(service),base_url="http://127.0.0.1:8890",headers={"Authorization":"Bearer "+token["token"]})
    assert limited.post("/api/admin-port",json={"port":free_port()}).status_code == 403
    assert limited.post("/api/restart",json={}).status_code == 403


def test_real_restart_switches_process_and_port_preserving_login(tmp_path):
    import os
    project = Path(__file__).resolve().parents[1]
    admin, gateway, selected = free_port(), free_port(), free_port()
    assert len({admin,gateway,selected}) == 3
    data = tmp_path / "data"
    service = Service(data)
    service.store.set("settings",service.settings() | {"admin_port":admin,"gateway_port":gateway})
    service.store.db.close()
    # Browser is mocked only in these isolated child interpreters.
    mock = tmp_path / "mock-browser"
    mock.mkdir()
    (mock / "webbrowser.py").write_text("def open(*args, **kwargs): return True\n",encoding="utf-8")
    env=os.environ.copy()
    env["PYTHONPATH"]=str(mock)
    process=subprocess.Popen([sys.executable,str(project/"run.py"),"--data-dir",str(data),"serve"],cwd=project,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def wait_client(port):
        url=f"http://127.0.0.1:{port}"
        client=httpx.Client(base_url=url,headers={"Origin":url},trust_env=False,timeout=2)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                if client.get("/api/bootstrap").status_code==200: return client
            except httpx.TransportError: pass
            time.sleep(0.1)
        client.close()
        raise AssertionError("isolated admin did not start")
    new_client=None
    try:
        with closing(wait_client(admin)) as client:
            credentials={"username":"admin","password":"isolated restart password"}
            assert client.post("/api/setup",json=credentials).status_code==200
            login=client.post("/api/login",json=credentials).json()
            client.headers["X-CSRF-Token"]=login["csrf"]
            assert client.post("/api/admin-port",json={"port":selected}).status_code==200
            # Port becoming occupied between save and restart must leave old service alive.
            with socket.socket() as blocked:
                blocked.bind(("127.0.0.1",selected))
                assert client.post("/api/restart",json={}).status_code==400
                assert client.get("/api/state").status_code==200
            cookies=client.cookies
            old_marker=client.get("/api/bootstrap").json()["instance"]
            result=client.post("/api/restart",json={})
            assert result.status_code==200 and result.json()["port"]==selected
        assert process.wait(timeout=20)==0
        new_client=wait_client(selected)
        new_client.cookies.update(cookies)
        new_client.headers["X-CSRF-Token"]=login["csrf"]
        state=new_client.get("/api/state").json()
        assert state["settings"]["admin_port"]==selected and state["pending_admin_port"] is None
        assert new_client.get("/api/bootstrap").json()["instance"]!=old_marker
        with socket.socket() as released:
            assert released.connect_ex(("127.0.0.1",admin)) != 0
        assert new_client.post("/api/shutdown",json={}).status_code==200
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            try:
                lock=acquire_runtime(data)
                lock.close()
                break
            except ValueError: time.sleep(0.1)
        else: raise AssertionError("restarted process failed to release runtime lock")
    finally:
        if new_client:
            try: new_client.post("/api/shutdown",json={})
            except httpx.HTTPError: pass
            new_client.close()
        if process.poll() is None:
            process.terminate();process.wait(timeout=10)


def test_startup_fallback_keeps_pending_port_and_old_admin_available(tmp_path):
    project=Path(__file__).resolve().parents[1]
    service=Service(tmp_path)
    admin,gateway=free_port(),free_port()
    service.store.set("settings",service.settings() | {"admin_port":admin,"gateway_port":gateway})
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1",0))
        selected=occupied.getsockname()[1]
        service.store.set("pending_admin_port",selected)
        service.store.db.close()
        result=tmp_path/"ready.json"
        process=subprocess.Popen([sys.executable,str(project/"run.py"),"--data-dir",str(tmp_path),"serve","--admin-port",str(selected),"--fallback-admin-port",str(admin),"--startup-result",str(result)],cwd=project,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+15
            while not result.exists():
                assert process.poll() is None and time.monotonic()<deadline
                time.sleep(.1)
            assert json.loads(result.read_text(encoding="utf-8"))["port"]==admin
            url=f"http://127.0.0.1:{admin}"
            with httpx.Client(base_url=url,headers={"Origin":url},trust_env=False) as client:
                credentials={"username":"admin","password":"isolated fallback password"}
                client.post("/api/setup",json=credentials)
                login=client.post("/api/login",json=credentials).json()
                client.headers["X-CSRF-Token"]=login["csrf"]
                state=client.get("/api/state").json()
                assert state["pending_admin_port"]==selected
                assert state["settings"]["admin_port"]==admin
                assert "恢复使用" in state["restart_warning"]
                assert client.post("/api/shutdown",json={}).status_code==200
            assert process.wait(timeout=15)==0
        finally:
            if process.poll() is None: process.terminate();process.wait(timeout=10)


def test_restart_plan_preserves_running_connector_and_resume_calls_start(service, monkeypatch):
    import uvicorn
    from run import serve
    from lanbridge.gateway_runtime import GatewayRuntime
    calls=[]
    service.store.set("settings", service.settings() | {"tunnel_id":"12345678-1234-1234-1234-123456789abc"})
    service.store.set_secret("tunnel_token", "test-only-tunnel-token")
    monkeypatch.setattr(service.connector,"status",lambda:{"running":True,"installed":True})
    monkeypatch.setattr(service.connector,"start",lambda:calls.append("resume"))
    monkeypatch.setattr(service.connector,"stop",lambda:calls.append("stop"))
    monkeypatch.setattr(GatewayRuntime,"start",lambda *args:{"error":"","running":True})
    monkeypatch.setattr(GatewayRuntime,"status",lambda *args:{"running":True})
    monkeypatch.setattr(GatewayRuntime,"stop",lambda *args:None)
    def run(server, sockets):
        cfg=service.settings()
        url=f"http://127.0.0.1:{cfg['admin_port']}"
        with TestClient(server.config.app,base_url=url,headers={"Origin":url}) as client:
            credentials={"username":"admin","password":"isolated connector resume"}
            client.post("/api/setup",json=credentials)
            login=client.post("/api/login",json=credentials).json()
            client.headers["X-CSRF-Token"]=login["csrf"]
            assert client.post("/api/restart",json={}).status_code==200
            assert server.should_exit
    monkeypatch.setattr(uvicorn.Server,"run",run)
    serve(service,admin_port=free_port(),resume_connector=True)
    assert service.restart_plan["resume_connector"]
    assert calls==["resume","stop"]
