import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from fastapi.testclient import TestClient
from lanbridge.gateway import create_gateway
from lanbridge.models import Site
from test_security import service, admin_client


@pytest.mark.parametrize('mode,expected', [('origin', 421), ('public', 200)])
def test_strict_source_host_routes_to_pinned_origin_and_filters_spoofed_headers(service, mode, expected):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            accepted = self.headers.get('Host') == 'app.example.com' and self.headers.get('X-Forwarded-Proto') == 'https'
            payload = json.dumps({'host': self.headers.get('Host'), 'proto': self.headers.get('X-Forwarded-Proto'),
                                  'forwarded': self.headers.get('X-Forwarded-Host'), 'cf': self.headers.get('CF-Connecting-IP')}).encode()
            self.send_response(200 if accepted else 421)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Content-Type', 'application/json');self.end_headers();self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    try:
        site = service.save_site({'name': 'Host policy', 'hostname': 'app.example.com', 'origin': f'http://127.0.0.1:{server.server_port}',
                           'human_check': False, 'origin_host_header': mode})
        with TestClient(create_gateway(service), base_url='https://app.example.com') as client:
            response = client.get('/', headers={'X-Forwarded-Host': 'evil.example', 'X-Forwarded-Proto': 'https', 'CF-Connecting-IP': 'not-an-ip'})
        assert response.status_code == expected
        assert response.json()['host'] == ('app.example.com' if mode == 'public' else f'127.0.0.1:{server.server_port}')
        assert response.json()['forwarded'] == 'app.example.com'
        assert response.json()['proto'] == 'https' and response.json()['cf'] is None
        owner = admin_client(service)
        probe = owner.post('/api/sites/' + site['id'] + '/probe').json()
        assert probe['http_status'] == expected and probe['reachable']
    finally:
        server.shutdown();thread.join(3);server.server_close()


@pytest.mark.parametrize('mode', ['origin', 'public'])
def test_websocket_host_mode_uses_one_host_and_pinned_connection(service, mode):
    from websockets.asyncio.server import serve
    ready, done, ports = threading.Event(), threading.Event(), []
    async def echo(ws):
        await ws.send(json.dumps({'hosts': ws.request.headers.get_all('Host'), 'proto': ws.request.headers.get('X-Forwarded-Proto')}))
        async for message in ws: await ws.send(message)
    async def run():
        async with serve(echo, '127.0.0.1', 0) as server:
            ports.append(server.sockets[0].getsockname()[1]);ready.set()
            while not done.is_set():await asyncio.sleep(.02)
    thread = threading.Thread(target=lambda: asyncio.run(run()), daemon=True);thread.start();assert ready.wait(5)
    try:
        service.save_site({'name': 'Socket', 'hostname': 'app.example.com', 'origin': f'http://127.0.0.1:{ports[0]}',
                           'human_check': False, 'origin_host_header': mode})
        with TestClient(create_gateway(service), base_url='https://app.example.com') as client:
            with client.websocket_connect('wss://app.example.com/ws', headers={'Origin':'https://app.example.com'}) as ws:
                headers = json.loads(ws.receive_text())
                assert headers['hosts'] == ['app.example.com' if mode == 'public' else f'127.0.0.1:{ports[0]}']
                assert headers['proto'] == 'https'
                ws.send_text('hello');assert ws.receive_text() == 'hello'
    finally:
        done.set();thread.join(5)


def test_host_mode_persists_on_partial_edit_and_rejects_arbitrary_host(service):
    site = service.save_site({'name': 'Host policy', 'hostname': 'app.example.com', 'origin': 'http://127.0.0.1:9300',
                              'human_check': False, 'origin_host_header': 'public'})
    edit = dict(site);edit.pop('origin_host_header');edit['name'] = 'Renamed'
    updated = service.save_site(edit)
    assert updated['origin_host_header'] == 'public' and updated['policy_version'] == site['policy_version']
    owner = admin_client(service)
    assert owner.post('/api/sites', json=updated | {'origin_host_header':'evil.example'}).status_code == 400
    assert service.sites()[0] == updated
    assert Site(name='Static',hostname='static.example.com',origin='file:///C:/Web/index.html',origin_host_header='public').origin_host_header == 'origin'
