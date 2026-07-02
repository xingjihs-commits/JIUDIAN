"""门锁扫描相关：scan_cardlock_candidates、MifareKeyExtractor"""
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


from .schema_analyzer import (DatabaseScanner, DatabaseCracker, SchemaAnalyzer,
                              MIFARE_DEFAULT_KEYS)

CARDLOCK_MARKER_FILES = {
    "cardlock.exe": 35,
    "system.ini": 25,
    "machinerecorddll.dll": 20,
    "mwic_32.dll": 20,
    "v9rfl.dll": 20,
    "repairaccess.exe": 10,
}


def _safe_read_text(path: str, limit: int = 8192) -> str:
    try:
        data = Path(path).read_bytes()[:limit]
    except OSError:
        return ""
    for enc in ("utf-8", "gbk", "gb18030", "latin1"):
        try:
            return data.decode(enc, errors="ignore")
        except Exception:
            continue
    return data.decode(errors="ignore")


def _score_cardlock_candidate(entry: Dict[str, Any]) -> Dict[str, Any]:
    """已迁移到扫描器模块，保留为向后兼容包装器。

    委托给门锁系统扫描器的评分逻辑。
    """
    p = str(entry.get("path") or "")
    name = os.path.basename(p).lower()
    folder = os.path.dirname(p)
    folder_name = os.path.basename(folder).lower()
    score = 0
    reasons: List[str] = []

    if name == "cardlock.mdb":
        score += 70
        reasons.append("文件名 CardLock.mdb")
    elif name.endswith(".mdb"):
        score += 20
        reasons.append("Access MDB")

    if "智能门锁" in folder or "cardlock" in folder_name or "prousb" in folder_name:
        score += 35
        reasons.append("目录名像门锁系统")

    try:
        present = {x.lower() for x in os.listdir(folder)}
    except OSError:
        present = set()
    for marker, pts in CARDLOCK_MARKER_FILES.items():
        if marker in present:
            score += pts
            reasons.append(marker)

    ini = os.path.join(folder, "System.ini")
    if os.path.isfile(ini):
        txt = _safe_read_text(ini).lower()
        if "智能门锁" in txt or "cardlock" in txt or "prousb" in txt:
            score += 40
            reasons.append("System.ini 门锁特征")
        if "hotelid" in txt or "dbbakpath" in txt:
            score += 10
            reasons.append("酒店配置字段")

    size = int(entry.get("size") or 0)
    if 100_000 <= size <= 80_000_000:
        score += 5
    elif size == 0:
        score -= 20

    out = dict(entry)
    out["cardlock_score"] = score
    out["cardlock_reasons"] = reasons
    out["detail"] = "；".join(reasons) if reasons else str(entry.get("magic_type") or "")
    return out


def _cardlock_scan_roots(seed_path: str = "") -> List[str]:
    roots: List[str] = []
    seed = (seed_path or "").strip()
    if seed:
        roots.append(seed if os.path.isdir(seed) else os.path.dirname(seed))
    roots.extend([
        r"D:\AI\智能门锁管理系统新2021网络版",
        deploy_paths.cardlock_install_dir(),
        r"D:\智能门锁管理系统新2021网络版",
        deploy_paths.cardlock_backup_dir(),
        r"C:\CardLock",
        r"C:\Program Files\CardLock",
        r"C:\Program Files (x86)\CardLock",
        os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
        os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"),
        os.path.join(os.environ.get("USERPROFILE", ""), "Documents"),
    ])
    for drive in DatabaseScanner.scan_drives():
        roots.extend([
            os.path.join(drive, "智能门锁管理系统新2021网络版"),
            os.path.join(drive, "CardLock"),
            os.path.join(drive, "proUSB_DBBak"),
        ])
    out: List[str] = []
    seen = set()
    for r in roots:
        if not r:
            continue
        nr = os.path.abspath(os.path.normpath(r))
        key = nr.lower()
        if key in seen or not os.path.exists(nr):
            continue
        seen.add(key)
        out.append(nr)
    return out


