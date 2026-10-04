import pytest
from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from test_security import service

URL = 'http://127.0.0.1:8890'


@pytest.fixture
def browsers(service, monkeypatch):
    opened = []
    monkeypatch.setattr('lanbridge.local_login.open_browser', lambda url: opened.append(url) or True)
    app = create_admin(service)
    headers = {'Origin': URL}
    approver = TestClient(app, base_url=URL, headers=headers)
    requester = TestClient(app, base_url=URL, headers=headers)
    thief = TestClient(app, base_url=URL, headers=headers)
    account = {'username': 'admin', 'password': 'test local login password'}
    assert approver.post('/api/setup', json=account).status_code == 200
    response = approver.post('/api/login', json=account)
    approver.headers['X-CSRF-Token'] = response.json()['csrf']
    return app, approver, requester, thief, opened


def start(requester):
    result = requester.post('/api/local-login/start', json={})
    assert result.status_code == 200
    assert 'HttpOnly' in result.headers['set-cookie'] and 'SameSite=strict' in result.headers['set-cookie']
    return result.json()


def test_external_confirmation_logs_in_only_the_bound_browser_once(browsers):
    app, approver, requester, thief, opened = browsers
    job = start(requester)
    data = {'request_id': job['request_id']}
    assert opened == [job['approval_url']]
    assert requester.get('/api/local-login/request/'+job['request_id']).status_code == 401
    assert requester.post('/api/local-login/poll', json=data).json()['phase'] == 'pending'
    details = approver.get('/api/local-login/request/'+job['request_id']).json()
    assert details['code'] == job['code'] and 'proof' not in details
    assert thief.post('/api/local-login/poll', json=data).status_code == 400
    approval = data | {'code': job['code'], 'allow': True}
    assert thief.post('/api/local-login/approve', json=approval).status_code == 401
    assert approver.post('/api/local-login/approve', json=approval).status_code == 200
    assert not requester.get('/api/bootstrap').json()['authenticated']
    assert thief.post('/api/local-login/poll', json=data).status_code == 400
    result = requester.post('/api/local-login/poll', json=data)
    assert result.json()['phase'] == 'done' and result.json()['csrf']
    assert requester.get('/api/bootstrap').json()['authenticated']
    assert requester.post('/api/local-login/poll', json=data).status_code == 400
    assert approver.post('/api/local-login/approve', json=approval).status_code == 400


def test_approval_requires_origin_csrf_and_matching_code(browsers):
    app, approver, requester, thief, opened = browsers
    job = start(requester)
    data = {'request_id': job['request_id'], 'code': job['code'], 'allow': True}
    assert approver.post('/api/local-login/approve', json=data, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert approver.post('/api/local-login/approve', json=data, headers={'X-CSRF-Token': ''}).status_code == 403
    assert approver.post('/api/local-login/approve', json=data | {'code': 'wrong'}).status_code == 400
    assert requester.post('/api/local-login/start', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403


@pytest.mark.parametrize('decision', ['deny', 'cancel', 'expire', 'restart', 'password'])
def test_rejected_cancelled_expired_and_invalidated_requests_cannot_log_in(browsers, service, decision):
    app, approver, requester, thief, opened = browsers
    job = start(requester)
    data = {'request_id': job['request_id']}
    if decision == 'deny':
        approver.post('/api/local-login/approve', json=data | {'code': job['code'], 'allow': False}).raise_for_status()
        assert requester.post('/api/local-login/poll', json=data).json()['phase'] == 'denied'
    elif decision == 'cancel':
        assert thief.post('/api/local-login/cancel', json=data).status_code == 400
        assert requester.post('/api/local-login/cancel', json=data).json()['phase'] == 'cancelled'
    elif decision == 'expire':
        app.state.local_login.requests[job['request_id']]['expires_at'] = 0
    elif decision == 'restart':
        start(requester)
    elif decision == 'password':
        response = approver.post('/api/password', json={'current': 'test local login password', 'password': 'changed local login password'})
        assert response.status_code == 200
    assert requester.post('/api/local-login/poll', json=data).status_code == 400
    assert not requester.get('/api/bootstrap').json()['authenticated']
    assert not any(record['action'] == 'admin_local_login' for record in service.store.audit_list())
