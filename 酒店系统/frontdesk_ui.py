# [UI-REDESIGN] 2026-06-15 v3 重构: PC端紧凑度量 + 金线SectionBar + 三级按键体系
"""前台 UI 度量与辅助组件 — 6px 栅格，PC 端紧凑规范。

v3 核心变化:
- 间距全面收紧（6px 栅格，PC 端不浪费像素）
- 输入框高度 30px，宽度即字符宽
- 按键三级体系：主操作(LG 36px) / 卡片操作(MD 32px) / 低频操作(SM 28px)
- 新增 fd_section_bar() — 金线品牌横栏，全局统一
- 新增 fd_apply_card_action_btn() — 卡片操作按键（发卡/退房等，实色同色）
- 新增 fd_apply_low_freq_btn() — 低频操作按键（换房/备注等，浅色实色）
- 取消幽灵按键，所有按键实色
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QObject, QEvent
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from design_tokens import _p
from ui.layout.frontdesk import (
    BTN_COLORS,
    FD_BTN_H,
    FD_BTN_H_CRITICAL,
    FD_BTN_H_LOW,
    FD_BTN_H_PRIMARY,
    FD_BTN_MIN_W,
    FD_CARD_PADDING,
    FD_CARD_RADIUS,
    FD_CARD_SHADOW,
    FD_GOLD_THREAD_COLOR,
    FD_GOLD_THREAD_WIDTH,
    FD_INPUT_H,
    FD_LEDGER_ROW_H,
    FD_LEDGER_TABLE_HEADER_H,
    FD_SPACE_LG,
    FD_SPACE_MD,
    FD_SPACE_SM,
    FD_SPACE_XL,
    FD_SPACE_XS,
    INPUT_COLORS,
    ROOM_STATUS_COLORS,
)

# ── 本地扩展常量（v4 未覆盖，保留兼容）──────────────────────────
FD_SPACE = FD_SPACE_SM
FD_MARGIN = FD_SPACE_MD
# ContentBox 内边距 — 左 0 贴金线，避免 surface 浅条夹在金线与分区栏之间
FD_CONTENT_BOX_MARGINS = (0, FD_SPACE_MD, FD_SPACE_LG, FD_SPACE_MD)
FD_TOOLBAR_H = 48           # 工具栏高度（与 SmartHeader 一致）

FD_INPUT_MIN_W = 40         # 最小宽度
FD_LABEL_INPUT_GAP = FD_SPACE_XS  # 标签与输入框间距
FD_INPUT_GAP = FD_SPACE_SM  # 输入框之间间距

# ── 账本 ────────────────────────────────────────────────────────
FD_LEDGER_VISIBLE_ROWS = 5
FD_LEDGER_FILTER_H = 28

# ── SectionBar（金线横栏）───────────────────────────────────────
FD_SECTION_BAR_H = 36       # v7: 32→36 横栏标准高度
FD_SECTION_BAR_LEFT_BAR_W = FD_GOLD_THREAD_WIDTH
FD_SECTION_BAR_LEFT_MARGIN = 10  # 金线到标题的间距

# 底栏最小高度（流水+交班）；实际高度由 QSplitter 分配，可吃满剩余空间
FD_CHECKIN_LEDGER_DOCK_MIN = (
    FD_SPACE_MD + FD_SPACE_SM
    + FD_SECTION_BAR_H + FD_SPACE_SM + FD_LEDGER_FILTER_H + FD_SPACE_SM
    + FD_LEDGER_TABLE_HEADER_H + FD_LEDGER_ROW_H * FD_LEDGER_VISIBLE_ROWS
    + FD_SPACE_SM + FD_SPACE_MD
)
FD_CHECKIN_SHIFT_DOCK_MIN = 188
FD_CHECKIN_BOTTOM_DOCK_MIN = max(FD_CHECKIN_LEDGER_DOCK_MIN, FD_CHECKIN_SHIFT_DOCK_MIN)
# 兼容旧引用
FD_CHECKIN_BOTTOM_DOCK_H = FD_CHECKIN_BOTTOM_DOCK_MIN
FD_CHECKIN_BANNER_PAD_V = 8
FD_CHECKIN_RIGHT_PAD = FD_SPACE_MD
FD_CHECKIN_COMMIT_H = 36  # v7: 44→36 统一按钮高度
FD_CHECKIN_STICKY_FOOTER_H = FD_CHECKIN_COMMIT_H + 9

# fd_section_bar() 使用 FD_SECTION_BAR_*（见上）

# ── 输入框宽度映射（字符宽度 + padding）────────────────────────
FD_INPUT_WIDTHS = {
    "room_number": 60,      # 3-4位数字
    "guest_name": 100,      # 4-6个汉字
    "phone": 140,           # 8-11位数字
    "days": 45,             # 1-2位数字
    "price": 80,            # 3-5位数字
    "discount": 45,         # 1-2位数字
    "deposit": 80,          # 3-5位数字
}


class _NoWheelSpinFilter(QObject):
    """Prevent accidental amount changes when the mouse wheel crosses a SpinBox."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return False


