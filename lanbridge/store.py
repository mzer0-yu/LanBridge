from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time

from cryptography.fernet import Fernet


def password_hash(value: str) -> str:
    if not 12 <= len(value) <= 256:
        raise ValueError("密码需要 12–256 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1)
    return base64.b64encode(salt + digest).decode()


def password_check(value: str, encoded: str) -> bool:
    try:
        raw = base64.b64decode(encoded)
        candidate = hashlib.scrypt(value[:257].encode(), salt=raw[:16], n=16384, r=8, p=1)
        return hmac.compare_digest(candidate, raw[16:])
    except (ValueError, TypeError):
        return False


def dpapi(data: bytes, decrypt: bool = False) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "LanBridge", None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise RuntimeError("Windows DPAPI 密钥保护失败")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def protect_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        principal = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                                 f"{principal}:(OI)(CI)F", "SYSTEM:(OI)(CI)F"], capture_output=True)
        if result.returncode:
            raise RuntimeError("无法收紧数据目录 ACL，拒绝保存凭据")
    else:
        path.chmod(0o700)


class Store:
    def __init__(self, root: Path):
        self.root = root
        protect_directory(root)
        self.lock = threading.RLock()
        keyfile = root / "vault.key"
        if not keyfile.exists():
            key = Fernet.generate_key()
            keyfile.write_bytes(dpapi(key) if os.name == "nt" else key)
            if os.name != "nt":
                keyfile.chmod(0o600)
        key = keyfile.read_bytes()
        self.cipher = Fernet(dpapi(key, True) if os.name == "nt" else key)
        self.db = sqlite3.connect(root / "state.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS secrets(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL, action TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, csrf TEXT, expires REAL);
        """)
        self.db.commit()

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))

    def secret(self, key):
        with self.lock:
            row = self.db.execute("SELECT value FROM secrets WHERE key=?", (key,)).fetchone()
            return self.cipher.decrypt(row[0].encode()).decode() if row else ""

    def set_secret(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (key, self.cipher.encrypt(value.encode()).decode()))

    def audit(self, action, detail):
        # Callers pass resource identifiers and counts only, never raw API bodies or credentials.
        with self.lock, self.db:
            self.db.execute("INSERT INTO audit(at,action,detail) VALUES (?,?,?)", (time.time(), action, json.dumps(detail)))

    def audit_list(self):
        with self.lock:
            return [dict(r) | {"detail": json.loads(r["detail"])} for r in self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]

    def login(self):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            self.db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), csrf, time.time() + 28800))
        return token, csrf

    def session(self, token):
        with self.lock:
            row = self.db.execute("SELECT * FROM sessions WHERE token=? AND expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))
