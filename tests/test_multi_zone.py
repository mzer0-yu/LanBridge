from copy import deepcopy
from urllib.parse import urlsplit, parse_qs
import pytest
from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from lanbridge.models import Settings
from lanbridge.token_manager import TokenManager
from test_security import service
from test_token_manager import groups


def mock_zones(service, monkeypatch):
    def request(method, path, body=None):
        zid = path.split('/')[2]
        return {'name': 'example.com' if zid == 'b'*32 else 'other.com', 'status':'active', 'account':{'id':'a'*32}}
    monkeypatch.setattr(service.cf, 'request', request)


def test_configuration_snapshot_reads_settings_and_credentials_once(service, monkeypatch):
    counts = {'settings': 0}
    original_settings, original_secret = service.settings, service.store.secret
    def settings():
        counts['settings'] += 1
        return original_settings()
    def secret(key):
        counts[key] = counts.get(key, 0) + 1
        return original_secret(key)
    monkeypatch.setattr(service, 'settings', settings)
    monkeypatch.setattr(service.store, 'secret', secret)
    snapshot = service.configuration_snapshot()
    assert counts == {'settings': 1, 'cf_read_token': 1, 'cf_write_token': 1, 'turnstile_secret': 1, 'tunnel_token': 1}
    assert snapshot['cloudflare_setup']['ready'] == snapshot['credentials']['cf_write_token']
    assert all(isinstance(value, bool) for value in snapshot['credentials'].values())
    service.store.set_secret('cf_write_token', '')
    assert '写入 API Token' in service.configuration_snapshot()['cloudflare_setup']['missing']


def test_legacy_settings_and_sites_keep_binding(service, monkeypatch):
    assert service.settings()['zones'] == [{'zone_id':'b'*32,'zone_name':'example.com'}]
    service.store.set('sites', [{'id':'legacy','hostname':'app.example.com','protocols':['http']}])
    assert service.sites()[0]['zone_id']=='b'*32
    mock_zones(service,monkeypatch)
    service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    assert service.sites()[0]['zone_id']=='b'*32
    assert len(service.settings()['zones'])==2


@pytest.mark.parametrize('status,account', [('pending','a'*32),('active','d'*32)])
def test_attachment_rejects_inactive_or_other_account_without_changes(service,monkeypatch,status,account):
    before=service.settings()
    monkeypatch.setattr(service.cf,'request',lambda *a,**k:{'name':'other.com','status':status,'account':{'id':account}})
    with pytest.raises(ValueError):service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    assert service.settings()==before


def test_zone_boundary_explicit_selection_and_remove_used_zone(service,monkeypatch):
    mock_zones(service,monkeypatch)
    service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    body={'name':'test','hostname':'app.other.com','origin':'http://127.0.0.1:9300','human_check':False}
    with pytest.raises(ValueError):service.save_site(body|{'zone_id':'b'*32})
    with pytest.raises(ValueError):service.save_site(body|{'hostname':'app.notother.com'})
    site=service.save_site(body)
    assert site['zone_id']=='c'*32
    with pytest.raises(ValueError,match='仍被'):service.remove_zone('c'*32)
    service.save_site(body|{'hostname':'app.example.com'})
    with pytest.raises(ValueError,match='仍被'):service.remove_zone('b'*32)
    service.store.set('sites',[])
    service.remove_zone('c'*32)
    assert len(service.settings()['zones'])==1


def test_remove_default_promotes_remaining_zone_without_cloud_changes(service, monkeypatch):
    mock_zones(service, monkeypatch)
    service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    site = service.save_site({'name':'remaining','hostname':'other.com','origin':'http://127.0.0.1:9300','human_check':False})
    service.store.set('published_hosts', ['other.com'])
    service.store.set_secret('tunnel_token', 'unchanged-test-tunnel-secret')
    cfg = service.settings() | {'tunnel_id':'11111111-1111-1111-1111-111111111111'}
    service.store.set('settings', cfg)
    owned = {'kind':'oauth','account_id':'a'*32,'zone_id':'b'*32,'credential_digest':'unchanged'}
    service.store.set('managed_business_token', owned)
    secret = service.store.secret('cf_write_token')
    monkeypatch.setattr(service.cf, 'request', lambda *a, **k: pytest.fail('removal must not call Cloudflare'))
    result = service.remove_zone('b'*32)
    assert result['zone_id']=='c'*32 and result['zone_name']=='other.com'
    assert result['zones']==[{'zone_id':'c'*32,'zone_name':'other.com'}]
    assert result['tunnel_id']==cfg['tunnel_id']
    assert service.sites()==[site] and service.store.get('published_hosts')==['other.com']
    assert service.store.secret('cf_write_token')==secret
    assert service.store.secret('tunnel_token')=='unchanged-test-tunnel-secret'
    assert service.store.get('managed_business_token')==owned|{'zone_id':'c'*32}


