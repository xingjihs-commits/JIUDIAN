import sys
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

logger = logging.getLogger(__name__)

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QInputDialog, QLineEdit, QTableWidget, QTableWidgetItem,
    QDialog, QHeaderView, QFileDialog, QStackedWidget, QApplication,
    QSplitter, QFrame, QSizePolicy, QScrollArea, QSpinBox, QToolButton,
    QMenu, QFormLayout, QCheckBox, QSlider,
)
from PySide6.QtCore import Qt, QTimer, QSize, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QKeySequence, QResizeEvent, QShortcut, QShowEvent

from brand_config_v4 import APP_NAME, APP_NAME_FULL, effective_brand
from database import db
from event_bus import bus
from room_matrix import RoomMatrix
from workspace_dock import WorkspaceDock
from ui.components.toast import ToastManager, ToastType
from debug_panel import ManufacturerDebugPanel
from timeline_view import TimelineView
from overlay_widgets import SuccessOverlay, CelebrationOverlay
from batch_create_dialog import BatchCreateRoomDialog, CreateSingleRoomDialog, ImportRoomsFromMdbDialog
from data_import_service import DataImportDialog
from i18n import i18n
from ui_helpers import ask_confirm, show_info, show_warning, style_dialog, build_dialog_header
from permission_system import PermissionManager
from command_palette import CommandPalette, install_command_palette_shortcut
from role_navigation import (
    SIDEBAR_NAV_GROUPS,
    action_permission,
    home_action_for_role,
    role_display_name,
    seed_role_layer_preset,
)
from vendor_lockdown import verify_vendor_code, vendor_code_configured
from role_ui import apply_avatar_to_toolbutton, brand_logo_label, make_brand_wordmark, make_role_avatar_label
from smart_header import SmartHeader, SMART_HEADER_HEIGHT, render_smart_header_qss
from mini_tab_strip import MiniTabStrip, MINI_TAB_STRIP_HEIGHT
from enhanced_status_bar import EnhancedStatusBar, ENHANCED_STATUSBAR_HEIGHT


def _current_actor_id() -> str:
    u = PermissionManager.current_user()
    if u:
        return str(u.get("username") or u.get("id") or "unknown")
    return PermissionManager.current_role() or "guest"



# ── 侧栏导航逻辑（提取自 main_window/navigation.py）──
from main_window.navigation import (
    NavigationMixin, SidebarButton,
    SIDEBAR_WIDTH, SIDEBAR_COLLAPSED_WIDTH, TOPBAR_HEIGHT,
    CONTEXT_BAR_HEIGHT, STATUSBAR_HEIGHT, CHROME_BAR_HEIGHT,
)


