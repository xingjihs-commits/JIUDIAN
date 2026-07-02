# [UI-REDESIGN] 2026-06-15 改动: 合并双顶栏(TopBar+ContextBar)为单行智能顶栏
"""smart_header.py — 单行智能顶栏 (48px)

设计原则：
  - 合并原 TopBar(56px) + ContextBar(48px) = 104px → 单行 48px
  - 节省 56px 垂直空间，对 1366x768 前台显示器至关重要
  - 三段式布局：[☰ 品牌 面包屑] [动态上下文工具] [搜索 通知 头像]
  - 上下文工具区随页面切换动态变化（筛选条/流程条/页面专属工具）
  - 质感优先：精细分割线、微动效、品牌色点缀

布局结构：
  ┌─────────────────────────────────────────────────────────────────┐
  │ ☰ │ 品牌名 │ 前台运营 / 收银台 │  [动态工具区] │ 🔍 🔔 头像 │
  └─────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QToolButton, QWidget, QSizePolicy, QMenu, QScrollArea,
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QSize
from PySide6.QtGui import QFont

from brand_config_v4 import APP_NAME, APP_NAME_FULL
from database import db
from i18n import i18n
from design_tokens import _p

# ── 布局常量 ──────────────────────────────────────────────────
SMART_HEADER_HEIGHT = 48
BREADCRUMB_SEPARATOR = "  /  "


class SmartHeader(QFrame):
    """单行智能顶栏：品牌 + 面包屑 + 动态工具 + 全局操作。

    动态工具区 (ctx_tool_host) 的内容随当前页面切换：
      - matrix 页 → 筛选芯片 + 时间线/批量按钮
      - checkin 页 → SOP 流程条
      - 其他页 → 页面专属工具（或留空）

    所有工具组件由 MainWindow 通过 add_context_widget() 注入，
    SmartHeader 本身不持有业务逻辑。
    """

    # 信号：侧栏折叠切换
    sidebar_toggle_requested = Signal()
    # 信号：搜索激活
    search_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bc_group = ""
        self._bc_page = ""
        self.setObjectName("SmartHeader")
        self.setMinimumHeight(SMART_HEADER_HEIGHT)
        self.setMaximumHeight(int(SMART_HEADER_HEIGHT * 1.15))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 12, 0)
        lay.setSpacing(0)

        # ═══ 左段：☰ + S LOGO + Solid 英文 + 面包屑 ═══
        self.btn_hamburger = QPushButton("\u2261")
        self.btn_hamburger.setObjectName("HamburgerBtn")
        self.btn_hamburger.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_hamburger.setFixedSize(32, 32)
        self.btn_hamburger.setToolTip(i18n.t("toggle_sidebar"))
        self.btn_hamburger.clicked.connect(self.sidebar_toggle_requested.emit)
        lay.addWidget(self.btn_hamburger, 0, Qt.AlignmentFlag.AlignVCenter)

        from brand_assets import make_brand_mark_label

        self.lbl_s_mark = make_brand_mark_label(28, prefer_sm=True, object_name="HeaderSMark")
        self.lbl_s_mark.setStyleSheet("QLabel#HeaderSMark { border-radius: 6px; padding: 0; }")
        lay.addWidget(self.lbl_s_mark, 0, Qt.AlignmentFlag.AlignVCenter)

        # "Solid" 英文（深海蓝主色，与侧栏品牌区一致）
        self.lbl_brand = QLabel("Solid")
        self.lbl_brand.setObjectName("TopBarBrand")
        self.lbl_brand.setToolTip(APP_NAME_FULL)
        self.lbl_brand.setStyleSheet(
            "QLabel#TopBarBrand { font-size: 14px; font-weight: 700; "
            "letter-spacing: 1px; padding: 0 8px 0 6px; background: transparent; }"
        )
        lay.addWidget(self.lbl_brand, 0, Qt.AlignmentFlag.AlignVCenter)

        # 细竖线分隔品牌与面包屑
        sep1 = QFrame()
        sep1.setObjectName("HeaderBrandSep")
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setFixedWidth(1)
        sep1.setFixedHeight(20)
        lay.addWidget(sep1, 0, Qt.AlignmentFlag.AlignVCenter)

        # 面包屑（替代原 ContextBar 的双行标题）
        self.lbl_breadcrumb = QLabel("")
        self.lbl_breadcrumb.setObjectName("HeaderBreadcrumb")
        self.lbl_breadcrumb.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.lbl_breadcrumb, 0, Qt.AlignmentFlag.AlignVCenter)

        # ═══ 中段：动态上下文工具区（横向滚动，防与迷你标签条叠压）═══
        self.ctx_tool_scroll = QScrollArea()
        self.ctx_tool_scroll.setObjectName("SmartHeaderCtxScroll")
        self.ctx_tool_scroll.setWidgetResizable(True)
        self.ctx_tool_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.ctx_tool_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.ctx_tool_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ctx_tool_scroll.setFixedHeight(SMART_HEADER_HEIGHT)
        self.ctx_tool_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.ctx_tool_panel = QWidget()
        self.ctx_tool_panel.setObjectName("SmartHeaderCtxPanel")
        self.ctx_tool_host = QHBoxLayout(self.ctx_tool_panel)
        self.ctx_tool_host.setSpacing(6)
        self.ctx_tool_host.setContentsMargins(12, 0, 8, 0)
        self.ctx_tool_scroll.setWidget(self.ctx_tool_panel)
        lay.addWidget(self.ctx_tool_scroll, 1)

        from ui_surface import fd_apply_scroll_area
        fd_apply_scroll_area(self.ctx_tool_scroll, bg_key="bg_container")

        # ═══ 右段：搜索 + 通知 + 用户 ═══
        # 搜索按钮（Ctrl+K）
        self.btn_search = QPushButton(i18n.t("hotel.search_placeholder", default="搜索菜单..."))
        self.btn_search.setObjectName("HeaderSearchBtn")
        self.btn_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_search.setToolTip(i18n.t("cmdk_open") + " (Ctrl+K)")
        self.btn_search.clicked.connect(self.search_requested.emit)
        lay.addWidget(self.btn_search, 0, Qt.AlignmentFlag.AlignVCenter)

        # 通知铃铛（预留）
        self.btn_bell = QToolButton()
        self.btn_bell.setObjectName("HeaderBellBtn")
        self.btn_bell.setText("🔔")
        self.btn_bell.setFixedSize(40, 40)
        self.btn_bell.setStyleSheet("font-size:18px;")
        self.btn_bell.setToolTip(i18n.t("topbar_notifications"))
        self.btn_bell.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bell.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        lay.addWidget(self.btn_bell, 0, Qt.AlignmentFlag.AlignVCenter)

        # 当前操作员标签
        self.lbl_current_op = QLabel("")
        self.lbl_current_op.setObjectName("HeaderCurrentOp")
        self.lbl_current_op.setMinimumWidth(80)
        self.lbl_current_op.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.lbl_current_op, 0, Qt.AlignmentFlag.AlignVCenter)

        # 用户头像按钮
        self.btn_session_user = QToolButton()
        self.btn_session_user.setObjectName("TopAvatarInner")
        self.btn_session_user.setText("—")
        self.btn_session_user.setFixedSize(40, 40)
        self.btn_session_user.setStyleSheet("font-size:16px;")
        self.btn_session_user.setToolTip(i18n.t("topbar_user_tip"))
        self.btn_session_user.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_session_user.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        lay.addWidget(self.btn_session_user, 0, Qt.AlignmentFlag.AlignVCenter)

        # 保存引用（兼容旧代码）
        self.lbl_session_user = self.btn_session_user

        # ── 隐藏的旧组件引用（兼容桥接，后续逐步移除）──
        self.chrome_action_group = QFrame()
        self.chrome_action_group.hide()
        self._chrome_btn_ci = None
        self._chrome_btn_co = None
        self.chrome_command_strip = QFrame()
        self.chrome_command_strip.hide()
        self.btn_chrome_nav = QToolButton()
        self.btn_chrome_nav.hide()
        self.lbl_shift_info = QLabel("")
        self.lbl_shift_info.setObjectName("FdMutedLabel")
        self.lbl_shift_info.hide()

    # ══════════════════════════════════════════════════════════════
    #  面包屑更新
    # ══════════════════════════════════════════════════════════════
    def set_breadcrumb(self, group: str, page: str) -> None:
        """设置面包屑：组名 / 页面名，交替深浅色形成层级感。"""
        self._bc_group = group or ""
        self._bc_page = page or ""
        self._apply_breadcrumb_html()

    def refresh_theme(self) -> None:
        """换主题后重刷面包屑 HTML 色（SmartHeader QSS 由 app_main 重载）。"""
        self._apply_breadcrumb_html()

    def _apply_breadcrumb_html(self) -> None:
        group = getattr(self, "_bc_group", "")
        page = getattr(self, "_bc_page", "")
        if group and page:
            self.lbl_breadcrumb.setText(
                f'<span style="color:{_p("text_dim")}">{group}</span>'
                f'<span style="color:{_p("border")}">  /  </span>'
                f'<span style="color:{_p("text")};font-weight:600">{page}</span>'
            )
        elif page:
            self.lbl_breadcrumb.setText(
                f'<span style="color:{_p("text")};font-weight:600">{page}</span>'
            )
        else:
            self.lbl_breadcrumb.setText("")

    # ══════════════════════════════════════════════════════════════
    #  动态上下文工具区
    # ══════════════════════════════════════════════════════════════
    def clear_context_tools(self) -> None:
        """清空动态工具区中的所有组件。"""
        while self.ctx_tool_host.count():
            item = self.ctx_tool_host.takeAt(0)
            w = item.widget()
            if w:
                w.hide()  # 先隐藏，避免 setParent(None) 时闪现独立窗口
                w.setParent(None)  # 不 deleteLater，让调用方管理生命周期

    def add_context_widget(self, widget: QWidget) -> None:
        """向动态工具区追加一个组件。"""
        self.ctx_tool_host.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def add_context_stretch(self) -> None:
        """在动态工具区末尾添加弹性空间。"""
        self.ctx_tool_host.addStretch()

    # ══════════════════════════════════════════════════════════════
    #  兼容性方法
    # ══════════════════════════════════════════════════════════════
    @property
    def btn_hamburger_ref(self):
        """兼容旧代码引用汉堡按钮。"""
        return self.btn_hamburger


# ══════════════════════════════════════════════════════════════════════════
#  QSS 样式 — 为新组件提供质感样式
# ══════════════════════════════════════════════════════════════════════════

SMART_HEADER_QSS = """
/* ━━ SmartHeader ━━ */
QFrame#SmartHeader {
    background-color: @bg_container@;
    border: none;
    border-bottom: 1px solid @border@;
    min-height: 48px;
    max-height: 56px;
}
/* 内部上下文面板透明 — 背景由 SmartHeader 统一承载，消除中段断档 */
QWidget#SmartHeaderCtxPanel {
    background-color: transparent;
}
QScrollArea#SmartHeaderCtxScroll {
    background-color: @bg_container@;
    border: none;
}
QScrollArea#SmartHeaderCtxScroll > QWidget#qt_scrollarea_viewport {
    background-color: @bg_container@;
}
QFrame#SmartHeader QLabel {
    background-color: transparent;
}
QLabel#TopBarBrand {
    font-size: 14px;
    font-weight: 800;
    color: @primary@;
    padding: 0 6px 0 2px;
    letter-spacing: 0.5px;
}
QLabel#HeaderSMark {
    padding: 0 0 0 4px;
    background-color: transparent;
}
QFrame#HeaderBrandSep {
    color: @border@;
    background-color: @border@;
    margin: 0 6px;
    max-width: 1px;
}
QLabel#HeaderBreadcrumb {
    font-size: 13px;
    font-weight: 500;
    padding: 0 6px;
    color: @text_muted@;
}
QPushButton#HeaderSearchBtn {
    background-color: @surface_alt@;
    border: 1px solid @border@;
    border-radius: 6px;
    font-size: 13px;
    color: @text_muted@;
    min-width: 160px;
    min-height: 32px;
    max-height: 32px;
    padding: 0 12px;
    text-align: left;
}
QPushButton#HeaderSearchBtn:hover {
    background-color: @surface_alt@;
    border-color: @primary@;
    color: @text@;
}
QToolButton#HeaderBellBtn {
    border: none;
    border-radius: 8px;
    background-color: @bg_container@;
    font-size: 16px;
    color: @text_muted@;
}
QToolButton#HeaderBellBtn:hover {
    background-color: @primary_10pct@;
    color: @primary@;
}
QToolButton#TopAvatarInner {
    border: 1px solid @border@;
    border-radius: 20px;
    background-color: @surface_alt@;
    font-size: 14px;
    color: @primary@;
    font-weight: 700;
}
QToolButton#TopAvatarInner:hover {
    background-color: @primary_10pct@;
    border-color: @accent@;
}
QLabel#HeaderCurrentOp {
    font-size: 12px;
    color: @text_muted@;
    padding: 0 6px;
    background-color: transparent;
}
QLabel#HeaderCurrentOp[loggedIn="false"] {
    color: @danger@;
    background-color: transparent;
}

