from copy import deepcopy
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, quote

import httpx
import pytest
from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from lanbridge.domain_onboarding import Aliyun, DomainOnboarding, dns_record
from test_security import service, admin_client

DOMAIN = 'new.example.net'
OLD = ['dns1.hichina.com', 'dns2.hichina.com']
NEW = ['one.ns.cloudflare.com', 'two.ns.cloudflare.com']
ZONE_ID = 'c' * 32


@pytest.fixture
def cloud(service, monkeypatch):
    manager = service.domain_onboarding
    monkeypatch.setattr(manager, 'resume', lambda: None)
    manager.save_credentials({'access_key_id': 'test-only-key', 'access_key_secret': 'test-only-secret'})
    state = {'ns': OLD[:], 'ds': [], 'records': [
        {'RecordId': '1', 'Type': 'A', 'RR': '@', 'Value': '192.0.2.1', 'TTL': 600, 'Status': 'ENABLE', 'Line': 'default'},
        {'RecordId': '2', 'Type': 'MX', 'RR': '@', 'Value': 'mail.example.net.', 'Priority': 10, 'TTL': 600, 'Status': 'ENABLE', 'Line': 'default'},
        {'RecordId': '3', 'Type': 'TXT', 'RR': '@', 'Value': 'v=spf1 -all', 'TTL': 600, 'Status': 'ENABLE', 'Line': 'default'}],
        'target': [], 'zone': {'id': ZONE_ID, 'name': DOMAIN, 'account': {'id': 'a' * 32}, 'name_servers': NEW[:], 'type': 'full', 'status': 'pending'},
        'registry': {'UpdateProhibitionLock':'CLOSE','ExpirationDateStatus':'1','DomainStatus':'3','TransferOutStatus':'NORMAL','RegistrantUpdatingStatus':'NORMAL','EmailVerificationClientHold':False}, 'created': False, 'calls': [], 'task_status': 2, 'fail_import': False, 'uncertain': False}
    def ali(product, action, **params):
        state['calls'].append((product, action, deepcopy(params)))
        if action == 'QueryDomainByDomainName':
            return {'DomainName': DOMAIN, 'DnsList': {'Dns': [','.join(state['ns'])]}, **state['registry']}
        if action == 'QueryDSRecord':
            return {'DSRecordList': deepcopy(state['ds'])}
        if action == 'DescribeDomainRecords':
            start = (params['PageNumber'] - 1) * params['PageSize']
            return {'TotalCount': len(state['records']), 'DomainRecords': {'Record': deepcopy(state['records'][start:start + params['PageSize']])}}
        if action == 'SaveBatchTaskForModifyingDomainDns':
            assert params['AliyunDns'] == 'false' and params['DomainName.1'] == DOMAIN
            assert [params['DomainNameServer.1'], params['DomainNameServer.2']] == NEW
            if state['uncertain']:
                raise RuntimeError('network failure')
            return {'TaskNo': 'test-task'}
        if action == 'QueryTaskDetailList':
            return {'Data': {'TaskDetail': [{'DomainName': DOMAIN, 'TaskType': 'CHG_DNS', 'TaskNo': 'test-task', 'TaskStatusCode': state['task_status']}]}}
        raise AssertionError(action)
    def cf(method, path, body=None, *, force_write=False):
        assert force_write
        state['calls'].append(('cf', method, path))
        if path.startswith('/zones?'):
            return [deepcopy(state['zone'])] if state['created'] else []
        if path == '/zones' and method == 'POST':
            state['created'] = True
            return deepcopy(state['zone'])
        if path == '/zones/' + ZONE_ID:
            return deepcopy(state['zone'])
        if '/dns_records' in path:
            if method == 'GET':
                return deepcopy(state['target'])
            if state['fail_import']:
                raise RuntimeError('import denied')
            state['target'].append(deepcopy(body) | {'id': str(len(state['target']) + 1)})
            return deepcopy(state['target'][-1])
        raise AssertionError(path)
    monkeypatch.setattr(manager.aliyun, 'call', ali)
    monkeypatch.setattr(service.cf, 'request', cf)
    return manager, state


def writes(state):
    return [call for call in state['calls'] if call[:2] == ('domain', 'SaveBatchTaskForModifyingDomainDns')]


