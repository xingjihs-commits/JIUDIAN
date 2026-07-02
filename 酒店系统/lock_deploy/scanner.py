"""
lock_deploy/scanner.py — 扫盘找已装的门锁系统

策略：
1. 列出所有本地驱动器（C/D/E/F/…）
2. 在每个盘根、Program Files、桌面、用户目录这些常见处做受限深度扫描
3. 命中"门锁系统"关键特征文件就纳入候选列表
4. 候选按"支持的品牌优先 > 历史数据完整度 > 是否有 mdb"排序
5. 每个候选都跑一遍 `lock_adapters.detect_adapter()` 看能不能识别

性能约束：
- 单盘扫描不超过 8 秒（time_budget）
- 单目录最深 3 层
- 命中即收，不深挖
"""

from __future__ import annotations

import json
import os
import platform
import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import logging
import deploy_paths
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# MDB 卡样本提取（兼并 legacy_migration 的扫描逻辑）
# ──────────────────────────────────────────────────────────────────

@dataclass
class CardSampleStat:
    """从 CardInfo 表提取的卡样本统计分析。"""
    total_samples: int = 0
    unique_guest: int = 0
    unique_master: int = 0
    payload_patterns: Optional[Dict[str, Any]] = None
    detected_brand: Optional[str] = None


def scan_mdb_for_card_samples(mdb_path: str, max_samples: int = 100) -> List[str]:
    """从 CardLock 类型 MDB 的 CardInfo 表提取卡数据十六进制样本。

    兼容多种表结构：
    - CardInfo.CardStr（32 个十六进制字符 = 16 字节数据）
    - CardInfo.CardData
    - CardRecord.CardHex

    返回最多 max_samples 条有效十六进制样本。
    """
    if not os.path.isfile(mdb_path):
        return []

    samples: List[str] = []

    # 尝试 pyodbc
    try:
        import pyodbc
        conn_str = (
            r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
            f"DBQ={mdb_path};"
        )
        conn = pyodbc.connect(conn_str, timeout=3)
        cursor = conn.cursor()
    except Exception:
        conn = None
        cursor = None

    if cursor is not None:
        queries = [
            "SELECT TOP {} CardStr FROM CardInfo WHERE CardStr IS NOT NULL".format(max_samples),
            "SELECT TOP {} CardData FROM CardInfo WHERE CardData IS NOT NULL".format(max_samples),
            "SELECT TOP {} CardHex FROM CardRecord WHERE CardHex IS NOT NULL".format(max_samples),
        ]
        for q in queries:
            try:
                cursor.execute(q)
                for row in cursor.fetchall():
                    val = str(row[0] or "").strip().upper()
                    if len(val) >= 16 and all(c in "0123456789ABCDEF" for c in val):
                        samples.append(val)
                        if len(samples) >= max_samples:
                            break
            except Exception:
                continue
            if samples:
                break
        try:
            conn.close()
        except Exception:
            pass

    if samples:
        return samples

    # 备选：ADODB COM
    try:
        import win32com.client  # type: ignore
        conn2 = win32com.client.Dispatch("ADODB.Connection")
        conn_str2 = (
            r"Provider=Microsoft.Jet.OLEDB.4.0;"
            f"Data Source={mdb_path};"
        )
        conn2.Open(conn_str2)
        rs = win32com.client.Dispatch("ADODB.Recordset")
        queries2 = [
            "SELECT TOP {} CardStr FROM CardInfo".format(max_samples),
            "SELECT TOP {} CardData FROM CardInfo".format(max_samples),
            "SELECT TOP {} CardHex FROM CardRecord".format(max_samples),
        ]
        for q in queries2:
            try:
                rs.Open(q, conn2)
                if not rs.EOF:
                    while not rs.EOF:
                        val = str(rs.Fields(0).Value or "").strip().upper()
                        if len(val) >= 16 and all(c in "0123456789ABCDEF" for c in val):
                            samples.append(val)
                            if len(samples) >= max_samples:
                                break
                        rs.MoveNext()
                    rs.Close()
                    if samples:
                        break
            except Exception:
                pass
        conn2.Close()
    except Exception:
        pass

    return samples


