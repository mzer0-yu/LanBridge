import hashlib
import time

from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from test_security import service, admin_client


def issue(service):
    return admin_client(service).post('/api/temporary-tokens', json={'name':'maintenance','hours':24}).json()


def test_issue_encrypts_value_and_lists_only_metadata(service):
    owner=admin_client(service)
    result=owner.post('/api/temporary-tokens',json={'name':'maintenance','hours':24}).json()
    assert result['token'].startswith('lb_tmp_')
    assert 86390 < result['expires']-time.time() <= 86400
    row=dict(service.store.db.execute('SELECT * FROM temporary_tokens').fetchone())
    assert row['digest']==hashlib.sha256(result['token'].encode()).hexdigest()
    assert result['token'] not in str(row)
    assert result['token'] not in owner.get('/api/temporary-tokens').text
    assert result['token'] not in str(service.store.audit_list())
    assert service.store.cipher.decrypt(row['encrypted_value'].encode()).decode()==result['token']
    for hours in [0,-1,True,'24']:
        assert owner.post('/api/temporary-tokens',json={'name':'test','hours':hours}).status_code==400
    for hours in [0.5,25,168]:
        result=owner.post('/api/temporary-tokens',json={'hours':hours}).json()
        assert abs(result['expires']-time.time()-hours*3600)<2


def test_bearer_scope_origin_expiry_and_revocation(service):
    owner=admin_client(service)
    grant=owner.post('/api/temporary-tokens',json={'name':'worker'}).json()
    client=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Authorization':'Bearer '+grant['token']})
    state=client.get('/api/state')
    assert state.status_code==200 and state.json()['access_scope']=='sites'
    assert not state.json()['audit'] and not state.json()['agent_skill_path']
    assert state.json()['audit_storage'] == {}
    for path in ['/api/password','/api/settings','/api/credentials','/api/gateway-port','/api/temporary-tokens','/api/connector/update','/api/cloudflare/create-tunnel','/api/shutdown','/api/local-login/approve']:
        assert client.post(path,json={}).status_code==403,path
    assert client.get('/api/temporary-tokens').status_code==403
    site=service.save_site({'name':'demo','hostname':'demo.example.com','origin':'http://127.0.0.1:9300','human_check':False})
    assert client.post('/api/sites/'+site['id']+'/pause',json={'paused':True}).status_code==200
    assert client.post('/api/sites/'+site['id']+'/pause',json={'paused':False},headers={'Origin':'https://evil.example'}).status_code==403
    owner.post('/api/temporary-tokens/'+grant['id']+'/revoke',json={})
    assert client.get('/api/state').status_code==401
    second=owner.post('/api/temporary-tokens',json={'name':'expires'}).json()
    service.store.db.execute('UPDATE temporary_tokens SET expires=? WHERE id=?',(time.time()-1,second['id']))
    service.store.db.commit()
    client.headers['Authorization']='Bearer '+second['token']
    assert client.get('/api/state').status_code==401


def test_browser_login_obeys_csrf_scope_and_revocation(service):
    grant=issue(service)
    client=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Origin':'http://127.0.0.1:8890'})
    login=client.post('/api/token-login',json={'token':grant['token']})
    assert login.status_code==200
    assert 'HttpOnly' in login.headers['set-cookie'] and 'SameSite=strict' in login.headers['set-cookie']
    bootstrap=client.get('/api/bootstrap').json()
    assert bootstrap['authenticated'] and bootstrap['scope']=='sites'
    assert client.post('/api/connector/stop',json={}).status_code==403
    client.headers['X-CSRF-Token']=bootstrap['csrf']
    assert client.post('/api/connector/stop',json={}).status_code==200
    assert client.post('/api/settings',json={}).status_code==403
    service.store.revoke_temporary_token(grant['id'])
    assert not client.get('/api/bootstrap').json()['authenticated']
    assert client.get('/api/state').status_code==401


def test_expiry_invalidates_derived_browser_session(service):
    grant=issue(service)
    session,_,_=service.store.temporary_login(grant['token'])
    assert service.store.session(session)['scope']=='sites'
    service.store.db.execute('UPDATE temporary_tokens SET expires=? WHERE id=?',(time.time()-1,grant['id']))
    service.store.db.commit()
    assert service.store.session(session) is None


