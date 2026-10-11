from pathlib import Path
from tempfile import TemporaryDirectory
import pytest
from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from lanbridge.gateway import create_gateway
from lanbridge.static_site import homepage_path, validate_homepage
from test_security import service, admin_client


@pytest.fixture
def web():
    temporary = TemporaryDirectory(prefix='lanbridge-static-test-')
    root = Path(temporary.name) / 'Public web'
    root.mkdir()
    home = root / 'home.html'
    home.write_text('<html><link rel="stylesheet" href="site.css">Hello</html>', encoding='utf-8')
    (root / 'site.css').write_text('body{color:green}', encoding='utf-8')
    (root / 'image.png').write_bytes(b'fake image')
    (root / 'nested').mkdir()
    (root / 'nested/index.html').write_text('<html>Nested</html>', encoding='utf-8')
    (root / 'empty').mkdir()
    (root / '.env').write_text('SECRET_NOT_PUBLIC', encoding='utf-8')
    (root / 'private.key').write_text('PRIVATE_KEY_NOT_PUBLIC', encoding='utf-8')
    (root / 'server.py').write_text('SOURCE_NOT_PUBLIC', encoding='utf-8')
    try:
        yield home
    finally:
        temporary.cleanup()


def body(home, **extra):
    return dict(name='Static website', hostname='static.example.com', origin=home.as_uri(), human_check=False) | extra


def test_static_pages_assets_nested_head_range_cache_and_metrics(service, web):
    site = service.save_site(body(web))
    assert site['protocols'] == ['http'] and site['target'] == 'website'
    service.validate_site(site)
    with TestClient(create_gateway(service), base_url='https://static.example.com') as client:
        response = client.get('/', headers={'Accept':'text/html'})
        assert response.status_code == 200 and 'Hello' in response.text
        assert response.headers['content-type'].startswith('text/html')
        assert 'private' in response.headers['cache-control']
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert client.get('/site.css').text == 'body{color:green}'
        assert client.get('/image.png').content == b'fake image'
        assert client.head('/').content == b''
        assert client.get('/', headers={'Range':'bytes=0-5'}).content == b'<html>'
        unchanged = client.get('/', headers={'If-None-Match':response.headers['etag'], 'Accept':'text/html'})
        assert unchanged.status_code == 304
        nested = client.get('/nested', follow_redirects=False)
        assert nested.status_code == 307 and nested.headers['location'].endswith('/nested/')
        assert 'Nested' in client.get('/nested/').text
        assert client.get('/empty/').status_code == 404
        assert client.post('/').status_code == 405
        assert client.get('/missing.html').status_code == 404
        row = service.visitor_risk.snapshot([site['id']])['sites'][site['id']]
        assert row['page_views'] == 4  # root, partial HTML, HTML 304, nested HTML
        web.write_text('<html>Changed content</html>', encoding='utf-8')
        assert 'Changed content' in client.get('/').text
        assert client.get('/', headers={'If-None-Match':response.headers['etag']}).status_code == 200
        web.unlink()
        missing = client.get('/')
        assert missing.status_code == 503 and str(web) not in missing.text


@pytest.mark.parametrize('path',['/.env','/private.key','/server.py','/%2e%2e/outside.html',
                                 '/nested/%2e%2e/%2e%2e/outside.html','/image.png:secret','/nested%5cindex.html'])
def test_static_request_boundaries(service, web, path):
    (web.parent.parent / 'outside.html').write_text('OUTSIDE_SECRET', encoding='utf-8')
    service.save_site(body(web))
    with TestClient(create_gateway(service), base_url='https://static.example.com') as client:
        result = client.get(path)
        assert result.status_code in {404,403}
        assert all(secret not in result.text for secret in ['OUTSIDE_SECRET','SECRET_NOT_PUBLIC','PRIVATE_KEY_NOT_PUBLIC','SOURCE_NOT_PUBLIC'])


@pytest.mark.parametrize('origin',['file://server/share/index.html','file:///C:/x/index.html?x=1',
                                   'file:///C:/x/index.html#anchor','file:///C:/x/%00.html',
                                   'file:///C:/x/%ZZ.html','file:///C:/x/file.txt',
                                   'file:relative/index.html','file:///C:/x/../index.html'])
def test_invalid_file_urls_are_rejected(origin):
    with pytest.raises(ValueError):
        homepage_path(origin)


def test_static_selected_private_directory_is_rejected(service, web):
    with pytest.raises(ValueError):
        service.save_site(body(web.parent.parent / 'index.html'))
    private = service.store.root / 'index.html'
    private.write_text('private', encoding='utf-8')
    with pytest.raises(ValueError):
        service.save_site(body(private))
    assert service.sites() == []


def test_static_links_cannot_expose_other_files(service, web):
    outside = web.parent.parent / 'outside.html'
    outside.write_text('OUTSIDE_SECRET', encoding='utf-8')
    link = web.parent / 'linked.html'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('OS does not grant symbolic-link creation')
    service.save_site(body(web))
    with TestClient(create_gateway(service), base_url='https://static.example.com') as client:
        assert client.get('/linked.html').status_code == 404
    with pytest.raises(ValueError):
        validate_homepage(link.as_uri(), (service.store.root,))


