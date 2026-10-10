import pytest
from fastapi.testclient import TestClient
from lanbridge.gateway import create_gateway, signed_human, signed_pass, valid_pass, HUMAN_COOKIE, PASS_COOKIE, Limiter
from test_security import service
from test_proxy import origin

HEADERS = {'CF-Connecting-IP': '203.0.113.4', 'User-Agent': 'Test Browser'}
NAV = HEADERS | {'Accept': 'text/html'}

@pytest.fixture
def adaptive(service, origin):
    site = service.save_site({'name': 'Adaptive', 'hostname': 'app.example.com',
        'origin': f'http://127.0.0.1:{origin}', 'human_check_mode': 'adaptive', 'requests_per_minute': 20})
    service.commit_settings(service.settings() | {'turnstile_sitekey': 'test-key'})
    service.store.set_secret('turnstile_secret', 'test-only-secret')
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    return service.sites()[0], client


def test_first_visit_then_high_frequency_requires_human(service, adaptive):
    site, client = adaptive
    for _ in range(9):
        response = client.get('/', headers=NAV)
        assert response.status_code == 200 and 'class="cf-turnstile"' not in response.text
    response = client.get('/', headers=NAV)
    assert response.status_code == 200 and 'class="cf-turnstile"' in response.text
    response = client.get('/api/data', headers=HEADERS)
    assert response.status_code == 401 and response.json()['verification_required']
    other = client.get('/', headers=NAV | {'CF-Connecting-IP': '203.0.113.5'})
    assert 'class="cf-turnstile"' not in other.text


def test_assets_do_not_trigger_but_cannot_bypass_pending_challenge(service, adaptive):
    site, client = adaptive
    for i in range(40): assert client.get(f'/file{i}.css', headers=HEADERS).status_code == 200
    assert 'class="cf-turnstile"' not in client.get('/', headers=NAV).text
    for _ in range(9): client.get('/api/data', headers=HEADERS)
    assert client.get('/file.css', headers=HEADERS).status_code == 401


def test_post_with_asset_suffix_counts_as_dynamic_request(service, adaptive):
    _, client = adaptive
    for _ in range(9): assert client.post('/file.css', headers=HEADERS).status_code != 401
    assert client.post('/file.css', headers=HEADERS).status_code == 401


def test_verified_browser_passes_challenge_but_hard_rate_limit_remains(service, adaptive):
    site, client = adaptive
    for _ in range(10): client.get('/', headers=NAV)
    client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser', '203.0.113.4'))
    response = client.get('/', headers=NAV)
    assert response.status_code == 200 and 'class="cf-turnstile"' not in response.text
    for _ in range(9): client.get('/', headers=NAV)
    assert client.get('/', headers=NAV).status_code == 429


def test_anonymous_verify_cannot_mint_human_proof(service, adaptive):
    site, client = adaptive
    result = client.post('/.lanbridge/verify', json={}, headers=HEADERS | {'Origin':'https://app.example.com'})
    assert result.status_code == 400
    assert 'set-cookie' not in result.headers and PASS_COOKIE not in client.cookies and HUMAN_COOKIE not in client.cookies
    assert service.visitor_risk.snapshot()['verified'] == 0
    for _ in range(10): response = client.get('/', headers=NAV)
    assert 'class="cf-turnstile"' in response.text
    denied = client.post('/.lanbridge/verify', json={}, headers=HEADERS | {'Origin':'https://app.example.com'})
    assert denied.status_code == 403


def test_password_session_cannot_bypass_later_human_challenge(service, adaptive):
    site, _ = adaptive
    site = service.save_site(site | {'passcode_required':True, 'passcode':'visitor long password'})
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1',123))
    page = client.get('/', headers=NAV)
    assert 'class="cf-turnstile"' not in page.text and 'id="passcode"' in page.text
    result = client.post('/.lanbridge/verify', json={'passcode':'visitor long password'}, headers=HEADERS | {'Origin':'https://app.example.com'})
    assert result.status_code == 200 and HUMAN_COOKIE not in client.cookies
    for _ in range(9): response = client.get('/', headers=NAV)
    assert 'class="cf-turnstile"' in response.text


def test_verified_human_without_password_cannot_bypass_password(service, adaptive):
    site, _ = adaptive
    site = service.save_site(site | {'passcode_required':True, 'passcode':'visitor long password'})
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1',123))
    client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser'))
    assert client.get('/api/data', headers=HEADERS).status_code == 401


