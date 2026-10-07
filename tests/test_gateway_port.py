import socket

from test_security import service, admin_client
from test_cloudflare import prepare


def test_gateway_port_saved_without_changing_live_settings(service):
    client = admin_client(service)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    old = service.settings()['gateway_port']
    response = client.post('/api/gateway-port', json={'port': port})
    assert response.status_code == 200
    assert response.json()['restart_required']
    assert service.settings()['gateway_port'] == old
    assert client.get('/api/state').json()['pending_gateway_port'] == port
    assert client.post('/api/gateway-port', json={'port': old}).status_code == 200
    assert service.store.get('pending_gateway_port') is None


def test_invalid_or_occupied_port_does_not_change_pending(service):
    client = admin_client(service)
    for port in [80, 65536, True, '9000', service.settings()['admin_port']]:
        assert client.post('/api/gateway-port', json={'port': port}).status_code == 400
        assert service.store.get('pending_gateway_port') is None
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        assert client.post('/api/gateway-port', json={'port': listener.getsockname()[1]}).status_code == 400
    service.store.set('sites', [{'id':'one','origin':'http://127.0.0.1:9001','hostname':'one.example.com','zone_id':'','enabled':True}])
    assert client.post('/api/gateway-port', json={'port':9001}).status_code == 400


def test_port_migration_updates_owned_routes_and_clears_previous_port(service, monkeypatch):
    remote, _, _, site = prepare(service, monkeypatch)
    service.cf.apply(service.cf.plan()['revision'])
    cfg = service.settings()
    service.store.set('previous_gateway_port', cfg['gateway_port'])
    cfg['gateway_port'] = 9002
    service.store.set('settings', cfg)
    service.cf.apply(service.cf.plan()['revision'])
    rule = next(r for r in remote['config']['ingress'] if r.get('hostname') == site['hostname'])
    assert rule['service'] == 'http://127.0.0.1:9002'
    assert service.store.get('previous_gateway_port') is None
