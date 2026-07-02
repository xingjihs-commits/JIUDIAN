# -*- mode: python ; coding: utf-8 -*-
#
# SolidCollector.spec — U盘万能采集工具打包配置
#
# 物理目录名是「采集器」，但 Python 包名是 collector。
# 打包前镜像到 build/_pkg_staging/collector/，让 PyInstaller 能正确收录模块。

import shutil
from pathlib import Path
from typing import Optional

HERE = Path(SPECPATH).resolve()

STAGING = HERE / "build" / "_pkg_staging"
PKG_DIR = STAGING / "collector"
if STAGING.exists():
    shutil.rmtree(STAGING)
STAGING.mkdir(parents=True)

_IGNORE = shutil.ignore_patterns(
    "build",
    "dist",
    "learned_profiles",
    "logs",
    "*.spec",
    "bridge32.exe",
    "pyi_rth_collector.py",
    "collector_main.py",
    "__pycache__",
    "*.pyc",
    "*.exe",
)
shutil.copytree(HERE, PKG_DIR, ignore=_IGNORE)

# 关键：staging 目录下 collector 必须要有 __init__.py，否则 PyInstaller 不认它是合法包
_PKG_INIT = PKG_DIR / "__init__.py"
if not _PKG_INIT.exists():
    _PKG_INIT.write_text("# collector 虚拟包入口 — 由 spec 自动生成，使 PyInstaller 正确收录子模块\n", encoding="utf-8")

# ── 全能力升级模块（惰性导入，缺模块不挡启动） ──
def _upgrade_module_path(here: Path, full_name: str) -> Optional[Path]:
    """把 'collector.bridgecore.xxx' 或 'collector.ghidra_toolkit.xxx'
    换成 here 目录下的绝对路径，用于判断文件/包是否存在。"""
    parts = full_name.split(".")
    if parts[0] == "collector":
        parts = parts[1:]  # 去掉 collector 前缀，剩下例如 ['bridgecore', 'ghidra_enricher']
    if not parts:
        return None
    rel = Path(*parts[:-1]) / f"{parts[-1]}.py"
    p = here / rel
    # 如果文件不存在，尝试作为包（目录 + __init__.py）
    if not p.is_file():
        pkg_init = here / Path(*parts) / "__init__.py"
        if pkg_init.is_file():
            return pkg_init
    return p if p.is_file() else None

_UPGRADE_IMPORTS = []
_upgrade_mods = [
    "collector.bridgecore.ghidra_enricher",
    "collector.bridgecore.dll_string_scanner",
    "collector.bridgecore.clue_hunter",
    "collector.bridgecore.mifare_weak_keys",
    "collector.bridgecore.encryption_fingerprints",
    "collector.bridgecore.parasitic_replay",
    "collector.bridgecore.experience_engine",
    "collector.bridgecore.forensic_packager",
    "collector.ghidra_toolkit",
    "collector.ghidra_toolkit.ghidra_finder",
    "collector.ghidra_toolkit.ghidra_scanner",
    "collector.ghidra_toolkit.scripts.auto_ghidra_scan",
]
# 仅添加存在的模块（参考版升级后可能缺某些文件）
for m in _upgrade_mods:
    if _upgrade_module_path(HERE, m):
        _UPGRADE_IMPORTS.append(m)

a = Analysis(
    [str(HERE / "collector_main.py")],
    pathex=[str(STAGING)],
    binaries=[],
    datas=[
        (str(HERE / "bridge32.exe"), "."),
        (str(HERE / "assets"), "assets"),
        (str(HERE / "known_signatures.json"), "."),
        (str(HERE / "tools" / "v9rfl_proxy" / "README.md"), "tools/v9rfl_proxy"),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtWidgets",
        "PySide6.QtGui",
        "collector.collector_ui",
        "collector.collector_ui.constants",
        "collector.collector_ui.models",
        "collector.collector_ui.workers",
        "collector.collector_ui.wizard",
        "collector.collector_ui.widgets",
        "collector.collector_ui.widgets.step_coach_bar",
        "collector.collector_ui.widgets.graduation_panel",
        "collector.collector_ui.widgets.identify_panel",
        "collector.collector_ui.widgets.sample_panel",
        "collector.collector_ui.widgets.handover_panel",
        "collector.collector_bridge",
        "collector.forensic_schema",
        "collector.filesystem_scanner",
        "collector.process_monitor",
        "collector.ui_workflow",
        "collector.apdu_sniffer",
        "collector.change_monitor",
        "collector.step_coach",
        "collector.bridgecore",
        "collector.bridgecore.operator_lib",
        "collector.bridgecore.protocol_learner",
        "collector.bridgecore.profile_generator",
        "collector.bridgecore.analysis_types",
        "collector.bridgecore.room_exporter",
        "collector.bridgecore.brand_analyzer",
        "collector.bridgecore.dll_probe",
        "collector.bridgecore.profile_merger",
        "collector.bridgecore.handover_package",
        "collector.bridgecore.handover_packager",
        "collector.bridgecore.path_prober",
        "collector.bridgecore.handover_assembler",
        "collector.bridgecore.identity_engine",
        "collector.bridgecore.oem_process",
        "collector.bridgecore.graduation_coach",
        "collector.bridgecore.serial_channel",
        "collector.bridgecore.token_recorder",
        "collector.bridgecore.protocol_verifier",
        "collector.bridgecore.keepalive",
        "collector.bridgecore.config",
        "collector.bridgecore.observer",
        "collector.bridgecore.fault_manager",
        "collector.bridgecore.injector",
        "collector.bridgecore.rx_monitor",
        "collector.bridgecore.orchestrator",
        "collector.bridgecore.protocol_processor",
        "collector.bridgecore.physical_channel",
        "collector.bridgecore.dll_prober",
        "collector.bridgecore.takeover_wizard",
        "collector.bridgecore.panic_recovery",
        "collector.bridgecore.proxy_log_parser",
        "collector.bridgecore.field_checklist",
        "collector.bridgecore.cloud_handover",
        "collector.bridgecore.task_fetcher",
        "collector.bridgecore.serial_protocol_learner",
        "collector.bridgecore.dll_sandbox",
        "collector.bridgecore.protocol_bruteforce",
        "collector.bridgecore.card_type_fuzzer",
        "collector.bridgecore.checksum_bruteforce",
        "collector.brand_assets",
        *[_ for _ in _UPGRADE_IMPORTS],
        "psutil",
        "pefile",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SolidCollector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[
        "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll",
        "Qt6OpenGL.dll", "Qt6Network.dll", "Qt6Svg.dll",
        "vcruntime140.dll", "vcruntime140_1.dll",
        "python311.dll", "python3.dll",
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(HERE / "assets" / "app_icon.ico") if (HERE / "assets" / "app_icon.ico").is_file() else None,
)
