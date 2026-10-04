import json

import httpx
import pytest

from lanbridge.service import Service
from lanbridge.token_manager import TokenManager


@pytest.fixture
def manager(tmp_path):
    service = Service(tmp_path / "data")
    cfg = service.settings() | {"account_id": "a" * 32, "zone_id": "b" * 32, "zone_name": "example.com"}
    service.store.set("settings", cfg)
    service.store.set_secret("cf_write_token", "old-business-token")
    yield TokenManager(service)
    service.store.db.close()


def groups():
    return [{"id": str(i) * 32, "name": name, "scopes": [scope]} for i, (name, scope) in enumerate([
        ("Cloudflare Tunnel Write", "com.cloudflare.api.account"),
        ("Turnstile Write", "com.cloudflare.api.account"),
        ("DNS Write", "com.cloudflare.api.account.zone"),
        ("Zone Read", "com.cloudflare.api.account.zone"),
        ("Cloudflare Tunnel Read", "com.cloudflare.api.account"),
        ("DNS Read", "com.cloudflare.api.account.zone"),
    ], 1)]


def mock_api(monkeypatch, respond):
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))


def test_create_scoped_token_encrypts_secrets_and_updates_only_owned_token(manager, monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        if request.url.path.endswith("permission_groups"):
            result = groups()
        elif request.url.path.endswith("verify"):
            assert request.headers["authorization"] == "Bearer child-token-secret-value"
            result = {"id": "c" * 32}
        elif request.method == "GET":
            result = {"id": "c" * 32, "condition": {"request_ip": {"in": ["192.0.2.1/32"]}}, "expires_on": "2030-01-01T00:00:00Z", "status": "disabled"}
        elif request.method == "POST":
            result = {"id": "c" * 32, "value": "child-token-secret-value"}
        else:
            assert request.method == "PUT" and request.url.path.endswith("/" + "c" * 32)
            result = {"id": "c" * 32}
        return httpx.Response(200, json={"success": True, "result": result})
    mock_api(monkeypatch, respond)
    result = manager.provision("authority-token-secret-value", remember=True)
    assert result["action"] == "created" and "value" not in result
    policies = json.loads(calls[1].content)["policies"]
    assert policies[0]["resources"] == {"com.cloudflare.api.account." + "a" * 32: "*"}
    assert policies[1]["resources"] == {"com.cloudflare.api.account.zone." + "b" * 32: "*"}
    assert len(policies[0]["permission_groups"]) == 2
    assert manager.service.store.secret("cf_write_token") == "child-token-secret-value"
    assert manager.service.store.secret("cf_token_authority") == "authority-token-secret-value"
    assert manager.provision(human_check=False)["action"] == "updated"
    assert len(json.loads(calls[-1].content)["policies"][0]["permission_groups"]) == 1
    assert json.loads(calls[-1].content)["status"] == "disabled"
    assert json.loads(calls[-1].content)["expires_on"] == "2030-01-01T00:00:00Z"
    assert json.loads(calls[-1].content)["condition"] == {"request_ip": {"in": ["192.0.2.1/32"]}}
    assert len([r for r in calls if r.method == "POST"]) == 1
    plaintext = json.dumps(manager.service.store.audit_list()) + str(manager.service.store.db.execute("SELECT * FROM secrets").fetchall())
    assert "child-token-secret-value" not in plaintext and "authority-token-secret-value" not in plaintext


def test_missing_permission_blocks_mutation_and_preserves_existing_credentials(manager, monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"success": True, "result": groups()[1:]})
    mock_api(monkeypatch, respond)
    with pytest.raises(ValueError, match="未创建或修改"):
        manager.provision("authority-token-secret-value")
    assert all(r.method == "GET" for r in calls)
    assert manager.service.store.secret("cf_write_token") == "old-business-token"


@pytest.mark.parametrize("failure", ["forbidden", "timeout"])
def test_create_failure_is_safe_and_unknown_result_is_not_repeated(manager, monkeypatch, failure):
    calls = []
    def respond(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "result": groups()})
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-upstream-text")
        return httpx.Response(403, json={"success": False, "errors": [{"code": 10000, "message": "secret-upstream-text"}]})
    mock_api(monkeypatch, respond)
    with pytest.raises(RuntimeError) as exc:
        manager.provision("authority-token-secret-value", remember=True)
    assert "secret-upstream-text" not in str(exc.value)
    assert manager.service.store.secret("cf_write_token") == "old-business-token"
    assert not manager.service.store.secret("cf_token_authority")
    if failure == "timeout":
        with pytest.raises(ValueError, match="不会重复创建"):
            manager.provision("authority-token-secret-value")
        assert sum(r.method == "POST" for r in calls) == 1
    else:
        assert manager.service.store.get("pending_business_token") is None


def test_one_time_authority_is_not_saved(manager, monkeypatch):
    mock_api(monkeypatch, lambda request: httpx.Response(200, json={"success": True, "result": groups() if request.method == "GET" else {"id": "c" * 32, "value": "child-token-secret-value"}}))
    manager.provision("authority-token-secret-value")
    assert not manager.service.store.secret("cf_token_authority")