def test_remove_last_default_clears_selection_and_readd_rebinds_local_metadata(service, monkeypatch):
    from lanbridge.browser_auth import BrowserAuth
    from test_browser_auth import snapshot
    auth = BrowserAuth(service)
    auth.save_profile(snapshot(), service.settings())
    foreign = {'account_id':'d'*32,'zone_id':'b'*32,'id':'e'*32}
    service.store.set('managed_read_token', foreign)
    result=service.remove_zone('b'*32)
    assert result['zones']==[] and result['zone_id']==result['zone_name']==''
    assert result['account_id']=='a'*32
    assert service.store.get('managed_read_token')==foreign
    assert auth.access_token()==snapshot()['profile']['oauth_token']
    # A newly attached first domain also moves only the local default metadata.
    mock_zones(service, monkeypatch)
    service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    assert service.settings()['zone_id']=='c'*32
    assert auth.access_token()==snapshot()['profile']['oauth_token']


@pytest.mark.parametrize('published', [False, True])
def test_remove_default_keeps_associations_and_reports_host(service, published):
    if published:
        service.store.set('published_hosts', ['example.com'])
    else:
        service.save_site({'name':'disabled','hostname':'example.com','origin':'http://127.0.0.1:9300','human_check':False,'enabled':False})
    before=service.settings(),service.sites(),service.store.get('published_hosts')
    with pytest.raises(ValueError, match='仍被.*example.com'):
        service.remove_zone('b'*32)
    assert (service.settings(),service.sites(),service.store.get('published_hosts'))==before


def test_remove_default_api_keeps_csrf_and_supports_last_zone(service):
    token,csrf=service.store.login()
    client=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',cookies={'lb_admin':token})
    path='/api/zones/'+('b'*32)+'/remove'
    assert client.post(path,json={}).status_code==403
    assert service.settings()['zone_id']=='b'*32
    response=client.post(path,json={},headers={'Origin':'http://127.0.0.1:8890','X-CSRF-Token':csrf})
    assert response.status_code==200
    assert response.json()['settings']['zones']==[]


def test_set_default_preserves_sites_routes_and_oauth_without_cloud_writes(service, monkeypatch):
    from lanbridge.browser_auth import BrowserAuth
    from test_browser_auth import snapshot
    mock_zones(service, monkeypatch)
    service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    site=service.save_site({'name':'old','hostname':'example.com','origin':'http://127.0.0.1:9300','human_check':False})
    service.store.set('sites',[{k:v for k,v in site.items() if k!='zone_id'}])
    service.store.set('published_hosts',['example.com'])
    auth=BrowserAuth(service);auth.save_profile(snapshot(),service.settings())
    previous=service.settings();secrets_before=[service.store.secret(k) for k in ('cf_write_token','cf_oauth_profile','signing_key')]
    monkeypatch.setattr(service.cf,'request',lambda *a,**k:pytest.fail('default selection is local'))
    cfg=service.set_default_zone('c'*32)
    assert cfg['zone_name']=='other.com' and cfg['zone_id']=='c'*32
    assert cfg['zones']==previous['zones'] and cfg['account_id']==previous['account_id']
    assert service.sites()==[site] and service.store.get('published_hosts')==['example.com']
    assert service.store.get('sites')[0]['zone_id']=='b'*32
    assert secrets_before==[service.store.secret(k) for k in ('cf_write_token','cf_oauth_profile','signing_key')]
    assert auth.access_token()==snapshot()['profile']['oauth_token']
    with pytest.raises(ValueError,match='仍被'):service.remove_zone('b'*32)
    before=service.store.audit_list()
    assert service.set_default_zone('c'*32)==cfg
    assert service.store.audit_list()==before
    with pytest.raises(ValueError,match='已接入'):service.set_default_zone('d'*32)
    assert service.settings()==cfg


