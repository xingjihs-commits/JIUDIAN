"""
handover_panel.py — 握手包生成面板

PMS 握手包生成 + 酒店名输入 + 保存位置 + 复制到 U 盘。
从 CollectorWizard 中抽取。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QProgressBar,
)

from ..constants import PALETTE


class HandoverPanel(QGroupBox):
    """握手包生成面板。

    Signals:
        build_requested()            — 用户点「生成握手包」/「重新生成」
        select_path_requested()      — 用户点「更改保存位置」
        copy_to_usb_requested()      — 用户点「复制到 U 盘」
    """

    build_requested       = Signal()
    select_path_requested = Signal()
    copy_to_usb_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("⑤ PMS 握手包 — 完成毕业才能生成", parent)
        self.setObjectName("FdGhost")
        self._build_ui()

    def _build_ui(self):
        hv = QVBoxLayout(self)
        hv.setSpacing(6)

        self._intro = QLabel(
            "分析+探测完成后，工具会自动生成 .solidhandover 文件。\n"
            "把文件拷到 U 盘，在 Solid PMS「厂家控制台 → 门锁 → 导入握手包」即可。")
        self._intro.setWordWrap(True)
        self._intro.setStyleSheet("color:%s; font-size:13px;" % PALETTE["text"])
        hv.addWidget(self._intro)

        self._hotel_name_i = QLineEdit()
        self._hotel_name_i.setPlaceholderText("酒店名（选填）")
        self._hotel_name_i.setStyleSheet(
            f"padding:6px 10px; font-size:13px; border:1px solid {PALETTE['border_strong']}; "
            f"border-radius:6px; color:{PALETTE['text']};")
        hv.addWidget(self._hotel_name_i)

        self._preview = QLabel("")
        self._preview.setWordWrap(True)
        self._preview.setStyleSheet(
            f"font-size:12px; font-family:Consolas,monospace; padding:8px; "
            f"background:{PALETTE['bg_alt']}; border:1px solid {PALETTE['border']}; "
            f"border-radius:8px; color:{PALETTE['text']}; white-space:pre;")
        self._preview.setVisible(False)
        hv.addWidget(self._preview)

        hp_row = QHBoxLayout()
        self._path_btn = QPushButton("更改保存位置（可选）")
        self._path_btn.setObjectName("FdGhostBtn")
        self._path_btn.clicked.connect(self.select_path_requested.emit)
        hp_row.addWidget(self._path_btn)
        self._path_lbl = QLabel("")
        self._path_lbl.setStyleSheet(f"color:{PALETTE['muted']}; font-size:12px;")
        hp_row.addWidget(self._path_lbl, 1)
        hv.addLayout(hp_row)

        self._pb = QProgressBar()
        self._pb.setVisible(False)
        self._pb.setTextVisible(False)
        self._pb.setFixedHeight(6)
        self._pb.setStyleSheet(
            f"QProgressBar {{background:{PALETTE['border']}; border:none; border-radius:3px;}} "
            f"QProgressBar::chunk {{background:{PALETTE['primary']}; border-radius:3px;}}")
        hv.addWidget(self._pb)

        self._build_btn = QPushButton("生成握手包")
        self._build_btn.setObjectName("SolidPrimaryBtn")
        self._build_btn.clicked.connect(self.build_requested.emit)
        self._build_btn.setEnabled(False)
        hv.addWidget(self._build_btn)

        self._result = QLabel("")
        self._result.setWordWrap(True)
        self._result.setStyleSheet("font-size:12px; padding:6px 10px; border-radius:6px;")
        self._result.setVisible(False)
        hv.addWidget(self._result)

        self._copy_btn = QPushButton("复制到 U 盘")
        self._copy_btn.setObjectName("FdGhostBtn")
        self._copy_btn.clicked.connect(self.copy_to_usb_requested.emit)
        self._copy_btn.setVisible(False)
        hv.addWidget(self._copy_btn)

    # ── 外部接口 ─────────────────────────────────────────

    def set_intro_graduated(self):
        self._intro.setText("毕业通过！可以生成 .solidhandover 握手包导入 PMS。")

    def set_intro_not_graduated(self):
        self._intro.setText("请完成所有必需项后再生成握手包。")

    def set_preview(self, text: str):
        self._preview.setText(text)
        self._preview.setVisible(True)

    def set_path_label(self, text: str):
        self._path_lbl.setText(text)

    def set_build_enabled(self, enabled: bool):
        self._build_btn.setEnabled(enabled)

    def set_build_loading(self):
        self._build_btn.setEnabled(False)
        self._build_btn.setText("生成中...")
        self._pb.setVisible(True)
        self._pb.setValue(0)
        self._result.setVisible(False)
        self._copy_btn.setVisible(False)

    def set_build_done(self, ok: bool, msg: str):
        self._pb.setVisible(False)
        if ok:
            parts = msg.split("\n", 2)
            filepath = parts[0]
            size_info = parts[1] if len(parts) > 1 else ""
            cloud_info = parts[2] if len(parts) > 2 else ""

            if cloud_info.startswith("已回传云端"):
                cloud_tag = f"\n☁ {cloud_info}"
                cloud_color = PALETTE["green"]
            elif cloud_info.startswith("仅本地") or cloud_info.startswith("已保存"):
                cloud_tag = f"\n⚠ {cloud_info}"
                cloud_color = PALETTE["warn"]
            else:
                cloud_tag = ""
                cloud_color = PALETTE["green"]

            import os
            self._result.setText(
                f"✅ 握手包已就绪，可直接导入 PMS\n\n"
                f"文件: {os.path.basename(filepath)}\n"
                f"位置: {os.path.dirname(filepath)}\n"
                f"大小: {size_info}"
                f"{cloud_tag}\n\n"
                f"下一步: Solid PMS → 厂家控制台 → 门锁 → 导入握手包\n"
                f"或点下方「复制到 U 盘」带走")
            self._result.setStyleSheet(
                f"color:{cloud_color}; font-size:13px; font-weight:600; "
                f"background:{PALETTE['green_bg']}; border:1px solid {PALETTE['green_border']}; "
                f"border-radius:4px; padding:8px 12px;")
            self._result.setVisible(True)
            self._copy_btn.setVisible(True)
            self._build_btn.setText("重新生成")
            self._build_btn.setEnabled(True)
        else:
            self._result.setText(f"❌ 生成失败: {msg}")
            self._result.setStyleSheet(
                f"color:{PALETTE['danger']}; font-size:13px; "
                f"background:{PALETTE['danger_bg']}; border:1px solid {PALETTE['danger_border']}; "
                f"border-radius:4px; padding:8px 12px;")
            self._result.setVisible(True)
            self._build_btn.setText("重试")
            self._build_btn.setEnabled(True)

    def set_usb_readonly_error(self, target_dir: str, desktop: str):
        """U 盘只读错误提示。"""
        import os
        self._result.setText(
            f"❌ U 盘只读（无写权限）\n目标: {target_dir}\n\n"
            f"请改用桌面生成，再手动拷贝到 U 盘：\n{desktop}")
        self._result.setStyleSheet(
            f"color:{PALETTE['danger']}; font-size:13px; "
            f"background:{PALETTE['danger_bg']}; border:1px solid {PALETTE['danger_border']}; "
            f"border-radius:4px; padding:8px 12px;")
        self._result.setVisible(True)

    def append_copy_info(self, dst_dir: str):
        self._result.setText(self._result.text() + f"\n已复制到: {dst_dir}")

    # ── 进度条 ───────────────────────────────────────────

    def set_progress(self, value: int):
        self._pb.setValue(value)

    # ── 属性 ─────────────────────────────────────────────

    @property
    def hotel_name_input(self) -> QLineEdit:
        return self._hotel_name_i

    @property
    def build_btn(self) -> QPushButton:
        return self._build_btn

    @property
    def progress_bar(self) -> QProgressBar:
        return self._pb
