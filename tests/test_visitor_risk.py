import copy
import concurrent.futures
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from lanbridge.gateway import (HUMAN_COOKIE, PASS_COOKIE, create_gateway, signed_human,
                               signed_pass, valid_human, valid_pass)
from lanbridge.visitor_risk import VisitorRisk
from test_human_memory import verified_browser
from test_proxy import origin
from test_security import service


class MemoryStore:
    def __init__(self): self.values, self.writes, self.audit_rows = {}, 0, []
    def get(self, key, default=None): return copy.deepcopy(self.values.get(key, default))
    def set(self, key, value): self.values[key] = copy.deepcopy(value); self.writes += 1
    def audit(self, action, detail): self.audit_rows.append((action, detail))


def guard():
    store, clock = MemoryStore(), [1000.0]
    return VisitorRisk(store, clock=lambda: clock[0]), store, clock


def trigger(risk, kind='rate', ip='203.0.113.4', grants=()):
    for _ in range(risk.RULES[kind][1]):
        result = risk.record('site', ip, kind, grants=grants)
    return result


def test_isolated_burst_does_not_revoke_and_normal_traffic_never_writes():
    risk, store, clock = guard()
    for _ in range(59): assert risk.record('site', 'ip', 'rate') == 0
    assert not risk.remaining('site', 'ip') and store.writes == 0
    clock[0] += 61
    assert risk.record('site', 'ip', 'rate') == 0
    assert store.writes == 0


def test_cooldown_revocation_restart_privacy_and_no_attacker_extension():
    risk, store, clock = guard()
    token = 'fake-only-test-grant'
    assert trigger(risk, grants=[(token, 2000)]) == 300
    assert risk.revoked('site', token, 999)
    assert risk.remaining('other', '203.0.113.4') == 0
    assert risk.remaining('site', '203.0.113.5') == 0
    for _ in range(100): assert risk.record('site', '203.0.113.4', 'rate') == 300
    assert store.writes == 1
    reloaded = VisitorRisk(store, clock=lambda: clock[0])
    assert reloaded.revoked('site', token, 999) and reloaded.remaining('site', '203.0.113.4') == 300
    clock[0] += 301
    assert reloaded.remaining('site', '203.0.113.4') == 0
    assert trigger(reloaded) == 600
    serialized = str(store.values) + str(store.audit_rows)
    assert token not in serialized and '203.0.113.4' not in serialized
    clock[0] = 2100
    assert not reloaded.revoked('site', token, 999)


def test_scan_needs_distinct_sensitive_paths():
    risk, store, _ = guard()
    for _ in range(30): assert risk.record('site', 'ip', 'scan', path='/.env') == 0
    for i in range(30): assert risk.record('site', 'ip', 'scan', path=f'/missing/{i}') == 0
    paths = ['/.git/config', '/.git/HEAD', '/.svn/entries', '/.aws/credentials', '/wp-config.php', '/phpinfo.php', '/backup.sql']
    for path in paths[:-1]: assert risk.record('site', 'ip', 'scan', path=path) == 0
    assert risk.record('site', 'ip', 'scan', path=paths[-1]) == 300
    assert store.writes == 1


def test_failures_have_independent_window_and_expire():
    risk, store, clock = guard()
    for _ in range(4): assert risk.record('site', 'ip', 'verify') == 0
    clock[0] += 301
    assert risk.record('site', 'ip', 'verify') == 0
    assert store.writes == 0
    for _ in range(3): assert risk.record('site', 'ip', 'verify') == 0
    assert risk.record('site', 'ip', 'verify') == 300


def test_capacity_preserves_revocations_and_existing_cooldowns():
    risk, store, clock = guard()
    risk.LIMIT = 1
    assert trigger(risk, grants=[('old', 5000)]) == 300
    assert trigger(risk, ip='another', grants=[('new', 5000)]) == 300
    assert len(risk.state['blocked']) == 1 and len(risk.state['revoked']) == 1
    assert risk.revoked('site', 'old', 900)
    assert risk.revoked('site', 'not-listed', 999)
    assert risk.remaining('site', 'any-source') == 300
    clock[0] += 301
    assert not risk.remaining('site', 'any-source')
    assert not risk.revoked('site', 'fresh', clock[0])


