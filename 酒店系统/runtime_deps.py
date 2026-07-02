"""
合作酒店现场依赖 — 全部预装在部署包内，无需酒店再装数据库驱动等。

策略：
  1. 优先使用本机已有数据库驱动（若旧系统已装过引擎，直接可用）
  2. 若无：静默安装部署包内配套程序（需管理员一次）
  3. 仍失败：用内置工具将数据转为轻量格式（免安装，推荐兜底）
"""

from __future__ import annotations

import os
import platform
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from deploy_paths import bundled_path, get_deploy_root


def _is_64bit() -> bool:
    return struct.calcsize("P") * 8 == 64


def list_access_odbc_drivers() -> List[str]:
    try:
        import pyodbc
        return [d for d in pyodbc.drivers() if "Access" in d or "ACE" in d or "Jet" in d]
    except ImportError:
        return []


def access_driver_ok() -> bool:
    return bool(list_access_odbc_drivers())


def find_bundled_ace_installer() -> Optional[Path]:
    arch = "X64" if _is_64bit() else "X86"
    candidates = [
        bundled_path("redist", "access", f"AccessDatabaseEngine_{arch}.exe"),
        bundled_path("redist", "access", "AccessDatabaseEngine.exe"),
        bundled_path("redist", f"AccessDatabaseEngine_{arch}.exe"),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def install_bundled_access_engine(*, passive: bool = True) -> Tuple[bool, str]:
    """
    可静默安装部署包内的数据库引擎。
    passive=True 时无需点下一步，但仍可能弹出 UAC（需管理员）。
    """
    exe = find_bundled_ace_installer()
    if not exe:
        return False, "部署包内未找到数据库引擎安装程序"
    flag = "/passive" if passive else "/quiet"
    try:
        r = subprocess.run(
            [str(exe), flag, "/norestart"],
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if access_driver_ok():
            return True, "Access 数据库引擎已就绪（部署包内置安装）"
        if r.returncode in (0, 3010, 1638):
            return access_driver_ok(), (
                "已执行内置安装程序；若仍不可用，请以管理员身份运行本程序或改用内置工具导入。"
            )
        return False, (r.stderr or r.stdout or f"安装退出码 {r.returncode}")[:500]
    except Exception as e:
        return False, str(e)


def mdbtools_bundled_ok() -> bool:
    try:
        from mdb_import_backend import mdbtools_available
        return mdbtools_available()
    except Exception:
        return False


def deploy_kit_status() -> Dict[str, Any]:
    ace = find_bundled_ace_installer()
    try:
        from mdb_import_backend import mdbtools_available, _access_parser_available
        mdb_bundled = mdbtools_available()
        parser_ok = _access_parser_available()
    except Exception:
        mdb_bundled = False
        parser_ok = False
    return {
        "deploy_root": str(get_deploy_root()),
        "access_driver": access_driver_ok(),
        "access_drivers": list_access_odbc_drivers()[:3],
        "ace_installer_bundled": ace is not None,
        "ace_installer_path": str(ace) if ace else "",
        "mdbtools_bundled": mdb_bundled,
        "access_parser_available": parser_ok,
        "os_arch": "64" if _is_64bit() else "32",
    }


def ensure_hotel_runtime_deps(
    *,
    install_ace: bool = True,
    prefer_mdbtools: bool = False,
) -> Dict[str, Any]:
    """
    前台到场时调用：尽量零配置。
    prefer_mdbtools=True 时跳过 ACE 安装，直接用 mdbtools 或 access_parser（无需管理员）。
    """
    report: Dict[str, Any] = {
        "ok": False,
        "method": "",
        "messages": [],
    }

    if access_driver_ok():
        report["ok"] = True
        report["method"] = "odbc_existing"
        report["messages"].append("本机已有数据库驱动，可直接读取")
        return report

    if prefer_mdbtools:
        if mdbtools_bundled_ok():
            report["ok"] = True
            report["method"] = "mdbtools"
            report["messages"].append("使用内置转换工具（无需安装数据库驱动）")
            return report
        if _access_parser_available():
            report["ok"] = True
            report["method"] = "access_parser"
            report["messages"].append("使用 Python 读取库解析数据（无需安装数据库驱动）")
            return report

    if install_ace and find_bundled_ace_installer():
        ok, msg = install_bundled_access_engine(passive=True)
        report["messages"].append(msg)
        if ok and access_driver_ok():
            report["ok"] = True
            report["method"] = "ace_bundled"
            return report

    if mdbtools_bundled_ok():
        report["ok"] = True
        report["method"] = "mdbtools"
        report["messages"].append("将使用内置 mdbtools 读取 MDB（行业部署包标准兜底）")
        return report

    if _access_parser_available():
        report["ok"] = True
        report["method"] = "access_parser"
        report["messages"].append("将使用 Python 读取库解析数据（纯 Python 免安装）")
        return report

    report["messages"].append(
        "部署包不完整：缺少 _deploy_deps/mdbtools 或 redist/access 安装程序。"
        "请使用完整版部署盘。"
    )
    return report


def _access_parser_available() -> bool:
    try:
        import access_parser  # noqa
        return True
    except ImportError:
        return False


def format_deps_report(report: Dict[str, Any]) -> str:
    lines = ["── Solid 现场组件 ──"]
    st = deploy_kit_status()
    lines.append(f"  [{'✓' if st['access_driver'] else '○'}] Access ODBC 驱动")
    if st["access_drivers"]:
        lines.append(f"      {', '.join(st['access_drivers'])}")
    lines.append(
        f"  [{'✓' if st['ace_installer_bundled'] else '○'}] 内置 ACE 安装包"
        + (f" ({st['ace_installer_path']})" if st["ace_installer_bundled"] else "")
    )
    lines.append(f"  [{'✓' if st['mdbtools_bundled'] else '○'}] 内置 mdbtools（位于 _deploy_deps/mdbtools）")
    lines.append(f"  [{'✓' if st['access_parser_available'] else '○'}] Python 纯读取库")
    lines.append(f"  系统位数: {st['os_arch']} 位")
    for m in report.get("messages", []):
        lines.append(f"  · {m}")
    if report.get("ok"):
        lines.append(f"\n✅ 可用方式: {report.get('method', '')}")
    else:
        lines.append("\n⚠️ 请使用完整部署包或联系技术支持。")
    return "\n".join(lines)