def test_static_pause_gate_and_websocket_boundaries(service, web):
    site = service.save_site(body(web, passcode_required=True, passcode='long visitor passcode'))
    with TestClient(create_gateway(service), base_url='https://static.example.com') as client:
        assert 'Hello' not in client.get('/').text
        verified = client.post('/.lanbridge/verify', json={'passcode':'long visitor passcode'}, headers={'Origin':'https://static.example.com'})
        assert verified.status_code == 200
        assert 'Hello' in client.get('/').text
        service.set_site_paused(site['id'], True)
        paused = client.get('/', headers={'Accept':'text/html'})
        assert paused.status_code == 503 and 'Hello' not in paused.text
    service.set_site_paused(site['id'], False)
    from starlette.websockets import WebSocketDisconnect
    with TestClient(create_gateway(service), base_url='https://static.example.com') as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/'):
                pass


@pytest.mark.parametrize('background',[False, True])
def test_static_target_requires_local_full_admin(service, web, background):
    owner = admin_client(service)
    service.save_site(dict(name='Remote', hostname='lb.example.com', target='lanbridge', human_check=False))
    grant = owner.post('/api/temporary-tokens', json={'name':'limited'}).json()
    limited = TestClient(create_admin(service), base_url='http://127.0.0.1:8890', headers={'Authorization':'Bearer '+grant['token']})
    with TestClient(create_admin(service, remote=True), base_url='https://lb.example.com', headers={'Origin':'https://lb.example.com'}) as remote:
        login = remote.post('/api/login', json={'username':'admin','password':'correct horse battery'})
        remote.headers['X-CSRF-Token'] = login.json()['csrf']
        for client in [limited, remote]:
            result = client.post('/api/sites', json=body(web, background=background))
            assert result.status_code == 400 and '本机完整管理员' in result.json()['detail']
        assert len(service.sites()) == 1
        site = service.save_site(body(web))
        service.store.set('published_hosts', [s['hostname'] for s in service.sites()])
        assert remote.post('/api/sites', json=site | {'name':'Renamed'}).status_code == 200
        routes = remote.get('/api/client/routes').json()['routes']
        route = next(row for row in routes if row['hostname'] == site['hostname'])
        assert route['static'] and route['origin'] == '本机静态网页' and str(web) not in str(route)


def test_static_local_save_and_probe(service, web, monkeypatch):
    monkeypatch.setattr(service.cf, 'plan', lambda: {'revision':'fixture'})
    monkeypatch.setattr(service.cf, 'apply', lambda revision: None)
    owner = admin_client(service)
    saved = owner.post('/api/sites', json=body(web))
    assert saved.status_code == 200, saved.text
    site = saved.json()
    assert site['origin'] == web.as_uri()
    probe = owner.post('/api/sites/'+site['id']+'/probe')
    assert probe.json()['reachable']
    web.unlink()
    assert not owner.post('/api/sites/'+site['id']+'/probe').json()['reachable']


def test_static_configuration_survives_reload(service, web):
    from lanbridge.service import Service
    saved = service.save_site(body(web))
    reloaded = Service(service.store.root)
    assert reloaded.sites()[0]['origin'] == web.as_uri()
    with TestClient(create_gateway(reloaded), base_url='https://static.example.com') as client:
        response = client.get('/')
        assert response.status_code == 200 and 'Hello' in response.text
    assert reloaded.sites()[0]['id'] == saved['id']


def test_static_conversion_without_protocols_drops_legacy_websocket(service, web):
    saved = service.save_site(dict(name='HTTP',hostname='static.example.com',origin='http://127.0.0.1:9300',human_check=False))
    changed = dict(saved, origin=web.as_uri())
    changed.pop('protocols')
    converted = service.save_site(changed)
    assert converted['protocols'] == ['http']
    assert service.sites()[0]['protocols'] == ['http']


def test_missing_static_homepage_can_be_disabled_but_not_reenabled(service, web):
    saved = service.save_site(body(web))
    web.unlink()
    disabled = service.save_site(saved | {'enabled':False})
    assert not disabled['enabled']
    assert service.save_site(disabled | {'name':'Disabled renamed'})['name'] == 'Disabled renamed'
    with pytest.raises(ValueError, match='不存在或无法读取'):
        service.save_site(disabled | {'enabled':True})
    assert not service.sites()[0]['enabled']


def test_static_rate_limit_rejects_before_reading_files(service, web, monkeypatch):
    import lanbridge.static_site as static
    site = service.save_site(body(web, requests_per_minute=10))
    calls = []
    validate = static.validate_homepage
    def observed(*args):
        calls.append(args[0])
        return validate(*args)
    monkeypatch.setattr(static, 'validate_homepage', observed)
    with TestClient(create_gateway(service), base_url='https://static.example.com') as client:
        for _ in range(10):
            assert client.get('/').status_code == 200
        rejected = client.get('/')
        assert rejected.status_code == 429 and int(rejected.headers['retry-after']) > 0
        assert len(calls) == 10
        row = service.visitor_risk.snapshot([site['id']])['sites'][site['id']]
        assert row['page_views'] == 10
