from copy import deepcopy
import threading
import time
import pytest
from test_security import service, admin_client


class WAF:
    def __init__(self):
        self.ruleset = {"id": "ruleset", "rules": [{"id": "unrelated", "ref": "owner_rule", "expression": "ip.src eq 192.0.2.1", "action": "block"}]}
        self.calls = []
        self.fail_after_create = False

    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, deepcopy(body)))
        assert kwargs.get("force_write") is True
        if method == "GET":
            return deepcopy(self.ruleset)
        if method == "POST":
            if path.endswith("/rulesets"):
                self.ruleset = {"id": "ruleset", "rules": deepcopy(body["rules"])}
                for i, rule in enumerate(self.ruleset["rules"]): rule["id"] = str(i)
            else:
                self.ruleset["rules"].insert(0, deepcopy(body) | {"id": "owned" + str(len(self.ruleset["rules"]))})
            if self.fail_after_create:
                self.fail_after_create = False
                raise RuntimeError("Cloudflare API 网络连接或响应异常")
            return deepcopy(self.ruleset)
        if method == "PATCH":
            rule = next(r for r in self.ruleset["rules"] if r["id"] == path.split("/")[-1])
            rule.update(body)
            return deepcopy(self.ruleset)
        if method == "DELETE":
            self.ruleset["rules"] = [r for r in self.ruleset["rules"] if r["id"] != path.split("/")[-1]]
            return None
        raise AssertionError(method)


def setup(service, monkeypatch):
    site = service.save_site({"name": "app", "hostname": "app.example.com", "origin": "http://127.0.0.1:9300", "human_check": False})
    waf = WAF()
    monkeypatch.setattr(service.cf, "request", waf.request)
    return site, waf


def finish(service):
    service.site_pause.thread.join(3)
    assert not service.site_pause.thread.is_alive()
    return next(iter(service.site_pause.status().values()))


def test_cloud_pause_resume_preserves_other_rules(service, monkeypatch):
    site, waf = setup(service, monkeypatch)
    original = deepcopy(waf.ruleset["rules"][0])
    result = service.site_pause.submit(site["id"], True, True)
    assert result["paused"]
    assert finish(service)["phase"] == "succeeded"
    assert len(waf.ruleset["rules"]) == 2
    assert waf.ruleset["rules"][0]["expression"] == 'http.host eq "app.example.com"'
    assert original in waf.ruleset["rules"]
    with pytest.raises(ValueError): service.save_site(site | {"hostname": "new.example.com"})
    with pytest.raises(ValueError): service.set_site_paused(site["id"], False)
    service.site_pause.submit(site["id"], False)
    assert finish(service)["phase"] == "succeeded"
    assert waf.ruleset["rules"] == [original]
    assert not service.sites()[0]["paused"]
    service.save_site(site | {"hostname": "new.example.com"})


def test_unknown_create_result_retry_does_not_duplicate(service, monkeypatch):
    site, waf = setup(service, monkeypatch)
    waf.fail_after_create = True
    service.site_pause.submit(site["id"], True, True)
    assert finish(service)["phase"] == "failed"
    assert service.sites()[0]["paused"]
    service.site_pause.submit(site["id"], True, True)
    assert finish(service)["phase"] == "succeeded"
    assert len(waf.ruleset["rules"]) == 2
    assert len([c for c in waf.calls if c[0] == "POST"]) == 1


def test_failure_and_interruption_never_resume_locally(service, monkeypatch):
    site, waf = setup(service, monkeypatch)
    service.site_pause.submit(site["id"], True, True)
    finish(service)
    def failure(*args, **kwargs): raise RuntimeError("需要 Zone WAF 编辑权限")
    monkeypatch.setattr(service.cf, "request", failure)
    service.site_pause.submit(site["id"], False)
    assert finish(service)["phase"] == "failed"
    assert service.sites()[0]["paused"]
    service.site_pause._save(site["id"], phase="running")
    service.site_pause.recover()
    assert service.site_pause.status()[site["id"]]["phase"] == "failed"
    assert service.sites()[0]["paused"]


