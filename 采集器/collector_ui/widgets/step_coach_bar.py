"""
step_coach_bar.py — 9 步操作教练条

固定顶部的操作指引条：步骤编号 + 进度条 + 当前位置 + 行动提示 + 细节提示。
从 CollectorWizard 中抽取，减少主窗口复杂度。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QWidget,
)

from ..constants import PALETTE


class StepCoachBar(QFrame):
    """固定顶部操作教练条。

    Signals: 无（纯展示组件，状态由外部 set_state() 驱动）
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StepCoachBar")
        self._highlight_timer: QTimer | None = None
        self._build_ui()

    # ── 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 14, 20, 14)
        vl.setSpacing(6)

        # 行1: 步骤编号 + 进度条 + 位置标签
        row1 = QHBoxLayout()
        self._step_label = QLabel("步骤 1 / 9")
        self._step_label.setStyleSheet(
            "font-size:13px; font-weight:700; color:%s;" % PALETTE["text"])
        row1.addWidget(self._step_label)
        row1.addSpacing(12)

        self._progress = QProgressBar()
        self._progress.setFixedHeight(4)
        self._progress.setMinimumWidth(120)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 9)
        self._progress.setValue(0)
        self._progress.setStyleSheet(
            "QProgressBar {background:%s; border:none; border-radius:2px;} "
            "QProgressBar::chunk {background:%s; border-radius:2px;}" %
            (PALETTE["border"], PALETTE["green"]))
        row1.addWidget(self._progress)
        row1.addStretch()

        self._location = QLabel()
        row1.addWidget(self._location)
        vl.addLayout(row1)

        # 行2: 主要行动提示
        self._action = QLabel("请填写原厂安装目录，点「开始扫描」")
        self._action.setWordWrap(True)
        self._action.setStyleSheet(
            "font-size:20px; font-weight:700; color:%s;" % PALETTE["text"])
        vl.addWidget(self._action)

        # 行3: 细节提示
        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(
            "font-size:13px; color:%s;" % PALETTE["muted"])
        vl.addWidget(self._detail)

        self._set_normal_style()

    # ── 状态更新 ─────────────────────────────────────────

    def set_state(self, state) -> None:
        """从 step_coach.resolve_step_coach() 返回的 state 更新 UI。"""
        self._step_label.setText(
            f"步骤 {state.step_index} / {state.step_total} · {state.title}")
        self._progress.setValue(state.step_index)

        if state.is_oem_pause:
            self._set_oem_style()
            self._location.setText("[请去原厂门锁软件]")
            self._location.setStyleSheet(
                "font-size:12px; font-weight:700; color:%s;" % PALETTE["warn"])
        else:
            self._set_normal_style()
            self._location.setText("🔧 [Solid 工具内]")
            self._location.setStyleSheet(
                "font-size:12px; font-weight:600; color:%s; "
                "padding:2px 8px; border-radius:4px;" % PALETTE["primary"])

        action_size = "16px" if state.bridge_blocked else "20px"
        action_color = PALETTE["warn"] if state.bridge_blocked else PALETTE["text"]
        self._action.setText(state.action)
        self._action.setStyleSheet(
            f"font-size:{action_size}; font-weight:700; color:{action_color};")

        parts = []
        if state.hardware_hint:
            parts.append(state.hardware_hint)
        if state.why_hint:
            parts.append(state.why_hint)
        if state.next_hint:
            parts.append(f"下一步：{state.next_hint}")
        self._detail.setText(" · ".join(parts))

    # ── 样式切换 ─────────────────────────────────────────

    def _set_normal_style(self):
        self.setStyleSheet(
            "QFrame#StepCoachBar { background:%s; "
            "border-bottom:1px solid %s; }" %
            (PALETTE["bg_alt"], PALETTE["border"]))

    def _set_oem_style(self):
        self.setStyleSheet(
            "QFrame#StepCoachBar { background:#FFF7ED; "
            "border-bottom:2px solid #F97316; }")

    # ── 高亮目标控件 ─────────────────────────────────────

    def highlight_target(self, widget: QWidget):
        """滚动到目标控件并短暂高亮。"""
        if not widget or not widget.isVisible():
            return
        # 需要找到外层 scroll area 来 ensureWidgetVisible
        scroll = widget.parent()
        while scroll and not scroll.inherits("QScrollArea"):
            scroll = scroll.parent()
        if scroll:
            scroll.ensureWidgetVisible(widget, 80)

        base_style = widget.styleSheet() or ""
        widget.setProperty("coach_base_style", base_style)
        widget.setStyleSheet(
            base_style + " QPushButton, QComboBox { "
            "border: 2px solid #2563EB; border-radius: 8px; }")
        if self._highlight_timer:
            self._highlight_timer.stop()
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)

        def _restore():
            saved = widget.property("coach_base_style")
            if saved is not None:
                widget.setStyleSheet(str(saved))

        self._highlight_timer.timeout.connect(_restore)
        self._highlight_timer.start(2000)