def test_frequency_window_expires_without_extending_rejections(service, adaptive, monkeypatch):
    _, client = adaptive
    import time
    now = time.monotonic()
    monkeypatch.setattr('lanbridge.gateway.time.monotonic', lambda: now)
    for _ in range(10): response = client.get('/', headers=NAV)
    assert 'class="cf-turnstile"' in response.text
    now += 61
    assert 'class="cf-turnstile"' not in client.get('/', headers=NAV).text


def test_existing_mode_and_omitted_updates_preserve_policy(service, adaptive):
    site, _ = adaptive
    old = site['policy_version']
    body = site.copy(); body.pop('human_check_mode')
    saved = service.save_site(body)
    assert saved['human_check_mode'] == 'adaptive' and saved['policy_version'] == old
    changed = service.save_site(saved | {'human_check_mode':'always'})
    assert changed['policy_version'] != old
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1',123))
    assert 'class="cf-turnstile"' in client.get('/', headers=NAV).text
    with pytest.raises(ValueError): service.save_site(changed | {'human_check_mode':'invalid'})


def test_limiter_capacity_fails_closed_for_unknown_trigger(monkeypatch):
    limiter = Limiter()
    for i in range(10000): limiter.allow(i, 1)
    assert limiter.exhausted('unknown', 60)


def test_adaptive_websocket_first_visit_then_challenge(service):
    import asyncio, threading
    from websockets.asyncio.server import serve
    from starlette.websockets import WebSocketDisconnect
    ready, done, ports = threading.Event(), threading.Event(), []
    async def echo(ws):
        async for message in ws: await ws.send(message)
    async def run():
        async with serve(echo, '127.0.0.1', 0) as server:
            ports.append(server.sockets[0].getsockname()[1]); ready.set()
            while not done.is_set(): await asyncio.sleep(.05)
    thread = threading.Thread(target=lambda: asyncio.run(run()), daemon=True); thread.start()
    assert ready.wait(5)
    try:
        site = service.save_site({'name':'socket','hostname':'app.example.com',
            'origin':f'http://127.0.0.1:{ports[0]}','human_check_mode':'adaptive','requests_per_minute':10})
        with TestClient(create_gateway(service), base_url='https://app.example.com') as client:
            for _ in range(4):
                with client.websocket_connect('wss://app.example.com/ws') as ws:
                    ws.send_text('hello'); assert ws.receive_text() == 'hello'
            with pytest.raises(WebSocketDisconnect) as caught:
                with client.websocket_connect('wss://app.example.com/ws'): pass
            assert caught.value.code == 1008
            client.cookies.set(HUMAN_COOKIE, signed_human(service, site, client.headers['user-agent']))
            with client.websocket_connect('wss://app.example.com/ws') as ws:
                ws.send_text('verified'); assert ws.receive_text() == 'verified'
    finally:
        done.set(); thread.join(timeout=5)


