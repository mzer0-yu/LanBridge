import json
import time
from pathlib import Path
from datetime import datetime, timezone
import pytest
import httpx
from lanbridge.browser_auth import BrowserAuth, SCOPES
from lanbridge.service import Cloudflare, Service, digest

@pytest.fixture
def browser(tmp_path, monkeypatch):
    service = Service(tmp_path / "data")
    service.store.set("settings", service.settings() | {"account_id": "a"*32, "zone_id": "b"*32, "zone_name": "example.com"})
    service.store.set_secret("cf_write_token", "old-business-secret")
    service.store.set_secret("cf_read_token", "old-read-secret")
    browser = BrowserAuth(service)
    service.browser_auth = browser
    def mock_tunnel():
        cfg = service.settings()
        service.store.set_many({'settings':cfg | {'tunnel_id':'11111111-1111-4111-8111-111111111111'}, 'owned_tunnel':'11111111-1111-4111-8111-111111111111'}, secret_values={'tunnel_token':'test-connection-secret'})
        return {'id':'11111111-1111-4111-8111-111111111111', 'token_saved':True}
    monkeypatch.setattr(service.cf, 'create_tunnel', mock_tunnel)
    monkeypatch.setattr(BrowserAuth, "command", lambda self: ["node", "official-cf"])
    monkeypatch.setattr(browser, "validate_zone", lambda token, cfg: None)
    yield browser
    browser.stop()
    if browser.thread:
        browser.thread.join(timeout=5)
    service.store.db.close()

def snapshot(token="oauth-access-secret", expiry=None):
    return {"path":"cloudflare/config/lanbridge.json", "profile":{"oauth_token": token, "refresh_token":"oauth-refresh-secret", "expiration_time":datetime.fromtimestamp(expiry or time.time()+3600, timezone.utc).isoformat(), "scopes": SCOPES}}


def prepare_blank_authorization(browser, monkeypatch, count=1):
    cfg=browser.service.settings() | {'account_id':'','zone_id':'','zone_name':''}
    browser.service.store.set('settings',cfg)
    choices=[{'account_id':'a'*32,'zone_id':str(i)*32,'zone_name':f'example{i}.com','account_name':'账户'} for i in range(1,count+1)]
    monkeypatch.setattr(browser,'discover_zones',lambda _:choices)
    fake_login(browser,monkeypatch)
    return choices


def test_one_zone_autofills_without_manual_ids(browser, monkeypatch):
    choices=prepare_blank_authorization(browser,monkeypatch)
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()['phase']=='done'
    for key in ('account_id','zone_id','zone_name'):
        assert browser.service.settings()[key]==choices[0][key]
    assert browser.service.store.secret('cf_write_token')=='oauth-access-secret'


@pytest.mark.parametrize('cancel', [False,True])
def test_multiple_zones_require_selection_and_allow_cancel(browser, monkeypatch, cancel):
    choices=prepare_blank_authorization(browser,monkeypatch,2)
    browser.start()
    deadline=time.time()+5
    while browser.status()['phase']!='choosing_zone' and time.time()<deadline:
        time.sleep(.02)
    assert browser.status()['phase']=='choosing_zone'
    assert browser.status()['zones']==choices
    assert browser.service.settings()['zone_id']==''
    assert browser.service.store.secret('cf_write_token')=='old-business-secret'
    with pytest.raises(ValueError,match='已授权'):
        browser.choose_zone('not-authorized')
    if cancel:
        browser.cancel()
    else:
        browser.choose_zone(choices[1]['zone_id'])
    browser.thread.join(timeout=5)
    assert browser.status()['phase']==('cancelled' if cancel else 'done')
    assert browser.service.settings()['zone_id']==('' if cancel else choices[1]['zone_id'])


def test_no_active_zone_does_not_save_credentials_or_settings(browser,monkeypatch):
    prepare_blank_authorization(browser,monkeypatch,0)
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()['phase']=='error'
    assert browser.service.settings()['zone_id']==''
    assert browser.service.store.secret('cf_write_token')=='old-business-secret'


def test_discovery_filters_inactive_zones_and_only_returns_public_metadata(monkeypatch):
    original=httpx.Client
    payload={'success':True,'result':[{'id':'b'*32,'name':'example.com','status':'active','account':{'id':'a'*32,'name':'账户'},'private':'never copy'}, {'id':'c'*32,'name':'pending.com','status':'pending','account':{'id':'a'*32}}], 'result_info':{'total_pages':1}}
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=payload)),**kwargs))
    zones=BrowserAuth.discover_zones('secret')
    assert zones==[{'account_id':'a'*32,'account_name':'账户','zone_id':'b'*32,'zone_name':'example.com'}]
    assert 'secret' not in json.dumps(zones) and 'never copy' not in json.dumps(zones)

