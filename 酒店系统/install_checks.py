"""
install_checks.py — 安装前环境检测

检测项：
  1. 32 位 Python 环境（发卡桥接需要 32 位运行时）
  2. Microsoft Access Database Engine（老系统 MDB 迁移用）
  3. Windows 版本检查
  4. 磁盘空间检查
  5. 管理员权限检查
"""
from __future__ import annotations

import ctypes
import os
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── 阈值 ──
MIN_DISK_FREE_MB = 500


@dataclass
class PreflightResult:
    passed: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def merge(self, other: PreflightResult):
        self.passed = self.passed and other.passed
        self.warnings.extend(other.warnings)
        self.errors.extend(other.errors)
        self.details.update(other.details)


def check_admin() -> PreflightResult:
    """检查是否以管理员权限运行（安装程序可代为提权，但手动安装时建议检查）。"""
    result = PreflightResult()
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        is_admin = False
    result.details["is_admin"] = bool(is_admin)
    if not is_admin:
        result.warnings.append("未以管理员权限运行，安装过程可能需要提权（安装程序将自动请求）。")
    return result


def check_windows_version() -> PreflightResult:
    """检查 Windows 版本 ≥ Windows 7 SP1。"""
    result = PreflightResult()
    try:
        ver = sys.getwindowsversion()
        major, minor, build = ver.major, ver.minor, ver.build
    except Exception:
        result.errors.append("无法获取 Windows 版本。")
        result.passed = False
        return result

    result.details["windows"] = f"{major}.{minor}.{build}"
    if major < 6 or (major == 6 and minor < 1):
        result.errors.append(f"Windows 版本过低 ({major}.{minor})，需要 Windows 7 SP1 或更高。")
        result.passed = False
    return result


def check_python_32bit() -> PreflightResult:
    """检测是否存在 32 位 Python 环境（发卡桥接程序依赖）。"""
    result = PreflightResult()
    paths_to_check = [
        Path("C:/Python312-32"),
        Path("C:/Python311-32"),
        Path("C:/Python310-32"),
        Path("C:/Python39-32"),
        Path("C:/Python38-32"),
    ]

    # 同时检查系统 PATH 中的 python（32-bit 版 pointer size = 4）
    try:
        out = subprocess.check_output(
            ["python", "-c", "import struct; print(struct.calcsize('P'))"],
            timeout=5, stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if out == "4":
            result.details["python_32_path"] = "PATH: python"
            return result
    except Exception:
        pass

    for p in paths_to_check:
        py_exe = p / "python.exe"
        if py_exe.exists():
            try:
                out = subprocess.check_output(
                    [str(py_exe), "-c", "import struct; print(struct.calcsize('P'))"],
                    timeout=5, stderr=subprocess.DEVNULL,
                    text=True,
                ).strip()
                if out == "4":
                    result.details["python_32_path"] = str(p)
                    return result
            except Exception:
                continue

    result.warnings.append(
        "未检测到 32 位 Python 环境。发卡桥接程序依赖 32 位 Python 运行时。"
        "若本酒店不通过 USB 发卡器直接发卡，可忽略此警告。"
    )
    return result


def check_access_engine() -> PreflightResult:
    """检测 Microsoft Access Database Engine（用于老系统 MDB/ACCDB 迁移）。

    检查方式：
      1. 注册表 HKLM\SOFTWARE\Microsoft\Office\ClickToRun\Configuration → version_to_report
      2. 注册表 HKLM\SOFTWARE\Microsoft\Office\16.0\Access Connectivity Engine
      3. 文件 C:\Program Files\Common Files\microsoft shared\OFFICE16\ACEOLEDB.DLL
    """
    import sys as _sys
    import platform as _plat
    result = PreflightResult()
    found = False

    # 方法 1: ClickToRun 注册表
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration",
        )
        try:
            _val, _ = winreg.QueryValueEx(key, "version_to_report")
            found = True
        except OSError:
            pass
        winreg.CloseKey(key)
    except OSError:
        pass
    except ImportError:
        pass

    # 方法 2: Access Connectivity Engine 注册表
    if not found:
        try:
            import winreg
            is_64bit_app = _plat.architecture()[0] == '64bit'
            access_flag = winreg.KEY_WOW64_32KEY if is_64bit_app else 0
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Office\16.0\Access Connectivity Engine",
                0,
                winreg.KEY_READ | access_flag,
            )
            winreg.CloseKey(key)
            found = True
        except OSError:
            pass
        except ImportError:
            pass

    # 方法 3: 直接检查 DLL
    if not found:
        import deploy_paths
        base = os.environ.get("CommonProgramFiles", os.path.join(deploy_paths.program_files(), "Common Files"))
        ace_dll = Path(base) / "microsoft shared" / "OFFICE16" / "ACEOLEDB.DLL"
        if ace_dll.exists():
            found = True

    result.details["access_engine_found"] = found
    if not found:
        result.warnings.append(
            "未检测到 Microsoft Access Database Engine。"
            "如需从老系统的 MDB/ACCDB 文件迁移数据，请安装 "
            "Access Database Engine 2016 Redistributable。"
            "仅 SQLite 迁移无需此组件。"
        )
    return result


