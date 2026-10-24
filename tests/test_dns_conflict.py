from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from lanbridge.admin import create_admin
from lanbridge.dns_conflict import DnsConflictError
from test_security import service, admin_client
from test_cloudflare import prepare
from test_site_publication import wait

OLD = '22222222-2222-2222-2222-222222222222'


def fixture(service, monkeypatch):
    remote, records, calls, site = prepare(service, monkeypatch)
    records.append({'id': 'old-dns', 'type': 'CNAME', 'name': site['hostname'],
                    'content': OLD + '.cfargotunnel.com', 'proxied': True, 'ttl': 1,
                    'comment': 'Keep this comment', 'tags': ['owner:example']})
    old = {'id': OLD, 'name': 'Old tunnel', 'status': 'down', 'connections': [], 'deleted_at': None}
    original = service.cf.request
    def request(method, path, body=None):
        if path.endswith('/cfd_tunnel/' + OLD):
            calls.append((method, path, None))
            return deepcopy(old)
        if method == 'PATCH' and '/dns_records/' in path:
            assert service.store.get('dns_migration_backups')[-1]['status'] == 'prepared'
            calls.append((method, path, deepcopy(body)))
            records[0].update(body)
            return deepcopy(records[0])
        return original(method, path, body)
    monkeypatch.setattr(service.cf, 'request', request)
    return remote, records, calls, site, old


def conflict(service):
    with pytest.raises(DnsConflictError) as error:
        service.cf.plan()
    return error.value.conflict


def test_offline_same_account_migration_preserves_record_and_verifies_routes(service, monkeypatch):
    remote, records, calls, site, old = fixture(service, monkeypatch)
    approved = conflict(service)
    assert approved['repairable'] and '已离线' in approved['detail']
    assert all(method == 'GET' for method, _, _ in calls)
    original = deepcopy(records[0])
    preview = service.cf.plan(dns_takeover=approved)
    assert all(method == 'GET' for method, _, _ in calls)
    assert service.cf.apply(preview['revision'], dns_takeover=approved)['verified']
    assert records[0]['id'] == original['id']
    assert records[0]['comment'] == original['comment'] and records[0]['tags'] == original['tags']
    backup = service.store.get('dns_migration_backups')[-1]
    assert backup['record'] == original and backup['status'] == 'verified'
    writes = [(m, p) for m, p, _ in calls if m != 'GET']
    assert len([x for x in writes if x[0] == 'PATCH']) == 1
    assert writes[0][0] == 'PUT'  # Publish and verify the new ingress before switching DNS.
    assert all('/cfd_tunnel/' + OLD not in path for _, path in writes)
    assert service.store.get('published_hosts') == [site['hostname']]


@pytest.mark.parametrize('change', ['healthy', 'connections', 'unknown', 'deleted', 'wrong_account', 'ordinary', 'multiple'])
def test_ineligible_conflicts_never_write(service, monkeypatch, change):
    _, records, calls, _, old = fixture(service, monkeypatch)
    if change == 'healthy': old['status'] = 'healthy'
    if change == 'connections': old['connections'] = [{'id': 'active'}]
    if change == 'unknown': old.pop('status')
    if change == 'deleted': old['deleted_at'] = '2026-01-01'
    if change == 'ordinary': records[0].update(type='A', content='192.0.2.1')
    if change == 'multiple': records.append(dict(records[0], id='second'))
    if change == 'wrong_account':
        previous = service.cf.request
        def denied(method, path, body=None):
            if path.endswith('/cfd_tunnel/' + OLD): raise RuntimeError('denied')
            return previous(method, path, body)
        monkeypatch.setattr(service.cf, 'request', denied)
    result = conflict(service)
    assert not result['repairable']
    assert all(method == 'GET' for method, _, _ in calls)
    with pytest.raises(DnsConflictError): service.cf.plan(dns_takeover=result)