def fake_login(browser, monkeypatch, scopes=None, change=False):
    calls=[]
    def run(command, env, cwd, args, timeout=45):
        calls.append((args, cwd))
        if args[:2]==["auth","create"]:
            data=snapshot();data["profile"]["scopes"]=SCOPES if scopes is None else scopes
            path=cwd/data["path"];path.parent.mkdir(parents=True);path.write_text(json.dumps(data["profile"]))
            if change: browser.service.store.set("settings", browser.service.settings() | {"zone_id":"d"*32})
            return "authorized"
        if args[:2]==["auth","whoami"]: return json.dumps({"scopes":SCOPES if scopes is None else scopes,"email":"private@example.com"})
        raise AssertionError("No token delegation or logout expected")
    monkeypatch.setattr(browser,"run",run)
    return calls

def test_browser_consent_directly_connects_and_cleans_temporary_files(browser, monkeypatch):
    calls=fake_login(browser,monkeypatch)
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()["phase"]=="done"
    assert browser.service.store.secret("cf_write_token")=="oauth-access-secret"
    assert browser.service.store.secret("cf_read_token")=="old-read-secret"
    assert browser.service.store.get("managed_business_token")["kind"]=="oauth"
    assert "account_api_tokens:create" not in SCOPES
    assert browser.service.store.secret("cf_oauth_profile")
    public=json.dumps(browser.status())+json.dumps(browser.service.store.audit_list())
    assert "oauth-access-secret" not in public and "oauth-refresh-secret" not in public and "private@example.com" not in public
    assert not calls[0][1].exists()
    assert len(calls)==2
    assert "zone.write" in calls[0][0]

@pytest.mark.parametrize("failure", ["missing_scope", "changed", "zone"])
def test_failed_authorization_preserves_previous_credentials(browser, monkeypatch, failure):
    fake_login(browser,monkeypatch, scopes=[s for s in SCOPES if s!="dns.write"] if failure=="missing_scope" else None,change=failure=="changed")
    if failure=="zone": monkeypatch.setattr(browser,"validate_zone",lambda *a: (_ for _ in ()).throw(ValueError("Zone不匹配")))
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()["phase"]=="error"
    assert browser.service.store.secret("cf_write_token")=="old-business-secret"
    assert not browser.service.store.secret("cf_oauth_profile")


def test_oauth_refresh_persists_rotation_without_exposing_secrets(browser,monkeypatch):
    browser.save_profile(snapshot(expiry=time.time()-60),browser.service.settings())
    calls=[]
    def run(self,command,env,cwd,args,timeout=45):
        calls.append(cwd)
        path=cwd/snapshot()["path"]
        data=json.loads(path.read_text());assert data["refresh_token"]=="oauth-refresh-secret"
        path.write_text(json.dumps(snapshot("rotated-access-secret")["profile"]))
        return "{}"
    monkeypatch.setattr(BrowserAuth,"run",run)
    assert browser.access_token()=="rotated-access-secret"
    assert browser.service.store.secret("cf_write_token")=="rotated-access-secret"
    assert not calls[0].exists()
    assert browser.access_token()=="rotated-access-secret" and len(calls)==1


def test_refresh_failure_is_retryable_and_keeps_credentials(browser,monkeypatch):
    browser.save_profile(snapshot(expiry=time.time()-60),browser.service.settings())
    monkeypatch.setattr(BrowserAuth,"run",lambda *a,**k: (_ for _ in ()).throw(ValueError("private-refresh-secret")))
    with pytest.raises(ValueError,match="重试续期") as exc:browser.access_token()
    assert "private-refresh-secret" not in str(exc.value)
    assert browser.service.store.secret("cf_write_token")=="oauth-access-secret"
    assert not list((browser.service.store.root/"browser-auth").iterdir())


def test_oauth_is_bound_to_configured_account_and_zone(browser):
    browser.save_profile(snapshot(),browser.service.settings())
    browser.service.store.set("settings",browser.service.settings() | {"account_id":"e"*32})
    with pytest.raises(ValueError,match="已变化"):browser.access_token()


def test_oauth_takes_precedence_over_old_read_token(browser,monkeypatch):
    import httpx
    browser.save_profile(snapshot(),browser.service.settings())
    def request(self,method,url,headers,json):
        assert headers["Authorization"]=="Bearer oauth-access-secret"
        return httpx.Response(200,json={"success":True,"result":{"ok":True}})
    monkeypatch.setattr(httpx.Client,"request",request)
    assert browser.service.cf.request("GET","/zones/"+"b"*32)=={"ok":True}


def test_profile_traversal_cannot_write_outside_temporary_directory(browser):
    data=snapshot(expiry=time.time()-60);data["path"]="../../lanbridge.json"
    browser.save_profile(data,browser.service.settings())
    with pytest.raises(ValueError,match="未能自动续期"):browser.access_token()


