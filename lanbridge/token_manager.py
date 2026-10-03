"""Provision only LanBridge-owned user API tokens using a separate authority."""
from __future__ import annotations

import json
import re
import secrets
import time

import httpx


class TokenManager:
    def __init__(self, service):
        self.service = service

    def request(self, token, method, path, body=None):
        try:
            with httpx.Client(timeout=25, trust_env=False, follow_redirects=False) as client:
                response = client.request(method, "https://api.cloudflare.com/client/v4" + path,
                                          headers={"Authorization": "Bearer " + token}, json=body)
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                raise RuntimeError("Cloudflare 令牌管理响应格式异常，请稍后核对。")
            if response.status_code >= 400 or not payload.get("success"):
                codes = [str(e["code"]) for e in payload.get("errors", []) if isinstance(e, dict) and isinstance(e.get("code"), int)]
                suffix = "，错误码 " + ",".join(codes[:3]) if codes else ""
                raise RuntimeError(f"Cloudflare 令牌管理 HTTP {response.status_code}{suffix}。请使用 Create Additional Tokens 模板创建的用户授权令牌（API Tokens Write），并检查有效期及 IP 限制。")
            return payload.get("result")
        except httpx.HTTPError:
            raise RuntimeError("Cloudflare 令牌管理网络异常；若创建结果未知，请按页面提示核对，勿重复创建。") from None

    @staticmethod
    def policies(groups, cfg, human_check):
        if not isinstance(groups, list) or any(not isinstance(g, dict) for g in groups):
            raise ValueError("Cloudflare 权限列表格式异常；未创建或修改令牌。")
        def group(names, scope):
            for name in names:
                matches = [g for g in groups if g.get("name") == name and scope in g.get("scopes", []) and g.get("is_selectable", True)]
                if len(matches) == 1 and re.fullmatch(r"[a-fA-F0-9]{32}", matches[0].get("id", "")):
                    return {"id": matches[0]["id"]}
            raise ValueError("Cloudflare 未提供所需权限：" + names[0] + "；未创建或修改令牌。")
        account_scope, zone_scope = "com.cloudflare.api.account", "com.cloudflare.api.account.zone"
        account = [group(["Cloudflare Tunnel Write", "Cloudflare One Connector: cloudflared Write", "Cloudflare One Connectors Write"], account_scope)]
        if human_check:
            account.append(group(["Turnstile Write", "Turnstile Edit"], account_scope))
        zone = [group(["DNS Write", "DNS Edit"], zone_scope), group(["Zone Read"], zone_scope)]
        return [
            {"effect": "allow", "permission_groups": account, "resources": {account_scope + "." + cfg["account_id"]: "*"}},
            {"effect": "allow", "permission_groups": zone, "resources": {zone_scope + "." + cfg["zone_id"]: "*"}},
        ]

    def provision(self, authority="", remember=False, human_check=True):
        with self.service.lock:
            store = self.service.store
            cfg = self.service.settings()
            if not all(re.fullmatch(r"[a-fA-F0-9]{32}", cfg[k]) for k in ("account_id", "zone_id")) or not cfg["zone_name"]:
                raise ValueError("请先保存 Account ID、Zone ID 和 Zone 名称。")
            authority = authority.strip() or store.secret("cf_token_authority")
            if not 10 <= len(authority) <= 4096:
                raise ValueError("请提供 API Tokens Write 授权令牌。")
            groups = self.request(authority, "GET", "/user/tokens/permission_groups")
            policies = self.policies(groups, cfg, human_check)
            owned = store.get("managed_business_token")
            if owned:
                if owned["account_id"] != cfg["account_id"] or owned["zone_id"] != cfg["zone_id"]:
                    raise ValueError("托管令牌绑定的账户或域名已变化，请先使用手动配置切换业务令牌。")
                # Verify the local credential is still the token we created before updating it.
                verified = self.request(store.secret("cf_write_token"), "GET", "/user/tokens/verify")
                if not isinstance(verified, dict) or verified.get("id") != owned["id"]:
                    raise ValueError("当前业务令牌与托管记录不一致，未修改远端令牌。")
                result = self.request(authority, "PUT", "/user/tokens/" + owned["id"],
                                      {"name": owned["name"], "status": "active", "policies": policies})
                if not isinstance(result, dict) or result.get("id") != owned["id"]:
                    raise RuntimeError("Cloudflare 返回的令牌 ID 不匹配，未更新本机记录。")
                action = "updated"
            else:
                pending = store.get("pending_business_token")
                if pending:
                    raise ValueError("先前创建结果未知，请在 Cloudflare 核对令牌名称 " + pending["name"] + "；已有令牌可通过手动配置接入，平台不会重复创建。")
                name = "LanBridge-" + secrets.token_hex(8)
                store.set("pending_business_token", {"name": name, "requested_at": time.time()})
                try:
                    result = self.request(authority, "POST", "/user/tokens", {"name": name, "policies": policies})
                except RuntimeError as exc:
                    if re.search(r"HTTP 4\d\d", str(exc)):
                        store.set("pending_business_token", None)
                    raise
                if not isinstance(result, dict) or not re.fullmatch(r"[a-fA-F0-9]{32}", result.get("id", "")) or not isinstance(result.get("value"), str) or not 10 <= len(result["value"]) <= 4096:
                    raise RuntimeError("Cloudflare 未返回完整令牌，创建结果需在 Cloudflare 核对。")
                owned = {"id": result["id"], "name": name, "account_id": cfg["account_id"], "zone_id": cfg["zone_id"]}
                action = "created"
            owned = owned | {"human_check": human_check, "updated_at": time.time()}
            # Credential and ownership record commit together; no token secrets in API results.
            with store.lock, store.db:
                if action == "created":
                    store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", ("cf_write_token", store.cipher.encrypt(result["value"].encode()).decode()))
                if remember:
                    store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", ("cf_token_authority", store.cipher.encrypt(authority.encode()).decode()))
                store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("managed_business_token", json.dumps(owned)))
                store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("pending_business_token", "null"))
            store.audit("business_token_" + action, {"id": owned["id"], "human_check": human_check})
            return {"action": action, "id": owned["id"], "saved": True, "human_check": human_check}
