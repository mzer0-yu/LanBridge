import json
import sqlite3
import pytest
from lanbridge.store import Store
from test_security import service, admin_client


def test_pages(service):
    store=service.store
    for n in range(125): store.audit('event', {'number':n})
    assert store.audit_stats()['limit_mb']==10
    first=store.audit_list();second=store.audit_list(first[-1]['id'])
    assert len(first)==100 and len(second)==25
    assert first[0]['detail']['number']==124 and second[-1]['detail']['number']==0
    assert not set(r['id'] for r in first)&set(r['id'] for r in second)


def test_small_appends_do_not_scan_history(service):
    queries=[]
    service.store.db.set_trace_callback(queries.append)
    try:
        for n in range(10):
            service.store.audit('small', {'number':n})
    finally:
        service.store.db.set_trace_callback(None)
    assert not any('DELETE FROM logs.audit' in q or 'OVER (ORDER BY' in q for q in queries)
    assert len(service.store.audit_list())==10


def test_lowering_limit_trims_existing_history_immediately(service):
    store=service.store
    for n in range(18):
        store.audit('large', {'number':n,'payload':'x'*100000})
    assert store.audit_stats()['size_bytes']>1024*1024
    store.set_audit_limit(1)
    assert store.audit_stats()['size_bytes']<=1024*1024
    records=[row for row in store.audit_list() if row['action']=='large']
    assert records[0]['detail']['number']==17
    assert len(records)<18


def test_physical_cap(service):
    store=service.store
    store.set('preserved', {'port':8891});store.set_secret('preserved','test-secret')
    store.set_audit_limit(1)
    for n in range(18): store.audit('large', {'number':n,'payload':'测'*40000})
    assert (store.root/'log.sqlite').stat().st_size<=1024*1024
    rows=store.audit_list()
    assert rows[0]['detail']['number']==17 and len(rows)<18
    assert store.get('preserved')=={'port':8891} and store.secret('preserved')=='test-secret'
    store.audit('oversized',{'payload':'x'*(1024*1024)})
    assert store.audit_list()[0]['detail']['truncated']
    assert store.audit_stats()['size_bytes']<=1024*1024


def test_migration_restart(service):
    root=service.store.root;service.store.db.close()
    db=sqlite3.connect(root/'state.sqlite')
    db.execute('CREATE TABLE audit(id INTEGER PRIMARY KEY, at REAL, action TEXT, detail TEXT)')
    db.execute('INSERT INTO audit VALUES (77,1,?,?)',('legacy',json.dumps({'legacy':True})))
    db.commit();db.close()
    store=Store(root)
    assert store.audit_list()[0]['action']=='legacy'
    assert not store.db.execute("SELECT name FROM main.sqlite_master WHERE name='audit'").fetchone()
    store.db.close();service.store=Store(root)
    assert service.store.audit_list()[0]['action']=='legacy'


def test_settings_permissions(service):
    owner=admin_client(service)
    for value in [0,-1,1025,True,'10',None]:
        assert owner.post('/api/audit/settings',json={'limit_mb':value}).status_code==400
    assert owner.post('/api/audit/settings',json={'limit_mb':10}).status_code==200
    assert owner.get('/api/audit').status_code==200
    token=owner.post('/api/temporary-tokens',json={'permissions':['sites','account']}).json()['token']
    headers={'Authorization':'Bearer '+token}
    assert owner.get('/api/audit',headers=headers).status_code==403
    assert owner.post('/api/audit/settings',json={'limit_mb':1},headers=headers).status_code==403


def test_settings_reject_invalid_or_oversized_body(service):
    owner = admin_client(service)
    for payload in ['[]', 'null', '{', '{"padding":"' + 'x' * 20000 + '","limit_mb":1}']:
        response = owner.post('/api/audit/settings', content=payload,
                              headers={'Content-Type': 'application/json'})
        assert response.status_code == 400
        assert service.store.audit_stats()['limit_mb'] == 10


def test_export_snapshot_auth_and_no_vault(service):
    from fastapi.testclient import TestClient
    from lanbridge.admin import create_admin
    owner=admin_client(service)
    service.store.set_secret('export-test','never-export-this-secret')
    for n in range(125): service.store.audit('export-event',{'number':n,'name':'测试'})
    response=owner.get('/api/audit/export')
    assert response.status_code==200
    assert response.headers['content-type'].startswith('application/x-ndjson')
    assert response.headers['cache-control']=='no-store'
    import re
    assert re.fullmatch(r'attachment; filename="lanbridge-log-\d{8}-\d{6}\.jsonl"', response.headers['content-disposition'])
    rows=[json.loads(line) for line in response.text.splitlines()]
    events=[row for row in rows if row['action']=='export-event']
    assert len(events)==125 and events[0]['detail']['number']==0 and events[-1]['detail']['number']==124
    assert 'never-export-this-secret' not in response.text
    assert TestClient(create_admin(service),base_url='http://127.0.0.1:8890').get('/api/audit/export').status_code==401
    token=owner.post('/api/temporary-tokens',json={'permissions':['sites','account']}).json()['token']
    assert owner.get('/api/audit/export',headers={'Authorization':'Bearer '+token}).status_code==403