def test_missing_refresh_token_rejects_incomplete_profile(browser,tmp_path):
    data=snapshot()["profile"];data.pop("refresh_token")
    (tmp_path/"lanbridge.json").write_text(json.dumps(data))
    with pytest.raises(ValueError,match="可刷新授权"):browser.read_profile(tmp_path)


def test_old_delegation_failure_points_to_browser_authorization(browser):
    browser.service.store.set("browser_auth_job",{"phase":"error","next_action":"token_authority"})
    status=BrowserAuth(browser.service).status()
    assert status["next_action"]=="reauthorize" and "无需另行提供" in status["message"]


def test_new_oauth_requests_zone_write():
    assert 'zone.write' in SCOPES


def test_old_oauth_without_zone_write_remains_readable(browser,tmp_path):
    data=snapshot()['profile'];data['scopes']=[s for s in SCOPES if s!='zone.write']
    (tmp_path/'lanbridge.json').write_text(json.dumps(data))
    assert browser.read_profile(tmp_path)['profile']['oauth_token']=='oauth-access-secret'


def test_new_login_rejects_missing_zone_write_without_replacing_old_token(browser,monkeypatch):
    fake_login(browser,monkeypatch,scopes=[s for s in SCOPES if s!='zone.write'])
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()['phase']=='error' and 'zone.write' in browser.status()['message']
    assert browser.service.store.secret('cf_write_token')=='old-business-secret'

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
    monkeypatch.setattr(BrowserAuth,"start",lambda self, browser="default", **kwargs:calls.append(True) or {"phase":"authorizing"})
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


def test_malformed_zone_response_is_reported_without_stuck_job(browser,monkeypatch):
    import httpx
    monkeypatch.setattr(httpx.Client,"get",lambda *a,**k:httpx.Response(200,json={"success":True,"result":"invalid"}))
    with pytest.raises(ValueError,match="响应无效"):
        BrowserAuth.validate_zone("oauth-access-secret",browser.service.settings())


def test_manual_token_replacement_clears_oauth_and_never_returns_secrets(browser):
    from fastapi.testclient import TestClient
    from lanbridge.admin import create_admin
    from lanbridge.store import password_hash
    browser.save_profile(snapshot(),browser.service.settings())
    browser.service.store.set("admin",{"username":"admin","password_hash":password_hash("correct horse battery")})
    client=TestClient(create_admin(browser.service),base_url="http://127.0.0.1:8890",headers={"Origin":"http://127.0.0.1:8890"})
    login=client.post("/api/login",json={"username":"admin","password":"correct horse battery"})
    state=client.get("/api/state").text
    assert "oauth-access-secret" not in state and "oauth-refresh-secret" not in state
    result=client.post("/api/credentials",json={"cf_write_token":"manual-replacement-secret"},headers={"X-CSRF-Token":login.json()["csrf"]})
    assert result.status_code==200
    assert not browser.service.store.secret("cf_oauth_profile")
    assert browser.service.store.get("managed_business_token") is None
    assert browser.service.store.secret("cf_write_token")=="manual-replacement-secret"


def test_cancel_stops_real_waiting_process_and_restart_needs_no_timeout(browser,monkeypatch):
    import sys
    monkeypatch.setattr(browser,"command",lambda:[sys.executable,"-c","import time; time.sleep(120)"])
    browser.start()
    deadline=time.monotonic()+5
    while browser.process is None and time.monotonic()<deadline:time.sleep(.01)
    assert browser.process is not None
    process=browser.process
    before=time.monotonic()
    result=browser.cancel()
    assert time.monotonic()-before<4
    assert result["phase"]=="cancelled" and process.poll() is not None
    assert not browser.thread.is_alive()
    assert browser.service.store.secret("cf_write_token")=="old-business-secret"
    assert not list((browser.service.store.root/"browser-auth").iterdir())
    monkeypatch.setattr(browser,"command",lambda:["node","official-cf"])
    fake_login(browser,monkeypatch)
    browser.restart();browser.thread.join(timeout=5)
    assert browser.status()["phase"]=="done"


def test_cancel_during_preparation_or_verification_does_not_interrupt_commit(browser):
    for phase in ("preparing","creating"):
        browser.update(phase,"正在处理")
        with pytest.raises(ValueError,match="稍候"):browser.cancel()
        assert not browser.cancelled.is_set()