def test_remote_token_access_keeps_website_and_network_policies(service):
    from test_remote_admin import remote_site, remote_client
    from lanbridge.gateway import create_gateway
    grant=issue(service)
    site=remote_site(service,human_check=True)
    headers={'Authorization':'Bearer '+grant['token']}
    with remote_client(service) as client:
        assert client.get('/api/state',headers=headers).status_code==200
        assert client.post('/api/sites/'+site['id']+'/pause',json={'paused':False},headers=headers).status_code==200
        assert client.post('/api/settings',json={},headers=headers).status_code in (401,403)
        service.set_site_paused(site['id'],True)
        assert client.get('/api/state',headers=headers).status_code==503
        service.set_site_paused(site['id'],False)
        service.save_site(site | {'allowed_ips':['192.0.2.1']})
        assert client.get('/api/state',headers=headers).status_code==403
    service.save_site({'name':'protected','hostname':'web.example.com','origin':'http://127.0.0.1:9300','human_check':True})
    with TestClient(create_gateway(service),base_url='https://web.example.com') as client:
        assert client.get('/api/state',headers=headers).status_code==401


def test_selected_account_permissions_do_not_grant_full_admin(service):
    owner=admin_client(service)
    for permissions in [[], ['admin'], 'account', [None], [{}]]:
        assert owner.post('/api/temporary-tokens',json={'name':'bad','permissions':permissions}).status_code==400
    grant=owner.post('/api/temporary-tokens',json={'name':'account operator','permissions':['account']}).json()
    assert grant['permissions']==['account']
    client=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Authorization':'Bearer '+grant['token']})
    assert client.get('/api/state').json()['access_permissions']==['account']
    assert client.post('/api/settings',json={'tunnel_name':'delegated'}).status_code==200
    assert service.settings()['tunnel_name']=='delegated'
    assert client.post('/api/credentials',json={'cf_write_token':'test-token-value'}).status_code==200
    for path,data in [('/api/settings',{'cloudflared_path':'evil.exe'}),('/api/settings',{'gateway_port':9999}),('/api/credentials',{'turnstile_secret':'test-secret'}),('/api/password',{}),('/api/temporary-tokens',{}),('/api/sites',{}),('/api/connector/start',{})]:
        assert client.post(path,json=data).status_code==403,path
    assert client.get('/api/temporary-tokens').status_code==403
    cookie_client=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Origin':'http://127.0.0.1:8890'})
    login=cookie_client.post('/api/token-login',json={'token':grant['token']}).json()
    assert login['permissions']==['account']
    cookie_client.headers['X-CSRF-Token']=login['csrf']
    assert cookie_client.post('/api/settings',json={'tunnel_name':'browser'}).status_code==200
    assert cookie_client.post('/api/sites',json={}).status_code==403
    both=owner.post('/api/temporary-tokens',json={'name':'both','permissions':['sites','account']}).json()
    client.headers['Authorization']='Bearer '+both['token']
    assert client.post('/api/connector/stop',json={}).status_code==200
    assert client.post('/api/settings',json={'tunnel_name':'both'}).status_code==200
    assert client.post('/api/temporary-tokens',json={}).status_code==403


def test_legacy_tokens_keep_sites_permission_after_migration(tmp_path):
    import sqlite3
    from lanbridge.store import Store
    raw='lb_tmp_legacy-test'
    db=sqlite3.connect(tmp_path/'state.sqlite')
    db.execute('CREATE TABLE temporary_tokens(id TEXT PRIMARY KEY,digest TEXT UNIQUE,name TEXT,created REAL,expires REAL,revoked INTEGER DEFAULT 0)')
    db.execute('INSERT INTO temporary_tokens(id,digest,name,created,expires) VALUES (?,?,?,?,?)',('legacy',hashlib.sha256(raw.encode()).hexdigest(),'legacy',time.time(),time.time()+3600))
    db.commit();db.close()
    store=Store(tmp_path)
    try:
        assert store.temporary_token(raw)['permissions']==['sites']
        session,_,_=store.temporary_login(raw)
        assert store.session(session)['permissions']==['sites']
        assert store.temporary_tokens()[0]['permissions']==['sites']
    finally:
        store.db.close()


def test_account_bearer_through_protected_remote_platform(service):
    from test_remote_admin import remote_site,remote_client
    remote_site(service,human_check=True)
    grant=service.store.issue_temporary_token('account',permissions=['account'])
    with remote_client(service) as client:
        client.headers['Authorization']='Bearer '+grant['token']
        assert client.post('/api/credentials',json={'cf_write_token':'account-test-value'}).status_code==200
        assert client.post('/api/settings',json={'tunnel_name':'remote'}).status_code==200
        assert client.post('/api/temporary-tokens',json={}).status_code in (401,403)


