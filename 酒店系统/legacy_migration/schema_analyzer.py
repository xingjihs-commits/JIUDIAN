"""Schema 分析相关：数据库扫描器、数据库破解器、遗留数据库连接、结构分析器"""
from __future__ import annotations
import csv
import hashlib
import json
import os
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import threading
import uuid as _uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import deploy_paths
from PySide6.QtCore import Qt, QThread, Signal as QtSignal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QProgressBar, QTabWidget,
    QCheckBox, QLineEdit, QWidget, QGroupBox, QGridLayout,
    QTableWidget, QTableWidgetItem, QComboBox, QTextEdit,
    QWizard, QWizardPage, QRadioButton, QButtonGroup,
    QHeaderView, QApplication, QListWidget, QListWidgetItem,
    QSplitter, QFrame, QSpinBox,
)

from database import db
from event_bus import bus
from ui_helpers import (
    style_dialog,
    style_wizard,
    build_dialog_header,
    show_error,
    show_info,
    show_warning,
    make_dialog_scroll_area,
)
from legacy_migration_guide import legacy_wizard_page_session
from migration_guide_panel import MigrationGuidePanel



def ensure_room_type_and_pricing(room_type: str) -> None:
    """迁移用：房型字符串在模板或价规中不存在时，补最小可用行（避免获取价格或入住算价失败）。"""
    rt = (room_type or "").strip()
    if not rt:
        return
    try:
        exists = db.execute("SELECT 1 FROM room_type_templates WHERE type_id=?", (rt,)).fetchone()
        if not exists:
            dep = db.get_config_float("default_deposit", 50.0)
            base = max(100.0, float(dep) * 2.0)
            hourly = max(40.0, base * 0.45)
            db.execute(
                "INSERT OR IGNORE INTO room_type_templates "
                "(type_id, type_name, base_price, hourly_price, consumables_json) VALUES (?,?,?,?,?)",
                (rt, rt, base, hourly, "{}"),
            )
        has_pr = db.execute("SELECT 1 FROM pricing_rules WHERE room_type=?", (rt,)).fetchone()
        if not has_pr:
            from pricing_engine import PricingEngine

            row = db.execute("SELECT base_price, hourly_price FROM room_type_templates WHERE type_id=?", (rt,)).fetchone()
            if row:
                bp, hp = float(row[0] or 100), float(row[1] or 50)
            else:
                bp, hp = 100.0, 50.0
            PricingEngine.save_rule(rt, bp, hp)
    except Exception:
        pass


