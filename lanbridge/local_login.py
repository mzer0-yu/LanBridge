"""Short-lived, browser-bound approval of local administrator sessions."""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
import webbrowser

COOKIE = "lb_local_login"
LIFETIME = 300


def open_browser(url):
    """Prefer Chrome, where the user may already have a password or session."""
    try:
        if os.name == "nt":
            for variable, suffix in (("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
                                     ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
                                     ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe")):
                base = os.environ.get(variable)
                executable = Path(base) / suffix if base else None
                if executable and executable.is_file():
                    subprocess.Popen([str(executable), url], creationflags=subprocess.CREATE_NO_WINDOW)
                    return True
        return bool(webbrowser.open(url))
    except OSError:
        return False


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


class LocalLogin:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.requests = {}

    def start(self, previous=""):
        with self.lock:
            now = time.time()
            for key, value in list(self.requests.items()):
                if value["expires_at"] <= now or (previous and hmac.compare_digest(value["proof"], fingerprint(previous))):
                    del self.requests[key]
            if len(self.requests) >= 20:
                raise ValueError("待确认的登录请求过多，请稍后重试")
            request_id, proof = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.requests[request_id] = {"proof": fingerprint(proof), "code": f"{secrets.randbelow(1000000):06d}",
                                         "expires_at": now + LIFETIME, "phase": "pending",
                                         "admin": fingerprint(self.store.get("admin")["password_hash"])}
            return request_id, proof, self.details(request_id)

    def get(self, request_id):
        job = self.requests.get(request_id)
        if not job or job["expires_at"] <= time.time():
            self.requests.pop(request_id, None)
            raise ValueError("登录请求已失效，请重新发起本机授权")
        admin = self.store.get("admin", {})
        if not admin or not hmac.compare_digest(job["admin"], fingerprint(admin["password_hash"])):
            self.requests.pop(request_id, None)
            raise ValueError("管理员凭据已变化，请重新发起本机授权")
        return job

    def details(self, request_id):
        with self.lock:
            job = self.get(request_id)
            return {key: job[key] for key in ("phase", "code", "expires_at")}

    def decide(self, request_id, code, allow):
        with self.lock:
            job = self.get(request_id)
            if job["phase"] != "pending" or not hmac.compare_digest(job["code"], str(code)):
                raise ValueError("登录请求已处理或确认码不匹配")
            job["phase"] = "approved" if allow else "denied"
            self.store.audit("local_login_approved" if allow else "local_login_denied", {})

    def poll(self, request_id, proof, cancel=False):
        with self.lock:
            job = self.get(request_id)
            if not proof or not hmac.compare_digest(job["proof"], fingerprint(proof)):
                raise ValueError("此登录请求不属于当前浏览器")
            if cancel:
                del self.requests[request_id]
                return {"phase": "cancelled"}, None
            phase = job["phase"]
            if phase == "approved":
                session = self.store.login()
                del self.requests[request_id]
                self.store.audit("admin_local_login", {})
                return {"phase": "done", "csrf": session[1]}, session[0]
            if phase == "denied":
                del self.requests[request_id]
            return {"phase": phase}, None
