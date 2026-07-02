"""
usb_lock_scanner.py — 门锁系统自动识别与迁移引擎（U盘/本地指纹/学习 三层）
=============================================================================
功能：
  1. U盘扫描 — 按品牌档案文件名匹配（老逻辑，其他品牌用）
  2. 本地硬盘深度指纹扫描 — 动态库导出表／配置文件字段／数据库表结构（改名也逃不掉）
  3. 学习模式 — 放一张现有卡→读卡提取发卡器标识→直接写配置

使用方式：
  from usb_lock_scanner import UsbLockScanner
  scanner = UsbLockScanner()
  result = scanner.scan_and_detect()
  scanner.migrate(result[0])
"""

import os
import json
import string
import shutil
import platform
import datetime
import fnmatch
import re
import threading
import ctypes
from pathlib import Path
from database import db
import logging
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  工具函数 — 带超时的执行包装，防止 os.listdir / os.walk 卡死
# ─────────────────────────────────────────────────────────────────────────────

def _run_with_timeout(fn, timeout_s: float = 8.0):
    """在守护线程中执行传入函数，超时返回空。"""
    result = [None]
    exc_info = [None]

    def _worker():
        try:
            result[0] = fn()
        except Exception as e:
            exc_info[0] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        logger.warning("[USB_SCANNER] 操作超时（> %gs），已跳过", timeout_s)
        return None
    if exc_info[0]:
        logger.debug("[USB_SCANNER] 操作异常: %s", exc_info[0])
        return None
    return result[0]


_DRIVE_TYPE_REMOTE = 4  # DRIVE_REMOTE

def _is_fixed_or_removable(drive_root: str) -> bool:
    """判断盘符是不是固定硬盘或可移动介质（排除网络映射盘）。"""
    try:
        dt = ctypes.windll.kernel32.GetDriveTypeW(drive_root)
        return dt in (2, 3)  # DRIVE_REMOVABLE=2, DRIVE_FIXED=3
    except Exception:
        return True  # 无法判断时放行


def _safe_listdir(path: str) -> list:
    """带超时的列表文件，超时返回空列表。"""
    ret = _run_with_timeout(lambda: os.listdir(path), timeout_s=6.0)
    return ret if ret is not None else []

# USB_LOCK_PROFILES 目录（与本文件同级）
_PROFILES_DIR = Path(__file__).parent / "USB_LOCK_PROFILES"
_PROFILES_FILE = _PROFILES_DIR / "profiles.json"

# V9/CardLock 常见安装路径（文件名可能改，但目录结构特征保留）
_V9_INSTALL_HINTS = [
    "智能门锁管理系统",
    "智能门锁",
    "门锁管理系统",
    "CardLock",
    "proUSB",
    "proUSB_DBBak",
    "门锁系统",
    "DoorLock",
]
# 全局扫描根目录（Windows）
_V9_SCAN_ROOTS = [
    "D:\\",
    "C:\\Program Files (x86)",
    "C:\\Program Files",
    os.path.expanduser("~\\Desktop"),
    os.path.expanduser("~\\Documents"),
]


# ─────────────────────────────────────────────────────────────────────────────
#  工具函数 — U盘枚举
# ─────────────────────────────────────────────────────────────────────────────

def _get_usb_drives() -> list:
    """获取当前系统所有可移动驱动器路径列表"""
    drives = []
    if platform.system() == "Windows":
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive = f"{letter}:\\"
                try:
                    drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive)
                    if drive_type == 2:
                        drives.append(drive)
                except Exception:
                    pass
            bitmask >>= 1
    elif platform.system() == "Linux":
        for base in ["/media", "/mnt"]:
            if os.path.exists(base):
                for entry in os.listdir(base):
                    full = os.path.join(base, entry)
                    if os.path.ismount(full):
                        drives.append(full)
    elif platform.system() == "Darwin":
        for entry in os.listdir("/Volumes"):
            full = f"/Volumes/{entry}"
            if os.path.ismount(full):
                drives.append(full)
    return drives