def _coerce_date(val: Any) -> Optional[date]:
    """解析旧库中的日期/时间或时间戳，用于在住判断、账单日期过滤。"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, (int, float)):
        x = float(val)
        if x > 1e12:
            x = x / 1000.0
        if x > 1e9:
            try:
                return datetime.utcfromtimestamp(x).date()
            except (OverflowError, OSError, ValueError):
                return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:26], fmt).date()
        except ValueError:
            continue
    try:
        t = s.replace("Z", "").replace("T", " ")[:19]
        return datetime.fromisoformat(t).date()
    except Exception:
        return None


# ================================================================
# 常量
# ================================================================
SCAN_EXCLUDE_DIRS = {
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\ProgramData\\Microsoft", "C:\\$Recycle.Bin",
    "/proc", "/sys", "/dev", "/run", "/boot", "/etc/ssl",
}
DB_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".mdb", ".accdb", ".dbf", ".sdf"}
DB_MAGIC_BYTES = {
    b"SQLite format 3\x00": "SQLite",
    b"\x00\x01\x00\x00Standard Jet DB": "Access MDB",
    b"\x00\x01\x00\x00Standard ACE DB": "Access ACCDB",
    b"\x03": "dBase III",  # dBase III header
    b"\x04": "dBase IV",
    b"\x30": "Visual FoxPro",
    b"\xf5": "FoxPro with memo",
}
MIFARE_DEFAULT_KEYS = [
    b"\xff\xff\xff\xff\xff\xff",  # 出厂默认
    b"\xa0\xa1\xa2\xa3\xa4\xa5",  # 常见默认
    b"\xb0\xb1\xb2\xb3\xb4\xb5",
    b"\x4d\x3a\x99\xc3\x51\xdd",
    b"\x1a\x98\x2c\x7e\x45\x9a",
    b"\x00\x00\x00\x00\x00\x00",  # 全零
    b"\xd3\xf7\xd3\xf7\xd3\xf7",  # NXP 公开密钥
    b"\xa0\xb0\xc0\xd0\xe0\xf0",
]


# ================================================================
# 硬盘扫描器
# ================================================================
class DatabaseScanner:
    """已迁移到门锁部署扫描器模块，保留为向后兼容的包装器。"""

    @staticmethod
    def scan_drives() -> List[str]:
        """获取所有可用盘符"""
        drives = []
        if sys.platform == "win32":
            import string
            for letter in string.ascii_uppercase:
                p = f"{letter}:\\"
                if os.path.exists(p):
                    drives.append(p)
        else:
            drives.append("/")
        return drives

    @staticmethod
    def scan_directory(root: str, max_depth: int = 5, progress_cb=None) -> List[Dict[str, Any]]:
        """已迁移：委托给门锁部署扫描器模块的全面扫描。"""
        from lock_deploy.scanner import LockSystemScanner
        scanner = LockSystemScanner(max_depth=max_depth)
        candidates = scanner.scan(seeds=[root])
        results = []
        for c in candidates:
            for mp in c.mdb_paths:
                try:
                    st = mp.stat()
                    results.append({
                        "path": str(mp),
                        "name": mp.name,
                        "ext": mp.suffix.lower(),
                        "size": st.st_size,
                        "size_mb": round(st.st_size / 1024 / 1024, 2),
                        "magic_type": "Access MDB",
                    })
                except OSError:
                    pass
            # 也加入系统配置文件
            if c.system_ini:
                try:
                    results.append({
                        "path": str(c.system_ini),
                        "name": c.system_ini.name,
                        "ext": ".ini",
                        "size": c.system_ini.stat().st_size,
                        "size_mb": 0,
                        "magic_type": "System.ini",
                    })
                except OSError:
                    pass
        return results

    @staticmethod
    def entry_for_database_file(path: str) -> Optional[Dict[str, Any]]:
        """单个数据库文件到扫描条目，与目录扫描单条结果相同结构。"""
        path = os.path.abspath(os.path.normpath(path.strip().strip('"').strip("'")))
        if not os.path.isfile(path):
            return None
        ext = os.path.splitext(path)[1].lower()
        if ext not in DB_EXTENSIONS:
            return None
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        magic = DatabaseScanner._read_magic(path)
        if magic == "未知" and ext in (".db", ".sqlite", ".sqlite3"):
            magic = "SQLite"
        return {
            "path": path,
            "name": os.path.basename(path),
            "ext": ext,
            "size": size,
            "size_mb": round(size / 1024 / 1024, 2),
            "magic_type": magic,
        }

    @staticmethod
    def scan_path_input(path: str, max_depth: int = 3, progress_cb=None) -> List[Dict[str, Any]]:
        """支持文件夹递归扫描，或直接传入单个数据库文件完整路径。"""
        path = os.path.abspath(os.path.normpath(path.strip().strip('"').strip("'")))
        if os.path.isfile(path):
            one = DatabaseScanner.entry_for_database_file(path)
            return [one] if one else []
        if os.path.isdir(path):
            return DatabaseScanner.scan_directory(path, max_depth, progress_cb)
        return []

    @staticmethod
    def _read_magic(path: str) -> str:
        """读取文件头魔数判断数据库类型"""
        try:
            with open(path, "rb") as f:
                header = f.read(64)
            for magic_bytes, db_type in DB_MAGIC_BYTES.items():
                if header.startswith(magic_bytes):
                    return db_type
            try:
                text = header.decode("utf-8", errors="ignore")[:100]
                if text.strip().startswith("SQLite format 3"):
                    return "SQLite"
            except Exception:
                pass
            return "未知"
        except Exception:
            return "无法读取"


CARDLOCK_MARKER_FILES = {
    "cardlock.exe": 35,
    "system.ini": 25,
    "machinerecorddll.dll": 20,
    "mwic_32.dll": 20,
    "v9rfl.dll": 20,
    "repairaccess.exe": 10,
}



# ================================================================
# 数据库破解器
# ================================================================
class DatabaseCracker:
    """尝试破解/绕过各种数据库的密码保护"""

    @staticmethod
    def try_open_sqlite(path: str) -> Tuple[bool, Optional[sqlite3.Connection], str]:
        """尝试打开 SQLite 数据库，通常无密码。"""
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
            return True, conn, "无需密码"
        except sqlite3.DatabaseError as e:
            msg = str(e)
            if "encrypted" in msg.lower() or "password" in msg.lower():
                # SQLCipher 加密，尝试常见密码
                for pwd in ["", "admin", "password", "123456", "hotel", "hotel123"]:
                    try:
                        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                        conn.execute("PRAGMA key=?", (pwd,))
                        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
                        return True, conn, f"密码: {pwd}"
                    except Exception:
                        pass
                return False, None, "SQLCipher 加密，常见密码均失败"
            return False, None, msg
        except Exception as e:
            return False, None, str(e)

    @staticmethod
    def try_open_access(path: str) -> Tuple[bool, Optional[List[Dict]], str]:
        """
        尝试读取 Access 数据库（MDB 或 ACCDB）
        Windows 上通过 ADO 或 ODBC，或使用 mdbtools
        """
        ext = os.path.splitext(path)[1].lower()
        # 方法一：尝试用 Python 的 pyodbc（需要 Access 驱动）
        try:
            import pyodbc
            conn_str = (
                f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={path};"
                if ext == ".mdb"
                else f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={path};"
            )
            conn = pyodbc.connect(conn_str)
            cursor = conn.cursor()
            tables = []
            for row in cursor.tables():
                if row.table_type == "TABLE":
                    tables.append({"name": row.table_name, "type": "TABLE"})
            conn.close()
            return True, tables, "通过 ODBC 读取成功"
        except ImportError:
            pass
        except Exception as e:
            # 尝试 mdbtools
            pass

        # 方法二：尝试 mdbtools 命令行
        try:
            result = subprocess.run(
                ["mdb-tables", "-1", path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                tables = [
                    {"name": t.strip(), "type": "TABLE"}
                    for t in result.stdout.strip().split("\n") if t.strip()
                ]
                return True, tables, "通过 mdbtools 读取成功"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # 方法三：直接解析 MDB 文件头（简单情况）
        try:
            with open(path, "rb") as f:
                header = f.read(1024)
            # Access 2000+ 格式，尝试跳过密码页
            if header[0x42:0x42+2] == b"\x00\x01":
                return True, [], "Access 文件无密码保护（需安装 Access 驱动读取数据）"
        except Exception:
            pass

        return False, None, "无法读取 Access 文件（需要安装 Access 驱动或 mdbtools）"

    @staticmethod
    def try_open_dbf(path: str) -> Tuple[bool, Optional[List[Dict]], str]:
        """尝试读取 dBase 或 FoxPro 文件"""
        try:
            import dbfread
            table = dbfread.DBF(path, load=True)
            records = list(table)
            fields = table.field_names
            return True, [{"fields": fields, "count": len(records)}], "通过 dbfread 读取成功"
        except ImportError:
            pass
        except Exception as e:
            pass

        # 手动解析 dBase III 文件头
        try:
            with open(path, "rb") as f:
                header = f.read(32)
            if header[0] in (0x03, 0x04, 0x30, 0xf5):
                record_count = struct.unpack("<I", header[4:8])[0]
                header_len = struct.unpack("<H", header[8:10])[0]
                record_len = struct.unpack("<H", header[10:12])[0]
                return True, [{
                    "fields": f"dBase 文件，{record_count} 条记录，记录长度 {record_len}",
                    "count": record_count,
                }], "dBase 文件头解析成功（需 dbfread 库读取数据）"
        except Exception:
            pass

        return False, None, "无法读取 dBase 文件（需要安装 dbfread 库）"

    @staticmethod
    def try_read_csv(path: str) -> Tuple[bool, Optional[List[Dict]], str]:
        """尝试作为 CSV 读取"""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                sample = f.read(4096)
            if "," in sample or "\t" in sample:
                dialect = csv.Sniffer().sniff(sample[:1024])
                reader = csv.DictReader(sample.splitlines())
                if reader.fieldnames:
                    return True, [{"fields": reader.fieldnames}], "CSV 格式识别成功"
        except Exception:
            pass
        return False, None, "不是有效的 CSV 文件"

    @staticmethod
    def _is_mdb_locked(path: str) -> bool:
        """检查 MDB 文件是否被其他进程锁定（LDB 文件存在则说明被锁定）。"""
        ldb = os.path.splitext(path)[0] + ".ldb"
        if os.path.isfile(ldb):
            # .ldb 存在且有内容 ≠ 0 说明有其他进程打开
            try:
                if os.path.getsize(ldb) > 0:
                    return True
            except OSError:
                pass
        # 尝试以排他方式打开（探测锁）
        try:
            fd = os.open(path, os.O_RDONLY | os.O_EXCL)
            os.close(fd)
            return False
        except (OSError, PermissionError):
            return True
        except Exception:
            return False

    @staticmethod
    def connect_access(path: str) -> Tuple[Optional[Any], str]:
        """打开 Access 只读连接（供导入或分析）；需本机 Access ODBC 驱动。"""
        ext = os.path.splitext(path)[1].lower()
        try:
            import pyodbc
        except ImportError:
            return None, "未安装 pyodbc，无法读取 MDB"

        # 预检：文件是否被锁定
        if DatabaseCracker._is_mdb_locked(path):
            return None, (
                "门锁数据库被其他程序占用（可能前台正在使用）。\n"
                "请关闭旧门锁系统前台后重试。"
            )

        last_err = ""
        for drv in _ACCESS_ODBC_DRIVERS:
            try:
                # 使用线程包装实现总超时
                conn_str = f"DRIVER={{{drv}}};DBQ={path};"
                conn_holder = [None]
                exc_holder = [None]

                def _do_connect():
                    try:
                        conn_holder[0] = pyodbc.connect(conn_str, readonly=True, timeout=8)
                    except Exception as e:
                        exc_holder[0] = e

                t = threading.Thread(target=_do_connect, daemon=True)
                t.start()
                t.join(12.0)  # 总超时 12 秒
                if t.is_alive():
                    # 超时未返回，跳过该驱动
                    continue
                if exc_holder[0]:
                    last_err = str(exc_holder[0])
                    continue
                conn = conn_holder[0]
                if conn is None:
                    continue
                conn.cursor().execute("SELECT 1")
                return conn, f"ODBC: {drv}"
            except Exception as e:
                last_err = str(e)
        return None, (
            last_err
            or "无法连接 Access。请安装 Microsoft Access Database Engine（与系统位数一致）。"
        )


# proUSB 或智能门锁 2021 常见表名与字段（前台门锁数据库）
CARDLOCK_TABLE_PRIORITY: Dict[str, List[str]] = {
    "rooms": ["room", "bld", "fang", "chambre", "rmdef", "rmaster", "roomsdefinition", "rminfo", "roominfo", "tblroom"],
    "guests": ["walkin", "checkout", "guest", "folio", "lodg", "inhouse", "checkin", "registration", "stay", "tblguest"],
    "orders": ["bill", "charge", "account", "sale", "消费", "营业", "payment", "receipt", "cashier", "ledger"],
    "cards": ["hotel_card", "cardlock", "issuecard", "cardrecord", "cardissue", "doorcard", "keycard"],
}
CARDLOCK_FIELD_EXTRA: Dict[str, List[str]] = {
    "room_id": ["bldroomno", "roomno", "fno", "fanghao", "rmno", "rm_no", "room_num", "bldno", "roomcode", "room_code", "លេខបន្ទប់"],
    "floor": ["floorno", "floor_no", "bldfloor", "fl", "level", "ជាន់"],
    "room_type": ["rtype", "roomtype", "room_class", "class", "category", "ប្រភេទបន្ទប់"],
    "guest_name": ["holder", "guestname", "customername", "持卡人", "namecn", "paxname", "clientname", "ឈ្មោះភ្ញៀវ"],
    "phone": ["guestphone", "mobilephone", "telno", "contactno", "ទូរស័ព្ទ"],
    "checkin_time": ["issuetime", "checkintime", "intime", "arrivetime", "入住时间", "arrivaldate", "starttime"],
    "checkout_time": ["checkouttime", "outtime", "departtime", "退房时间", "expiredtime", "departuredate", "validuntil"],
    "deposit": ["deposit", "depositamt", "preauth", "bond", "advance", "ប្រាក់កក់"],
    "price": ["roomrate", "rate", "dayrate", "amount", "tariff", "តម្លៃ"],
    "card_id": ["cardno", "card_no", "cardnum", "uid", "serialno", "卡号", "cardid", "card_code"],
    "issue_time": ["issuetime", "issuedate", "maketime", "发卡时间", "createtime"],
    "expire_time": ["expiretime", "validto", "endtime", "有效期", "valid_until"],
    "key_a": ["keya", "key_a", "sector_key_a", "authkeya", "mifarekeya"],
    "key_b": ["keyb", "key_b", "sector_key_b", "authkeyb", "mifarekeyb"],
}


_ACCESS_ODBC_DRIVERS = (
    "Microsoft Access Driver (*.mdb, *.accdb)",
    "Microsoft Access Driver (*.mdb)",
)


class LegacyDbConn:
    """SQLite 或 Access 统一只读访问（前台旧门锁库等）。"""

    def __init__(self, raw_conn: Any, kind: str):
        self._conn = raw_conn
        self.kind = kind  # sqlite | access

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    @staticmethod
    def _safe_ident(name: str) -> str:
        n = (name or "").strip()
        if not n or not re.match(r"^[\w\u4e00-\u9fff]+$", n):
            raise ValueError(f"非法标识符: {name!r}")
        return n

    def fetch_table(
        self,
        table_name: str,
        *,
        date_col: Optional[str] = None,
        recent_days: int = 0,
    ) -> Tuple[List[Any], List[str]]:
        t = self._safe_ident(table_name)
        cur = self._conn.cursor()
        if self.kind == "sqlite":
            sql = f"SELECT * FROM '{t}'"
            if recent_days > 0 and date_col:
                dc = self._safe_ident(date_col)
                sql = (
                    f"SELECT * FROM '{t}' WHERE date(\"{dc}\") "
                    f">= date('now', '-{int(recent_days)} days')"
                )
        else:
            sql = f"SELECT * FROM [{t}]"
            if recent_days > 0 and date_col:
                dc = self._safe_ident(date_col)
                sql = (
                    f"SELECT * FROM [{t}] WHERE [{dc}] >= "
                    f"DateAdd('d', -{int(recent_days)}, Date())"
                )
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in (cur.description or ())]
        return rows, cols


def open_readonly_legacy_db(path: str) -> Tuple[Optional[LegacyDbConn], str, str]:
    """按扩展名打开只读库；返回（连接、类型标签、说明）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".db", ".sqlite", ".sqlite3"):
        ok, conn, msg = DatabaseCracker.try_open_sqlite(path)
        if ok and conn:
            return LegacyDbConn(conn, "sqlite"), "SQLite", msg
        return None, "SQLite", msg or "无法打开 SQLite"
    if ext in (".mdb", ".accdb"):
        raw, msg = DatabaseCracker.connect_access(path)
        if raw:
            return LegacyDbConn(raw, "access"), "Access MDB", msg
        try:
            from runtime_deps import ensure_hotel_runtime_deps

            dep = ensure_hotel_runtime_deps(install_ace=True, prefer_mdbtools=False)
            if dep.get("method") == "ace_bundled" or dep.get("method") == "odbc_existing":
                raw2, msg2 = DatabaseCracker.connect_access(path)
                if raw2:
                    return LegacyDbConn(raw2, "access"), "Access MDB", msg2
        except Exception:
            pass
        try:
            from mdb_import_backend import open_mdb_via_sqlite_cache

            legacy, mmsg = open_mdb_via_sqlite_cache(path)
            if legacy:
                return legacy, "CardLock(MDB)", mmsg
            msg = f"{msg} | {mmsg}" if mmsg else msg
        except Exception as e:
            msg = f"{msg} | 内置工具: {e}"
        return None, "Access MDB", msg
    return None, "未知", f"不支持的扩展名: {ext}"


