import socket

import pytest

from run import serve, startup_settings
from test_security import service


def test_startup_selection_uses_pending_port_without_writing(service):
    before = service.settings()
    service.store.set('pending_gateway_port', 19001)
    assert startup_settings(service)['gateway_port'] == 19001
    assert startup_settings(service, 19002, 19003)['admin_port'] == 19002
    assert service.settings() == before
    assert service.store.get('pending_gateway_port') == 19001


@pytest.mark.parametrize('admin,gateway', [(80, 19001), (19000, 65536), (19000, 19000)])
def test_invalid_selection_preserves_configuration(service, admin, gateway):
    before = service.settings()
    with pytest.raises(ValueError):
        startup_settings(service, admin, gateway)
    assert service.settings() == before


def test_startup_selection_rejects_forwarding_loop(service):
    service.store.set('sites', [{'id': 'one', 'origin': 'http://192.168.1.10:19001', 'hostname': 'one.example.com', 'enabled': True}])
    with pytest.raises(ValueError, match='转发循环'):
        startup_settings(service, 19000, 19001)


@pytest.mark.parametrize('occupied_role', ['admin', 'gateway'])
def test_occupied_port_does_not_save_either_selection(service, occupied_role):
    before = service.settings()
    service.store.set('pending_gateway_port', 19003)
    with socket.socket() as occupied, socket.socket() as free:
        occupied.bind(('127.0.0.1', 0))
        free.bind(('127.0.0.1', 0))
        blocked, available = occupied.getsockname()[1], free.getsockname()[1]
        free.close()
        admin, gateway = (blocked, available) if occupied_role == 'admin' else (available, blocked)
        with pytest.raises(ValueError, match=str(blocked)):
            serve(service, admin_port=admin, gateway_port=gateway)
        # A failed gateway bind must also release the already-bound admin socket.
        with socket.socket() as check:
            check.bind(('127.0.0.1', available))
    assert service.settings() == before
    assert service.store.get('pending_gateway_port') == 19003


def test_successful_selection_commits_routes_and_migration_together(service, monkeypatch):
    import uvicorn

    class Server:
        should_exit = False

        def __init__(self, config):
            self.config = config

        def run(self, sockets):
            assert all(sock.fileno() >= 0 for sock in sockets)

    monkeypatch.setattr(uvicorn, 'Server', Server)
    before = service.settings() | {'tunnel_id': '11111111-1111-1111-1111-111111111111'}
    service.store.set('settings', before)
    service.store.set('sites', [{'id': 'admin', 'target': 'lanbridge', 'hostname': 'admin.example.com', 'origin': f'http://127.0.0.1:{before["admin_port"]}'}])
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0))
        second.bind(('127.0.0.1', 0))
        admin, gateway = first.getsockname()[1], second.getsockname()[1]
    serve(service, admin_port=admin, gateway_port=gateway)
    assert service.settings() == before | {'admin_port': admin, 'gateway_port': gateway}
    assert service.store.get('sites')[0]['origin'] == f'http://127.0.0.1:{admin}'
    assert service.store.get('previous_gateway_port') == before['gateway_port']
    assert service.store.get('pending_gateway_port') is None
    assert '同步云端路由' in service.store.get('publication_error')