def scan_cardlock_candidates(
    seed_path: str = "",
    *,
    limit: int = 12,
    progress_cb: Optional[Callable[[str], None]] = None,
    time_budget_s: float = 8.0,
) -> List[Dict[str, Any]]:
    """已迁移到扫描器模块的卡片样本提取，保留向后兼容包装器。

    自动扫描常见位置，按门锁特征排序候选旧库。
    内部委托给门锁系统扫描器获得更全面的扫描结果，再转换回旧格式。
    """
    found: Dict[str, Dict[str, Any]] = {}
    start = time.time()

    # 优先用新扫描器做全面检测
    try:
        from lock_deploy.scanner import LockSystemScanner
        scanner = LockSystemScanner(time_budget_s=time_budget_s, max_depth=5)
        roots = _cardlock_scan_roots(seed_path)
        for root in roots:
            if time.time() - start > time_budget_s:
                break
            if progress_cb:
                progress_cb(root)
            candidates = scanner.scan(seeds=[root])
            for c in candidates:
                if time.time() - start > time_budget_s:
                    break
                if not c.mdb_paths:
                    continue
                for mp in c.mdb_paths:
                    p = str(mp)
                    key = p.lower()
                    if key in found:
                        continue
                    entry = {
                        "path": p,
                        "name": mp.name,
                        "ext": ".mdb",
                        "size": mp.stat().st_size if mp.is_file() else 0,
                        "size_mb": 0,
                        "magic_type": "Access MDB",
                        "brand": c.brand,
                        "adapter_id": c.adapter_id,
                        "supported": c.supported,
                        "card_samples_count": len(c.card_samples),
                    }
                    scored = _score_cardlock_candidate(entry)
                    found[key] = scored
                    if len(found) >= limit:
                        break
    except Exception:
        # 降级到纯旧逻辑
        for root in _cardlock_scan_roots(seed_path):
            if time.time() - start > time_budget_s:
                break
            if progress_cb:
                progress_cb(root)
            root_l = root.lower()
            max_depth = 5 if any(k in root_l for k in ("cardlock", "智能门锁", "prousb")) else 2
            for entry in DatabaseScanner.scan_path_input(root, max_depth=max_depth, progress_cb=progress_cb):
                if time.time() - start > time_budget_s:
                    break
                if not entry:
                    continue
                ext = str(entry.get("ext") or "").lower()
                if ext not in (".mdb", ".accdb", ".db", ".sqlite", ".sqlite3", ".dbf"):
                    continue
                p = os.path.abspath(os.path.normpath(str(entry.get("path") or "")))
                if not p or p.lower() in found:
                    continue
                scored = _score_cardlock_candidate({**entry, "path": p})
                if int(scored.get("cardlock_score") or 0) >= 20 or os.path.basename(p).lower() == "cardlock.mdb":
                    found[p.lower()] = scored
                    if len(found) >= limit and int(scored.get("cardlock_score") or 0) >= 100:
                        break

    ranked = sorted(
        found.values(),
        key=lambda x: (
            -int(x.get("cardlock_score") or 0),
            str(x.get("name", "")).lower() != "cardlock.mdb",
            -int(x.get("size") or 0),
        ),
    )
    return ranked[:limit]