def confirm(manager):
    job = manager.status()['job']
    return manager.confirm(job['id'], job['domain'])


def test_prepare_does_not_change_dns_and_hides_credentials(cloud):
    manager, state = cloud
    result = manager.prepare(DOMAIN)
    assert result['job']['phase'] == 'preview' and state['created']
    assert state['target'] == [] and not writes(state)
    assert manager.backup()['source'] == state['records']
    assert 'test-only-key' not in json.dumps(result)
    assert 'context' not in result['job'] and 'source' not in result['job']


def test_confirmation_migrates_records_then_submits_once_and_attaches_on_active(cloud, service):
    manager, state = cloud
    manager.prepare(DOMAIN)
    assert confirm(manager)['job']['phase'] == 'waiting'
    assert len(state['target']) == 3 and len(writes(state)) == 1
    assert all(not row['proxied'] and row['ttl'] == 600 for row in state['target'])
    assert state['target'][1]['priority'] == 10
    assert confirm(manager)['job']['phase'] == 'waiting' and len(writes(state)) == 1
    assert manager.check()['job']['phase'] == 'waiting'
    state['zone']['status'] = 'active'
    assert manager.check()['job']['phase'] == 'waiting'
    state['ns'] = NEW[:]
    assert manager.check()['job']['phase'] == 'done'
    assert {'zone_id': ZONE_ID, 'zone_name': DOMAIN} in service.settings()['zones']
    assert manager.check()['job']['phase'] == 'done'
    assert manager.backup()['nameservers'] == OLD


@pytest.mark.parametrize('change', ['source', 'target', 'nameservers', 'context', 'expired'])
def test_preview_revalidation_blocks_changes(cloud, change):
    manager, state = cloud
    manager.prepare(DOMAIN)
    if change == 'source': state['records'][0]['Value'] = '192.0.2.2'
    elif change == 'target': state['target'].append({'type': 'TXT', 'name': DOMAIN, 'content': 'new', 'ttl': 600})
    elif change == 'nameservers': state['ns'] = ['another.hichina.com']
    elif change == 'context': manager.store.set_secret('cf_write_token', 'changed-token')
    else:
        job = manager._job(); job['created_at'] = time.time() - 1900; manager._save(job)
    with pytest.raises(ValueError): confirm(manager)
    assert not writes(state)


@pytest.mark.parametrize('change', ['dnssec', 'line', 'disabled', 'unknown', 'duplicate', 'provider', 'ttl'])
def test_unsupported_source_is_blocked_before_zone_creation(cloud, change):
    manager, state = cloud
    if change == 'dnssec': state['ds'] = [{'Digest': 'test'}]
    elif change == 'line': state['records'][0]['Line'] = 'telecom'
    elif change == 'disabled': state['records'][0]['Status'] = 'DISABLE'
    elif change == 'unknown': state['records'][0]['Type'] = 'URL'
    elif change == 'duplicate': state['records'][1]['RecordId'] = '1'
    elif change == 'provider': state['ns'] = ['ns.other.net']
    elif change == 'ttl': state['records'][0]['TTL'] = 30
    with pytest.raises(ValueError): manager.prepare(DOMAIN)
    assert not state['created'] and not writes(state)


@pytest.mark.parametrize('change', ['value', 'ttl', 'proxied', 'extra'])
def test_target_conflicts_block_prepare(cloud, change):
    manager, state = cloud
    row = dns_record(state['records'][0], DOMAIN)
    if change == 'value': row['content'] = '192.0.2.2'
    elif change == 'ttl': row['ttl'] = 300
    elif change == 'proxied': row['proxied'] = True
    else: row['name'] = 'extra.' + DOMAIN
    state['target'] = [row]
    with pytest.raises(ValueError): manager.prepare(DOMAIN)
    assert not writes(state)


def test_existing_subset_of_multivalue_rrset_can_resume_import(cloud):
    manager, state = cloud
    second = deepcopy(state['records'][0]) | {'RecordId': '4', 'Value': '192.0.2.2'}
    state['records'].append(second)
    state['target'] = [dns_record(state['records'][0], DOMAIN)]
    manager.prepare(DOMAIN)
    confirm(manager)
    assert len(state['target']) == 4 and len(writes(state)) == 1