def test_reuse_free_numbers_and_track_authenticated_activity(service):
    owner=admin_client(service)
    first=service.store.issue_temporary_token()
    second=service.store.issue_temporary_token()
    assert [first['name'],second['name']]==['临时管理1','临时管理2']
    service.store.revoke_temporary_token(first['id'])
    replacement=service.store.issue_temporary_token()
    assert replacement['name']=='临时管理1' and replacement['id']!=first['id']
    service.store.db.execute('UPDATE temporary_tokens SET expires=? WHERE id=?',(time.time()-1,second['id']))
    service.store.db.commit()
    assert service.store.issue_temporary_token()['name']=='临时管理2'
    row=next(t for t in owner.get('/api/temporary-tokens').json()['tokens'] if t['id']==replacement['id'])
    assert row['last_login'] is None and row['last_access'] is None
    bearer=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Authorization':'Bearer '+replacement['token']})
    assert bearer.post('/api/password',json={}).status_code==403
    assert next(t for t in service.store.temporary_tokens() if t['id']==replacement['id'])['last_access'] is None
    assert bearer.get('/api/state').status_code==200
    row=next(t for t in service.store.temporary_tokens() if t['id']==replacement['id'])
    assert time.time()-row['last_access']<2 and row['last_login'] is None
    browser=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Origin':'http://127.0.0.1:8890'})
    assert browser.post('/api/token-login',json={'token':replacement['token']}).status_code==200
    assert browser.get('/api/state').status_code==200
    row=next(t for t in service.store.temporary_tokens() if t['id']==replacement['id'])
    assert row['last_access']>=row['last_login'] and time.time()-row['last_login']<2


def test_only_owner_can_reveal_active_tokens_with_csrf(service):
    owner=admin_client(service)
    grant=owner.post('/api/temporary-tokens',json={'permissions':['sites','account']}).json()
    path='/api/temporary-tokens/'+grant['id']+'/reveal'
    assert owner.get('/api/temporary-tokens').json()['tokens'][0]['revealable']
    unauthenticated=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Origin':'http://127.0.0.1:8890'})
    assert unauthenticated.post(path,json={}).status_code==401
    assert owner.post(path,json={},headers={'X-CSRF-Token':''}).status_code==403
    assert owner.post(path,json={},headers={'Origin':'https://evil.example'}).status_code==403
    bearer=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Authorization':'Bearer '+grant['token']})
    assert bearer.post(path,json={}).status_code==403
    assert owner.post(path,json={}).json()['token']==grant['token']
    assert grant['token'] not in str(service.store.audit_list())
    assert grant['token'] not in owner.get('/api/state').text
    owner.post('/api/temporary-tokens/'+grant['id']+'/revoke',json={})
    assert owner.post(path,json={}).status_code==400
    assert service.store.db.execute('SELECT encrypted_value FROM temporary_tokens WHERE id=?',(grant['id'],)).fetchone()[0] is None
    old=service.store.issue_temporary_token()
    service.store.db.execute('UPDATE temporary_tokens SET encrypted_value=NULL WHERE id=?',(old['id'],))
    service.store.db.commit()
    assert not next(t for t in service.store.temporary_tokens() if t['id']==old['id'])['revealable']
    assert owner.post('/api/temporary-tokens/'+old['id']+'/reveal',json={}).status_code==400


def test_access_timestamp_coalesces_writes_without_bypassing_revocation(service, monkeypatch):
    grant = service.store.issue_temporary_token()
    now = time.time()
    monkeypatch.setattr("lanbridge.store.time.time", lambda: now)
    before = service.store.db.total_changes
    service.store.touch_temporary_token(grant["id"])
    assert service.store.db.total_changes == before + 1
    for _ in range(100):
        service.store.touch_temporary_token(grant["id"])
    assert service.store.db.total_changes == before + 1
    now += 31
    service.store.touch_temporary_token(grant["id"])
    assert service.store.db.total_changes == before + 2
    service.store.revoke_temporary_token(grant["id"])
    assert service.store.temporary_token(grant["token"]) is None
    before = service.store.db.total_changes
    now += 31
    service.store.touch_temporary_token(grant["id"])
    assert service.store.db.total_changes == before
