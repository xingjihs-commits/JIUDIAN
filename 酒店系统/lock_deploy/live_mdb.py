"""
lock_deploy/live_mdb.py — 自动发现 proUSB 等门锁系统的「活」MDB 数据库

老软件每天滚动生成 CardLock<YYMMDD>.MDB，路径由 System.ini 的 ShareDBPath / DBBakPath 配置。
本模块只负责路径发现与结构校验（含 CardInfo 表），不读取业务行。
"""

from __future__ import annotations

import datetime as _dt
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

_CARDLOCK_RE = re.compile(r"CardLock(\d{6})\.MDB$", re.IGNORECASE)

# install_dir 下常见备份子目录名
_INSTALL_BACKUP_NAMES = (
    "数据备份记录",
    "BakData",
    "Backup",
    "Records",
    "DBBak",
)

# 盘根浅扫时匹配的目录名片段
_COMMON_DIR_GLOBS = ("proUSB*", "*DBBak*", "*proUSB*DBBak*")

_COMMON_DRIVE_ROOTS = ("D:\\", "C:\\", "E:\\")


@dataclass
class LiveMdbResult:
    path: Path | None
    dir: Path | None
    date_in_name: str = ""
    mtime_iso: str = ""
    source: str = ""
    candidates: list[Path] = field(default_factory=list)
    validated: bool = False
    error: str | None = None


def _parse_date_from_name(path: Path) -> str:
    m = _CARDLOCK_RE.search(path.name)
    return m.group(1) if m else ""


def _mtime_iso(path: Path) -> str:
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def _is_unc(path_str: str) -> bool:
    return path_str.startswith("\\\\") or path_str.startswith("//")


def _safe_is_file(p: Path, *, timeout_s: float) -> bool:
    """对 UNC 路径用超时命令探测，本地路径直接 is_file。"""
    s = str(p)
    if _is_unc(s):
        return _path_reachable(p, timeout_s=timeout_s) and not _is_dir_unc(s, timeout_s=timeout_s)
    try:
        return p.is_file()
    except OSError:
        return False


