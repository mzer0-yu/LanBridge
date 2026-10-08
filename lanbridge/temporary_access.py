import re


def allowed(method, path, permissions=None):
    permissions = ["sites"] if permissions is None else permissions
    if method == "GET":
        return path in {"/api/state", "/api/bootstrap"}
    if method != "POST":
        return False
    if path in {"/api/logout", "/api/token-login"}:
        return True
    if "sites" in permissions and (path in {"/api/sites", "/api/cloudflare/preview", "/api/cloudflare/apply", "/api/cloudflare/check", "/api/connector/start", "/api/connector/stop"} or re.fullmatch(r"/api/sites/[^/]+/(pause|probe)", path)):
        return True
    return "account" in permissions and (path in {"/api/settings", "/api/credentials", "/api/zones", "/api/cloudflare/token-discover", "/api/cloudflare/token-connect", "/api/cloudflare/zones", "/api/cloudflare/provision-token", "/api/cloudflare/forget-token-authority", "/api/cloudflare/create-tunnel", "/api/cloudflare/browser-authorize", "/api/cloudflare/browser-authorize-cancel", "/api/cloudflare/browser-authorize-restart", "/api/cloudflare/browser-authorize-select-zone", "/api/cloudflare/browser-authorize-setup"} or bool(re.fullmatch(r"/api/zones/[^/]+/(?:remove|default)", path)))
