"""
Ghidra 查找器 — 在 U 盘/采集器同目录下定位 Ghidra 便携版
支持 Windows (.bat) 和 Linux (无扩展名) 的 analyzeHeadless
"""

import os
import sys
from pathlib import Path


def _get_collector_root() -> Path:
    """获取采集器所在根目录。打包版用 sys.executable，源码版用 __file__"""
    if getattr(sys, 'frozen', False):
        exe_path = Path(sys.executable).resolve().parent
    else:
        exe_path = Path(__file__).resolve().parent.parent
    return exe_path


def find_ghidra() -> Path | None:
    """
    在采集器根目录下查找 Ghidra 安装。
    检查路径：ghidra/support/analyzeHeadless.bat (Win) 或 analyzeHeadless (Linux)
    返回完整路径或 None
    """
    root = _get_collector_root()
    ghidra_dir = root / "ghidra"

    if not ghidra_dir.exists():
        return None

    win_bat = ghidra_dir / "support" / "analyzeHeadless.bat"
    if win_bat.exists():
        return win_bat

    unix_sh = ghidra_dir / "support" / "analyzeHeadless"
    if unix_sh.exists():
        return unix_sh

    return None


def get_ghidra_analyze_headless() -> str | None:
    """返回 analyzeHeadless 的完整路径字符串，找不到返回 None"""
    p = find_ghidra()
    return str(p) if p else None


def get_ghidra_project_dir() -> Path:
    """返回 Ghidra 临时项目目录（U盘内，不写酒店电脑）"""
    root = _get_collector_root()
    proj = root / "ghidra" / "temp"
    proj.mkdir(parents=True, exist_ok=True)
    return proj


def get_portable_java_home() -> Path | None:
    """返回 U 盘内 Java JRE 路径，用于设置 JAVA_HOME"""
    root = _get_collector_root()
    java_dir = root / "java"
    return java_dir if java_dir.exists() else None