def _is_dir_unc(s: str, *, timeout_s: float) -> bool:
    try:
        r = subprocess.run(
            ["cmd", "/c", f'if exist "{s}\\*" (echo OK) else (echo NO)'],
            capture_output=True,
            timeout=max(0.5, timeout_s),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (r.stdout or b"").decode("utf-8", errors="replace").strip().upper()
        return out == "OK"
    except Exception:
        return False


def _path_reachable(path: Path, *, timeout_s: float) -> bool:
    """UNC / 网络路径带超时探测，避免阻塞 UI。"""
    p = Path(path)
    if not str(p):
        return False
    s = str(p)
    is_unc = s.startswith("\\\\") or s.startswith("//")

    if is_unc:
        # Windows 上 Path.exists() 对不可达 UNC 可能阻塞数十秒，线程超时无法中断。
        try:
            r = subprocess.run(
                ["cmd", "/c", f'if exist "{s}" (echo OK) else (echo NO)'],
                capture_output=True,
                timeout=max(0.5, timeout_s),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out = (r.stdout or b"").decode("utf-8", errors="replace").strip().upper()
            return out == "OK"
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return False

    try:
        if p.is_file():
            return True
        if p.is_dir():
            return True
        return False
    except OSError:
        return False


def _iter_cardlock_mdbs_in_dir(directory: Path) -> Iterator[Path]:
    if not _path_reachable(directory, timeout_s=2.0):
        return
    try:
        for entry in directory.iterdir():
            try:
                if entry.is_file() and _CARDLOCK_RE.match(entry.name):
                    yield entry
            except OSError:
                continue
    except OSError:
        return


def _collect_from_dir(directory: Path, *, source: str, net_timeout_s: float) -> List[Tuple[Path, str, str]]:
    """返回 [(路径, 日期名, 来源), ...]"""
    if not directory or not _path_reachable(directory, timeout_s=net_timeout_s):
        return []
    out: List[Tuple[Path, str, str]] = []
    for p in _iter_cardlock_mdbs_in_dir(directory):
        out.append((p, _parse_date_from_name(p), source))
    return out


def _pick_best(candidates: List[Tuple[Path, str, str]]) -> Tuple[Path, str, str] | None:
    if not candidates:
        return None
    # 按文件名 YYMMDD 倒序；同日期按修改时间最新
    def sort_key(item: Tuple[Path, str, str]) -> tuple:
        path, date_s, _src = item
        try:
            date_int = int(date_s) if date_s else 0
        except ValueError:
            date_int = 0
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        return (date_int, mtime)

    return max(candidates, key=sort_key)


def _shallow_scan_roots(
    roots: Iterable[str],
    *,
    max_depth: int = 2,
    net_timeout_s: float,
) -> List[Tuple[Path, str, str]]:
    found: List[Tuple[Path, str, str]] = []

    def walk_dir(base: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if not _path_reachable(base, timeout_s=net_timeout_s):
            return
        # 当前目录里的 CardLock*.MDB
        for p in _iter_cardlock_mdbs_in_dir(base):
            found.append((p, _parse_date_from_name(p), "common_root"))
        if depth >= max_depth:
            return
        try:
            children = list(base.iterdir())
        except OSError:
            return
        for child in children:
            try:
                if not child.is_dir():
                    continue
                name_lower = child.name.lower()
                if any(
                    frag.replace("*", "") in name_lower
                    for pat in _COMMON_DIR_GLOBS
                    for frag in (pat.lower().split("*") if "*" in pat else [pat.lower()])
                    if frag
                ):
                    walk_dir(child, depth + 1)
                    continue
                # 深度 1：也进入名字含 pro 或 db 或 bak 的目录
                if depth == 0 and any(k in name_lower for k in ("prousb", "dbbak", "cardlock", "backup")):
                    walk_dir(child, depth + 1)
            except OSError:
                continue

    for root_s in roots:
        root = Path(root_s)
        if not root.exists() and not str(root).startswith("\\\\"):
            continue
        walk_dir(root, 0)
    return found


def validate_mdb_has_cardinfo(mdb_path: Path, *, timeout_s: int = 30) -> Tuple[bool, str]:
    """用 mdb-tables 校验 MDB 可读且含 CardInfo 表。"""
    try:
        from mdb_import_backend import find_mdbtools_exe
    except ImportError:
        return False, "mdb_import_backend 不可用"

    exe = find_mdbtools_exe("mdb-tables")
    if not exe:
        return False, "未找到 mdb-tables"

    try:
        r = subprocess.run(
            [str(exe), "-1", str(mdb_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "mdb-tables 超时"
    except Exception as e:
        return False, str(e)

    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        return False, err or f"mdb-tables 退出码 {r.returncode}"

    tables = {t.strip() for t in (r.stdout or "").splitlines() if t.strip()}
    if "CardInfo" in tables:
        return True, ""
    # 大小写不敏感再查一次
    for t in tables:
        if t.lower() == "cardinfo":
            return True, ""
    return False, f"缺少 CardInfo 表（共 {len(tables)} 张表）"


def discover_live_mdb(
    *,
    install_dir: Path | None = None,
    share_db_path_hint: str | None = None,
    db_bak_path_hint: str | None = None,
    manual_override: Path | None = None,
    probe_common_roots: bool = True,
    net_timeout_s: float = 2.0,
) -> LiveMdbResult:
    """
    按优先级发现今日（或最新）活 MDB。

    顺序：manual → ShareDBPath → DBBakPath → install 备份子目录 → 盘根浅扫 → install/CardLock.mdb
    """
    collected: List[Tuple[Path, str, str]] = []
    install_dir = Path(install_dir) if install_dir else None

    def add_batch(items: List[Tuple[Path, str, str]]) -> None:
        for item in items:
            if item not in collected:
                collected.append(item)

    # 1) manual
    if manual_override:
        mo = Path(manual_override)
        if mo.suffix.lower() == ".mdb" and _safe_is_file(mo, timeout_s=net_timeout_s):
            collected.append((mo, _parse_date_from_name(mo), "manual"))
        elif _path_reachable(mo, timeout_s=net_timeout_s):
            add_batch(_collect_from_dir(mo, source="manual", net_timeout_s=net_timeout_s))

    # 2) ShareDBPath
    if share_db_path_hint and str(share_db_path_hint).strip():
        sp = Path(share_db_path_hint.strip())
        if sp.suffix.lower() == ".mdb" and _safe_is_file(sp, timeout_s=net_timeout_s):
            collected.append((sp, _parse_date_from_name(sp), "share"))
        else:
            add_batch(_collect_from_dir(sp, source="share", net_timeout_s=net_timeout_s))

    # 3) DBBakPath
    if db_bak_path_hint and str(db_bak_path_hint).strip():
        bp = Path(db_bak_path_hint.strip())
        add_batch(_collect_from_dir(bp, source="ini", net_timeout_s=net_timeout_s))

    # 4) install_dir 备份子目录
    if install_dir and install_dir.is_dir():
        for sub_name in _INSTALL_BACKUP_NAMES:
            sub = install_dir / sub_name
            add_batch(_collect_from_dir(sub, source="install_backup", net_timeout_s=net_timeout_s))

    # 5) 盘根浅扫
    if probe_common_roots:
        add_batch(_shallow_scan_roots(_COMMON_DRIVE_ROOTS, net_timeout_s=net_timeout_s))

    # 6) 兜底 install_dir/CardLock.mdb
    fallback: Path | None = None
    if install_dir:
        fb = install_dir / "CardLock.mdb"
        if fb.is_file():
            fallback = fb
            if not any(p.resolve() == fb.resolve() for p, _, _ in collected):
                collected.append((fb, _parse_date_from_name(fb), "install_root"))

    all_paths = sorted({p for p, _, _ in collected}, key=lambda x: (_parse_date_from_name(x), x.name), reverse=True)

    best = _pick_best(collected)
    if best is None:
        if fallback is not None:
            best = (fallback, _parse_date_from_name(fallback), "install_root")
        else:
            return LiveMdbResult(
                path=None,
                dir=None,
                source="",
                candidates=all_paths,
                validated=False,
                error="未找到任何 CardLock*.MDB",
            )

    best_path, best_date, best_source = best
    parent_dir = best_path.parent if best_path else None

    validated, val_err = validate_mdb_has_cardinfo(best_path)
    if not validated and fallback and fallback != best_path:
        ok_fb, _ = validate_mdb_has_cardinfo(fallback)
        if ok_fb:
            best_path = fallback
            best_date = _parse_date_from_name(fallback)
            best_source = "install_root"
            parent_dir = fallback.parent
            validated = True
            val_err = ""

    return LiveMdbResult(
        path=best_path,
        dir=parent_dir,
        date_in_name=best_date,
        mtime_iso=_mtime_iso(best_path),
        source=best_source,
        candidates=all_paths,
        validated=validated,
        error=None if validated else (val_err or "校验未通过"),
    )