# ─────────────────────────────────────────────────────────────────────────────
#  工具函数 — 本地硬盘扫描（找门锁安装目录）
# ─────────────────────────────────────────────────────────────────────────────

def _find_v9_install_dirs() -> list:
    """在硬盘上扫描可能的 V9／门锁安装目录。
    
    策略：先扫常见根目录下含特征关键词的文件夹，
    再扫所有本地（非网络）盘符根目录（一级）。
    所有文件列表调用均有 6 秒超时保护。
    """
    candidates = []

    def _probe_dir(d: str) -> bool:
        """看一个目录是否有门锁文件气息。"""
        d = d.lower()
        for hint in _V9_INSTALL_HINTS:
            if hint.lower() in d:
                return True
        return False

    for root in _V9_SCAN_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            for entry in _safe_listdir(root):
                full = os.path.join(root, entry)
                if os.path.isdir(full) and _probe_dir(entry):
                    candidates.append(full)
        except PermissionError:
            pass

    # 只扫本地固定盘/可移动盘符，跳过网络盘（DRIVE_REMOTE=4）
    if platform.system() == "Windows":
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive = f"{letter}:\\"
                bitmask >>= 1
                if not _is_fixed_or_removable(drive):
                    continue  # 跳过网络映射盘
                try:
                    for entry in _safe_listdir(drive):
                        full = os.path.join(drive, entry)
                        if os.path.isdir(full) and _probe_dir(entry):
                            if full not in candidates:
                                candidates.append(full)
                except PermissionError:
                    pass

    return candidates


# ─────────────────────────────────────────────────────────────────────────────
#  内容指纹识别 — DLL 导出表
# ─────────────────────────────────────────────────────────────────────────────

def _fingerprint_dll(dll_path: str, expected: list) -> dict:
    """扫描一个动态库的导出表，匹配预期函数名列表。
    
    expected: [{"name": "initializeUSB", "min_score": 80}, ...]
    returns: {"score": 0-100, "matched": ["name1", ...]}
    
    用 pefile 纯静态扫描（不加载动态库，不中毒、不崩）。
    """
    matched = []
    total = len(expected)
    if total == 0:
        return {"score": 0, "matched": []}

    try:
        import pefile  # type: ignore
    except ImportError:
        # pefile 不安装时返回 0 分
        return {"score": 0, "matched": []}

    try:
        pe = pefile.PE(dll_path, fast_load=True)
    except Exception:
        return {"score": 0, "matched": []}

    if not hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        pe.close()
        return {"score": 0, "matched": []}

    try:
        export_names = set()
        for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
            if exp.name:
                try:
                    export_names.add(exp.name.decode("utf-8", errors="replace"))
                except Exception:
                    pass
    except Exception:
        pe.close()
        return {"score": 0, "matched": []}
    pe.close()

    # 模糊匹配：不区分大小写、允许前后缀
    for exp in expected:
        target = exp["name"].lower()
        min_score = exp.get("min_score", 50)
        for en in export_names:
            en_lower = en.lower()
            if en_lower == target:
                matched.append(exp["name"])
                break
            # 允许动态库加了前后缀，如 _initializeUSB@16
            if target in en_lower or en_lower in target:
                matched.append(f"{exp['name']}(≈{en})")
                break

    score = int(len(matched) / total * 100) if total > 0 else 0
    return {"score": score, "matched": matched}


# ─────────────────────────────────────────────────────────────────────────────
#  内容指纹识别 — 配置文件字段
# ─────────────────────────────────────────────────────────────────────────────