def test_abuse_counters_are_bounded_and_escalation_is_thread_safe():
    risk, store, _ = guard()
    risk.LIMIT = 20
    for i in range(100): risk.record('site', str(i), 'rate')
    assert len(risk.events) == 20 and store.writes == 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: risk.record('site', 'attacker', 'rate'), range(100)))
    assert risk.remaining('site', 'attacker') == 300 and store.writes == 1


def test_rate_escalation_revokes_both_grants_and_reverify_cannot_bypass(service, verified_browser):
    site, client, headers, calls, _ = verified_browser
    site = service.save_site(site | {'requests_per_minute': 10})
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    human, short = signed_human(service, site, 'Test Browser'), signed_pass(service, site, '203.0.113.4')
    previous_short = signed_pass(service, site, '203.0.113.4')
    client.cookies.set(HUMAN_COOKIE, human); client.cookies.set(PASS_COOKIE, short)
    for _ in range(10): assert client.get('/', headers=headers).status_code == 200
    for _ in range(59): assert client.get('/', headers=headers).status_code == 429
    assert valid_human(service, site, 'Test Browser', human)
    response = client.get('/', headers=headers)
    assert response.status_code == 429 and response.headers['retry-after'] == '300'
    assert '异常' in response.text
    assert not valid_human(service, site, 'Test Browser', human)
    assert not valid_pass(service, site, '203.0.113.4', short)
    assert not valid_pass(service, site, '203.0.113.4', previous_short)
    client.cookies.set(HUMAN_COOKIE, human); client.cookies.set(PASS_COOKIE, short)
    assert client.post('/.lanbridge/verify', json={'token': 'valid-test-token'}, headers=headers).status_code == 429
    assert len(calls) == 1
    changed_network = headers | {'CF-Connecting-IP': '203.0.113.99', 'Accept': 'text/html'}
    assert 'class="cf-turnstile"' in client.get('/', headers=changed_network).text
    # Expiry ends only the cooldown, never resurrects the revoked cookies.
    now = time.time(); service.visitor_risk.clock = lambda: now + 301
    fresh = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    assert fresh.post('/.lanbridge/verify', json={'token': 'fresh-token'}, headers=headers).status_code == 200
    assert fresh.get('/', headers=headers).status_code == 200
    assert not valid_human(service, site, 'Test Browser', human)
    loaded = VisitorRisk(service.store)
    assert loaded.revoked(site['id'], human, now - 1)


def test_verification_failures_block_but_provider_faults_do_not(service, verified_browser, monkeypatch):
    site, client, headers, calls, _ = verified_browser
    site = service.save_site(site | {'passcode_required': True, 'passcode': 'long visitor password'})
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    human = signed_human(service, site, 'Test Browser')
    client.cookies.clear(); client.cookies.set(HUMAN_COOKIE, human)
    for _ in range(4): assert client.post('/.lanbridge/verify', json={'passcode': 'wrong'}, headers=headers).status_code == 403
    result = client.post('/.lanbridge/verify', json={'passcode': 'wrong'}, headers=headers)
    assert result.status_code == 429 and result.json()['detail']
    assert not valid_human(service, site, 'Test Browser', human)


@pytest.mark.parametrize('code', ['internal-error', 'timeout-or-duplicate', 'invalid-input-secret'])
def test_provider_error_codes_never_count_as_visitor_failures(service, verified_browser, monkeypatch, code):
    site, _, headers, _, _ = verified_browser
    original = httpx.AsyncClient
    class Client(original):
        async def post(self, url, **kwargs):
            return httpx.Response(200, json={'success': False, 'error-codes': [code]})
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    for _ in range(5): assert client.post('/.lanbridge/verify', json={'token': 'test'}, headers=headers).status_code == 403
    assert service.visitor_risk.remaining(site['id'], '203.0.113.4') == 0
    assert not any(key[1] == 'verify' for key in service.visitor_risk.events)


