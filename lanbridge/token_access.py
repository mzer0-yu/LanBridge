"""Read-only discovery and atomic API Token onboarding."""
import json
import time
from .browser_auth import BrowserAuth
from .models import Settings
from .service import digest


class TokenAccess:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def token(value):
        if not isinstance(value, str) or not 10 <= len(value.strip()) <= 4096:
            raise ValueError("请填写有效的 API Token")
        return value.strip()

    def revision(self):
        s = self.service
        return digest([s.settings(), s.store.secret("cf_write_token"), s.store.get("sites", []), s.store.get("published_hosts", [])])

    def discover(self, value):
        token = self.token(value)
        with self.service.lock:
            revision = self.revision()
            cfg = self.service.settings()
            bound = bool(cfg["zones"] or self.service.sites() or cfg["tunnel_id"])
            fixed_default = bool(self.service.sites() or cfg["tunnel_id"])
        try:
            zones = BrowserAuth.discover_zones(token)
        except ValueError:
            raise ValueError("无法读取账户与域名。请检查令牌是否有效、网络是否正常，以及是否具有 Zone Read 权限；也可手动配置账户与域名。") from None
        choices = [z for z in zones if (not bound or not cfg["account_id"] or z["account_id"] == cfg["account_id"]) and (not fixed_default or not cfg["zone_id"] or z["zone_id"] == cfg["zone_id"])]
        if not choices:
            raise ValueError("未找到可接入的 Active 域名。请核对令牌的域名资源范围；已有网站或隧道时需保留当前账户与默认域名。")
        with self.service.lock:
            if revision != self.revision():
                raise ValueError("配置已变化，请重新读取账户与域名")
        return {"zones": choices, "revision": revision}

    def connect(self, data):
        token = self.token(data.get("token"))
        save_token = data.get("save_token", True)
        if not isinstance(save_token, bool):
            raise ValueError("令牌保存选项无效")
        discovered = self.discover(token)
        if data.get("revision") != discovered["revision"]:
            raise ValueError("配置已变化，请重新读取账户与域名")
        selected = next((z for z in discovered["zones"] if z["zone_id"] == data.get("zone_id") and z["account_id"] == data.get("account_id")), None)
        if selected is None:
            raise ValueError("请选择此令牌可以访问的账户与域名")
        s = self.service
        with s.lock:
            if discovered["revision"] != self.revision():
                raise ValueError("配置已变化，请重新读取账户与域名")
            old = s.settings()
            if old["account_id"] and old["zones"] and old["account_id"] != selected["account_id"]:
                raise ValueError("已有域名属于其他账户，请使用独立数据目录")
            cfg = Settings(**(old | {k: selected[k] for k in ("account_id", "zone_id", "zone_name")})).model_dump()
            values = {}
            if save_token:
                values["cf_write_token"] = token
                read = data.get("cf_read_token", "")
                if not isinstance(read, str) or (read.strip() and not 10 <= len(read.strip()) <= 4096):
                    raise ValueError("只读令牌长度或格式无效")
                if read.strip():
                    values["cf_read_token"] = read.strip()
            encrypted = {k: s.store.cipher.encrypt(v.encode()).decode() for k, v in values.items()}
            changed = s.store.get("credential_updated_at", {}) | {k: time.time() for k in values}
            with s.store.lock, s.store.db:
                s.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("settings", json.dumps(cfg)))
                for key, value in encrypted.items():
                    s.store.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (key, value))
                if save_token:
                    s.store.db.execute("DELETE FROM secrets WHERE key=?", ("cf_oauth_profile",))
                    for key in ("managed_business_token", "pending_business_token", "pending_browser_token"):
                        s.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, "null"))
                    if "cf_read_token" in values:
                        for key in ("managed_read_token", "pending_read_token"):
                            s.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, "null"))
                    s.store.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("credential_updated_at", json.dumps(changed)))
            s.store.audit("api_token_connected" if save_token else "settings_saved", {"account_id": selected["account_id"], "zone_id": selected["zone_id"]})
        return {"saved": True, "settings": cfg}