def check_disk_space(target_dir: str = None) -> PreflightResult:
    """检查目标磁盘剩余空间 ≥ MIN_DISK_FREE_MB。"""
    result = PreflightResult()
    try:
        if target_dir:
            p = Path(target_dir)
        else:
            p = Path(os.environ.get("SystemDrive", "C:")) / ""
    except Exception:
        p = Path("C:") / ""

    try:
        usage = shutil.disk_usage(str(p))
        free_mb = usage.free / (1024 * 1024)
    except OSError:
        result.errors.append(f"无法检查磁盘 {p.drive} 的剩余空间。")
        result.passed = False
        return result

    result.details["disk_free_mb"] = round(free_mb, 1)
    result.details["disk_target"] = str(p.drive)
    if free_mb < MIN_DISK_FREE_MB:
        result.errors.append(
            f"磁盘 {p.drive} 剩余空间不足（{free_mb:.0f} MB < {MIN_DISK_FREE_MB} MB）。"
        )
        result.passed = False
    return result


def run_all_checks(target_dir: str = None) -> PreflightResult:
    """依次运行所有环境检测，返回综合结果。"""
    final = PreflightResult()
    checks = [
        ("管理员权限", check_admin),
        ("Windows 版本", check_windows_version),
        ("32位 Python", check_python_32bit),
        ("Access 引擎", check_access_engine),
        ("磁盘空间", lambda: check_disk_space(target_dir)),
    ]
    for name, fn in checks:
        try:
            r = fn()
        except Exception as e:
            r = PreflightResult(passed=False, errors=[f"检测 {name} 时异常: {e}"])
        final.merge(r)

    return final


def format_report(result: PreflightResult) -> str:
    """格式化输出检测报告（简体中文）。"""
    lines = []
    lines.append("=" * 50)
    lines.append("  Solid PMS 安装前环境检测报告")
    lines.append("=" * 50)
    lines.append(f"  整体结果: {'✅ 通过' if result.passed else '❌ 未通过'}")
    lines.append(f"  错误数: {len(result.errors)} | 警告数: {len(result.warnings)}")
    lines.append("")

    if result.details:
        lines.append("  ── 检测详情 ──")
        for k, v in result.details.items():
            lines.append(f"    {k}: {v}")
        lines.append("")

    if result.errors:
        lines.append("  ── 错误 ──")
        for e in result.errors:
            lines.append(f"  ❌ {e}")
        lines.append("")

    if result.warnings:
        lines.append("  ── 警告 ──")
        for w in result.warnings:
            lines.append(f"  ⚠️ {w}")
        lines.append("")

    lines.append("=" * 50)
    return "\n".join(lines)


def cli_main():
    """命令行入口：python install_checks.py [目标目录]"""
    target = sys.argv[1] if len(sys.argv) > 1 else None
    result = run_all_checks(target)
    print(format_report(result))
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(cli_main())
