# -*- coding: utf-8 -*-
"""部署包内置 USB 串口／发卡器驱动与 Access 引擎安装助手。"""
from __future__ import annotations

import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from deploy_paths import bundled_path
from i18n import i18n
from runtime_deps import access_driver_ok, find_bundled_ace_installer, install_bundled_access_engine
from ui_helpers import style_dialog, build_dialog_header, show_warning, show_info, ask_confirm
from design_tokens import _p

# VID -> chip label -> preferred installer basename (partial match)
VID_CHIP_MAP = {
    "1A86": ("CH340/CH341", "CH341"),
    "10C4": ("CP210x", "CP210"),
    "0403": ("FTDI", "CDM212"),
    "067B": ("PL2303", "PL2303"),
    "2341": ("Arduino", "CH341"),
    "1A40": ("USB-Serial", "CH341"),
}


@dataclass
class BundledInstaller:
    path: Path
    label: str
    kind: str  # usb | access
    chip_hint: str = ""
    package: str = "exe"  # exe | zip-exe | zip-inf

    @property
    def exists(self) -> bool:
        return self.path.is_file() and self.path.stat().st_size > 20_000


DRIVER_LABELS = {
    "CH341": ("CH340/CH341 沁恒（国产发卡器最常见）", "exe"),
    "CP210": ("CP210x Silicon Labs", "zip-inf"),
    "CDM212": ("FTDI VCP", "exe"),
    "PL2303": ("PL2303 Prolific（老款）", "exe"),
}


def _classify_pkg(name: str) -> tuple[str, str, str]:
    upper = name.upper()
    for key, (label, default_pkg) in DRIVER_LABELS.items():
        if key in upper:
            if upper.endswith(".ZIP"):
                pkg = default_pkg if default_pkg.startswith("zip") else "zip-exe"
            else:
                pkg = "exe"
            return key, label, pkg
    return name, name, "exe" if upper.endswith(".EXE") else "zip-exe"


def list_bundled_drivers() -> List[BundledInstaller]:
    items: List[BundledInstaller] = []
    drv_dir = bundled_path("redist", "drivers")
    if drv_dir.is_dir():
        for f in sorted(list(drv_dir.glob("*.exe")) + list(drv_dir.glob("*.zip"))):
            key, label, pkg = _classify_pkg(f.name)
            items.append(
                BundledInstaller(
                    path=f,
                    label=label,
                    kind="usb",
                    chip_hint=key,
                    package=pkg,
                )
            )
    ace = find_bundled_ace_installer()
    if ace:
        items.append(
            BundledInstaller(
                path=ace,
                label="Microsoft Access 数据库引擎（读旧版 CardLock.mdb）",
                kind="access",
            )
        )
    return items


def _hwid_from_port(port: str) -> str:
    try:
        import serial.tools.list_ports

        for p in serial.tools.list_ports.comports():
            if p.device == port:
                return (p.hwid or "").upper()
    except Exception:
        pass
    return ""


def detect_serial_chip(port: str = "") -> str:
    """根据 COM 口或任意已连接设备的 VID 推断芯片类型。"""
    if port:
        hw = _hwid_from_port(port)
        for vid, (chip, _) in VID_CHIP_MAP.items():
            if f"VID_{vid}" in hw:
                return chip
    try:
        import serial.tools.list_ports

        for p in serial.tools.list_ports.comports():
            hw = (p.hwid or "").upper()
            for vid, (chip, _) in VID_CHIP_MAP.items():
                if f"VID_{vid}" in hw:
                    return chip
    except Exception:
        pass
    return ""


def suggest_installer_for_chip(chip: str) -> Optional[BundledInstaller]:
    chip_u = (chip or "").upper()
    for inst in list_bundled_drivers():
        if inst.kind != "usb":
            continue
        if inst.chip_hint and inst.chip_hint.upper() in chip_u:
            return inst
        if inst.chip_hint and inst.chip_hint.upper() in inst.path.name.upper():
            return inst
    return None


def list_serial_ports_detailed() -> List[Tuple[str, str, str]]:
    """(device, description, hwid)"""
    out: List[Tuple[str, str, str]] = []
    try:
        import serial.tools.list_ports

        for p in serial.tools.list_ports.comports():
            out.append((p.device, p.description or "", p.hwid or ""))
    except ImportError:
        pass
    return out


def needs_usb_driver_hint() -> Tuple[bool, str]:
    """
    是否应向用户提示安装内置驱动。
    返回 (需要提示, 原因说明)。
    """
    ports = list_serial_ports_detailed()
    if not ports:
        bundled = [d for d in list_bundled_drivers() if d.kind == "usb" and d.exists]
        if bundled:
            return True, i18n.t("usb_drv_reason_no_ports")
        return False, ""

    for _dev, _desc, hwid in ports:
        hw = (hwid or "").upper()
        for vid, (chip, _) in VID_CHIP_MAP.items():
            if f"VID_{vid}" in hw:
                inst = suggest_installer_for_chip(chip)
                if inst and inst.exists:
                    return True, i18n.t("usb_drv_reason_chip").format(chip=chip)
    return False, ""


def _run_exe(exe: Path) -> Tuple[bool, str]:
    try:
        subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
        )
        return True, ""
    except Exception as e:
        return False, str(e)