def test_scan_escalates_only_after_failed_upstream_sensitive_probes(service, verified_browser, monkeypatch):
    site, client, headers, _, _ = verified_browser
    original = httpx.AsyncClient
    class Client(original):
        async def send(self, request, **kwargs):
            return httpx.Response(404, request=request, stream=httpx.ByteStream(b'not found'), headers={'Content-Length': '9'})
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    for i in range(8): assert client.get(f'/ordinary-missing-{i}', headers=headers).status_code == 404
    paths = ['/.env', '/.git/config', '/.git/HEAD', '/.svn/entries', '/.aws/credentials', '/wp-config.php', '/phpinfo.php', '/backup.sql']
    for path in paths[:-1]: assert client.get(path, headers=headers).status_code == 404
    assert client.get(paths[-1], headers=headers).status_code == 429
    assert service.visitor_risk.remaining(site['id'], '203.0.113.4', verify=True) > 0


def test_malformed_provider_errors_are_outage_not_attack(service, verified_browser, monkeypatch):
    site, _, headers, _, _ = verified_browser
    original = httpx.AsyncClient
    class Client(original):
        async def post(self, url, **kwargs):
            return httpx.Response(200, json={'success': False, 'error-codes': None})
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    for _ in range(5): assert client.post('/.lanbridge/verify', json={'token': 'test'}, headers=headers).status_code == 503
    assert not service.visitor_risk.remaining(site['id'], '203.0.113.4')


@pytest.mark.parametrize('proof_kind', ['human', 'short'])
def test_websocket_memory_and_active_connection_respect_revocation(service, proof_kind):
    import asyncio
    import threading
    from websockets.asyncio.server import serve
    from starlette.websockets import WebSocketDisconnect

    ready, done, box = threading.Event(), threading.Event(), {}
    async def echo(ws):
        async for message in ws:
            await ws.send(message)
    async def run():
        async with serve(echo, '127.0.0.1', 0) as server:
            box['port'] = server.sockets[0].getsockname()[1]
            ready.set()
            while not done.is_set():
                await asyncio.sleep(.05)
    thread = threading.Thread(target=lambda: asyncio.run(run()), daemon=True)
    thread.start()
    assert ready.wait(5)
    try:
        site = service.save_site({'name': 'socket', 'hostname': 'app.example.com',
                                 'origin': f"http://127.0.0.1:{box['port']}", 'human_check': True})
        token = signed_human(service, site, 'Test Browser') if proof_kind == 'human' else signed_pass(service, site, 'testclient')
        with TestClient(create_gateway(service), base_url='https://app.example.com') as client:
            client.cookies.set(HUMAN_COOKIE if proof_kind == 'human' else PASS_COOKIE, token)
            with client.websocket_connect('wss://app.example.com/ws', headers={'user-agent': 'Test Browser'}) as ws:
                ws.send_text('normal message')
                assert ws.receive_text() == 'normal message'
                assert service.visitor_risk.snapshot()['memory_hits'] == (1 if proof_kind == 'human' else 0)
                service.visitor_risk.record(site['id'], 'testclient', 'flood', grants=[(token, time.time() + 600)])
                with pytest.raises(WebSocketDisconnect):
                    ws.receive_text()
            # A revoked browser proof cannot reopen a socket from a fresh peer.
            with pytest.raises(Exception):
                with client.websocket_connect('wss://app.example.com/ws', headers={'user-agent': 'Test Browser'}):
                    pass
    finally:
        done.set()
        thread.join(timeout=5)


