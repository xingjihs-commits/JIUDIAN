"""前台工作台枢纽"""
import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QLineEdit, QFormLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame, QPlainTextEdit,
    QScrollArea, QAbstractItemView, QStackedWidget, QSplitter,
    QDialog, QInputDialog, QSizePolicy, QDoubleSpinBox, QRadioButton, QGroupBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from database import db, LEDGER_REVENUE_TX_TYPES, LEDGER_DEPOSIT_TX_TYPES
from i18n import i18n
from frontdesk_layers import HUB_ORDER, ROOM_CHIP_KEYS, OPS_CHIP_KEYS, get_frontdesk_layers, layer_is_on
from ledger_format import ledger_tx_type_display
from permission_system import PermissionManager
from event_bus import bus
from i18n import i18n
from ui_helpers import ask_confirm, select_from_list, show_error, show_info, show_warning, style_dialog, build_dialog_header
from sound_helper import play_success, play_fail, play_warn, play_notify
from frontdesk_ledger_strip import FrontdeskLedgerStrip
from design_tokens import _p
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
from tabs._shared import current_operator_id, _wrap_scroll
import datetime
import time
from collections import defaultdict

logger = logging.getLogger(__name__)


class FrontdeskHubWidget(QWidget):
    """前台工作台：入住·收银、在住客单、云端超市、客服呼叫、交班账单同一页切换。"""

    def __init__(
        self,
        checkin_tab,
        roster_tab,
        shop_tab,
        service_tab,
        shift_tab,
        *,
        on_quick_checkin=None,
        on_quick_checkout=None,
    ):
        super().__init__()
        self.setObjectName("FrontdeskHubWidget")
        self._on_quick_checkin = on_quick_checkin
        self._on_quick_checkout = on_quick_checkout
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        hub_scroll = QScrollArea()
        hub_scroll.setObjectName("FdHubToolbar")
        hub_scroll.setFrameShape(QFrame.Shape.NoFrame)
        hub_scroll.setWidgetResizable(True)
        hub_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        hub_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hub_scroll.setMinimumHeight(FD_TOOLBAR_H)
        hub_scroll.setMaximumHeight(FD_TOOLBAR_H + 8)
        inner = QWidget()
        inner.setObjectName("FdHubToolbarInner")
        row = QHBoxLayout(inner)
        row.setContentsMargins(FD_MARGIN, 6, FD_MARGIN, 6)
        row.setSpacing(8)
        spec = [
            ("checkin", i18n.t("fd_hub_checkin")),
            ("shop", i18n.t("tab_shop")),
            ("roster", i18n.t("tab_roster")),
            ("service", i18n.t("tab_service_short")),
            ("shift", i18n.t("tab_shift")),
        ]
        self._btn = {}
        for key, text in spec:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setObjectName("FrontdeskHubBtn")
            b.setToolTip(text)
            b.setMinimumHeight(36)
            b.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, k=key: self.navigate(k))
            row.addWidget(b)
            self._btn[key] = b
        row.addStretch()
        hub_scroll.setWidget(inner)
        lay.addWidget(hub_scroll)

        from ui_surface import fd_apply_scroll_area, fd_refresh_surfaces
        fd_apply_scroll_area(hub_scroll)

        self.ledger_strip = None

        # ── 前台实时看板条 ───────────────────────────────────────
        self._dash_strip = self._build_frontdesk_dashboard()
        lay.addWidget(self._dash_strip)

        self.stack = QStackedWidget()
        self.stack.addWidget(checkin_tab)
        self.stack.addWidget(shop_tab)
        self.stack.addWidget(roster_tab)
        self.stack.addWidget(service_tab)
        self.stack.addWidget(shift_tab)
        lay.addWidget(self.stack, stretch=1)

        self.navigate("checkin")
        fd_refresh_surfaces(self)
        bus.theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _theme: str = "") -> None:
        """换主题后重刷前台枢纽看板 + 子页。"""
        from ui_surface import fd_refresh_surfaces

        fd_refresh_surfaces(self)
        self._refresh_dashboard()
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            if page is None:
                continue
            for name in ("_on_theme_changed", "_refresh_theme_styles"):
                fn = getattr(page, name, None)
                if callable(fn):
                    try:
                        fn(_theme)
                    except TypeError:
                        fn()
                    break

    def apply_frontdesk_layers(self, ly):
        """按层级显示/隐藏枢纽按钮；当前页若被关则跳到第一个可用页。"""
        for key in HUB_ORDER:
            b = self._btn.get(key)
            if b:
                b.setVisible(layer_is_on(ly, "frontdesk_hub", key))
        order = HUB_ORDER
        idx = self.stack.currentIndex()
        cur = order[idx] if 0 <= idx < len(order) else "checkin"
        if not layer_is_on(ly, "frontdesk_hub", cur):
            for k in order:
                if layer_is_on(ly, "frontdesk_hub", k):
                    self.navigate(k, _ly=ly)
                    return
        self._refresh_visible()
        self._sync_dash_strip_visibility(cur)

    def _build_frontdesk_dashboard(self):
        """前台实时看板条：在住/空净/脏房/超时计数 + 今日营收 + 班次倒计时 + 最近日志。"""
        strip = QFrame()
        strip.setObjectName("FrontdeskDashStrip")
        # 修复 min>max 冲突：统一 40px，避免 Qt 行为未定义导致 chip 文字被裁切
        strip.setFixedHeight(40)
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(FD_MARGIN, 6, FD_MARGIN, 6)
        sl.setSpacing(12)

        self._dash_inhouse = QLabel(i18n.t("dashbrd_inhouse", default="在住: —"))
        self._dash_inhouse.setObjectName("DashStatChip")
        sl.addWidget(self._dash_inhouse)

        self._dash_ready = QLabel(i18n.t("dashbrd_ready", default="空净: —"))
        self._dash_ready.setObjectName("DashStatChip")
        sl.addWidget(self._dash_ready)

        self._dash_dirty = QLabel(i18n.t("dashbrd_dirty", default="脏房: —"))
        self._dash_dirty.setObjectName("DashStatChip")
        sl.addWidget(self._dash_dirty)

        self._dash_otime = QLabel(i18n.t("dashbrd_overtime", default="超时: —"))
        self._dash_otime.setObjectName("DashStatChipWarn")
        sl.addWidget(self._dash_otime)

        sep1 = QLabel("|")
        sep1.setObjectName("DashSep")
        sl.addWidget(sep1)

        self._dash_rev = QLabel(i18n.t("dashbrd_rev", default="今日收: —"))
        self._dash_rev.setObjectName("DashStatChip")
        sl.addWidget(self._dash_rev)

        self._dash_pend = QLabel(i18n.t("dashbrd_pending", default="待收: —"))
        self._dash_pend.setObjectName("DashStatChip")
        sl.addWidget(self._dash_pend)

        sep2 = QLabel("|")
        sep2.setObjectName("DashSep")
        sl.addWidget(sep2)

        self._dash_shift_left = QLabel(i18n.t("dashbrd_shift", default="交班: —"))
        self._dash_shift_left.setObjectName("DashStatChipWarn")
        sl.addWidget(self._dash_shift_left)

        sl.addStretch()

        self._dash_log = QLabel("")
        self._dash_log.setObjectName("DashLogLabel")
        self._dash_log.setMaximumWidth(400)
        sl.addWidget(self._dash_log)

        # 定时刷新
        self._dash_timer = QTimer(self)
        self._dash_timer.timeout.connect(self._refresh_dashboard)
        self._dash_timer.start(10000)

        # 监听 event_bus
        try:
            from event_bus import bus
            bus.room_status_changed.connect(lambda *a: self._refresh_dashboard())
            bus.ledger_updated.connect(lambda *a: self._refresh_dashboard())
        except Exception:
            pass

        return strip

    def _refresh_dashboard(self):
        """刷新前台看板数据。"""
        try:
            row = db.execute("""
                SELECT
                    (SELECT COUNT(*) FROM rooms WHERE status='INHOUSE'),
                    (SELECT COUNT(*) FROM rooms WHERE status='READY'),
                    (SELECT COUNT(*) FROM rooms WHERE status='DIRTY'),
                    (SELECT COUNT(*) FROM rooms WHERE status='OVERTIME')
            """).fetchone()
            if row:
                self._dash_inhouse.setText(i18n.t("dashbrd_inhouse_fmt", "在住: {}").format(row[0]))
                self._dash_ready.setText(i18n.t("dashbrd_ready_fmt", "空净: {}").format(row[1]))
                self._dash_dirty.setText(i18n.t("dashbrd_dirty_fmt", "脏房: {}").format(row[2]))
                self._dash_otime.setText(i18n.t("dashbrd_overtime_fmt", "超时: {}").format(row[3]))

            # 今日营收
            from datetime import date
            today = date.today().isoformat()
            rev = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE date(created_at)=? AND tx_type IN ('ROOM_IN','SHOP')",
                (today,),
            ).fetchone()
            if rev:
                self._dash_rev.setText(i18n.t("dashbrd_rev_fmt", "今日收: {}").format(f"{i18n.t('currency_symbol')}{rev[0]:.0f}"))

            # 交班倒计时
            try:
                shift_start = db.get_shift_start_time()
                from datetime import datetime, timedelta
                st = datetime.fromisoformat(shift_start) if shift_start else datetime.now()
                elapsed = (datetime.now() - st).total_seconds() / 3600
                left = max(0, 12.0 - elapsed)
                self._dash_shift_left.setText(i18n.t("dashbrd_shift_fmt", "交班: {:.1f}h").format(left))
            except Exception:
                self._dash_shift_left.setText(i18n.t("dashbrd_shift", "交班: —"))

            # 最近日志
            try:
                log_row = db.execute(
                    "SELECT note FROM ledger WHERE note IS NOT NULL AND note != '' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if log_row and log_row[0]:
                    self._dash_log.setText(log_row[0][:60])
            except Exception:
                pass
        except Exception:
            pass

    def navigate(self, key: str, _ly=None):
        order = HUB_ORDER
        if key not in order:
            key = "checkin"
        ly = _ly if _ly is not None else get_frontdesk_layers(db)
        if not layer_is_on(ly, "frontdesk_hub", key):
            for k in order:
                if layer_is_on(ly, "frontdesk_hub", k):
                    key = k
                    break
        idx = order.index(key)
        self.stack.setCurrentIndex(idx)
        for k, b in self._btn.items():
            b.blockSignals(True)
            b.setChecked(k == key)
            b.blockSignals(False)
        self._refresh_visible()
        self._sync_ledger_visibility(key)
        self._sync_dash_strip_visibility(key)

    def _sync_ledger_visibility(self, key: str) -> None:
        pass

    def _sync_dash_strip_visibility(self, key: str) -> None:
        if hasattr(self, "_dash_strip"):
            self._dash_strip.setVisible(key != "checkin")

    @staticmethod
    def _unwrap(w):
        if hasattr(w, "widget") and callable(w.widget):
            inner = w.widget()
            if inner is not None:
                return inner
        return w

    def _refresh_visible(self):
        w = self._unwrap(self.stack.currentWidget())
        if hasattr(w, "refresh"):
            w.refresh()
        elif hasattr(w, "_rf"):
            w._rf()
        elif hasattr(w, "_refresh_expected"):
            w._refresh_expected()

    def refresh_all_pages(self):
        for i in range(self.stack.count()):
            w = self._unwrap(self.stack.widget(i))
            if hasattr(w, "refresh"):
                w.refresh()
            elif hasattr(w, "_rf"):
                w._rf()
