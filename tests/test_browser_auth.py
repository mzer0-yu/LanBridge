import json
import threading
import pytest
from lanbridge.browser_auth import BrowserAuth, SCOPES
from lanbridge.service import Service, digest
from test_token_manager import groups

@pytest.fixture
def browser(tmp_path, monkeypatch):
    service = Service(tmp_path / "data")
    service.store.set("settings", service.settings() | {"account_id": "a"*32, "zone_id": "b"*32, "zone_name": "example.com"})
    service.store.set_secret("cf_write_token", "old-business-secret")
    service.store.set_secret("cf_read_token", "old-read-secret")
    browser = BrowserAuth(service)
    monkeypatch.setattr(browser, "command", lambda: ["node", "official-cf"])
    yield browser
    browser.stop()
    if browser.thread:
        browser.thread.join(timeout=5)
    service.store.db.close()

def test_browser_consent_creates_scoped_account_token_and_cleans_session(browser, monkeypatch):
    calls = []
    def run(command, env, cwd, args, timeout=45):
        calls.append((args, env, cwd))
        if args[:2] == ["auth", "whoami"]: return json.dumps({"scopes": SCOPES})
        if "permission-groups" in args:
            return json.dumps(groups())
        if args[:3] == ["accounts", "tokens", "create"]:
            body = json.loads(args[args.index("--policies")+1])
            assert body[0]["resources"] == {"com.cloudflare.api.account."+"a"*32:"*"}
            assert body[1]["resources"] == {"com.cloudflare.api.account.zone."+"b"*32:"*"}
            assert len(body[0]["permission_groups"]) == 1
            return json.dumps({"id":"c"*32,"value":"new-account-token-secret"})
        return "authorized"
    monkeypatch.setattr(browser, "run", run)
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()["phase"] == "done"
    assert browser.service.store.secret("cf_write_token") == "new-account-token-secret"
    assert browser.service.store.secret("cf_read_token") == "old-read-secret"
    assert browser.service.store.get("managed_business_token")["kind"] == "account"
    assert browser.service.store.get("pending_browser_token") is None
    assert "new-account-token-secret" not in json.dumps(browser.status())+json.dumps(browser.service.store.audit_list())
    assert calls[0][0][:3] == ["auth","create","lanbridge"]
    assert calls[0][0][-len(SCOPES):] == SCOPES
    assert calls[-1][0] == ["auth","logout","--profile","lanbridge"]
    assert not calls[0][2].exists()

@pytest.mark.parametrize("failure", ["denied", "changed", "unknown"])
def test_browser_failure_preserves_credentials_and_unknown_creation_blocks_retry(browser, monkeypatch, failure):
    calls=[]
    def run(command, env, cwd, args, timeout=45):
        calls.append(args)
        if args[:2] == ["auth", "whoami"]: return json.dumps({"scopes": SCOPES})
        if args[:2] == ["auth","create"]:
            if failure == "denied": raise ValueError("用户未完成授权")
            if failure == "changed": browser.service.store.set("settings", browser.service.settings() | {"zone_id":"d"*32})
            return "authorized"
        if "permission-groups" in args: return json.dumps(groups())
        if args[:3] == ["accounts","tokens","create"]: raise ValueError("创建超时")
        return ""
    monkeypatch.setattr(browser,"run",run)
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()["phase"] == "error"
    assert browser.service.store.secret("cf_write_token") == "old-business-secret"
    creates=[args for args in calls if args[:3]==["accounts","tokens","create"]]
    assert len(creates) == (1 if failure=="unknown" else 0)
    if failure=="unknown":
        assert browser.service.store.get("pending_browser_token")
        with pytest.raises(ValueError,match="结果未知"):browser.start()

def test_isolated_environment_removes_existing_credentials_and_node_injection(monkeypatch,tmp_path):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN","private-secret")
    monkeypatch.setenv("CLOUDFLARE_CLIENT_ID","other-client")
    monkeypatch.setenv("NODE_OPTIONS","--require bad.js")
    env=BrowserAuth.environment(tmp_path,"a"*32)
    assert "CLOUDFLARE_API_TOKEN" not in env and "NODE_OPTIONS" not in env and "CLOUDFLARE_CLIENT_ID" not in env
    assert env["XDG_CONFIG_HOME"]==str(tmp_path)
    assert env["APPDATA"]==str(tmp_path)
    assert env["CLOUDFLARE_ACCOUNT_ID"]=="a"*32

