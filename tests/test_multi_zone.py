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
    with pytest.raises(ValueError,match='默认'):service.remove_zone('b'*32)
    service.store.set('sites',[])
    service.remove_zone('c'*32)
    assert len(service.settings()['zones'])==1


def test_multi_zone_dns_publish_and_readback_use_each_site_zone(service,monkeypatch):
    mock_zones(service,monkeypatch)
    service.add_zone({'zone_id':'c'*32,'zone_name':'other.com'})
    cfg=service.settings()|{'tunnel_id':'11111111-1111-1111-1111-111111111111'}
    service.store.set('settings',cfg);service.store.set('owned_tunnel',cfg['tunnel_id'])
    for host in ('app.example.com','app.other.com'):
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
                assert body['name'].endswith('.'+('example.com' if zid=='b'*32 else 'other.com'))
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
