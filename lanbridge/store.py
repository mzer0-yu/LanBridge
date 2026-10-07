from __future__ import annotations

import base64
import ctypes
import hashlib
import math
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time
from tempfile import NamedTemporaryFile, SpooledTemporaryFile

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
            protected_key = dpapi(key) if os.name == "nt" else key
            temporary = None
            try:
                with NamedTemporaryFile(dir=root, prefix=".vault-", suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(protected_key)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    # Publish a complete key without replacing another initializer's key.
                    if os.name == "nt":
                        os.rename(temporary, keyfile)
                    else:
                        os.link(temporary, keyfile)
                except FileExistsError:
                    pass
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        key = keyfile.read_bytes()
        self.cipher = Fernet(dpapi(key, True) if os.name == "nt" else key)
        self.db = sqlite3.connect(root / "state.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS secrets(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, csrf TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS temporary_tokens(id TEXT PRIMARY KEY, digest TEXT UNIQUE, name TEXT, created REAL, expires REAL, revoked INTEGER DEFAULT 0);
        """)
        try:
            with self.db:
                # Lock before inspecting columns, including across separate processes.
                self.db.execute("BEGIN IMMEDIATE")
                if "access_token_id" not in [r[1] for r in self.db.execute("PRAGMA table_info(sessions)")]:
                    self.db.execute("ALTER TABLE sessions ADD COLUMN access_token_id TEXT")
                if "permissions" not in [r[1] for r in self.db.execute("PRAGMA table_info(temporary_tokens)")]:
                    self.db.execute("ALTER TABLE temporary_tokens ADD COLUMN permissions TEXT NOT NULL DEFAULT '[\"sites\"]'")
                for column in ("last_login", "last_access"):
                    if column not in [r[1] for r in self.db.execute("PRAGMA table_info(temporary_tokens)")]:
                        self.db.execute(f"ALTER TABLE temporary_tokens ADD COLUMN {column} REAL")
                if "encrypted_value" not in [r[1] for r in self.db.execute("PRAGMA table_info(temporary_tokens)")]:
                    self.db.execute("ALTER TABLE temporary_tokens ADD COLUMN encrypted_value TEXT")
        except BaseException:
            self.db.close()
            raise
        # Recover a possible SQLite journal before renaming the legacy log file.
        legacy_log = root / "audit.sqlite"
        log_file = root / "log.sqlite"
        if legacy_log.exists() and not log_file.exists():
            legacy_db = sqlite3.connect(legacy_log)
            try:
                legacy_db.execute("SELECT COUNT(*) FROM audit").fetchone()
            finally:
                legacy_db.close()
            legacy_log.replace(log_file)
        # Keep operation history separate from settings and encrypted credentials.
        self.db.execute("ATTACH DATABASE ? AS logs", (str(root / "log.sqlite"),))
        self.db.execute("PRAGMA logs.auto_vacuum=FULL")
        self.db.execute("PRAGMA logs.journal_mode=DELETE")
        self.db.execute("CREATE TABLE IF NOT EXISTS logs.audit(id INTEGER PRIMARY KEY, at REAL, action TEXT, detail TEXT)")
        if self.db.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='audit'").fetchone():
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO logs.audit SELECT * FROM main.audit")
                self.db.execute("DROP TABLE main.audit")
        publication = self.db.execute("SELECT action FROM logs.audit WHERE action IN ('publish_verified','publish_incomplete') ORDER BY id DESC LIMIT 1").fetchone()
        if publication:
            self.set("last_publication_action", publication[0])
        self.trim_audit()

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))

    def set_many(self, values, *, secret_values=None):
        # Prepare every value before writing; commit related policies and secrets together.
        encoded = [(key, json.dumps(value)) for key, value in values.items()]
        encrypted = [(key, self.cipher.encrypt(value.encode()).decode())
                     for key, value in (secret_values or {}).items()]
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO kv VALUES (?,?)", encoded)
            self.db.executemany("INSERT OR REPLACE INTO secrets VALUES (?,?)", encrypted)

    def secret(self, key):
        with self.lock:
            row = self.db.execute("SELECT value FROM secrets WHERE key=?", (key,)).fetchone()
            return self.cipher.decrypt(row[0].encode()).decode() if row else ""

    def set_secret(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (key, self.cipher.encrypt(value.encode()).decode()))

    def audit_limit_mb(self):
        return self.get("audit_limit_mb", 10)

    def audit_stats(self):
        with self.lock:
            return {"limit_mb": self.audit_limit_mb(), "size_bytes": (self.root / "log.sqlite").stat().st_size,
                    "count": self.db.execute("SELECT COUNT(*) FROM logs.audit").fetchone()[0],
                    "oldest_id": self.db.execute("SELECT MIN(id) FROM logs.audit").fetchone()[0]}

    def set_audit_limit(self, mb):
        if isinstance(mb, bool) or not isinstance(mb, (int, float)) or not math.isfinite(mb) or not 1 <= mb <= 1024:
            raise ValueError("日志大小需要为 1–1024 MB")
        with self.lock:
            self.set("audit_limit_mb", mb)
            self.audit("audit_limit_changed", {"limit_mb": mb})
        return self.audit_stats()

    def trim_audit(self):
        with self.lock:
            limit = int(self.audit_limit_mb() * 1024 * 1024)
            # Appends below the physical cap need no history scan or DELETE.
            # Limit changes and startup use this same check, so lowering the cap
            # still trims immediately when the existing file exceeds it.
            if (self.root / "log.sqlite").stat().st_size <= limit:
                return
            # Include row/page overhead; autovacuum returns freed pages on commit.
            with self.db:
                self.db.execute("""DELETE FROM logs.audit WHERE id IN (
                    SELECT id FROM (SELECT id, SUM(length(CAST(detail AS BLOB))+length(CAST(action AS BLOB))+64)
                    OVER (ORDER BY id DESC) AS retained FROM logs.audit) WHERE retained > ?
                )""", (int(limit * .75),))
            while (self.root / "log.sqlite").stat().st_size > limit:
                count = self.db.execute("SELECT COUNT(*) FROM logs.audit").fetchone()[0]
                if not count:
                    break
                with self.db:
                    self.db.execute("DELETE FROM logs.audit WHERE id IN (SELECT id FROM logs.audit ORDER BY id LIMIT ?)", (max(1, count // 10),))

    def audit(self, action, detail):
        # Never log credentials. Oversized details cannot consume the entire history.
        with self.lock:
            encoded = json.dumps(detail, ensure_ascii=False)
            if len(encoded.encode()) > self.audit_limit_mb() * 1024 * 1024 / 2:
                encoded = json.dumps({"truncated": True, "reason": "日志详情超过单条大小限制"}, ensure_ascii=False)
            with self.db:
                self.db.execute("INSERT INTO logs.audit(at,action,detail) VALUES (?,?,?)", (time.time(), action, encoded))
            if action in ("publish_verified", "publish_incomplete"):
                self.set("last_publication_action", action)
            self.trim_audit()

    def audit_list(self, before=None):
        with self.lock:
            query = "SELECT * FROM logs.audit" + (" WHERE id < ?" if before is not None else "") + " ORDER BY id DESC LIMIT 100"
            return [dict(r) | {"detail": json.loads(r["detail"])} for r in self.db.execute(query, (before,) if before is not None else ())]

    def audit_export(self):
        """Build an oldest-first snapshot with bounded buffering; caller closes it."""
        snapshot = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b", dir=self.root)
        try:
            with self.lock:
                for row in self.db.execute("SELECT * FROM logs.audit ORDER BY id"):
                    snapshot.write((json.dumps(dict(row) | {"detail": json.loads(row["detail"])}, ensure_ascii=False) + "\n").encode("utf-8"))
            snapshot.seek(0)
            return snapshot
        except BaseException:
            snapshot.close()
            raise

    def login(self, expected_password_hash=None):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.lock, self.db:
            if expected_password_hash is not None:
                current = self.get("admin", {})
                if not current or not hmac.compare_digest(current["password_hash"], expected_password_hash):
                    raise ValueError("管理员凭据已变化，请重新登录")
            self.db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            self.db.execute("INSERT INTO sessions(token,csrf,expires) VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), csrf, time.time() + 28800))
        return token, csrf

    def session(self, token):
        with self.lock:
            row = self.db.execute("SELECT * FROM sessions WHERE token=? AND expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if not row:
                return None
            session = dict(row)
            if session.get("access_token_id"):
                grant = self.db.execute("SELECT id,expires,permissions FROM temporary_tokens WHERE id=? AND revoked=0 AND expires>?", (session["access_token_id"], time.time())).fetchone()
                if not grant:
                    return None
                session.update(scope="sites", permissions=json.loads(grant["permissions"]), expires=min(session["expires"], grant["expires"]))
            else:
                session["scope"] = "admin"
            return session

    def issue_temporary_token(self, name="", hours=24, permissions=None):
        permissions = ["sites"] if permissions is None else permissions
        if not isinstance(permissions, list) or not permissions or any(p not in ("sites", "account") for p in permissions):
            raise ValueError("请至少选择一项有效权限")
        permissions = list(dict.fromkeys(permissions))
        if not isinstance(name, str) or len(name.strip()) > 64:
            raise ValueError("用途名称最多 64 字")
        name = name.strip()
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or (isinstance(hours, float) and not math.isfinite(hours)) or hours <= 0:
            raise ValueError("有效期须填写大于 0 的小时数")
        now = time.time()
        if hours >= (253402300799 - now) / 3600:
            raise ValueError("有效期超出可表示的日期范围")
        token, token_id = "lb_tmp_" + secrets.token_urlsafe(32), secrets.token_hex(12)
        with self.lock, self.db:
            if self.db.execute("SELECT count(*) FROM temporary_tokens WHERE revoked=0 AND expires>?", (now,)).fetchone()[0] >= 20:
                raise ValueError("最多保留 20 个有效临时管理 Token，请先撤销不再使用的 Token")
            if not name:
                numbers = {int(r[0][4:]) for r in self.db.execute("SELECT name FROM temporary_tokens WHERE revoked=0 AND expires>? AND (name LIKE '临时管理%' OR name LIKE '临时访问%')", (now,)) if r[0][4:].isascii() and r[0][4:].isdecimal()}
                number = 1
                while number in numbers:
                    number += 1
                name = "临时管理" + str(number)
            self.db.execute("INSERT INTO temporary_tokens(id,digest,name,created,expires,permissions,encrypted_value) VALUES (?,?,?,?,?,?,?)", (token_id, hashlib.sha256(token.encode()).hexdigest(), name.strip(), now, now + hours * 3600, json.dumps(permissions), self.cipher.encrypt(token.encode()).decode()))
        self.audit("temporary_token_created", {"id": token_id, "permissions": permissions, "expires": now + hours * 3600})
        return {"id": token_id, "token": token, "name": name.strip(), "scope": "sites", "permissions": permissions, "expires": now + hours * 3600}

    def temporary_token(self, token):
        if not isinstance(token, str) or not token.startswith("lb_tmp_") or len(token) > 256:
            return None
        with self.lock:
            row = self.db.execute("SELECT id,name,expires,permissions FROM temporary_tokens WHERE digest=? AND revoked=0 AND expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return dict(row) | {"scope": "sites", "permissions": json.loads(row["permissions"])} if row else None

    def temporary_login(self, token):
        with self.lock, self.db:
            grant = self.temporary_token(token)
            if not grant:
                raise ValueError("临时管理 Token 无效、已到期或已撤销")
            session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            self.db.execute("INSERT INTO sessions(token,csrf,expires,access_token_id) VALUES (?,?,?,?)", (hashlib.sha256(session.encode()).hexdigest(), csrf, grant["expires"], grant["id"]))
            now = time.time()
            self.db.execute("UPDATE temporary_tokens SET last_login=?,last_access=? WHERE id=?", (now, now, grant["id"]))
            return session, csrf, grant

    def touch_temporary_token(self, token_id):
        now = time.time()
        with self.lock, self.db:
            self.db.execute("UPDATE temporary_tokens SET last_access=? WHERE id=? AND revoked=0 AND expires>? AND (last_access IS NULL OR last_access<=?)", (now, token_id, now, now - 30))

    def temporary_tokens(self):
        with self.lock:
            return [dict(r) | {"scope": "sites", "permissions": json.loads(r["permissions"]), "revealable": bool(r["revealable"])} for r in self.db.execute("SELECT id,name,created,expires,revoked,permissions,last_login,last_access,(encrypted_value IS NOT NULL) AS revealable FROM temporary_tokens ORDER BY (revoked=0 AND expires>?) DESC, created DESC LIMIT 100", (time.time(),))]

    def reveal_temporary_token(self, token_id):
        with self.lock:
            row = self.db.execute("SELECT encrypted_value FROM temporary_tokens WHERE id=? AND revoked=0 AND expires>?", (token_id, time.time())).fetchone()
            if not row:
                raise ValueError("Token 不存在、已到期或已撤销")
            if not row["encrypted_value"]:
                raise ValueError("旧版本未保存此 Token 原值，无法恢复；请撤销后重新生成")
            value = self.cipher.decrypt(row["encrypted_value"].encode()).decode()
        self.audit("temporary_token_viewed", {"id": token_id})
        return value

    def revoke_temporary_token(self, token_id):
        with self.lock, self.db:
            self.db.execute("UPDATE temporary_tokens SET revoked=1,encrypted_value=NULL WHERE id=?", (token_id,))
            self.db.execute("DELETE FROM sessions WHERE access_token_id=?", (token_id,))
        self.audit("temporary_token_revoked", {"id": token_id})

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))
