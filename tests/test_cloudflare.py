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