def find_cardlock_mdb_paths() -> List[str]:
    """前台电脑上自动定位门锁旧数据库。"""
    candidates: List[str] = []
    roots = [
        deploy_paths.cardlock_install_dir(),
        r"D:\智能门锁管理系统新2021网络版",
        r"D:\AI\智能门锁管理系统新2021网络版",
        r"C:\CardLock",
        r"C:\Program Files\CardLock",
        r"C:\Program Files (x86)\CardLock",
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "智能门锁管理系统新2021网络版"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "智能门锁管理系统新2021网络版"),
    ]
    for root in roots:
        if not root:
            continue
        p = os.path.join(root, "CardLock.mdb")
        if os.path.isfile(p):
            candidates.append(os.path.normpath(p))
    bak = deploy_paths.cardlock_backup_dir()
    if os.path.isdir(bak):
        for name in os.listdir(bak):
            if name.lower().endswith(".mdb"):
                candidates.append(os.path.normpath(os.path.join(bak, name)))
    seen = set()
    out: List[str] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            out.append(p)
    # 兜底自动扫描整机，按门锁系统特征打分，避免操作员手工找路径。
    try:
        for ent in scan_cardlock_candidates(limit=8):
            p = os.path.normpath(str(ent.get("path") or ""))
            if p and p not in seen and os.path.isfile(p):
                seen.add(p)
                out.append(p)
    except Exception:
        pass
    return out