def fd_card(parent=None) -> QFrame:
    """带卡片样式的内容分区。"""
    card = QFrame(parent)
    card.setObjectName("FdCard")
    return card


def fd_section_title(text: str, parent=None) -> QLabel:
    lbl = QLabel(text, parent)
    lbl.setObjectName("FdSectionTitle")
    return lbl


def fd_section_bar(
    title: str,
    parent=None,
    *,
    action_widgets: list = None,
    show_gold: bool = True,
) -> QFrame:
    """金线品牌横栏 — 全局统一分区标准。

    左侧金线（3px） + 标题文字 + 右侧可选操作控件
    所有页面的分区标题统一使用此组件，保持视觉一致性。

    Args:
        title: 分区标题文字
        action_widgets: 右侧放置的操作控件列表（如按钮、输入框）
        show_gold: False 时省略子金线（父面板已有 border-left 时用，防双线+细缝）
    """
    bar = QFrame(parent)
    bar.setObjectName("FdSectionBar")
    bar.setFixedHeight(FD_SECTION_BAR_H)

    lay = QHBoxLayout(bar)
    lay.setContentsMargins(0, 0, FD_MARGIN, 0)
    lay.setSpacing(FD_SECTION_BAR_LEFT_MARGIN if show_gold else FD_SPACE_SM)

    if show_gold:
        gold_line = QFrame(bar)
        gold_line.setObjectName("FdGoldLine")
        gold_line.setFixedWidth(FD_SECTION_BAR_LEFT_BAR_W)
        gold_line.setFixedHeight(FD_SECTION_BAR_H - 8)
        lay.addWidget(gold_line, 0, Qt.AlignmentFlag.AlignVCenter)
        from ui_surface import fd_apply_gold_line
        fd_apply_gold_line(gold_line)

    # 标题
    lbl = QLabel(title, bar)
    lbl.setObjectName("FdSectionBarTitle")
    lay.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)

    lay.addStretch(1)

    # 右侧操作控件
    if action_widgets:
        for w in action_widgets:
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)

    return bar


def fd_apply_toolbar_btn(btn: QPushButton, *, primary: bool = False, ghost: bool = False) -> None:
    """工具栏按钮（兼容旧调用，ghost 映射到低频样式）。"""
    if primary:
        btn.setMinimumHeight(FD_BTN_H_PRIMARY)
        btn.setObjectName("FdToolbarPrimary")
        btn.setMinimumWidth(76)
    elif ghost:
        # v3: ghost 不再幽灵，映射到低频实色
        btn.setMinimumHeight(FD_BTN_H_LOW)
        btn.setObjectName("FdLowFreqBtn")
        btn.setMinimumWidth(60)
    else:
        btn.setMinimumHeight(FD_BTN_H)
        btn.setObjectName("FdCardActionBtn")
        btn.setMinimumWidth(60)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)


def fd_apply_compact_input(widget, *, width_key: str = "") -> None:
    """QLineEdit / QComboBox / QDoubleSpinBox — 紧凑版。

    Args:
        widget: 输入控件
        width_key: FD_INPUT_WIDTHS 中的宽度键名（如 "room_number"）
    """
    name = widget.metaObject().className()
    if "Spin" in name:
        widget.setObjectName("FdCompactSpin")
        try:
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            flt = _NoWheelSpinFilter(widget)
            widget.installEventFilter(flt)
            widget._solid_no_wheel_filter = flt
        except Exception:
            pass
    elif "Combo" in name:
        widget.setObjectName("FdCompactCombo")
    else:
        widget.setObjectName("FdCompactInput")
    widget.setMinimumHeight(FD_INPUT_H)
    widget.setMaximumHeight(int(FD_INPUT_H * 1.2))
    # 宽度自适应
    if width_key and width_key in FD_INPUT_WIDTHS:
        widget.setFixedWidth(FD_INPUT_WIDTHS[width_key])
    else:
        widget.setMaximumWidth(160)


def fd_apply_action_btn(btn: QPushButton, *, primary: bool = False, danger: bool = False) -> None:
    """操作按键 — v7 统一 36px，靠颜色分级。

    primary=True  → 主操作（收款/制卡）主色实底 + 白字
    danger=True   → 危险操作（注销/挂失）红色实底 + 白字
    默认          → 卡片操作（发卡/退房/延住）浅底灰边
    v7.8: 不覆盖已有 objectName，避免组合支付/换房等吃错样式
    """
    existing = btn.objectName() or ""
    if primary:
        btn.setMinimumHeight(FD_BTN_H)
        if not existing:
            btn.setObjectName("SolidPrimaryBtn")
        btn.setMinimumWidth(80)
    elif danger:
        btn.setMinimumHeight(FD_BTN_H)
        if not existing:
            btn.setObjectName("FdDangerBtn")
        btn.setMinimumWidth(60)
    else:
        btn.setMinimumHeight(FD_BTN_H)
        if not existing:
            btn.setObjectName("FdCardActionBtn")
        btn.setMinimumWidth(60)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)


