"""Source revision detection and coordinated, whole-process reloads.

No importlib.reload: each backend generation starts in a fresh interpreter.
Runtime data, dependencies and tests are deliberately outside the watch set.
"""
from __future__ import annotations
import ast
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


def revision(root, backend=True):
    root = Path(root)
    files = [root / "run.py", *sorted((root / "lanbridge").glob("*.py"))] if backend else sorted((root / "ui").glob("*"))
    result = {}
    for path in files:
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def validate_sources(root, expected):
    if "run.py" not in expected:
        raise ValueError("启动入口缺失")
    if revision(root) != expected:
        return False
    for name in expected:
        path = Path(root) / name
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=name)
    return revision(root) == expected


def check_imports(root):
    """Check startup imports in a fresh interpreter without opening data/listeners."""
    try:
        result = subprocess.run([sys.executable, "-c", "import run; import lanbridge.admin; import lanbridge.gateway; import lanbridge.domain_onboarding"], cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class UpdateController:
    def __init__(self, root, service, reload_callback, *, enabled=True, settle_seconds=3, notice_seconds=5, clock=time.monotonic, preflight=None):
        self.root, self.service, self.reload_callback = Path(root), service, reload_callback
        self.preflight, self.rejected = preflight, None
        self.enabled, self.settle_seconds, self.notice_seconds, self.clock = enabled, settle_seconds, notice_seconds, clock
        self.loaded = revision(root)
        self.frontend = revision(root, False)
        self.observed_frontend = self.frontend
        self.candidate = None
        self.changed_at = 0
        self._status = {"phase": "current", "automatic": enabled, "message": ""}
        self.lock, self.stopped = threading.RLock(), threading.Event()
        self.thread = None

    def status(self):
        with self.lock:
            return dict(self._status, frontend_revision=hashlib.sha256(repr(self.observed_frontend).encode()).hexdigest())

    def tick(self):
        with self.lock:
            if self._status["phase"] == "reloading":
                return
            try:
                current, frontend = revision(self.root), revision(self.root, False)
            except OSError:
                self._status.update(phase="pending", message="更新文件暂不可读取，等待文件写入完成")
                return
            self.observed_frontend = frontend
            if current == self.loaded:
                self.candidate = None
                self._status.update(phase="frontend" if frontend != self.frontend else "current", message="界面已更新，请刷新页面" if frontend != self.frontend else "")
                return
            now = self.clock()
            if current != self.candidate:
                self.candidate, self.changed_at = current, now
            self._status.update(phase="pending", message="更新待生效，请重启实例" if not self.enabled else "更新待生效，校验后将自动重载")
            if not self.enabled or now - self.changed_at < self.settle_seconds + self.notice_seconds:
                return
            if current == self.rejected:
                self._status.update(phase="error", message="更新启动校验未通过，当前实例继续运行；请修正更新文件")
                return
            try:
                if not validate_sources(self.root, current):
                    return
            except (SyntaxError, UnicodeError, OSError, ValueError):
                self._status.update(phase="error", message="更新代码校验未通过，当前实例继续运行；请修正更新文件")
                return
            if self.preflight and not self.preflight():
                self.rejected = current
                self._status.update(phase="error", message="更新启动校验未通过，当前实例继续运行；请修正更新文件")
                return
            # Do not interrupt management writes, browser authorization or DNS migration.
            manager = getattr(self.service, "domain_onboarding", None)
            locks = [self.service.lock] + ([manager.lock] if manager else [])
            acquired = []
            try:
                for lock in locks:
                    if not lock.acquire(blocking=False):
                        self._status.update(message="更新待生效，等待当前操作完成后自动重载")
                        return
                    acquired.append(lock)
                auth = getattr(self.service, "browser_auth", None)
                if auth and auth.status().get("phase") in {"preparing", "authorizing", "choosing_zone", "creating", "cancelling"}:
                    self._status.update(message="更新待生效，等待浏览器授权完成后自动重载")
                    return
                if revision(self.root) != current:
                    return
                self.reload_callback()
                self._status.update(phase="reloading", message="正在重载，管理台与转发会短暂中断并自动恢复")
            except (ValueError, RuntimeError, OSError):
                self._status.update(phase="error", message="自动重载未能开始，当前实例继续运行；请通过启动器重启实例")
                self.changed_at = now
            finally:
                for lock in reversed(acquired):
                    lock.release()

    def start(self):
        def watch():
            while not self.stopped.wait(1):
                try:
                    self.tick()
                except Exception:
                    with self.lock:
                        self._status.update(phase="error", message="更新检测异常，当前实例继续运行；请通过启动器重启实例")
        self.thread = threading.Thread(target=watch, name="lanbridge-updates", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=2)