@pytest.mark.parametrize("endpoint",["browser-authorize-cancel","browser-authorize-restart","browser-authorize-setup"])
def test_recovery_endpoints_require_admin_and_csrf(browser,monkeypatch,endpoint):
    from fastapi.testclient import TestClient
    from lanbridge.admin import create_admin
    from lanbridge.store import password_hash
    browser.service.store.set("admin",{"username":"admin","password_hash":password_hash("correct horse battery")})
    calls=[]
    method="cancel" if endpoint.endswith("cancel") else "continue_setup" if endpoint.endswith("setup") else "restart"
    monkeypatch.setattr(BrowserAuth,method,lambda self, browser=None:calls.append(True) or {"phase":"cancelled"})
    client=TestClient(create_admin(browser.service),base_url="http://127.0.0.1:8890",headers={"Origin":"http://127.0.0.1:8890"})
    assert client.post("/api/cloudflare/"+endpoint,json={}).status_code==401
    login=client.post("/api/login",json={"username":"admin","password":"correct horse battery"})
    assert client.post("/api/cloudflare/"+endpoint,json={}).status_code==403
    assert client.post("/api/cloudflare/"+endpoint,json={},headers={"X-CSRF-Token":login.json()["csrf"]}).status_code==200
    assert calls==[True]


def test_interrupted_cancellation_is_recoverable_after_service_restart(browser):
    browser.service.store.set("browser_auth_job",{"phase":"cancelling"})
    recovered=BrowserAuth(browser.service)
    assert recovered.status()["phase"]=="error"


def test_consent_automatically_configures_human_check_without_exposing_keys(browser,monkeypatch):
    browser.service.store.set("sites",[{"id":"site","enabled":True,"human_check":True,"hostname":"app.example.com"}])
    calls=[]
    monkeypatch.setattr(browser.service.cf,"create_widget",lambda:calls.append(True) or {"saved":True})
    fake_login(browser,monkeypatch)
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()["phase"]=="done" and calls==[True]
    assert "challenge-widgets.write" in browser.service.store.get("managed_business_token")["scopes"]


def test_old_oauth_requires_widget_consent_without_creating_pending_widget(browser):
    data=snapshot();data["profile"]["scopes"]=[s for s in SCOPES if s!="challenge-widgets.write"]
    browser.save_profile(data,browser.service.settings())
    assert not browser.service.cf.widgets_authorized()
    with pytest.raises(ValueError,match="缺少 Turnstile"):
        browser.service.cf.request("POST","/accounts/"+"a"*32+"/challenges/widgets",{})
    assert browser.service.store.get("pending_widget_create") is None


def test_widget_auto_endpoint_starts_full_browser_consent_for_old_profile(browser,monkeypatch):
    from fastapi.testclient import TestClient
    from lanbridge.admin import create_admin
    from lanbridge.store import password_hash
    data=snapshot();data["profile"]["scopes"]=[s for s in SCOPES if s!="challenge-widgets.write"]
    browser.save_profile(data,browser.service.settings())
    browser.service.store.set("admin",{"username":"admin","password_hash":password_hash("correct horse battery")})
    calls=[];monkeypatch.setattr(BrowserAuth,"start",lambda self, browser="default":calls.append(True) or {"phase":"authorizing"})
    client=TestClient(create_admin(browser.service),base_url="http://127.0.0.1:8890",headers={"Origin":"http://127.0.0.1:8890"})
    login=client.post("/api/login",json={"username":"admin","password":"correct horse battery"})
    r=client.post("/api/cloudflare/turnstile-auto",json={},headers={"X-CSRF-Token":login.json()["csrf"]})
    assert r.status_code==200 and r.json()["phase"]=="authorizing" and calls==[True]


def test_selected_browser_disables_cli_default_launcher(browser, monkeypatch):
    monkeypatch.setattr("lanbridge.browser_auth.available_browsers", lambda: [{"id":"default"}, {"id":"chrome"}])
    calls = fake_login(browser, monkeypatch)
    browser.start("chrome")
    browser.thread.join(timeout=5)
    assert "--no-browser" in calls[0][0]
    assert browser.status()["browser"] == "chrome"
    assert browser.status()["phase"] == "done"


def test_invalid_browser_cannot_start_or_cancel_existing_authorization(browser):
    before = browser.status()
    with pytest.raises(ValueError, match="已安装"):
        browser.start("powershell.exe")
    with pytest.raises(ValueError, match="已安装"):
        browser.restart("powershell.exe")
    assert browser.status() == before


@pytest.mark.parametrize("stream", ["stdout", "stderr", "both"])
def test_private_cli_output_opens_only_selected_browser(browser, monkeypatch, stream):
    import subprocess
    import sys
    opened = []
    monkeypatch.setattr("lanbridge.browser_auth.open_browser", lambda url, selected: opened.append((url, selected)) or True)
    url = "https://dash.cloudflare.com/oauth2/auth?state=private-test-state"
    code = "import sys; " + "; ".join("print('Visit this link to authenticate: '+" + repr(url) + ", file=sys." + name + ", flush=True)" for name in (["stdout", "stderr"] if stream=="both" else [stream]))
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    browser.communicate_browser(process, "edge", 5)
    assert opened == [(url, "edge")]
    assert "private-test-state" not in str(browser.status())