@pytest.mark.parametrize('change', ['dns', 'site', 'status', 'account'])
def test_approved_conflict_revalidated_before_writes(service, monkeypatch, change):
    _, records, calls, site, old = fixture(service, monkeypatch)
    approved = conflict(service)
    preview = service.cf.plan(dns_takeover=approved)
    if change == 'dns': records[0]['comment'] = 'changed elsewhere'
    if change == 'site': service.save_site(site | {'origin': 'http://127.0.0.1:9302'})
    if change == 'status': old['status'] = 'healthy'
    if change == 'account': service.store.set('settings', service.settings() | {'account_id': 'b' * 32})
    with pytest.raises(ValueError): service.cf.apply(preview['revision'], dns_takeover=approved)
    assert all(method == 'GET' for method, _, _ in calls)
    assert not service.store.get('dns_migration_backups')


def test_backup_save_failure_prevents_dns_change(service, monkeypatch):
    _, records, calls, _, _ = fixture(service, monkeypatch)
    approved = conflict(service);preview = service.cf.plan(dns_takeover=approved)
    original = service.store.set
    def fail(key, value):
        if key == 'dns_migration_backups': raise OSError('disk full')
        return original(key, value)
    monkeypatch.setattr(service.store, 'set', fail)
    with pytest.raises(RuntimeError): service.cf.apply(preview['revision'], dns_takeover=approved)
    assert not any(method == 'PATCH' for method, _, _ in calls)
    assert records[0]['content'].startswith(OLD)


def test_unknown_patch_outcome_keeps_backup_and_normal_retry_reconciles(service, monkeypatch):
    _, records, calls, _, _ = fixture(service, monkeypatch)
    approved = conflict(service);preview = service.cf.plan(dns_takeover=approved)
    original = service.cf.request
    def timeout(method, path, body=None):
        result = original(method, path, body)
        if method == 'PATCH': raise RuntimeError('response lost')
        return result
    monkeypatch.setattr(service.cf, 'request', timeout)
    with pytest.raises(RuntimeError): service.cf.apply(preview['revision'], dns_takeover=approved)
    assert service.store.get('dns_migration_backups')[-1]['status'] == 'needs_recheck'
    assert service.cf.apply(service.cf.plan()['revision'])['verified']
    assert len([x for x in calls if x[0] == 'PATCH']) == 1
    assert service.store.get('dns_migration_backups')[-1]['status'] == 'verified'


def test_background_job_exposes_conflict_and_repairs_only_current_job(service, monkeypatch):
    fixture(service, monkeypatch)
    service.site_publication.submit();job = wait(service)
    assert job['phase'] == 'failed' and job['dns_conflict']['repairable']
    with pytest.raises(ValueError): service.site_publication.submit(repair_job='stale')
    service.site_publication.submit(repair_job=job['id'])
    assert wait(service)['phase'] == 'succeeded'
    assert service.store.get('dns_migration_backups')[-1]['status'] == 'verified'


def test_temporary_scope_cannot_migrate_dns_and_forged_options_rejected(service, monkeypatch):
    owner = admin_client(service)
    fixture(service, monkeypatch)
    grant = owner.post('/api/temporary-tokens', json={'name': 'limited'}).json()
    with TestClient(create_admin(service), base_url='http://127.0.0.1:8890', headers={'Authorization': 'Bearer ' + grant['token']}) as limited:
        response = limited.post('/api/sites', json={'background': True, 'retry_publication': True, 'repair_dns_conflict': 'fake'})
        assert response.status_code == 403
    assert owner.post('/api/sites', json={'repair_dns_conflict': 'fake'}).status_code == 400
    assert not service.store.get('dns_migration_backups')