def test_import_failure_keeps_original_ns_and_encrypted_backup(cloud):
    manager, state = cloud
    manager.prepare(DOMAIN); state['fail_import'] = True
    with pytest.raises(RuntimeError): confirm(manager)
    assert manager.status()['job']['phase'] == 'blocked' and not writes(state)
    assert manager.backup()['nameservers'] == OLD
    state['fail_import'] = False
    manager.prepare(DOMAIN); confirm(manager)
    assert len(writes(state)) == 1


def test_uncertain_submit_never_retries_and_can_finish_tracking(cloud):
    manager, state = cloud
    manager.prepare(DOMAIN); state['uncertain'] = True
    with pytest.raises(RuntimeError): confirm(manager)
    assert manager.status()['job']['phase'] == 'uncertain'
    confirm(manager)
    assert len(writes(state)) == 1
    with pytest.raises(ValueError): manager.cancel(manager.status()['job']['id'])
    job = manager.status()['job']
    assert manager.finish_tracking(job['id'], DOMAIN)['job']['phase'] == 'closed'
    assert len(writes(state)) == 1


def test_task_failure_does_not_attach_zone(cloud, service):
    manager, state = cloud
    manager.prepare(DOMAIN); confirm(manager); state['task_status'] = 3
    assert manager.check()['job']['phase'] == 'blocked'
    assert not any(zone['zone_id'] == ZONE_ID for zone in service.settings()['zones'])


def test_job_survives_manager_recreation_and_context_changes_block(cloud, service):
    manager, state = cloud
    manager.prepare(DOMAIN); confirm(manager)
    restored = DomainOnboarding(service)
    restored.aliyun = manager.aliyun
    assert restored.status() == manager.status()
    state['ns'] = NEW[:]; state['zone']['status'] = 'active'
    assert restored.check()['job']['phase'] == 'done'


def test_records_pagination_reads_complete_source(cloud):
    manager, state = cloud
    state['records'] = [deepcopy(state['records'][0]) | {'RecordId': str(i), 'RR': 'host' + str(i)} for i in range(205)]
    assert len(manager.prepare(DOMAIN)['job']['records']) == 205