def test_unexpected_cli_url_never_opens_browser(browser, monkeypatch):
    import subprocess
    import sys
    opened = []
    monkeypatch.setattr("lanbridge.browser_auth.open_browser", lambda *args: opened.append(args) or True)
    code = "print('https://dash.cloudflare.com.evil.example/oauth2/auth?state=bad', flush=True)"
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with pytest.raises(ValueError, match="未返回授权地址"):
        browser.communicate_browser(process, "chrome", 5)
    assert not opened


def test_cli_reader_failure_terminates_child_and_preserves_credentials(browser, monkeypatch, tmp_path):
    import subprocess
    import sys
    processes = []
    original = subprocess.Popen
    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr("lanbridge.browser_auth.subprocess.Popen", start)
    def failed_reader(*args):
        raise OSError("simulated pipe failure")
    monkeypatch.setattr(browser, "communicate_browser", failed_reader)
    with pytest.raises(OSError, match="pipe failure"):
        browser.run([sys.executable, "-c", "import time; time.sleep(60)"], {}, tmp_path,
                    ["auth", "create", "--no-browser"], timeout=5)
    assert len(processes) == 1 and processes[0].poll() is not None
    assert browser.process is None
    assert browser.service.store.secret("cf_write_token") == "old-business-secret"
    assert processes[0].stdout.closed and processes[0].stderr.closed


