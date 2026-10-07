import pytest
from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from test_security import service

URL = 'http://127.0.0.1:8890'


@pytest.fixture
def browsers(service, monkeypatch):
    opened = []
    monkeypatch.setattr('lanbridge.local_login.open_browser', lambda url, browser='default': opened.append(url) or True)
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


def test_approved_login_replaces_existing_temporary_session(browsers):
    app,approver,requester,_,_=browsers
    issued=approver.post('/api/temporary-tokens',json={}).json()
    grant=requester.post('/api/token-login',json={'token':issued['token']})
    assert grant.status_code==200
    assert requester.get('/api/bootstrap').json()['scope']=='sites'
    job=start(requester)
    decision=approver.post('/api/local-login/approve',json={'request_id':job['request_id'],'code':job['code'],'allow':True})
    assert decision.status_code==200
    response=requester.post('/api/local-login/poll',json={'request_id':job['request_id']})
    assert response.status_code==200 and response.json()['phase']=='done'
    assert 'lb_admin=' in response.headers['set-cookie']
    assert requester.get('/api/bootstrap').json()['scope']=='admin'


def test_external_confirmation_logs_in_only_the_bound_browser_once(browsers):
    app, approver, requester, thief, opened = browsers
    job = start(requester)
    data = {'request_id': job['request_id']}
    assert opened == []
    assert thief.post('/api/local-login/open', json=data).status_code == 400
    assert requester.post('/api/local-login/open', json=data).json()['browser_opened']
    assert opened == [job['approval_url']]
    assert requester.post('/api/local-login/open', json=data,
                          headers={'Origin': 'https://evil.example'}).status_code == 403
    assert requester.post('/api/local-login/open', json=data).json()['browser_opened']
    assert opened == [job['approval_url'], job['approval_url']]
    assert '/admin#local-login=' in job['approval_url']
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
    assert requester.post('/api/local-login/open', json=data).status_code == 400
    assert approver.post('/api/local-login/approve', json=approval).status_code == 400


def test_browser_selection_is_validated_and_passed_to_launcher(service, monkeypatch):
    monkeypatch.setattr('lanbridge.local_login.available_browsers', lambda: [
        {'id': 'default', 'name': '系统默认浏览器'}, {'id': 'edge', 'name': 'Edge'}])
    # create_admin imports the launcher functions when building the app.
    selected = []
    monkeypatch.setattr('lanbridge.local_login.open_browser',
                        lambda url, browser: selected.append(browser) or True)
    app = create_admin(service)
    requester = TestClient(app, base_url=URL, headers={'Origin': URL})
    assert requester.post('/api/setup', json={'username':'admin','password':'test browser password'}).status_code == 200
    # Inspect this app's discovered choices without exposing executable paths.
    result = requester.get('/api/local-login/browsers')
    assert result.status_code == 200
    assert all(set(item) == {'id', 'name'} for item in result.json()['browsers'])
    for value in ('arbitrary.exe', ['edge'], 'C:/Windows/cmd.exe', 'chrome'):
        assert requester.post('/api/local-login/start', json={'browser': value}).status_code == 400
    assert not app.state.local_login.requests
    job = requester.post('/api/local-login/start', json={'browser':'edge'})
    assert job.status_code == 200 and job.json()['browser_name'] == 'Edge'
    assert selected == []
    assert requester.post('/api/local-login/open', json={'request_id':job.json()['request_id']}).status_code == 200
    assert selected == ['edge']


def test_same_browser_confirmation_preserves_shared_admin_session(browsers):
    app, approver, requester, thief, opened = browsers
    job = start(requester)
    # Two tabs in Chrome share cookies, but retain their own JavaScript CSRF values.
    login = requester.post('/api/login', json={
        'username': 'admin', 'password': 'test local login password'})
    csrf = login.json()['csrf']
    requester.headers['X-CSRF-Token'] = csrf
    token = requester.cookies.get('lb_admin')
    data = {'request_id': job['request_id']}
    assert requester.post('/api/local-login/poll', json=data).json()['phase'] == 'pending'
    assert requester.post('/api/local-login/approve', json=data | {
        'code': job['code'], 'allow': True}).status_code == 200
    result = requester.post('/api/local-login/poll', json=data)
    assert result.json() == {'phase': 'done', 'csrf': csrf}
    assert requester.cookies.get('lb_admin') == token
    # The confirmation tab can still make protected requests using its CSRF value.
    assert requester.post('/api/local-login/approve', json=data | {
        'code': job['code'], 'allow': True}).status_code == 400


def test_named_browser_launch_never_falls_back(monkeypatch):
    from pathlib import Path
    from lanbridge import local_login
    calls = []
    monkeypatch.setattr(local_login, 'browser_executable', lambda name: Path('C:/fake/msedge.exe') if name == 'edge' else None)
    monkeypatch.setattr(local_login.subprocess, 'Popen', lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(local_login.webbrowser, 'open', lambda url: calls.append(['default', url]) or True)
    assert local_login.open_browser(URL, 'edge')
    assert calls == [[str(Path('C:/fake/msedge.exe')), URL]]
    assert not local_login.open_browser(URL, 'chrome')
    assert len(calls) == 1
    assert local_login.open_browser(URL, 'default')


def test_confirmation_url_preserves_localhost_cookie_origin(browsers):
    app, approver, requester, thief, opened = browsers
    localhost = TestClient(app, base_url='http://localhost:8890',
                           headers={'Origin': 'http://localhost:8890'})
    job = start(localhost)
    assert job['approval_url'].startswith('http://localhost:8890/admin#local-login=')


def test_expired_shared_session_is_not_reused(browsers, service):
    app, approver, requester, thief, opened = browsers
    job = start(requester)
    login = requester.post('/api/login', json={
        'username': 'admin', 'password': 'test local login password'})
    old_token = requester.cookies.get('lb_admin')
    data = {'request_id': job['request_id']}
    approver.post('/api/local-login/approve', json=data | {
        'code': job['code'], 'allow': True}).raise_for_status()
    service.store.logout(old_token)
    result = requester.post('/api/local-login/poll', json=data)
    assert result.json()['phase'] == 'done'
    assert result.json()['csrf'] != login.json()['csrf']
    assert requester.cookies.get('lb_admin') != old_token


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


def test_password_change_cannot_interleave_local_session_issuance(service, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from lanbridge.local_login import LocalLogin
    from lanbridge.store import password_hash
    service.store.set("admin", {"username":"admin", "password_hash":password_hash("old local login password")})
    local = LocalLogin(service.store)
    request_id, proof, details = local.start()
    local.decide(request_id, details["code"], True)
    checked, release, changing, changed = (threading.Event() for _ in range(4))
    original = service.store.get
    def pause_admin(key, default=None):
        value = original(key, default)
        if key == "admin" and not checked.is_set():
            checked.set()
            assert release.wait(5)
        return value
    monkeypatch.setattr(service.store, "get", pause_admin)
    new_hash = password_hash("new local login password")
    def change():
        changing.set()
        with service.store.lock, service.store.db:
            service.store.set("admin", {"username":"admin", "password_hash":new_hash})
            service.store.db.execute("DELETE FROM sessions")
        changed.set()
    with ThreadPoolExecutor(max_workers=2) as executor:
        poll = executor.submit(local.poll, request_id, proof)
        try:
            assert checked.wait(5)
            mutation = executor.submit(change)
            assert changing.wait(5)
            assert not changed.wait(.2)
        finally:
            release.set()
        result, token = poll.result(timeout=5)
        mutation.result(timeout=5)
    assert result["phase"] == "done"
    assert service.store.session(token) is None