def _install_inf_via_pnputil(inf_dir: Path) -> Tuple[bool, str]:
    infs = list(inf_dir.rglob("*.inf"))
    if not infs:
        return False, "no .inf file in extracted package"
    ok_any = False
    msgs: List[str] = []
    for inf in infs:
        try:
            r = subprocess.run(
                ["pnputil", "/add-driver", str(inf), "/install"],
                capture_output=True,
                text=True,
                timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
            )
            out = (r.stdout or "") + (r.stderr or "")
            msgs.append(f"{inf.name}: rc={r.returncode}")
            if r.returncode == 0:
                ok_any = True
            elif "需要提升" in out or "elevation" in out.lower() or r.returncode == 5:
                msgs.append("需要以管理员身份运行 Solid 才能安装 INF 驱动。")
        except Exception as e:
            msgs.append(f"{inf.name}: {e}")
    return ok_any, "\n".join(msgs)


def run_installer(inst: BundledInstaller, parent: Optional[QWidget] = None) -> Tuple[bool, str]:
    if not inst.exists:
        return False, i18n.t("usb_drv_missing_file").format(name=inst.path.name)

    if inst.kind == "access":
        if access_driver_ok():
            return True, i18n.t("usb_drv_ace_already")
        ok, msg = install_bundled_access_engine(passive=True)
        return ok, msg

    if inst.package == "exe":
        ok, err = _run_exe(inst.path)
        if not ok:
            return False, err
        return True, i18n.t("usb_drv_launched").format(name=inst.label)

    extract_dir = Path(tempfile.mkdtemp(prefix="solid_drv_"))
    try:
        with zipfile.ZipFile(inst.path) as zf:
            zf.extractall(extract_dir)
    except Exception as e:
        return False, f"解压失败：{e}"

    if inst.package == "zip-exe":
        exes = sorted(extract_dir.rglob("SETUP.EXE")) + sorted(extract_dir.rglob("setup.exe"))
        if not exes:
            exes = sorted(extract_dir.rglob("*.exe"))
        if not exes:
            return False, "包内未找到 SETUP.EXE"
        ok, err = _run_exe(exes[0])
        if not ok:
            return False, err
        return True, i18n.t("usb_drv_launched").format(name=inst.label)

    ok, msg = _install_inf_via_pnputil(extract_dir)
    if ok:
        return True, i18n.t("usb_drv_inf_installed").format(name=inst.label) + "\n" + msg
    return False, msg or "INF 安装失败，请以管理员身份运行 Solid 后重试。"


class BundledInstallersDialog(QDialog):
    """列出 redist 内所有可安装项，一键启动安装程序。"""

    def __init__(self, parent: Optional[QWidget] = None, *, chip_hint: str = ""):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("usb_drv_dialog_title"))
        style_dialog(self, size="medium")
        self._chip_hint = chip_hint

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        lay.addWidget(
            build_dialog_header(
                i18n.t("usb_drv_dialog_h1"),
                i18n.t("usb_drv_dialog_sub"),
            )
        )
        if chip_hint:
            tip = QLabel(i18n.t("usb_drv_dialog_chip").format(chip=chip_hint))
            tip.setWordWrap(True)
            tip.setStyleSheet(
                f"color:{_p('accent')}; background:{_p('surface_alt')}; border:1px solid {_p('accent')}; "
                "border-radius:8px; padding:10px;"
            )
            lay.addWidget(tip)

        self._host = QWidget()
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setSpacing(8)
        lay.addWidget(self._host, 1)

        note = QLabel(i18n.t("usb_drv_dialog_note"))
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{_p('text_muted')}; font-size:11px;")
        lay.addWidget(note)

        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton(i18n.t("takeover_hub_close"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

        self._rebuild()

    def _rebuild(self) -> None:
        while self._host_lay.count():
            item = self._host_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        items = list_bundled_drivers()
        if not items:
            self._host_lay.addWidget(QLabel(i18n.t("usb_drv_none_bundled")))
            return

        for inst in items:
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            status = "✓" if self._is_installed(inst) else "○"
            lbl = QLabel(f"{status} {inst.label}\n{inst.path.name}")
            lbl.setWordWrap(True)
            row_l.addWidget(lbl, 1)
            btn = QPushButton(i18n.t("usb_drv_btn_install"))
            btn.setEnabled(inst.exists)
            btn.clicked.connect(lambda _=False, i=inst: self._on_install(i))
            row_l.addWidget(btn)
            self._host_lay.addWidget(row_w)

    def _is_installed(self, inst: BundledInstaller) -> bool:
        if inst.kind == "access":
            return access_driver_ok()
        return False

    def _on_install(self, inst: BundledInstaller) -> None:
        ok, msg = run_installer(inst, self)
        if ok:
            show_info(self, i18n.t("dlg_tip"), msg)
        else:
            show_warning(self, i18n.t("dlg_tip"), msg)
        self._rebuild()


def open_driver_install_dialog(parent: Optional[QWidget] = None, *, chip_hint: str = "") -> None:
    dlg = BundledInstallersDialog(parent, chip_hint=chip_hint)
    dlg.exec()


def offer_usb_driver_install(parent: Optional[QWidget], reason: str = "") -> bool:
    """若需要则弹窗询问；用户选「安装」返回 True。"""
    need, auto_reason = needs_usb_driver_hint()
    if not need and not reason:
        return False
    text = reason or auto_reason
    chip = detect_serial_chip()
    if ask_confirm(
        parent,
        i18n.t("usb_drv_offer_title"),
        i18n.t("usb_drv_offer_body").format(reason=text),
    ):
        open_driver_install_dialog(parent, chip_hint=chip)
        return True
    return False
