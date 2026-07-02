"""
deploy_paths.py — 统一路径管理（部署根目录 + 外部硬编码路径集中化）

逐步替代各文件中散落的硬编码路径。新路径函数优先读环境变量（方便测试/部署按需覆盖）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


# ── 部署根目录 ──────────────────────────────────────────

def get_deploy_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_path(*parts: str) -> Path:
    return get_deploy_root().joinpath(*parts)


# ── 系统路径 ────────────────────────────────────────────

def hotel_data_root() -> str:
    """Solid 业务数据根目录。"""
    return os.environ.get("SOLID_HOTEL_DATA", "D:\\SolidHotel")


def system_root() -> str:
    """Windows 系统根目录。"""
    return os.environ.get("SystemRoot", "C:\\Windows")


def fonts_dir() -> str:
    """Windows 字体目录。"""
    return os.path.join(system_root(), "Fonts")


def program_files() -> str:
    """Program Files 目录（64位）。"""
    return os.environ.get("ProgramFiles", "C:\\Program Files")


def program_files_x86() -> str:
    """Program Files (x86) 目录（32位）。"""
    return os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")


# ── 门锁 / 老系统路径 ───────────────────────────────────

def cardlock_install_dir() -> str:
    """智能门锁管理系统安装目录。"""
    return "D:\\项目\\智能门锁管理系统新2021网络版"


def cardlock_backup_dir() -> str:
    """proUSB 数据库备份目录。"""
    return "D:\\proUSB_DBBak"


# ── 扫描 / 探测用 ──────────────────────────────────────

def scan_roots() -> list[str]:
    """整机扫盘时的默认根目录列表。"""
    return ["C:\\", "D:\\"]