def test_tunnel_recovers_during_route_publish_prevents_dns_switch(service, monkeypatch):
    _, records, calls, _, old = fixture(service, monkeypatch)
    approved = conflict(service);preview = service.cf.plan(dns_takeover=approved)
    original = service.cf.request
    def recovered(method, path, body=None):
        result = original(method, path, body)
        if method == 'PUT': old['status'] = 'healthy'
        return result
    monkeypatch.setattr(service.cf, 'request', recovered)
    with pytest.raises(RuntimeError): service.cf.apply(preview['revision'], dns_takeover=approved)
    assert not any(method == 'PATCH' for method, _, _ in calls)
    assert records[0]['content'].startswith(OLD)


def test_multiple_conflicts_collect_approvals_then_migrate_together(service, monkeypatch):
    _, records, calls, site, old = fixture(service, monkeypatch)
    second = service.save_site({'name': 'Second', 'hostname': 'second.example.com', 'origin': 'http://127.0.0.1:9302', 'human_check': False})
    records.append(dict(records[0], id='second-dns', name=second['hostname']))
    original = service.cf.request
    def by_host(method, path, body=None):
        if method == 'GET' and '/dns_records?name=' in path:
            host = path.split('?name=', 1)[1]
            calls.append((method, path, None))
            return deepcopy([record for record in records if record['name'] == host])
        if method == 'PATCH' and '/dns_records/' in path:
            assert service.store.get('dns_migration_backups')
            key = path.rsplit('/', 1)[1]
            record = next(record for record in records if record['id'] == key)
            record.update(body);calls.append((method, path, deepcopy(body)))
            return deepcopy(record)
        return original(method, path, body)
    monkeypatch.setattr(service.cf, 'request', by_host)
    service.site_publication.submit();first = wait(service)
    service.site_publication.submit(repair_job=first['id']);second_job = wait(service)
    assert second_job['phase'] == 'failed' and second_job['dns_conflict']['hostname'] == second['hostname']
    assert not any(method == 'PATCH' for method, _, _ in calls)
    service.site_publication.submit(repair_job=second_job['id'])
    assert wait(service)['phase'] == 'succeeded'
    assert len([entry for entry in calls if entry[0] == 'PATCH']) == 2
    assert all(record['content'] == service.settings()['tunnel_id'] + '.cfargotunnel.com' for record in records)


@pytest.mark.parametrize('all_unresolved', [False, True])
def test_backup_capacity_never_evicts_unresolved_records(service, monkeypatch, all_unresolved):
    _, records, calls, _, _ = fixture(service, monkeypatch)
    history = [{'revision': f'previous-{i}', 'hostname': f'old-{i}.example.com',
                'status': 'needs_recheck' if all_unresolved or i == 0 else 'verified',
                'record': {'id': str(i)}} for i in range(100)]
    service.store.set('dns_migration_backups', history)
    approved = conflict(service);preview = service.cf.plan(dns_takeover=approved)
    if all_unresolved:
        with pytest.raises(ValueError, match='备份'):
            service.cf.apply(preview['revision'], dns_takeover=approved)
        assert not any(method == 'PATCH' for method, _, _ in calls)
        assert service.store.get('dns_migration_backups') == history
    else:
        assert service.cf.apply(preview['revision'], dns_takeover=approved)['verified']
        retained = service.store.get('dns_migration_backups')
        assert len(retained) == 100
        assert history[0] in retained
        assert history[1] not in retained


@pytest.mark.parametrize('field', ['hostname', 'zone_id', 'target', 'record_id'])
def test_readback_does_not_resolve_unrelated_migration_backup(service, monkeypatch, field):
    fixture(service, monkeypatch)
    approved = conflict(service)
    assert service.cf.apply(service.cf.plan(dns_takeover=approved)['revision'], dns_takeover=approved)['verified']
    history = service.store.get('dns_migration_backups')
    history[0]['status'] = 'needs_recheck'
    if field == 'record_id': history[0]['record']['id'] = 'other-record'
    else: history[0][field] = 'other-context'
    service.store.set('dns_migration_backups', history)
    assert service.cf.apply(service.cf.plan()['revision'])['verified']
    assert service.store.get('dns_migration_backups') == history