def test_prior_source_memory_stays_revoked_after_a_day_and_strikes_reset(service):
    site = service.save_site({'name': 'memory', 'hostname': 'app.example.com',
                             'origin': 'http://127.0.0.1:8080', 'human_check': True})
    old = signed_human(service, site, 'Test Browser', 'old-source')
    now = time.time()
    service.visitor_risk.clock = lambda: now
    service.visitor_risk.record(site['id'], 'old-source', 'flood')
    assert not valid_human(service, site, 'Test Browser', old)
    service.visitor_risk.clock = lambda: now + 86401
    assert not valid_human(service, site, 'Test Browser', old)
    assert service.visitor_risk.record(site['id'], 'old-source', 'flood') == 300


def test_verification_started_before_cooldown_cannot_issue_new_cookie(service, verified_browser, monkeypatch):
    site, _, headers, _, _ = verified_browser
    original = httpx.AsyncClient
    class Client(original):
        async def post(self, url, **kwargs):
            service.visitor_risk.record(site['id'], '203.0.113.4', 'flood')
            return httpx.Response(200, json={'success': True, 'hostname': site['hostname'], 'action': 'lanbridge'})
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    result = client.post('/.lanbridge/verify', json={'token': 'test'}, headers=headers)
    assert result.status_code == 429
    assert all('Max-Age=0' in cookie for cookie in result.headers.get_list('set-cookie'))


def test_browser_anomalies_follow_network_changes_without_blocking_other_verified_peers(service, verified_browser):
    site, client, headers, _, _ = verified_browser
    # Present only the long memory, so moving networks keeps the same signed identity.
    import base64, json
    token = client.cookies.get(HUMAN_COOKIE)
    claims = json.loads(base64.urlsafe_b64decode(token.split('.')[0] + '=' * (-len(token.split('.')[0]) % 4)))
    browser_id = claims['browser_id']
    client.cookies.clear(); client.cookies.set(HUMAN_COOKIE, token)
    # Escalate across two networks using the same verified browser identity.
    for i in range(5):
        result = service.visitor_risk.record(site['id'], '203.0.113.' + str(4 + i % 2), 'verify', grants=[(token, claims['exp'])], browser_id=browser_id)
    assert result == 300
    assert service.visitor_risk.remaining(site['id'], '203.0.113.99', browser_id) == 300
    other = 'a' * 32
    assert not service.visitor_risk.remaining(site['id'], '203.0.113.4', other)
    assert service.visitor_risk.remaining(site['id'], '203.0.113.4', verify=True) == 300
    other_client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    other_client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser', '203.0.113.4', other))
    assert other_client.get('/', headers=headers).status_code == 200
    client.cookies.clear()
    assert client.post('/.lanbridge/verify', json={'token': 'test'}, headers=headers).status_code == 429


def test_static_resource_budget_does_not_consume_page_or_api_budget(service, verified_browser):
    site, _, headers, _, _ = verified_browser
    site = service.save_site(site | {'requests_per_minute': 10})
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser', browser_id='b' * 32))
    for _ in range(40): assert client.get('/app.js', headers=headers).status_code == 200
    assert client.get('/app.js', headers=headers).status_code == 429
    for _ in range(10): assert client.get('/api/data', headers=headers).status_code == 200
    assert client.get('/api/data', headers=headers).status_code == 429
    # Non-read requests cannot gain the asset allowance by changing their suffix.
    assert client.post('/costly.js', headers=headers).status_code == 429
    assert service.visitor_risk.snapshot()['memory_hits'] == 50
    site = service.save_site(site | {'protocols': ['websocket']})
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser', browser_id='b' * 32))
    assert client.get('/api/data', headers=headers).status_code == 403
    assert service.visitor_risk.snapshot()['memory_hits'] == 50


def test_network_ceiling_bounds_multiple_browser_identities(service, verified_browser):
    site, _, headers, _, _ = verified_browser
    site = service.save_site(site | {'requests_per_minute': 10})
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    for i in range(8):
        client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser', browser_id=f'{i:032x}'))
        for _ in range(10): assert client.get('/', headers=headers).status_code == 200
    client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser', browser_id='c' * 32))
    assert client.get('/', headers=headers).status_code == 429