def fd_apply_card_action_btn(btn: QPushButton) -> None:
    """卡片操作按键 — 发卡/读卡退房/快退/延住，同色实色。

    v3 核心: 同行同类一种颜色，不幽灵。
    """
    btn.setMinimumHeight(FD_BTN_H)
    btn.setObjectName("FdCardActionBtn")
    btn.setMinimumWidth(60)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


def fd_apply_low_freq_btn(btn: QPushButton) -> None:
    """低频操作按键 — 换房/备注/打印等，浅色实色。

    v3 核心: 沉底放流水台账下方，浅色但实色不幽灵。
    """
    btn.setMinimumHeight(FD_BTN_H_LOW)
    btn.setObjectName("FdLowFreqBtn")
    btn.setMinimumWidth(FD_BTN_MIN_W)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


def fd_card_layout(card: QFrame) -> QVBoxLayout:
    lay = QVBoxLayout(card)
    lay.setContentsMargins(FD_CARD_PADDING, FD_CARD_PADDING, FD_CARD_PADDING, FD_CARD_PADDING)
    lay.setSpacing(FD_SPACE_SM)
    return lay


def fd_compact_form_row(*widgets, spacing: int = FD_INPUT_GAP) -> QHBoxLayout:
    """紧凑表单行 — 标签+输入框紧密排列。

    用法:
        row = fd_compact_form_row(
            lbl_room, input_room,
            lbl_name, input_name,
            lbl_phone, input_phone,
        )
    """
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
    return lay


# ═══════════════════════════════════════════════════════════════
# v7 新增组件构造器 — 最美 UI + 最顺 UX
# ═══════════════════════════════════════════════════════════════
def fd_kpi_card(parent=None) -> QFrame:
    """KPI 卡片 — 三面亮渐隐色条 + 大数字。"""
    card = QFrame(parent)
    card.setObjectName("KpiCard")
    return card


def fd_section_card(parent=None, *, title: str = "") -> QFrame:
    """大区块卡片 — 顶部 accent 色条 + 标题。"""
    card = QFrame(parent)
    card.setObjectName("OverviewSectionCard")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(FD_SPACE_LG, FD_SPACE_LG, FD_SPACE_LG, FD_SPACE_LG)
    lay.setSpacing(FD_SPACE_MD)
    if title:
        lbl = QLabel(title)
        lbl.setObjectName("OverviewSectionTitle")
        lay.addWidget(lbl)
    return card


def fd_apply_amount_input(widget) -> None:
    """金额输入框 — 44px 大号，收银台主锚点。"""
    widget.setObjectName("FdAmountInput")
    try:
        from ui.layout.frontdesk import FD_INPUT_H_LG
        widget.setMinimumHeight(FD_INPUT_H_LG)
        widget.setMaximumHeight(FD_INPUT_H_LG)
    except Exception:
        widget.setMinimumHeight(44)
        widget.setMaximumHeight(44)


def fd_apply_standard_input(widget, *, width_key: str = "") -> None:
    """标准输入框 — 34px，用于表单/搜索。"""
    name = widget.metaObject().className()
    if "Spin" in name:
        widget.setObjectName("FdStandardSpin")
    elif "Combo" in name:
        widget.setObjectName("FdStandardCombo")
    else:
        widget.setObjectName("FdStandardInput")
    widget.setMinimumHeight(FD_INPUT_H)
    widget.setMaximumHeight(int(FD_INPUT_H * 1.2))
    if width_key and width_key in FD_INPUT_WIDTHS:
        widget.setFixedWidth(FD_INPUT_WIDTHS[width_key])


def fd_apply_commit_btn(btn: QPushButton) -> None:
    """确认入住按钮 — v7 统一 36px（不再 52px）。"""
    btn.setObjectName("FdCommitBtn")
    btn.setMinimumHeight(FD_BTN_H)  # v7: 统一 36px
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def fd_apply_quick_btn(btn: QPushButton) -> None:
    """快捷操作按键 — banner 行的快捷按钮。"""
    btn.setMinimumHeight(FD_BTN_H)
    btn.setObjectName("FdQuickBtn")
    btn.setMinimumWidth(72)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


def fd_apply_mode_group(widget, *, mode: str) -> None:
    """操作模式色编码容器 — READY/INHOUSE 视觉区分（UX 创新核心）。

    READY    → 主色左条 + 主色 10% 底（入住前发卡/读卡）
    INHOUSE  → 危险色左条 + 7% 危险底（在住退房/注销/挂失）
    """
    if mode == "ready":
        widget.setObjectName("FdCardGroupReady")
    elif mode == "inhouse":
        widget.setObjectName("FdCardGroupInhouse")



# 实底设色 — 全站唯一入口见 ui_surface.py