# ─────────────────────────────────────────────────────────────────────────────
#  主窗口 — 单壳布局（侧栏 + 顶栏 + 上下文栏 + 分屏工作区）
# ─────────────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow, NavigationMixin):
    LAYOUT_SHELL = "Lovable"

    # ── 焦点状态分类常量 ──
    _FOCUS_SPLIT = frozenset({"checkin", "roster", "shop", "service", "shift"})
    _FOCUS_MATRIX = frozenset({"matrix"})
    _FOCUS_OVERVIEW = frozenset({"overview"})
    _FOCUS_WORKSPACE_FULL = frozenset({
        "finance", "report", "inventory", "audit", "staff", "member",
        "pricing", "card", "settings", "vendor_console", "night_audit",
        "room_unified", "item_dict", "ota", "dashboard", "hk", "energy",
    })

    def __init__(self):
        super().__init__()
        try:
            _t = effective_brand(db)["title"]
        except Exception as e:
            logger.warning("加载品牌标题失败: %s", e)
            _t = "Solid PMS"
        try:
            self.setWindowTitle(_t)
        except Exception as e:
            logger.warning("setWindowTitle 失败: %s", e)
        try:
            self.resize(1366, 800)
            self.setMinimumSize(1024, 640)
        except Exception as e:
            logger.warning("resize 失败: %s", e)

        self.menuBar().setVisible(False)
        self._layout_mode = self.LAYOUT_SHELL

        # 预创建工作台（三种布局共用）
        self.workspace = WorkspaceDock()
        QApplication.processEvents()
        self.workspace.setObjectName("WorkspaceDockPanel")
        self.workspace.tabs.tabBar().hide()

        self._build_ui()
        QApplication.processEvents()
        self._connect_signals()
        self._init_toast()
        self._init_mini_tab_strip()
        self._update_session_chrome()

        bus.theme_changed.connect(lambda _: self.room_matrix._on_theme_changed())

        self.timer = QTimer()
        self.timer.timeout.connect(self._refresh)
        self.timer.start(5000)

        self.shift_timer = QTimer()
        self.shift_timer.timeout.connect(self._check_shift)
        self.shift_timer.start(60000)

        self._setup_shortcuts()

        self._refresh()

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        from ui_helpers import apply_windows_light_title_bar
        QTimer.singleShot(0, lambda: apply_windows_light_title_bar(self))
        QTimer.singleShot(0, self._polish_room_matrix_geometry)
        self._schedule_splitter_sync()
        QTimer.singleShot(2500, lambda: self._run_ui_probe_silent())

    def _run_ui_probe_silent(self):
        try:
            from ui_probe import schedule_ui_probe
            schedule_ui_probe(self, context="startup", delay_ms=1200)
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

    def _polish_room_matrix_geometry(self):
        if not hasattr(self, "room_matrix") or not hasattr(self, "stack"):
            return
        matrix_shown = False
        matrix_shown = bool(self.page_matrix.isVisible())
        if not matrix_shown:
            return
        if self.stack.currentIndex() != 0:
            return
        rm = self.room_matrix
        rm.sw.updateGeometry()
        rm.scroll.updateGeometry()
        rm.scroll.viewport().update()
        if not rm.cards:
            rm._load()
            self._bind()
            return
        self._bind()
        # During first paint Qt reports child widgets as not visible until their
        # ancestors are shown; use the explicit hidden flag for filter self-heal.
        vis = sum(1 for c in rm.cards.values() if not c.isHidden())
        if vis == 0:
            rm.current_filter = "ALL"
            rm.current_search = ""
            if hasattr(self, "_sync_matrix_filter_ui"):
                self._sync_matrix_filter_ui("ALL")
            rm._apply_visibility()

    def _setup_shortcuts(self):
        """全局快捷键体系。"""
        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence("F2"), self, lambda: self._hotkey("checkin"))
        QShortcut(QKeySequence("F3"), self, lambda: self._hotkey_checkout())
        QShortcut(QKeySequence("F4"), self, lambda: self._hotkey("card"))
        QShortcut(QKeySequence("F5"), self, lambda: self._hotkey("shop"))
        QShortcut(QKeySequence("F6"), self, lambda: self._hotkey_search())
        QShortcut(QKeySequence("F7"), self, lambda: self._hotkey_matrix_refresh())
        QShortcut(QKeySequence("F8"), self, lambda: self._hotkey("hk"))
        QShortcut(QKeySequence("F9"), self, lambda: self._hotkey_print_bill())
        QShortcut(QKeySequence("F10"), self, lambda: self._hotkey_toggle_fullscreen())
        QShortcut(QKeySequence("F11"), self, lambda: self._hotkey_lock_screen())
        # 改绑热键：Ctrl+1 到矩阵，Ctrl+2 到前台，Ctrl+3 到财务，Ctrl+4 到报表
        QShortcut(QKeySequence("Ctrl+1"), self, lambda: self._hotkey("matrix"))
        QShortcut(QKeySequence("Ctrl+2"), self, lambda: self._hotkey("checkin"))
        QShortcut(QKeySequence("Ctrl+3"), self, lambda: self._hotkey("finance"))
        QShortcut(QKeySequence("Ctrl+4"), self, lambda: self._hotkey("report"))
        QShortcut(QKeySequence("Ctrl+5"), self, lambda: self._hotkey("report"))
        QShortcut(QKeySequence("Ctrl+N"), self, lambda: self._hotkey("night_audit"))
        QShortcut(QKeySequence("Ctrl+S"), self, lambda: self._hotkey("shift"))
        QShortcut(QKeySequence("Ctrl+D"), self, lambda: self._hotkey("vendor_console"))
        QShortcut(QKeySequence("Ctrl+Shift+N"), self, lambda: self._hotkey_new_reservation())
        QShortcut(QKeySequence("Ctrl+P"), self, lambda: self._hotkey_print_bill())
        QShortcut(QKeySequence("Ctrl+E"), self, lambda: self._hotkey_export_csv())
        QShortcut(QKeySequence("Ctrl+L"), self, lambda: self._hotkey_lock_screen())
        QShortcut(QKeySequence("Ctrl+B"), self, lambda: self._hotkey_toggle_sidebar())
        QShortcut(QKeySequence("F12"), self, lambda: self._run_ui_probe())

    def _run_ui_probe(self):
        try:
            from ui_probe import probe_and_toast, latest_report_path
            tab = ""
            if hasattr(self, "workspace") and hasattr(self.workspace, "tabs"):
                tab = self.workspace.tabs.tabText(self.workspace.tabs.currentIndex())
            probe_and_toast(self, context=f"F12:{tab or 'main'}")
        except Exception as exc:
            logger.warning("UI probe: %s", exc)
            try:
                from ui_helpers import show_info
                from ui_probe import latest_report_path
                show_info(self, "UI 探针", f"失败: {exc}")
            except Exception:
                pass

    def _hotkey(self, action: str):
        self._execute_action(action)

    def _execute_action(self, action: str) -> None:
        self._navigate(action)

    def _hotkey_checkout(self):
        self.workspace.navigate_frontdesk("checkin")
        if hasattr(self.workspace, "checkin_tab"):
            ct = self.workspace.checkin_tab
            if hasattr(ct, "start_checkout"):
                ct.start_checkout()

    def _hotkey_matrix_refresh(self):
        self._execute_action("matrix")
        if hasattr(self.workspace, "matrix_tab") and hasattr(self.workspace.matrix_tab, "refresh"):
            self.workspace.matrix_tab.refresh()

    def _hotkey_print_bill(self):
        try:
            if hasattr(self.workspace, "checkin_tab") and hasattr(self.workspace.checkin_tab, "print_current_bill"):
                self.workspace.checkin_tab.print_current_bill()
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

    def _hotkey_toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _hotkey_lock_screen(self):
        try:
            from permission_system import PermissionManager
            pm = PermissionManager()
            pm.lock_session()
        except Exception:
            from ui_helpers import show_info
            show_info(self, i18n.t("dlg_tip"), i18n.t("msg_session_locked"))

    def _hotkey_new_reservation(self):
        try:
            from timeline_view import ReservationDialog
            dlg = ReservationDialog(self)
            dlg.exec()
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

    def _hotkey_export_csv(self):
        try:
            from i18n import i18n
            self.workspace.export_current_csv()
        except Exception:
            from ui_helpers import show_warning
            show_warning(self, i18n.t("export_title"), i18n.t("msg_export_unsupported"))

    def _hotkey_toggle_sidebar(self):
        if hasattr(self, "left_sidebar"):
            self._toggle_sidebar_collapse()

    # ══════════════════════════════════════════════════════════════════════════
    #  UI 构建 — 主入口
    # ══════════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("AppRoot")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCentralWidget(root)
        self.root_widget = root
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # 外层容器：水平布局（侧边栏 + 内容区）
        self.outer_layout = QHBoxLayout()
        self.outer_layout.setContentsMargins(0, 0, 0, 0)
        self.outer_layout.setSpacing(0)

        # ① 左侧导航栏
        self.left_sidebar = self._build_left_sidebar()
        self.outer_layout.addWidget(self.left_sidebar)

        # ② 右侧内容区
        self.right_content = QWidget()
        self.right_content.setObjectName("RightContent")
        self.right_content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        right_lay = QVBoxLayout(self.right_content)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        # ━━━ 单行智能顶栏（替代原 TopBar + ContextBar，节省 56px）━━━
        self.smart_header = SmartHeader()
        self.smart_header.sidebar_toggle_requested.connect(self._toggle_sidebar_collapse)
        self.smart_header.search_requested.connect(self._open_command_palette)
        right_lay.addWidget(self.smart_header)

        # 初始化上下文工具组件（筛选芯片、流程条、操作按钮等）
        self._init_context_tools()

        # 兼容旧代码引用桥接
        self.btn_hamburger = self.smart_header.btn_hamburger
        self.lbl_current_op = self.smart_header.lbl_current_op
        self.btn_session_user = self.smart_header.btn_session_user
        self.lbl_session_user = self.smart_header.lbl_session_user
        self.chrome_action_group = self.smart_header.chrome_action_group
        self._chrome_btn_ci = self.smart_header._chrome_btn_ci
        self._chrome_btn_co = self.smart_header._chrome_btn_co
        self.chrome_command_strip = self.smart_header.chrome_command_strip
        self.btn_chrome_nav = self.smart_header.btn_chrome_nav
        self.lbl_breadcrumb = self.smart_header.lbl_breadcrumb
        self.lbl_shift_info = self.smart_header.lbl_shift_info
        self.header_area = self.smart_header

        # ━━━ 迷你标签条（替代隐藏的 TabBar，提供位置感知）━━━
        self.mini_tab_strip = MiniTabStrip()
        right_lay.addWidget(self.mini_tab_strip)

        # 主体区域（房态 + 工作台分屏）
        self.body_container = QWidget()
        self.body_container.setObjectName("BodyContainer")
        self.body_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.body_lay = QVBoxLayout(self.body_container)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(0)

        # 堆叠模式（经典/专注）
        self.main_stack = QStackedWidget()
        self.main_stack.setObjectName("MainStack")
        self.main_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.page_matrix = self._build_center()
        self.main_stack.addWidget(self.page_matrix)
        self.main_stack.addWidget(self.workspace)
        # 动画节奏：主页面 220ms 缓出淡入加滑入
        from motion_gate import attach_stack_fade
        attach_stack_fade(self.main_stack)

        # 分割器模式（指挥台）— 注意：页面矩阵/工作台不能同时挂在堆叠与分割器下，
        # 否则添加分割器会把子控件从主堆叠抢走，导致经典模式主区域空白。
        self.cmd_splitter = QSplitter(Qt.Horizontal)
        self.cmd_splitter.setObjectName("CommandSplitter")
        # setCollapsible 须在 splitter 已有两个子控件后调用，否则 Qt 报 Index out of range
        self.cmd_splitter.setSizes([500, 500])
        self.cmd_splitter.hide()

        self.body_lay.addWidget(self.main_stack, stretch=1)
        self.body_lay.addWidget(self.cmd_splitter, stretch=1)
        # sub-c 粘性页脚：body_container 用 stretch=1，自动撑满剩余空间，
        # 把 status_bar 顶到 right_content 底部；内容不足一屏时不会留白，
        # 内容超屏时 body_container 内部 QScrollArea 接管滚动，footer 永远贴底不被覆盖。
        right_lay.addWidget(self.body_container, stretch=1)

        self.status_bar = self._rebuild_status_bar()
        right_lay.addWidget(self.status_bar)

        self.outer_layout.addWidget(self.right_content, stretch=1)
        root_lay.addLayout(self.outer_layout)

        # 浮层
        self.toast = None  # replaced by ToastManager singleton
        self.success_overlay = SuccessOverlay(self)
        self.celeb_overlay = CelebrationOverlay(self)
        self.cart_queue = 0

    # ══════════════════════════════════════════════════════════════════════════
    #  初始化上下文工具组件（供 _update_context_chrome 使用）
    # ══════════════════════════════════════════════════════════════════════════
    def _init_context_tools(self) -> None:
        """创建上下文工具按钮，这些组件不再显示在 ContextBar 中，
        而是由 SmartHeader 动态工具区按需注入。"""
        # 筛选芯片
        self._filter_chip_btns = {}
        for key, label_key in (
            ("ALL", "filter_all"),
            ("READY", "stat_chip_ready"),
            ("INHOUSE", "stat_chip_inhouse"),
            ("DIRTY", "stat_chip_dirty"),
            ("OVERTIME", "stat_chip_overtime"),
            ("MAINTENANCE", "filter_maintenance"),
        ):
            b = QPushButton(i18n.t(label_key))
            b.setObjectName("FilterChip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._on_filter_click(k))
            self._filter_chip_btns[key] = b

        # 操作按钮
        self.btn_ctx_timeline = QPushButton(i18n.t("btn_timeline_mode"))
        self.btn_ctx_timeline.setObjectName("FdGhostBtn")
        self.btn_ctx_timeline.setCursor(Qt.PointingHandCursor)
        self.btn_ctx_timeline.clicked.connect(self._toggle_view_mode)
        self.btn_view_mode = self.btn_ctx_timeline

        self.btn_ctx_batch = QPushButton(i18n.t("mw_batch_mode"))
        self.btn_ctx_batch.setObjectName("FdGhostBtn")
        self.btn_ctx_batch.setCheckable(True)
        self.btn_ctx_batch.setCursor(Qt.PointingHandCursor)
        self.btn_ctx_batch.clicked.connect(self._toggle_batch_mode)
        self.btn_batch = self.btn_ctx_batch

        self.btn_ctx_add = QPushButton(i18n.t("btn_add"))
        self.btn_ctx_add.setObjectName("FdGhostBtn")
        self.btn_ctx_add.setCursor(Qt.PointingHandCursor)
        self.btn_ctx_add.clicked.connect(self._show_add_menu)

        # 全部默认隐藏（由 _update_context_chrome 控制显隐）
        for btn in self._filter_chip_btns.values():
            btn.hide()
        self.btn_ctx_timeline.hide()
        self.btn_ctx_batch.hide()
        self.btn_ctx_add.hide()

    # ══════════════════════════════════════════════════════════════════════════
    #  增强状态栏（替代原 StatusBar，角色信息从侧栏底部迁移至此）
    # ══════════════════════════════════════════════════════════════════════════
    def _rebuild_status_bar(self) -> EnhancedStatusBar:
        """增强状态栏 — 承接角色信息。"""
        bar = EnhancedStatusBar()
        bar.btn_cart.clicked.connect(self._goto_shop_pending)
        # 兼容旧代码引用
        self.btn_cart = bar.btn_cart
        self.lbl_status_role = bar.lbl_role_badge
        self._status_diag_host = bar._status_diag_host
        self.lbl_status_db = bar.lbl_status_db
        self.lbl_status_lock = bar.lbl_status_lock
        self.lbl_status_hb = bar.lbl_status_hb
        self.lbl_status_ver = bar.lbl_status_ver
        self.lbl_status_clock = bar.lbl_status_clock
        return bar

    # ═══════════════════════════════════════════════════════════════
    #  导航逻辑区 (计划抽离为 main_window/navigation.py)
    # ═══════════════════════════════════════════════════════════════
    #
    #  侧栏导航方法 → NavigationMixin (main_window/navigation.py)



    # _build_top_bar removed — replaced by SmartHeader (line 309)
    # _build_status_bar removed — replaced by EnhancedStatusBar (line 375)
    # _build_context_bar removed — no longer used


    def _open_command_palette(self) -> None:
        if not hasattr(self, "_cmd_palette"):
            self._cmd_palette = CommandPalette(self)
            self._cmd_palette.navigated.connect(self._on_cmdk_navigate)
        self._rebuild_cmdk_commands()
        self._cmd_palette.open_palette()

    def _rebuild_cmdk_commands(self) -> None:
        cmds = []
        for btn, act in self._sidebar_actions:
            if btn.isVisible():
                cmds.append((act, btn.text(), i18n.t("cmdk_nav"), lambda a=act: self._on_sidebar_menu_clicked(a)))
        cmds.append(("settings", i18n.t("nav_settings"), "", self._settings))
        self._cmd_palette.set_commands(cmds)

    def _on_cmdk_navigate(self, payload: str) -> None:
        if payload.startswith("room:"):
            rid = payload.split(":", 1)[1]
            self._on_sidebar_menu_clicked("matrix")
            self.room_matrix.search_rooms(rid)
            for card in self.room_matrix.cards.values():
                if card.room_id == rid:
                    card.clicked.emit(rid, card.room_type)
                    break

    def _init_toast(self):
        """通知系统 — 使用 ToastManager 单例。"""
        ToastManager.instance().set_parent(self)
        from event_bus import bus
        bus.toast_requested.connect(self._show_toast)

    def _show_toast(self, text: str):
        ToastManager.instance().show(text, ToastType.INFO, duration=2200)

    def _update_context_chrome(self, action: str) -> None:
        """动态更新 SmartHeader 的面包屑和上下文工具区。"""
        is_matrix = action == "matrix"

        # ── 清空 SmartHeader 动态工具区 ──
        self.smart_header.clear_context_tools()

        # ── 更新面包屑和工具区 ──
        if action == "checkin":
            # 收银台：面包屑 + 流程条
            ct = self.workspace.checkin_tab
            rid = getattr(ct, "current_room", None) or ""
            page_name = i18n.t("tab_checkin")
            self.smart_header.set_breadcrumb(
                i18n.t("nav_group_frontdesk"),
                page_name + (f" · {rid}" if rid else ""),
            )

        elif is_matrix:
            # 房态矩阵：面包屑 + 筛选芯片 + 操作按钮
            self.smart_header.set_breadcrumb("", i18n.t("ctx_page_matrix"))

            # 筛选芯片注入动态工具区（先加父级再 show，避免无父窗口闪现）
            for key in ("ALL", "READY", "INHOUSE", "DIRTY", "OVERTIME", "MAINTENANCE"):
                btn = self._filter_chip_btns.get(key)
                if btn:
                    self.smart_header.add_context_widget(btn)
                    btn.show()

            # 操作按钮（先加父级再 show）
            if hasattr(self, "btn_ctx_timeline"):
                self.smart_header.add_context_widget(self.btn_ctx_timeline)
                self.btn_ctx_timeline.show()
            if hasattr(self, "btn_ctx_batch"):
                self.smart_header.add_context_widget(self.btn_ctx_batch)
                self.btn_ctx_batch.setVisible(
                    PermissionManager.has_permission("batch_create")
                )
            if hasattr(self, "btn_ctx_add"):
                self.smart_header.add_context_widget(self.btn_ctx_add)
                self.btn_ctx_add.show()

        else:
            # 其他页面：面包屑 + 留空
            group_name = self._action_group_map.get(action, "")
            btn_text = ""
            for btn, act in self._sidebar_actions:
                if act == action:
                    btn_text = btn.text()
                    break
            self.smart_header.set_breadcrumb(group_name, btn_text)

        # ── 隐藏不在当前页面使用的组件 ──
        if not is_matrix:
            for btn in self._filter_chip_btns.values():
                btn.hide()
            if hasattr(self, "btn_ctx_timeline"):
                self.btn_ctx_timeline.hide()
            if hasattr(self, "btn_ctx_batch"):
                self.btn_ctx_batch.hide()
            if hasattr(self, "btn_ctx_add"):
                self.btn_ctx_add.hide()

    # ══════════════════════════════════════════════════════════════════════════
    #  主体（房态矩阵 / 时间轴）
    # ══════════════════════════════════════════════════════════════════════════
    def _build_center(self) -> QWidget:
        outer = QWidget()
        outer.setObjectName("MatrixPage")
        outer.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        # 勿设过大 minimumWidth，否则与右侧工作台最小宽叠加后首屏必须手拉分隔条
        outer.setMinimumWidth(280)
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # 不再套外层 QScrollArea：与 RoomMatrix 内层 MatrixScroll 双嵌套时，Qt 常把内容区高度算成 0 → 房卡整片空白
        from ui_surface import SurfacePanel
        container = SurfacePanel(parent=outer)
        container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        container.layout_().setContentsMargins(4, 2, 4, 4)
        container.layout_().setSpacing(0)

        self.stack = QStackedWidget()
        self.stack.setObjectName("MatrixStack")
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.room_matrix = RoomMatrix()
        self.room_matrix.set_external_chrome(True)
        self.room_matrix.set_empty_handlers(self._add, self._open_setup_wizard)
        self.timeline_view = TimelineView()
        self.stack.addWidget(self.room_matrix)
        self.stack.addWidget(self.timeline_view)
        from motion_gate import attach_stack_fade
        attach_stack_fade(self.stack)

        container.layout_().addWidget(self.stack, stretch=1)

        outer_lay.addWidget(container, stretch=1)
        return outer

    def _apply_layout(self, mode=None):
        """唯一壳布局：侧栏折叠 + 房态/工作台分屏。"""
        self._layout_mode = self.LAYOUT_SHELL
        db.set_config("layout", self.LAYOUT_SHELL)
        self._apply_shell_layout()

    def _embed_body_in_splitter(self):
        """将房态区 + 工作台挂到指挥台 QSplitter。"""
        if self.page_matrix.parent() is self.main_stack:
            self.page_matrix.setParent(None)
            self.workspace.setParent(None)
        if self.page_matrix.parent() is not self.cmd_splitter:
            self.cmd_splitter.addWidget(self.page_matrix)
        if self.workspace.parent() is not self.cmd_splitter:
            self.cmd_splitter.addWidget(self.workspace)
        self.cmd_splitter.setStretchFactor(0, 13)
        self.cmd_splitter.setStretchFactor(1, 7)
        self.cmd_splitter.setCollapsible(0, False)
        self.cmd_splitter.setCollapsible(1, False)
        if not getattr(self, "_splitter_ratio_hooked", False):
            self._splitter_ratio_hooked = True
            self.cmd_splitter.splitterMoved.connect(self._on_cmd_splitter_moved)

    def _apply_shell_layout(self):
        """Lovable：侧栏图标+文字 + 左房态与右工作台同屏。"""
        self.left_sidebar.show()
        self.left_sidebar.setFixedWidth(SIDEBAR_WIDTH)
        self.left_sidebar.setProperty("collapsed", False)
        self.left_sidebar.style().unpolish(self.left_sidebar)
        self.left_sidebar.style().polish(self.left_sidebar)
        if hasattr(self, "brand_wordmark"):
            self.brand_wordmark.show()
        for btn, _ in self._sidebar_actions:
            btn.set_collapsed(False)
        for lbl in self._sidebar_group_labels:
            lbl.show()

        self.main_stack.hide()
        self.body_lay.removeWidget(self.main_stack)
        self.body_lay.removeWidget(self.cmd_splitter)
        self._embed_body_in_splitter()
        self.body_lay.addWidget(self.cmd_splitter, stretch=1)
        self.cmd_splitter.show()
        self.page_matrix.show()
        self.workspace.show()
        self._schedule_splitter_sync()

        # Header + TopBar 正常
        self.header_area.show()

        self.setMinimumSize(1024, 640)
        if self.width() < 1100:
            self.resize(1366, 800)

    def _schedule_splitter_sync(self) -> None:
        """首帧布局未完成时 width 常为 0，需延迟多次同步分屏比例。"""
        if getattr(self, "_layout_mode", None) != self.LAYOUT_SHELL:
            return
        for ms in (0, 60, 180, 400):
            QTimer.singleShot(ms, self._sync_command_splitter_sizes)

    def _shell_splitter_ratio(self) -> float:
        try:
            ratio = float(db.get_config("shell_splitter_ratio") or "0.64")
        except (TypeError, ValueError):
            ratio = 0.64
        return max(0.50, min(0.74, ratio))

    def _on_cmd_splitter_moved(self, _pos: int, _index: int) -> None:
        sizes = self.cmd_splitter.sizes()
        total = sum(sizes)
        if total > 80:
            db.set_config("shell_splitter_ratio", f"{sizes[0] / total:.4f}")

    def _sync_command_splitter_sizes(self):
        """分屏：左房态约 64%、右工作台约 36%，且总和不超过可用宽度（避免右侧被裁切）。"""
        if getattr(self, "_layout_mode", None) != self.LAYOUT_SHELL:
            return
        if not self.cmd_splitter.isVisible():
            return
        tw = self.cmd_splitter.width()
        if tw < 80:
            return
        hw = self.cmd_splitter.handleWidth()
        avail = max(0, tw - hw)
        mx_min, ws_min = 280, 300
        last = getattr(self, "_last_focus_action", None)
        if last in self._FOCUS_MATRIX:
            self.cmd_splitter.setSizes([max(avail - 1, mx_min), 1])
            return
        if last in self._FOCUS_OVERVIEW or last in self._FOCUS_WORKSPACE_FULL:
            self.cmd_splitter.setSizes([1, max(avail - 1, ws_min)])
            return
        if last in self._FOCUS_SPLIT:
            ratio = 0.55
        else:
            ratio = self._shell_splitter_ratio()
        left = int(avail * ratio)
        right = avail - left
        if right < ws_min:
            right = ws_min
            left = avail - right
        if left < mx_min:
            left = mx_min
            right = max(ws_min, avail - left)
        if left + right > avail:
            left = int(avail * ratio)
            right = avail - left
        left = max(1, min(left, avail - 1))
        right = max(1, avail - left)
        self.cmd_splitter.setSizes([left, right])

    def _apply_focus_mode(self, action: str) -> None:
        """根据 action 切换 cmd_splitter 左右比例、matrix/workspace 显隐。"""
        if not hasattr(self, "cmd_splitter") or not self.cmd_splitter.isVisible():
            return
        tw = max(self.cmd_splitter.width(), 100)
        if action in self._FOCUS_MATRIX:
            self.page_matrix.show()
            self.workspace.show()
            self.cmd_splitter.setSizes([tw - 2, 1])
        elif action in self._FOCUS_OVERVIEW or action in self._FOCUS_WORKSPACE_FULL:
            self.page_matrix.show()
            self.workspace.show()
            self.cmd_splitter.setSizes([1, tw - 2])
        else:
            self._sync_command_splitter_sizes()
        self._last_focus_action = action

    # ══════════════════════════════════════════════════════════════════════════
    #  登录后会话 / 角色导航
    # ══════════════════════════════════════════════════════════════════════════
    def apply_session_after_login(self) -> None:
        """登录成功后裁剪菜单、写入岗位默认层级并进入首页。"""
        role = PermissionManager.current_role()
        seed_role_layer_preset(role)
        self._apply_role_navigation()
        self._update_session_chrome()
        self._apply_frontdesk_layers()
        QTimer.singleShot(0, self._navigate_role_home)
        self._schedule_splitter_sync()


    # _apply_role_navigation / _rebuild_chrome_nav_menu → NavigationMixin

    def _rebuild_user_menu(self) -> None:
        if not hasattr(self, "btn_session_user"):
            return
        menu = QMenu(self)
        u = PermissionManager.current_user()
        if u:
            role_txt = role_display_name(PermissionManager.current_role())
            menu.addAction(f"{u.get('display_name', '')} · {role_txt}").setEnabled(False)
            menu.addSeparator()
        if PermissionManager.has_permission("settings_view"):
            act_set = menu.addAction(i18n.t("nav_settings"))
            act_set.triggered.connect(self._settings)
        act_cmdk = menu.addAction(i18n.t("cmdk_open"))
        act_cmdk.triggered.connect(self._open_command_palette)
        menu.addSeparator()
        act_logout = menu.addAction(i18n.t("btn_logout"))
        act_logout.triggered.connect(self._logout_account)
        self.btn_session_user.setMenu(menu)

    def _logout_account(self) -> None:
        from ui_helpers import ask_confirm
        from permission_system import PermissionManager, ensure_authenticated

        if not ask_confirm(self, i18n.t("btn_logout"), i18n.t("btn_logout") + "?"):
            return
        PermissionManager.logout()
        self.close()
        if not ensure_authenticated():
            QApplication.quit()
            return
        win = MainWindow()
        win.show()

    def _update_session_chrome(self) -> None:
        u = PermissionManager.current_user()
        role = PermissionManager.current_role()
        if u:
            role_txt = role_display_name(role)
            display_name = u.get("display_name", "")
            tip = i18n.t("topbar_user_tip_named").format(
                name=display_name, role=role_txt
            )

            # SmartHeader 用户信息
            if hasattr(self, "btn_session_user"):
                apply_avatar_to_toolbutton(self.btn_session_user, role, tip)
            if hasattr(self, "lbl_current_op"):
                self.lbl_current_op.setText(f"{display_name} ({role_txt})")
                self.lbl_current_op.setProperty("loggedIn", True)
                self.lbl_current_op.style().unpolish(self.lbl_current_op)
                self.lbl_current_op.style().polish(self.lbl_current_op)

            # EnhancedStatusBar 角色信息（侧栏角色条已迁移至此）
            if hasattr(self, "status_bar") and isinstance(self.status_bar, EnhancedStatusBar):
                self.status_bar.set_role_info(display_name, role_txt)
            elif hasattr(self, "lbl_status_role"):
                self.lbl_status_role.setText(
                    i18n.t("status_role_line").format(role=role_txt)
                )

            # 诊断信息可见性
            if hasattr(self, "_status_diag_host"):
                show_diag = PermissionManager.has_permission("debug_panel")
                self._status_diag_host.setVisible(show_diag)

            self._rebuild_user_menu()
        else:
            if hasattr(self, "status_bar") and isinstance(self.status_bar, EnhancedStatusBar):
                self.status_bar.clear_role_info()
            elif hasattr(self, "lbl_status_role"):
                self.lbl_status_role.setText("")
            if hasattr(self, "lbl_current_op"):
                self.lbl_current_op.setText(i18n.t("topbar_not_logged_in"))
                self.lbl_current_op.setProperty("loggedIn", False)
                self.lbl_current_op.style().unpolish(self.lbl_current_op)
                self.lbl_current_op.style().polish(self.lbl_current_op)


    # ══════════════════════════════════════════════════════════════════════════
    #  迷你标签条（MiniTabStrip）初始化与同步
    # ══════════════════════════════════════════════════════════════════════════

    def _init_mini_tab_strip(self) -> None:
        """初始化迷你标签条：将 WorkspaceDock 的标签同步到 MiniTabStrip。"""
        if not hasattr(self, "mini_tab_strip") or not hasattr(self, "workspace"):
            return

        # 采集标签名
        labels = []
        for i in range(self.workspace.tabs.count()):
            text = self.workspace.tabs.tabText(i)
            # 去掉 emoji 前缀，保留纯文字
            clean = text
            for j, ch in enumerate(text):
                if ch.isalpha() or '\u4e00' <= ch <= '\u9fff':
                    clean = text[j:].strip()
                    break
            labels.append(clean if clean else text)

        self.mini_tab_strip.set_tabs(labels)
        self.mini_tab_strip.tab_clicked.connect(self._on_mini_tab_clicked)
        self.workspace.tabs.currentChanged.connect(self._sync_mini_tab_strip)

    def _on_mini_tab_clicked(self, index: int) -> None:
        """迷你标签条点击 -> 切换工作台标签页。"""
        if hasattr(self, "workspace") and 0 <= index < self.workspace.tabs.count():
            self.workspace.tabs.setCurrentIndex(index)
            # 同步侧栏高亮
            self._sync_sidebar_from_tab_index(index)

    def _sync_mini_tab_strip(self, workspace_index: int) -> None:
        """工作台标签切换 -> 同步 MiniTabStrip 高亮。"""
        if hasattr(self, "mini_tab_strip"):
            self.mini_tab_strip.set_active_index(workspace_index)

    def _sync_sidebar_from_tab_index(self, index: int) -> None:
        """从工作台标签索引反推侧栏高亮。"""
        if not hasattr(self, "workspace"):
            return
        for key, widget in self.workspace._tab_refs.items():
            idx = self.workspace.tab_index(key)
            if idx == index:
                self._sync_sidebar_state(key)
                break


    # _navigate_role_home / _sync_sidebar_state / _on_sidebar_menu_clicked
    # _navigate_to / _focus_workspace_* → NavigationMixin

    def _apply_frontdesk_layers(self):
        """按「系统设置 → 酒店基础 → 前台显示层级」颗粒化显示房态区与工作台枢纽。"""
        from frontdesk_layers import OPS_CHIP_KEYS, ROOM_CHIP_KEYS, get_frontdesk_layers, layer_is_on

        ly = get_frontdesk_layers(db)
        if hasattr(self, "btn_cart"):
            self.btn_cart.setVisible(layer_is_on(ly, "stats_bar", "pending_cart"))
        if hasattr(self, "btn_ctx_timeline"):
            self.btn_ctx_timeline.setVisible(layer_is_on(ly, "stats_bar", "timeline_toggle"))
        if hasattr(self, "btn_ctx_batch"):
            self.btn_ctx_batch.setVisible(layer_is_on(ly, "stats_bar", "batch_mode"))
        if hasattr(self, "workspace"):
            self.workspace.apply_frontdesk_layers(ly)

    # ══════════════════════════════════════════════════════════════════════════
    #  信号连接
    # ══════════════════════════════════════════════════════════════════════════
    def _install_checkin_shortcuts(self) -> None:
        ct = self.workspace.checkin_tab

        def _focus_pay():
            if hasattr(ct, "txt_amount"):
                ct.txt_amount.setFocus()
                ct.txt_amount.selectAll()

        sc_f2 = QShortcut(QKeySequence("F2"), self)
        sc_f2.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc_f2.activated.connect(_focus_pay)

        sc_f3 = QShortcut(QKeySequence("F3"), self)
        sc_f3.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc_f3.activated.connect(lambda: ct._issue_card_clicked() if hasattr(ct, "_issue_card_clicked") else None)

        sc_ent = QShortcut(QKeySequence("Return"), self)
        sc_ent.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_ent.activated.connect(lambda: ct.btn_commit.click() if getattr(ct, "btn_commit", None) and ct.btn_commit.isEnabled() else None)

    def _connect_signals(self):
        bus.cart_received.connect(self._on_cart)
        bus.ledger_updated.connect(self._on_ledger_updated)
        bus.room_status_changed.connect(lambda *_: self._refresh())
        bus.layout_changed.connect(self._apply_layout)
        bus.guest_called.connect(self._on_guest_call)
        bus.request_screenshot.connect(self._on_request_shot)
        bus.cloud_order_received.connect(self._on_cloud_order)
        bus.cloud_service_request.connect(self._on_cloud_service)
        bus.kill_switch_triggered.connect(self._on_kill_switch)
        bus.lock_level_changed.connect(self._apply_lock_level)
        bus.vendor_toast.connect(self._on_vendor_toast)
        bus.show_warning.connect(self._on_show_warning)
        bus.frontdesk_layers_changed.connect(self._apply_frontdesk_layers)
        bus.user_logged_in.connect(lambda *_: self.apply_session_after_login())
        if hasattr(self, "room_matrix"):
            self.room_matrix.room_selected.connect(self._on_card)
            self._bind()
        try:
            from health_monitor import health_monitor
            health_monitor.offline_detected.connect(self._on_offline_lock)
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)
        self._apply_layout()
        self._apply_frontdesk_layers()
        install_command_palette_shortcut(self, self._open_command_palette)
        self._install_checkin_shortcuts()
        self._apply_lock_level()

    def _on_ledger_updated(self, *args):
        self._refresh()
        if hasattr(self, "workspace"):
            try:
                self.workspace.finance_tab.refresh()
                self.workspace.shift_tab._refresh_expected()
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    #  数据刷新
    # ══════════════════════════════════════════════════════════════════════════
    def _refresh(self):
        try:
            if hasattr(self, "room_matrix") and hasattr(self, "_sync_matrix_filter_ui"):
                cur = getattr(self.room_matrix, "current_filter", None) or "ALL"
                self._sync_matrix_filter_ui(cur)
            if hasattr(self, "btn_cart"):
                self.btn_cart.setText(f"{i18n.t('cart_pending')}: {self.cart_queue}")

            if hasattr(self, "lbl_pending_orders"):
                try:
                    pending = db.execute(
                        "SELECT COUNT(*) FROM pending_carts WHERE status='PENDING'"
                    ).fetchone()[0]
                    self.lbl_pending_orders.setText(i18n.t("mw_pending_orders").format(pending=pending))
                except Exception:
                    pass
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  快速入住弹窗
    # ══════════════════════════════════════════════════════════════════════════
    def _show_checkin_dialog(self, rid: str, rt: str, price: float):
        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("mw_quick_checkin_title").format(rid=rid))
        style_dialog(dlg, size="compact")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        lay.addWidget(
            build_dialog_header(
                i18n.t("mw_quick_register_title").format(rid=rid),
                i18n.t("mw_quick_register_sub").format(
                    rt=rt, sym=i18n.t("currency_symbol"), price=int(price)
                ),
            )
        )

        from PySide6.QtWidgets import QFormLayout
        form = QFormLayout()
        form.setSpacing(10)

        txt_guest = QLineEdit()
        txt_guest.setPlaceholderText(i18n.t("mw_ph_guest_required"))
        form.addRow(i18n.t("label_guest_name") + ":", txt_guest)

        txt_phone = QLineEdit()
        txt_phone.setPlaceholderText(i18n.t("mw_ph_phone_optional"))
        form.addRow(i18n.t("label_phone") + ":", txt_phone)

        spin_days = QSpinBox()
        spin_days.setRange(1, 365)
        spin_days.setValue(1)
        spin_days.setSuffix(i18n.t("mw_suffix_nights"))
        form.addRow(i18n.t("mw_label_stay_nights"), spin_days)

        lay.addLayout(form)
        lay.addStretch()

        btn_confirm = QPushButton(i18n.t("mw_btn_confirm_checkin"))
        btn_confirm.setObjectName("SolidPrimaryBtn")
        btn_confirm.setMaximumHeight(56)
        btn_confirm.setCursor(Qt.PointingHandCursor)
        lay.addWidget(btn_confirm)

        def _do_checkin():
            from permission_system import PermissionManager
            if not PermissionManager.has_permission("checkin"):
                from ui_helpers import show_warning
                show_warning(dlg, i18n.t("perm_denied"), i18n.t("perm_no_checkin"))
                return
            guest = txt_guest.text().strip()
            if not guest:
                show_warning(dlg, i18n.t("dlg_tip"), i18n.t("msg_guest_name_empty"))
                return
            phone = txt_phone.text().strip()
            days = spin_days.value()
            try:
                if hasattr(self, "workspace"):
                    self.workspace.checkin_tab.prefill_quick_checkin(rid, rt, price, guest, phone, days)
                    self.workspace.focus_checkin_tab()
                    self._sync_sidebar_state("checkin")
                    self._sync_chrome_frontdesk_actions("checkin")
                self.toast.show_toast(i18n.t("mw_qc_nav_toast").format(rid=rid))
                dlg.accept()
            except Exception as e:
                from ui_helpers import show_error
                show_error(dlg, i18n.t("mw_checkin_fail"), str(e))

        btn_confirm.clicked.connect(_do_checkin)
        dlg.exec()

    def _do_refresh(self):
        self.room_matrix._load()
        self._bind()
        self._refresh()

    # ══════════════════════════════════════════════════════════════════════════
    #  视图切换
    # ══════════════════════════════════════════════════════════════════════════
    def _toggle_view_mode(self):
        if self.stack.currentIndex() == 0:
            db.log_action(_current_actor_id(), "SWITCH_VIEW", "TIMELINE")
            self.stack.setCurrentIndex(1)
            self.btn_view_mode.setText(i18n.t("btn_matrix_mode"))
            self.timeline_view._refresh()
        else:
            db.log_action(_current_actor_id(), "SWITCH_VIEW", "MATRIX")
            self.stack.setCurrentIndex(0)
            self.btn_view_mode.setText(i18n.t("btn_timeline_mode"))
            self.room_matrix._load()
            self._bind()

    # ══════════════════════════════════════════════════════════════════════════
    #  筛选 / 搜索
    # ══════════════════════════════════════════════════════════════════════════
    def _sync_matrix_filter_ui(self, ft: str):
        """ContextBar 筛选芯片 + 旧 StatChip 高亮。"""
        if hasattr(self, "_filter_chip_btns"):
            for code, btn in self._filter_chip_btns.items():
                btn.blockSignals(True)
                btn.setChecked(code == ft)
                btn.blockSignals(False)

    def _on_filter_click(self, ft):
        self._sync_matrix_filter_ui(ft)
        if hasattr(self, "room_matrix"):
            self.room_matrix.filter_rooms(ft)

    def _on_search(self, text):
        text = (text or "").strip()
        if len(text) > 2:
            db.log_action(_current_actor_id(), "SEARCH", text)
        
        # 房态搜索（现有逻辑）
        if text:
            self.room_matrix.search_rooms(text)
        
        # 全局搜索增强：客人/房间/预订/账单
        if len(text) >= 1:
            self._global_search_results(text)

    def _global_search_results(self, text: str):
        """跨模块搜索：客人姓名/手机-房间号-预订号-账单。结果显示在下拉面板。"""
        results = []
        search_pattern = f"%{text}%"

        # 1. 搜客人
        try:
            guests = db.execute(
                "SELECT g.name, g.phone, g.room_id, r.status FROM guests g "
                "LEFT JOIN rooms r ON r.room_id=g.room_id "
                "WHERE g.name LIKE ? OR g.phone LIKE ? ORDER BY g.checkin_time DESC LIMIT 3",
                (search_pattern, search_pattern),
            ).fetchall()
            for g in guests:
                results.append(("guest", f"{g[0]} · {g[1] or '无电话'} · {g[2]} · {g[3] or '—'}", g[2]))
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

        # 2. 搜房间
        try:
            rooms = db.execute(
                "SELECT room_id, room_type, status, note FROM rooms WHERE room_id LIKE ? OR note LIKE ? LIMIT 3",
                (search_pattern, search_pattern),
            ).fetchall()
            for r in rooms:
                results.append(("room", f"{r[0]} · {r[1] or '—'} · {r[2]} · {r[3] or ''}", r[0]))
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

        # 3. 搜账单
        try:
            bills = db.execute(
                "SELECT id, room_id, amount, tx_type FROM ledger WHERE room_id LIKE ? OR note LIKE ? ORDER BY id DESC LIMIT 3",
                (search_pattern, search_pattern),
            ).fetchall()
            for b in bills:
                results.append(("bill", f"账单#{b[0]} · {b[1]} · ${b[2] or 0:.0f} · {b[3]}", b[1]))
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

        # 4. 搜预订
        try:
            from timeline_view import get_reservations
            reservations = db.execute(
                "SELECT room_id, guest_name, phone, checkin_time FROM reservations WHERE guest_name LIKE ? OR phone LIKE ? LIMIT 3",
                (search_pattern, search_pattern),
            ).fetchall()
            for res in reservations:
                results.append(("reservation", f"{res[1]} · {res[0]} · {res[3] or '—'}", res[0]))
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  卡片绑定
    # ══════════════════════════════════════════════════════════════════════════
    def _bind(self):
        """房卡重建后重绑；与房间矩阵选中信号双通道，避免漏连。"""
        if not hasattr(self, "room_matrix"):
            return
        for card in self.room_matrix.cards.values():
            try:
                card.clicked.disconnect(self._on_card)
            except (TypeError, RuntimeError):
                pass
            card.clicked.connect(self._on_card)

    def _on_card(self, rid, rt):
        if not hasattr(self, "workspace"):
            return
        try:
            ct = self.workspace.checkin_tab
            ct.update_room(rid, rt, None)
            self.workspace.focus_checkin_tab()
            self._highlight_room_card(rid)
            self._sync_sidebar_state("checkin")
            self._sync_chrome_frontdesk_actions("checkin")
            self._update_context_chrome("checkin")
            hub = getattr(self.workspace, "frontdesk_hub", None)
            if hub and hub.stack.currentWidget():
                scroll = hub.stack.currentWidget()
                if isinstance(scroll, QScrollArea):
                    scroll.verticalScrollBar().setValue(0)
        except Exception as e:
            from ui_helpers import show_error
            show_error(self, i18n.t("dlg_tip"), str(e))

    def _highlight_room_card(self, rid: str) -> None:
        if not hasattr(self, "room_matrix"):
            return
        for room_id, card in self.room_matrix.cards.items():
            card.setProperty("selectedRoom", room_id == rid)
            card.style().unpolish(card)
            card.style().polish(card)

    # ══════════════════════════════════════════════════════════════════════════
    #  快捷入住 / 退房
    # ══════════════════════════════════════════════════════════════════════════
    def _ci(self):
        rs = db.execute("SELECT room_id,room_type FROM rooms WHERE status='READY'").fetchall()
        if not rs:
            show_warning(self, i18n.t("table_op"), i18n.t("msg_no_rooms"))
            return
        ids = [f"{r[0]} ({r[1]})" for r in rs]
        c, ok = QInputDialog.getItem(self, i18n.t("btn_checkin"), i18n.t("table_room") + ":", ids, 0, False)
        idx = ids.index(c) if ok and c else -1
        if ok and 0 <= idx < len(rs):
            rid, rt = rs[idx]
            price = db.get_rate_for_room_type(rt, "standard")
            self._show_checkin_dialog(rid, rt, price)

    def _co(self):
        rs = db.execute("SELECT room_id,room_type FROM rooms WHERE status='INHOUSE'").fetchall()
        if not rs:
            show_warning(self, i18n.t("table_op"), i18n.t("msg_no_inhouse"))
            return
        ids = [f"{r[0]} ({r[1]})" for r in rs]
        c, ok = QInputDialog.getItem(self, i18n.t("btn_checkout"), i18n.t("table_room") + ":", ids, 0, False)
        if ok and c:
            rid = c.split(" ")[0]

            def _tx_logic():
                db.execute("UPDATE rooms SET status='DIRTY' WHERE room_id=?", (rid,))
                db.append_ledger("ROOM_OUT", 0, "SYSTEM", 1, rid, i18n.t("ledger_note_quick_co"))

            try:
                db.run_transaction(_tx_logic)
                bus.room_status_changed.emit(rid, "DIRTY")
                self.toast.show_toast(i18n.t("msg_checkout_success").format(rid))
            except Exception as e:
                from ui_helpers import show_error
                show_error(self, i18n.t("table_op"), str(e))

    def _add(self):
        if not PermissionManager.has_permission("batch_create"):
            show_warning(self, i18n.t("btn_add"), i18n.t("perm_denied"))
            return
        dlg = BatchCreateRoomDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.room_matrix._load()
            self._bind()

    def _add_single(self):
        if not PermissionManager.has_permission("batch_create"):
            show_warning(self, i18n.t("btn_add"), i18n.t("perm_denied"))
            return
        dlg = CreateSingleRoomDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.room_matrix._load()
            self._bind()

    def _import_rooms_from_mdb(self):
        if not PermissionManager.has_permission("batch_create"):
            show_warning(self, i18n.t("btn_add"), i18n.t("perm_denied"))
            return
        dlg = ImportRoomsFromMdbDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.room_matrix._load()
            self._bind()

    def _show_add_menu(self):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        a_import = menu.addAction(i18n.t("add_menu_import_from_legacy"))
        a_batch = menu.addAction(i18n.t("add_menu_batch_create"))
        a_single = menu.addAction(i18n.t("add_menu_single_room"))
        try:
            from batch_create_dialog import _candidate_mdb_paths
            has_mdb = any(p.is_file() for p in _candidate_mdb_paths())
        except Exception:
            has_mdb = False
        a_import.setEnabled(has_mdb)
        if not has_mdb:
            a_import.setToolTip(i18n.t("add_menu_no_mdb_tip"))
        a_import.triggered.connect(self._import_rooms_from_mdb)
        a_batch.triggered.connect(self._add)
        a_single.triggered.connect(self._add_single)
        btn = getattr(self, "btn_ctx_add", None)
        if btn is not None:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            menu.exec()

    def _open_setup_wizard(self):
        if not PermissionManager.has_permission("batch_create"):
            show_warning(self, i18n.t("matrix_empty_btn_wizard"), i18n.t("perm_denied"))
            return
        from setup_wizard import SetupWizard
        wiz = SetupWizard(self)
        if wiz.exec() == QDialog.DialogCode.Accepted:
            self.room_matrix._load()
            self._bind()

    # ══════════════════════════════════════════════════════════════════════════
    #  云端 / IoT 信号处理
    # ══════════════════════════════════════════════════════════════════════════
    def _on_cart(self, data=None):
        try:
            pending = db.execute(
                "SELECT COUNT(*) FROM pending_carts WHERE status='PENDING'"
            ).fetchone()[0]
        except Exception:
            pending = 0
        self.cart_queue = pending
        if hasattr(self, "btn_cart"):
            self.btn_cart.setText(f"{i18n.t('cart_pending')}: {pending}")

        d = data if isinstance(data, dict) else {}
        # 仅"新订单到达"才播声音+通知；FULFILLED/CANCELLED 只静默刷新计数
        is_new_order = bool(
            d.get("cart_id")
            and not d.get("fulfilled")
            and not d.get("cancelled")
        )

        # 同步刷新前台超市页待处理区域（不切走当前标签页）
        try:
            if hasattr(self, "workspace") and hasattr(self.workspace, "shop_tab"):
                self.workspace.shop_tab.refresh()
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

        if not is_new_order:
            return

        # ── 声音通知（前台必听）
        try:
            from sound_helper import play_notify
            play_notify()
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

        # ── 醒目通知：客房 / 件数 / 金额 / 付款 / 找零
        try:
            sym = db.get_config("currency_symbol") or "¥"
            room = str(d.get("room_id") or "?")
            amount = float(d.get("amount") or 0)
            pay = str(d.get("payment_method") or "CASH").upper()
            change = float(d.get("cash_change") or 0)
            items = d.get("items") or []
            n_items = sum(int(it.get("qty") or 0) for it in items) if items else 1
            pay_part = (
                f"现金 {sym}{float(d.get('cash_received') or 0):.0f}"
                + (f" / 找零 {sym}{change:.0f}" if change > 0.005 else "")
                if pay == "CASH"
                else f"抵押金 {sym}{amount:.0f}"
            )
            toast_text = (
                f"客房点单 · 房间 {room} · {n_items} 件 · {sym}{amount:.0f}"
                f"\n{pay_part}　 ｜ 共 {self.cart_queue} 单待处理"
            )
        except Exception:
            toast_text = f"新客房订单 · 共 {self.cart_queue} 单待处理"
        if hasattr(self, "toast"):
            try:
                self.toast.show_toast(toast_text[:280])
            except Exception:
                pass

        # ── 闪烁 btn_cart 3 秒 — 使用 QSS 属性驱动
        if hasattr(self, "btn_cart"):
            try:
                self.btn_cart.setProperty("flash", True)
                self.btn_cart.style().unpolish(self.btn_cart)
                self.btn_cart.style().polish(self.btn_cart)
                QTimer.singleShot(
                    3000,
                    lambda: (
                        self.btn_cart.setProperty("flash", False),
                        self.btn_cart.style().unpolish(self.btn_cart),
                        self.btn_cart.style().polish(self.btn_cart),
                    )
                    if hasattr(self, "btn_cart") else None,
                )
            except Exception:
                pass

    def _goto_shop_pending(self):
        """点击顶部购物车计数 → 跳到前台超市页（待处理订单可视）。"""
        try:
            if hasattr(self, "workspace") and hasattr(self.workspace, "navigate_frontdesk"):
                self.workspace.navigate_frontdesk("shop")
                if hasattr(self.workspace, "shop_tab"):
                    self.workspace.shop_tab.refresh()
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

    def _on_guest_call(self, room_id):
        from ui_helpers import show_info
        show_info(self, i18n.t("mw_guest_call_title"), i18n.t("mw_guest_call_msg").format(room_id))

    def _on_request_shot(self):
        pass

    def _on_cloud_order(self, data):
        if hasattr(self, "workspace"):
            try:
                self.workspace.shop_tab.refresh()
            except Exception:
                pass

    def _on_cloud_service(self, data):
        if hasattr(self, "workspace"):
            try:
                self.workspace.service_tab.refresh()
            except Exception:
                pass

    def _on_kill_switch(self):
        from ui_helpers import show_warning
        show_warning(self, i18n.t("mw_kill_switch_title"), i18n.t("mw_kill_switch_msg"))
        self.close()

    def _on_show_warning(self, title: str, message: str = ""):
        """云端心跳等后台告警用通知，避免模态框挡住房态点击与收银操作。"""
        title = (title or "").strip()
        message = (message or "").strip()
        if title == i18n.t("err_cloud_connection") or "心跳" in title or "心跳" in message:
            brief = title if not message else f"{title} — {message.splitlines()[0]}"
            if hasattr(self, "toast"):
                self.toast.show_toast(brief[:240])
            return
        from ui_helpers import show_warning
        body = f"{title}\n\n{message}" if title and message else (title or message)
        show_warning(self, i18n.t("dlg_tip"), body)

    def _apply_lock_level(self, level: str = ""):
        try:
            from vendor_lockdown import (
                LOCK_ALL, LOCK_GUEST_BOT, LOCK_REPORTS, LOCK_WARNING_BANNER,
                current_lock_level, lock_message,
            )
            lv = (level or current_lock_level() or "").strip().upper()
            msg = lock_message(lv)
            if msg and hasattr(self, "toast"):
                self.toast.show_toast(msg[:240])
            if hasattr(self, "workspace") and hasattr(self.workspace, "tabs"):
                tabs = self.workspace.tabs
                for idx in range(tabs.count()):
                    tabs.setTabEnabled(idx, True)
                if lv == LOCK_REPORTS:
                    for key in ("report", "dashboard", "audit"):
                        idx = self.workspace.tab_index(key)
                        if 0 <= idx < tabs.count():
                            tabs.setTabEnabled(idx, False)
                elif lv == LOCK_ALL:
                    for idx in range(tabs.count()):
                        tabs.setTabEnabled(idx, False)
                elif lv in (LOCK_WARNING_BANNER, LOCK_GUEST_BOT):
                    pass
            if hasattr(self, "body_container"):
                self.body_container.setEnabled(lv != LOCK_ALL)
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

    def _on_vendor_toast(self, data: dict):
        title = (data.get("title") or "厂家消息").strip()
        body = (data.get("body") or "").strip()
        notify_id = (data.get("notify_id") or "").strip()
        from ui_helpers import show_info
        show_info(self, title, body or title)
        if notify_id:
            try:
                from cloud_security import signed_post_json
                from local_adapter import CLOUD_WORKER_URL
                if CLOUD_WORKER_URL:
                    signed_post_json(
                        f"{CLOUD_WORKER_URL.rstrip('/')}/api/notify-read",
                        {"notify_id": notify_id},
                        timeout=5,
                    )
            except Exception:
                pass

    def _on_offline_lock(self):
        from overlay_widgets import OfflineLockOverlay
        overlay = OfflineLockOverlay(self)
        overlay.exec()

    # ══════════════════════════════════════════════════════════════════════════
    #  班次检查
    # ══════════════════════════════════════════════════════════════════════════
    def _check_shift(self):
        try:
            row = db.execute("SELECT value FROM system_config WHERE key='last_shift_close'").fetchone()
            if not row:
                return
            from datetime import datetime
            last = datetime.fromisoformat(row[0])
            now = datetime.now()
            hours = (now - last).total_seconds() / 3600
            if hours >= 16:
                from overlay_widgets import ShiftOverdueOverlay
                ShiftOverdueOverlay(self).show()
            elif hours >= 8:
                from overlay_widgets import ShiftWarningOverlay
                ShiftWarningOverlay(self).show()
        except Exception:
            logger.debug("ui_probe 调度跳过", exc_info=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  功能入口
    # ══════════════════════════════════════════════════════════════════════════
    def _settings(self):
        """设置已整合为工作区标签页。"""
        if not PermissionManager.has_permission("settings_view"):
            show_warning(self, i18n.t("nav_settings"), i18n.t("perm_denied_settings"))
            return
        self.workspace.tabs.setCurrentIndex(self.workspace.tab_index("settings"))

    def _open_takeover_hub(self):
        try:
            from legacy_takeover_hub import open_legacy_takeover_hub

            open_legacy_takeover_hub(self)
        except Exception as e:
            show_warning(self, i18n.t("takeover_hub_err_title"), str(e))

    def _open_lock_migrate(self):
        """锁管理 → 改为导航到房间管理。"""
        self._navigate_to("room_unified")

    def _open_card_sniffer(self):
        """厂家工具：发卡信号嗅探。"""
        try:
            from card_sniffer import open_card_sniffer
            open_card_sniffer(self)
        except ImportError as e:
            show_warning(self, i18n.t("settings_module_missing"), i18n.t("settings_sniffer_import_err").format(e=e))
        except Exception as e:
            show_warning(self, i18n.t("settings_start_fail"), i18n.t("settings_sniffer_start_err").format(e=e))

    def _vendor_cloud(self):
        """厂家工具：云端工作器对接配置。"""
        try:
            from tabs.cloud_config_dialog import CloudConfigDialog
            CloudConfigDialog(self).exec()
        except Exception as e:
            show_warning(self, i18n.t("settings_start_fail"), str(e))

    def _debug(self):
        if not PermissionManager.has_permission("debug_panel"):
            show_warning(self, i18n.t("nav_vendor_panel"), i18n.t("perm_denied_vendor"))
            return
        pwd, ok = QInputDialog.getText(self, i18n.t("nav_vendor_panel"), i18n.t("mw_debug_pwd"), QLineEdit.Password)
        if ok:
            import hashlib
            stored = db.get_config("debug_password") or ""
            if stored and hashlib.sha256(pwd.encode()).hexdigest() == stored:
                dlg = ManufacturerDebugPanel(self)
                dlg.exec()

    def _open_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_batch_mode(self):
        checked = self.btn_batch.isChecked()
        self.room_matrix.setProperty("batchMode", checked)
        self.room_matrix.style().unpolish(self.room_matrix)
        self.room_matrix.style().polish(self.room_matrix)
        if checked:
            self.room_matrix.enter_batch_mode()
        else:
            self.room_matrix.exit_batch_mode()

    # ══════════════════════════════════════════════════════════════════════════
    #  窗口事件
    # ══════════════════════════════════════════════════════════════════════════
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_layout_mode", None) == self.LAYOUT_SHELL:
            self._sync_command_splitter_sizes()
        # 勿把通知拉到全窗口尺寸，否则会挡住所有点击
        for w in [self.success_overlay, self.celeb_overlay]:
            if hasattr(w, "resize"):
                w.resize(self.size())

    def keyPressEvent(self, event):
        if event.modifiers() == Qt.AltModifier and event.key() == Qt.Key_D:
            self._debug()
        elif event.modifiers() == (Qt.AltModifier | Qt.ShiftModifier) and event.key() == Qt.Key_M:
            if PermissionManager.has_permission("import_data"):
                from data_import_service import DataImportDialog
                DataImportDialog(self).exec()
        elif event.modifiers() == (Qt.AltModifier | Qt.ShiftModifier) and event.key() == Qt.Key_H:
            if PermissionManager.has_permission("migration"):
                self._open_takeover_hub()
        super().keyPressEvent(event)

    def closeEvent(self, event):
        import logging
        logging.info(f"{APP_NAME_FULL} shutdown")
        bus.room_status_changed.disconnect()
        event.accept()
