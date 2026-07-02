"""
oem_process.py — 原厂进程与前台程序发现（通用，不绑 V9 命名）

职责：
1. 在安装目录枚举可能的前台 exe
2. 检测正在运行的原厂进程（路径 + 模糊名）
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# 精确匹配（加分，非唯一条件）
KNOWN_LOCK_EXE_NAMES = {
    "cardlock.exe", "cardserver.exe", "cardsvr.exe",
    "locker.exe", "lockmanager.exe", "hotellock.exe",
    "syslock.exe", "prousb.exe", "iccard.exe",
    "doorlock.exe", "hotelcard.exe", "cardlockv9.exe",
    "writecard.exe", "cardmanager.exe",
}

# 进程名模糊关键词
_NAME_KEYWORDS = ("cardlock", "cardlock-", "门锁", "doorlock", "hotellock", "lock")

# exe 文件名降权关键词
_EXE_SKIP_KEYWORDS = (
    "uninstall", "setup", "install", "repair", "update",
    "patch", "helper", "service", "svr", "server",
)

# exe 文件名加分关键词
_EXE_BOOST_KEYWORDS = (
    "cardlock", "doorlock", "lock", "门锁", "writecard", "card",
)


@dataclass
class OemExeInfo:
    name: str
    path: str
    score: int = 0


@dataclass
class OemProcess:
    pid: int
    name: str
    exe_path: str = ""
    reason: str = ""


def _norm_path(p: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(p))
    except Exception:
        return p or ""


def _install_prefix(install_dir: str) -> str:
    return _norm_path(install_dir).rstrip("\\/") + os.sep


def _exe_name_score(name: str) -> int:
    lower = name.lower()
    for skip in _EXE_SKIP_KEYWORDS:
        if skip in lower:
            return -100
    score = 0
    for kw in _EXE_BOOST_KEYWORDS:
        if kw in lower:
            score += 50
    if lower in KNOWN_LOCK_EXE_NAMES:
        score += 80
    return score


def find_oem_exes(install_dir: str, *, top_n: int = 5) -> List[OemExeInfo]:
    """枚举安装目录下的前台 exe，按相关性排序。"""
    base = Path(install_dir)
    if not base.is_dir():
        return []

    candidates: List[OemExeInfo] = []
    try:
        for entry in base.glob("*.exe"):
            if not entry.is_file():
                continue
            score = _exe_name_score(entry.name)
            if score < 0:
                continue
            candidates.append(OemExeInfo(
                name=entry.name,
                path=str(entry.resolve()),
                score=score,
            ))
    except OSError as e:
        logger.warning("枚举 exe 失败: %s", e)

    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates[:top_n]


def find_primary_oem_exe(install_dir: str) -> Optional[str]:
    """返回最可能的前台程序完整路径。白名单未命中时取最近修改的 .exe。"""
    exes = find_oem_exes(install_dir, top_n=1)
    if exes:
        return exes[0].path
    # B3: 白名单未命中 → 回退取目录下最近修改的 .exe
    try:
        all_exes = []
        for f in os.listdir(install_dir):
            if f.lower().endswith('.exe'):
                fp = os.path.join(install_dir, f)
                all_exes.append((os.path.getmtime(fp), fp))
        if all_exes:
            all_exes.sort(reverse=True)
            logger.info("白名单未命中，回退取最新 .exe: %s", all_exes[0][1])
            return all_exes[0][1]
    except OSError:
        pass
    return None


def _name_matches_lock(name: str) -> bool:
    lower = (name or "").lower()
    if lower in KNOWN_LOCK_EXE_NAMES:
        return True
    for kw in _NAME_KEYWORDS:
        if kw in lower:
            return True
    return False


def _exe_under_install(exe_path: str, install_dir: str) -> bool:
    if not exe_path or not install_dir:
        return False
    prefix = _install_prefix(install_dir)
    return _norm_path(exe_path).startswith(prefix)


def find_running_oem_processes(install_dir: str) -> List[OemProcess]:
    """检测可能占用发卡器的原厂进程。"""
    results: List[OemProcess] = []
    try:
        import psutil
    except ImportError:
        logger.warning("psutil 未安装，无法检测原厂进程")
        return results

    seen_pids: set[int] = set()
    prefix = _install_prefix(install_dir)

    try:
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                pid = int(proc.info.get("pid") or 0)
                if pid <= 0 or pid in seen_pids:
                    continue
                name = proc.info.get("name") or ""
                exe = proc.info.get("exe") or ""

                under = _exe_under_install(exe, install_dir)
                name_hit = _name_matches_lock(name)

                if not under and not name_hit:
                    continue

                reasons = []
                if under:
                    reasons.append("exe在所选目录")
                if name_hit:
                    reasons.append("进程名匹配门锁")

                seen_pids.add(pid)
                results.append(OemProcess(
                    pid=pid,
                    name=name,
                    exe_path=exe or "",
                    reason="、".join(reasons),
                ))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        logger.warning("进程扫描异常: %s", e)

    results.sort(key=lambda p: (0 if _exe_under_install(p.exe_path, install_dir) else 1, p.name.lower()))
    return results


def format_oem_running_hint(processes: List[OemProcess]) -> str:
    """生成用户可读的关闭原厂指引。"""
    if not processes:
        return ""
    names = ", ".join(p.name for p in processes[:3])
    extra = f" 等 {len(processes)} 个" if len(processes) > 3 else ""
    return (
        f"检测到原厂软件正在运行：{names}{extra}\n"
        "请先完全退出原厂程序（任务管理器结束 CARDLOCK/门锁 相关进程），"
        "拔掉发卡器等待 5 秒再插上，然后点「重新扫描」。"
    )