def test_async_api_and_busy_guard(service, monkeypatch):
    site, waf = setup(service, monkeypatch)
    started, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return waf.request(*args, **kwargs)
    monkeypatch.setattr(service.cf, "request", slow)
    admin = admin_client(service)
    try:
        before = time.monotonic()
        r = admin.post('/api/sites/' + site["id"] + '/pause', json={"paused": True, "cloudflare": True})
        assert r.status_code == 200 and time.monotonic() - before < 1
        assert started.wait(1)
        assert admin.get('/api/state').status_code == 200
        assert admin.post('/api/sites/' + site["id"] + '/pause', json={"paused": False}).status_code == 400
    finally:
        release.set()
        finish(service)


def test_missing_entrypoint_and_validation(service, monkeypatch):
    site, waf = setup(service, monkeypatch)
    waf.ruleset = None
    assert admin_client(service).post('/api/sites/' + site["id"] + '/pause', json={"paused": True, "cloudflare": "true"}).status_code == 400
    service.site_pause.submit(site["id"], True, True)
    assert finish(service)["phase"] == "succeeded"
    assert len(waf.ruleset["rules"]) == 1
    assert any(c[0] == "POST" and c[1].endswith('/rulesets') for c in waf.calls)


def test_external_rule_change_not_deleted(service, monkeypatch):
    site, waf = setup(service, monkeypatch)
    service.site_pause.submit(site["id"], True, True)
    finish(service)
    waf.ruleset["rules"][0]["expression"] = 'http.host eq "other.example.com"'
    service.site_pause.submit(site["id"], False)
    assert finish(service)["phase"] == "failed"
    assert len(waf.ruleset["rules"]) == 2 and service.sites()[0]["paused"]


def test_completed_resume_allows_new_host_and_local_pause(service, monkeypatch):
    site, waf = setup(service, monkeypatch)
    service.site_pause.submit(site["id"], True, True)
    finish(service)
    service.site_pause.submit(site["id"], False)
    finish(service)
    new = service.save_site(site | {"hostname": "new.example.com"})
    service.site_pause.submit(site["id"], True, True)
    assert finish(service)["phase"] == "succeeded"
    assert waf.ruleset["rules"][0]["expression"] == 'http.host eq "new.example.com"'
    service.site_pause.submit(site["id"], False)
    finish(service)
    service.site_pause.submit(site["id"], True)
    assert service.site_pause.status() == {}
    assert service.sites()[0]["paused"]


def test_two_hosts_are_independent(service, monkeypatch):
    first, waf = setup(service, monkeypatch)
    second = service.save_site(first | {"id": "", "hostname": "other.example.com"})
    for site in (first, second):
        service.site_pause.submit(site["id"], True, True)
        service.site_pause.thread.join(3)
        assert service.site_pause.status()[site["id"]]["phase"] == "succeeded"
    assert len(waf.ruleset["rules"]) == 3
    service.site_pause.submit(first["id"], False)
    service.site_pause.thread.join(3)
    assert len(waf.ruleset["rules"]) == 2
    assert any(r.get("expression") == 'http.host eq "other.example.com"' for r in waf.ruleset["rules"])
    assert next(s for s in service.sites() if s["id"] == second["id"])["paused"]


def test_temporary_token_cannot_manage_cloud_block(service, monkeypatch):
    from fastapi.testclient import TestClient
    from lanbridge.admin import create_admin
    site, waf = setup(service, monkeypatch)
    issued = service.store.issue_temporary_token(permissions=["sites", "account"])
    client = TestClient(create_admin(service), base_url="http://127.0.0.1:8890", headers={"Authorization": "Bearer " + issued["token"]})
    path = '/api/sites/' + site["id"] + '/pause'
    assert client.post(path, json={"paused": True, "cloudflare": True}).status_code == 403
    assert not waf.calls
    assert client.post(path, json={"paused": True}).status_code == 200