def suggest_cardlock_import_plan(tables: Dict[str, Any]) -> List[Dict[str, Any]]:
    """为门锁数据库生成推荐导入项（表 + 映射 + 类型）。"""
    scored: List[Tuple[int, str, str, Dict[str, str]]] = []
    for tname, tinfo in tables.items():
        if not isinstance(tinfo, dict) or "column_names" not in tinfo:
            continue
        columns = tinfo.get("column_names", [])
        if not columns:
            continue
        mapping = SchemaAnalyzer.auto_map_fields(columns, extra_aliases=CARDLOCK_FIELD_EXTRA)
        if not mapping:
            continue
        tl = tname.lower()
        purpose = SchemaAnalyzer.guess_table_purpose(tname, columns)
        import_type = None
        score = 0
        for itype, hints in CARDLOCK_TABLE_PRIORITY.items():
            for h in hints:
                if h in tl:
                    score += 10
                    import_type = itype if itype != "cards" else None
                    break
        if purpose == "房间表":
            import_type = import_type or "rooms"
            score += 5
        elif purpose in ("客人表", "入住记录表"):
            import_type = import_type or "guests"
            score += 5
        elif purpose == "门锁/房卡表":
            score += 8
        elif purpose == "账单/消费表":
            import_type = import_type or "orders"
            score += 4
        if import_type in ("rooms", "guests", "orders"):
            scored.append((score, tname, import_type, mapping))
    scored.sort(key=lambda x: (-x[0], x[1]))
    used_types: set = set()
    imports: List[Dict[str, Any]] = []
    for _sc, tname, itype, mapping in scored:
        if itype in used_types:
            continue
        used_types.add(itype)
        imports.append({"table": tname, "type": itype, "mapping": mapping})
    return imports



