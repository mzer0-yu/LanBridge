from copy import deepcopy
import pytest
from test_security import service


def prepare(service, monkeypatch):
    cfg = service.settings()
    cfg["tunnel_id"] = "11111111-1111-1111-1111-111111111111"
    service.store.set("settings", cfg)
    service.store.set("owned_tunnel", cfg["tunnel_id"])
    site = service.save_site({"name": "test", "hostname": "app.example.com", "origin": "http://127.0.0.1:9300", "human_check": False})
    remote = {"config": {"ingress": [{"hostname": "other.example.com", "service": "http://127.0.0.1:9301", "originRequest": {"httpHostHeader": "custom"}}, {"service": "http_status:404"}], "warp-routing": {"enabled": False}}, "version": 1}
    records = []
    calls = []
    def request(method, path, body=None):
        calls.append((method, path, deepcopy(body)))
        if "/zones/" in path and "/dns_records" not in path:
            return {"name": "example.com", "status": "active", "account": {"id": "a" * 32}}
        if path.endswith("/configurations"):
            if method == "PUT":
                remote["config"] = deepcopy(body["config"])
                remote["version"] += 1
            return deepcopy(remote)
        if "/dns_records" in path:
            if method == "POST": records.append({"id": "dns-1", **body})
            return deepcopy(records[-1] if method == "POST" else records)
        raise AssertionError((method, path))
    monkeypatch.setattr(service.cf, "request", request)
    return remote, records, calls, site


def test_preview_preserves_unrelated_routes_and_apply_verifies(service, monkeypatch):
    remote, records, calls, site = prepare(service, monkeypatch)
    before = deepcopy(remote)
    plan = service.cf.plan()
    assert all(method == "GET" for method, _, _ in calls)
    assert plan["after"]["ingress"][0] == before["config"]["ingress"][0]
    assert plan["after"]["warp-routing"] == {"enabled": False}
    assert plan["after"]["ingress"][-1] == {"service": "http_status:404"}
    result = service.cf.apply(plan["revision"])
    assert result["verified"] and result["restart_required"] is False
    assert remote["config"] == plan["after"]
    assert records[0]["content"].endswith(".cfargotunnel.com")
    calls.clear()
    plan = service.cf.plan()
    service.cf.apply(plan["revision"])
    assert all(method == "GET" for method, _, _ in calls)


def test_first_publish_accepts_cloudflare_materialized_default_and_rejects_drift(service, monkeypatch):
    remote, records, calls, site = prepare(service, monkeypatch)
    remote["config"] = None
    original = service.cf.request
    drift = [False]
    def request(method, path, body=None):
        result = original(method, path, body)
        if method == "PUT" and path.endswith("/configurations"):
            remote["config"]["warp-routing"] = {"enabled": False}
            if drift[0]:
                remote["config"]["ingress"][0]["service"] = "http://127.0.0.1:9999"
        return result
    monkeypatch.setattr(service.cf, "request", request)
    assert service.cf.apply(service.cf.plan()["revision"])["verified"]
    assert service.store.get("published_hosts") == [site["hostname"]]
    assert len(records) == 1
    remote["config"] = None
    drift[0] = True
    with pytest.raises(RuntimeError, match="无法核验"):
        service.cf.apply(service.cf.plan()["revision"])
    assert len(records) == 1


def test_apply_rejects_remote_and_local_drift(service, monkeypatch):
    remote, records, calls, site = prepare(service, monkeypatch)
    plan = service.cf.plan()
    remote["version"] += 1
    with pytest.raises(ValueError, match="已经变化"):
        service.cf.apply(plan["revision"])
    assert all(method == "GET" for method, _, _ in calls)
    plan = service.cf.plan()
    service.save_site(site | {"origin": "http://127.0.0.1:9302"})
    with pytest.raises(ValueError, match="已经变化"):
        service.cf.apply(plan["revision"])


def test_conflicting_dns_and_unmanaged_tunnel_rejected(service, monkeypatch):
    remote, records, calls, site = prepare(service, monkeypatch)
    records.append({"id": "conflict", "type": "A", "content": "1.1.1.1", "proxied": True})
    with pytest.raises(ValueError, match="冲突 DNS"):
        service.cf.plan()
    assert all(method == "GET" for method, _, _ in calls)
    service.store.set("owned_tunnel", "not-owned")
    with pytest.raises(ValueError, match="独立 Tunnel"):
        service.cf.plan()


def test_disabled_site_removes_only_its_route(service, monkeypatch):
    remote, records, calls, site = prepare(service, monkeypatch)
    service.cf.apply(service.cf.plan()["revision"])
    service.save_site(site | {"enabled": False})
    plan = service.cf.plan()
    assert len(plan["after"]["ingress"]) == 2
    assert plan["after"]["ingress"][0]["hostname"] == "other.example.com"
    service.cf.apply(plan["revision"])
    assert records  # DNS stays registered, but the ingress now denies the disabled site.


def test_partial_failure_does_not_claim_success(service, monkeypatch):
    remote, records, calls, site = prepare(service, monkeypatch)
    plan = service.cf.plan()
    original = service.cf.request
    def failing(method, path, body=None):
        if method == "PUT":
            raise RuntimeError("network timeout")
        return original(method, path, body)
    monkeypatch.setattr(service.cf, "request", failing)
    with pytest.raises(RuntimeError, match="部分变更"):
        service.cf.apply(plan["revision"])
    assert records
    assert service.store.audit_list()[0]["action"] == "publish_incomplete"


