import logging
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QPushButton, QLabel, QComboBox, QLineEdit, QFormLayout, QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QProgressBar, QGridLayout, QFrame, QPlainTextEdit, QInputDialog, QScrollArea, QDoubleSpinBox, QAbstractItemView, QStackedWidget, QSplitter, QDialog, QSizePolicy, QApplication)
from PySide6.QtCore import Qt, QTimer
from database import db, LEDGER_REVENUE_TX_TYPES, LEDGER_DEPOSIT_TX_TYPES
logger = logging.getLogger(__name__)
from frontdesk_layers import HUB_ORDER, ROOM_CHIP_KEYS, OPS_CHIP_KEYS, get_frontdesk_layers, layer_is_on
from ledger_format import ledger_tx_type_display
from permission_system import PermissionManager
from event_bus import bus
from housekeeping_panel import HousekeepingPanel
from tabs.energy_audit_group import EnergyMonitorPanel
from i18n import i18n
from ui_helpers import ask_confirm, select_from_list, show_error, show_info, show_warning, style_dialog, build_dialog_header, safe_status_icon
from sound_helper import play_success, play_fail, play_warn, play_notify
from frontdesk_flow_strip import FrontdeskFlowStrip
from frontdesk_ledger_strip import FrontdeskLedgerStrip
from audit_tab_widget import AuditTab
from design_tokens import _p
from shop_frontdesk import ShopTab
from energy_entry_page import EnergyEntryPage
from unified_room_page import UnifiedRoomPage
from item_dictionary_page import ItemDictionaryPage
from frontdesk_ui import (
    FD_MARGIN,
    FD_SPACE,
    FD_SPACE_SM,
    FD_TOOLBAR_H,
    fd_apply_action_btn,
    fd_apply_compact_input,
    fd_apply_toolbar_btn,
    fd_card,
    fd_card_layout,
    fd_section_title,
)
from lock_legacy_bridge import (
    CARD_STATUS_ACTIVE,
    CARD_STATUS_ERASED,
    CARD_STATUS_EXPIRED,
    CARD_STATUS_LOST,
    CARD_STATUS_LOST_PENDING,
    LEGACY_ACTIVE_CARD_STATUSES,
)
import datetime
import time
from collections import defaultdict

# Tab imports from tabs/
from tabs.frontdesk_tab import ShiftTab, CheckinTab, PaymentMethodTiles, FrontdeskHubWidget
from tabs.finance_tab import FinanceTab
from tabs.guest_list_tab import GuestListTab
from tabs.inventory_tab import InventoryTab
from tabs.staff_tab import StaffTab
from tabs.member_tab import MemberTab
from tabs.pricing_tab import PricingTab
from tabs.night_audit_tab import NightAuditTab
from tabs.energy_audit_group import EnergyAuditGroup
from tabs.system_console_tab import SystemConsoleTab
from tabs.vendor_console_tab import VendorConsoleTab


def _status_placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


def _legacy_card_status_display(status: str) -> str:
    key = f"card_status_{str(status or '')}"
    t = i18n.t(key)
    if t != key:
        return t
    mapping = {
        CARD_STATUS_ACTIVE: safe_status_icon("ACTIVE") + " " + i18n.t("card_status_ACTIVE"),
        CARD_STATUS_ERASED: safe_status_icon("ERASED") + " " + i18n.t("card_status_ERASED"),
        CARD_STATUS_EXPIRED: safe_status_icon("EXPIRED") + " " + i18n.t("card_status_EXPIRED"),
        CARD_STATUS_LOST_PENDING: safe_status_icon("LOST_PENDING") + " " + i18n.t("card_status_LOST_PENDING"),
        CARD_STATUS_LOST: safe_status_icon("LOST") + " " + i18n.t("card_status_LOST"),
        "ACTIVE": safe_status_icon("ACTIVE") + " " + i18n.t("card_status_ACTIVE"),
        "PENDING": i18n.t("card_status_PENDING"),
        "CANCELLED": safe_status_icon("CANCELLED") + " " + i18n.t("card_status_CANCELLED"),
        "EXPIRED": safe_status_icon("EXPIRED") + " " + i18n.t("card_status_EXPIRED"),
        "LOST_PENDING_PHYSICAL": safe_status_icon("LOST_PENDING_PHYSICAL") + " " + i18n.t("card_status_LOST_PENDING_PHYSICAL"),
        "LOST": safe_status_icon("LOST") + " " + i18n.t("card_status_LOST"),
        "BLACKLISTED": i18n.t("card_status_BLACKLISTED"),
    }
    return mapping.get(str(status or ""), str(status or i18n.t("unknown")))


