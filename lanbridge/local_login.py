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


BROWSER_NAMES = {"default": "系统默认浏览器", "chrome": "Chrome", "edge": "Edge"}


def browser_executable(browser):
    suffixes = {"chrome": "Google/Chrome/Application/chrome.exe",
                "edge": "Microsoft/Edge/Application/msedge.exe"}
    if os.name != "nt" or browser not in suffixes:
        return None
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        executable = Path(base) / suffixes[browser] if base else None
        if executable and executable.is_file():
            return executable
    return None


def available_browsers():
    return [{"id": key, "name": name} for key, name in BROWSER_NAMES.items()
            if key == "default" or browser_executable(key) is not None]


def open_browser(url, browser="default"):
    """Launch only a fixed, installed browser; never accept an executable path."""
    if browser not in BROWSER_NAMES:
        raise ValueError("浏览器选项无效")
    try:
        if browser == "default":
            return bool(webbrowser.open(url))
        executable = browser_executable(browser)
        if not executable:
            return False
        subprocess.Popen([str(executable), url], creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except (OSError, webbrowser.Error):
        return False


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


class LocalLogin:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.requests = {}

    def start(self, previous="", browser="default"):
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
                                         "admin": fingerprint(self.store.get("admin")["password_hash"]), "browser": browser}
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
            candidate = str(code)
            if (job["phase"] != "pending" or len(candidate) != 6
                    or not candidate.isascii() or not candidate.isdecimal() or not hmac.compare_digest(job["code"], candidate)):
                raise ValueError("登录请求已处理或确认码不匹配")
            job["phase"] = "approved" if allow else "denied"
            self.store.audit("local_login_approved" if allow else "local_login_denied", {})

    def browser_for_open(self, request_id, proof):
        with self.lock:
            job = self.get(request_id)
            if not proof or not hmac.compare_digest(job["proof"], fingerprint(proof)):
                raise ValueError("此登录请求不属于当前浏览器")
            if job["phase"] != "pending":
                raise ValueError("登录请求已处理，请重新发起")
            return job["browser"]

    def poll(self, request_id, proof, cancel=False, existing_token=""):
        # Keep the password fingerprint check and session issuance atomic.
        with self.lock, self.store.lock:
            job = self.get(request_id)
            if not proof or not hmac.compare_digest(job["proof"], fingerprint(proof)):
                raise ValueError("此登录请求不属于当前浏览器")
            if cancel:
                del self.requests[request_id]
                return {"phase": "cancelled"}, None
            phase = job["phase"]
            if phase == "approved":
                # Tabs in the same browser share the admin cookie. Rotating it here
                # would invalidate the confirmation tab's in-memory CSRF value.
                existing = self.store.session(existing_token) if existing_token else None
                session = (None, existing["csrf"]) if existing and existing.get("scope") == "admin" else self.store.login()
                del self.requests[request_id]
                self.store.audit("admin_local_login", {})
                return {"phase": "done", "csrf": session[1]}, session[0]
            if phase == "denied":
                del self.requests[request_id]
            return {"phase": phase}, None
