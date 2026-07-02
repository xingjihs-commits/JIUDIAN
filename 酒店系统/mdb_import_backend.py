"""
把 .mdb 转为 SQLite，走路径统一的 LegacyDbConn。
合作酒店现场无需安装 Microsoft Access 数据库引擎。

优先级：
  1. 命令行工具（传统方式，需系统路径或有独立程序）
  2. Python 纯读取库（无外部依赖，推荐兜底）
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from deploy_paths import bundled_path, get_deploy_root
from legacy_migration import LegacyDbConn

logger = logging.getLogger(__name__)

_MDB_BIN_NAMES = ("mdb-tables", "mdb-export", "mdb-schema")


def find_mdbtools_exe(name: str) -> Optional[Path]:
    base = name if name.endswith(".exe") else f"{name}.exe"
    for rel in (
        "_deploy_deps/mdbtools/win64",
        "tools/mdbtools/win64",
        "tools/mdbtools",
        "tools/mdbtools/bin",
    ):
        p = bundled_path(rel, base)
        if p.is_file():
            return p
    found = _which_on_path(base)
    return Path(found) if found else None


def _which_on_path(name: str) -> Optional[str]:
    paths = os.environ.get("PATH", "").split(os.pathsep)
    for d in paths:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def mdbtools_available() -> bool:
    tables = find_mdbtools_exe("mdb-tables")
    export = find_mdbtools_exe("mdb-export")
    if not tables or not export:
        return False
    code, _out, _err = _run_mdb([str(tables), "--version"], timeout=10)
    return code in (0, 1)


def _run_mdb(
    args: List[str], *, timeout: int = 120, env_extra: Optional[dict] = None
) -> Tuple[int, str, str]:
    exe = args[0]
    env = None
    if env_extra:
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in env_extra.items()})
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=env,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError:
        return 127, "", f"未找到: {exe}"
    except subprocess.TimeoutExpired:
        return 124, "", "执行超时"


def list_mdb_tables(mdb_path: str) -> Tuple[List[str], str]:
    exe = find_mdbtools_exe("mdb-tables")
    if not exe:
        return [], "部署包内未包含 mdbtools（mdb-tables）"
    code, out, err = _run_mdb([str(exe), "-1", mdb_path])
    if code != 0:
        return [], err or f"mdb-tables 退出码 {code}"
    tables = [t.strip() for t in out.splitlines() if t.strip() and not t.startswith("MSys")]
    return tables, f"{len(tables)} 张表"


def _sqlite_table_ident(name: str) -> str:
    """保留原表名，仅做引号转义。"""
    n = (name or "t").strip().replace('"', '""')
    if not n:
        n = "t"
    return n


def _unique_headers(headers: List[str]) -> List[str]:
    """MDB 导出可能出现重复/空字段名，SQLite 建表前必须去重。"""
    seen: dict[str, int] = {}
    out: List[str] = []
    for idx, h in enumerate(headers):
        base = str(h or "").strip() or f"col_{idx + 1}"
        if base in seen:
            seen[base] += 1
            base = f"{base}_{seen[base]}"
        else:
            seen[base] = 1
        out.append(base)
    return out


def _export_table_csv(mdb_path: str, table: str) -> Tuple[List[str], List[List[str]], str]:
    """用 mdb-export 把单表导成 CSV，再解析成 (列名, 数据)。

    注意：mdb-export 的 `-H` 参数是 *suppress header*（不输出表头），
    我们要的恰恰相反 —— 必须输出表头。这里强制 UTF-8 输出，避免
    CardLock.mdb 里中文字段（"姓名"、"身份证号"等）被本地 codepage 截断。
    """
    exe = find_mdbtools_exe("mdb-export")
    if not exe:
        return [], [], "缺少 mdb-export"
    args = [str(exe), "-D", "%Y-%m-%d %H:%M:%S"]
    # 强制输出 UTF-8（默认会随系统区域设置，Windows 上常给 GBK）
    args += ["-X", "double", mdb_path, table]
    env_extra = {"MDB_JET3_CHARSET": "CP936", "MDB_JET4_CHARSET": "UTF-8"}
    code, out, err = _run_mdb(args, timeout=180, env_extra=env_extra)
    if code != 0:
        return [], [], err or f"导出表 {table} 失败"
    if not out.strip():
        return [], [], ""
    sample = out[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(out), dialect)
    rows = list(reader)
    if not rows:
        return [], [], ""
    headers = _unique_headers([str(h).strip() for h in rows[0]])
    data = [[str(c) for c in row] for row in rows[1:] if any(str(x).strip() for x in row)]
    return headers, data, ""


# ──────────────────────────────────────────────────────────────────
# access_parser 后端（纯 Python，无外部二进制依赖）
# ──────────────────────────────────────────────────────────────────

def _access_parser_available() -> bool:
    """检查 access_parser 库是否已安装。"""
    try:
        import access_parser  # noqa
        return True
    except ImportError:
        return False


def _list_tables_via_access_parser(mdb_path: str) -> Tuple[List[str], str]:
    """用 access_parser 列 MDB 表名。"""
    try:
        from access_parser import AccessParser
        db = AccessParser(mdb_path)
        tables = [str(t) for t in db.catalog if not str(t).startswith("MSys")]
        return tables, f"{len(tables)} 张表"
    except Exception as e:
        return [], str(e)


def _export_table_via_access_parser(mdb_path: str, table: str) -> Tuple[List[str], List[List[str]], str]:
    """用 access_parser 导出单表数据为 (列名, 数据行)。"""
    try:
        from access_parser import AccessParser
        db = AccessParser(mdb_path)
        raw = db.parse_table(table)
        if not raw:
            return [], [], ""
        # raw 是 defaultdict(list) — {col_name: [row0_val, row1_val, ...]}
        col_names = list(raw.keys())
        if not col_names:
            return [], [], ""
        n_rows = max(len(v) for v in raw.values()) if raw else 0
        data = []
        for i in range(n_rows):
            row = []
            for c in col_names:
                vals = raw.get(c, [])
                row.append(str(vals[i]) if i < len(vals) else "")
            data.append(row)
        return col_names, data, ""
    except Exception as e:
        return [], [], str(e)


def _mdb_to_sqlite_via_parser(mdb_path: str, sqlite_path: str, *, progress_cb=None) -> Tuple[bool, str]:
    """通过 Python 纯读取库将 MDB 转为 SQLite。"""
    tables, msg = _list_tables_via_access_parser(mdb_path)
    if not tables:
        return False, msg or "未找到任何表"
    if os.path.isfile(sqlite_path):
        try:
            os.remove(sqlite_path)
        except OSError as e:
            return False, f"无法覆盖缓存库: {e}"
    conn = sqlite3.connect(sqlite_path)
    try:
        done = 0
        for tname in tables:
            headers, data, err = _export_table_via_access_parser(mdb_path, tname)
            if err and not headers:
                continue
            st = _sqlite_table_ident(tname)
            cols_sql = ", ".join(f'"{c.replace(chr(34), chr(34)*2)}" TEXT' for c in headers) if headers else '"_row" TEXT'
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{st}" ({cols_sql})')
            if headers and data:
                placeholders = ", ".join("?" for _ in headers)
                col_names = ", ".join(f'"{c.replace(chr(34), chr(34)*2)}"' for c in headers)
                conn.executemany(
                    f'INSERT INTO "{st}" ({col_names}) VALUES ({placeholders})',
                    [row[: len(headers)] + [""] * max(0, len(headers) - len(row)) for row in data],
                )
            done += 1
            if progress_cb:
                progress_cb(tname, done, len(tables))
        conn.commit()
    finally:
        conn.close()
    return True, f"已通过 Python 读取库转换 {done}/{len(tables)} 张表 → SQLite"


# ──────────────────────────────────────────────────────────────────
# 导出入口（自动选择后端）
# ──────────────────────────────────────────────────────────────────

def mdb_to_sqlite(mdb_path: str, sqlite_path: str, *, progress_cb=None) -> Tuple[bool, str]:
    """将 MDB 全库导出为 SQLite（只读转换，不修改原 MDB）。

    自动按优先级选择后端：mdbtools > access_parser。
    """
    # 优先尝试 mdbtools（经典方案）
    tables, msg = list_mdb_tables(mdb_path)
    if tables:
        return _mdb_to_sqlite_core(mdb_path, sqlite_path, tables, progress_cb=progress_cb,
                                    backend_name="mdbtools")
    # 兜底：Python 纯读取库
    if _access_parser_available():
        tables2, msg2 = _list_tables_via_access_parser(mdb_path)
        if tables2:
            return _mdb_to_sqlite_core(mdb_path, sqlite_path, tables2, progress_cb=progress_cb,
                                        backend_name="access_parser")
        return False, msg2 or "access_parser 也无法读取"
    return False, msg


def _mdb_to_sqlite_core(mdb_path: str, sqlite_path: str, tables: List[str],
                        *, progress_cb=None, backend_name: str) -> Tuple[bool, str]:
    """核心转换逻辑，根据提供的表名列表导出到 SQLite。"""
    if os.path.isfile(sqlite_path):
        try:
            os.remove(sqlite_path)
        except OSError as e:
            return False, f"无法覆盖缓存库: {e}"
    conn = sqlite3.connect(sqlite_path)
    try:
        done = 0
        for tname in tables:
            if backend_name == "access_parser":
                headers, data, err = _export_table_via_access_parser(mdb_path, tname)
            else:
                headers, data, err = _export_table_csv(mdb_path, tname)
            if err and not headers:
                continue
            st = _sqlite_table_ident(tname)
            cols_sql = ", ".join(f'"{c.replace(chr(34), chr(34)*2)}" TEXT' for c in headers) if headers else '"_row" TEXT'
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{st}" ({cols_sql})')
            if headers and data:
                placeholders = ", ".join("?" for _ in headers)
                col_names = ", ".join(f'"{c.replace(chr(34), chr(34)*2)}"' for c in headers)
                conn.executemany(
                    f'INSERT INTO "{st}" ({col_names}) VALUES ({placeholders})',
                    [row[: len(headers)] + [""] * max(0, len(headers) - len(row)) for row in data],
                )
            done += 1
            if progress_cb:
                progress_cb(tname, done, len(tables))
        conn.commit()
    finally:
        conn.close()
    return True, f"已通过 {backend_name} 转换 {done}/{len(tables)} 张表 → SQLite"


def sqlite_cache_path_for_mdb(mdb_path: str) -> Path:
    mdb = Path(mdb_path).resolve()
    return mdb.parent / f"{mdb.stem}.solidcache.sqlite"


def open_mdb_via_sqlite_cache(mdb_path: str, *, rebuild: bool = False) -> Tuple[Optional[LegacyDbConn], str]:
    """
    优先使用同目录缓存 SQLite；若无或 rebuild，则从 MDB 生成。
    自动选择可用后端：mdbtools > access_parser。
    """
    cache = sqlite_cache_path_for_mdb(mdb_path)
    if rebuild and cache.is_file():
        try:
            cache.unlink()
        except OSError:
            pass
    if not cache.is_file():
        ok, msg = mdb_to_sqlite(mdb_path, str(cache))
        if not ok:
            return None, msg
    try:
        conn = sqlite3.connect(f"file:{cache}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        backend = "access_parser" if not mdbtools_available() and _access_parser_available() else "mdbtools"
        return LegacyDbConn(conn, "sqlite"), f"内置 MDB 工具 → {cache.name} ({backend})"
    except Exception as e:
        return None, str(e)
