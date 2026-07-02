"""
bridgecore/clue_hunter.py — 线索猎人

从安装目录中自动发现门锁品牌线索，给 identity_engine 喂料。
不依赖任何品牌特定代码，纯启发式扫描。

线索来源（按权重排序）：
1. DLL 文件名 + 导出函数名 → 品牌指纹匹配
2. System.ini 字段（dlsCoID/HotelID/PCID/SN/LD/LDO）
3. CardLock.mdb/Access 数据库表结构
4. 注册表键
5. 原厂 EXE 文件版本信息
6. 安装目录名/路径特征
7. 卡数据样本的 magic 头匹配

输出：ClueReport — 包含品牌候选列表、置信度、证据链
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# 品牌指纹库
# ──────────────────────────────────────────────────────────────────

_DLL_BRAND_MAP: Dict[str, Dict[str, str]] = {
    "v9rfl.dll":      {"brand": "proUSB V9",    "channel": "dll"},
    "prorfl.dll":     {"brand": "proUSB Pro",   "channel": "dll"},
    "prorflv10.dll":  {"brand": "proUSB V10",   "channel": "dll"},
    "lock9200.dll":   {"brand": "爱迪尔 9200",   "channel": "dll"},
    "lock3200.dll":   {"brand": "爱迪尔 3200",   "channel": "dll"},
    "locksaas.dll":   {"brand": "爱迪尔 SAAS",   "channel": "dll"},
    "locklm.dll":     {"brand": "爱迪尔 LockLM", "channel": "dll"},
    "maindll.dll":    {"brand": "爱迪尔 通用",   "channel": "dll"},
    "bidaicard.dll":  {"brand": "必达 IB",      "channel": "dll"},
    "bidainterface.dll":{"brand": "必达 IB",   "channel": "dll"},
    "hotellock.dll":  {"brand": "宝迅达",        "channel": "serial"},
    "icdll.dll":      {"brand": "同创新佳",      "channel": "serial"},
    "tc_doorlock.dll":{"brand": "同创新佳",      "channel": "serial"},
    "ydd_jk2008.dll": {"brand": "雅迪顿",        "channel": "serial"},
    "yadidun.dll":    {"brand": "雅迪顿",        "channel": "serial"},
    "rfulock.dll":    {"brand": "力维 RFU",      "channel": "serial"},
    "xr_carddll.dll": {"brand": "西容",          "channel": "serial"},
    "xr_client.dll":  {"brand": "西容",          "channel": "serial"},
    "lockmanage.dll": {"brand": "力维 LevelLock","channel": "dll"},
    "locksvr.dll":    {"brand": "力维 LevelLock","channel": "dll"},
    "syron.dll":      {"brand": "西容 Syron",    "channel": "dll"},
    "d12.dll":        {"brand": "proUSB 系列",   "channel": "dll", "role": "usb_layer"},
    "d12c.dll":       {"brand": "proUSB 系列",   "channel": "dll", "role": "usb_layer"},
    "mwic_32.dll":    {"brand": "proUSB 系列",   "channel": "dll", "role": "security_chip"},
}

# DLL 导出函数特征 → 品牌推断
_EXPORT_SIGNATURES: Dict[str, Dict[str, Any]] = {
    "initializeUSB,ReadCard,WriteCard,GuestCard,CardErase,Buzzer,CloseUSB,GetDLLVersion": {
        "brand": "proUSB V9 公版", "confidence": 0.95},
    "initializeUSB,ReadCard,WriteCard,GuestCard,CardErase,Buzzer,CloseUSB": {
        "brand": "proUSB V9 变体", "confidence": 0.90},
    "init,readcard,guestcard": {
        "brand": "爱迪尔 通用", "confidence": 0.70},
    "init,readcard,writecard,guestcard": {
        "brand": "爱迪尔 通用", "confidence": 0.85},
}

_EXE_BRAND_MAP: Dict[str, str] = {
    "cardlock.exe":   "proUSB V9 CardLock",
    "cardserver.exe": "proUSB 后台服务",
    "cardsvr.exe":    "proUSB 后台服务",
    "locker.exe":     "力维系列",
    "lockmanager.exe":"力维 LevelLock",
    "hotellock.exe":  "宝迅达",
    "prousb.exe":     "proUSB 工具",
    "iccard.exe":     "同创新佳",
    "doorlock.exe":   "通用门锁",
}

_MAGIC_FINGERPRINTS: Dict[str, Dict[str, str]] = {
    "C92B20B7": {"brand": "proUSB V9 公版", "payload_size": "16"},
    "AABBCCDD": {"brand": "爱迪尔 3200",    "payload_size": "16"},
    "01020304": {"brand": "爱迪尔 9200",    "payload_size": "16"},
}


@dataclass
class ClueItem:
    """单条线索。"""
    source: str = ""
    brand_hint: str = ""
    confidence: float = 0.0
    evidence: str = ""
    channel: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClueReport:
    """线索猎人完整报告。"""
    install_dir: str = ""
    clues: List[ClueItem] = field(default_factory=list)
    best_brand: str = ""
    best_channel: str = ""
    best_confidence: float = 0.0
    all_dlls: List[str] = field(default_factory=list)
    all_exes: List[str] = field(default_factory=list)
    ini_fields: Dict[str, str] = field(default_factory=dict)
    registry_keys: List[str] = field(default_factory=list)
    magic_samples: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    total_clues: int = 0
    scan_duration_ms: float = 0.0

    @property
    def has_viable_clue(self) -> bool:
        return self.best_confidence > 0.0

    @property
    def summary(self) -> str:
        if not self.has_viable_clue:
            return "未发现可识别品牌线索"
        return f"{self.best_brand} (置信度 {self.best_confidence:.0%}, 通道 {self.best_channel})"


class ClueHunter:
    """扫描安装目录，发现门锁品牌线索。"""

    def __init__(self, install_dir: str):
        self._root = Path(install_dir)
        self._clues: List[ClueItem] = []
        self._all_dlls: List[str] = []
        self._all_exes: List[str] = []
        self._ini_fields: Dict[str, str] = {}
        self._registry_keys: List[str] = []
        self._magic_samples: List[str] = []

    def hunt(self) -> ClueReport:
        import time
        t0 = time.monotonic()
        self._clues = []
        if not self._root.is_dir():
            r = ClueReport(install_dir=str(self._root))
            r.blockers.append(f"目录不存在: {self._root}")
            return r

        self._hunt_dlls()
        self._hunt_exes()
        self._hunt_system_ini()
        self._hunt_directory_name()
        self._hunt_registry()
        self._hunt_magic_from_samples()

        best = self._compute_best()
        return ClueReport(
            install_dir=str(self._root.resolve()),
            clues=self._clues,
            best_brand=best.get("brand", ""),
            best_channel=best.get("channel", ""),
            best_confidence=best.get("confidence", 0.0),
            all_dlls=sorted(set(self._all_dlls)),
            all_exes=sorted(set(self._all_exes)),
            ini_fields=dict(self._ini_fields),
            registry_keys=sorted(set(self._registry_keys)),
            magic_samples=list(self._magic_samples),
            total_clues=len(self._clues),
            scan_duration_ms=round((time.monotonic() - t0) * 1000, 1),
        )

    def _hunt_dlls(self) -> None:
        for entry in self._root.rglob("*.dll"):
            try:
                if not entry.is_file():
                    continue
            except Exception:
                continue
            nl = entry.name.lower()
            self._all_dlls.append(entry.name)
            if nl in _DLL_BRAND_MAP:
                info = _DLL_BRAND_MAP[nl]
                role_note = f" ({info['role']})" if 'role' in info else ""
                self._clues.append(ClueItem(
                    source="dll_name", brand_hint=info['brand'],
                    confidence=0.80 if 'role' not in info else 0.50,
                    evidence=f"DLL 文件名匹配: {entry.name}{role_note}",
                    channel=info.get('channel', 'dll'),
                    extra={"dll_name": entry.name, "role": info.get('role', '')},
                ))
        for entry in self._root.rglob("*.ocx"):
            try:
                if entry.is_file():
                    self._all_dlls.append(entry.name)
            except Exception:
                continue
        self._hunt_dll_exports()

    def _hunt_dll_exports(self) -> None:
        main_dlls = [d for d in self._all_dlls
                     if d.lower() not in ('d12.dll', 'd12c.dll', 'mwic_32.dll')]
        if not main_dlls:
            main_dlls = self._all_dlls
        for dll_name in main_dlls[:3]:
            dll_path = self._root / dll_name
            exports = self._safe_enum_exports(str(dll_path))
            if not exports:
                continue
            export_names = sorted(set(e.get('name', '') for e in exports if e.get('name')))
            for sig, info in _EXPORT_SIGNATURES.items():
                sig_set = set(s.lower() for s in sig.split(','))
                exp_set = set(n.lower() for n in export_names)
                overlap = sig_set & exp_set
                if len(overlap) >= 4:
                    match_ratio = len(overlap) / max(len(sig_set), 1)
                    conf = min(info['confidence'] * match_ratio, 1.0)
                    self._clues.append(ClueItem(
                        source="dll_exports", brand_hint=info['brand'],
                        confidence=conf,
                        evidence=f"{dll_name} 导出匹配 {len(overlap)}/{len(sig_set)}",
                        channel="dll",
                        extra={"dll_name": dll_name, "matched": sorted(overlap),
                               "total_exports": len(export_names)},
                    ))

    def _hunt_exes(self) -> None:
        for entry in self._root.rglob("*.exe"):
            try:
                if not entry.is_file():
                    continue
            except Exception:
                continue
            nl = entry.name.lower()
            self._all_exes.append(entry.name)
            if nl in _EXE_BRAND_MAP:
                self._clues.append(ClueItem(
                    source="exe_name", brand_hint=_EXE_BRAND_MAP[nl],
                    confidence=0.65,
                    evidence=f"EXE 文件名匹配: {entry.name}",
                    extra={"exe_name": entry.name, "size": entry.stat().st_size},
                ))

    def _hunt_system_ini(self) -> None:
        ini_path = None
        for candidate in list(self._root.rglob("System.ini")) + list(self._root.rglob("system.ini")):
            if candidate.is_file():
                ini_path = candidate
                break
        if ini_path is None:
            return
        try:
            import configparser
            cp = configparser.ConfigParser()
            cp.read(str(ini_path), encoding='gbk')
        except Exception:
            try:
                cp.read(str(ini_path), encoding='utf-8')
            except Exception:
                return

        pro_usb_hits = 0
        pro_fields = ['dlsCoID', 'PCID', 'SN', 'LD', 'LDO', 'HotelID']
        for section in cp.sections():
            for key, value in cp.items(section):
                self._ini_fields[f"{section}.{key}"] = value
                if key in pro_fields:
                    pro_usb_hits += 1
                    self._clues.append(ClueItem(
                        source="system_ini", brand_hint="proUSB 体系",
                        confidence=0.55,
                        evidence=f"System.ini [{section}] {key}={value[:32]}",
                        extra={"field": key, "section": section},
                    ))
        if pro_usb_hits >= 3:
            self._clues.append(ClueItem(
                source="system_ini", brand_hint="proUSB 体系（综合）",
                confidence=0.75,
                evidence=f"System.ini 含 {pro_usb_hits} 个 proUSB 特征字段",
            ))

    def _hunt_directory_name(self) -> None:
        dn = self._root.name.lower()
        dir_clues = {
            "智能门锁": ("proUSB V9 公版", 0.30),
            "cardlock": ("proUSB V9", 0.40),
            "门锁": ("通用门锁系统", 0.15),
        }
        for kw, (brand, conf) in dir_clues.items():
            if kw in dn:
                self._clues.append(ClueItem(
                    source="path", brand_hint=brand, confidence=conf,
                    evidence=f"安装目录名含 '{kw}': {self._root.name}",
                ))

    def _hunt_registry(self) -> None:
        if os.name != 'nt':
            return
        known_keys = [
            r"Software\CardLock", r"Software\proUSB", r"Software\Walton",
            r"Software\智能门锁", r"Software\酒店门锁", r"Software\CardServer",
        ]
        try:
            import winreg
            for hive_name, hive in [("HKLM", winreg.HKEY_LOCAL_MACHINE),
                                      ("HKCU", winreg.HKEY_CURRENT_USER)]:
                for key_path in known_keys:
                    try:
                        key = winreg.OpenKey(hive, key_path)
                        self._registry_keys.append(f"{hive_name}\\{key_path}")
                        winreg.CloseKey(key)
                    except FileNotFoundError:
                        continue
                    except Exception:
                        continue
        except ImportError:
            pass
        if self._registry_keys:
            self._clues.append(ClueItem(
                source="registry", brand_hint="已注册的门锁软件",
                confidence=0.35,
                evidence=f"发现 {len(self._registry_keys)} 个注册表键",
            ))

    def _hunt_magic_from_samples(self) -> None:
        samples_dir = self._root / "samples"
        if not samples_dir.is_dir():
            return
        for entry in samples_dir.iterdir():
            if not entry.is_file():
                continue
            try:
                with open(entry, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        line = line.strip().upper()
                        if re.match(r'^[0-9A-F]{16,}$', line):
                            magic = line[:8]
                            self._magic_samples.append(magic)
                            if magic in _MAGIC_FINGERPRINTS:
                                info = _MAGIC_FINGERPRINTS[magic]
                                self._clues.append(ClueItem(
                                    source="magic", brand_hint=info['brand'],
                                    confidence=0.90,
                                    evidence=f"卡数据 magic 头匹配: {magic}",
                                    extra={"magic": magic, "payload_size": info['payload_size']},
                                ))
            except Exception:
                continue

    def _safe_enum_exports(self, dll_path: str) -> List[Dict[str, Any]]:
        try:
            import pefile
            pe = pefile.PE(dll_path, fast_load=True)
            if not hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
                pe.close()
                return []
            exports = []
            for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
                name = getattr(exp, "name", None)
                if name is None:
                    continue
                try:
                    n = name.decode("utf-8", errors="replace")
                except Exception:
                    n = str(name)
                exports.append({"name": n, "ordinal": exp.ordinal or 0})
            pe.close()
            return exports
        except ImportError:
            pass
        except Exception as e:
            logger.debug("[ClueHunter] pefile: %s", e)
        return []

    def _compute_best(self) -> Dict[str, Any]:
        if not self._clues:
            return {"brand": "", "channel": "", "confidence": 0.0}
        brand_scores: Dict[str, float] = {}
        brand_channels: Dict[str, str] = {}
        weights = {"dll_name": 1.0, "dll_exports": 1.2, "magic": 1.5,
                   "exe_name": 0.8, "system_ini": 0.7, "path": 0.3, "registry": 0.3}
        for clue in self._clues:
            w = weights.get(clue.source, 0.5)
            brand_scores[clue.brand_hint] = brand_scores.get(clue.brand_hint, 0.0) + clue.confidence * w
            if clue.channel:
                brand_channels[clue.brand_hint] = clue.channel
        if not brand_scores:
            return {"brand": "", "channel": "", "confidence": 0.0}
        best_brand = max(brand_scores, key=brand_scores.get)
        return {
            "brand": best_brand,
            "channel": brand_channels.get(best_brand, "dll"),
            "confidence": round(min(brand_scores[best_brand] / 3.0, 1.0), 2),
        }


def hunt(install_dir: str) -> ClueReport:
    """一键扫描安装目录，返回品牌线索报告。"""
    return ClueHunter(install_dir).hunt()


def load_custom_signatures(json_path: str) -> int:
    """加载自定义品牌签名扩展，返回新增签名数。"""
    global _DLL_BRAND_MAP, _MAGIC_FINGERPRINTS
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("[ClueHunter] 加载自定义签名失败 %s: %s", json_path, e)
        return 0
    sigs = data.get("signatures", data)
    if not isinstance(sigs, dict):
        return 0
    added = 0
    for brand_key, sig in sigs.items():
        if not isinstance(sig, dict):
            continue
        for dll_name in sig.get("dll_names", []):
            key = dll_name.lower()
            if key not in _DLL_BRAND_MAP:
                _DLL_BRAND_MAP[key] = {"brand": sig.get("brand", brand_key),
                                         "channel": sig.get("channel", "dll")}
                added += 1
        magic = sig.get("magic", "")
        if magic and magic not in _MAGIC_FINGERPRINTS:
            _MAGIC_FINGERPRINTS[magic] = {"brand": sig.get("brand", brand_key),
                                           "payload_size": str(sig.get("payload_size", 16))}
            added += 1
    logger.info("[ClueHunter] 加载了 %d 条自定义签名", added)
    return added