from tabs._shared import current_operator_id, _wrap_scroll


def _boot_yield() -> None:
    """启动阶段让出事件循环，避免 Windows 误判 Python 未响应。"""
    QApplication.processEvents()


class WorkspaceDock(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("WorkspaceDock")
        self.setMinimumWidth(300)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideNone)
        self.tabs.tabBar().hide()
        # 防启动闪烁：QTabWidget 面板在 QSS 生效前用调色板兜底
        from PySide6.QtGui import QColor, QPalette
        self.tabs.setAutoFillBackground(True)
        tp = self.tabs.palette()
        tp.setColor(QPalette.ColorRole.Window, QColor(_p("bg_root")))
        self.tabs.setPalette(tp)

        self.checkin_tab = CheckinTab()
        _boot_yield()
        self.shift_tab = ShiftTab()
        self.finance_tab = FinanceTab()
        _boot_yield()
        from report_engine import ReportTab
        self.report_tab = ReportTab()
        self.roster_tab = GuestListTab()
        self.shop_tab = ShopTab()
        self.hk_tab = HousekeepingPanel()
        self.energy_tab = EnergyAuditGroup()
        self.audit_tab = AuditTab()
        self.inventory_tab = InventoryTab()
        _boot_yield()
        self.room_unified_tab = UnifiedRoomPage()
        self.item_dict_tab = ItemDictionaryPage()
        self.staff_tab = StaffTab()
        self.member_tab = MemberTab()
        self.pricing_tab = PricingTab()
        from card_system import CardSystemTab
        self.card_tab = CardSystemTab()
        from tabs.hotel_overview_tab_v4 import HotelOverviewTab
        self.overview_tab = HotelOverviewTab()
        from ota_connector import OTATab
        self.ota_tab = OTATab()
        from qr_code_service import ServiceRequestPanel
        self.service_tab = ServiceRequestPanel()
        self.night_audit_tab = NightAuditTab()
        _boot_yield()

        self.frontdesk_hub = FrontdeskHubWidget(
            self.checkin_tab,
            self.roster_tab,
            self.shop_tab,
            self.service_tab,
            self.shift_tab,
        )
        _boot_yield()
        self._toolbar_callbacks_attached = False
        self.tabs.addTab(self.frontdesk_hub, i18n.t("tab_frontdesk_hub"))

        self.tabs.addTab(_wrap_scroll(self.overview_tab), i18n.t("nav_overview"))
        self.INDEX_OVERVIEW = 1

        self.tabs.addTab(_wrap_scroll(self.finance_tab), i18n.t("tab_finance_short"))
        from tabs.frontdesk.refunds_tab import RefundsTab
        self.refunds_tab = RefundsTab()
        self.tabs.addTab(_wrap_scroll(self.refunds_tab), i18n.t("refund_pending_title", default="待审退款"))
        self.tabs.addTab(_wrap_scroll(self.report_tab), i18n.t("tab_report_pl"))
        self.tabs.addTab(self.hk_tab, i18n.t("tab_housekeeping"))
        self.tabs.addTab(self.energy_tab, i18n.t("tab_energy"))
        self.tabs.addTab(_wrap_scroll(self.audit_tab), i18n.t("tab_audit_report"))
        self.tabs.addTab(_wrap_scroll(self.inventory_tab), i18n.t("tab_inventory_short"))
        self.tabs.addTab(_wrap_scroll(self.room_unified_tab), i18n.t("nav_room_unified"))
        self.tabs.addTab(_wrap_scroll(self.item_dict_tab), i18n.t("nav_item_dict"))
        self.tabs.addTab(_wrap_scroll(self.staff_tab), i18n.t("tab_staff_short"))
        self.tabs.addTab(_wrap_scroll(self.member_tab), i18n.t("tab_member_short"))
        self.tabs.addTab(_wrap_scroll(self.pricing_tab), i18n.t("tab_pricing_short"))
        self.tabs.addTab(_wrap_scroll(self.card_tab), i18n.t("tab_card_short"))
        self.tabs.addTab(_wrap_scroll(self.ota_tab), i18n.t("tab_booking_short"))
        self.tabs.addTab(_wrap_scroll(self.night_audit_tab), i18n.t("tab_night_audit_short"))

        # 系统控制台（替代废弃的 SettingsTab，侧栏"⚙ 设置"→index 17）
        self.settings_tab = SystemConsoleTab()
        _boot_yield()
        self.tabs.addTab(_wrap_scroll(self.settings_tab), i18n.t("tab_settings_short"))

        self.vendor_console_tab = VendorConsoleTab()
        _boot_yield()
        self.tabs.addTab(self.vendor_console_tab, "" + i18n.t("nav_vendor_console"))

        # G02: 导航标签 Tooltip 分组说明（鼠标悬停即可知道每个标签作用）
        _tab_tooltips = [
            "前台中心 — 入住 · 续住 · 退房 · 团队 · 服务",        # 0 frontdesk
            "经营总览 — 房态地图快照与实时概览",                     # 1 overview
            "财务账目 — 今日/本月营收、流水明细、对账",              # 2 finance
            "待审退款 — 需要主管审批的退款申请",                     # 3 refunds
            "经营报表 — 损益 P&L / 月度收支分析",                   # 4 report
            "客房保洁 — 保洁任务派单与完成状态",                    # 5 hk
            "能耗管理 — 水电用量监控与异常预警",                    # 6 energy
            "审计日志 — 操作记录查询与合规审查",                    # 7 audit
            "库存管理 — 商品/耗材进出库与预警",                     # 8 inventory
            "房型配置 — 房型与房间统一管理",                        # 9 room_unified
            "物品字典 — 商品/服务分类词表维护",                     # 10 item_dict
            "员工管理 — 账号、角色、权限配置",                      # 11 staff
            "会员管理 — 积分、等级、会员档案",                      # 12 member
            "价格策略 — 季节定价与特殊规则设置",                    # 13 pricing
            "门卡管理 — 卡片档案与状态追踪",                        # 14 card
            "OTA预订 — 第三方平台订单汇聚",                         # 15 ota
            "夜间审计 — 日结与夜审报告生成",                        # 16 night_audit
            "系统设置 — 参数配置与系统控制台",                      # 17 settings
            "厂商控制台 — 调试专用（仅内部使用）",                  # 18 vendor_console
        ]
        for _idx, _tip in enumerate(_tab_tooltips):
            if _idx < self.tabs.count():
                self.tabs.setTabToolTip(_idx, _tip)

        self.tabs.currentChanged.connect(self._on_tab_change)
        bus.theme_changed.connect(self._on_workspace_theme_changed)
        l.addWidget(self.tabs)

        # 动态标签页映射 — 用 indexOf 替代硬编码常量
        self._tab_refs: dict[str, QWidget] = {
            "frontdesk": self.frontdesk_hub,
            "overview": self.overview_tab,
            "finance": self.finance_tab,
            "refunds": self.refunds_tab,
            "report": self.report_tab,
            "hk": self.hk_tab,
            "energy": self.energy_tab,
            "audit": self.audit_tab,
            "inventory": self.inventory_tab,
            "room_unified": self.room_unified_tab,
            "item_dict": self.item_dict_tab,
            "staff": self.staff_tab,
            "member": self.member_tab,
            "pricing": self.pricing_tab,
            "card": self.card_tab,
            "ota": self.ota_tab,
            "night_audit": self.night_audit_tab,
            "settings": self.settings_tab,
            "vendor_console": self.vendor_console_tab,
        }

        self.INDEX_FRONTS = 0
        self.INDEX_OVERVIEW = 1
        self.INDEX_FINANCE = 2
        self.INDEX_REPORT = 3
        self.INDEX_HK = 4
        self.INDEX_ENERGY = 5
        self.INDEX_AUDIT = 6
        self.INDEX_INVENTORY = 7
        self.INDEX_ROOM_UNIFIED = 8
        self.INDEX_ITEM_DICT = 9
        self.INDEX_STAFF = 10
        self.INDEX_MEMBER = 11
        self.INDEX_PRICING = 12
        self.INDEX_CARD = 13
        self.INDEX_OTA = 14
        self.INDEX_NIGHT_AUDIT = 15
        self.INDEX_SETTINGS = 16
        self.INDEX_VENDOR_CONSOLE = 17

        self.INDEX_CHECKIN = 0
        self.INDEX_SHIFT = 0

        # 挂载前台按钮呼吸辉光动画（通过 motion_gate 控制）
        from motion_gate import install_workspace_dock_motion as _install_motion
        _install_motion(self)

    def _on_workspace_theme_changed(self, _theme: str = "") -> None:
        """换主题后重刷工作区各 Tab 的 palette / inline 色。"""
        from PySide6.QtWidgets import QScrollArea
        from ui_surface import fd_apply_scroll_area, fd_refresh_surfaces

        for scroll in self.tabs.findChildren(QScrollArea, "PageScrollWrap"):
            fd_apply_scroll_area(scroll)

        widgets = [
            self.checkin_tab, self.frontdesk_hub, self.overview_tab, self.finance_tab, self.refunds_tab, self.report_tab,
            self.roster_tab, self.shop_tab, self.hk_tab, self.energy_tab,
            self.audit_tab, self.inventory_tab, self.room_unified_tab,
            self.item_dict_tab, self.staff_tab, self.member_tab, self.pricing_tab,
            self.card_tab, self.ota_tab, self.service_tab, self.night_audit_tab,
            self.settings_tab, self.vendor_console_tab,
        ]
        for w in widgets:
            if w is None:
                continue
            fd_refresh_surfaces(w)
            for name in (
                "_on_theme_changed", "_refresh_theme_styles",
                "_refresh_inline_colors", "_refresh_dash_colors",
            ):
                fn = getattr(w, name, None)
                if not callable(fn):
                    continue
                try:
                    fn(_theme)
                except TypeError:
                    try:
                        fn()
                    except Exception:
                        pass
                except Exception:
                    pass
            w.update()

        if self.energy_tab is not None:
            for page in self.energy_tab.findChildren(QWidget):
                if page.objectName():
                    fd_refresh_surfaces(page)
                    refresh = getattr(page, "refresh", None)
                    if callable(refresh):
                        try:
                            refresh()
                        except Exception:
                            pass

    def attach_frontdesk_toolbar(self, on_checkin, on_checkout) -> None:
        """Lovable 壳层：快捷入住/退房不再在 Hub 工具栏重复。"""
        self._toolbar_callbacks_attached = True

    def apply_frontdesk_layers(self, ly=None):
        if ly is None:
            ly = get_frontdesk_layers(db)
        if hasattr(self, "frontdesk_hub"):
            self.frontdesk_hub.apply_frontdesk_layers(ly)

    def navigate_frontdesk(self, action: str):
        self.tabs.setCurrentIndex(self.tab_index("frontdesk"))
        self.frontdesk_hub.navigate(action)

    def focus_checkin_tab(self):
        self.navigate_frontdesk("checkin")

    def focus_shift_tab(self):
        self.navigate_frontdesk("shift")

    def tab_index(self, key: str) -> int:
        """通过动态 indexOf 查询标签页位置，避免硬编码常量漂移。
        若 widget 被 QScrollArea 包裹则自动解一层。"""
        w = self._tab_refs.get(key)
        if w is None:
            return -1
        idx = self.tabs.indexOf(w)
        if idx >= 0:
            return idx
        # 兜底：w 可能被 QScrollArea 包裹
        from PySide6.QtWidgets import QScrollArea
        for i in range(self.tabs.count()):
            tw = self.tabs.widget(i)
            if isinstance(tw, QScrollArea) and tw.widget() is w:
                return i
        return -1

    def focus_finance_tab(self):
        self.tabs.setCurrentIndex(self.tab_index("finance"))

    def focus_report_tab(self):
        self.tabs.setCurrentIndex(self.tab_index("report"))

    def _on_tab_change(self, idx):
        db.log_action(current_operator_id(), "TAB_SWITCH", self.tabs.tabText(idx))
        widget = self.tabs.widget(idx)
        if isinstance(widget, FrontdeskHubWidget):
            widget._refresh_visible()
            return
        if hasattr(widget, "widget") and callable(widget.widget):
            inner = widget.widget()
            if inner is not None:
                widget = inner
        if hasattr(widget, "refresh"):
            widget.refresh()
        elif hasattr(widget, "_rf"):
            widget._rf()
        try:
            from ui_probe import schedule_ui_probe
            root = self.window()
            schedule_ui_probe(root, context=f"tab:{self.tabs.tabText(idx)}")
        except Exception:
            pass

    def refresh_all(self):
        if hasattr(self, "frontdesk_hub"):
            self.frontdesk_hub.refresh_all_pages()
        self.roster_tab._rf()
        self.audit_tab._rf()
        if hasattr(self, "inventory_tab"):
            self.inventory_tab.refresh()
        if hasattr(self, "finance_tab"):
            self.finance_tab.refresh()
        if hasattr(self, "staff_tab"):
            self.staff_tab.refresh()
        if hasattr(self, "ota_tab"):
            self.ota_tab.refresh()
        if hasattr(self, "service_tab"):
            self.service_tab.refresh()
        if hasattr(self, "night_audit_tab"):
            self.night_audit_tab.refresh()
