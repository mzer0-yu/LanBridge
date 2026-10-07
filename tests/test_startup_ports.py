import socket

import pytest

from run import serve
from lanbridge.gateway_runtime import GatewayRuntime
from test_security import service


@pytest.mark.parametrize('port', [80, 65536, 8890])
def test_invalid_gateway_selection_preserves_configuration(service, port):
    before = service.settings()
    with pytest.raises(ValueError):
        GatewayRuntime(service).start(port)
    assert service.settings() == before


def test_startup_selection_rejects_forwarding_loop(service):
    service.store.set('sites', [{'id': 'one', 'origin': 'http://192.168.1.10:19001', 'hostname': 'one.example.com', 'enabled': True}])
    result = GatewayRuntime(service).start(19001)
    assert not result['running'] and '转发循环' in result['error']


def test_occupied_admin_port_still_blocks_startup(service):
    before = service.settings()
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        with pytest.raises(ValueError, match='管理台端口'):
            serve(service, admin_port=occupied.getsockname()[1])
    assert service.settings() == before


def test_failed_gateway_keeps_config_and_can_recover_without_admin_restart(service):
    from lanbridge.gateway_runtime import GatewayRuntime
    before = service.settings()
    runtime = GatewayRuntime(service)
    try:
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            blocked = occupied.getsockname()[1]
            result = runtime.start(blocked)
            assert not result['running'] and str(blocked) in result['error']
            assert service.settings() == before
        result = runtime.start(blocked)
        assert result['running']
        assert service.settings()['gateway_port'] == blocked
        assert runtime.start()['running']
    finally:
        runtime.stop()