def test_default_selection_api_authentication_and_account_scope(service, monkeypatch):
    from test_security import admin_client
    mock_zones(service,monkeypatch);service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    path='/api/zones/'+('c'*32)+'/default'
    anon=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Origin':'http://127.0.0.1:8890'})
    assert anon.post(path,json={}).status_code==401
    owner=admin_client(service)
    assert owner.post(path,json={},headers={'X-CSRF-Token':'invalid'}).status_code==403
    assert service.settings()['zone_id']=='b'*32
    for permissions,status in [(['sites'],403),(['sites','account'],200)]:
        grant=owner.post('/api/temporary-tokens',json={'permissions':permissions}).json()
        client=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Authorization':'Bearer '+grant['token']})
        assert client.post(path,json={}).status_code==status
    assert service.settings()['zone_id']=='c'*32


@pytest.mark.parametrize('hosts', [('app.example.com', 'app.other.com'), ('example.com', 'other.com')])
def test_multi_zone_dns_publish_and_readback_use_each_site_zone(service,monkeypatch,hosts):
    mock_zones(service,monkeypatch)
    service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    cfg=service.settings()|{'tunnel_id':'11111111-1111-1111-1111-111111111111'}
    service.store.set('settings',cfg);service.store.set('owned_tunnel',cfg['tunnel_id'])
    for host in hosts:
        service.save_site({'name':host,'hostname':host,'origin':'http://127.0.0.1:9300','human_check':False})
    remote={'config':{'ingress':[{'hostname':'unrelated.example.com','service':'http://127.0.0.1:1234'},{'service':'http_status:404'}]},'version':1}
    dns={'b'*32:[],'c'*32:[]};writes=[]
    def request(method,path,body=None):
        if path.endswith('/configurations'):
            if method=='PUT':remote['config']=deepcopy(body['config']);remote['version']+=1
            return deepcopy(remote)
        zid=path.split('/')[2]
        if '/dns_records' in path:
            if method=='POST':
                zone_name='example.com' if zid=='b'*32 else 'other.com'
                assert body['name']==zone_name or body['name'].endswith('.'+zone_name)
                row={'id':zid,**body};dns[zid].append(row);writes.append(zid);return deepcopy(row)
            name=parse_qs(urlsplit(path).query)['name'][0]
            return deepcopy([r for r in dns[zid] if r['name']==name])
        return {'name':'example.com' if zid=='b'*32 else 'other.com','status':'active','account':{'id':'a'*32}}
    monkeypatch.setattr(service.cf,'request',request)
    preview=service.cf.plan();assert {d['zone_id'] for d in preview['dns']}==set(dns)
    assert service.cf.apply(preview['revision'])['verified']
    assert writes==['b'*32,'c'*32]
    assert remote['config']['ingress'][0]['hostname']=='unrelated.example.com'
    service.cf.apply(service.cf.plan()['revision'])
    assert len(writes)==2
    previous = next(site for site in service.sites() if site['hostname'] == hosts[1])
    renamed = 'other.com' if hosts[1] != 'other.com' else 'www.other.com'
    changed = service.save_site(previous | {'hostname': renamed})
    assert changed['id'] == previous['id'] and changed['zone_id'] == previous['zone_id']
    assert service.cf.apply(service.cf.plan()['revision'])['verified']
    actual_hosts = [row.get('hostname') for row in remote['config']['ingress']]
    assert renamed in actual_hosts and hosts[1] not in actual_hosts
    assert 'unrelated.example.com' in actual_hosts
    assert hosts[1] not in service.store.get('published_hosts')
    assert any(row['name'] == hosts[1] for row in dns['c'*32])  # Old DNS is retained; only platform routes are removed.