def test_waf_http_permission_hint_and_missing_entrypoint(service, monkeypatch):
    import httpx
    import lanbridge.service as module
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404, json={"success":False}, request=request)))
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: client)
    path = '/zones/' + 'b'*32 + '/rulesets/phases/http_request_firewall_custom/entrypoint'
    assert service.cf.request('GET',path,force_write=True,allow_missing=True) is None
    response = httpx.Response(403,json={"errors":[{"code":10000}]})
    hint = service.cf.failure_hint('GET',path,response,'oauth')
    assert 'Zone WAF' in hint and 'API Token' in hint and '本机暂停仍保留' in hint


def test_adjust_cloud_pause_without_resuming_local_site(service, monkeypatch):
    site, waf = setup(service, monkeypatch)
    admin = admin_client(service)
    path = '/api/sites/' + site["id"] + '/pause'
    assert admin.post(path,json={"paused":True}).status_code == 200
    version = service.sites()[0]["policy_version"]
    for cloud in (True, False, True):
        assert admin.post(path,json={"paused":True,"cloudflare":cloud}).status_code == 200
        assert finish(service)["phase"] == "succeeded"
        assert service.sites()[0]["paused"]
        assert service.sites()[0]["policy_version"] == version
        owned = [r for r in waf.ruleset["rules"] if r.get("ref", "").startswith("lanbridge_pause_")]
        assert bool(owned) == cloud
    assert admin.post(path,json={"paused":False}).status_code == 200
    finish(service)
    assert not service.sites()[0]["paused"]


def test_adjust_remove_failure_retry_keeps_local_pause(service, monkeypatch):
    site, waf = setup(service, monkeypatch)
    service.site_pause.submit(site["id"],True,True)
    finish(service)
    def fail(*args, **kwargs): raise RuntimeError("网络异常")
    monkeypatch.setattr(service.cf,"request",fail)
    service.site_pause.submit(site["id"],True,False)
    entry = finish(service)
    assert entry["phase"] == "failed" and not entry["desired"] and entry["paused"]
    assert service.sites()[0]["paused"]
    service.site_pause.recover()
    monkeypatch.setattr(service.cf,"request",waf.request)
    service.site_pause.submit(site["id"],True,False)
    assert finish(service)["message"] == "云端阻断已解除，仅本机暂停"
    assert service.sites()[0]["paused"] and len(waf.ruleset["rules"]) == 1


