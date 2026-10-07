from copy import deepcopy
import pytest
from lanbridge.browser_auth import BrowserAuth
from lanbridge.token_access import TokenAccess
from test_security import service, admin_client

TOKEN = "test-onboarding-token"
ZONES = [dict(account_id="a"*32, account_name="First", zone_id="b"*32, zone_name="example.com"), dict(account_id="d"*32, account_name="Second", zone_id="c"*32, zone_name="other.com")]


def mock_discovery(monkeypatch):
    monkeypatch.setattr(BrowserAuth, "discover_zones", staticmethod(lambda token: deepcopy(ZONES)))


def connect_data(access, **extra):
    result = access.discover(TOKEN)
    return dict(token=TOKEN, revision=result["revision"], account_id="a"*32, zone_id="b"*32) | extra


def test_blank_setup_discovers_multiple_accounts_without_saving(service, monkeypatch):
    service.store.set("settings", {})
    mock_discovery(monkeypatch)
    original = service.store.secret("cf_write_token")
    result = TokenAccess(service).discover(TOKEN)
    assert len(result["zones"]) == 2
    assert service.settings()["account_id"] == ""
    assert service.store.secret("cf_write_token") == original
    assert TOKEN not in str(result)


def test_selected_domain_and_token_saved_together(service, monkeypatch):
    service.store.set("settings", {})
    service.store.set_secret("cf_oauth_profile", "old-profile")
    mock_discovery(monkeypatch)
    access = TokenAccess(service)
    result = access.connect(connect_data(access, cf_read_token="valid-optional-read-token"))
    assert result["settings"]["account_id"] == "a"*32
    assert result["settings"]["zone_name"] == "example.com"
    assert service.store.secret("cf_write_token") == TOKEN
    assert service.store.secret("cf_read_token") == "valid-optional-read-token"
    assert not service.store.secret("cf_oauth_profile")
    assert TOKEN not in str(result)


def test_authority_discovery_does_not_replace_working_credential(service, monkeypatch):
    mock_discovery(monkeypatch)
    access = TokenAccess(service)
    original = service.store.secret("cf_write_token")
    access.connect(connect_data(access, save_token=False))
    assert service.store.secret("cf_write_token") == original


def test_stale_selection_and_forged_selection_preserve_config(service, monkeypatch):
    mock_discovery(monkeypatch)
    access = TokenAccess(service)
    data = connect_data(access)
    old = service.settings()
    with pytest.raises(ValueError):
        access.connect(data | {"zone_id":"f"*32})
    assert service.settings() == old
    service.store.set("settings", old | {"tunnel_name":"Changed"})
    with pytest.raises(ValueError, match="配置已变化"):
        access.connect(data)
    assert service.settings()["tunnel_name"] == "Changed"


def test_invalid_optional_token_prevents_partial_save(service, monkeypatch):
    service.store.set("settings", {})
    mock_discovery(monkeypatch)
    access = TokenAccess(service)
    old = service.settings()
    original = service.store.secret("cf_write_token")
    with pytest.raises(ValueError):
        access.connect(connect_data(access, cf_read_token="short"))
    assert service.settings() == old
    assert service.store.secret("cf_write_token") == original


def test_existing_domain_binding_filters_other_accounts(service, monkeypatch):
    mock_discovery(monkeypatch)
    choices = TokenAccess(service).discover(TOKEN)["zones"]
    assert choices == [ZONES[0]]


def test_missing_zone_read_permission_has_safe_manual_fallback(service, monkeypatch):
    def fail(token):
        raise ValueError("raw secret " + token)
    monkeypatch.setattr(BrowserAuth, "discover_zones", staticmethod(fail))
    with pytest.raises(ValueError, match="手动配置") as exc:
        TokenAccess(service).discover(TOKEN)
    assert TOKEN not in str(exc.value)


def test_api_requires_authentication_and_csrf(service, monkeypatch):
    mock_discovery(monkeypatch)
    client = admin_client(service)
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/cloudflare/token-discover", json={"token":TOKEN}).status_code == 403
    assert client.post("/api/cloudflare/token-connect", json={"token":TOKEN}).status_code == 403


def test_guarded_api_can_discover_and_connect(service, monkeypatch):
    service.store.set("settings", {})
    mock_discovery(monkeypatch)
    client = admin_client(service)
    response = client.post("/api/cloudflare/token-discover", json={"token":TOKEN})
    assert response.status_code == 200
    data = dict(token=TOKEN, revision=response.json()["revision"], account_id="a"*32, zone_id="b"*32)
    response = client.post("/api/cloudflare/token-connect", json=data)
    assert response.status_code == 200
    assert TOKEN not in response.text
    assert service.settings()["zone_id"] == "b"*32