def test_management_endpoint_requires_auth_csrf_and_never_returns_token(manager, monkeypatch):
    from fastapi.testclient import TestClient
    from lanbridge.admin import create_admin
    from lanbridge.store import password_hash
    service = manager.service
    service.store.set("admin", {"username": "admin", "password_hash": password_hash("correct horse battery")})
    calls = []
    def provision(*args):
        calls.append(args)
        return {"action": "created", "id": "c" * 32, "saved": True}
    monkeypatch.setattr(TokenManager, "provision", lambda self, *args: provision(*args))
    client = TestClient(create_admin(service), base_url="http://127.0.0.1:8890", headers={"Origin": "http://127.0.0.1:8890"})
    data = {"authority": "authority-token-secret-value", "remember": True, "human_check": True}
    assert client.post("/api/cloudflare/provision-token", json=data).status_code == 401
    login = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"})
    assert login.status_code == 200
    assert client.post("/api/cloudflare/provision-token", json=data).status_code == 403
    response = client.post("/api/cloudflare/provision-token", json=data, headers={"X-CSRF-Token": login.json()["csrf"]})
    assert response.status_code == 200 and "authority-token-secret-value" not in response.text
    assert len(calls) == 1
    assert calls[0][-2:] == ("write", False)


def test_create_read_only_token_preserves_write_token_and_uses_no_write_permissions(manager, monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"success": True, "result": groups() if request.method == "GET" else {"id": "c" * 32, "value": "new-read-only-secret"}})
    mock_api(monkeypatch, respond)
    result = manager.provision("authority-token-secret-value", target="read")
    assert result["target"] == "read" and result["action"] == "created"
    policies = json.loads(calls[-1].content)["policies"]
    assert {g["id"] for p in policies for g in p["permission_groups"]} == {"4" * 32, "5" * 32, "6" * 32}
    assert policies[0]["resources"] == {"com.cloudflare.api.account." + "a" * 32: "*"}
    assert policies[1]["resources"] == {"com.cloudflare.api.account.zone." + "b" * 32: "*"}
    assert manager.service.store.secret("cf_write_token") == "old-business-token"
    assert manager.service.store.secret("cf_read_token") == "new-read-only-secret"
    assert manager.service.store.get("managed_read_token")["id"] == "c" * 32
    assert manager.service.store.get("managed_business_token") is None
    assert "new-read-only-secret" not in json.dumps(result)
    from fastapi.testclient import TestClient
    from lanbridge.admin import create_admin
    from lanbridge.store import password_hash
    manager.service.store.set("admin", {"username": "admin", "password_hash": password_hash("correct horse battery")})
    client = TestClient(create_admin(manager.service), base_url="http://127.0.0.1:8890", headers={"Origin": "http://127.0.0.1:8890"})
    client.post("/api/login", json={"username": "admin", "password": "correct horse battery"})
    snapshot = client.get("/api/state")
    assert snapshot.status_code == 200
    assert "credential_digest" not in snapshot.text and "new-read-only-secret" not in snapshot.text
    assert snapshot.json()["token_management"]["managed_read"]["id"] == "c" * 32


@pytest.mark.parametrize("target", ["read", "write"])
def test_repair_current_token_preserves_secret_policies_and_restrictions(manager, monkeypatch, target):
    credential = "cf_" + target + "_token"
    manager.service.store.set_secret(credential, "current-secret-to-repair")
    original_policies = [{"id": "d" * 32, "effect": "deny", "permission_groups": [{"id": "8" * 32}], "resources": {"other-resource": "*"}}]
    remote = {"id": "c" * 32, "name": "Existing shared token", "policies": original_policies, "condition": {"request_ip": {"in": ["192.0.2.1/32"]}}, "expires_on": "2030-01-01T00:00:00Z", "not_before": "2020-01-01T00:00:00Z", "status": "active"}
    calls = []
    def respond(request):
        calls.append(request)
        if request.url.path.endswith("permission_groups"):
            result = groups()
        elif request.url.path.endswith("verify"):
            assert request.headers["authorization"] == "Bearer current-secret-to-repair"
            result = {"id": "c" * 32}
        elif request.method == "GET":
            assert request.headers["authorization"] == "Bearer authority-token-secret-value"
            result = remote
        else:
            assert request.method == "PUT"
            body = json.loads(request.content)
            for key in ("name", "condition", "expires_on", "not_before", "status"):
                assert body[key] == remote[key]
            assert body["policies"][0] == original_policies[0]
            assert len(body["policies"]) == 3
            remote["policies"] = body["policies"]
            result = {"id": "c" * 32}
        return httpx.Response(200, json={"success": True, "result": result})
    mock_api(monkeypatch, respond)
    assert manager.provision("authority-token-secret-value", target=target, repair_existing=True)["action"] == "updated"
    assert manager.service.store.secret(credential) == "current-secret-to-repair"
    # Subsequent updates of adopted tokens still preserve policies and avoid duplicates.
    assert manager.provision("authority-token-secret-value", target=target)["action"] == "updated"
    assert all(request.method != "POST" for request in calls)


def test_repair_refuses_mismatched_remote_id_without_mutation(manager, monkeypatch):
    manager.service.store.set_secret("cf_read_token", "current-read-secret")
    calls = []
    def respond(request):
        calls.append(request)
        result = groups() if request.url.path.endswith("permission_groups") else {"id": ("c" if request.url.path.endswith("verify") else "d") * 32}
        return httpx.Response(200, json={"success": True, "result": result})
    mock_api(monkeypatch, respond)
    with pytest.raises(ValueError, match="不一致"):
        manager.provision("authority-token-secret-value", target="read", repair_existing=True)
    assert all(r.method == "GET" for r in calls)
    assert manager.service.store.secret("cf_read_token") == "current-read-secret"


def test_unknown_read_token_creation_blocks_duplicates_independently(manager, monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "result": groups()})
        raise httpx.ReadTimeout("test-only-timeout")
    mock_api(monkeypatch, respond)
    with pytest.raises(RuntimeError):
        manager.provision("authority-token-secret-value", target="read")
    with pytest.raises(ValueError, match="不会重复创建"):
        manager.provision("authority-token-secret-value", target="read")
    assert sum(r.method == "POST" for r in calls) == 1
    assert manager.service.store.get("pending_read_token")
    assert manager.service.store.get("pending_business_token") is None
