"""hotel_overview_tab_v4 — v8 总览仪表盘（一屏看全店）。

[v8] 改造为信息密集型仪表盘：
  ① 顶部欢迎条 — 日期 + 班次 + 操作员 + 自动刷新指示
  ② 6 张 KPI 卡片网格 — 今日营收 / 占用率 / ADR / RevPAR / 今日入住 / 今日退房
     - 大号数字 + 趋势箭头（vs 昨日，正绿↑ 负红↓ 中性灰—）
     - 桌面 3 列 × 2 行 / 平板 2 列 / 移动 1 列（响应式 QGridLayout）
  ③ 房态分布 + 财务明细 — 左右分栏
  ④ 在住客人 Top + 待办事项 — 左右分栏
  ⑤ 风险告警 — 满宽（若有）
- 数据每 30 秒自动刷新（QTimer），切换 tab 时立即刷新
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette, QResizeEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QProgressBar,
    QGridLayout, QScrollArea, QSizePolicy, QPushButton,
)

from design_tokens import _p
from ui.tokens.colors import ColorRoomStatus
from ui.layout.frontdesk import FD_SPACE_LG, FD_SPACE_MD
from i18n import i18n
from event_bus import bus
from ui_surface import fd_apply_scroll_area

try:
    from reconciliation_checks import ALL_CHECKS
except Exception:
    ALL_CHECKS = []

try:
    import overview_data
except Exception:
    overview_data = None


# 自动刷新间隔（ms）—— 30 秒
_OVERVIEW_REFRESH_INTERVAL_MS = 30_000


def _overview_full() -> dict:
    """取 build_full_overview() 全量数据，异常时返回空 dict。"""
    try:
        if overview_data and hasattr(overview_data, "build_full_overview"):
            return overview_data.build_full_overview()
        if overview_data:
            return overview_data.assemble("today").get("full", {})
    except Exception:
        pass
    return {}


def _trend_arrow(pct) -> str:
    """趋势箭头：正绿↑ 负红↓ None 灰—"""
    if pct is None:
        return "<span style='color:{0}'>—</span>".format(_p("text_muted"))
    if pct > 0:
        return "<span style='color:{0}'>▲ +{1}%</span>".format(_p("amount_positive"), pct)
    if pct < 0:
        return "<span style='color:{0}'>▼ {1}%</span>".format(_p("danger"), pct)
    return "<span style='color:{0}'>— 0%</span>".format(_p("text_muted"))


def _kpi_card(label, value, *, tone="", sub="", trend_html=""):
    """KPI 卡片 — QSS 选择器驱动，换主题自动变色。"""
    card = QFrame()
    card.setObjectName("KpiCard")
    # 用 property 驱动 QSS 中的边框色（避免内联 setStyleSheet 锁死颜色）
    tone_prop = tone if tone else "primary"
    card.setProperty("kpiTone", tone_prop)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(4)
    lbl = QLabel(label)
    lbl.setObjectName("KpiLabel")
    lay.addWidget(lbl)
    val = QLabel(value)
    val.setObjectName("KpiValue")
    if tone:
        val.setProperty("tone", tone)
    lay.addWidget(val)
    if sub or trend_html:
        s = QLabel((sub + "  " if sub else "") + trend_html)
        s.setObjectName("KpiSub")
        s.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(s)
    return card


def _status_bar_row(label, count, total, color):
    """房态快照单行 — 标签 + 进度条 + 计数。"""
    row = QFrame()
    row.setObjectName("RoomSnapRow")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 4, 0, 4)
    lay.setSpacing(FD_SPACE_MD)
    lbl = QLabel(label)
    lbl.setMinimumWidth(64)
    lbl.setObjectName("RoomSnapLabel")
    lay.addWidget(lbl)
    bar = QProgressBar()
    bar.setObjectName("RoomSnapBar")
    bar.setRange(0, 100)
    pct = int(count * 100 / total) if total > 0 else 0
    bar.setValue(pct)
    bar.setTextVisible(False)
    bar.setFixedHeight(10)
    bar.setStyleSheet(f"""
        QProgressBar#RoomSnapBar {{ background: {_p('surface_alt')}; }}
        QProgressBar#RoomSnapBar::chunk {{ background: {color}; }}
    """)
    lay.addWidget(bar, 1)
    cnt = QLabel(f"{count}  ·  {pct}%")
    cnt.setMinimumWidth(72)
    cnt.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    cnt.setObjectName("RoomSnapCount")
    cnt.setStyleSheet(f"color: {color};")
    lay.addWidget(cnt)
    return row


def _alert_item(title, detail, level):
    """风险告警单项 — 左色条 + 标题 + 详情。"""
    item = QFrame()
    item.setObjectName("AlertItem")
    color_map = {
        "ERROR": _p("danger"), "CRITICAL": _p("danger"),
        "WARN": _p("accent"), "WARNING": _p("accent"),
        "INFO": _p("text_muted"),
    }
    color = color_map.get(level.upper(), _p("text_muted"))
    item.setStyleSheet(f"""
        QFrame#AlertItem {{
            background: {_p('surface_alt')};
            border: none;
            border-left: 3px solid {color};
            border-radius: 6px;
        }}
    """)
    lay = QVBoxLayout(item)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("AlertTitle")
    t.setStyleSheet(f"color: {color};")
    lay.addWidget(t)
    if detail:
        d = QLabel(detail)
        d.setWordWrap(True)
        d.setObjectName("AlertDetail")
        lay.addWidget(d)
    return item


def _todo_item(title, hint, action_text="", on_click=None):
    """待办事项单项 — 标题 + 提示 + 操作按钮。"""
    item = QFrame()
    item.setObjectName("TodoItem")
    item.setStyleSheet(
        f"QFrame#TodoItem {{"
        f" background: {_p('surface')};"
        f" border: 1px solid {_p('border')};"
        f" border-radius: 8px;"
        f"}}"
    )
    lay = QHBoxLayout(item)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(FD_SPACE_MD)
    text_lay = QVBoxLayout()
    text_lay.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("TodoTitle")
    text_lay.addWidget(t)
    if hint:
        h = QLabel(hint)
        h.setObjectName("TodoHint")
        text_lay.addWidget(h)
    lay.addLayout(text_lay, 1)
    if action_text:
        btn = QPushButton(action_text)
        btn.setObjectName("FdActSecondary")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if on_click:
            btn.clicked.connect(on_click)
        lay.addWidget(btn)
    return item


def _section_card(title, *, extra_widget=None):
    """大区块卡片 — 三面亮色条 + 标题。"""
    card = QFrame()
    card.setObjectName("OverviewSectionCard")
    card.setStyleSheet(
        f"QFrame#OverviewSectionCard {{"
        f" background: {_p('surface')};"
        f" border: 1px solid {_p('border')};"
        f" border-top: 2px solid {_p('accent')};"
        f" border-left: 1px solid {_p('accent')};"
        f" border-right: 1px solid {_p('accent')};"
        f" border-radius: 8px;"
        f"}}"
    )
    lay = QVBoxLayout(card)
    lay.setContentsMargins(FD_SPACE_LG, FD_SPACE_LG, FD_SPACE_LG, FD_SPACE_LG)
    lay.setSpacing(FD_SPACE_MD)
    header = QHBoxLayout()
    lbl = QLabel(title)
    lbl.setObjectName("SectionCardTitle")
    header.addWidget(lbl)
    header.addStretch()
    if extra_widget is not None:
        header.addWidget(extra_widget)
    lay.addLayout(header)
    return card, lay


def _guest_row(guest: dict) -> QFrame:
    """在住客人单行：房号 | 客人 | 退房日期。"""
    row = QFrame()
    row.setObjectName("GuestRow")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(8, 6, 8, 6)
    lay.setSpacing(FD_SPACE_MD)
    room_lbl = QLabel(str(guest.get("room_id", "—")))
    room_lbl.setMinimumWidth(56)
    room_lbl.setStyleSheet(f"color: {_p('primary')}; font-weight: 600;")
    lay.addWidget(room_lbl)
    name_lbl = QLabel(str(guest.get("guest_name", "—")))
    name_lbl.setMinimumWidth(80)
    lay.addWidget(name_lbl, 1)
    co = guest.get("checkout_time") or "—"
    if len(str(co)) > 10:
        co = str(co)[:10]
    co_lbl = QLabel(co)
    co_lbl.setStyleSheet(f"color: {_p('text_muted')};")
    lay.addWidget(co_lbl)
    return row


class HotelOverviewTab(QWidget):
    """酒店总览页 — v8 一屏看全店仪表盘。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HotelOverviewTabV4")
        self._full = _overview_full()

        # 引用各子区块，刷新时只更新内容而不重建外层
        self._kpi_grid: QGridLayout | None = None
        self._rooms_section_lay: QVBoxLayout | None = None
        self._fin_section_lay: QVBoxLayout | None = None
        self._guests_section_lay: QVBoxLayout | None = None
        self._todos_section_lay: QVBoxLayout | None = None
        self._alerts_section_lay: QVBoxLayout | None = None
        # 外层卡片 QFrame 引用（主题切换时重刷三面亮色条）
        self._kpi_card: QFrame | None = None
        self._rooms_card: QFrame | None = None
        self._fin_card: QFrame | None = None
        self._guests_card: QFrame | None = None
        self._todos_card: QFrame | None = None
        self._alerts_card: QFrame | None = None
        self._refresh_lbl: QLabel | None = None
        self._scroll: QScrollArea | None = None

        self._build_ui()

        # 30 秒自动刷新
        self._timer = QTimer(self)
        self._timer.setInterval(_OVERVIEW_REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        # 首次刷新（填充动态内容）
        QTimer.singleShot(0, self.refresh)

        # 主题切换时重刷所有内联颜色（防上一主题的 accent 残留）
        bus.theme_changed.connect(lambda _: self.refresh())

    # ── UI 构建 ────────────────────────────────────────────

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll = scroll

        body = QWidget()
        body.setObjectName("OverviewScrollBody")
        body.setAutoFillBackground(True)
        body_pal = body.palette()
        body_pal.setColor(QPalette.ColorRole.Window, QColor(_p("bg_root")))
        body.setPalette(body_pal)

        root = QVBoxLayout(body)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(FD_SPACE_LG)

        root.addWidget(self._build_welcome_strip())
        root.addWidget(self._build_kpi_section())

        mid_row1 = QHBoxLayout()
        mid_row1.setSpacing(FD_SPACE_LG)
        mid_row1.addWidget(self._build_rooms_section(), 1)
        mid_row1.addWidget(self._build_financials_section(), 1)
        root.addLayout(mid_row1)

        mid_row2 = QHBoxLayout()
        mid_row2.setSpacing(FD_SPACE_LG)
        mid_row2.addWidget(self._build_guests_section(), 1)
        mid_row2.addWidget(self._build_todos_section(), 1)
        root.addLayout(mid_row2)

        root.addWidget(self._build_alerts_section())
        root.addStretch(1)

        scroll.setWidget(body)
        # 必须在 setWidget 之后调用 — viewport 此时才存在，调色板兜底防闪烁
        scroll.viewport().setAutoFillBackground(True)
        vp_pal = scroll.viewport().palette()
        from PySide6.QtGui import QColor as _QColor
        vp_pal.setColor(QPalette.ColorRole.Window, _QColor(_p("bg_root")))
        scroll.viewport().setPalette(vp_pal)
        fd_apply_scroll_area(scroll)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _build_welcome_strip(self):
        strip = QFrame()
        strip.setObjectName("WelcomeStrip")
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(FD_SPACE_LG)
        now = datetime.now()
        welcome = QLabel("\U0001f4cb  " + i18n.t("overview_welcome", default="今日总览"))
        welcome.setObjectName("WelcomeTitle")
        lay.addWidget(welcome)
        date_lbl = QLabel(now.strftime("%Y-%m-%d %A  ·  %H:%M"))
        date_lbl.setObjectName("WelcomeDate")
        lay.addWidget(date_lbl)
        lay.addStretch()
        # 自动刷新指示
        self._refresh_lbl = QLabel("⟳ " + i18n.t("auto_refresh_30s", default="每 30 秒自动刷新"))
        self._refresh_lbl.setObjectName("WelcomeRefresh")
        lay.addWidget(self._refresh_lbl)
        shift_lbl = QLabel("\U0001f504  " + i18n.t("current_shift", default="当前班次") + ": —")
        shift_lbl.setObjectName("WelcomeShift")
        lay.addWidget(shift_lbl)
        try:
            from permission_system import PermissionManager
            user = PermissionManager.current_user()
            name = (user or {}).get("display_name") or (user or {}).get("username") or "—"
        except Exception:
            name = "—"
        op_lbl = QLabel(f"\U0001f464  {name}")
        op_lbl.setObjectName("WelcomeOperator")
        lay.addWidget(op_lbl)
        return strip

    def _build_kpi_section(self):
        card, lay = _section_card(i18n.t("overview_pulse", default="经营脉搏 — KPI"))
        self._kpi_card = card
        self._kpi_section_lay = lay
        # KPI grid 在 refresh() 中填充，初始放空 grid
        self._kpi_grid = QGridLayout()
        self._kpi_grid.setSpacing(FD_SPACE_MD)
        self._kpi_grid.setContentsMargins(0, 8, 0, 0)
        lay.addLayout(self._kpi_grid)
        return card

    def _refresh_kpi_grid(self):
        # 清空旧 widget
        if self._kpi_grid is None:
            return
        while self._kpi_grid.count():
            item = self._kpi_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()  # 先隐藏，防 setParent(None) 瞬间变成带标题栏的顶级窗口
                w.setParent(None)
                w.deleteLater()

        sym = i18n.t("currency_symbol", default="¥")
        full = self._full
        fin = full.get("financials", {}) or {}
        rooms = full.get("rooms", {}) or {}
        kpi = full.get("kpi", {}) or {}
        today = full.get("today", {}) or {}

        revenue = float(fin.get("revenue_total", 0))
        occupancy = float(rooms.get("occupancy_pct", 0))
        adr = float(kpi.get("adr", 0))
        revpar = float(kpi.get("revpar", 0))
        actual_checkin = int(today.get("actual_checkin", 0))
        actual_checkout = int(today.get("actual_checkout", 0))

        trend_pct = fin.get("revenue_trend_pct")
        trend_html = _trend_arrow(trend_pct)

        kpis = [
            (i18n.t("kpi_revenue_label", default="今日营收"),
             f"{sym}{revenue:,.0f}", "primary",
             i18n.t("kpi_revenue_sub", default="本日营收合计"), trend_html),
            (i18n.t("kpi_occupancy_label", default="占用率"),
             f"{occupancy:.1f}%", "positive",
             i18n.t("kpi_occupancy_sub", default=f"在住 {rooms.get('inhouse', 0)} / 总 {rooms.get('total', 0)}"), ""),
            (i18n.t("kpi_adr", default="ADR"),
             f"{sym}{adr:,.0f}", "accent",
             i18n.t("kpi_adr_sub", default="平均房价"), ""),
            (i18n.t("kpi_revpar", default="RevPAR"),
             f"{sym}{revpar:,.0f}", "primary",
             i18n.t("kpi_revpar_sub", default="每可售房收入"), ""),
            (i18n.t("kpi_checkin_today", default="今日入住"),
             f"{actual_checkin}", "positive",
             i18n.t("kpi_checkin_sub", default=f"预计到达 {today.get('arrivals_count', 0)}"), ""),
            (i18n.t("kpi_checkout_today", default="今日退房"),
             f"{actual_checkout}", "danger",
             i18n.t("kpi_checkout_sub", default=f"预计离店 {today.get('departures_count', 0)}"), ""),
        ]

        # 响应式列数：宽度 > 1200 → 4 列，> 800 → 3 列，> 500 → 2 列，否则 1 列
        width = self.width() if self.width() > 0 else 1200
        if width > 1200:
            cols = 3
        elif width > 800:
            cols = 3
        elif width > 500:
            cols = 2
        else:
            cols = 1

        for i, (label, value, tone, sub, trend) in enumerate(kpis):
            r, c = divmod(i, cols)
            self._kpi_grid.addWidget(_kpi_card(label, value, tone=tone, sub=sub, trend_html=trend),
                                      r, c)
        for c in range(cols):
            self._kpi_grid.setColumnStretch(c, 1)

    def _build_rooms_section(self):
        card, lay = _section_card(i18n.t("overview_rooms", default="房态快照"))
        self._rooms_card = card
        self._rooms_section_lay = lay
        return card

    def _refresh_rooms_section(self):
        if self._rooms_section_lay is None:
            return
        # 清空除 header 外的旧 widget
        self._clear_layout_below_header(self._rooms_section_lay, keep=1)

        full = self._full
        rooms = full.get("rooms", {}) or {}
        total = int(rooms.get("total", 0)) or 1

        color_map = {
            "READY": ColorRoomStatus.VACANT.value,
            "INHOUSE": ColorRoomStatus.OCCUPIED.value,
            "DIRTY": ColorRoomStatus.DIRTY.value,
            "MAINTENANCE": ColorRoomStatus.MAINTENANCE.value,
        }
        label_map = {
            "READY": i18n.t("status_ready", default="空净"),
            "INHOUSE": i18n.t("status_inhouse", default="在住"),
            "DIRTY": i18n.t("status_dirty", default="空脏"),
            "MAINTENANCE": i18n.t("status_maintenance", default="维修"),
        }
        counts = {
            "READY": int(rooms.get("ready", 0)),
            "INHOUSE": int(rooms.get("inhouse", 0)),
            "DIRTY": int(rooms.get("dirty", 0)),
            "MAINTENANCE": int(rooms.get("maintenance", 0)),
        }

        total_row = QHBoxLayout()
        total_lbl = QLabel(f"{i18n.t('stat_total', default='总房数')}  {total}")
        total_lbl.setObjectName("RoomTotalLabel")
        total_row.addWidget(total_lbl)
        total_row.addStretch()
        occ_pct = float(rooms.get("occupancy_pct", 0))
        occ_lbl = QLabel(f"{i18n.t('kpi_occupancy_label', default='入住率')}  {occ_pct:.1f}%")
        occ_lbl.setObjectName("RoomOccupancyLabel")
        total_row.addWidget(occ_lbl)
        self._rooms_section_lay.addLayout(total_row)

        for code in ("READY", "INHOUSE", "DIRTY", "MAINTENANCE"):
            self._rooms_section_lay.addWidget(
                _status_bar_row(label_map[code], counts[code], total, color_map[code])
            )
        self._rooms_section_lay.addStretch()

    def _build_financials_section(self):
        card, lay = _section_card(i18n.t("overview_financials", default="财务明细"))
        self._fin_card = card
        self._fin_section_lay = lay
        return card

    def _refresh_financials_section(self):
        if self._fin_section_lay is None:
            return
        self._clear_layout_below_header(self._fin_section_lay, keep=1)

        sym = i18n.t("currency_symbol", default="¥")
        full = self._full
        fin = full.get("financials", {}) or {}

        # 分币种营收
        rev_by_cur = fin.get("revenue_by_currency", {}) or {}
        if rev_by_cur:
            cur_lines = "  ·  ".join(
                f"{c}: {sym}{v:,.0f}" for c, v in rev_by_cur.items()
            )
        else:
            cur_lines = f"{sym}0"
        rev_total = float(fin.get("revenue_total", 0))
        receipts = float(fin.get("receipts_today", 0))
        refunds = float(fin.get("refunds_today", 0))
        deposit = float(fin.get("deposit_held", 0))

        rows = [
            (i18n.t("fin_revenue_total", default="今日营收"), f"{sym}{rev_total:,.2f}", cur_lines),
            (i18n.t("fin_receipts", default="今日收款"), f"{sym}{receipts:,.2f}", ""),
            (i18n.t("fin_refunds", default="今日退款"), f"{sym}{refunds:,.2f}", ""),
            (i18n.t("fin_deposit_held", default="押金在押"), f"{sym}{deposit:,.2f}", ""),
        ]
        for label, value, sub in rows:
            self._fin_section_lay.addWidget(self._kv_row(label, value, sub))
        self._fin_section_lay.addStretch()

    def _build_guests_section(self):
        card, lay = _section_card(i18n.t("overview_inhouse_guests", default="在住客人 Top"))
        self._guests_card = card
        self._guests_section_lay = lay
        return card

    def _refresh_guests_section(self):
        if self._guests_section_lay is None:
            return
        self._clear_layout_below_header(self._guests_section_lay, keep=1)

        full = self._full
        guests = full.get("inhouse_guests_top", []) or []
        if not guests:
            empty = QLabel(i18n.t("overview_no_inhouse", default="暂无在住客人"))
            empty.setObjectName("AlertEmptyLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._guests_section_lay.addWidget(empty)
        else:
            header_row = QFrame()
            hlay = QHBoxLayout(header_row)
            hlay.setContentsMargins(8, 4, 8, 4)
            for col_text, w in [
                (i18n.t("col_room", default="房号"), 56),
                (i18n.t("col_guest", default="客人"), 80),
                (i18n.t("col_checkout", default="退房"), 0),
            ]:
                lbl = QLabel(col_text)
                lbl.setStyleSheet(f"color: {_p('text_muted')}; font-size: 11px;")
                if w > 0:
                    lbl.setMinimumWidth(w)
                hlay.addWidget(lbl, 1 if w == 0 else 0)
            self._guests_section_lay.addWidget(header_row)
            for g in guests[:8]:
                self._guests_section_lay.addWidget(_guest_row(g))
        self._guests_section_lay.addStretch()

    def _build_todos_section(self):
        card, lay = _section_card(i18n.t("overview_todos", default="待办事项"))
        self._todos_card = card
        self._todos_section_lay = lay
        return card

    def _refresh_todos_section(self):
        if self._todos_section_lay is None:
            return
        self._clear_layout_below_header(self._todos_section_lay, keep=1)

        full = self._full
        todos = full.get("todos", {}) or {}
        dirty = int(todos.get("dirty_rooms", 0))
        pending_cards = int(todos.get("pending_cards", 0))
        low_stock = int(todos.get("low_stock", 0))
        staff = full.get("staff", {}) or {}
        onshift = int(staff.get("onshift_count", 0))

        todo_items = [
            (i18n.t("todo_dirty_rooms", default="未清扫房间"),
             i18n.t("todo_dirty_rooms_hint", default=f"{dirty} 间脏房/超时待清扫"),
             i18n.t("todo_go_housekeeping", default="去清扫") if dirty > 0 else ""),
            (i18n.t("todo_pending_cards", default="待发卡"),
             i18n.t("todo_pending_cards_hint", default=f"{pending_cards} 间在住房尚未发卡"),
             i18n.t("todo_go_card", default="去发卡") if pending_cards > 0 else ""),
            (i18n.t("todo_low_stock", default="库存预警"),
             i18n.t("todo_low_stock_hint", default=f"{low_stock} 项商品低于安全阈值"),
             i18n.t("todo_go_inventory", default="去盘点") if low_stock > 0 else ""),
            (i18n.t("todo_staff_onshift", default="当班员工"),
             i18n.t("todo_staff_onshift_hint", default=f"{onshift} 人当班"),
             ""),
        ]
        for title, hint, action in todo_items:
            self._todos_section_lay.addWidget(_todo_item(title, hint, action))
        self._todos_section_lay.addStretch()

    def _build_alerts_section(self):
        card, lay = _section_card(i18n.t("overview_alerts", default="风险告警"))
        self._alerts_card = card
        self._alerts_section_lay = lay
        return card

    def _refresh_alerts_section(self):
        if self._alerts_section_lay is None:
            return
        self._clear_layout_below_header(self._alerts_section_lay, keep=1)

        out = []
        for line in (self._full.get("alerts") or []):
            out.append((str(line), "", "INFO"))
        for check in ALL_CHECKS:
            try:
                ok, count, detail = check.fn()
            except Exception as exc:
                out.append((check.title, str(exc), getattr(check, "severity", "INFO").upper()))
                continue
            if ok or count <= 0:
                continue
            out.append((check.title, detail or f"{count} 项",
                        getattr(check, "severity", "INFO").upper()))

        if not out:
            empty = QLabel(i18n.t("overview_no_alerts", default="暂无风险告警"))
            empty.setObjectName("AlertEmptyLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._alerts_section_lay.addWidget(empty)
        else:
            for title, detail, level in out[:12]:
                self._alerts_section_lay.addWidget(_alert_item(title, detail, level))
        self._alerts_section_lay.addStretch()

    # ── 工具 ─────────────────────────────────────────────

    def _kv_row(self, label: str, value: str, sub: str = "") -> QFrame:
        row = QFrame()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(FD_SPACE_MD)
        lbl = QLabel(label)
        lay.addWidget(lbl)
        lay.addStretch()
        val_lbl = QLabel(value)
        val_lbl.setStyleSheet(f"color: {_p('primary')}; font-weight: 600;")
        lay.addWidget(val_lbl)
        if sub:
            sub_lbl = QLabel(sub)
            sub_lbl.setStyleSheet(f"color: {_p('text_muted')}; font-size: 11px;")
            lay.addWidget(sub_lbl)
        return row

    @staticmethod
    def _clear_layout_below_header(lay: QVBoxLayout, *, keep: int = 1) -> None:
        """清空 layout 末尾 keep 项之后的所有项。"""
        # QVBoxLayout 不支持直接 count-keep 操作；按倒序移除
        while lay.count() > keep:
            item = lay.takeAt(lay.count() - 1)
            w = item.widget()
            if w is not None:
                w.hide()  # 先隐藏，防 setParent(None) 瞬间变成带标题栏的顶级窗口
                w.setParent(None)
                w.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    while sub.count():
                        sub_item = sub.takeAt(0)
                        sw = sub_item.widget()
                        if sw is not None:
                            sw.hide()
                            sw.setParent(None)
                            sw.deleteLater()

    def _refresh_card_styles(self) -> None:
        """换主题后重刷所有外层卡片的 accent 色边框 + 背景调色板（_p() 实时读取当前主题）。"""
        accent = _p("accent")
        border = _p("border")
        surface = _p("surface")
        for card in (self._kpi_card, self._rooms_card, self._fin_card,
                      self._guests_card, self._todos_card, self._alerts_card):
            if card is not None:
                card.setStyleSheet(
                    f"QFrame#OverviewSectionCard {{"
                    f" background: {surface};"
                    f" border: 1px solid {border};"
                    f" border-top: 2px solid {accent};"
                    f" border-left: 1px solid {accent};"
                    f" border-right: 1px solid {accent};"
                    f" border-radius: 8px;"
                    f"}}"
                )
        # 重刷滚动体背景调色板
        bg_root = _p("bg_root")
        body = self._scroll.widget() if self._scroll else None
        if body is not None and body.objectName() == "OverviewScrollBody":
            body_pal = body.palette()
            body_pal.setColor(QPalette.ColorRole.Window, QColor(bg_root))
            body.setPalette(body_pal)
            body.setStyleSheet(f"QWidget#OverviewScrollBody {{ background-color: {bg_root}; }}")
        if self._scroll is not None:
            vp = self._scroll.viewport()
            if vp is not None:
                vp_pal = vp.palette()
                vp_pal.setColor(QPalette.ColorRole.Window, QColor(bg_root))
                vp.setPalette(vp_pal)
            fd_apply_scroll_area(self._scroll)

    def refresh(self):
        """刷新全部数据并重绘子区块（不重建外层 layout）。"""
        self._full = _overview_full()
        self._refresh_card_styles()
        self._refresh_kpi_grid()
        self._refresh_rooms_section()
        self._refresh_financials_section()
        self._refresh_guests_section()
        self._refresh_todos_section()
        self._refresh_alerts_section()
        if self._refresh_lbl is not None:
            now = datetime.now().strftime("%H:%M:%S")
            self._refresh_lbl.setText(
                "⟳ " + i18n.t("auto_refresh_30s", default="每 30 秒自动刷新")
                + f"  ·  {now}"
            )
        self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """响应式：宽度变化时重排 KPI 网格列数。"""
        super().resizeEvent(event)
        if self._kpi_grid is not None:
            self._refresh_kpi_grid()