/* ━━ MiniTabStrip ━━ */
QFrame#MiniTabStrip {
    background-color: @bg_container@;
    border: none;
    border-bottom: 1px solid @border@;
    min-height: 32px;
    max-height: 34px;
}
QFrame#MiniTabStrip QLabel {
    background-color: transparent;
}
QScrollArea#MiniTabScrollArea {
    background-color: @bg_container@;
    border: none;
}
QWidget#MiniTabContainer {
    background-color: @bg_container@;
}
QPushButton#MiniTabButton {
    background-color: @surface@;
    border: 1px solid @panel_border@;
    border-bottom: 1px solid @border@;
    border-radius: 4px 4px 0 0;
    padding: 2px 10px;
    color: @text@;
    font-weight: 500;
}
QPushButton#MiniTabButton:hover {
    background-color: @surface_alt@;
    color: @text@;
    border-color: @text_muted@;
}
QPushButton#MiniTabButton[active="true"] {
    background-color: @primary@;
    color: @selected_fg@;
    font-weight: 700;
    border: 1px solid @primary@;
    border-bottom: 2px solid @accent@;
}
QPushButton#MiniTabScrollBtn {
    border: none;
    border-radius: 4px;
    color: @text_dim@;
    font-size: 10px;
    min-height: 24px;
    min-width: 24px;
}
QPushButton#MiniTabScrollBtn:hover {
    background-color: @surface_alt@;
    color: @text@;
}

/* ━━ EnhancedStatusBar — 样式见 base.qss，此处不重复 ━━ */

/* ━━ SidebarCollapseBtn ━━ */
QPushButton#SidebarCollapseBtn {
    border: none;
    border-radius: 0;
    background-color: transparent;
    color: @text_dim@;
    font-size: 14px;
    font-weight: 700;
    min-height: 28px;
}
QPushButton#SidebarCollapseBtn:hover {
    background-color: @sidebar_hover@;
    color: @accent@;
}
"""




def render_smart_header_qss() -> str:
    """渲染 SmartHeader QSS，用 theme_tokens 与全站 L0–L3 色板一致。"""
    from theme_palette import theme_tokens, _replace_qss_vars, resolve_theme_name
    try:
        from database import db
        name = resolve_theme_name(db.get_config("theme"))
    except Exception:
        name = "old_money"
    tokens = _replace_qss_vars(theme_tokens(name))  # 补齐 bg_container/bg_root 等派生 token
    qss = SMART_HEADER_QSS
    replacements = {f"@{k}@": str(v) for k, v in tokens.items() if isinstance(v, str)}
    for k, v in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
        qss = qss.replace(k, v)
    return qss

