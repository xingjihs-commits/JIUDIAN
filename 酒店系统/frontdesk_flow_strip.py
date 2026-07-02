"""前台 SOP 流程条 — 重设计：编号圆圈 + 文字标签，步骤状态一目了然。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from design_tokens import _p
from frontdesk_ui import (
    FD_MARGIN, FD_SPACE_SM, FD_SPACE_MD,
    fd_section_bar, fd_section_title,
)
from i18n import i18n
from theme_palette import _hex_to_rgba
from event_bus import bus


class FrontdeskFlowStrip(QFrame):
    """可视化 SOP；当前步骤高亮（金色圆），已完成步骤绿圆，待完成灰圆。"""

    STEP_KEYS = ("fd_flow_pick", "fd_flow_pay", "fd_flow_card", "fd_flow_checkin")
    STEP_NUMS = ("①", "②", "③", "④")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FrontdeskFlowStrip")
        self.setFixedHeight(50)

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(FD_MARGIN, 6, FD_MARGIN, 0)
        main_lay.setSpacing(0)

        step_row = QHBoxLayout()
        step_row.setSpacing(FD_SPACE_SM)

        self._circles: list[QLabel] = []
        self._step_lbls: list[QLabel] = []
        self._arrows: list[QLabel] = []

        for i, (key, num) in enumerate(zip(self.STEP_KEYS, self.STEP_NUMS)):
            if i > 0:
                arrow = QLabel("›")
                arrow.setObjectName("FdFlowArrow")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                arrow.setFixedWidth(16)
                self._arrows.append(arrow)
                step_row.addWidget(arrow)

            step_widget = QWidget()
            step_widget.setObjectName("FdFlowStep")
            step_lay = QHBoxLayout(step_widget)
            step_lay.setContentsMargins(0, 0, 0, 0)
            step_lay.setSpacing(5)

            circle = QLabel(num)
            circle.setObjectName("FdFlowCircle")
            circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            circle.setFixedSize(26, 26)
            self._circles.append(circle)

            step_lbl = QLabel(i18n.t(key))
            step_lbl.setObjectName("FdFlowLabel")
            step_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self._step_lbls.append(step_lbl)

            step_lay.addWidget(circle)
            step_lay.addWidget(step_lbl)
            step_row.addWidget(step_widget)

        step_row.addStretch(1)
        main_lay.addLayout(step_row)

        # ── 金线品牌装饰线（底边 1px, 40% 透明度）──
        self._gold_line = QFrame(self)
        self._gold_line.setObjectName("FdFlowStripGoldLine")
        self._gold_line.setFixedHeight(1)
        main_lay.addWidget(self._gold_line)

        # 缓存当前状态，用于主题切换刷新
        self._cached_state = dict(current=0, room_selected=False, paid=False, card_issued=False, checked_in=False)
        bus.theme_changed.connect(self._refresh_theme)
        self.set_state(0, room_selected=False, paid=False, card_issued=False, checked_in=False)

    def set_state(
        self,
        current: int,
        *,
        room_selected: bool,
        paid: bool,
        card_issued: bool,
        checked_in: bool,
    ) -> None:
        self._cached_state = dict(current=current, room_selected=room_selected, paid=paid, card_issued=card_issued, checked_in=checked_in)
        done = [room_selected, paid, card_issued, checked_in]

        for i, (circle, lbl) in enumerate(zip(self._circles, self._step_lbls)):
            if done[i]:
                state = "done"
                color = _p("amount_positive")
            elif i == current:
                state = "current"
                color = _p("gold_thread")
            else:
                state = "pending"
                color = _p("text_dim")

            circle.setProperty("flowState", state)
            lbl.setProperty("flowState", state)
            circle.style().unpolish(circle)
            circle.style().polish(circle)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

            # 主题感知色彩覆盖 — _p() token 驱动
            bg = _hex_to_rgba(color, 0.25)
            circle.setStyleSheet(f"""
                QLabel#FdFlowCircle {{
                    color: {color};
                    border: 1.5px solid {color};
                    border-radius: 13px;
                    background: {bg};
                }}
            """)
            lbl.setStyleSheet(f"color: {color};")

        for j, arrow in enumerate(self._arrows):
            arrow_state = "done" if done[j] else "pending"
            arrow.setProperty("flowState", arrow_state)
            arrow.style().unpolish(arrow)
            arrow.style().polish(arrow)
            # 金线品牌色箭头
            arrow.setStyleSheet(f"color: {_p('gold_thread')};")

        # 金线装饰线
        gt = _p("gold_thread")
        self._gold_line.setStyleSheet(f"background: {_hex_to_rgba(gt, 0.4)};")

    def _refresh_theme(self, *args):
        """主题切换时刷新流程条颜色。"""
        self.set_state(**self._cached_state)
