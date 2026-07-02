"""
DLL 字符串扫描器 — Ghidra 不可用时的降级方案
用 Sysinternals strings.exe 直接从 DLL 抠字符串，正则搜密钥和品牌线索
"""

import os
import re
import sys
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional


HEX_KEY_PATTERNS = [
    ("S50_key_6byte",  r"\b[A-Fa-f0-9]{12}\b"),
    ("S50_key_8byte",  r"\b[A-Fa-f0-9]{16}\b"),
    ("key_12byte",     r"\b[A-Fa-f0-9]{24}\b"),
    ("key_16byte",     r"\b[A-Fa-f0-9]{32}\b"),
    ("key_32byte",     r"\b[A-Fa-f0-9]{64}\b"),
    ("key_64byte",     r"\b[A-Fa-f0-9]{128}\b"),
]

BRAND_KEYWORDS = [
    "Walton", "CardLock", "爱迪尔", "必达", "力维",
    "西容", "雅迪顿", "同创新佳", "宝迅达",
    "proUSB", "V9", "V10", "V11", "RFL",
    "RFLOCK", "DIGILOCK", "ONITY", "SALTO",
]

FILE_PATTERNS = [
    r"\b\w+\.mdb\b", r"\b\w+\.ini\b", r"\b\w+\.dll\b",
    r"\b\w+\.exe\b", r"\b\w+\.dat\b", r"\b\w+\.cfg\b",
]


def _get_toolbox_dir() -> Path:
    import sys
    if getattr(sys, 'frozen', False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent
    return root / "toolbox"


def _find_strings_exe() -> Optional[Path]:
    toolbox = _get_toolbox_dir()
    local = toolbox / "strings.exe"
    if local.exists():
        return local
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(path_dir) / "strings.exe"
        if p.exists():
            return p
    return None


def scan_dll_strings(dll_path: str) -> Dict[str, Any]:
    result = {
        "dll_path": dll_path,
        "total_functions": 0,
        "exports": [],
        "keys": [],
        "xrefs": [],
        "strings_hint": [],
        "file_clues": [],
        "_scanner": "strings_fallback",
    }

    strings_exe = _find_strings_exe()
    if not strings_exe:
        result["_error"] = "strings.exe not found in toolbox or PATH"
        return result

    try:
        proc = subprocess.run(
            [str(strings_exe), "-nobanner", "-utf8", dll_path],
            capture_output=True, text=True, timeout=60,
        )
        all_text = proc.stdout
    except Exception as e:
        result["_error"] = f"strings.exe failed: {str(e)}"
        return result

    seen_keys = set()
    seen_hints = set()
    seen_clues = set()

    for line in all_text.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue

        for key_type, pattern in HEX_KEY_PATTERNS:
            for m in re.finditer(pattern, line_stripped):
                hex_val = m.group(0).upper()
                if hex_val not in seen_keys and hex_val not in ("0" * len(hex_val), "F" * len(hex_val)):
                    seen_keys.add(hex_val)
                    result["keys"].append({
                        "type": key_type,
                        "address": "0x0",
                        "value": hex_val,
                        "context": line_stripped[:80],
                    })

        for brand in BRAND_KEYWORDS:
            if brand.lower() in line_stripped.lower():
                key = (brand, line_stripped[:120])
                if key not in seen_hints:
                    seen_hints.add(key)
                    result["strings_hint"].append({
                        "keyword": brand,
                        "address": "0x0",
                        "context": line_stripped[:120],
                    })

        for fp in FILE_PATTERNS:
            for m in re.finditer(fp, line_stripped, re.IGNORECASE):
                clue = m.group(0)
                if clue not in seen_clues:
                    seen_clues.add(clue)
                    result["file_clues"].append(clue)

    return result