# ================================================================
# MIFARE 卡密钥破解器
# ================================================================
class MifareKeyExtractor:
    """
    MIFARE 经典卡密钥破解
    利用 MIFARE 经典卡的加密算法已知漏洞
    通过嵌套认证攻击获取所有扇区密钥
    """

    @staticmethod
    def check_nfc_reader() -> Tuple[bool, str]:
        """检查是否有 NFC 读卡器可用"""
        # 检查 libnfc 或 nfcpy
        try:
            import nfc
            readers = nfc.ContactlessFrontend().scan()
            if readers:
                return True, f"发现读卡器: {readers[0]}"
            return False, "未检测到 NFC 读卡器"
        except ImportError:
            pass
        except Exception:
            pass

        # 检查 ACR122U 等 USB 设备
        try:
            import smartcard.System
            readers = smartcard.System.readers()
            if readers:
                return True, f"发现智能卡读卡器: {readers[0]}"
            return False, "未检测到智能卡读卡器"
        except ImportError:
            pass
        except Exception:
            pass

        # 检查 mfoc 命令行工具
        try:
            result = subprocess.run(
                ["mfoc", "-h"], capture_output=True, timeout=5
            )
            if result.returncode in (0, 1):
                return True, "mfoc 工具可用"
        except FileNotFoundError:
            pass

        return False, "未检测到 NFC 读卡器（需要 ACR122U 或类似设备）"

    @staticmethod
    def try_default_keys() -> List[bytes]:
        """返回常见默认密钥列表"""
        return list(MIFARE_DEFAULT_KEYS)

    @staticmethod
    def crack_with_mfoc(output_file: str = None) -> Dict[str, Any]:
        """
        调用 mfoc 工具破解 MIFARE 经典卡
        需要: ACR122U 读卡器 + mfoc 工具
        """
        if not output_file:
            output_file = os.path.join(tempfile.gettempdir(), f"mfoc_dump_{int(time.time())}.mfd")

        try:
            result = subprocess.run(
                ["mfoc", "-O", output_file],
                capture_output=True, text=True, timeout=60,
            )
            output = result.stdout + result.stderr

            # 解析输出，提取密钥
            keys_found = []
            for line in output.split("\n"):
                if "Key found:" in line or "key found" in line.lower():
                    keys_found.append(line.strip())
                if "Auth OK" in line:
                    keys_found.append(line.strip())

            if os.path.exists(output_file):
                with open(output_file, "rb") as f:
                    dump_data = f.read()
                return {
                    "ok": True,
                    "keys_found": keys_found,
                    "dump_file": output_file,
                    "dump_size": len(dump_data),
                    "raw_output": output[:2000],
                }
            else:
                return {
                    "ok": False,
                    "error": "mfoc 未能生成转储文件",
                    "raw_output": output[:2000],
                }
        except FileNotFoundError:
            return {
                "ok": False,
                "error": "mfoc 工具未安装。请安装 apt 安装 mfoc 或从 GitHub 编译",
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "破解超时（60秒），请确认卡片已放在读卡器上"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def analyze_card_dump(dump_path: str) -> Dict[str, Any]:
        """
        分析 MIFARE 转储文件，提取房号、时间等信息
        MIFARE Classic 1K = 16扇区 × 4块 × 16字节 = 1024字节
        """
        try:
            with open(dump_path, "rb") as f:
                data = f.read()

            if len(data) < 1024:
                return {"ok": False, "error": f"转储文件太小 ({len(data)} 字节)，可能不完整"}

            analysis = {
                "ok": True,
                "total_bytes": len(data),
                "sectors": [],
                "possible_room_number": None,
                "possible_dates": [],
                "possible_ids": [],
                "raw_hex_preview": data[:256].hex(),
            }

            # 逐扇区分析
            for sector in range(min(16, len(data) // 64)):
                sector_data = data[sector * 64:(sector + 1) * 64]
                sector_info = {
                    "sector": sector,
                    "blocks": [],
                }

                for block in range(4):
                    block_data = sector_data[block * 16:(block + 1) * 16]
                    block_hex = block_data.hex()
                    block_ascii = "".join(
                        chr(b) if 32 <= b < 127 else "." for b in block_data
                    )

                    sector_info["blocks"].append({
                        "block": block,
                        "hex": block_hex,
                        "ascii": block_ascii,
                    })

                    # 尝试提取房号（通常是3-4位数字）
                    numbers = re.findall(r'\b(\d{3,4})\b', block_ascii)
                    for n in numbers:
                        if 100 <= int(n) <= 9999:
                            analysis["possible_room_number"] = n

                    # 尝试提取日期
                    dates = re.findall(
                        r'(\d{4}[-/]\d{2}[-/]\d{2})|(\d{2}[-/]\d{2}[-/]\d{4})',
                        block_ascii
                    )
                    for d in dates:
                        analysis["possible_dates"].append("".join(d))

                    # 尝试提取编号
                    ids = re.findall(r'([A-Z0-9]{6,12})', block_ascii)
                    for i in ids:
                        if i not in analysis["possible_ids"]:
                            analysis["possible_ids"].append(i)

                analysis["sectors"].append(sector_info)

            return analysis
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ================================================================

# ================================================================
# 后台扫描线程
# ================================================================
class ScanWorker(QThread):
    """后台线程：扫描硬盘找数据库文件"""
    progress = QtSignal(str)
    found_db = QtSignal(dict)
    finished = QtSignal(list)

    def __init__(self, scan_paths: List[str]):
        super().__init__()
        self.scan_paths = scan_paths

    def run(self):
        all_results = []
        for p in self.scan_paths:
            self.progress.emit(f"正在扫描: {p}")
            results = DatabaseScanner.scan_directory(
                p, max_depth=4,
                progress_cb=lambda path: self.progress.emit(f"扫描中: {path[:80]}")
            )
            for r in results:
                self.found_db.emit(r)
            all_results.extend(results)
        self.finished.emit(all_results)


class CrackWorker(QThread):
    """后台线程：尝试破解数据库"""
    progress = QtSignal(str)
    table_found = QtSignal(dict)
    finished = QtSignal(dict)

    def __init__(self, db_path: str, db_type: str):
        super().__init__()
        self.db_path = db_path
        self.db_type = db_type

    def run(self):
        result = {"ok": False, "tables": {}, "error": "", "path": self.db_path}

        if self.db_type in ("SQLite",):
            self.progress.emit("尝试打开 SQLite...")
            ok, conn, msg = DatabaseCracker.try_open_sqlite(self.db_path)
            if ok and conn:
                self.progress.emit("分析表结构...")
                tables = SchemaAnalyzer.analyze_sqlite(conn)
                conn.close()
                result["ok"] = True
                result["tables"] = tables
                result["msg"] = msg
            else:
                result["error"] = msg

        elif self.db_type in ("Access MDB", "Access ACCDB"):
            self.progress.emit("尝试读取 Access（如门锁数据库等）...")
            legacy, _dtype, msg = open_readonly_legacy_db(self.db_path)
            if legacy:
                try:
                    tables = SchemaAnalyzer.analyze_legacy(legacy)
                    result["ok"] = bool(tables)
                    result["tables"] = tables
                    result["msg"] = msg
                finally:
                    legacy.close()
            else:
                result["error"] = msg

        elif "dBase" in self.db_type or "FoxPro" in self.db_type:
            self.progress.emit("尝试读取 dBase...")
            ok, info, msg = DatabaseCracker.try_open_dbf(self.db_path)
            if ok:
                result["ok"] = True
                result["tables"] = {"_dbf_info": info}
                result["msg"] = msg
            else:
                result["error"] = msg

        else:
            # 尝试作为 CSV
            self.progress.emit("尝试作为 CSV 读取...")
            ok, info, msg = DatabaseCracker.try_read_csv(self.db_path)
            if ok:
                result["ok"] = True
                result["tables"] = {"_csv_info": info}
                result["msg"] = msg
            else:
                result["error"] = "无法识别数据库格式"

        self.finished.emit(result)