@pytest.mark.parametrize("paused", [True, False])
def test_limited_pause_rechecks_cloud_state_inside_submission(service, monkeypatch, paused):
    import asyncio
    from fastapi.testclient import TestClient
    from lanbridge.admin import create_admin
    site, waf = setup(service, monkeypatch)
    issued = service.store.issue_temporary_token(permissions=["sites", "account"])
    client = TestClient(create_admin(service), base_url="http://127.0.0.1:8890",
                        headers={"Authorization": "Bearer " + issued["token"]})
    original = asyncio.to_thread
    changed = []

    async def switch_before_submission(fn, *args, **kwargs):
        if not changed and getattr(fn, "__name__", "") in {"submit", "pause_submission"}:
            changed.append(True)
            service.set_site_paused(site["id"], True)
            service.site_pause._save(site["id"], hostname=site["hostname"],
                zone_id=site["zone_id"], account_id=service.settings()["account_id"],
                ref="lanbridge_pause_test", desired=True, phase="succeeded")
        return await original(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", switch_before_submission)
    response = client.post('/api/sites/' + site["id"] + '/pause', json={"paused": paused})
    if service.site_pause.thread:
        service.site_pause.thread.join(3)
    assert changed
    assert response.status_code == 403
    assert not waf.calls
    assert service.sites()[0]["paused"]


@pytest.mark.parametrize("entry, required", [
    ({}, False), ({"phase": "succeeded", "desired": False}, False),
    ({"phase": "succeeded", "desired": True}, True),
    ({"phase": "queued"}, True), ({"phase": "running"}, True),
    ({"phase": "failed"}, True), ({"phase": "unknown"}, True),
])
def test_cloud_access_requirement_preserves_uncertain_outcomes(entry, required):
    from lanbridge.site_pause import SitePause
    assert SitePause.requires_cloud_access(entry) is required


def test_pause_status_returns_independent_snapshots(service):
    service.site_pause._save("test", desired=True, phase="failed")
    first = service.site_pause.status()
    first["test"]["desired"] = False
    assert service.site_pause.status()["test"]["desired"]


@pytest.mark.parametrize("change", ["account", "shutdown"])
def test_resume_rejects_context_change_during_final_cloud_confirmation(service, monkeypatch, change):
    site, waf = setup(service, monkeypatch)
    service.site_pause.submit(site["id"], True, True)
    assert finish(service)["phase"] == "succeeded"
    reads = []
    def change_after_confirmation(method, path, body=None, **kwargs):
        result = waf.request(method, path, body, **kwargs)
        if method == "GET":
            reads.append(path)
            if len(reads) == 2:
                if change == "account":
                    service.store.set("settings", service.settings() | {"account_id": "d" * 32})
                else:
                    service.site_pause.stopping = True
        return result
    monkeypatch.setattr(service.cf, "request", change_after_confirmation)
    service.site_pause.submit(site["id"], False)
    job = finish(service)
    assert len(reads) == 2
    assert job["phase"] == "failed"
    assert service.sites()[0]["paused"]
    assert not any(a["action"] == "site_cloud_resume" for a in service.store.audit_list())


def test_failed_resume_status_commit_keeps_local_pause(service, monkeypatch):
    site, waf = setup(service, monkeypatch)
    service.site_pause.submit(site["id"], True, True)
    assert finish(service)["phase"] == "succeeded"
    original_version = service.sites()[0]["policy_version"]
    service.store.set("paused_auto_attempts", {site["id"]: True})
    service.store.db.execute("CREATE TEMP TRIGGER reject_resume_status BEFORE INSERT ON kv "
        "WHEN NEW.key='site_pause' AND NEW.value LIKE '%succeeded%' "
        "BEGIN SELECT RAISE(ABORT,'test-only completion failure'); END")
    service.site_pause.submit(site["id"], False)
    job = finish(service)
    assert job["phase"] == "failed"
    assert service.sites()[0]["paused"]
    assert not any(a["action"] == "site_resumed" for a in service.store.audit_list())
    assert service.sites()[0]["policy_version"] == original_version
    assert service.store.get("paused_auto_attempts")[site["id"]]
    service.store.db.execute('DROP TRIGGER reject_resume_status')
    service.site_pause.submit(site["id"], False)
    assert finish(service)["phase"] == "succeeded"
    assert not service.sites()[0]["paused"]


@pytest.mark.parametrize("action", ["site_resumed", "site_cloud_resume"])
def test_audit_failure_does_not_relabel_committed_resume_as_paused(service, monkeypatch, caplog, action):
    site, waf = setup(service, monkeypatch)
    service.site_pause.submit(site["id"], True, True)
    assert finish(service)["phase"] == "succeeded"
    original = service.store.audit
    def fail_resume_audit(event, detail):
        if event == action:
            raise OSError("test-only audit failure")
        return original(event, detail)
    monkeypatch.setattr(service.store, "audit", fail_resume_audit)
    service.site_pause.submit(site["id"], False)
    assert finish(service)["phase"] == "succeeded"
    assert not service.sites()[0]["paused"]
    assert 'site_cloud_pause_audit_failed' in caplog.text


def test_pause_stop_signal_does_not_wait_for_publication_service_lock(service):
    finished = threading.Event()
    def stop_pause():
        service.site_pause.stop()
        finished.set()
    with service.lock:
        caller = threading.Thread(target=stop_pause, daemon=True)
        caller.start()
        stopped = finished.wait(1)
    caller.join(2)
    assert stopped and service.site_pause.stopping
    assert not caller.is_alive()
