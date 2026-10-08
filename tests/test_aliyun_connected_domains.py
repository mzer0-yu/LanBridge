import pytest
from fastapi.testclient import TestClient

from lanbridge.admin import create_admin
from test_security import service, admin_client


def configure(service):
    service.store.set_secret('aliyun_access_key_id', 'test-key')
    service.store.set_secret('aliyun_access_key_secret', 'test-secret')
    cfg=service.settings()
    cfg['zones'] += [{'zone_id':'c'*32,'zone_name':'other.com'},{'zone_id':'d'*32,'zone_name':'foreign.com'}]
    service.store.set('settings',cfg)


def test_owned_attached_intersection_paginates_and_caches(service,monkeypatch):
    configure(service);manager=service.domain_onboarding;calls=[]
    def query(product,action,**params):
        calls.append((product,action,params))
        names=['OTHER.COM','unused.com'] if params['PageNum']==1 else ['example.com']
        return {'Data':{'Domain':[{'DomainName':name} for name in names]},'TotalPageNum':2}
    monkeypatch.setattr(manager,'_ali',query)
    before=service.settings(),service.sites(),service.store.get('published_hosts')
    result=manager.connected_domains()
    assert [d['zone_name'] for d in result['domains']]==['example.com','other.com']
    assert result['domains'][0]['default'] and not result['domains'][1]['default']
    assert len(calls)==2 and all(c[1]=='QueryDomainList' for c in calls)
    assert manager.connected_domains()==result and len(calls)==2
    manager.connected_domains(refresh=True);assert len(calls)==4
    assert before==(service.settings(),service.sites(),service.store.get('published_hosts'))
    assert 'test-secret' not in str(result)


@pytest.mark.parametrize('payload',[{}, {'Data':{},'TotalPageNum':1}, {'Data':{'Domain':'invalid'},'TotalPageNum':1}, {'Data':{'Domain':[{'DomainName':'bad'}]},'TotalPageNum':1}])
def test_invalid_inventory_is_not_an_empty_success(service,monkeypatch,payload):
    configure(service)
    monkeypatch.setattr(service.domain_onboarding,'_ali',lambda *a,**k:payload)
    with pytest.raises(ValueError,match='读取阿里云域名列表失败'):service.domain_onboarding.connected_domains()
    assert service.domain_onboarding.inventory_cache is None


def test_inventory_account_change_discards_old_response(service,monkeypatch):
    configure(service);manager=service.domain_onboarding
    def query(*args,**kwargs):
        service.store.set_secret('aliyun_access_key_id','another-account-key')
        return {'Data':{'Domain':[{'DomainName':'example.com'}]},'TotalPageNum':1}
    monkeypatch.setattr(manager,'_ali',query)
    with pytest.raises(ValueError,match='已变化'):manager.connected_domains()
    assert manager.inventory_cache is None


def test_inventory_refresh_failure_retains_cache_and_zone_change_invalidates(service,monkeypatch):
    configure(service);manager=service.domain_onboarding;calls=[]
    def query(*args,**kwargs):
        calls.append(kwargs)
        return {'Data':{'Domain':[{'DomainName':'example.com'}]},'TotalPageNum':1}
    monkeypatch.setattr(manager,'_ali',query)
    original=manager.connected_domains()
    monkeypatch.setattr(manager,'_ali',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('private details')))
    with pytest.raises(ValueError,match='读取阿里云域名列表失败'):manager.connected_domains(refresh=True)
    assert manager.connected_domains()==original
    monkeypatch.setattr(manager,'_ali',query)
    cfg=service.settings();cfg['zones'].append({'zone_id':'e'*32,'zone_name':'newly-attached.com'});service.store.set('settings',cfg)
    manager.connected_domains();assert len(calls)==2


def test_inventory_empty_and_no_authorization_do_not_query(service,monkeypatch):
    manager=service.domain_onboarding
    monkeypatch.setattr(manager,'_ali',lambda *a,**k:pytest.fail('no request expected'))
    assert manager.connected_domains()=={'configured':False,'domains':[],'checked_at':None}
    configure(service);service.store.set('settings',service.settings()|{'zone_id':'','zone_name':'','zones':[]})
    assert manager.connected_domains()['domains']==[]


def test_inventory_api_is_local_full_admin_only(service,monkeypatch):
    configure(service)
    monkeypatch.setattr(service.domain_onboarding,'_ali',lambda *a,**k:{'Data':{'Domain':[{'DomainName':'example.com'}]},'TotalPageNum':1})
    owner=admin_client(service);path='/api/domain-onboarding/connected-domains'
    result=owner.get(path)
    assert result.status_code==200 and result.headers['cache-control']=='no-store'
    assert result.json()['domains'][0]['zone_name']=='example.com'
    grant=owner.post('/api/temporary-tokens',json={'permissions':['sites','account']}).json()
    temporary=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Authorization':'Bearer '+grant['token']})
    assert temporary.get(path).status_code==403
    remote=TestClient(create_admin(service,remote=True),base_url='https://admin.example.com',cookies=owner.cookies)
    assert remote.get(path).status_code==403
