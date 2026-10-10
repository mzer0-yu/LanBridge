"""Persisted, selective Cloudflare pause; never replace unrelated WAF rules."""
import logging
import secrets
import threading
import time


class SitePause:
    ACTIVE = {"queued", "running"}
    PHASE = "http_request_firewall_custom"

    def __init__(self, service):
        self.service = service
        self.thread = None
        self.stopping = False
        self.lifecycle_lock = threading.Lock()
        from .paused_traffic import PausedTraffic
        self.traffic = PausedTraffic()

    def status(self):
        return self.service.store.get("site_pause", {})

    @staticmethod
    def requires_cloud_access(entry):
        # Unknown/interrupted outcomes need reconciliation before local resume.
        return bool(entry and (entry.get("desired") or entry.get("phase") != "succeeded"))

    def _save(self, site_id, **values):
        with self.service.lock:
            entries = self.status()
            entries[site_id] = entries.get(site_id, {}) | values | {"updated_at": time.time()}
            self.service.store.set("site_pause", entries)

    def guard_edit(self, site_id):
        entry = self.status().get(site_id, {})
        if self.requires_cloud_access(entry):
            raise ValueError("请先在编辑窗口中解除 Cloudflare 阻断，或恢复转发，再修改网站配置")

    def recover(self):
        for site_id, entry in self.status().items():
            if entry.get("phase") in self.ACTIVE:
                self._save(site_id, phase="failed", message="云端操作被中断，结果待核对；请重试")

    def submit(self, site_id, paused, cloudflare=False, *, automatic_reason=None):
        if not isinstance(paused, bool) or not isinstance(cloudflare, bool):
            raise ValueError("暂停状态和 Cloudflare 联动选项必须为布尔值")
        with self.service.lock:
            if self.stopping:
                raise ValueError("平台正在停止，请稍后重试")
            site = next((s for s in self.service.sites() if s["id"] == site_id), None)
            if not site or not site["enabled"]:
                raise ValueError("网站不存在或已停用")
            entry = self.status().get(site_id)
            if entry and entry.get("account_id") != self.service.settings()["account_id"]:
                raise ValueError("Cloudflare 账户已变化，请核对原阻断规则")
            managed = self.requires_cloud_access(entry)
            if not cloudflare and not managed:
                if entry:
                    entries = self.status()
                    entries.pop(site_id, None)
                    self.service.store.set("site_pause", entries)
                return self.service.set_site_paused(site_id, paused)
            if self.thread and self.thread.is_alive():
                raise ValueError("云端暂停操作正在处理，请等待完成")
            zone = self.service.site_zone(site)
            if entry and (entry["hostname"] != site["hostname"] or entry["zone_id"] != zone["zone_id"]):
                if managed:
                    raise ValueError("域名配置已变化，请先核对原 Cloudflare 阻断规则")
                entry = None
            # Persist uncertain outcomes before making any request; resume remains locally paused.
            self.service.set_site_paused(site_id, True, cloud_internal=True)
            self._save(site_id, hostname=site["hostname"], zone_id=zone["zone_id"],
                       account_id=self.service.settings()["account_id"],
                       ref=(entry or {}).get("ref") or "lanbridge_pause_" + secrets.token_hex(12),
                       desired=paused and cloudflare, paused=paused, phase="queued",
                       automatic=automatic_reason is not None, automatic_reason=automatic_reason,
                       message="本机已暂停，正在设置云端阻断" if paused and cloudflare else "正在解除云端阻断，保持本机暂停" if paused else "正在解除云端阻断，完成后恢复转发")
            self.thread = threading.Thread(target=self._run, args=(site_id,), daemon=True, name="LanBridge-site-pause")
            try:
                self.thread.start()
            except RuntimeError:
                self._save(site_id, phase="failed", message="后台操作未启动，请重试；本机仍暂停")
            return site | {"paused": True, "site_pause": self.status()}

    def auto_block(self, site_id, policy_version, reason):
        """Recheck pause state under the same lock as manual resume; attempt once per pause."""
        with self.service.lock:
            if self.stopping or (self.thread and self.thread.is_alive()):
                return False
            site = next((s for s in self.service.sites() if s['id'] == site_id), None)
            if (not site or not site['enabled'] or not site.get('paused')
                    or site['policy_version'] != policy_version
                    or site['hostname'] not in self.service.store.get('published_hosts', [])):
                return True
            entry = self.status().get(site_id, {})
            if self.requires_cloud_access(entry):
                return True
            attempts = self.service.store.get('paused_auto_attempts', {})
            if attempts.get(site_id):
                return True
            current_ids = {s['id'] for s in self.service.sites()}
            attempts = {k: v for k, v in attempts.items() if k in current_ids}
            attempts[site_id] = True
            self.service.store.set('paused_auto_attempts', attempts)
            self.service.store.audit('site_auto_cloud_pause_triggered', {'id': site_id, 'hostname': site['hostname'], **reason})
            try:
                self.submit(site_id, True, True, automatic_reason=reason)
            except (ValueError, RuntimeError) as exc:
                cfg = self.service.settings()
                self._save(site_id, hostname=site['hostname'], zone_id=site.get('zone_id') or cfg['zone_id'],
                           account_id=cfg['account_id'], ref=entry.get('ref') or 'lanbridge_pause_' + secrets.token_hex(12),
                           desired=True, paused=True, phase='failed', automatic=True, automatic_reason=reason,
                           message=str(exc)[:400] + '；自动云端阻断未完成，本机仍保持暂停')
                self.service.store.audit('site_auto_cloud_pause_failed', {'id': site_id, 'hostname': site['hostname']})
            return True

    def _run(self, site_id):
        entry = self.status()[site_id]
        completed = False
        try:
            self._save(site_id, phase="running")
            def check_context():
                if self.stopping:
                    raise RuntimeError("平台正在停止，云端操作未完成，请重试")
                if self.service.settings()["account_id"] != entry["account_id"]:
                    raise ValueError("Cloudflare 账户已变化，请核对原阻断规则后重试")
            def call(method, path, body=None, **kwargs):
                check_context()
                return self.service.cf.request(method, path, body, force_write=True, **kwargs)
            base = "/zones/" + entry["zone_id"] + "/rulesets"
            ruleset = call("GET", base + "/phases/" + self.PHASE + "/entrypoint", allow_missing=True)
            expression = 'http.host eq "' + entry["hostname"] + '"'
            rule = {"ref": entry["ref"], "description": "LanBridge website pause", "expression": expression, "action": "block", "enabled": True}
            matches = [r for r in (ruleset or {}).get("rules", []) if r.get("ref") == entry["ref"]]
            if any(r.get("expression") != expression or r.get("action") != "block" for r in matches):
                raise ValueError("平台阻断规则被外部修改，请在 Cloudflare 核对后重试")
            if entry["desired"]:
                if ruleset is None:
                    call("POST", base, {"name": "Custom rules", "kind": "zone", "phase": self.PHASE, "rules": [rule]})
                elif not matches:
                    call("POST", base + "/" + ruleset["id"] + "/rules", rule | {"position": {"index": 1}})
                else:
                    for match in matches:
                        call("PATCH", base + "/" + ruleset["id"] + "/rules/" + match["id"], rule | {"position": {"index": 1}})
            elif ruleset:
                for match in matches:
                    call("DELETE", base + "/" + ruleset["id"] + "/rules/" + match["id"])
            confirmed = call("GET", base + "/phases/" + self.PHASE + "/entrypoint", allow_missing=True)
            remaining = [r for r in (confirmed or {}).get("rules", []) if r.get("ref") == entry["ref"]]
            valid = bool(remaining) and (confirmed or {}).get("rules", [{}])[0].get("ref") == entry["ref"] and all(r.get("enabled", True) and r.get("action") == "block" and r.get("expression") == expression for r in remaining)
            if (entry["desired"] and not valid) or (not entry["desired"] and remaining):
                raise RuntimeError("Cloudflare 操作结果未通过核对，请重试")
            with self.service.lock, self.lifecycle_lock:
                # A completed HTTP request may belong to an account or runtime we left meanwhile.
                check_context()
                entries = self.status()
                entries[site_id] = entries[site_id] | {"phase": "succeeded", "updated_at": time.time(),
                    "message": ("已自动在 Cloudflare 阻断公网访问" if entry.get("automatic") else "Cloudflare 已阻断公网访问") if entry["desired"] else "云端阻断已解除，仅本机暂停" if entry.get("paused") else "云端阻断已解除，转发已恢复"}
                try:
                    if not entry["desired"]:
                        # Resume and its completion record either both persist or both roll back.
                        self.service.set_site_paused(site_id, entry.get("paused", False),
                                                     cloud_internal=True, cloud_status=entries)
                    else:
                        self.service.store.set("site_pause", entries)
                finally:
                    # Capture this task's committed result before releasing the lock.
                    completed = self.status().get(site_id, {}).get("phase") == "succeeded"
                self.service.store.audit("site_cloud_pause" if entry["desired"] else "site_cloud_resume", {"id": site_id, "hostname": entry["hostname"]})
        except Exception as exc:
            if completed:
                # The operation already committed; a later audit error cannot undo it.
                logging.getLogger(__name__).warning("site_cloud_pause_audit_failed %s", type(exc).__name__)
                return
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "云端网络或响应异常"
            if len(message) > 400 or message.startswith("{"):
                message = "云端操作失败，请检查权限和网络"
            self._save(site_id, phase="failed", message=message + "；本机仍暂停，云端结果待核对")
            self.service.store.audit("site_cloud_pause_failed", {"id": site_id, "hostname": entry["hostname"]})

    def stop(self):
        # Signal promptly even if publication is holding the shared service lock.
        with self.lifecycle_lock:
            self.stopping = True
            thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=90)