def test_tunnel_create_unknown_outcome_recovers_without_duplicate(service, monkeypatch):
    created = []
    def request(method, path, body=None):
        if "/zones/" in path:
            return {"name": "example.com", "status": "active", "account": {"id": "a" * 32}}
        if path.endswith("/token"):
            return "fake-local-connector-token"
        if method == "POST":
            created.append({"id": "11111111-1111-1111-1111-111111111111", "name": body["name"]})
            raise RuntimeError("response lost after cloud commit")
        return created
    monkeypatch.setattr(service.cf, "request", request)
    with pytest.raises(RuntimeError):
        service.cf.create_tunnel()
    result = service.cf.create_tunnel()
    assert result["token_saved"]
    assert len(created) == 1
    assert service.store.get("owned_tunnel") == result["id"]


def test_plan_requires_turnstile_hostname_registration(service, monkeypatch):
    remote, records, calls, site = prepare(service, monkeypatch)
    cfg = service.settings()
    cfg["turnstile_sitekey"] = "key"
    service.store.set("settings", cfg)
    service.store.set_secret("turnstile_secret", "dummy-turnstile-secret")
    service.save_site(site | {"human_check": True})
    request = service.cf.request
    def fake(method, path, body=None):
        if "/challenges/widgets/" in path:
            return {"domains": ["other.example.com"]}
        return request(method, path, body)
    monkeypatch.setattr(service.cf, "request", fake)
    with pytest.raises(ValueError, match="未包含全部"):
        service.cf.plan()


def test_widget_creation_is_atomic_idempotent_and_recovers_unknown_result(service,monkeypatch):
    site=service.save_site({"name":"human","hostname":"app.example.com","origin":"http://127.0.0.1:9300","human_check":True})
    widgets=[];calls=[];fail=[True]
    def request(method,path,body=None):
        calls.append(method)
        if '/zones/' in path:return {"name":"example.com","status":"active","account":{"id":"a"*32}}
        if method=='POST':
            widget={**body,"sitekey":"0x4-widget-site-key","secret":"widget-private-secret"};widgets.append(widget)
            if fail[0]:fail[0]=False;raise RuntimeError('network timeout after commit')
            return widget
        if '?' in path:return widgets
        if method=='GET':return widgets[0]
        if method=='PUT':widgets[0].update(body);return widgets[0]
        raise AssertionError(method)
    monkeypatch.setattr(service.cf,'request',request)
    with pytest.raises(RuntimeError):service.cf.create_widget()
    assert service.store.get('pending_widget_create') and not service.store.secret('turnstile_secret')
    result=service.cf.create_widget()
    assert result['saved'] and service.store.secret('turnstile_secret')=='widget-private-secret'
    assert 'widget-private-secret' not in str(result)+str(service.store.audit_list())
    assert service.store.get('pending_widget_create') is None and calls.count('POST')==1
    service.cf.create_widget();assert calls.count('POST')==1 and 'PUT' not in calls
    service.save_site({"name":"human2","hostname":"second.example.com","origin":"http://127.0.0.1:9301","human_check":True})
    service.cf.create_widget();assert calls.count('POST')==1 and 'second.example.com' in widgets[0]['domains']


def test_widget_missing_secret_does_not_claim_configuration_complete(service,monkeypatch):
    service.save_site({"name":"human","hostname":"app.example.com","origin":"http://127.0.0.1:9300","human_check":True})
    def request(method,path,body=None):
        if '/zones/' in path:return {"name":"example.com","status":"active","account":{"id":"a"*32}}
        return {"sitekey":"0x4-widget-site-key","domains":["app.example.com"]}
    monkeypatch.setattr(service.cf,'request',request)
    with pytest.raises(RuntimeError,match='密钥'):service.cf.create_widget()
    assert not service.settings()['turnstile_sitekey'] and service.store.get('pending_widget_create')['sitekey']=='0x4-widget-site-key'


def test_website_api_automatically_prepares_verification_and_disabling_is_local(service, monkeypatch):
    from test_security import admin_client
    widgets = []
    calls = []
    def request(method, path, body=None):
        calls.append((method, path))
        if '/zones/' in path:
            return {"name": "example.com", "status": "active", "account": {"id": "a" * 32}}
        if method == 'POST':
            widgets.append(body | {"sitekey": "owned-widget", "secret": "private-widget-secret"})
        elif method == 'PUT':
            widgets[0].update(body)
        return widgets[0]
    monkeypatch.setattr(service.cf, 'request', request)
    client = admin_client(service)
    response = client.post('/api/sites', json={"name": "First", "hostname": "first.example.com", "origin": "http://127.0.0.1:9300", "human_check": True})
    assert response.status_code == 200
    first = response.json()
    assert widgets[0]['domains'] == ['first.example.com']
    assert service.store.secret('turnstile_secret') == 'private-widget-secret'
    response = client.post('/api/sites', json={"name": "Second", "hostname": "second.example.com", "origin": "http://127.0.0.1:9301", "human_check": True})
    assert response.status_code == 200
    assert widgets[0]['domains'] == ['first.example.com', 'second.example.com']
    # Removing the local requirement must work even while Cloudflare is unavailable.
    def unavailable(*args, **kwargs):
        raise RuntimeError('Cloudflare unavailable')
    monkeypatch.setattr(service.cf, 'request', unavailable)
    response = client.post('/api/sites', json=first | {"human_check": False})
    assert response.status_code == 200 and not response.json()['human_check']
    disabled = response.json()
    response = client.post('/api/sites', json=disabled | {"human_check": True, "origin": "http://127.0.0.1:9302"})
    assert response.status_code == 400 and '未保存' in response.json()['detail']
    assert next(s for s in service.sites() if s['id'] == first['id']) == disabled
    assert sum(method == 'POST' for method, _ in calls) == 1