def test_protection_statistics_are_bounded_aggregate_and_do_not_write_normal_storage():
    risk, store, _ = guard()
    risk.count('verified'); risk.count('memory_hits')
    assert store.writes == 0
    trigger(risk)
    snapshot = risk.snapshot()
    assert snapshot == {'since': 1000.0, 'verified': 1, 'memory_hits': 1, 'restricted': 1}
    assert 'ip' not in snapshot and 'cookies' not in snapshot


def test_restriction_returns_browser_countdown_and_json_retry_after():
    from lanbridge.gateway import restricted_response
    page = restricted_response('/', 300, browser_page=True)
    assert page.status_code == 429 and page.headers['retry-after'] == '300'
    assert '访问暂时受限' in page.body.decode() and 'id="retry" disabled' in page.body.decode()
    api = restricted_response('/.lanbridge/verify', 300, browser_page=True)
    assert api.headers['content-type'].startswith('application/json')
    assert api.headers['cache-control'] == 'no-store'


def test_protection_statistics_require_full_administrator(service):
    from lanbridge.admin import create_admin
    from test_security import admin_client
    service.visitor_risk.count('verified')
    admin = admin_client(service)
    result = admin.get('/api/state')
    assert result.status_code == 200 and result.json()['visitor_protection']['verified'] == 1
    limited = service.store.issue_temporary_token('isolated test', 1, ['sites'])
    client = TestClient(create_admin(service), base_url='http://127.0.0.1:8890')
    response = client.get('/api/state', headers={'Authorization': 'Bearer ' + limited['token']})
    assert response.status_code == 200 and response.json()['visitor_protection'] is None
    assert TestClient(create_admin(service), base_url='http://127.0.0.1:8890').get('/api/state').status_code == 401


def test_browser_revocation_invalidates_other_tokens_with_same_signed_identity(service, verified_browser):
    import base64, json
    site, client, _, _, _ = verified_browser
    human = client.cookies.get(HUMAN_COOKIE)
    payload = human.split('.')[0]
    browser_id = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))['browser_id']
    another_human = signed_human(service, site, 'Test Browser', '203.0.113.4', browser_id)
    another_short = signed_pass(service, site, '203.0.113.4', browser_id)
    assert valid_human(service, site, 'Test Browser', another_human)
    assert valid_pass(service, site, '203.0.113.4', another_short)
    service.visitor_risk.record(site['id'], '203.0.113.4', 'flood', browser_id=browser_id)
    assert not valid_human(service, site, 'Test Browser', another_human)
    assert not valid_pass(service, site, '203.0.113.4', another_short)
    service.visitor_risk = VisitorRisk(service.store)
    assert not valid_human(service, site, 'Test Browser', another_human)
    assert not valid_pass(service, site, '203.0.113.4', another_short)


@pytest.mark.parametrize("verify", [False, True])
def test_rate_retry_after_uses_remaining_window(service, monkeypatch, verify):
    from lanbridge.gateway import Limiter
    from test_security import add_site
    site = add_site(service, requests_per_minute=10)
    client = TestClient(create_gateway(service), base_url="https://app.example.com")
    now = [100.0]
    # Isolate the limiter clock; do not alter asyncio's monotonic clock.
    original_allow = Limiter.allow
    def allow(self, key, count, window=60):
        result = original_allow(self, key, count, window)
        if key not in starts: starts[key] = now[0]
        self.buckets[key][0] = starts[key] - now[0] + time.monotonic()
        return result
    starts = {}
    monkeypatch.setattr(Limiter, "allow", allow)
    path = "/.lanbridge/verify" if verify else "/api/data"
    for _ in range(5 if verify else 10):
        response = client.post(path, json={}, headers={"Origin":"https://app.example.com"}) if verify else client.get(path)
        assert response.status_code in (401, 403)
    now[0] = 158.2
    response = client.post(path, json={}, headers={"Origin":"https://app.example.com"}) if verify else client.get(path)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "2"