# ================================================================
# 表结构分析器
# ================================================================
class SchemaAnalyzer:
    """读取数据库表结构，自动推荐字段映射"""

    # 常见旧系统字段名 → 我们系统字段名
    FIELD_ALIAS_MAP: Dict[str, List[str]] = {
        "room_id": ["room_id", "room_no", "roomno", "room_num", "roomnumber",
                     "fh", "房号", "房间号", "room_code", "rno", "r_id",
                     "chambre", "zimmer", "habitacion", "room", "roomcode", "roomname",
                     "room#", "លេខបន្ទប់"],
        "floor": ["floor", "flr", "floor_no", "lc", "楼层", "floor_num",
                   "storey", "etage", "stockwerk"],
        "room_type": ["room_type", "rtype", "type", "roomtype", "rt", "房型",
                       "room_class", "category", "cat", "type_name", "lx",
                       "tariff_type", "rate_code", "ប្រភេទបន្ទប់"],
        "status": ["status", "state", "room_status", "zt", "状态", "flag",
                    "occ", "occupied", "available"],
        "guest_name": ["guest_name", "name", "guestname", "customer", "xm",
                        "姓名", "住客", "guest", "cname", "fullname",
                        "first_name", "last_name", "surname", "client", "pax",
                        "ឈ្មោះ", "ឈ្មោះភ្ញៀវ"],
        "phone": ["phone", "tel", "mobile", "telephone", "dh", "电话", "手机",
                   "contact", "cell", "cellphone", "phonenumber", "whatsapp",
                   "telegram", "ទូរស័ព្ទ"],
        "checkin_time": ["checkin", "checkin_time", "ci_time", "in_time",
                          "rzsj", "入住时间", "arrive", "arrival", "indate",
                          "check_in", "start_date", "begin_time"],
        "checkout_time": ["checkout", "checkout_time", "co_time", "out_time",
                           "tzsj", "退房时间", "depart", "departure", "outdate",
                           "check_out", "end_date", "leave_time"],
        "id_card": ["id_card", "idcard", "id_no", "identity", "sfz", "身份证",
                     "passport", "card_no", "document", "license"],
        "deposit": ["deposit", "dep", "yjj", "押金", "prepay", "advance",
                     "bond", "guarantee", "cash_pledge"],
        "price": ["price", "rate", "room_rate", "jg", "价格", "房价", "amount",
                   "daily_rate", "day_price", "unit_price", "base_price", "tariff",
                   "usd", "khr", "តម្លៃ"],
        "consumption": ["consumption", "consume", "xf", "消费", "charge",
                         "extra", "additional", "bill", "expense"],
        "bill_date": ["created_at", "create_time", "biz_date", "bill_date", "trade_time", "oper_time",
                      "消费时间", "结账时间", "营业日期", "会计日期", "发生时间", "日期时间", "dt", "op_time"],
    }

    @staticmethod
    def analyze_sqlite(conn: sqlite3.Connection) -> Dict[str, Any]:
        """分析 SQLite 数据库结构"""
        tables = {}
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            table_names = [row[0] for row in cur.fetchall()]

            for tname in table_names:
                cur.execute(f"PRAGMA table_info('{tname}')")
                columns = [
                    {"name": row[1], "type": row[2], "pk": bool(row[5])}
                    for row in cur.fetchall()
                ]
                MAX_COUNT_ROWS = 500_000
                cur.execute(f"SELECT COUNT(*) FROM '{tname}'")
                row_count = min(int(cur.fetchone()[0]), MAX_COUNT_ROWS)
                tables[tname] = {
                    "columns": columns,
                    "row_count": row_count,
                    "column_names": [c["name"] for c in columns],
                }
        except Exception:
            pass
        return tables

    @staticmethod
    def analyze_access(conn: Any) -> Dict[str, Any]:
        """分析 Access 库表结构（pyodbc 连接）。"""
        tables: Dict[str, Any] = {}
        try:
            cur = conn.cursor()
            names: List[str] = []
            for row in cur.tables(tableType="TABLE"):
                tname = row.table_name
                if not tname or str(tname).startswith("MSys"):
                    continue
                names.append(tname)
            for tname in names:
                cols: List[Dict[str, Any]] = []
                try:
                    for col in cur.columns(table=tname):
                        cols.append({
                            "name": col.column_name,
                            "type": col.type_name or "",
                            "pk": False,
                        })
                except Exception:
                    pass
                row_count = 0
                try:
                    safe = LegacyDbConn._safe_ident(tname)
                    # 使用 TOP 限制行数，避免在大表上卡死
                    MAX_COUNT_ROWS = 500_000
                    cur.execute(f"SELECT COUNT(*) FROM [{safe}]")
                    raw = cur.fetchone()
                    if raw:
                        row_count = min(int(raw[0]), MAX_COUNT_ROWS)
                except Exception:
                    pass
                tables[tname] = {
                    "columns": cols,
                    "row_count": row_count,
                    "column_names": [c["name"] for c in cols],
                }
        except Exception:
            pass
        return tables

    @staticmethod
    def analyze_legacy(legacy: LegacyDbConn) -> Dict[str, Any]:
        if legacy.kind == "sqlite":
            return SchemaAnalyzer.analyze_sqlite(legacy._conn)
        return SchemaAnalyzer.analyze_access(legacy._conn)

    @staticmethod
    def auto_map_fields(
        source_columns: List[str],
        extra_aliases: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, str]:
        """
        自动将旧系统字段映射到我们系统字段
        返回：旧字段名到我们字段名的映射
        """
        alias_map = dict(SchemaAnalyzer.FIELD_ALIAS_MAP)
        if extra_aliases:
            for k, extra in extra_aliases.items():
                alias_map[k] = list(dict.fromkeys(list(alias_map.get(k, [])) + list(extra)))
        mapping: Dict[str, str] = {}
        for our_field, aliases in alias_map.items():
            for src_col in source_columns:
                src_lower = src_col.lower().strip().replace(" ", "_").replace("-", "_")
                for alias in aliases:
                    alias_lower = alias.lower().strip().replace(" ", "_").replace("-", "_")
                    if src_lower == alias_lower or src_lower in alias_lower or alias_lower in src_lower:
                        if src_col not in mapping:
                            mapping[src_col] = our_field
                        break
                if src_col in mapping:
                    break
        return mapping

    @staticmethod
    def guess_table_purpose(table_name: str, columns: List[str]) -> str:
        """猜测表的用途（表名 + 列名联合，优先识别在住入住与营业流水）。"""
        name_lower = table_name.lower()
        cols_join = " ".join(c.lower() for c in columns)

        has_room = any(
            k in cols_join
            for k in ("room_id", "room_no", "roomno", "房号", "fh", "r_id", "roomnum",
                      "bldroomno", "rmno", "rm_no", "roomcode", "room_code")
        )
        has_person = any(
            k in cols_join
            for k in ("guest", "name", "姓名", "住客", "customer", "xm", "cname")
        )
        has_ci = any(k in cols_join for k in ("checkin", "入住", "arrival", "indate", "rzsj"))
        has_co = any(k in cols_join for k in ("checkout", "退房", "depart", "outdate", "tzsj"))
        inhouse_kw = ("在住", "现时", "实时", "当前", "lodg", "folio", "occup", "stay", "房态")
        if has_room and has_person and (has_ci or has_co):
            if any(k in name_lower for k in inhouse_kw) or (has_ci and has_co):
                return "入住记录表"
            if has_ci:
                return "入住记录表"

        has_amt = any(k in cols_join for k in ("amount", "total", "金额", "money", "price", "amt", "fee"))
        has_dt = any(
            k in cols_join
            for k in ("created_at", "create_time", "biz_date", "bill_date", "trade_time", "时间", "日期")
        )
        if has_room and has_amt and has_dt and not has_person:
            return "账单/消费表"

        if any(k in name_lower for k in ["room", "房", "chambre", "zimmer"]):
            return "房间表"
        if any(k in name_lower for k in ["guest", "customer", "住", "客", "client"]):
            return "客人表"
        if any(k in name_lower for k in ["order", "bill", "账", "单", "消费", "charge", "invoice", "营业", "流水", "pos"]):
            return "账单/消费表"
        if any(k in name_lower for k in ["checkin", "check_in", "入住", "register"]):
            return "入住记录表"
        if any(k in name_lower for k in ["config", "setting", "配置", "param"]):
            return "配置表"
        if any(k in name_lower for k in ["user", "staff", "员工", "operator"]):
            return "员工/操作员表"
        if any(k in name_lower for k in ["lock", "card", "门锁", "卡", "key"]):
            return "门锁/房卡表"
        if any(k in name_lower for k in ["member", "vip", "会员", "积分", "loyalty"]):
            return "会员/积分表"
        if any(k in name_lower for k in ["log", "audit", "日志", "审计"]):
            return "日志/审计表"
        return "其他"


