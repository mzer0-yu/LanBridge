"""Provision scoped tokens or explicitly repair the configured user's token."""
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
                errors = payload.get("errors") or []
                codes = [str(e["code"]) for e in errors if isinstance(e, dict) and isinstance(e.get("code"), int)] if isinstance(errors, list) else []
                suffix = "，错误码 " + ",".join(codes[:3]) if codes else ""
                detail = f"Cloudflare 令牌管理 HTTP {response.status_code}{suffix}。请使用 Create Additional Tokens 模板创建的用户授权令牌（API Tokens Write），并检查有效期及 IP 限制。"
                self.service.store.set("token_management_error", {"detail": detail, "checked_at": time.time()})
                raise RuntimeError(detail)
            return payload.get("result")
        except httpx.HTTPError:
            raise RuntimeError("Cloudflare 令牌管理网络异常；若创建结果未知，请按页面提示核对，勿重复创建。") from None

    @staticmethod
    def policies(groups, cfg, human_check, target="write"):
        if not isinstance(groups, list) or any(not isinstance(g, dict) for g in groups):
            raise ValueError("Cloudflare 权限列表格式异常；未创建或修改令牌。")
        def group(names, scope):
            for name in names:
                matches = [g for g in groups if g.get("name") == name and isinstance(g.get("scopes"), list) and scope in g["scopes"] and g.get("is_selectable", True)]
                if len(matches) == 1 and isinstance(matches[0].get("id"), str) and re.fullmatch(r"[a-fA-F0-9]{32}", matches[0]["id"]):
                    return {"id": matches[0]["id"]}
            raise ValueError("Cloudflare 未提供所需权限：" + names[0] + "；未创建或修改令牌。")
        account_scope, zone_scope = "com.cloudflare.api.account", "com.cloudflare.api.account.zone"
        account = [group(["Cloudflare Tunnel Read", "Cloudflare One Connector: cloudflared Read", "Cloudflare One Connectors Read"] if target == "read" else ["Cloudflare Tunnel Write", "Cloudflare One Connector: cloudflared Write", "Cloudflare One Connectors Write"], account_scope)]
        if human_check and target == "write":
            account.append(group(["Turnstile Write", "Turnstile Edit"], account_scope))
        zone = [group(["DNS Read"] if target == "read" else ["DNS Write", "DNS Edit"], zone_scope), group(["Zone Read"], zone_scope)]
        return [
            {"effect": "allow", "permission_groups": account, "resources": {account_scope + "." + cfg["account_id"]: "*"}},
            {"effect": "allow", "permission_groups": zone, "resources": {zone_scope + "." + cfg["zone_id"]: "*"}},
        ]

    def provision(self, authority="", remember=False, human_check=True, target="write", repair_existing=False, force_new=False):
        with self.service.lock:
            if target not in ("write", "read") or not isinstance(repair_existing, bool) or not isinstance(force_new, bool) or (force_new and repair_existing):
                raise ValueError("令牌类型或修复选项无效")
            store = self.service.store
            cfg = self.service.settings()
            credential = "cf_" + target + "_token"
            record_key = "managed_read_token" if target == "read" else "managed_business_token"
            pending_key = "pending_read_token" if target == "read" else "pending_business_token"
            if not all(re.fullmatch(r"[a-fA-F0-9]{32}", cfg[k]) for k in ("account_id", "zone_id")) or not cfg["zone_name"]:
                raise ValueError("请先保存 Account ID、Zone ID 和 Zone 名称。")
            if target == "write" and store.get("pending_browser_token"):
                raise ValueError("浏览器令牌创建结果未知，请先在 Cloudflare 核对并手动接入。")
            authority = authority.strip() or store.secret("cf_token_authority")
            if not 10 <= len(authority) <= 4096:
                raise ValueError("请提供 API Tokens Write 授权令牌。")
            if not force_new and target == "write" and (store.get("managed_business_token") or {}).get("kind") == "account":
                raise ValueError("当前是浏览器授权创建的账户令牌，请重新使用浏览器授权创建，或在 Cloudflare 账户 API Tokens 编辑权限。")
            groups = self.request(authority, "GET", "/user/tokens/permission_groups")
            policies = self.policies(groups, cfg, human_check, target)
            owned = None if force_new else store.get(record_key)
            if repair_existing:
                current = store.secret(credential)
                if not current:
                    raise ValueError("未保存当前令牌，请先保存令牌或选择自动创建")
                if current == authority:
                    raise ValueError("授权令牌与业务令牌相同，请使用独立的 API Tokens Write 授权令牌")
                verified = self.request(current, "GET", "/user/tokens/verify")
                if not isinstance(verified, dict) or not isinstance(verified.get("id"), str) or not re.fullmatch(r"[a-fA-F0-9]{32}", verified["id"]):
                    raise ValueError("无法核对当前用户令牌 ID，未修改任何令牌；请创建新令牌或检查有效期、IP 限制")
                from .service import digest
                owned = {"id": verified["id"], "account_id": cfg["account_id"], "zone_id": cfg["zone_id"], "credential_digest": digest(current), "external": True}
            if owned:
                if owned["account_id"] != cfg["account_id"] or owned["zone_id"] != cfg["zone_id"]:
                    raise ValueError("托管令牌绑定的账户或域名已变化，请先使用手动配置切换业务令牌。")
                # Verify the local credential is still the token we created before updating it.
                from .service import digest
                if owned.get("credential_digest"):
                    if owned["credential_digest"] != digest(store.secret(credential)):
                        raise ValueError("当前业务令牌与托管记录不一致，未修改远端令牌。")
                else:
                    verified = self.request(store.secret(credential), "GET", "/user/tokens/verify")
                    if not isinstance(verified, dict) or verified.get("id") != owned["id"]:
                        raise ValueError("当前业务令牌与托管记录不一致，未修改远端令牌。")
                remote = self.request(authority, "GET", "/user/tokens/" + owned["id"])
                if not isinstance(remote, dict) or remote.get("id") != owned["id"]:
                    raise ValueError("当前业务令牌与托管记录不一致，未修改远端令牌。")
                if owned.get("external"):
                    existing = remote.get("policies")
                    if not isinstance(existing, list) or any(not isinstance(p, dict) for p in existing):
                        raise ValueError("无法读取原令牌权限，未修改令牌")
                    # Preserve every original policy, including denies and other resources.
                    policies = existing + [p for p in policies if not any(e.get("effect") == "allow" and e.get("resources") == p["resources"] and {g["id"] for g in p["permission_groups"]}.issubset({g.get("id") for g in e.get("permission_groups", []) if isinstance(g, dict)}) for e in existing)]
                owned["name"] = remote.get("name") or owned.get("name") or "LanBridge-" + target
                body = {"name": owned["name"], "policies": policies}
                for key in ("condition", "expires_on", "not_before", "status"):
                    if remote.get(key) is not None:
                        body[key] = remote[key]
                result = self.request(authority, "PUT", "/user/tokens/" + owned["id"],
                                      body)
                if not isinstance(result, dict) or result.get("id") != owned["id"]:
                    raise RuntimeError("Cloudflare 返回的令牌 ID 不匹配，未更新本机记录。")
                action = "updated"
            else:
                pending = store.get(pending_key)
                if pending:
                    raise ValueError("先前创建结果未知，请在 Cloudflare 核对令牌名称 " + pending["name"] + "；已有令牌可通过手动配置接入，平台不会重复创建。")
                name = "LanBridge-" + target + "-" + secrets.token_hex(8)
                store.set(pending_key, {"name": name, "requested_at": time.time()})
                try:
                    result = self.request(authority, "POST", "/user/tokens", {"name": name, "policies": policies})
                except RuntimeError as exc:
                    if re.search(r"HTTP 4\d\d", str(exc)):
                        store.set(pending_key, None)
                    raise
                if not isinstance(result, dict) or not isinstance(result.get("id"), str) or not re.fullmatch(r"[a-fA-F0-9]{32}", result["id"]) or not isinstance(result.get("value"), str) or not 10 <= len(result["value"]) <= 4096:
                    raise RuntimeError("Cloudflare 未返回完整令牌，创建结果需在 Cloudflare 核对。")
                from .service import digest
                owned = {"id": result["id"], "name": name, "account_id": cfg["account_id"], "zone_id": cfg["zone_id"], "credential_digest": digest(result["value"])}
                action = "created"
            owned = owned | {"human_check": human_check if target == "write" else False, "updated_at": time.time()}
            # Credential and ownership record commit together; no token secrets in API results.
            with store.lock, store.db:
                if action == "created":
                    store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (credential, store.cipher.encrypt(result["value"].encode()).decode()))
                if remember:
                    store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", ("cf_token_authority", store.cipher.encrypt(authority.encode()).decode()))
                store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (record_key, json.dumps(owned)))
                store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (pending_key, "null"))
                changed = store.get("credential_updated_at", {}) | {credential: time.time()}
                store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("credential_updated_at", json.dumps(changed)))
            store.audit(("read_token_" if target == "read" else "business_token_") + action, {"id": owned["id"], "human_check": owned["human_check"], "repair_existing": repair_existing})
            store.set("token_management_error", None)
            return {"action": action, "id": owned["id"], "saved": True, "human_check": owned["human_check"], "target": target, "repair_existing": repair_existing}
