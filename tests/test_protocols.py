import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from lanbridge.gateway import create_gateway, PASS_COOKIE, signed_pass
from test_proxy import origin
from test_security import service, admin_client


def test_protocol_selection_enforced_and_legacy_updates_preserve_selection(service, origin):
    site = service.save_site({'name': 'Protocols', 'hostname': 'app.example.com',
                              'origin': f'http://127.0.0.1:{origin}', 'human_check': False})
    client = TestClient(create_gateway(service), base_url='https://app.example.com')
    assert site['protocols'] == ['http', 'websocket']
    assert client.get('/').status_code == 200
    site = service.save_site(site | {'protocols': ['websocket']})
    for method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'):
        assert client.request(method, '/').status_code == 403
    legacy = site.copy()
    legacy.pop('protocols')
    assert service.save_site(legacy)['protocols'] == ['websocket']
    service.save_site(site | {'protocols': ['http']})
    assert client.get('/').status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('wss://app.example.com/ws'):
            pass
    # Existing stored rows without the new field keep their previous behavior.
    service.store.set('sites', [legacy])
    assert service.sites()[0]['protocols'] == ['http', 'websocket']
    assert client.get('/').status_code == 200


def test_protocol_validation_does_not_change_saved_site(service):
    site = service.save_site({'name': 'Protocols', 'hostname': 'app.example.com',
                              'origin': 'http://127.0.0.1:8765', 'human_check': False})
    admin = admin_client(service)
    for invalid in ([], ['tcp'], ['http', 'udp'], 'http', None):
        assert admin.post('/api/sites', json=site | {'protocols': invalid}).status_code == 400
        assert service.sites()[0] == site


def test_websocket_only_retains_verification_without_http_forwarding(service, origin):
    site = service.save_site({'name': 'Protected socket', 'hostname': 'app.example.com',
                              'origin': f'http://127.0.0.1:{origin}', 'protocols': ['websocket'],
                              'human_check': False, 'passcode_required': True,
                              'passcode': 'visitor long password'})
    client = TestClient(create_gateway(service), base_url='https://app.example.com')
    assert client.get('/', headers={'Accept': 'text/html'}).status_code == 200
    assert client.get('/api').status_code == 401
    # Verification endpoint remains reachable (invalid submission is rejected normally).
    assert client.post('/.lanbridge/verify', headers={'Origin': 'https://app.example.com'},
                       data={'passcode': 'incorrect'}).status_code != 403
    client.cookies.set(PASS_COOKIE, signed_pass(service, site, 'testclient'))
    assert client.get('/').status_code == 403


@pytest.mark.parametrize("old_protocols", [["websocket"], ["http", "websocket"]])
def test_switch_to_lanbridge_without_protocols_keeps_management_http(service, old_protocols):
    admin_client(service)
    site = service.save_site({'name': 'Switch target', 'hostname': 'app.example.com',
                              'origin': 'http://127.0.0.1:9300', 'human_check': False,
                              'protocols': old_protocols})
    edit = site | {'target': 'lanbridge'}
    edit.pop('protocols')
    updated = service.save_site(edit)
    assert updated['protocols'] == ['http']
    with TestClient(create_gateway(service), base_url='https://app.example.com') as client:
        assert client.get('/client').status_code == 200
