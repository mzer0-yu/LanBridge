from types import SimpleNamespace
import pytest
from test_security import service, admin_client
from lanbridge.service import Service


def test_auto_start_setting_persists_and_requires_admin_csrf(service):
    client = admin_client(service)
    assert client.get('/api/state').json()['connector_auto_start'] is True
    assert client.post('/api/connector-auto-start', json={'enabled':False}, headers={'X-CSRF-Token':'bad'}).status_code == 403
    for invalid in (None, 1, 'false', [], {}):
        assert client.post('/api/connector-auto-start', json={'enabled':invalid}).status_code == 400
    assert client.post('/api/connector-auto-start', json={'enabled':False}).json()['saved']
    reopened = Service(service.store.root)
    try:
        assert reopened.store.get('connector_auto_start') is False
    finally:
        reopened.store.db.close()
    grant = service.store.issue_temporary_token('test', 1, ['account','sites'])
    assert client.post('/api/connector-auto-start', json={'enabled':True}, headers={'Authorization':'Bearer '+grant['token']}).status_code == 403


@pytest.mark.parametrize('enabled,resume,ready,gateway,installed,fails,expected', [
    (True,None,True,True,True,False,1),
    (False,None,True,True,True,False,0),
    (True,False,True,True,True,False,0),
    (False,True,True,True,True,False,1),
    (True,None,False,True,True,False,0),
    (True,None,True,False,True,False,0),
    (True,None,True,True,False,False,0),
    (True,None,True,True,True,True,1),
])
def test_launch_is_once_and_respects_readiness_and_restart_state(service, monkeypatch, enabled,resume,ready,gateway,installed,fails,expected):
    service.store.set('connector_auto_start',enabled)
    if ready:
        service.store.set('settings',service.settings() | {'tunnel_id':'12345678-1234-1234-1234-123456789abc'})
        service.store.set_secret('tunnel_token','test-only-tunnel-token')
    service.gateway_runtime=SimpleNamespace(status=lambda:{'running':gateway})
    monkeypatch.setattr(service.connector,'status',lambda:{'installed':installed,'running':False})
    calls=[]
    def start():
        calls.append('start')
        if fails: raise OSError('do not expose internal command')
    monkeypatch.setattr(service.connector,'start',start)
    service.start_connector_on_launch(resume)
    assert len(calls)==expected
    assert bool(service.connector_startup_warning)==((enabled if resume is None else resume) and (not ready or not gateway or not installed or fails))
    assert 'do not expose' not in (service.connector_startup_warning or '')


def test_auto_connect_runs_after_admin_ready_and_finishes_before_cleanup(service, monkeypatch):
    import socket
    import threading
    import uvicorn
    from fastapi.testclient import TestClient
    from lanbridge.gateway_runtime import GatewayRuntime
    from run import serve
    started, release = threading.Event(), threading.Event()
    calls=[]
    service.store.set('settings', service.settings() | {'tunnel_id':'12345678-1234-1234-1234-123456789abc'})
    service.store.set_secret('tunnel_token','test-only-tunnel-token')
    monkeypatch.setattr(GatewayRuntime,'start',lambda *args:{'running':True,'error':''})
    monkeypatch.setattr(GatewayRuntime,'status',lambda *args:{'running':True})
    monkeypatch.setattr(GatewayRuntime,'stop',lambda *args:None)
    monkeypatch.setattr(service.connector,'status',lambda:{'installed':True,'running':False})
    def start():
        started.set()
        assert release.wait(3)
        calls.append('start')
        raise OSError('isolated startup failure')
    monkeypatch.setattr(service.connector,'start',start)
    monkeypatch.setattr(service.connector,'stop',lambda:calls.append('stop'))
    def run(server,sockets):
        server.started=True
        assert started.wait(3)
        url='http://127.0.0.1:'+str(service.settings()['admin_port'])
        try:
            with TestClient(server.config.app,base_url=url) as client:
                assert client.get('/api/bootstrap').status_code==200
        finally:
            release.set()
    monkeypatch.setattr(uvicorn.Server,'run',run)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        port=probe.getsockname()[1]
    serve(service,admin_port=port)
    assert calls==['start','stop']
    assert '自动连接失败' in service.connector_startup_warning