def test_api_requires_local_admin_origin_csrf_and_returns_no_secrets(cloud, service):
    manager, state = cloud
    anonymous = TestClient(create_admin(service), base_url='http://127.0.0.1:8890')
    assert anonymous.get('/api/domain-onboarding').status_code == 401
    assert anonymous.get('/domain-onboarding.js').status_code == 200
    client = admin_client(service)
    assert client.post('/api/domain-onboarding/prepare', json={'domain': DOMAIN}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/domain-onboarding/prepare', json={'domain': DOMAIN}, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert client.post('/api/domain-onboarding/prepare', json={'domain': DOMAIN}).status_code == 200
    assert not writes(state)
    assert client.get('/api/domain-onboarding/backup').json()['nameservers'] == OLD
    assert 'test-only-secret' not in client.get('/api/domain-onboarding').text
    service.save_site({'name': 'admin', 'hostname': 'admin.example.com', 'origin': 'http://127.0.0.1:8890', 'target': 'lanbridge', 'human_check': False})
    remote = TestClient(create_admin(service, remote=True), base_url='https://admin.example.com')
    remote.cookies.update(client.cookies)
    assert remote.get('/api/domain-onboarding').status_code == 403


def test_temporary_cookie_denied_even_with_account_permission(cloud, service):
    manager, state = cloud
    client = admin_client(service)
    grant = client.post('/api/temporary-tokens', json={'name': 'test', 'permissions': ['account']}).json()
    client.cookies.clear()
    assert client.post('/api/token-login', json={'token': grant['token']}).status_code == 200
    assert client.get('/api/domain-onboarding').status_code in {401,403}
    assert not writes(state)


def test_rpc_signature_and_secret_sanitization(service, monkeypatch):
    manager = service.domain_onboarding
    manager.save_credentials({'access_key_id': 'test-access-id', 'access_key_secret': 'test-secret', 'security_token': 'test-sts'})
    def handler(request):
        assert str(request.url) == 'https://domain.aliyuncs.com/' and request.method == 'POST'
        params = {k: v[0] for k,v in parse_qs(request.content.decode()).items()}
        signature = params.pop('Signature')
        encode = lambda v: quote(v, safe='~')
        canonical = '&'.join(encode(k) + '=' + encode(params[k]) for k in sorted(params))
        expected = base64.b64encode(hmac.new(b'test-secret&', ('POST&%2F&' + encode(canonical)).encode(), hashlib.sha1).digest()).decode()
        assert signature == expected and params['SecurityToken'] == 'test-sts'
        assert 'test-secret' not in request.content.decode()
        return httpx.Response(403, json={'Code': 'Denied', 'Message': 'test-secret'})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(RuntimeError) as error: Aliyun(service.store).call('domain', 'QueryDomainByDomainName', DomainName=DOMAIN)
    assert 'test-secret' not in str(error.value)
    assert 'test-secret' not in json.dumps(manager.status())


@pytest.mark.parametrize('kind,value,expected', [('CAA','0 issue "letsencrypt.org"',{'flags':0,'tag':'issue','value':'letsencrypt.org'}),('SRV','10 5 443 service.example.net.',{'priority':10,'weight':5,'port':443,'target':'service.example.net'})])
def test_structured_record_preserves_values(kind,value,expected):
    row={'RecordId':'1','Type':kind,'RR':'_service._tcp' if kind=='SRV' else '@','Value':value,'TTL':600,'Status':'ENABLE','Line':'default'}
    assert dns_record(row,DOMAIN)['data']==expected


def test_background_waiter_stops_without_cloud_calls(cloud, monkeypatch):
    manager, state = cloud
    manager.prepare(DOMAIN); confirm(manager)
    monkeypatch.setattr(manager, 'resume', DomainOnboarding.resume.__get__(manager))
    count = len(state['calls'])
    manager.resume()
    assert manager.thread.is_alive()
    manager.stop()
    assert not manager.thread.is_alive() and len(state['calls']) == count
    with pytest.raises(RuntimeError): manager.check()


def test_oauth_token_rotation_keeps_binding_but_scope_changes_do_not(cloud):
    manager, state = cloud
    manager.store.set('managed_business_token', {'kind':'oauth','account_id':'a'*32,'zone_id':'b'*32,'scopes':['zone.read','dns.write']})
    manager.prepare(DOMAIN)
    manager.store.set_secret('cf_write_token','rotated-oauth-token')
    assert confirm(manager)['job']['phase']=='waiting'
    owned=manager.store.get('managed_business_token');owned['scopes'].append('zone.edit');manager.store.set('managed_business_token',owned)
    with pytest.raises(ValueError): manager.check()


def test_invalid_credentials_are_atomic_and_backup_is_not_plaintext(cloud):
    manager,state=cloud
    original=manager.store.secret('aliyun_access_key_id')
    with pytest.raises(ValueError): manager.save_credentials({'access_key_id':'new-key','access_key_secret':['invalid']})
    assert manager.store.secret('aliyun_access_key_id')==original
    manager.prepare(DOMAIN);confirm(manager)
    rows=str([tuple(row) for row in manager.store.db.execute('SELECT * FROM secrets')])
    assert 'test-only-secret' not in rows and 'v=spf1 -all' not in rows
    assert 'test-only-secret' not in str(manager.store.audit_list())


def test_lost_task_number_and_failed_response_never_repeat_submit(cloud,monkeypatch):
    manager,state=cloud
    manager.prepare(DOMAIN)
    original=manager.aliyun.call
    def no_number(product,action,**params):
        result=original(product,action,**params)
        return {} if action=='SaveBatchTaskForModifyingDomainDns' else result
    monkeypatch.setattr(manager.aliyun,'call',no_number)
    with pytest.raises(RuntimeError):confirm(manager)
    assert manager.status()['job']['phase']=='uncertain'
    assert confirm(manager)['job']['phase']=='uncertain' and len(writes(state))==1
    state['ns']=NEW[:];state['zone']['status']='active'
    assert manager.check()['job']['phase']=='done'


def test_temporary_bearer_cannot_read_backup_or_confirm(cloud,service):
    manager,state=cloud
    owner=admin_client(service)
    grant=owner.post('/api/temporary-tokens',json={'permissions':['account','sites']}).json()
    client=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Authorization':'Bearer '+grant['token']})
    for path in ['/api/domain-onboarding','/api/domain-onboarding/backup','/api/domain-onboarding/help']:
        assert client.get(path).status_code==403
    assert client.post('/api/domain-onboarding/confirm',json={'id':'test','confirmed_domain':DOMAIN}).status_code==403
    assert not writes(state)


@pytest.mark.parametrize('field,value',[('UpdateProhibitionLock','OPEN'),('UpdateProhibitionLock',None),('ExpirationDateStatus','2'),('DomainStatus','2'),('TransferOutStatus','PENDING'),('RegistrantUpdatingStatus','PENDING'),('EmailVerificationClientHold',True)])
def test_registry_recovery_states_block_before_any_zone_write(cloud,field,value):
    manager,state=cloud
    state['registry'][field]=value
    with pytest.raises(ValueError):manager.prepare(DOMAIN)
    assert not state['created'] and not writes(state)


def test_dismiss_failed_zone_creation_preserves_other_issues_and_audits(service):
    from lanbridge.service import digest
    key=digest(['POST','/zones']);other=digest(['POST','/accounts/test/cfd_tunnel'])
    service.store.set('cloudflare_permission_issues',{key:{'checked_at':123,'http_status':403},other:{'checked_at':124,'http_status':403,'detail':'其他失败','credential':'cf_write_token'}})
    manager=service.domain_onboarding
    with pytest.raises(ValueError,match='已变化'):manager.dismiss_prepare_failure(122)
    assert key in service.store.get('cloudflare_permission_issues')
    client=admin_client(service)
    assert client.post('/api/domain-onboarding/dismiss-error',json={'checked_at':123}).status_code==200
    assert key not in service.store.get('cloudflare_permission_issues') and other in service.store.get('cloudflare_permission_issues')
    assert any(row['action']=='domain_onboarding_failure_dismissed' for row in service.store.audit_list())


def test_dismiss_cannot_stop_active_migration(service,monkeypatch):
    manager=service.domain_onboarding
    monkeypatch.setattr(manager,'_job',lambda:{'phase':'waiting'})
    with pytest.raises(ValueError,match='已有域名接入任务'):manager.dismiss_prepare_failure(123)


def test_dismiss_requires_local_admin_and_csrf(service):
    client=TestClient(create_admin(service),base_url='http://127.0.0.1:8890',headers={'Origin':'http://127.0.0.1:8890'})
    assert client.post('/api/domain-onboarding/dismiss-error',json={'checked_at':123}).status_code==401
    owner=admin_client(service)
    assert owner.post('/api/domain-onboarding/dismiss-error',json={'checked_at':123},headers={'X-CSRF-Token':'invalid'}).status_code==403
    remote=TestClient(create_admin(service,remote=True),base_url='https://example.com')
    assert remote.post('/api/domain-onboarding/dismiss-error',json={'checked_at':123}).status_code==403

def test_domain_task_summary_is_local_admin_only(service, monkeypatch):
    owner = admin_client(service)
    monkeypatch.setattr(service.domain_onboarding, 'status', lambda: {'job': {'domain': DOMAIN, 'phase': 'preview', 'records': [{'content': 'private-record'}]}})
    response = owner.get('/api/state')
    assert response.status_code == 200
    assert response.json()['domain_onboarding'] == {'domain': DOMAIN, 'phase': 'preview'}
    remote = TestClient(create_admin(service, remote=True), base_url='https://admin.example.com')
    remote.cookies.set('lb_admin', owner.cookies.get('lb_admin'))
    with monkeypatch.context() as remote_context:
        remote_context.setattr(service, 'sites', lambda cfg=None: [{'id': 'test-admin-site', 'hostname': 'admin.example.com', 'enabled': True, 'target': 'lanbridge'}])
        response = remote.get('/api/state')
    assert response.status_code == 200
    assert response.json()['domain_onboarding'] is None
    grant = owner.post('/api/temporary-tokens', json={'permissions': ['account', 'sites']}).json()
    limited = TestClient(create_admin(service), base_url='http://127.0.0.1:8890', headers={'Authorization': 'Bearer ' + grant['token']})
    response = limited.get('/api/state')
    assert response.status_code == 200
    assert response.json()['domain_onboarding'] is None
