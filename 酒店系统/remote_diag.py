"""
remote_diag.py — 厂家控制台远程诊断模块

  供厂家技术支持人员在厂家控制台或云端远程调用，
实现非侵入式诊断：日志查看、数据库状态、适配器状态、系统资源、远程 SQL。

安全约束：
  - remote_sql() 只允许 SELECT 语句，且需要酒店端操作员确认。
  - 所有诊断结果均为只读快照，不修改任何数据。
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import platform
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LOG_LOCK = threading.Lock()


from deploy_paths import get_deploy_root as _get_app_dir


def _log_file_path() -> Path:
    return _get_app_dir() / "logs" / "solid.log"


def _db_file_path() -> Path:
    from database import db as _db
    return Path(_db.db_path).resolve()


class RemoteDiagnosis:

    @staticmethod
    def tail_logs(lines: int = 50) -> list[str]:
        lines = max(1, min(lines, 500))
        log_path = _log_file_path()
        with _LOG_LOCK:
            if not log_path.exists():
                return ["[LOG NOT FOUND]"]
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(0, os.SEEK_END)
                    file_size = f.tell()
                    if file_size == 0:
                        return []
                    block_size = 4096
                    collected = []
                    remaining = lines
                    pos = file_size
                    while pos > 0 and remaining > 0:
                        read_size = min(block_size, pos)
                        pos -= read_size
                        f.seek(pos)
                        chunk = f.read(read_size)
                        chunk_lines = chunk.splitlines(keepends=False)
                        if pos > 0 and collected:
                            chunk_lines[-1] = chunk_lines[-1] + (collected[0] if collected else "")
                            collected.pop(0)
                        collected = chunk_lines + collected
                        remaining -= len(chunk_lines)
                    return collected[-lines:] if len(collected) > lines else collected
            except OSError as e:
                return [f"[ERR] 读取日志失败: {e}"]

    @staticmethod
    def db_status() -> dict:
        result = {
            "db_path": "", "db_size_mb": 0.0, "fragmentation_pct": 0.0,
            "table_count": 0, "total_rows": 0,
            "last_backup_at": None, "last_vacuum_at": None,
            "journal_mode": "unknown",
        }
        try:
            db_path = _db_file_path()
            result["db_path"] = str(db_path)
            if db_path.exists():
                result["db_size_mb"] = round(db_path.stat().st_size / (1024 * 1024), 2)
        except Exception:
            return result
        from database import db as _db
        try:
            row = _db.execute("PRAGMA journal_mode").fetchone()
            if row:
                result["journal_mode"] = row[0] or "unknown"
            fc = _db.execute("PRAGMA freelist_count").fetchone()
            pc = _db.execute("PRAGMA page_count").fetchone()
            if fc and pc and pc[0] > 0:
                result["fragmentation_pct"] = round((fc[0] / pc[0]) * 100, 2)
            rows = _db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()
            result["table_count"] = rows[0] if rows else 0
            big_tables = ["ledger", "audit_events", "card_records", "guests", "rooms"]
            # [sub-e] SQL 注入加固：big_tables 是硬编码列表，但白名单是 defense-in-depth
            from database import _ALLOWED_TABLES, _validate_identifier
            total = 0
            for t in big_tables:
                try:
                    safe_t = _validate_identifier(t, _ALLOWED_TABLES)
                    r = _db.execute(f"SELECT COUNT(*) FROM {safe_t}").fetchone()
                    if r:
                        total += r[0]
                except Exception:
                    pass
            result["total_rows"] = total
        except Exception as e:
            result["error"] = str(e)
        try:
            result["last_backup_at"] = _db.get_config("last_backup_at") or None
            result["last_vacuum_at"] = _db.get_config("last_vacuum_at") or None
        except Exception:
            pass
        return result

    @staticmethod
    def lock_adapter_status() -> dict:
        result = {
            "active_adapter": None, "available_brands": [],
            "bridge_32_running": False, "cardlock_auto_running": False,
            "last_issue_at": None, "last_issue_room": None,
            "last_issue_success": False,
        }
        try:
            from lock_adapters import available_adapters
            result["available_brands"] = [
                a.__name__ if hasattr(a, '__name__') else type(a).__name__
                for a in available_adapters()
            ]
        except Exception:
            pass
        try:
            from database import db as _db
            active = _db.get_config("lock_adapter_type") or ""
            if active:
                result["active_adapter"] = active
                result["last_issue_at"] = _db.get_config("last_card_issue_at") or None
                result["last_issue_room"] = _db.get_config("last_card_issue_room") or None
                result["last_issue_success"] = _db.get_config("last_card_issue_success") == "1"
        except Exception:
            pass
        try:
            import subprocess
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq rfl_bridge_32.exe"],
                timeout=5, text=True, stderr=subprocess.DEVNULL,
            )
            result["bridge_32_running"] = "rfl_bridge_32.exe" in out
        except Exception:
            pass
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq CardLock.exe"],
                timeout=5, text=True, stderr=subprocess.DEVNULL,
            )
            result["cardlock_auto_running"] = "CardLock.exe" in out
        except Exception:
            pass
        return result

    @staticmethod
    def system_resources() -> dict:
        import sys
        result = {
            "cpu_pct": 0.0, "memory_rss_mb": 0.0, "memory_vms_mb": 0.0,
            "disk_total_gb": 0.0, "disk_free_gb": 0.0, "disk_pct_used": 0.0,
            "os": platform.platform(), "python_version": sys.version,
            "uptime_seconds": 0.0,
        }
        try:
            import psutil
            proc = psutil.Process()
            result["cpu_pct"] = round(proc.cpu_percent(interval=0.1), 1)
            mem = proc.memory_info()
            result["memory_rss_mb"] = round(mem.rss / (1024 * 1024), 1)
            result["memory_vms_mb"] = round(mem.vms / (1024 * 1024), 1)
            result["uptime_seconds"] = round(time.time() - proc.create_time(), 0)
        except ImportError:
            try:
                import ctypes
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_uint32),
                        ("dwMemoryLoad", ctypes.c_uint32),
                        ("ullTotalPhys", ctypes.c_uint64),
                        ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64),
                        ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64),
                        ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64),
                    ]
                mem_stat = MEMORYSTATUSEX()
                mem_stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem_stat))
                import win32process
                import win32api
                hproc = win32api.GetCurrentProcess()
                mem_info = win32process.GetProcessMemoryInfo(hproc)
                result["memory_rss_mb"] = round(mem_info["WorkingSetSize"] / (1024 * 1024), 1)
            except Exception:
                pass
        except Exception as e:
            result["resource_error"] = str(e)
        try:
            db_path = _db_file_path()
            drive_letter = getattr(db_path, 'drive', 'C:') or 'C:'
            usage = shutil.disk_usage(f"{drive_letter}/")
            result["disk_total_gb"] = round(usage.total / (1024**3), 2)
            result["disk_free_gb"] = round(usage.free / (1024**3), 2)
            result["disk_pct_used"] = round((1 - usage.free / usage.total) * 100, 1)
        except Exception:
            pass
        return result

    @staticmethod
    def remote_sql(query: str) -> dict:
        result: dict = {"ok": False, "rows": [], "columns": [], "error": None}
        q = (query or "").strip()
        if not q.upper().startswith("SELECT"):
            result["error"] = "仅允许 SELECT 查询"
            return result
        dangerous = ["ATTACH", "DETACH", "CREATE", "DROP", "ALTER",
                      "INSERT", "UPDATE", "DELETE", "PRAGMA", "REINDEX"]
        q_upper = q.upper()
        for kw in dangerous:
            if kw in q_upper:
                result["error"] = f"禁止使用 {kw} 语句"
                return result
        q_lower = q.lower()
        if "limit" not in q_lower:
            q = f"{q} LIMIT 200"
        from database import db as _db
        try:
            cur = _db.execute(q)
            result["columns"] = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            result["rows"] = [list(r) for r in rows[:200]]
            result["ok"] = True
        except Exception as e:
            result["error"] = str(e)
            logger.warning("[remote_diag] SQL 执行失败: %s → %s", q[:120], e)
        return result


def get_full_diagnosis() -> dict:
    return {
        "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
        "hotel_id": _safe_get_config("hotel_id"),
        "hotel_name": _safe_get_config("hotel_name"),
        "version": _safe_get_config("app_version") or "1.0.0",
        "logs": RemoteDiagnosis.tail_logs(30),
        "database": RemoteDiagnosis.db_status(),
        "adapters": RemoteDiagnosis.lock_adapter_status(),
        "resources": RemoteDiagnosis.system_resources(),
    }


def _safe_get_config(key: str) -> Optional[str]:
    try:
        from database import db as _db
        return _db.get_config(key)
    except Exception:
        return None