def test_browser_endpoint_requires_auth_and_csrf(browser,monkeypatch):
    from fastapi.testclient import TestClient
    from lanbridge.admin import create_admin
    from lanbridge.store import password_hash
    browser.service.store.set("admin", {"username":"admin","password_hash":password_hash("correct horse battery")})
    calls=[]
    monkeypatch.setattr(BrowserAuth,"start",lambda self:calls.append(True) or {"phase":"authorizing"})
    client=TestClient(create_admin(browser.service),base_url="http://127.0.0.1:8890",headers={"Origin":"http://127.0.0.1:8890"})
    assert client.post("/api/cloudflare/browser-authorize",json={}).status_code==401
    login=client.post("/api/login",json={"username":"admin","password":"correct horse battery"})
    assert client.post("/api/cloudflare/browser-authorize",json={}).status_code==403
    assert client.post("/api/cloudflare/browser-authorize",json={},headers={"X-CSRF-Token":login.json()["csrf"]}).status_code==200
    assert calls==[True]

def test_account_token_turnstile_is_explained_before_network_request(browser):
    browser.service.store.set("managed_business_token",{"kind":"account"})
    with pytest.raises(ValueError,match="不支持 Turnstile"):
        browser.service.cf.request("POST","/accounts/"+"a"*32+"/challenges/widgets",{})


def test_successful_login_without_selectable_tunnel_permissions_never_creates(browser, monkeypatch):
    calls = []
    def run(command, env, cwd, args, timeout=45):
        calls.append(args)
        if args[:2] == ["auth", "whoami"]: return json.dumps({"scopes": SCOPES})
        if "permission-groups" in args:
            available = groups()
            available[0]["is_selectable"] = False
            return json.dumps(available)
        return "authorized"
    monkeypatch.setattr(browser, "run", run)
    browser.start(); browser.thread.join(timeout=5)
    assert browser.status()["phase"] == "error"
    assert "不可授予" in browser.status()["message"]
    assert "实际包含" in browser.status()["message"]
    assert browser.status()["next_action"] == "token_authority"
    assert not any(args[:3] == ["accounts", "tokens", "create"] for args in calls)
    assert browser.service.store.secret("cf_write_token") == "old-business-secret"
    assert browser.service.store.get("pending_browser_token") is None


def test_cli_callback_timeout_has_specific_safe_retry_guidance(browser, monkeypatch, tmp_path):
    class Process:
        returncode = 1
        def communicate(self, timeout):
            return b"", b"Timed out waiting for authorization code, please try again. secret-callback-code"
    monkeypatch.setattr("lanbridge.browser_auth.subprocess.Popen", lambda *a, **kw: Process())
    with pytest.raises(ValueError, match="8877") as error:
        browser.run(["node", "cf"], {}, tmp_path, ["auth", "create", "lanbridge"])
    assert "重新发起" in str(error.value) and "secret-callback-code" not in str(error.value)
    assert browser.process is None
    assert browser.service.store.secret("cf_write_token") == "old-business-secret"


@pytest.mark.parametrize("granted_dns", [True, False])
def test_dns_grant_and_token_delegation_are_distinct_and_secrets_are_not_retained(browser, monkeypatch, granted_dns):
    calls = []
    def run(command, env, cwd, args, timeout=45):
        calls.append(args)
        if args[:2] == ["auth", "whoami"]:
            return json.dumps({"scopes": SCOPES if granted_dns else [s for s in SCOPES if s != "dns.write"], "email": "private@example.com", "token": "private-token-value"})
        if "permission-groups" in args:
            available = groups()
            available[2]["is_selectable"] = False
            return json.dumps(available)
        return "authorized"
    monkeypatch.setattr(browser, "run", run)
    browser.start(); browser.thread.join(timeout=5)
    status = browser.status()
    assert status["phase"] == "error"
    assert status["next_action"] == ("token_authority" if granted_dns else "reauthorize")
    assert ("dns.write" in status["granted_scopes"]) == granted_dns
    assert "private@example.com" not in json.dumps(status) and "private-token-value" not in json.dumps(status)
    assert not any(args[:3] == ["accounts", "tokens", "create"] for args in calls)
    assert browser.service.store.secret("cf_write_token") == "old-business-secret"
    if granted_dns:
        assert "DNS Write" in status["message"] and "无需反复授权" in status["message"]
    else:
        assert "实际未授予" in status["message"]


def test_old_dns_rejection_gets_alternative_without_claiming_verified_grants(browser):
    browser.service.store.set("browser_auth_job", {"phase": "error", "message": "Cloudflare 返回了所需权限但当前授权不可授予：DNS Write"})
    migrated = BrowserAuth(browser.service).status()
    assert migrated["next_action"] == "token_authority"
    assert "未保存实际授予" in migrated["message"]
    assert "granted_scopes" not in migrated