def analyze_card_samples(samples: List[str]) -> Optional[CardSampleStat]:
    """对卡样本做基本统计分析。"""
    if not samples:
        return None

    stat = CardSampleStat(total_samples=len(samples))

    # 尝试用 BrandAnalyzer 做深度分析
    try:
        from lock_adapters.profile.brand_analyzer import BrandAnalyzer
        patterns = BrandAnalyzer.analyze(samples)
        stat.detected_brand = patterns.get("brand", "")
        stat.payload_patterns = patterns
    except Exception as exc:
        logger.debug("BrandAnalyzer 分析失败: %s", exc)

    return stat

# 已知品牌的关键文件特征。即使没有对应的 LockAdapter，也能在诊断模式下识别出来。
# 'required': 必须全部存在
# 'optional': 存在加分
# 'adapter_id': 对应 lock_adapters 里的品牌名（如果有）
BRAND_SIGNATURES: List[Dict] = [
    {
        "brand": "proUSB V9",
        "adapter_id": "proUSB",
        "required": ["V9RFL.dll", "d12.dll"],
        "optional": ["System.ini", "CardLock.mdb", "CardLock.exe", "Mwic_32.dll"],
    },
    {
        "brand": "proUSB / 普蓝德 Pro",
        "adapter_id": None,  # 未来可加
        "required": ["proRFL.dll", "d12c.dll"],
        "optional": ["System.ini"],
    },
    {
        "brand": "proUSB V10/V11",
        "adapter_id": None,
        "required": ["proRFLV10.dll"],
        "optional": ["System.ini"],
    },
    {
        "brand": "爱迪尔 Lock9200",
        "adapter_id": None,
        "required": ["Lock9200.dll"],
        "optional": [],
    },
    {
        "brand": "爱迪尔 Lock3200",
        "adapter_id": None,
        "required": ["Lock3200.dll"],
        "optional": [],
    },
    {
        "brand": "爱迪尔 通用 (MAINDLL)",
        "adapter_id": None,
        "required": ["maindll.dll"],
        "optional": [],
    },
    {
        "brand": "必达 IB",
        "adapter_id": None,
        "required": ["btIB232.dll", "BEDEIB32.dll"],
        "optional": [],
    },
    {
        "brand": "力维 LevelLock",
        "adapter_id": None,
        "required": ["LevelLock.dll"],
        "optional": [],
    },
    {
        "brand": "西容 SYRON",
        "adapter_id": None,
        "required": ["SYRONWR2007.ocx"],
        "optional": [],
    },
    {
        "brand": "雅迪顿",
        "adapter_id": None,
        "required": ["YDD_JK2008.dll"],
        "optional": [],
    },
    {
        "brand": "同创新佳",
        "adapter_id": None,
        "required": ["icdll.dll"],
        "optional": [],
    },
    {
        "brand": "宝迅达",
        "adapter_id": None,
        "required": ["HotelLock.dll"],
        "optional": [],
    },
]