@pytest.mark.parametrize("scopes", [None, [{}], {scope: True for scope in SCOPES}])
def test_malformed_profile_scopes_are_rejected(browser, tmp_path, scopes):
    data = snapshot()["profile"] | {"scopes": scopes}
    (tmp_path / "lanbridge.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="权限"):
        browser.read_profile(tmp_path)


def test_malformed_profile_sets_error_instead_of_leaving_running_status(browser, monkeypatch):
    fake_login(browser, monkeypatch)
    original = browser.run
    def malformed(command, env, cwd, args, timeout=45):
        result = original(command, env, cwd, args, timeout)
        if args[:2] == ["auth", "create"]:
            path = cwd / snapshot()["path"]
            data = json.loads(path.read_text())
            data["scopes"] = None
            path.write_text(json.dumps(data), encoding="utf-8")
        return result
    monkeypatch.setattr(browser, "run", malformed)
    browser.start()
    browser.thread.join(timeout=5)
    assert not browser.thread.is_alive()
    assert browser.status()["phase"] == "error"
    assert browser.service.store.secret("cf_write_token") == "old-business-secret"
    assert not browser.service.store.secret("cf_oauth_profile")


@pytest.mark.parametrize("failure", ["encryption", "database"])
def test_failed_browser_profile_commit_keeps_discovered_settings_and_credentials(browser, monkeypatch, failure):
    from test_atomic_configuration import reject_write
    prepare_blank_authorization(browser, monkeypatch)
    before = browser.service.settings()
    updated_at = browser.service.store.get("credential_updated_at")
    if failure == "encryption":
        def reject(value):
            raise RuntimeError("private-encryption-diagnostic")
        monkeypatch.setattr(browser.service.store.cipher, "encrypt", reject)
    else:
        reject_write(browser.service.store, "cf_oauth_profile", "secrets")
    browser.start()
    browser.thread.join(timeout=5)
    assert not browser.thread.is_alive()
    assert browser.status()["phase"] == "error"
    assert "private-encryption-diagnostic" not in json.dumps(browser.status())
    assert browser.service.settings() == before
    assert browser.service.store.secret("cf_write_token") == "old-business-secret"
    assert not browser.service.store.secret("cf_oauth_profile")
    assert browser.service.store.get("managed_business_token") is None
    assert browser.service.store.get("credential_updated_at") == updated_at


def test_browser_discovery_cannot_persist_primary_zone_beyond_capacity(browser, monkeypatch):
    prepare_blank_authorization(browser, monkeypatch)
    cfg = browser.service.settings() | {"zones": [
        {"zone_id": format(i, "032x"), "zone_name": f"zone{i}.example.com"} for i in range(100)]}
    browser.service.store.set("settings", cfg)
    before = browser.service.settings()
    browser.start()
    browser.thread.join(timeout=5)
    assert not browser.thread.is_alive()
    assert browser.status()["phase"] == "error"
    assert browser.service.store.get("settings") == cfg
    assert browser.service.settings() == before
    assert browser.service.store.secret("cf_write_token") == "old-business-secret"


def test_early_refresh_failure_keeps_usable_token_and_scopes_status(browser, monkeypatch):
    browser.save_profile(snapshot(expiry=time.time()+60),browser.service.settings())
    monkeypatch.setattr(BrowserAuth,"run",lambda *a,**k: (_ for _ in ()).throw(ValueError("private-refresh-secret")))
    assert browser.access_token()=="oauth-access-secret"
    status=browser.service.oauth_refresh_status()
    assert status["phase"]=="warning" and "管理员登录" in status["message"]
    assert "secret" not in json.dumps(status)
    with pytest.raises(ValueError,match="重试续期"): browser.access_token(force_refresh=True)
    assert browser.service.oauth_refresh_status()["retry_failed"]
    browser.service.store.set_secret("cf_write_token","replacement")
    assert browser.service.oauth_refresh_status() is None


def test_expired_refresh_status_clears_after_success(browser, monkeypatch):
    browser.save_profile(snapshot(expiry=time.time()-60),browser.service.settings())
    monkeypatch.setattr(BrowserAuth,"run",lambda *a,**k: (_ for _ in ()).throw(ValueError("private-refresh-secret")))
    with pytest.raises(ValueError,match="云端管理操作"): browser.access_token()
    assert browser.service.oauth_refresh_status()["phase"]=="error"
    def run(self,command,env,cwd,args,timeout=45):
        (cwd/snapshot()["path"]).write_text(json.dumps(snapshot("renewed-secret")["profile"]))
        return "{}"
    monkeypatch.setattr(BrowserAuth,"run",run)
    assert browser.access_token(force_refresh=True)=="renewed-secret"
    assert browser.service.oauth_refresh_status() is None


def test_manual_renewal_requires_local_admin_and_csrf(browser, monkeypatch):
    from fastapi.testclient import TestClient
    from lanbridge.admin import create_admin
    from lanbridge.store import password_hash
    browser.save_profile(snapshot(),browser.service.settings())
    browser.service.store.set("admin",{"username":"admin","password_hash":password_hash("correct horse battery")})
    calls=[]
    monkeypatch.setattr(BrowserAuth,"access_token",lambda self,**kw:calls.append(kw) or "private-token")
    client=TestClient(create_admin(browser.service),base_url="http://127.0.0.1:8890",headers={"Origin":"http://127.0.0.1:8890"})
    login=client.post("/api/login",json={"username":"admin","password":"correct horse battery"})
    endpoint="/api/cloudflare/browser-authorize-refresh"
    assert client.post(endpoint,json={}).status_code==403
    response=client.post(endpoint,json={},headers={"X-CSRF-Token":login.json()["csrf"]})
    assert response.status_code==200 and response.json()=={"saved":True}
    assert calls==[{"force_refresh":True}]


@pytest.mark.parametrize('seconds,force', [(60,False),(3600,True)])
def test_renewal_expires_only_isolated_copy_to_force_cli_exchange(browser,monkeypatch,seconds,force):
    original=snapshot(expiry=time.time()+seconds)
    browser.save_profile(original,browser.service.settings())
    def run(self,command,env,cwd,args,timeout=45):
        path=cwd/original['path'];copy=json.loads(path.read_text())
        assert copy['expiration_time']=='1970-01-01T00:00:00Z'
        assert json.loads(browser.service.store.secret('cf_oauth_profile'))==original
        path.write_text(json.dumps(snapshot('fresh-access-secret')['profile']))
        return '{}'
    monkeypatch.setattr(BrowserAuth,'run',run)
    assert browser.access_token(force_refresh=force)=='fresh-access-secret'


def test_automatic_failure_backoff_does_not_block_explicit_retry(browser,monkeypatch):
    browser.save_profile(snapshot(expiry=time.time()-60),browser.service.settings());calls=[]
    def run(*args,**kwargs):
        calls.append(1);raise ValueError('private diagnostic')
    monkeypatch.setattr(BrowserAuth,'run',run)
    for _ in range(3):
        with pytest.raises(ValueError):browser.access_token()
    assert len(calls)==1
    with pytest.raises(ValueError):browser.access_token(force_refresh=True)
    assert len(calls)==2


@pytest.mark.parametrize('output,reason',[(b'Token refresh failed: OAuth error: invalid_grant\nprivate-token','invalid_grant'),(b'fetch failed private-token','network'),(b'ETIMEDOUT private-token','timeout'),(b'private-token',None)])
def test_refresh_diagnostic_classification_is_allowlisted(output,reason):
    assert BrowserAuth.classify_refresh_error(output)==reason


def test_refresh_failure_reason_is_safe_and_visible(browser,monkeypatch):
    browser.save_profile(snapshot(expiry=time.time()-60),browser.service.settings())
    def run(self,*args,**kwargs):
        self.refresh_reason='network';raise ValueError('private-refresh-secret')
    monkeypatch.setattr(BrowserAuth,'run',run)
    with pytest.raises(ValueError):browser.access_token()
    result=browser.service.oauth_refresh_status()
    assert result['reason']=='network' and '网络连接错误' in result['message']
    assert 'private-refresh-secret' not in json.dumps(result)
    records=browser.service.store.audit_list()
    assert records[0]['action']=='oauth_refresh_failed'
    assert records[0]['detail']=={'reason':'network','manual_retry':False}
    assert 'private-refresh-secret' not in json.dumps(records)


def test_refresh_cannot_overwrite_new_authorization(browser,monkeypatch):
    browser.save_profile(snapshot(expiry=time.time()-60),browser.service.settings())
    def run(self,command,env,cwd,args,timeout=45):
        (cwd/snapshot()['path']).write_text(json.dumps(snapshot('old-flow-rotation')['profile']))
        browser.save_profile(snapshot('new-login-token'),browser.service.settings())
        return '{}'
    monkeypatch.setattr(BrowserAuth,'run',run)
    with pytest.raises(ValueError,match='已变化'):browser.access_token()
    assert browser.service.store.secret('cf_write_token')=='new-login-token'
    assert browser.service.oauth_refresh_status() is None


def test_create_zone_oauth_forbidden_distinguishes_permissions_from_renewal():
    response=httpx.Response(403,json={"errors":[{"code":0,"message":"private-upstream-secret"}]})
    hint=Cloudflare.failure_hint("POST","/zones",response,"oauth")
    assert "zone.write" in hint and "续期不会扩大权限" in hint
    assert "账户角色与资源范围" in hint and "private-upstream-secret" not in hint
    assert "未能自动续期" not in hint
    dns_hint=Cloudflare.failure_hint("POST","/zones/"+"b"*32+"/dns_records",response,"oauth")
    assert "zone.write" not in dns_hint


def test_project_node_runtime_works_without_path(tmp_path,monkeypatch):
    import subprocess
    node=tmp_path/'bin'/'node-runtime'/('node.exe' if __import__('os').name=='nt' else 'node')
    node.parent.mkdir(parents=True);node.touch()
    monkeypatch.setattr('shutil.which',lambda _:None)
    monkeypatch.setenv('NODE_OPTIONS','--require untrusted.js')
    def run(args,**kwargs):
        assert args==[str(node),'--version'] and 'NODE_OPTIONS' not in kwargs['env']
        return subprocess.CompletedProcess(args,0,b'v24.19.0\n',b'')
    monkeypatch.setattr(subprocess,'run',run)
    assert BrowserAuth.node_runtime(tmp_path)==str(node)


def test_old_project_node_falls_back_to_compatible_path(tmp_path,monkeypatch):
    import subprocess
    node=tmp_path/'bin'/'node-runtime'/('node.exe' if __import__('os').name=='nt' else 'node')
    node.parent.mkdir(parents=True);node.touch();system=tmp_path/'system-node.exe';system.touch()
    monkeypatch.setattr('shutil.which',lambda _:str(system))
    monkeypatch.setattr(subprocess,'run',lambda args,**kwargs:subprocess.CompletedProcess(args,0,b'v22.17.0' if args[0]==str(node) else b'v22.18.0',b''))
    assert BrowserAuth.node_runtime(tmp_path)==str(system)


def test_incompatible_node_is_not_used(tmp_path,monkeypatch):
    import subprocess
    node=tmp_path/'old-node.exe';node.touch()
    monkeypatch.setattr('shutil.which',lambda _:str(node))
    monkeypatch.setattr(subprocess,'run',lambda args,**kwargs:subprocess.CompletedProcess(args,0,b'v20.19.0',b''))
    with pytest.raises(ValueError,match='未找到可运行的 Node.js'):BrowserAuth.node_runtime(tmp_path)


def tunnel_upstream(browser, monkeypatch, failure=None):
    calls = []
    service = browser.service
    monkeypatch.setattr(service.cf, 'create_tunnel', Cloudflare.create_tunnel.__get__(service.cf, Cloudflare))
    def request(method, path, body=None, **kwargs):
        calls.append((method, path))
        if path.startswith('/zones/'):
            return {'name':'example.com', 'account':{'id':'a'*32}, 'status':'active'}
        if method == 'POST':
            if failure == 'create' and sum(m=='POST' for m,p in calls)==1:
                raise RuntimeError('Cloudflare API 网络连接或响应异常')
            return {'id':'11111111-1111-4111-8111-111111111111', 'name':body['name']}
        if path.endswith('/token'):
            if failure == 'token' and sum(p.endswith('/token') for m,p in calls)==1:
                raise RuntimeError('Cloudflare API 网络连接或响应异常')
            return 'test-connection-secret'
        pending = service.store.get('pending_tunnel_create')
        return [{'id':'11111111-1111-4111-8111-111111111111', 'name':pending['name']}]
    monkeypatch.setattr(service.cf, 'request', request)
    return calls


def test_authorization_automatically_creates_tunnel_and_reauthorization_preserves_it(browser, monkeypatch):
    calls = tunnel_upstream(browser, monkeypatch)
    fake_login(browser, monkeypatch)
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()['phase']=='done' and browser.status()['tunnel_ready']
    assert browser.service.settings()['tunnel_id']=='11111111-1111-4111-8111-111111111111'
    assert browser.service.store.secret('tunnel_token')=='test-connection-secret'
    assert sum(m=='POST' for m,p in calls)==1
    before=list(calls)
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()['phase']=='done' and calls==before
    assert 'test-connection-secret' not in json.dumps(browser.status())+json.dumps(browser.service.store.audit_list())


@pytest.mark.parametrize('failure', ['create','token'])
def test_saved_authorization_can_resume_setup_without_duplicate_tunnel_or_new_consent(browser, monkeypatch, failure):
    calls=tunnel_upstream(browser, monkeypatch, failure)
    login_calls=fake_login(browser, monkeypatch)
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()['phase']=='error'
    assert browser.status()['authorization_saved'] and browser.status()['next_action']=='configure_tunnel'
    assert browser.service.store.secret('cf_write_token')=='oauth-access-secret'
    assert not browser.service.store.secret('tunnel_token')
    browser.continue_setup();browser.thread.join(timeout=5)
    assert browser.status()['phase']=='done' and browser.status()['tunnel_ready']
    assert browser.service.store.secret('tunnel_token')=='test-connection-secret'
    assert sum(m=='POST' for m,p in calls)==1
    assert len(login_calls)==2
    assert browser.service.store.get('pending_tunnel_create') is None


def test_changed_account_or_credential_cannot_resume_old_setup(browser, monkeypatch):
    tunnel_upstream(browser, monkeypatch, 'token');fake_login(browser, monkeypatch)
    browser.start();browser.thread.join(timeout=5)
    browser.service.store.set_secret('cf_write_token','changed-private-credential')
    with pytest.raises(ValueError, match='已变化'):
        browser.continue_setup()


def test_saved_authorization_setup_can_resume_after_manager_restart(browser, monkeypatch):
    calls=tunnel_upstream(browser, monkeypatch, 'token');fake_login(browser, monkeypatch)
    browser.start();browser.thread.join(timeout=5)
    failed=browser.status()
    resumed=BrowserAuth(browser.service)
    assert resumed.status()==failed
    assert resumed.status()['next_action']=='configure_tunnel'
    resumed.continue_setup();resumed.thread.join(timeout=5)
    assert resumed.status()['phase']=='done'
    assert sum(m=='POST' for m,p in calls)==1


def test_automatic_setup_retrieves_missing_token_for_configured_tunnel_without_creation(browser, monkeypatch):
    browser.service.store.set('settings',browser.service.settings() | {'tunnel_id':'22222222-2222-4222-8222-222222222222'})
    calls=tunnel_upstream(browser, monkeypatch);fake_login(browser, monkeypatch)
    browser.start();browser.thread.join(timeout=5)
    assert browser.status()['phase']=='done'
    assert calls==[('GET','/accounts/'+'a'*32+'/cfd_tunnel/22222222-2222-4222-8222-222222222222/token')]
    assert browser.service.settings()['tunnel_id']=='22222222-2222-4222-8222-222222222222'


def test_waf_consent_requests_and_persists_scope(browser, monkeypatch):
    from lanbridge.browser_auth import WAF_SCOPE
    calls = fake_login(browser, monkeypatch, scopes=SCOPES + [WAF_SCOPE])
    browser.start(require_waf=True)
    browser.thread.join(timeout=5)
    assert browser.status()["phase"] == "done"
    assert WAF_SCOPE in calls[0][0]
    assert WAF_SCOPE in browser.service.store.get("managed_business_token")["scopes"]


def test_waf_consent_missing_scope_keeps_previous_credentials(browser, monkeypatch):
    calls = fake_login(browser, monkeypatch)
    browser.start(require_waf=True)
    browser.thread.join(timeout=5)
    assert "zone-waf.write" in calls[0][0]
    assert browser.status()["phase"] == "error"
    assert "zone-waf.write" in browser.status()["message"]
    assert browser.service.store.secret("cf_write_token") == "old-business-secret"
    assert not browser.service.store.secret("cf_oauth_profile")


def test_reauthorization_preserves_existing_waf_scope(browser, monkeypatch):
    from lanbridge.browser_auth import WAF_SCOPE
    browser.service.store.set("managed_business_token", {"kind":"oauth", "scopes":[WAF_SCOPE]})
    calls = fake_login(browser, monkeypatch, scopes=SCOPES + [WAF_SCOPE])
    browser.start()
    browser.thread.join(timeout=5)
    assert browser.status()["phase"] == "done"
    assert WAF_SCOPE in calls[0][0]


def test_waf_consent_rejects_non_boolean(browser):
    with pytest.raises(ValueError, match="布尔"):
        browser.start(require_waf="true")
