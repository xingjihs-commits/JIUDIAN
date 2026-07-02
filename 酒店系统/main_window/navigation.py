# [UI-REDESIGN] 2026-06-15 v2 改动: 侧栏紧凑化 — 宽度180px、按钮38px高、gold_thread金线指示器、品牌区金线分隔
"""
main_window/navigation.py — 侧栏导航逻辑

包含：
- SidebarButton：自定义侧边栏按钮
- NavigationMixin：导航方法混入类（供 MainWindow 混入使用）
- 导航常量：侧栏宽度等

原代码提取自 main_window.py，保持完全兼容。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QMenu, QWidget,
    QSizePolicy, QApplication,
)
from PySide6.QtCore import Qt, Signal as QtSignal, QSize
from PySide6.QtGui import QFont

from brand_config_v4 import APP_NAME, APP_NAME_FULL
from database import db
from design_tokens import _p
from i18n import i18n
from ui_helpers import show_warning
from permission_system import PermissionManager
from role_navigation import (
    SIDEBAR_NAV_GROUPS,
    action_permission,
    home_action_for_role,
    role_display_name,
)
from role_ui import (
    apply_avatar_to_toolbutton,
    brand_logo_label,
    make_brand_wordmark,
    make_role_avatar_label,
)
from nav_manifest import get_entry, NavEntry

# ── 布局常量（三栏骨架）──────────────────────────────────────────────
# 侧栏紧凑至 200px，桌面端 PC 紧凑风格
SIDEBAR_WIDTH = 200
SIDEBAR_COLLAPSED_WIDTH = 64
# 顶栏 48px，视觉比例更紧凑
TOPBAR_HEIGHT = 48
CONTEXT_BAR_HEIGHT = 48
STATUSBAR_HEIGHT = 24
CHROME_BAR_HEIGHT = TOPBAR_HEIGHT

# 侧边栏按钮高度常量（统一 38px，紧凑规整）
SIDEBAR_BTN_HEIGHT = 38


class SidebarButton(QFrame):
    """自定义侧边栏按钮：图标+文字严格对齐，支持折叠模式。

    改动要点：
    - 固定高度 38px（紧凑统一）
    - active 态左侧 3px gold_thread 色条（通过 QSS NavBtnFrame[active="true"] 驱动）
    - 图标区域固定 24px，文字区左对齐
    - 折叠模式居中对齐，图标自动放大
    """
    clicked = QtSignal()

    def __init__(self, icon: str, text: str, action: str):
        super().__init__()
        self.action = action
        self._icon_text = icon
        self._label_text = text

        self.setFixedHeight(SIDEBAR_BTN_HEIGHT)
        self.setObjectName("NavBtnFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(text)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(10)

        # 图标：固定 24×24 区域，居中
        self.lbl_icon = QLabel(icon)
        self.lbl_icon.setObjectName("NavBtnIcon")
        self.lbl_icon.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.lbl_icon.setFixedSize(24, 24)
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_icon.setFont(QFont("Segoe UI Emoji", 14))

        # 文字：竖向居中、左对齐
        self.lbl_text = QLabel(text)
        self.lbl_text.setObjectName("NavBtnText")
        self.lbl_text.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.lbl_text.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        lay.addWidget(self.lbl_icon)
        lay.addWidget(self.lbl_text, stretch=1)

        self._checked = False
        self._collapsed = False
        self.updateStyle()

    def setChecked(self, state: bool) -> None:
        self._checked = state
        self.updateStyle()

    def text(self) -> str:
        return self._label_text

    def set_collapsed(self, collapsed: bool) -> None:
        """折叠模式：仅显示图标，图标居中放大。"""
        self._collapsed = collapsed
        self.lbl_text.setVisible(not collapsed)
        if collapsed:
            self.setFixedWidth(SIDEBAR_COLLAPSED_WIDTH)
            lay = self.layout()
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_icon.setFixedSize(28, 28)
            self.lbl_icon.setFont(QFont("Segoe UI Emoji", 16))
        else:
            self.setFixedWidth(self.parentWidget().width() if self.parentWidget() else SIDEBAR_WIDTH)
            lay = self.layout()
            lay.setContentsMargins(12, 0, 12, 0)
            lay.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self.lbl_icon.setFixedSize(24, 24)
            self.lbl_icon.setFont(QFont("Segoe UI Emoji", 14))
        self.updateStyle()

    def updateStyle(self) -> None:
        self.setProperty("active", "true" if self._checked else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.lbl_text.style().unpolish(self.lbl_text)
        self.lbl_text.style().polish(self.lbl_text)
        self.lbl_icon.style().unpolish(self.lbl_icon)
        self.lbl_icon.style().polish(self.lbl_icon)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.updateStyle()
        super().leaveEvent(event)


# ══════════════════════════════════════════════════════════════════════════
#  NavigationMixin — 侧栏导航逻辑（混入 MainWindow）
# ══════════════════════════════════════════════════════════════════════════
class NavigationMixin:
    """侧栏导航逻辑混入类，供 MainWindow 混入使用。

    改动要点：
    - 侧栏品牌区高度提升，品牌名更突出
    - 品牌区底部 gold_thread 金线分隔
    - 分组标签样式更清晰（全大写 + 字间距）
    - 角色信息条置底布局优化
    """

    def _build_left_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("LeftSidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        sidebar.setProperty("collapsed", False)

        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── 品牌区 — 新 LOGO（深海蓝底 + 烫金 S 形钥匙）──
        brand_w = QWidget()
        brand_w.setObjectName("BrandArea")
        brand_w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        brand_w.setMinimumHeight(72)
        brand_w.setMaximumHeight(88)
        brand_lay = QHBoxLayout(brand_w)
        brand_lay.setContentsMargins(12, 8, 12, 8)
        brand_lay.setSpacing(12)

        logo = brand_logo_label(size=40)
        logo.setToolTip(APP_NAME_FULL)
        brand_lay.addWidget(logo)

        self.brand_wordmark = make_brand_wordmark()
        self.brand_wordmark.setToolTip(APP_NAME_FULL)
        brand_lay.addWidget(self.brand_wordmark)
        brand_lay.addStretch()
        lay.addWidget(brand_w)

        # ── 品牌区底部金线分隔（烫金色线）──
        brand_sep = QFrame()
        brand_sep.setObjectName("BrandSep")
        brand_sep.setFrameShape(QFrame.Shape.HLine)
        brand_sep.setFixedHeight(1)
        brand_sep.setStyleSheet(
            f"QFrame#BrandSep {{ background: {_p('gold_thread')}; border: none; }}"
        )
        lay.addWidget(brand_sep)


        # ── 导航滚动区 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("SidebarScroll")
        scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll.viewport().setObjectName("SidebarScrollViewport")
        scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        container = QWidget()
        container.setObjectName("SidebarScrollContainer")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        c_lay = QVBoxLayout(container)
        c_lay.setContentsMargins(8, 10, 8, 10)
        c_lay.setSpacing(2)

        self._sidebar_actions = []
        self._sidebar_group_labels = []
        self._sidebar_separators: list[QFrame] = []

        # 从 SIDEBAR_NAV_GROUPS 构建 perm_map（用于分组名称查找）
        self._perm_map = {item.action: item.perm[0] if item.perm else ""
                          for group in SIDEBAR_NAV_GROUPS for item in group.items}
        self._perm_map.update({
            "dashboard": "view_dashboard", "roster": "view_guests",
            "service": "view_dashboard", "shop": "view_shop",
            "pricing": "manage_pricing", "member": "manage_staff",
            "ota": "manage_pricing", "staff": "manage_staff",
            "inventory": "manage_shop",
            "room_unified": "manage_pricing",
            "item_dict": "manage_shop",
            "card": "settings_view",
            "settings": "settings_view", "debug": "debug_panel",
        })
        self._nav_groups: list[tuple[QLabel, list[tuple]]] = []
        self._action_group_map = {}

        for group in SIDEBAR_NAV_GROUPS:
            g_title = group.name or ""

            # 分组标签 — 全大写小字，字间距强调结构感；空标题分组不显示标签
            if g_title:
                g_lbl = QLabel(g_title.upper())
                g_lbl.setObjectName("SidebarGroupLabel")
                g_lbl.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                g_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                g_lbl.setCursor(Qt.CursorShape.ArrowCursor)
                c_lay.addWidget(g_lbl)
                self._sidebar_group_labels.append(g_lbl)
            else:
                g_lbl = None

            entries = []
            for item in group.items:
                text = item.label
                btn = SidebarButton(item.icon, text, item.action)
                btn.clicked.connect(lambda a=item.action: self._on_sidebar_menu_clicked(a))
                c_lay.addWidget(btn)
                self._sidebar_actions.append((btn, item.action))
                self._action_group_map[item.action] = g_title
                # perm: None = 所有人可见；字符串 = 需该权限
                perm_check = action_permission(item.action)
                entries.append((btn, item.action, perm_check))
            self._nav_groups.append((g_lbl, entries))

            # 分组间分隔线（比原来更细腻）
            sep = QFrame()
            sep.setObjectName("SidebarGroupSep")
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFixedHeight(1)
            c_lay.addWidget(sep)
            self._sidebar_separators.append(sep)

        c_lay.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll, stretch=1)

        # ── 角色信息条（置底）——头像+用户名 ──
        role_strip = QWidget()
        role_strip.setObjectName("SidebarRoleStrip")
        role_strip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rs_lay = QVBoxLayout(role_strip)
        rs_lay.setContentsMargins(12, 6, 12, 8)
        rs_lay.setSpacing(2)

        # 头像 + 用户名 横排
        self._sidebar_role_row = QWidget()
        self._sidebar_role_row.setObjectName("SidebarRoleRow")
        self._sidebar_role_row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        role_row_lay = QHBoxLayout(self._sidebar_role_row)
        role_row_lay.setContentsMargins(0, 4, 0, 0)
        role_row_lay.setSpacing(10)

        self._sidebar_avatar_host = QWidget()
        self._sidebar_avatar_host.setObjectName("SidebarAvatarHost")
        self._sidebar_avatar_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._sidebar_avatar_lay = QHBoxLayout(self._sidebar_avatar_host)
        self._sidebar_avatar_lay.setContentsMargins(0, 0, 0, 0)
        role_row_lay.addWidget(self._sidebar_avatar_host, 0, Qt.AlignmentFlag.AlignTop)

        self.lbl_sidebar_role = QLabel("")
        self.lbl_sidebar_role.setObjectName("SidebarRoleLabel")
        self.lbl_sidebar_role.setWordWrap(True)
        role_row_lay.addWidget(self.lbl_sidebar_role, 1)
        rs_lay.addWidget(self._sidebar_role_row)

        role_strip.hide()  # 角色信息已迁移至底部 EnhancedStatusBar
        lay.addWidget(role_strip)

        self._sidebar_collapsible_frames = [sidebar, role_strip]
        self._sidebar_collapsed = False

        return sidebar

    # ══════════════════════════════════════════════════════════════════════════
    #  侧栏折叠切换
    # ══════════════════════════════════════════════════════════════════════════
    def _toggle_sidebar_collapse(self) -> None:
        """切换侧栏折叠/展开，带平滑动画。"""
        self._sidebar_collapsed = not getattr(self, "_sidebar_collapsed", False)
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        anim = QPropertyAnimation(self.left_sidebar, b"maximumWidth")
        anim.setDuration(180)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(SIDEBAR_WIDTH if self._sidebar_collapsed else SIDEBAR_COLLAPSED_WIDTH)
        anim.setEndValue(SIDEBAR_COLLAPSED_WIDTH if self._sidebar_collapsed else SIDEBAR_WIDTH)
        anim.start()
        self.left_sidebar.setFixedWidth(SIDEBAR_COLLAPSED_WIDTH if self._sidebar_collapsed else SIDEBAR_WIDTH)
        self.left_sidebar.setProperty("collapsed", self._sidebar_collapsed)

        for btn, _act in getattr(self, "_sidebar_actions", []):
            if hasattr(btn, "set_collapsed"):
                btn.set_collapsed(self._sidebar_collapsed)

        if hasattr(self, "brand_wordmark"):
            self.brand_wordmark.setVisible(not self._sidebar_collapsed)

        for gl in getattr(self, "_sidebar_group_labels", []):
            gl.setVisible(not self._sidebar_collapsed)

        if hasattr(self, "_sidebar_role_row"):
            self._sidebar_role_row.setVisible(True)
            if hasattr(self, "lbl_sidebar_role"):
                self.lbl_sidebar_role.setVisible(not self._sidebar_collapsed)

        self.left_sidebar.style().unpolish(self.left_sidebar)
        self.left_sidebar.style().polish(self.left_sidebar)

    def _apply_role_navigation(self) -> None:
        """按权限显示侧栏；无权限的分组自动隐藏。"""
        for g_lbl, entries in getattr(self, "_nav_groups", []):
            any_visible = False
            for btn, action, perm in entries:
                # perm None 或空 = 全员可见
                ok = True if not perm else PermissionManager.has_permission(perm)
                btn.setVisible(ok)
                if ok:
                    any_visible = True
            if g_lbl is not None:
                g_lbl.setVisible(any_visible)

        self._rebuild_chrome_nav_menu()

        try:
            self._vendor_console_available = True
        except Exception:
            pass

    def _rebuild_chrome_nav_menu(self) -> None:
        if not hasattr(self, "btn_chrome_nav"):
            return
        nav_menu = QMenu(self.btn_chrome_nav)
        for sb, act in self._sidebar_actions:
            if not sb.isVisible():
                continue
            title = f"{sb.lbl_icon.text()}  {sb.text()}"
            na = nav_menu.addAction(title)
            na.triggered.connect(lambda _=False, ac=act: self._on_sidebar_menu_clicked(ac))
        self.btn_chrome_nav.setMenu(nav_menu)

    def _navigate_role_home(self) -> None:
        home = home_action_for_role(PermissionManager.current_role())
        for btn, act in self._sidebar_actions:
            if act == home and btn.isVisible():
                btn.setChecked(True)
                self._on_sidebar_menu_clicked(home)
                return
        for btn, _act in self._sidebar_actions:
            if btn.isVisible():
                btn.setChecked(True)
                btn.clicked.emit()
                return

    # ══════════════════════════════════════════════════════════════════════════
    #  导航与状态同步
    # ══════════════════════════════════════════════════════════════════════════
    _FD_ROUTE_ACTIONS = frozenset({"checkin", "roster", "shop", "service", "shift"})
    _last_sidebar_action: str | None = None

    def _sync_sidebar_state(self, action: str) -> None:
        """只切换两个按钮：旧的 active → 新的 active，避免全量遍历触发 60+ 次重绘。"""
        if not hasattr(self, "_sidebar_actions"):
            return
        old_action = self._last_sidebar_action
        self._last_sidebar_action = action
        for btn, act in self._sidebar_actions:
            if act == action or (old_action and act == old_action):
                btn.blockSignals(True)
                btn.setChecked(act == action)
                btn.blockSignals(False)

    def _sync_chrome_frontdesk_actions(self, action: str) -> None:
        """顶栏不再重复前台动作（已由上下文栏/工作台承担）。"""
        pass

    def _navigate(self, action: str, *, hub_sub: str | None = None) -> None:
        """全项目唯一跳转入口。侧栏/快捷键/room_matrix/settings 只调此函数。"""
        entry = get_entry(action)
        if hasattr(self, "_apply_focus_mode"):
            self._apply_focus_mode(action)
        if entry is None:
            # 兼容 vendor/debug 等特殊 action，走旧逻辑
            self._on_sidebar_menu_clicked(action)
            return

        if entry.container == "matrix":
            self._on_sidebar_menu_clicked("matrix")
            return

        if entry.container == "hub":
            sub = hub_sub or entry.hub_sub or "checkin"
            self.workspace.navigate_frontdesk(sub)
            self._sync_sidebar_state(action if action in self._FD_ROUTE_ACTIONS else "checkin")
            self._update_context_chrome(sub)
            return

        if entry.container == "workspace":
            idx = self.workspace.tab_index(action)
            if idx >= 0:
                self.workspace.tabs.setCurrentIndex(idx)
            self._sync_sidebar_state(action)
            self._update_context_chrome(action)
            return

        self._on_sidebar_menu_clicked(action)

    def _on_sidebar_menu_clicked(self, action: str) -> None:
        self._sync_sidebar_state(action)

        group_name = self._action_group_map.get(action, i18n.t("nav_group_desk"))
        item_name = ""
        for btn, act in self._sidebar_actions:
            if act == action:
                item_name = btn.text()
                break
        if not item_name:
            _nav_title_keys = {
                "matrix": "nav_matrix_dash",
                "dashboard": "nav_dashboard",
                "checkin": "tab_frontdesk_hub",
                "overview": "nav_overview",
                "roster": "nav_roster",
                "service": "nav_service",
                "shop": "nav_shop",
                "hk": "nav_hk",
                "energy": "nav_energy",
                "shift": "nav_shift",
                "finance": "nav_finance",
                "refunds": "refund_pending_title",
                "report": "nav_report",
                "night_audit": "nav_night_audit",
                "audit": "nav_audit",
                "pricing": "nav_pricing",
                "member": "nav_member",
                "ota": "nav_ota",
                "staff": "nav_staff",
                "inventory": "nav_inventory",
                "room_unified": "nav_room_unified",
                "item_dict": "nav_item_dict",
                "card": "nav_card",
                "settings": "nav_settings",
                "vendor_console": "nav_vendor_console",
                "debug": "nav_debug",
            }
            nk = _nav_title_keys.get(action)
            item_name = i18n.t(nk) if nk else action
        self._update_context_chrome(action)

        fd_actions = ("checkin", "roster", "shop", "service", "shift")
        tab_keys = (
            "overview", "finance", "report", "dashboard", "hk", "energy",
            "audit", "inventory", "room_unified", "item_dict", "staff",
            "member", "pricing", "card", "ota", "night_audit",
            "settings", "vendor_console",
        )

        if action == "matrix":
            self.stack.setCurrentIndex(0)
            self.btn_view_mode.setText(i18n.t("btn_timeline_mode"))
            self.room_matrix.search_rooms("")
        elif action == "debug":
            self._debug()
        elif action == "vendor_takeover":
            self._open_takeover_hub()
        elif action == "vendor_lock":
            self._navigate_to("room_unified")
        elif action == "vendor_sniffer":
            self._open_card_sniffer()
        elif action == "vendor_cloud":
            self._vendor_cloud()
        elif action == "vendor_debug":
            self._debug()
        elif action in fd_actions:
            self.workspace.navigate_frontdesk(action)
        elif action in tab_keys:
            idx = self.workspace.tab_index(action)
            if idx >= 0:
                self.workspace.tabs.setCurrentIndex(idx)

        self._sync_chrome_frontdesk_actions(action)
        if hasattr(self, "_apply_focus_mode"):
            self._apply_focus_mode(action)
        self._refresh()

    def _navigate_to(self, action: str) -> None:
        self._navigate(action)

    def _focus_workspace_checkin(self) -> None:
        self.workspace.navigate_frontdesk("checkin")
        self._sync_sidebar_state("checkin")

    def _focus_workspace_shift(self) -> None:
        self.workspace.navigate_frontdesk("shift")
        self._sync_sidebar_state("shift")

    def _focus_workspace_finance(self) -> None:
        self.workspace.tabs.setCurrentIndex(self.workspace.tab_index("finance"))
        self._sync_sidebar_state("finance")

    def _focus_workspace_report(self) -> None:
        self.workspace.tabs.setCurrentIndex(self.workspace.tab_index("report"))
        self._sync_sidebar_state("report")