@dataclass
class InstallationCandidate:
    """扫描结果中的一个候选目录。"""
    path: Path
    brand: str
    adapter_id: Optional[str] = None
    score: int = 0
    matched_required: List[str] = field(default_factory=list)
    matched_optional: List[str] = field(default_factory=list)
    has_mdb: bool = False
    mdb_paths: List[Path] = field(default_factory=list)
    system_ini: Optional[Path] = None
    supported: bool = False  # 是否有对应的 LockAdapter
    card_samples: List[str] = field(default_factory=list)
    """从 CardInfo 表提取的历史卡数据十六进制样本（最多 100 张）。"""
    sample_stat: Optional[CardSampleStat] = None
    """对 card_samples 的统计分析结果。"""
    payload_patterns: Dict[str, Any] = field(default_factory=dict)
    """自动推断的数据结构。"""

    def as_dict(self) -> Dict:
        return {
            "path": str(self.path),
            "brand": self.brand,
            "adapter_id": self.adapter_id,
            "score": self.score,
            "matched_required": list(self.matched_required),
            "matched_optional": list(self.matched_optional),
            "has_mdb": self.has_mdb,
            "mdb_paths": [str(p) for p in self.mdb_paths],
            "system_ini": str(self.system_ini) if self.system_ini else None,
            "supported": self.supported,
            "card_samples_count": len(self.card_samples),
            "detected_brand": self.sample_stat.detected_brand if self.sample_stat else None,
        }


# ──────────────────────────────────────────────────────────────────
# 扫描器
# ──────────────────────────────────────────────────────────────────

