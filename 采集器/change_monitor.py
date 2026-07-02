"""
change_monitor.py — 注册表 + 文件变化监听器

职责：
1. 发卡前拍摄文件系统/注册表快照
2. 发卡后拍摄快照
3. 对比差异，产出变化报告

实现方式：
- 文件监控：扫描安装目录下所有文件，记录 size/mtime/md5
- 注册表监控：读取 HKLM\Software 和 HKCU\Software 下门锁相关键
- 不依赖 Procmon 内核驱动（纯 Python 实现，适合 U 盘工具场景）

用法：
    from collector.change_monitor import ChangeMonitor
    monitor = ChangeMonitor(install_dir="D:\\智能门锁管理系统")
    monitor.snapshot("before")
    # ... 操作原厂软件发卡 ...
    monitor.snapshot("after")
    report = monitor.diff()
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from .forensic_schema import (
    FileChangeEntry, FileChangeReport,
    RegChange, RegistryChangeReport,
)

logger = logging.getLogger(__name__)

# 已知的门锁相关注册表路径（前缀匹配）
KNOWN_LOCK_REG_KEYS = [
    r"Software\CardLock",
    r"Software\proUSB",
    r"Software\Walton",
    r"Software\智能门锁",
    r"Software\酒店门锁",
    r"Software\CardServer",
    r"Software\必达",
    r"Software\爱迪尔",
]


class ChangeMonitor:
    """注册表 + 文件变化快照对比器。"""

    def __init__(self, install_dir: str):
        self._root = Path(install_dir)
        self._file_snapshots: dict[str, dict] = {}
        self._reg_snapshots: dict[str, dict] = {}

    # ── 文件快照 ──────────────────────────────────────────

    def snapshot(self, tag: str):
        """同时拍摄文件 + 注册表快照。"""
        self._file_snapshots[tag] = self._snapshot_files()
        self._reg_snapshots[tag] = self._snapshot_registry()
        logger.info("快照 [%s]: %d 文件, %d 注册表键",
                     tag, len(self._file_snapshots[tag]),
                     len(self._reg_snapshots[tag]))

    def diff(self) -> tuple[FileChangeReport, RegistryChangeReport]:
        """对比 before/after 快照，产出变化报告。"""
        file_report = self._diff_files()
        reg_report = self._diff_registry()
        return file_report, reg_report

    def _snapshot_files(self) -> dict[str, dict]:
        """扫描安装目录下所有文件，返回 {rel_path: {size, mtime, md5}}。"""
        snap: dict[str, dict] = {}
        if not self._root.is_dir():
            return snap
        for entry in self._root.rglob("*"):
            if not entry.is_file():
                continue
            try:
                stat = entry.stat()
                rel = str(entry.relative_to(self._root))
                info = {
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "md5": "",
                }
                if stat.st_size < 50 * 1024 * 1024:
                    info["md5"] = self._md5_file(str(entry))
                snap[rel] = info
            except (OSError, PermissionError):
                continue
        return snap

    def _diff_files(self) -> FileChangeReport:
        report = FileChangeReport()
        before = self._file_snapshots.get("before", {})
        after = self._file_snapshots.get("after", {})

        report.before_snapshot = sorted(before.keys())
        report.after_snapshot = sorted(after.keys())

        before_paths = set(before.keys())
        after_paths = set(after.keys())

        # 新增文件
        for p in after_paths - before_paths:
            ai = after[p]
            report.changes.append(FileChangeEntry(
                path=p,
                change_type="added",
                after_size=ai["size"],
                after_md5=ai.get("md5", ""),
            ))

        # 删除文件
        for p in before_paths - after_paths:
            bi = before[p]
            report.changes.append(FileChangeEntry(
                path=p,
                change_type="deleted",
                before_size=bi["size"],
                before_md5=bi.get("md5", ""),
            ))

        # 修改文件
        for p in before_paths & after_paths:
            bi = before[p]
            ai = after[p]
            if bi["size"] != ai["size"] or bi.get("md5") != ai.get("md5"):
                report.changes.append(FileChangeEntry(
                    path=p,
                    change_type="modified",
                    before_size=bi["size"],
                    after_size=ai["size"],
                    before_md5=bi.get("md5", ""),
                    after_md5=ai.get("md5", ""),
                ))

        logger.info("文件变化: %d 新增, %d 删除, %d 修改",
                     sum(1 for c in report.changes if c.change_type == "added"),
                     sum(1 for c in report.changes if c.change_type == "deleted"),
                     sum(1 for c in report.changes if c.change_type == "modified"))
        return report

    @staticmethod
    def _md5_file(filepath: str) -> str:
        try:
            h = hashlib.md5()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    # ── 注册表快照 ────────────────────────────────────────

    def _snapshot_registry(self) -> dict[str, dict]:
        """扫描门锁相关注册表键，返回 {key_path: {value_name: value}}。"""
        snap: dict[str, dict] = {}
        try:
            import winreg
        except ImportError:
            logger.debug("winreg 不可用（非 Windows 环境）")
            return snap

        hives = [
            (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
            (winreg.HKEY_CURRENT_USER, "HKCU"),
        ]

        for hive, hive_name in hives:
            for subkey_pattern in KNOWN_LOCK_REG_KEYS:
                try:
                    full_key = fr"{hive_name}\{subkey_pattern}"
                    snap[full_key] = self._read_registry_key(
                        hive, subkey_pattern
                    )
                except Exception:
                    continue

        return snap

    def _read_registry_key(self, hive: Any, subkey: str) -> dict[str, str]:
        """递归读注册表键下所有值。"""
        values: dict[str, str] = {}
        try:
            import winreg
            key = winreg.OpenKey(hive, subkey, 0,
                                 winreg.KEY_READ | winreg.KEY_WOW64_32KEY)
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(key, i)
                    values[name] = str(data)
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except Exception:
            try:
                # 尝试 64 位视图
                import winreg
                key = winreg.OpenKey(hive, subkey, 0,
                                     winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
                i = 0
                while True:
                    try:
                        name, data, _ = winreg.EnumValue(key, i)
                        values[name] = str(data)
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(key)
            except Exception:
                pass
        return values

    def _diff_registry(self) -> RegistryChangeReport:
        report = RegistryChangeReport()
        before = self._reg_snapshots.get("before", {})
        after = self._reg_snapshots.get("after", {})

        report.before_snapshot = before
        report.after_snapshot = after

        all_keys = set(before.keys()) | set(after.keys())
        for key in sorted(all_keys):
            before_vals = before.get(key, {})
            after_vals = after.get(key, {})

            all_vals = set(before_vals.keys()) | set(after_vals.keys())

            for vn in sorted(all_vals):
                bv = before_vals.get(vn)
                av = after_vals.get(vn)
                if bv == av:
                    continue
                if bv is None:
                    report.changes.append(RegChange(
                        key=key, value_name=vn,
                        old_value="", new_value=av,
                        change_type="added",
                    ))
                elif av is None:
                    report.changes.append(RegChange(
                        key=key, value_name=vn,
                        old_value=bv, new_value="",
                        change_type="deleted",
                    ))
                else:
                    report.changes.append(RegChange(
                        key=key, value_name=vn,
                        old_value=bv, new_value=av,
                        change_type="modified",
                    ))

        logger.info("注册表变化: %d 项", len(report.changes))
        return report
