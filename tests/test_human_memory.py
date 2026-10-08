import time

import httpx
import pytest
from fastapi.testclient import TestClient

from lanbridge.gateway import (HUMAN_COOKIE, PASS_COOKIE, create_gateway,
                               signed_human, signed_pass, valid_human, valid_pass)
from test_proxy import origin
from test_security import service, add_site


@pytest.fixture
def verified_browser(service, origin, monkeypatch):
    site = service.save_site({'name': 'Remembered browser', 'hostname': 'app.example.com', 'origin': f'http://127.0.0.1:{origin}', 'human_check': True})
    service.commit_settings(service.settings() | {'turnstile_sitekey': 'test-key'})
    service.store.set_secret('turnstile_secret', 'test-only-human-secret')
    site = service.sites()[0]
    calls = []
    original = httpx.AsyncClient
    class Client(original):
        async def post(self, url, **kwargs):
            if url == 'https://challenges.cloudflare.com/turnstile/v0/siteverify':
                calls.append(kwargs['data'])
                return httpx.Response(200, json={'success': True, 'hostname': site['hostname'], 'action': 'lanbridge'})
            return await super().post(url, **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    client = TestClient(create_gateway(service), base_url='https://app.example.com', client=('127.0.0.1', 123))
    headers = {'Origin': 'https://app.example.com', 'CF-Connecting-IP': '203.0.113.4', 'User-Agent': 'Test Browser'}
    response = client.post('/.lanbridge/verify', json={'token': 'valid-test-token'}, headers=headers)
    assert response.status_code == 200
    return site, client, headers, calls, response


def test_human_memory_survives_short_session_and_mobile_network_change(service, verified_browser, monkeypatch):
    site, client, headers, calls, response = verified_browser
    assert len(calls) == 1
    cookie = next(value for value in response.headers.get_list('set-cookie') if value.startswith(HUMAN_COOKIE))
    assert 'Max-Age=86400' in cookie and 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=lax' in cookie
    assert 'Domain=' not in cookie
    now = time.time()
    monkeypatch.setattr('lanbridge.gateway.time.time', lambda: now + 3601)
    assert not valid_pass(service, site, '203.0.113.4', client.cookies.get(PASS_COOKIE))
    changed_network = headers | {'CF-Connecting-IP': '203.0.113.99', 'Accept': 'text/html'}
    response = client.get('/', headers=changed_network)
    assert response.status_code == 200 and 'cf-turnstile' not in response.text
    assert len(calls) == 1
    # Ordinary traffic must not extend the original one-day expiry.
    assert HUMAN_COOKIE not in response.headers.get('set-cookie', '')
    monkeypatch.setattr('lanbridge.gateway.time.time', lambda: now + 86400 + 1)
    assert 'cf-turnstile' in client.get('/', headers=changed_network).text


@pytest.mark.parametrize('change', ['browser', 'host', 'site', 'policy', 'tamper', 'disabled', 'shortened'])
def test_remembered_human_is_bound_and_revocable(service, change, monkeypatch):
    site = add_site(service, human_remember_days=7)
    token = signed_human(service, site, 'Browser')
    assert valid_human(service, site, 'Browser', token)
    agent = 'Browser'
    if change == 'browser': agent = 'Other Browser'
    elif change == 'host': site = site | {'hostname': 'other.example.com'}
    elif change == 'site': site = site | {'id': 'other'}
    elif change == 'policy': site = site | {'policy_version': 'new'}
    elif change == 'tamper': token += 'x'
    elif change == 'disabled': site = site | {'human_remember_days': 0}
    else:
        site = site | {'human_remember_days': 1}
        now = time.time()
        monkeypatch.setattr('lanbridge.gateway.time.time', lambda: now + 2 * 86400)
    assert not valid_human(service, site, agent, token)
    assert not valid_pass(service, site, '203.0.113.4', token)


def test_remembered_human_does_not_bypass_password_or_network_policy(service, verified_browser):
    site, client, headers, calls, _ = verified_browser
    site = service.save_site(site | {'passcode_required': True, 'passcode': 'visitor long password'})
    # Issue a human-only grant for the updated policy; it must never grant password access.
    client.cookies.clear()
    client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser'))
    page = client.get('/', headers=headers | {'Accept': 'text/html'})
    assert 'id="passcode"' in page.text and 'class="cf-turnstile"' not in page.text
    assert client.get('/api/private', headers=headers).status_code == 401
    assert client.post('/.lanbridge/verify', json={'passcode': 'wrong'}, headers=headers).status_code == 403
    assert client.post('/.lanbridge/verify', json={'passcode': 'visitor long password'}, headers=headers).status_code == 200
    assert len(calls) == 1  # The independent human proof avoids another Cloudflare request.
    assert client.get('/', headers=headers).status_code == 200
    site = service.save_site(site | {'allowed_ips': ['192.0.2.0/24']})
    client.cookies.set(HUMAN_COOKIE, signed_human(service, site, 'Test Browser'))
    assert client.get('/', headers=headers).status_code == 403


def test_cosmetic_and_noop_saves_preserve_grants_policy_changes_revoke(service):
    site = add_site(service)
    token = signed_human(service, site, 'Browser')
    for body in [site, site | {'name': 'Renamed website'}]:
        updated = service.save_site(body)
        assert updated['policy_version'] == site['policy_version']
        assert valid_human(service, updated, 'Browser', token)
    changed = service.save_site(updated | {'requests_per_minute': 200})
    assert changed['policy_version'] != site['policy_version']
    assert not valid_human(service, changed, 'Browser', token)


def test_cookie_not_forwarded_to_origin(service, verified_browser):
    _, client, headers, _, _ = verified_browser
    client.cookies.set('app_cookie', 'preserved')
    response = client.get('/headers', headers=headers)
    assert response.status_code == 200
    assert response.json()['cookie'] == 'app_cookie=preserved'
    assert HUMAN_COOKIE not in response.text and PASS_COOKIE not in response.text


@pytest.mark.parametrize('value', [-1, 31])
def test_human_memory_configuration_has_bounds(service, value):
    with pytest.raises(ValueError):
        add_site(service, human_remember_days=value)


def test_legacy_partial_edit_preserves_configured_memory_period(service):
    site = add_site(service, human_remember_days=30)
    body = site | {'name': 'Updated label'}
    del body['human_remember_days']
    changed = service.save_site(body)
    assert changed['human_remember_days'] == 30
    assert changed['policy_version'] == site['policy_version']


def test_one_day_default_legacy_fallback_and_explicit_seven_day_option(service, monkeypatch):
    site = add_site(service)
    assert site['human_remember_days'] == 1
    legacy = dict(site)
    del legacy['human_remember_days']
    token = signed_human(service, legacy, 'Browser')
    optional = site | {'human_remember_days': 7}
    longer = signed_human(service, optional, 'Browser')
    now = time.time()
    monkeypatch.setattr('lanbridge.gateway.time.time', lambda: now + 86401)
    assert not valid_human(service, legacy, 'Browser', token)
    assert valid_human(service, optional, 'Browser', longer)