def test_successful_adaptive_verification_saves_proof(service, adaptive, monkeypatch):
    import httpx
    site, client = adaptive
    for _ in range(10): client.get('/', headers=NAV)
    original = httpx.AsyncClient
    class Client(original):
        async def post(self, url, **kwargs):
            if url == 'https://challenges.cloudflare.com/turnstile/v0/siteverify':
                return httpx.Response(200, json={'success':True,'hostname':site['hostname'],'action':'lanbridge'})
            return await super().post(url, **kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    result = client.post('/.lanbridge/verify', json={'token':'fake-valid-token'}, headers=HEADERS | {'Origin':'https://app.example.com'})
    assert result.status_code == 200 and HUMAN_COOKIE in client.cookies
    assert valid_pass(service, site, '203.0.113.4', client.cookies.get(PASS_COOKIE), require_human=True)
    assert service.visitor_risk.snapshot()['verified'] == 1
    assert service.visitor_risk.snapshot([site['id']])['sites'][site['id']]['verified'] == 1
    assert 'class="cf-turnstile"' not in client.get('/', headers=NAV).text


def test_legacy_site_without_mode_stays_always(service, origin):
    site = service.save_site({'name':'legacy','hostname':'app.example.com','origin':f'http://127.0.0.1:{origin}'})
    assert site['human_check_mode'] == 'always'
    legacy = site.copy();legacy.pop('human_check_mode');service.store.set('sites',[legacy])
    assert service.sites()[0]['human_check_mode'] == 'always'
    assert service.save_site(legacy)['policy_version'] == site['policy_version']


@pytest.mark.parametrize('path', ['/', '/file.css', '/api/data'])
def test_cookie_clear_cannot_bypass_adaptive_cooldown(service, adaptive, path):
    site, client = adaptive
    browser = '1' * 32
    token = signed_human(service, site, 'Test Browser', '203.0.113.4', browser)
    client.cookies.set(HUMAN_COOKIE, token)
    for i in range(8):
        service.visitor_risk.record(site['id'], '203.0.113.4', 'scan', path='/.env'+str(i), browser_id=browser)
    assert client.get(path, headers=NAV).status_code == 429
    client.cookies.clear()
    assert client.get(path, headers=NAV).status_code == 429
    assert client.post('/.lanbridge/verify', json={}, headers=HEADERS | {'Origin':'https://app.example.com'}).status_code == 429
    # An unrelated authenticated browser on the same NAT remains independent.
    client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser', '203.0.113.4', '2'*32))
    assert client.get(path, headers=NAV).status_code == 200


def test_legacy_empty_session_is_not_a_browser_identity(service, adaptive):
    from starlette.requests import Request
    from lanbridge.gateway import visitor_identity, visitor_grants
    site, _ = adaptive
    token = signed_pass(service, site, '203.0.113.4', '3'*32, human_verified=False)
    req = Request({'type':'http','method':'GET','path':'/', 'headers':[(b'user-agent',b'Test Browser'),(b'cookie',(PASS_COOKIE+'='+token).encode())]})
    assert visitor_identity(service, site, '203.0.113.4', req) is None
    assert visitor_grants(service, site, '203.0.113.4', req) == []


def test_request_proof_reuse_does_not_persist_across_requests(service, adaptive, monkeypatch):
    site, client = adaptive
    client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser', '203.0.113.4', '4'*32))
    original = service.store.secret
    calls = []
    def secret(key):
        if key == 'signing_key': calls.append(1)
        return original(key)
    monkeypatch.setattr(service.store, 'secret', secret)
    for expected in (1, 2):
        assert client.get('/', headers=NAV).status_code == 200
        assert len(calls) == expected


def test_cached_proof_still_checks_revocation_and_expiry(service, adaptive, monkeypatch):
    import time
    from lanbridge.gateway import valid_human
    site, _ = adaptive
    browser = '5'*32
    token = signed_human(service, site, 'Test Browser', '203.0.113.4', browser)
    proofs = {}
    assert valid_human(service, site, 'Test Browser', token, proofs=proofs)
    assert not valid_human(service, site | {'policy_version':'changed'}, 'Test Browser', token, proofs=proofs)
    assert not valid_human(service, site, 'Other Browser', token, proofs=proofs)
    for _ in range(60): service.visitor_risk.record(site['id'], '203.0.113.4', 'rate', browser_id=browser)
    assert not valid_human(service, site, 'Test Browser', token, proofs=proofs)
    assert valid_human(service, site, 'Test Browser', token, check_risk=False, proofs=proofs)
    monkeypatch.setattr('lanbridge.gateway.time.time', lambda: proofs[token]['exp'] + 1)
    assert not valid_human(service, site, 'Test Browser', token, check_risk=False, proofs=proofs)


def test_anonymous_websocket_checks_source_cooldown(service):
    from starlette.websockets import WebSocketDisconnect
    site = service.save_site({'name':'socket', 'hostname':'app.example.com', 'origin':'http://127.0.0.1:9300', 'human_check_mode':'adaptive'})
    for i in range(8): service.visitor_risk.record(site['id'], '127.0.0.1', 'scan', path='/.env'+str(i), browser_id='6'*32)
    with TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1',123)) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect('wss://app.example.com/ws'): pass
        assert caught.value.code == 1008


@pytest.mark.parametrize('change', ['policy', 'pause', 'disable', 'remove', 'rename'])
def test_inflight_verification_rechecks_current_site(service, adaptive, monkeypatch, change):
    import httpx
    site, client = adaptive
    original = httpx.AsyncClient
    class Client(original):
        async def post(self, url, **kwargs):
            if url == 'https://challenges.cloudflare.com/turnstile/v0/siteverify':
                if change == 'remove':
                    service.store.set('sites', [])
                else:
                    update = {'policy':{'requests_per_minute':30}, 'pause':{'paused':True},
                              'disable':{'enabled':False}, 'rename':{'name':'new name'}}[change]
                    service.save_site(site | update)
                return httpx.Response(200,json={'success':True,'hostname':site['hostname'],'action':'lanbridge'})
            return await super().post(url,**kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    result = client.post('/.lanbridge/verify',json={'token':'test-only-token'},headers=HEADERS|{'Origin':'https://app.example.com'})
    if change == 'rename':
        assert result.status_code == 200 and HUMAN_COOKIE in client.cookies
        assert service.visitor_risk.snapshot()['verified'] == 1
    else:
        assert result.status_code == 409
        assert 'set-cookie' not in result.headers
        assert service.visitor_risk.snapshot()['verified'] == 0