def _fingerprint_ini(ini_path: str, expected: list) -> dict:
    """扫描 INI 文件，匹配预期字段。
    
    expected: [{"section": "System", "key": "PCID", "min_score": 100}, ...]
    不依赖 configparser（可能解析崩溃），直接逐行匹配。
    """
    matched = []
    total = len(expected)
    if total == 0:
        return {"score": 0, "matched": []}

    try:
        with open(ini_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        try:
            with open(ini_path, "r", encoding="gbk", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return {"score": 0, "matched": []}

    current_section = ""
    content = "".join(lines).lower()
    for exp in expected:
        section = exp.get("section", "").lower()
        key = exp.get("key", "").lower()
        # 简单：在全文搜 key= 或 [section]...key=
        if f"{key}=" in content:
            matched.append(f"{section}.{exp['key']}")
        # 也可以尝试节区感知，但整篇范围已经够用

    score = int(len(matched) / total * 100) if total > 0 else 0
    return {"score": score, "matched": matched}


# ─────────────────────────────────────────────────────────────────────────────
#  内容指纹识别 — MDB 表结构
# ─────────────────────────────────────────────────────────────────────────────

def _fingerprint_mdb(mdb_path: str, expected: list) -> dict:
    """扫描 MDB 数据库，匹配预期表名。
    
    expected: [{"name": "RoomInfo", "min_score": 100}, ...]
    使用 access_parser 或 pyodbc，纯只读、不修改。
    """
    matched = set()
    total = len(expected)
    if total == 0:
        return {"score": 0, "matched": []}

    # 尝试 access_parser（纯 Python，无外部依赖）
    try:
        from access_parser import AccessParser
        parser = AccessParser(mdb_path)
        catalog = parser.catalog or {}
        table_names = set()
        for row in catalog:
            name = row.get("Name") or row.get("name") or ""
            if name:
                table_names.add(name.lower())
        if not table_names:
            table_names = set(k.lower() for k in (parser.__dict__.get("_tables_with_data", {}) or {}).keys())
        for exp in expected:
            target = exp["name"].lower()
            if target in table_names:
                matched.add(exp["name"])
        score = int(len(matched) / total * 100) if total > 0 else 0
        return {"score": score, "matched": sorted(matched)}
    except Exception:
        pass

    # 尝试 pyodbc（走数据库驱动）
    try:
        import pyodbc
        conn_str = (
            r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
            f"DBQ={mdb_path};READONLY=1;"
        )
        conn = pyodbc.connect(conn_str, timeout=3)
        cursor = conn.cursor()
        rows = cursor.execute("SELECT NAME FROM MSysObjects WHERE TYPE=1 AND NAME NOT LIKE '~%' AND NAME NOT LIKE 'MSys%'").fetchall()
        table_names = set(str(r[0]).lower() for r in rows if r[0])
        conn.close()
        for exp in expected:
            target = exp["name"].lower()
            if target in table_names:
                matched.add(exp["name"])
        score = int(len(matched) / total * 100) if total > 0 else 0
        return {"score": score, "matched": sorted(matched)}
    except Exception:
        pass

    return {"score": 0, "matched": []}


# ─────────────────────────────────────────────────────────────────────────────
#  本地目录深度指纹扫描
# ─────────────────────────────────────────────────────────────────────────────

def _scan_local_dir(install_dir: str, brand: dict) -> dict | None:
    """对一个本地安装目录做三层内容指纹扫描，返回匹配信息。
    
    扫描：
      1. *.dll → pefile 导出表匹配
      2. *.ini → 字段匹配
      3. *.mdb / *.accdb → 表结构匹配
    
    返回空（不匹配）或字典（匹配结果）。
    """
    fp = brand.get("fingerprints", {})
    if not fp:
        return None  # 该品牌没有定义指纹，跳过

    base = Path(install_dir)

    # 用 os.scandir 递归扫描文件（最多 200 个，每层扫描限 500 条目）
    all_files = []
    try:
        stack = [base]
        visited = 0
        while stack and len(all_files) < 200:
            cur = stack.pop()
            try:
                for entry in os.scandir(str(cur)):
                    visited += 1
                    if visited > 500:
                        break  # 单层超过 500 条目，跳过该目录避免卡死
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            all_files.append(Path(entry.path))
                            if len(all_files) >= 200:
                                break
                    except OSError:
                        continue
            except PermissionError:
                continue
            except OSError:
                continue
    except Exception:
        return None

    # ── 动态库导出表扫描 ──
    dll_fingerprints = fp.get("dll_exports", [])
    best_dll_score = 0
    dll_matched = []
    dll_found_path = ""

    if dll_fingerprints:
        for f in all_files:
            if f.suffix.lower() not in (".dll", ".ocx"):
                continue
            if f.stat().st_size < 10240:
                继续  # 太小的动态库不可能有复杂导出表
            result = _fingerprint_dll(str(f), dll_fingerprints)
            if result["score"] > best_dll_score:
                best_dll_score = result["score"]
                dll_matched = result["matched"]
                dll_found_path = str(f)
            if best_dll_score >= 90:
                break  # 90 分以上可以确认了

    # ── 配置文件字段扫描 ──
    ini_fingerprints = fp.get("ini_fields", [])
    best_ini_score = 0
    ini_matched = []
    ini_found_path = ""

    if ini_fingerprints:
        for f in all_files:
            if f.suffix.lower() not in (".ini", ".cfg", ".conf"):
                continue
            if f.stat().st_size > 1024 * 1024:
                continue
            result = _fingerprint_ini(str(f), ini_fingerprints)
            if result["score"] > best_ini_score:
                best_ini_score = result["score"]
                ini_matched = result["matched"]
                ini_found_path = str(f)
            if best_ini_score >= 80:
                break

    # ── MDB 表结构扫描 ──
    mdb_fingerprints = fp.get("mdb_tables", [])
    best_mdb_score = 0
    mdb_matched = []
    mdb_found_path = ""

    if mdb_fingerprints:
        for f in all_files:
            if f.suffix.lower() not in (".mdb", ".accdb"):
                continue
            if f.stat().st_size < 10240:
                continue
            result = _fingerprint_mdb(str(f), mdb_fingerprints)
            if result["score"] > best_mdb_score:
                best_mdb_score = result["score"]
                mdb_matched = result["matched"]
                mdb_found_path = str(f)
            if best_mdb_score >= 90:
                break

    # 综合评分：三个维度加权
    dll_weight = 0.5
    ini_weight = 0.3
    mdb_weight = 0.2

    total_score = (
        best_dll_score * dll_weight +
        best_ini_score * ini_weight +
        best_mdb_score * mdb_weight
    )

    threshold = brand.get("fingerprint_threshold", 40)
    if total_score < threshold:
        return None

    return {
        "brand_id":      brand["id"],
        "brand_name":    brand["name"],
        "brand_en":      brand.get("name_en", ""),
        "drive":         install_dir,
        "found_files":   sorted(set(filter(None, [dll_found_path, ini_found_path, mdb_found_path]))),
        "key_data":      {"System.ini": {"path": ini_found_path}} if ini_found_path else {},
        "baud_rate":     brand.get("baud_rate", 9600),
        "notes":         brand.get("notes", ""),
        "detected_at":   datetime.datetime.now().isoformat(),
        "fingerprint":   {
            "total_score": round(total_score, 1),
            "dll":  {"score": best_dll_score, "matched": dll_matched, "path": dll_found_path},
            "ini":  {"score": best_ini_score, "matched": ini_matched, "path": ini_found_path},
            "mdb":  {"score": best_mdb_score, "matched": mdb_matched, "path": mdb_found_path},
        },
        "detect_method": "fingerprint",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  学习模式 — 读卡提取参数
# ─────────────────────────────────────────────────────────────────────────────

def _try_learn_from_card(install_dirs: list | None = None) -> dict | None:
    """放一张现有卡到发卡器，读卡提取发卡器标识。
    
    前提：发卡器已插、专用模式可用。
    返回配置字典或空。
    
    学习流程：
      1. 打开发卡器（专用模式）
      2. 放卡 → 读数据
      3. 解析数据中的发卡器标识
      4. 写配置 → 完成
    """
    try:
        from lock_adapters.prousb_v9 import ProUsbV9Adapter
    except ImportError:
        logger.warning("[USB_SCANNER] 无法导入 ProUsbV9Adapter，跳过学习模式")
        return None

    # 复用上层已扫描的安装目录，避免重复扫描
    if install_dirs is None:
        install_dirs = _find_v9_install_dirs()
    if not install_dirs:
        logger.warning("[USB_SCANNER] 学习模式：未找到门锁安装目录")
        return None

    for install_dir in install_dirs:
        try:
            adapter = ProUsbV9Adapter(Path(install_dir))
            if not adapter.initialize(d12_mode=1):
                continue

            result = adapter.read_card_payload()
            if not result or len(result) < 12:
                adapter.close()
                continue

            payload = result.upper()
            if not payload.startswith("C92B20B7"):
                logger.info("[USB_SCANNER] 学习模式：卡不是 V9 格式（magic 不对）")
                adapter.close()
                continue

            dls_co_id = payload[8:12]  # byte[4:6] = 4 hex chars
            logger.info("[USB_SCANNER] 学习模式 ✅ 从卡提取 dlsCoID: %s", dls_co_id)

            adapter.close()
            return {
                "brand_id":      "prousb_cardlock",
                "brand_name":    "proUSB / CardLock（学习模式）",
                "brand_en":      "proUSB CardLock (learned)",
                "drive":         install_dir,
                "found_files":   [],
                "key_data":      {},
                "baud_rate":     9600,
                "notes":         f"从已有卡学习：dlsCoID={dls_co_id}",
                "detected_at":   datetime.datetime.now().isoformat(),
                "learned":       True,
                "learned_dlsCoID": dls_co_id,
                "detect_method": "learn",
            }
        except Exception as e:
            logger.debug("[USB_SCANNER] 学习模式尝试目录 %s 失败: %s", install_dir, e)
            continue

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  U盘文件名匹配（原逻辑）
# ─────────────────────────────────────────────────────────────────────────────

def _scan_drive_for_brand(drive_path: str, brand: dict) -> dict | None:
    """在指定驱动器上扫描品牌特征文件（文件名匹配）。"""
    found_files = []
    drive = Path(drive_path)

    scan_dirs = [drive]
    try:
        for item in drive.iterdir():
            if item.is_dir():
                scan_dirs.append(item)
    except PermissionError:
        pass

    for sig in brand.get("signatures", []):
        for scan_dir in scan_dirs:
            if "*" in sig or "?" in sig:
                try:
                    for child in scan_dir.iterdir():
                        if child.is_file() and fnmatch.fnmatch(child.name.lower(), sig.lower()):
                            found_files.append(str(child))
                except Exception:
                    pass
            else:
                candidate = scan_dir / sig
                if candidate.exists():
                    found_files.append(str(candidate))

    if not found_files:
        return None

    key_data = {}
    for kf in brand.get("key_files", []):
        for scan_dir in scan_dirs:
            candidates = []
            if "*" in kf or "?" in kf:
                try:
                    candidates = [p for p in scan_dir.iterdir() if p.is_file() and fnmatch.fnmatch(p.name.lower(), kf.lower())]
                except Exception:
                    candidates = []
            else:
                candidates = [scan_dir / kf]
            for candidate in candidates:
                if not candidate.exists():
                    continue
                try:
                    with open(candidate, "rb") as f:
                        raw = f.read()
                    key_data[candidate.name] = {
                        "path": str(candidate),
                        "size": len(raw),
                        "hex_preview": raw[:32].hex(),
                        "raw": raw,
                    }
                except Exception as e:
                    key_data[candidate.name] = {"path": str(candidate), "error": str(e)}

    return {
        "brand_id":    brand["id"],
        "brand_name":  brand["name"],
        "brand_en":    brand.get("name_en", ""),
        "drive":       drive_path,
        "found_files": found_files,
        "key_data":    key_data,
        "baud_rate":   brand.get("baud_rate", 9600),
        "notes":       brand.get("notes", ""),
        "detected_at": datetime.datetime.now().isoformat(),
        "detect_method": "usb_filename",
    }


def _load_profiles() -> list:
    """加载品牌特征库"""
    if not _PROFILES_FILE.exists():
        return []
    try:
        with open(_PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("brands", [])
    except Exception as e:
        logger.warning("[USB_SCANNER] 加载特征库失败: %s", e)
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  主扫描器类
# ─────────────────────────────────────────────────────────────────────────────

class UsbLockScanner:
    """门锁系统自动识别与迁移引擎（三层识别）"""

    def __init__(self):
        self.profiles = _load_profiles()
        self._last_scan_results = []

    def scan_and_detect(self, skip_usb: bool = False) -> list:
        """三层扫描：指纹扫描→学习模式（跳过U盘时设为真）。
        
        对于 V9/门锁等不需要 USB 加密狗的品牌，跳过第一层。
        返回检测到的门锁品牌列表（按优先级排序）。
        空列表 = 什么都没找到。
        """
        results = []

        # ── 第一层：U盘文件名匹配（仅非 skip_usb 模式）──
        if not skip_usb:
            drives = _get_usb_drives()

        # ── 第二层：本地硬盘内容指纹扫描 ──
        if not results:
            install_dirs = _find_v9_install_dirs()
            logger.info("[USB_SCANNER] 第二层：扫描 %d 个候选安装目录", len(install_dirs))
            for install_dir in install_dirs:
                for brand in self.profiles:
                    if not brand.get("fingerprints"):
                        continue
                    match = _scan_local_dir(install_dir, brand)
                    if match:
                        logger.info("[USB_SCANNER] ✅ 第二层指纹匹配: %s @ %s (评分 %.1f)",
                                    brand['name'], install_dir, match.get('fingerprint', {}).get('total_score', 0))
                        results.append(match)
                        break

        # ── 第三层：学习模式（读已有卡） ──
        if not results:
            logger.info("[USB_SCANNER] 第三层：尝试从已有卡学习")
            match = _try_learn_from_card(install_dirs=install_dirs)
            if match:
                logger.info("[USB_SCANNER] ✅ 第三层学习模式匹配: %s", match.get('learned_dlsCoID', '?'))
                results.append(match)

        self._last_scan_results = results
        return results

    def diagnostic_snapshot(self) -> dict:
        """未识别时返回现场可发回厂家的线索。"""
        out = {
            "drives": [],
            "known_brands": len(self.profiles),
            "local_dirs_found": _find_v9_install_dirs(),
            "generated_at": datetime.datetime.now().isoformat(),
        }
        for drive in _get_usb_drives():
            files = []
            try:
                for p in Path(drive).iterdir():
                    if p.is_file():
                        files.append({"name": p.name, "size": p.stat().st_size})
                    elif p.is_dir():
                        files.append({"name": p.name + "/", "size": 0})
                    if len(files) >= 80:
                        break
            except Exception as e:
                files.append({"error": str(e)})
            out["drives"].append({"drive": drive, "files": files})
        return out

    def migrate(self, detection_result: dict) -> dict:
        """
        将检测到的门锁配置迁移到本系统。
        支持三种 detect_method: usb_filename / fingerprint / learn
        """
        brand_id   = detection_result.get("brand_id", "unknown")
        brand_name = detection_result.get("brand_name", "未知品牌")
        key_data   = detection_result.get("key_data", {})
        baud_rate  = detection_result.get("baud_rate", 9600)
        drive      = detection_result.get("drive", "")
        method     = detection_result.get("detect_method", "unknown")
        learned_dls = detection_result.get("learned_dlsCoID", "")

        try:
            backup_dir = Path(__file__).parent / "USB_LOCK_PROFILES" / "migrated" / brand_id
            backup_dir.mkdir(parents=True, exist_ok=True)

            migrated_keys = []
            for filename, info in key_data.items():
                if "raw" in info:
                    dest = backup_dir / filename
                    with open(dest, "wb") as f:
                        f.write(info["raw"])
                    migrated_keys.append(filename)
                    logger.info("[USB_SCANNER] 已备份密钥文件: %s", dest)

            # 写入系统配置
            db.set_config("lock_brand", brand_id)
            db.set_config("lock_brand_name", brand_name)
            db.set_config("lock_baud_rate", str(baud_rate))
            db.set_config("lock_key_dir", str(backup_dir))
            db.set_config("lock_migrated_at", datetime.datetime.now().isoformat())
            db.set_config("lock_source_drive", drive)
            db.set_config("lock_detect_method", method)

            # 如果是学习模式，保存提取的 dlsCoID
            if learned_dls:
                db.set_config("lock_learned_dlsCoID", learned_dls)

            brand_to_card_system = {
                "anjubao":       "安居宝",
                "kaidisite":     "凯迪仕",
                "tcl":           "TCL",
                "onity":         "通用串口",
                "vingcard":      "通用串口",
                "dormakaba":     "通用串口",
                "adel":          "通用串口",
                "beian":         "通用串口",
                "dessmann":      "通用串口",
                "salto":         "通用串口",
                "generic_mifare":"通用串口",
            }
            card_brand = brand_to_card_system.get(brand_id, "通用串口")
            db.set_config("card_brand", card_brand)

            try:
                from power_controller_config import sync_power_from_lock_brand
                sync_power_from_lock_brand(brand_id, brand_name)
            except Exception as _pce:
                logger.warning("[USB_SCANNER] 取电配置同步: %s", _pce)

            db.log_action(
                "SYSTEM", "USB_LOCK_MIGRATED",
                f"品牌:{brand_name} 方式:{method} 驱动器:{drive} 密钥文件:{','.join(migrated_keys)}"
                + (f" 学习发卡器标识: {learned_dls}" if learned_dls else "")
            )

            extra = ""
            if method == "fingerprint":
                fp = detection_result.get("fingerprint", {})
                extra = f"\n🔍 内容指纹评分: {fp.get('total_score', '?')}"
            elif method == "learn":
                extra = f"\n🎓 从已有卡学习: dlsCoID={learned_dls}"

            return {
                "ok": True,
                "brand_name": brand_name,
                "brand_id": brand_id,
                "migrated_keys": migrated_keys,
                "backup_dir": str(backup_dir),
                "message": f"✅ {brand_name} 门锁配置已成功迁移！{extra}\n密钥文件已备份到本地，系统已自动配置。",
            }

        except Exception as e:
            return {
                "ok": False,
                "brand_name": brand_name,
                "brand_id": brand_id,
                "migrated_keys": [],
                "message": f"❌ 迁移失败: {e}",
            }

    def get_migrated_config(self) -> dict:
        """获取当前已迁移的门锁配置"""
        return {
            "brand_id":      db.get_config("lock_brand") or "",
            "brand_name":    db.get_config("lock_brand_name") or "",
            "baud_rate":     db.get_config("lock_baud_rate") or "9600",
            "key_dir":       db.get_config("lock_key_dir") or "",
            "migrated_at":   db.get_config("lock_migrated_at") or "",
            "source_drive":  db.get_config("lock_source_drive") or "",
            "detect_method": db.get_config("lock_detect_method") or "",
            "learned_dlsCoID": db.get_config("lock_learned_dlsCoID") or "",
        }

    def list_all_brands(self) -> list:
        """返回所有已知品牌列表（用于UI展示）"""
        return [
            {
                "id":      b["id"],
                "name":    b["name"],
                "name_en": b.get("name_en", ""),
                "region":  b.get("region", []),
                "notes":   b.get("notes", "")
            }
            for b in self.profiles
        ]


# ─────────────────────────────────────────────────────────────────────────────
#  全局单例
# ─────────────────────────────────────────────────────────────────────────────
usb_lock_scanner = UsbLockScanner()