def test_zone_list_change_invalidates_publication_revision(service,monkeypatch):
    mock_zones(service,monkeypatch)
    cfg=service.settings()|{'tunnel_id':'11111111-1111-1111-1111-111111111111'}
    service.store.set('settings',cfg);service.store.set('owned_tunnel',cfg['tunnel_id'])
    orig=service.cf.request
    monkeypatch.setattr(service.cf,'request',lambda method,path,body=None:{'config':{'ingress':[{'service':'http_status:404'}]},'version':1} if path.endswith('/configurations') else orig(method,path,body))
    preview=service.cf.plan();service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    with pytest.raises(ValueError,match='已经变化'):service.cf.apply(preview['revision'])


def test_api_preserves_zones_on_partial_settings_and_protects_mutations(service,monkeypatch):
    mock_zones(service,monkeypatch)
    token,csrf=service.store.login()
    client=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',cookies={'lb_admin':token})
    headers={'Origin':'http://127.0.0.1:8890','X-CSRF-Token':csrf}
    assert client.post('/api/zones',json={'zone_id':'c'*32,'zone_name':'other.com'}).status_code==403
    assert client.post('/api/zones',headers=headers,json={'zone_id':'c'*32,'zone_name':'other.com'}).status_code==200
    data=service.settings();data.pop('zones');data['tunnel_name']='test'
    assert client.post('/api/settings',headers=headers,json=data).status_code==200
    assert len(service.settings()['zones'])==2
    data['zones']=[]
    assert client.post('/api/settings',headers=headers,json=data).status_code==400


def test_token_policies_cover_only_attached_zones(service,monkeypatch):
    mock_zones(service,monkeypatch);service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    policy=TokenManager.policies(groups(),service.settings(),False)[1]
    assert policy['resources']=={'com.cloudflare.api.account.zone.'+z:'*' for z in ('b'*32,'c'*32)}


def test_available_zones_paginate_and_exclude_other_accounts(service,monkeypatch):
    pages=[]
    def request(method,path,body=None):
        page=int(parse_qs(urlsplit(path).query)['page'][0]);pages.append(page)
        if page==1:
            return [{'id':format(i,'032x'),'name':f'domain{i}.com','status':'active','account':{'id':'a'*32}} for i in range(1,51)]
        return [{'id':'c'*32,'name':'foreign.com','status':'active','account':{'id':'d'*32}}, {'id':'e'*32,'name':'pending.com','status':'pending','account':{'id':'a'*32}}]
    monkeypatch.setattr(service.cf,'request',request)
    zones=service.cf.available_zones()
    assert pages==[1,2] and len(zones)==50
    assert not any(z['zone_name'] in ('foreign.com','pending.com') for z in zones)


def test_second_zone_can_use_human_verification_with_both_domains(service,monkeypatch):
    mock_zones(service,monkeypatch);service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    seen=[]
    monkeypatch.setattr(service.cf,'create_widget',lambda sites=None:seen.extend(s['hostname'] for s in sites or []))
    first=service.save_site({'name':'first','hostname':'app.example.com','origin':'http://127.0.0.1:9300','human_check':True},synchronize_verification=True)
    second=service.save_site({'name':'second','hostname':'app.other.com','origin':'http://127.0.0.1:9300','human_check':True},synchronize_verification=True)
    assert seen[-2:]==[first['hostname'],second['hostname']]
    service.save_site(second|{'name':'edited'},synchronize_verification=True)
    assert service.sites()[-1]['zone_id']=='c'*32


def test_full_zone_list_cannot_overflow_when_primary_is_automatically_added(service):
    from test_security import admin_client
    zones = [{"zone_id":"b"*32,"zone_name":"example.com"}] + [
        {"zone_id":format(i,"032x"),"zone_name":f"zone{i}.example.com"} for i in range(1,100)]
    settings = service.settings() | {"zones":zones}
    service.store.set("settings", settings)
    assert len(Settings(**settings).model_dump()["zones"]) == 100
    owner = admin_client(service)
    response = owner.post("/api/settings", json=settings | {"zone_id":"f"*32,"zone_name":"new.example.com"})
    assert response.status_code == 400
    assert service.store.get("settings") == settings
    assert owner.get("/api/bootstrap").status_code == 200
    with pytest.raises(ValueError):
        Settings(**(settings | {"zone_id":"f"*32,"zone_name":"new.example.com"}))