def test_legacy_log_filename_migration(service):
    root=service.store.root
    service.store.audit('rename_test',{'preserved':True})
    service.store.set_audit_limit(5)
    service.store.db.close()
    (root/'log.sqlite').replace(root/'audit.sqlite')
    service.store=Store(root)
    assert (root/'log.sqlite').exists() and not (root/'audit.sqlite').exists()
    assert any(row['action']=='rename_test' for row in service.store.audit_list())
    assert service.store.audit_stats()['limit_mb']==5


def test_large_export_snapshot_does_not_buffer_whole_history(service):
    import tracemalloc
    store=service.store
    payload=json.dumps({'payload':'x'*32768})
    with store.lock,store.db:
        store.db.executemany('INSERT INTO logs.audit(at,action,detail) VALUES (?,?,?)',
                             [(1,'large-export',payload)]*200)
    tracemalloc.start()
    try:
        snapshot=store.audit_export()
        _,peak=tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    try:
        assert peak<3*1024*1024
        store.audit('after-snapshot',{})
        records=[json.loads(line) for line in snapshot]
        assert len(records)==200 and all(row['action']=='large-export' for row in records)
    finally:
        snapshot.close()


def test_export_response_closes_snapshot_after_success_and_disconnect(service,monkeypatch):
    import asyncio
    from lanbridge.admin import create_admin
    from starlette.requests import ClientDisconnect
    owner=admin_client(service)
    snapshots=[];original=service.store.audit_export
    def capture():
        snapshot=original();snapshots.append(snapshot);return snapshot
    monkeypatch.setattr(service.store,'audit_export',capture)
    assert owner.get('/api/audit/export').status_code==200
    assert snapshots[-1].closed
    endpoint=next(route.endpoint for route in create_admin(service).routes if getattr(route,'path',None)=='/api/audit/export')
    response=endpoint()
    async def run():
        async def receive():
            await asyncio.Event().wait()
        async def send(message):
            if message['type']=='http.response.body':raise OSError('disconnected')
        await response({'type':'http','method':'GET','asgi':{'spec_version':'2.4'}},receive,send)
    with pytest.raises((OSError,ClientDisconnect)):
        asyncio.run(run())
    assert snapshots[-1].closed


def test_temporary_state_skips_log_queries(service,monkeypatch):
    owner=admin_client(service)
    token=owner.post('/api/temporary-tokens',json={'permissions':['sites','account']}).json()['token']
    def forbidden(*args,**kwargs):
        pytest.fail('Temporary state must not read log history or statistics')
    monkeypatch.setattr(service.store,'audit_list',forbidden)
    monkeypatch.setattr(service.store,'audit_stats',forbidden)
    response=owner.get('/api/state',headers={'Authorization':'Bearer '+token})
    assert response.status_code==200
    assert response.json()['audit']==[] and response.json()['audit_storage']=={}


def test_large_export_uses_protected_storage_and_removes_temporary_file(service):
    from pathlib import Path
    store = service.store
    payload = json.dumps({"payload": "x" * 32768})
    with store.lock, store.db:
        store.db.executemany("INSERT INTO logs.audit(at,action,detail) VALUES (?,?,?)", [(1, "export", payload)] * 40)
    snapshot = store.audit_export()
    try:
        assert snapshot._rolled
        temporary = Path(snapshot.name)
        assert temporary.parent.resolve() == store.root.resolve()
        assert temporary.exists()
        assert len([json.loads(line) for line in snapshot]) == 40
    finally:
        snapshot.close()
    assert not temporary.exists()


def test_remote_audit_is_read_only_even_with_forged_local_headers(service):
    from test_remote_admin import remote_site, remote_client
    remote_site(service)
    service.store.audit("preserved_evidence", {"number": 42})
    with remote_client(service) as client:
        login = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"})
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        for method, path, payload in [
            ("POST", "/api/audit/settings", '{"limit_mb":1}'),
            ("POST", "/api/audit/settings", '{"limit_mb":20}'),
            ("POST", "/api/audit/settings", '{'),
            ("POST", "/api/audit/clear", '{}'),
            ("DELETE", "/api/audit", '{}'),
            ("PUT", "/api/audit/settings", '{}'),
        ]:
            response = client.request(method, path, content=payload, headers={
                "Content-Type": "application/json", "X-Forwarded-For": "127.0.0.1",
                "CF-Connecting-IP": "127.0.0.1", "X-Forwarded-Host": "localhost:8890"})
            assert response.status_code == 403
            assert "本机管理员" in response.json()["detail"]
            assert response.headers["cache-control"] == "no-store"
        assert service.store.audit_stats()["limit_mb"] == 10
        rows = client.get("/api/audit").json()["records"]
        assert any(row["action"] == "preserved_evidence" for row in rows)
        assert sum(row["action"] == "audit_write_rejected" for row in rows) == 1
        exported = client.get("/api/audit/export")
        assert exported.status_code == 200 and "preserved_evidence" in exported.text
    owner = admin_client(service)
    assert owner.post("/api/audit/settings", json={"limit_mb":20}).status_code == 200
