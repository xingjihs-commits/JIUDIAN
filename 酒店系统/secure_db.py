"""secure_db.py - SQLCipher-aware database connection helper.

C0-encryption-1 goal:
- Keep normal development databases working with stdlib sqlite3.
- When SQLCipher is required, never silently fall back to plaintext SQLite.
- Support encrypted databases once a SQLCipher Python driver is available
  (sqlcipher3 or pysqlcipher3.dbapi2).

Enable strict SQLCipher mode with:
    SOLID_REQUIRE_SQLCIPHER=1
"""
from __future__ import annotations

import hashlib
import importlib
import os
import sqlite3 as _stdlib_sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any


SQLCIPHER_DRIVER_CANDIDATES = (
    "sqlcipher3",
    "pysqlcipher3.dbapi2",
)

SQLITE_HEADER = b"SQLite format 3\x00"
_KEY_SALT = "SOLID-HOTEL-C0-ENCRYPTION-1"


class SecureDatabaseError(RuntimeError):
    """Raised when encryption is required but SQLCipher is unavailable or invalid."""


def require_sqlcipher() -> bool:
    # 打包构建优先尝试 sqlcipher3，但允许降级到普通 sqlite3
    # 避免因 sqlcipher3 本机模块打包失败导致程序完全无法启动
    raw = os.environ.get("SOLID_REQUIRE_SQLCIPHER", "")
    if raw.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    # 未强制要求时，允许 plain sqlite3（开发/降级模式）
    return False


def import_sqlcipher_module():
    errors: list[str] = []
    for name in SQLCIPHER_DRIVER_CANDIDATES:
        try:
            return importlib.import_module(name)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise SecureDatabaseError("SQLCipher 驱动不可用：" + " | ".join(errors))


def is_plain_sqlite_file(path: str | Path) -> bool:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return True
    with p.open("rb") as fh:
        return fh.read(len(SQLITE_HEADER)) == SQLITE_HEADER


def derive_database_key() -> str:
    """Derive a local SQLCipher key from machine identity.

    This is intentionally deterministic so the same machine can reopen the DB.
    A future cloud-backed deployment can replace this with a vendor-issued key.
    """
    mac = ("%012X" % uuid.getnode()).upper()
    raw = f"{_KEY_SALT}|{mac}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _quote_pragma_value(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _open_sqlcipher(path: str, **kwargs: Any):
    mod = import_sqlcipher_module()
    conn = mod.connect(path, **kwargs)
    key = os.environ.get("SOLID_DB_KEY") or derive_database_key()
    conn.execute(f"PRAGMA key = {_quote_pragma_value(key)}")
    # Conservative defaults compatible with SQLCipher 4.x.
    conn.execute("PRAGMA cipher_page_size = 4096")
    conn.execute("PRAGMA kdf_iter = 256000")
    conn.execute("PRAGMA foreign_keys = ON")
    # Force key validation early. Wrong key / non-SQLCipher driver fails here.
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return conn


def connect(path: str, *, check_same_thread: bool = False, timeout: int = 10):
    """Return a DB-API connection.

    Plain SQLite is allowed only when:
    - SOLID_REQUIRE_SQLCIPHER is not enabled, and
    - the target file is either absent/empty or has a normal SQLite header.

    Encrypted-looking files always require SQLCipher.
    """
    encrypted_required = require_sqlcipher()
    plain_file = is_plain_sqlite_file(path)
    kwargs = {"check_same_thread": check_same_thread, "timeout": timeout}

    if encrypted_required or not plain_file:
        try:
            return _open_sqlcipher(path, **kwargs)
        except SecureDatabaseError:
            raise
        except Exception as exc:
            raise SecureDatabaseError(f"SQLCipher 数据库打开失败：{exc}") from exc

    conn = _stdlib_sqlite3.connect(path, **kwargs)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def sqlcipher_available() -> bool:
    try:
        import_sqlcipher_module()
        return True
    except SecureDatabaseError:
        return False


def status(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    return {
        "path": str(p),
        "exists": p.exists(),
        "plain_sqlite_header": is_plain_sqlite_file(p),
        "require_sqlcipher": require_sqlcipher(),
        "sqlcipher_available": sqlcipher_available(),
    }