def test_site_save_distinguishes_zone_validation_failures(service, monkeypatch):
    mock_zones(service, monkeypatch)
    service.add_zone({'zone_id': 'c'*32, 'zone_name': 'other.com'})
    body = {'name': 'secondary', 'hostname': 'app.other.com', 'origin': 'http://127.0.0.1:9300', 'human_check': False}
    site = service.save_site(body | {'zone_id': 'c'*32})
    assert site['hostname'] == 'app.other.com' and site['zone_id'] == 'c'*32
    assert service.save_site(site | {'name': 'edited'})['zone_id'] == 'c'*32
    cases = [
        ({'hostname': 'new.other.com', 'zone_id': 'b'*32}, '不属于所选域名 example.com'),
        ({'hostname': 'new.other.com', 'zone_id': 'd'*32}, '已不在已接入列表'),
    ]
    before = service.sites()
    for change, message in cases:
        with pytest.raises(ValueError, match=message):
            service.save_site(body | change)
        assert service.sites() == before

def test_apex_matching_preserves_zone_boundary_and_published_removal_guard(service, monkeypatch):
    mock_zones(service, monkeypatch)
    service.add_zone({'zone_id': 'c'*32, 'zone_name': 'other.com'})
    body = {'name': 'Official site', 'hostname': 'other.com', 'origin': 'http://127.0.0.1:9300', 'human_check': False}
    assert service.save_site(body | {'zone_id': 'c'*32})['zone_id'] == 'c'*32
    with pytest.raises(ValueError, match='不属于所选域名'):
        service.save_site(body | {'hostname': 'example.com', 'zone_id': 'c'*32})
    with pytest.raises(ValueError):
        service.save_site(body | {'hostname': 'notother.com', 'zone_id': 'c'*32})
    service.store.set('sites', [])
    service.store.set('published_hosts', ['other.com'])
    with pytest.raises(ValueError, match='仍被'):
        service.remove_zone('c'*32)


def test_legacy_apex_site_keeps_default_zone_and_specific_zone_wins(service):
    service.store.set('sites', [{'id': 'legacy-root', 'hostname': 'example.com'}])
    assert service.sites()[0]['zone_id'] == 'b'*32
    cfg=service.settings()
    cfg['zones'].append({'zone_id': 'c'*32, 'zone_name': 'sub.example.com'})
    service.store.set('settings', cfg)
    assert service.site_zone({'hostname': 'sub.example.com'})['zone_id'] == 'c'*32
    assert service.site_zone({'hostname': 'app.sub.example.com'})['zone_id'] == 'c'*32

def test_hostname_edit_preserves_identity_secret_and_policy_and_rejects_duplicates(service, monkeypatch):
    mock_zones(service, monkeypatch)
    service.add_zone({'zone_id':'c'*32, 'zone_name':'other.com'})
    old = service.save_site({'name':'Official site', 'hostname':'app.other.com', 'origin':'http://127.0.0.1:9300', 'human_check':False, 'passcode_required':True, 'passcode':'test-only-secret-123', 'allowed_countries':['CN'], 'paused':True})
    secret = service.store.secret('passcode_' + old['id'])
    changed = service.save_site(old | {'hostname':'other.com'})
    assert changed['id'] == old['id']
    assert changed['origin'] == old['origin'] and changed['allowed_countries'] == ['CN'] and changed['paused']
    assert service.store.secret('passcode_' + old['id']) == secret
    assert changed['policy_version'] != old['policy_version']
    moved = service.save_site({key:value for key,value in changed.items() if key!='zone_id'} | {'hostname':'official.example.com'})
    assert moved['zone_id'] == 'b'*32
    another = service.save_site({'name':'Other', 'hostname':'example.com', 'origin':'http://127.0.0.1:9300', 'human_check':False})
    before = service.sites()
    with pytest.raises(ValueError, match='域名已被另一个网站使用'):
        service.save_site(moved | {'hostname':another['hostname']})
    assert service.sites() == before
    with pytest.raises(ValueError):
        service.save_site(moved | {'hostname':'official.notexample.com'})
    assert service.sites() == before