class LockSystemScanner:
    """扫盘找门锁系统候选。可以独立使用，也可被向导调用。"""

    def __init__(
        self,
        *,
        max_depth: int = 4,
        time_budget_s: float = 8.0,
        skip_dirs: Optional[Iterable[str]] = None,
    ):
        self.max_depth = max_depth
        self.time_budget_s = time_budget_s
        _default_skip = {
            "$RECYCLE.BIN", "System Volume Information", "Windows", "WinSxS",
            "Recovery", "PerfLogs", "ProgramData", "Microsoft", "node_modules",
            ".git", ".vscode", "__pycache__", ".idea", "AppData",
            "Common Files", "Internet Explorer", "Windows Defender",
        }
        self.skip_dirs: set = set(skip_dirs or ()) | _default_skip

    # ──────────── 入口 ────────────

    def scan(self, seeds: Optional[Iterable] = None) -> List[InstallationCandidate]:
        """返回按得分倒序的候选清单。"""
        found: List[InstallationCandidate] = []
        start = time.time()

        raw_roots = list(seeds) if seeds is not None else self._default_roots()
        roots: List[Path] = []
        for r in raw_roots:
            p = r if isinstance(r, Path) else Path(str(r))
            if p.is_dir():
                roots.append(p)

        for root in roots:
            if time.time() - start > self.time_budget_s:
                break
            try:
                found.extend(self._scan_root(root, start))
            except Exception as e:
                logger.warning("[scanner] error scanning %s: %s", root, e)

        # 去重 + 排序
        uniq: Dict[Path, InstallationCandidate] = {}
        for c in found:
            if c.path not in uniq or uniq[c.path].score < c.score:
                uniq[c.path] = c
        result = sorted(uniq.values(), key=lambda x: x.score, reverse=True)

        # 让 lock_adapters 再确认一遍并标为已支持
        try:
            from lock_adapters import detect_adapter
            for c in result:
                inst = detect_adapter(c.path)
                if inst is not None:
                    c.supported = True
                    c.adapter_id = inst.brand
        except Exception as e:
            logger.warning("[scanner] adapter detect error: %s", e)

        return result

    # ──────────── 候选目录 ────────────

    def _default_roots(self) -> List[Path]:
        roots: List[Path] = []
        if platform.system() == "Windows":
            # 1. 所有本地固定盘
            import ctypes
            try:
                bitmask = ctypes.windll.kernel32.GetLogicalDrives()
                for letter in string.ascii_uppercase:
                    if bitmask & 1:
                        drive = f"{letter}:\\"
                        try:
                            t = ctypes.windll.kernel32.GetDriveTypeW(drive)
                            if t == 3:  # DRIVE_FIXED
                                roots.append(Path(drive))
                        except Exception:
                            pass
                    bitmask >>= 1
            except Exception:
                roots.extend(Path(d) for d in deploy_paths.scan_roots())

            # 2. 常见安装目录
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
            user_home = Path.home()
            for p in (program_files, program_files_x86, str(user_home / "Desktop"), str(user_home)):
                pp = Path(p)
                if pp.is_dir() and pp not in roots:
                    roots.append(pp)
        else:
            roots.append(Path.home())

        return roots

    # ──────────── 单根扫描 ────────────

    def _scan_root(self, root: Path, start_time: float) -> List[InstallationCandidate]:
        if not root.is_dir():
            return []

        out: List[InstallationCandidate] = []
        # 用迭代式 DFS，方便随时切走
        stack: List[tuple[Path, int]] = [(root, 0)]
        while stack:
            if time.time() - start_time > self.time_budget_s:
                break
            cur, depth = stack.pop()
            if depth > self.max_depth:
                continue
            try:
                entries = list(os.scandir(cur))
            except Exception:
                continue

            # 看本目录是否命中
            cand = self._inspect_dir(cur, entries)
            if cand is not None:
                out.append(cand)
                # 命中后继续往下浅扫一层（很多酒店软件子目录还有 mdb 或 ini）
                if depth >= self.max_depth - 1:
                    continue

            # 递归子目录
            for e in entries:
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                    name = e.name
                    if not name or name.startswith(".") or name in self.skip_dirs:
                        continue
                    # 名称中含中文/英文关键词的优先
                    stack.append((Path(e.path), depth + 1))
                except Exception:
                    continue

        return out

    def _inspect_dir(self, path: Path, entries) -> Optional[InstallationCandidate]:
        """看 path 是不是某个门锁系统的安装目录。"""
        names_lower = {e.name.lower(): e.name for e in entries if not e.is_dir(follow_symlinks=False)}

        best: Optional[InstallationCandidate] = None
        for sig in BRAND_SIGNATURES:
            required_lower = [r.lower() for r in sig["required"]]
            if not all(rl in names_lower for rl in required_lower):
                continue
            optional_lower = [o.lower() for o in sig["optional"]]
            matched_opt = [names_lower[ol] for ol in optional_lower if ol in names_lower]
            score = 100 + len(matched_opt) * 10
            if sig.get("adapter_id"):
                score += 50  # 已支持品牌加分

            cand = InstallationCandidate(
                path=path,
                brand=sig["brand"],
                adapter_id=sig.get("adapter_id"),
                score=score,
                matched_required=list(sig["required"]),
                matched_optional=matched_opt,
            )

            # mdb 探测 + 卡样本提取
            for e in entries:
                try:
                    if e.is_file() and e.name.lower().endswith(".mdb"):
                        cand.has_mdb = True
                        cand.mdb_paths.append(Path(e.path))
                        cand.score += 20
                        # 提取卡样本（最多 100 张）
                        try:
                            samples = scan_mdb_for_card_samples(str(e.path), max_samples=100)
                            if samples:
                                cand.card_samples = samples
                                cand.score += 15
                                # 做品牌分析
                                stat = analyze_card_samples(samples)
                                if stat:
                                    cand.sample_stat = stat
                                    if stat.payload_patterns:
                                        cand.payload_patterns = stat.payload_patterns
                                    if stat.detected_brand:
                                        cand.score += 10
                        except Exception as e2:
                            logger.debug("MDB 卡样本提取失败 %s: %s", e.path, e2)
                except Exception:
                    continue

            # ini 探测
            for e in entries:
                try:
                    if e.is_file() and e.name.lower() == "system.ini":
                        cand.system_ini = Path(e.path)
                        cand.score += 15
                        break
                except Exception:
                    continue

            if best is None or cand.score > best.score:
                best = cand

        return best


# ──────────────────────────────────────────────────────────────────
# 便捷入口
# ──────────────────────────────────────────────────────────────────

def scan_for_lock_systems(
    *, time_budget_s: float = 8.0, seeds: Optional[Iterable[str]] = None,
) -> List[InstallationCandidate]:
    scanner = LockSystemScanner(time_budget_s=time_budget_s)
    return scanner.scan(seeds=seeds)
