"""
lock_deploy/dll_probe.py — 通用DLL探针

在酒店现场自动探测未知品牌门锁系统的 DLL：
1. 在安装目录中找所有 .dll / .ocx，优先挑名字带 RFL/Lock/Comm/USB 的
2. 用 pefile 静态枚举导出函数表（不加载 DLL，避免位数冲突）
3. 按关键词匹配合集（init/read/write/guest/erase/buzzer…）
4. 生成候选配置 JSON，可直接嵌入诊断包带回厂家

设计约束：
- 纯静态分析，不试图加载 DLL（32/64 位差异由桥泛化解决）
- pefile 不可用时降级到 ctypes.windll 枚举（但仅 32 位可用）
- 输出的配置格式与 lock_adapters/profile/profiles/ 完全兼容

用法：
    from lock_deploy.dll_probe import probe
    result = probe(r"D:\智能门锁管理系统")
    # result["candidate_profile"] 可以直接写入 profiles/
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 函数名关键词合集（V9 品牌常见导出模式）
# ──────────────────────────────────────────────────────────────────

# 每组: (group_name, priority, [keywords…])
# priority 越高越优先作为该组的"代表函数名"
_FUNCTION_PATTERNS: List[Tuple[str, int, List[str]]] = [
    # init 组 — 初始化/打开连接
    ("init_usb",     100, ["initializeusb", "initusb", "initialusb"]),
    ("init",          90, ["init", "initial", "openusb", "opencomm"]),
    ("open",          80, ["open",   "connect"]),
    # read 组 — 读卡
    ("read_card",    100, ["readcard", "read_card"]),
    ("read",          90, ["read"]),
    # write 组 — 写卡
    ("write_card",   100, ["writecard", "write_card"]),
    ("write",         90, ["write"]),
    # guest 组 — 发客人卡
    ("guest_card",   100, ["guestcard", "guest_card", "guest"]),
    ("issue_card",    90, ["issuecard", "issue_card", "issue"]),
    ("make_card",     80, ["makecard", "make_card", "make"]),
    # erase 组 — 擦卡
    ("erase",        100, ["erase", "carderase", "card_erase"]),
    ("clear",         80, ["clear", "deletecard", "delete"]),
    # close 组 — 关闭/断开
    ("close",        100, ["close", "disconnect"]),
    # buzzer 组 — 蜂鸣器
    ("buzzer",       100, ["buzzer", "beep"]),
    # 系统卡组
    ("master_card",  100, ["mastercard", "master_card", "master"]),
    ("building_card", 90, ["buildingcard", "building_card", "building"]),
    ("floor_card",   100, ["floorcard", "floor_card", "floor"]),
    ("emergency",    100, ["emergencycard", "emergency_card", "emergency"]),
    ("limit_card",   100, ["limitcard", "limit_card", "limits"]),
    # 工具组
    ("get_version",  100, ["getdllversion", "getversion", "dllversion"]),
    ("parse_type",    90, ["getcardtype", "cardtype", "gettype"]),
    ("parse_time",    90, ["getguestetime", "getgueststime", "gettime"]),
]

# 用于 DLL 文件名优先匹配的关键词（按权重排序）
_DLL_PRIORITY_KEYWORDS = [
    "rfl", "lock", "comm", "usb", "hotel", "hotellock",
    "card", "dllmain", "maindll", "icdll",
]


# ──────────────────────────────────────────────────────────────────
# Step 1: 找 DLL
# ──────────────────────────────────────────────────────────────────

def find_dlls(install_dir: str, *, top_n: int = 5) -> List[Dict[str, Any]]:
    """在 install_dir 里找所有 .dll / .ocx，按名称相关性排序。

    优先匹配常见命名模式（*RFL*, *Lock*, *Comm*, *USB*），
    返回最多 top_n 个候选，每个包含 path/name/size/mtime。
    """
    dlls: List[Dict[str, Any]] = []
    base = Path(install_dir)
    if not base.is_dir():
        logger.warning("[dll_probe] install_dir 不存在: %s", install_dir)
        return dlls

    for entry in base.iterdir():
        try:
            if not entry.is_file():
                continue
            ext = entry.suffix.lower()
            if ext not in (".dll", ".ocx"):
                continue
            stat = entry.stat()
            score = _dll_name_score(entry.name)
            dlls.append({
                "path": str(entry.resolve()),
                "name": entry.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "score": score,
            })
        except Exception as e:
            logger.debug("[dll_probe] 扫描 DLL 异常 %s: %s", entry.name, e)

    # 按得分从高到低排
    dlls.sort(key=lambda d: d["score"], reverse=True)
    return dlls[:top_n]


_RUNTIME_DLL_PREFIXES = (
    "msvcr", "msvcp", "vcruntime", "concrt", "ucrtbase",
    "api-ms-", "mfc", "atl",
)

_USB_LAYER_DLL_NAMES = {
    "d12.dll", "d12c.dll", "d12c", "mwic_32.dll",
}


def _dll_name_score(name: str) -> int:
    """根据 DLL 文件名判断相关性分数。"""
    name_lower = name.lower()
    # 排除系统 DLL
    if name_lower in ("kernel32.dll", "user32.dll", "gdi32.dll",
                       "ntdll.dll", "advapi32.dll", "ole32.dll",
                       "oleaut32.dll", "ws2_32.dll", "shell32.dll"):
        return -100
    for prefix in _RUNTIME_DLL_PREFIXES:
        if name_lower.startswith(prefix):
            return -100
    if name_lower in _USB_LAYER_DLL_NAMES:
        return -20
    score = 0
    for kw in _DLL_PRIORITY_KEYWORDS:
        if kw in name_lower:
            score += 50
    return score


# ──────────────────────────────────────────────────────────────────
# Step 2: 枚举导出函数（纯静态）
# ──────────────────────────────────────────────────────────────────

def enumerate_exports(dll_path: str) -> List[Dict[str, Any]]:
    """用 pefile 枚举 DLL 的所有导出函数。

    返回 [{name, ordinal, address}, …] 按 name 排序。
    pefile 不可用时降级到 ctypes（仅 32 位 Python 可用）。
    """
    exports: List[Dict[str, Any]] = []

    # 尝试用 pefile（64 位也可用，纯静态）
    if _try_pefile_exports(dll_path, exports):
        return exports

    # 降级：ctypes.windll 枚举（仅 32 位）
    if _try_ctypes_exports(dll_path, exports):
        return exports

    return exports


def _try_pefile_exports(dll_path: str, out: List[Dict[str, Any]]) -> bool:
    """用 pefile 读导出表，成功返回 True。"""
    try:
        import pefile  # type: ignore
    except ImportError:
        return False

    try:
        pe = pefile.PE(dll_path, fast_load=True)
        if not hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
            pe.close()
            return False

        for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
            name = getattr(exp, "name", None)
            if name is None:
                continue
            try:
                n = name.decode("utf-8", errors="replace")
            except Exception:
                n = str(name)
            out.append({
                "name": n,
                "ordinal": exp.ordinal or 0,
                "address": exp.address or 0,
            })
        out.sort(key=lambda x: x["name"].lower())
        pe.close()
        return True
    except Exception as e:
        logger.debug("[dll_probe] pefile 解析 %s 失败: %s", dll_path, e)
        return False


def _try_ctypes_exports(dll_path: str, out: List[Dict[str, Any]]) -> bool:
    """用 ctypes 的 windll.LoadLibrary 加载后枚举。

    仅在 **32 位** Python 中可用。64 位 Python 尝试加载 32 位 DLL 会直接失败。
    """
    import sys
    if sys.maxsize > 2 ** 32:
        # 64 位 Python —— ctypes 无法加载 32 位 DLL
        return False

    try:
        import ctypes
        dll = ctypes.WinDLL(dll_path)
    except Exception as e:
        logger.debug("[dll_probe] ctypes 加载 %s 失败: %s", dll_path, e)
        return False

    try:
        from ctypes import windll  # noqa
        # ctypes 没有直接枚举导出的 API，我们只能试已知名字
        # 这种方法有限，只用做 pefile 不可用时的最后降级
        return False  # 暂时不做，以免误导
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────
# Step 3: 匹配合集
# ──────────────────────────────────────────────────────────────────

def match_export_patterns(exports: List[Dict[str, Any]]) -> Dict[str, str]:
    """把枚举到的导出函数名匹配合集关键词。

    返回 {group_name: matched_function_name, ...}。
    匹配规则：
    - 不区分大小写
    - 子串匹配（只要 export_name 含有 keyword 就算命中）
    - 每组取优先级最高的匹配
    """
    matched: Dict[str, str] = {}
    used_names: set = set()

    # 建立 name → export_name 的映射
    name_map: Dict[str, str] = {}
    for exp in exports:
        n = exp["name"].lower()
        if n not in name_map:
            name_map[n] = exp["name"]

    for group, priority, keywords in _FUNCTION_PATTERNS:
        best: Optional[str] = None
        best_priority = -1
        for kw in keywords:
            # 精确匹配优先
            if kw in name_map:
                candidate = name_map[kw]
                if priority > best_priority:
                    best = candidate
                    best_priority = priority
                    continue
            # 子串匹配
            for export_lower, export_original in name_map.items():
                if kw in export_lower:
                    if priority > best_priority:
                        best = export_original
                        best_priority = priority

        if best and best.lower() not in used_names:
            matched[group] = best
            used_names.add(best.lower())

    return matched


# 向后兼容旧名
match_v9_patterns = match_export_patterns


def match_by_dll_name(dll_name: str) -> Dict[str, str]:
    """如果 DLL 名本身暗示品牌，返回硬编码的匹配提示。

    对已经知道的品牌，直接返回已知的导出函数名映射（不完全依赖导出表）。
    例如 V9RFL.dll → initializeUSB, ReadCard, GuestCard 等。
    这让我们能在导出表被混淆时也提供有用的默认值。
    """
    known: Dict[str, Dict[str, str]] = {
        "v9rfl.dll": {
            "init_usb":   "initializeUSB",
            "read_card":  "ReadCard",
            "write":      "WriteCard",
            "guest_card": "GuestCard",
            "erase":      "CardErase",
            "close":      "CloseUSB",
            "buzzer":     "Buzzer",
            "get_version":"GetDLLVersion",
        },
        "lock9200.dll": {
            "init":       "init",
            "read_card":  "readcard",
            "guest_card": "guestcard",
        },
        "lock3200.dll": {
            "init":       "init",
            "read_card":  "readcard",
            "guest_card": "guestcard",
        },
        "maindll.dll": {
            "init":       "init",
            "read_card":  "readcard",
            "guest_card": "guestcard",
        },
        "bteib232.dll": {
            "init":       "init",
            "read_card":  "readcard",
        },
        "levelLock.dll": {
            "init":       "init",
            "read_card":  "readcard",
        },
        "ydd_jk2008.dll": {
            "init":       "init",
            "read_card":  "readcard",
        },
        "icdll.dll": {
            "init":       "init",
            "read_card":  "readcard",
        },
        "hotellock.dll": {
            "init":       "init",
            "read_card":  "readcard",
            "guest_card": "guestcard",
        },
        "prorfl.dll": {
            "init_usb":   "init",
            "read_card":  "readcard",
        },
        "prorflv10.dll": {
            "init_usb":   "init",
            "read_card":  "readcard",
        },
    }
    key = dll_name.lower()
    for known_name, mapping in known.items():
        if known_name in key:
            return mapping
    return {}


# ──────────────────────────────────────────────────────────────────
# Step 4: 生成候选配置
# ──────────────────────────────────────────────────────────────────

def generate_candidate_profile(
    install_dir: str,
    dll_info: Dict[str, Any],
    exports: List[Dict[str, Any]],
    matched: Dict[str, str],
    hardcoded_match: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """根据探测结果生成候选配置 JSON。

    格式与 lock_adapters/profile/profiles/*.json 完全兼容，
    但带 `confidence` 标记和 `probe_meta` 说明。
    """
    dll_name = dll_info["name"]
    dll_path_rel = dll_info["name"]

    # 从扫描器品牌签名推断品牌名
    brand_guess = _guess_brand(install_dir, dll_name)
    adapter_id = f"auto_{Path(dll_name).stem.lower()[:20]}"

    # 合并 matched 和 hardcoded_match，matched 优先
    merged_match: Dict[str, str] = dict(hardcoded_match or {})
    for group, fn_name in matched.items():
        if group in merged_match:
            # 如果导出表匹配到了更具体的函数名，覆盖 hardcoded
            if not merged_match[group].startswith("auto_"):
                merged_match[group] = fn_name
        else:
            merged_match[group] = fn_name

    # 计算置信度
    confidence = _compute_confidence(merged_match)

    # 构造 dll 配置段
    dll_config: Dict[str, Any] = {
        "path": dll_path_rel,
    }
    if "init_usb" in merged_match:
        dll_config["init"] = merged_match["init_usb"]
    elif "init" in merged_match:
        dll_config["init"] = merged_match["init"]
    elif "open" in merged_match:
        dll_config["init"] = merged_match["open"]

    if "init_usb" in merged_match and "init" in merged_match:
        pass  # init_usb 已优先

    if "read_card" in merged_match:
        dll_config["read"] = merged_match["read_card"]
    elif "read" in merged_match:
        dll_config["read"] = merged_match["read"]

    if "write_card" in merged_match:
        dll_config["write"] = merged_match["write_card"]
    elif "write" in merged_match:
        dll_config["write"] = merged_match["write"]

    if "guest_card" in merged_match:
        dll_config["guest"] = merged_match["guest_card"]
    elif "issue_card" in merged_match:
        dll_config["guest"] = merged_match["issue_card"]

    if "erase" in merged_match:
        dll_config["erase"] = merged_match["erase"]

    if "buzzer" in merged_match:
        dll_config["buzzer"] = merged_match["buzzer"]

    if "close" in merged_match:
        dll_config["close"] = merged_match["close"]

    profile: Dict[str, Any] = {
        "brand": brand_guess,
        "adapter_id": adapter_id,
        "description": f"自动探测: {brand_guess} — Solid 自动探针生成",
        "detect": {
            "files": [dll_name] + _extra_detect_files(install_dir, dll_name),
            "exports": {k: v for k, v in merged_match.items()},
        },
        "dll": dll_config,
        "payload": {
            "magic": "C92B20B7",
            "size": 16,
        },
        "confidence": round(confidence, 2),
        "supported": False,
        "probe_meta": {
            "probe_tool": "dll_probe.py v1",
            "dll_size": dll_info.get("size", 0),
            "total_exports": len(exports),
            "matched_groups": list(merged_match.keys()),
            "install_dir": install_dir,
        },
    }

    return profile


def _guess_brand(install_dir: str, dll_name: str) -> str:
    """尝试从安装目录和 DLL 名推断品牌名称。"""
    # 先看扫描器的品牌签名表
    try:
        from .scanner import BRAND_SIGNATURES
        install_lower = install_dir.lower()
        for sig in BRAND_SIGNATURES:
            if all(r.lower() in install_lower for r in sig["required"]):
                return sig["brand"]
    except Exception:
        pass

    # 按 DLL 文件名匹配已知品牌
    dll_lower = dll_name.lower()
    brand_map = {
        "v9rfl":       "疑似 proUSB V9",
        "prorfl":      "疑似 proUSB Pro",
        "prorflv10":   "疑似 proUSB V10/V11",
        "lock9200":    "疑似 爱迪尔 Lock9200",
        "lock3200":    "疑似 爱迪尔 Lock3200",
        "maindll":     "疑似 爱迪尔 通用",
        "bteib232":    "疑似 必达 IB",
        "bededib32":   "疑似 必达 IB",
        "levellock":   "疑似 力维 LevelLock",
        "syronwr2007": "疑似 西容 SYRON",
        "ydd_jk2008":  "疑似 雅迪顿",
        "icdll":       "疑似 同创新佳",
        "hotellock":   "疑似 宝迅达",
    }
    for key, brand in brand_map.items():
        if key in dll_lower:
            return brand

    # 完全未知
    stem = Path(dll_name).stem
    safe = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]", "", stem)[:20]
    return f"未知品牌_{safe}" if safe else "未知品牌_unknown"


def _extra_detect_files(install_dir: str, dll_name: str) -> List[str]:
    """返回额外的识别特征文件名（System.ini, *.mdb 等）。"""
    extras: List[str] = []
    try:
        for entry in Path(install_dir).iterdir():
            if not entry.is_file():
                continue
            name = entry.name.lower()
            if name in ("system.ini", "cardlock.mdb"):
                extras.append(entry.name)
    except Exception:
        pass
    return extras


def _compute_confidence(matched: Dict[str, str]) -> float:
    """根据匹配到的功能组计算置信度 (0.0 ~ 1.0)。"""
    essential = {"init", "init_usb", "read_card", "read", "guest_card", "guest"}
    found_essential = essential & set(matched.keys())
    score = 0.0

    if "init_usb" in matched or "init" in matched or "open" in matched:
        score += 0.35  # 有初始化 = 能通信
    if "read_card" in matched or "read" in matched:
        score += 0.25  # 能读卡
    if "guest_card" in matched or "issue_card" in matched or "write" in matched or "write_card" in matched:
        val = 0.25 if "guest_card" in matched else 0.15
        score += val  # 能发卡或写卡
    if "close" in matched:
        score += 0.05
    if "buzzer" in matched:
        score += 0.05
    if "erase" in matched:
        score += 0.05  # 加擦卡检测

    return min(max(score, 0.05), 1.0)


# ──────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────

def format_exports_report(exports: List[Dict[str, Any]]) -> str:
    """把导出函数列表格式化为可读文本（用于诊断包）。"""
    lines = [f"共 {len(exports)} 个导出函数：", ""]
    for exp in exports:
        lines.append(f"  [{exp['ordinal']}] {exp['name']}")
    return "\n".join(lines)


def format_match_report(matched: Dict[str, str], hardcoded: Optional[Dict[str, str]] = None) -> str:
    """把匹配结果格式化为可读文本（用于诊断包）。"""
    lines = ["匹配合集结果：", ""]
    all_groups: Dict[str, str] = {}
    if hardcoded:
        for k, v in hardcoded.items():
            all_groups[f"{k} (硬编码)"] = v
    for k, v in matched.items():
        all_groups[f"{k} (导出表)"] = v
    for group, fn in sorted(all_groups.items()):
        lines.append(f"  {group:35s} → {fn}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────────

def probe(install_dir: str) -> Dict[str, Any]:
    """在酒店现场自动探测未知品牌门锁系统。

    执行流程：
    1. 在 install_dir 里找候选 DLL
    2. 对每个候选 DLL 枚举导出函数
    3. 按关键词匹配合集
    4. 尝试 DLL 名硬编码匹配
    5. 生成候选配置
    6. 返回完整探测结果

    Returns:
        {
            "detected": bool,           # 是否找到至少一个候选 DLL
            "brand_guess": str,         # 推断的品牌名
            "dll_path": str,            # 最佳候选 DLL 路径
            "all_dlls": [...],          # 所有候选 DLL 的路径
            "exports": [...],           # 导出函数列表
            "matched_functions": {...}, # 匹配合集结果
            "hardcoded_fallback": {...},# 按 DLL 名硬编码的匹配
            "can_issue": bool,          # 是否能发卡（找到 guest/write 函数）
            "confidence": float,        # 置信度 0.0~1.0
            "candidate_profile": {...}, # 可直接写入 profiles/ 的 JSON
        }
    """
    result: Dict[str, Any] = {
        "detected": False,
        "brand_guess": "",
        "dll_path": "",
        "all_dlls": [],
        "exports": [],
        "matched_functions": {},
        "hardcoded_fallback": {},
        "can_issue": False,
        "confidence": 0.0,
        "candidate_profile": None,
    }

    # Step 1: 找 DLL
    dlls = find_dlls(install_dir)
    if not dlls:
        logger.info("[dll_probe] %s 中没有找到候选 DLL", install_dir)
        return result

    result["all_dlls"] = [d["path"] for d in dlls]
    best_dll = dlls[0]

    result["dll_path"] = best_dll["path"]
    dll_name = best_dll["name"]

    # Step 2: 枚举导出
    exports = enumerate_exports(best_dll["path"])
    result["exports"] = exports
    logger.info(
        "[dll_probe] %s: 导出函数 %d 个", dll_name, len(exports)
    )

    # 如果导出一个都没读到（pefile 失败 / DLL 太特殊），
    # 也继续生成配置（含硬编码降级）
    if exports:
        # Step 3: 匹配合集
        matched = match_export_patterns(exports)
        result["matched_functions"] = matched
    else:
        matched = {}

    # Step 4: 按 DLL 名硬编码匹配
    hardcoded = match_by_dll_name(dll_name)
    result["hardcoded_fallback"] = hardcoded

    # Step 5: 生成候选配置
    profile = generate_candidate_profile(
        install_dir, best_dll, exports,
        matched, hardcoded_match=hardcoded,
    )
    result["candidate_profile"] = profile
    result["brand_guess"] = profile["brand"]
    result["confidence"] = profile["confidence"]

    # 判断发卡能力
    dll_cfg = profile.get("dll", {})
    result["can_issue"] = bool(
        dll_cfg.get("guest") or dll_cfg.get("write")
    )

    result["detected"] = True
    return result


def probe_single_dll(install_dir: str, dll_info: Dict[str, Any]) -> Dict[str, Any]:
    """对单个 DLL 做静态探测，返回与 probe() 相同结构的子结果。"""
    dll_name = dll_info["name"]
    dll_path = dll_info["path"]
    exports = enumerate_exports(dll_path)
    matched = match_export_patterns(exports) if exports else {}
    hardcoded = match_by_dll_name(dll_name)
    profile = generate_candidate_profile(
        install_dir, dll_info, exports, matched, hardcoded_match=hardcoded,
    )
    dll_cfg = profile.get("dll", {})
    return {
        "dll_path": dll_path,
        "dll_name": dll_name,
        "score": dll_info.get("score", 0),
        "exports": exports,
        "matched_functions": matched,
        "hardcoded_fallback": hardcoded,
        "candidate_profile": profile,
        "brand_guess": profile.get("brand", ""),
        "confidence": profile.get("confidence", 0.0),
        "can_issue": bool(dll_cfg.get("guest") or dll_cfg.get("write")),
    }


def probe_candidates(install_dir: str, *, top_n: int = 3) -> List[Dict[str, Any]]:
    """对得分最高的 N 个 DLL 分别静态分析，按 confidence 排序返回。"""
    dlls = find_dlls(install_dir, top_n=top_n)
    results: List[Dict[str, Any]] = []
    for dll_info in dlls:
        if dll_info.get("score", 0) < 0:
            continue
        try:
            results.append(probe_single_dll(install_dir, dll_info))
        except Exception as e:
            logger.warning("[dll_probe] 分析 %s 失败: %s", dll_info.get("name"), e)
    results.sort(key=lambda r: float(r.get("confidence") or 0), reverse=True)
    filtered: List[Dict[str, Any]] = []
    for r in results:
        matched = r.get("matched_functions") or {}
        has_init = any(k in matched for k in ("init_usb", "init", "initialize"))
        has_read = any(k in matched for k in ("read_card", "read"))
        if has_init and has_read:
            filtered.append(r)
    return filtered if filtered else results


# ──────────────────────────────────────────────────────────────────
# 独立使用
# ──────────────────────────────────────────────────────────────────

def main():
    """命令行入口：python dll_probe.py <install_dir>

    输出探测结果的 JSON 到 stdout。
    """
    import json
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s [%(name)s] %(message)s",
    )

    if len(sys.argv) < 2:
        print("用法: python dll_probe.py <install_dir>")
        print("示例: python dll_probe.py \"D:\\智能门锁管理系统\"")
        sys.exit(1)

    install_dir = sys.argv[1]
    result = probe(install_dir)

    # 打印摘要
    if result["detected"]:
        print(f"\n✅ 探测成功: {result['brand_guess']}")
        print(f"   DLL: {result['dll_path']}")
        print(f"   导出函数: {len(result['exports'])} 个")
        print(f"   匹配合集: {len(result['matched_functions'])} 组")
        print(f"   置信度: {result['confidence']:.0%}")
        print(f"   能否发卡: {'能 ✨' if result['can_issue'] else '否'}")
    else:
        print(f"\n❌ 未找到候选 DLL")

    if result.get("candidate_profile"):
        print("\n--- candidate_profile ---")
        print(json.dumps(result["candidate_profile"], ensure_ascii=False, indent=2))
    else:
        print("\n⚠ 未生成候选配置")


if __name__ == "__main__":
    main()
