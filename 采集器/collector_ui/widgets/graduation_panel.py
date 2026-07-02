"""
graduation_panel.py — 毕业证据面板

7 维度毕业检核 + 进度条 + 核对读数 + Token 采集。
从 CollectorWizard 中抽取。
"""

from __future__ import annotations

from typing import Optional, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QWidget,
)

from ..constants import PALETTE, GRADUATION_DIMS


class GraduationPanel(QGroupBox):
    """毕业证据面板。

    Signals:
        readback_requested()     — 用户点「核对读数」
        token_collection_requested() — 用户点「采集授权卡 Token」
    """

    readback_requested = Signal()
    token_collection_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("毕业证据（自动更新）", parent)
        self.setObjectName("FdGhost")
        self._item_labels: list[QLabel] = []
        self._build_ui()

    # ── 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        gl = QVBoxLayout(self)
        gl.setSpacing(8)

        # 7 项标签（2行×4列网格）
        self._items_grid = QHBoxLayout()
        self._items_grid.setSpacing(4)
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        for i, (dim_id, title) in enumerate(GRADUATION_DIMS):
            lbl = QLabel("○ " + title)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                "font-size:10px; padding:3px 5px; border-radius:6px; "
                "color:%s; background:%s; "
                "border:1px solid %s;" %
                (PALETTE["muted"], PALETTE["bg_alt"], PALETTE["border_strong"]))
            if i < 4:
                row1.addWidget(lbl)
            else:
                row2.addWidget(lbl)
            self._item_labels.append(lbl)
        gl.addLayout(row1)
        gl.addLayout(row2)

        # 分数行 + 进度条 + 毕业状态牌
        score_row = QHBoxLayout()
        score_row.setSpacing(12)

        self._score = QLabel("0 / 7")
        self._score.setStyleSheet(
            "font-size:22px; font-weight:800; color:%s;" % PALETTE["muted"])
        score_row.addWidget(self._score)

        self._progress = QProgressBar()
        self._progress.setFixedHeight(6)
        self._progress.setMinimumWidth(160)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 7)
        self._progress.setValue(0)
        self._progress.setStyleSheet(
            "QProgressBar {background:%s; border:none; border-radius:3px;} "
            "QProgressBar::chunk {background:%s; border-radius:3px;}" %
            (PALETTE["border"], PALETTE["green"]))
        score_row.addWidget(self._progress)

        self._badge = QLabel("未毕业")
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.setFixedWidth(100)
        self._badge.setStyleSheet(
            "font-size:12px; font-weight:700; color:%s; "
            "background:%s; border:1px solid %s; "
            "border-radius:10px; padding:4px 8px;" %
            (PALETTE["muted"], PALETTE["warn_text_bg"], PALETTE["warn"]))
        score_row.addWidget(self._badge)
        score_row.addStretch()
        gl.addLayout(score_row)

        # next_action 提示
        self._next_action = QLabel("")
        self._next_action.setWordWrap(True)
        self._next_action.setStyleSheet(
            "font-size:14px; font-weight:600; color:%s; "
            "padding:8px 12px; background:%s; border:1px solid %s; "
            "border-radius:8px;" %
            (PALETTE["primary"], PALETTE["info_bg"], PALETTE["info_border"]))
        self._next_action.setVisible(False)
        gl.addWidget(self._next_action)

        # 核对读数按钮行
        readback_row = QHBoxLayout()
        self._readback_btn = QPushButton("核对读数（毕业验证）")
        self._readback_btn.setObjectName("SolidPrimaryBtn")
        self._readback_btn.clicked.connect(self.readback_requested.emit)
        readback_row.addWidget(self._readback_btn)
        self._readback_status = QLabel("")
        self._readback_status.setStyleSheet(
            "color:%s; font-size:12px;" % PALETTE["muted"])
        readback_row.addWidget(self._readback_status, 1)
        gl.addLayout(readback_row)

        # Token 采集按钮行
        token_row = QHBoxLayout()
        self._token_btn = QPushButton("采集授权卡 Token")
        self._token_btn.setObjectName("SolidSecondaryBtn")
        self._token_btn.clicked.connect(self.token_collection_requested.emit)
        token_row.addWidget(self._token_btn)
        self._token_status = QLabel("")
        self._token_status.setStyleSheet(
            "color:%s; font-size:12px;" % PALETTE["muted"])
        token_row.addWidget(self._token_status, 1)
        gl.addLayout(token_row)

        # 初始隐藏 Token 按钮（检测到 auth_token_repeat 时由外部显示）
        self._token_btn.setVisible(False)
        self._token_status.setVisible(False)

    # ── 状态更新 ─────────────────────────────────────────

    def update_state(self, state) -> None:
        """从 graduation_coach.evaluate() 返回的 state 更新面板。"""
        # 更新 7 项标签
        for i, item in enumerate(state.items):
            if i >= len(self._item_labels):
                break
            lbl = self._item_labels[i]
            if item.passed:
                lbl.setStyleSheet(
                    "font-size:11px; padding:4px 8px; border-radius:8px; "
                    "color:white; background:%s; font-weight:700; "
                    "border:none;" % PALETTE["green"])
                lbl.setText("✓ " + item.title)
            else:
                lbl.setStyleSheet(
                    "font-size:11px; padding:4px 8px; border-radius:8px; "
                    "color:%s; background:%s; "
                    "border:1px solid %s;" %
                    (PALETTE["muted"], PALETTE["bg_alt"], PALETTE["border_strong"]))
                lbl.setText(("○ " if item.required else "· ") + item.title)

        # 分数 + 进度条
        score_color = PALETTE["green"] if state.can_graduate else PALETTE["primary"]
        self._score.setText(f"{state.passed_count} / {state.required_count}")
        self._score.setStyleSheet(
            "font-size:22px; font-weight:800; color:%s;" % score_color)
        self._progress.setMaximum(state.required_count)
        self._progress.setValue(state.passed_count)

        # 毕业状态牌
        if state.can_graduate:
            self._badge.setText("✅ 可毕业")
            self._badge.setStyleSheet(
                "font-size:12px; font-weight:700; color:white; "
                "background:%s; border:none; "
                "border-radius:10px; padding:4px 8px;" % PALETTE["green"])
        else:
            self._badge.setText("⏳ 未毕业")
            self._badge.setStyleSheet(
                "font-size:12px; font-weight:700; color:%s; "
                "background:%s; border:1px solid %s; "
                "border-radius:10px; padding:4px 8px;" %
                (PALETTE["warn"], PALETTE["warn_text_bg"], PALETTE["warn"]))

        # next_action
        if state.next_action:
            self._next_action.setText(f"下一步：{state.next_action}")
            self._next_action.setVisible(True)
        else:
            self._next_action.setVisible(False)

    # ── Token 按钮可见性 ─────────────────────────────────

    def set_token_visible(self, visible: bool, collected: bool = False):
        """由主窗口根据分析结果控制 Token 采集按钮。"""
        self._token_btn.setVisible(visible)
        self._token_status.setVisible(visible)
        if visible and collected:
            self._token_status.setText("✅ Token 矩阵已采集")
            self._token_status.setStyleSheet(
                "color:%s; font-size:12px; font-weight:600;" % PALETTE["green"])
            self._token_btn.setEnabled(False)
            self._token_btn.setText("Token 已采集 ✓")
        elif visible:
            self._token_status.setText("⚠ 需要采集")
            self._token_status.setStyleSheet(
                "color:%s; font-size:12px;" % PALETTE["warn"])
            self._token_btn.setEnabled(True)
            self._token_btn.setText("采集授权卡 Token")

    # ── 核对读数状态 ─────────────────────────────────────

    def set_readback_loading(self):
        self._readback_btn.setEnabled(False)
        self._readback_btn.setText("读取中...")
        self._readback_status.setText("")

    def set_readback_result(self, ok: bool, msg: str, match: bool = False, fail_count: int = 0):
        self._readback_btn.setEnabled(True)
        self._readback_btn.setText("核对读数（毕业验证）")
        if ok and match:
            self._readback_status.setText(f"✅ 匹配已写卡 ({msg[:16]}...)")
            self._readback_status.setStyleSheet(
                "color:%s; font-size:12px; font-weight:600;" % PALETTE["green"])
        elif ok and not match:
            self._readback_status.setText(f"❌ 不匹配 ({msg[:16]}...)—已 {fail_count}/3 次")
            self._readback_status.setStyleSheet(
                "color:%s; font-size:12px; font-weight:600;" % PALETTE["danger"])
        else:
            self._readback_status.setText(f"读卡失败: {msg}")
            self._readback_status.setStyleSheet(
                "color:%s; font-size:12px;" % PALETTE["danger"])

    # ── Token 采集状态 ───────────────────────────────────

    def set_token_loading(self):
        self._token_btn.setEnabled(False)
        self._token_btn.setText("采集中...")
        self._token_status.setText("⏳ 正在发卡采集...")
        self._token_status.setStyleSheet(
            "color:%s; font-size:12px; font-weight:600;" % PALETTE["primary"])

    def set_token_result(self, ok: bool, msg: str, path: str = ""):
        self._token_btn.setEnabled(True)
        if ok:
            self.set_token_visible(True, collected=True)
        else:
            self._token_btn.setText("采集授权卡 Token")
            self._token_status.setText(f"❌ 采集失败: {msg}")
            self._token_status.setStyleSheet(
                "color:%s; font-size:12px; font-weight:600;" % PALETTE["danger"])

    @property
    def readback_btn(self) -> QPushButton:
        return self._readback_btn

    @property
    def token_btn(self) -> QPushButton:
        return self._token_btn
