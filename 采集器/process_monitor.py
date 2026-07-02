"""
process_monitor.py — 进程树法医监控引擎

职责：
1. 发卡前拍摄进程快照
2. 发卡中持续监控新进程、加载的 DLL
3. 检测守护进程（guardian / watchdog）
4. 对比前后变化，产出进程树报告

用法：
    from collector.process_monitor import ProcessMonitor
    monitor = ProcessMonitor()
    monitor.snapshot("before")           # 拍快照
    # ... 操作原厂软件发卡 ...
    monitor.snapshot("during")
    # ... 等待完成 ...
    monitor.snapshot("after")
    report = monitor.diff()              # 产出 ProcessTreeReport
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .forensic_schema import ProcessSnapshot, ProcessTreeReport

logger = logging.getLogger(__name__)

# 已知门锁进程名（不区分大小写）
KNOWN_LOCK_NAMES = {
    "cardlock.exe", "cardserver.exe", "cardsvr.exe",
    "locker.exe",  "lockmanager.exe",   "hotellock.exe",
    "syslock.exe", "prousb.exe",         "iccard.exe",
    "doorlock.exe", "hotelcard.exe",
}

# 已知守护/监控进程模式
GUARDIAN_PATTERNS = {"watchdog", "guard", "monitor", "svchost", "service"}


class ProcessMonitor:
    """进程树快照 + 差异分析器。"""

    def __init__(self):
        self._snapshots: dict[str, list[ProcessSnapshot]] = {}

    @property
    def has_snapshots(self) -> bool:
        return len(self._snapshots) > 0

    def snapshot(self, tag: str) -> list[ProcessSnapshot]:
        """拍摄当前进程树快照。tag 如 'before' / 'during' / 'after'。"""
        procs = self._capture_snapshot()
        self._snapshots[tag] = procs
        logger.info("进程快照 [%s]: %d 进程", tag, len(procs))
        return procs

    def diff(self) -> ProcessTreeReport:
        """对比快照，产出差异报告。"""
        report = ProcessTreeReport()
        report.before_issue = self._snapshots.get("before", [])
        report.during_issue = self._snapshots.get("during", [])
        report.after_issue = self._snapshots.get("after", [])

        if "before" in self._snapshots and "after" in self._snapshots:
            before_pids = {p.pid for p in self._snapshots["before"]}
            after_pids = {p.pid for p in self._snapshots["after"]}

            new_pids = after_pids - before_pids
            for snap in self._snapshots["after"]:
                if snap.pid in new_pids:
                    report.new_processes.append(snap)

            # 识别守护进程（在门锁目录下且非核心进程）
            for snap in report.after_issue:
                name_lower = snap.name.lower()
                is_lock = any(kn in name_lower for kn in KNOWN_LOCK_NAMES)
                if not is_lock:
                    continue
                is_guardian = any(gp in name_lower for gp in GUARDIAN_PATTERNS)
                if is_guardian:
                    report.guardian_processes.append(snap)

        return report

    def find_lock_processes(self) -> list[ProcessSnapshot]:
        """从最新快照中找出所有门锁相关进程。"""
        latest = self._snapshots.get("after") or self._snapshots.get("before", [])
        results = []
        for snap in latest:
            name_lower = snap.name.lower()
            if name_lower in KNOWN_LOCK_NAMES:
                results.append(snap)
        return results

    def detect_guardian(self) -> list[ProcessSnapshot]:
        """检测守护进程。"""
        diff_result = self.diff()
        if diff_result.guardian_processes:
            return diff_result.guardian_processes

        # 额外启发式检测：卡主进程的子进程
        lock_procs = self.find_lock_processes()
        guardians = []
        for lp in lock_procs:
            for snap in (self._snapshots.get("after") or
                         self._snapshots.get("during") or
                         self._snapshots.get("before", [])):
                if snap.parent_pid == lp.pid and snap.pid != lp.pid:
                    guardians.append(snap)
        return guardians

    # ── 内部 ──────────────────────────────────────────────

    def _capture_snapshot(self) -> list[ProcessSnapshot]:
        results: list[ProcessSnapshot] = []
        try:
            import psutil
        except ImportError:
            logger.warning("psutil 未安装，进程监控不可用")
            return results

        try:
            for proc in psutil.process_iter([
                "pid", "name", "exe", "cmdline", "ppid",
            ]):
                try:
                    pid = proc.info["pid"]
                    name = proc.info["name"] or ""
                    exe_path = proc.info["exe"] or ""
                    cmdline = " ".join(proc.info["cmdline"] or [])
                    ppid = proc.info["ppid"] or 0

                    # 加载的 DLL（仅对门锁进程才枚举，减少开销）
                    loaded_dlls: list[str] = []
                    name_lower = name.lower()
                    if any(kn in name_lower for kn in KNOWN_LOCK_NAMES):
                        try:
                            mmaps = proc.memory_maps()
                            seen_paths = set()
                            for mm in mmaps:
                                p = mm.path
                                if p and p.lower().endswith(".dll") and p not in seen_paths:
                                    seen_paths.add(p)
                                    loaded_dlls.append(p)
                                    if len(loaded_dlls) > 200:
                                        break
                        except (psutil.AccessDenied, Exception):
                            pass

                    results.append(ProcessSnapshot(
                        pid=pid,
                        name=name,
                        exe_path=exe_path,
                        cmdline=cmdline,
                        parent_pid=ppid,
                        loaded_dlls=loaded_dlls,
                    ))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.warning("进程快照采集异常: %s", e)

        return results
