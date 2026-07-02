"""前台账单流水 — 入住收银页顶部可见的近期账本。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QVBoxLayout,
)

from database import db
from design_tokens import _p
from event_bus import bus
from frontdesk_ui import (
    FD_LEDGER_ROW_H, FD_LEDGER_VISIBLE_ROWS, FD_LEDGER_TABLE_HEADER_H,
    FD_MARGIN, FD_SPACE_SM, FD_SPACE_MD,
    fd_section_bar, fd_section_title,
)
from ui_surface import fd_apply_data_table_shell, fd_apply_ledger_dock
from i18n import i18n
from ledger_format import ledger_tx_type_display


class FrontdeskLedgerStrip(QFrame):
    """紧凑流水表，供前台扫一眼今日进出账。"""

    def __init__(self, parent=None, *, limit: int = 18, dock_mode: bool = False):
        super().__init__(parent)
        self._limit = limit
        self._dock_mode = dock_mode
        self._logs_cache: list = []
        self._active_filter = "all"
        self.setObjectName("FrontdeskLedgerStrip")
        if dock_mode:
            self.setObjectName("FrontdeskLedgerDock")
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        pad_l = 0 if dock_mode else FD_MARGIN
        pad_r = 0 if dock_mode else FD_MARGIN
        pad_v = 0 if dock_mode else FD_SPACE_SM
        lay = QVBoxLayout(self)
        lay.setContentsMargins(pad_l, pad_v, pad_r, pad_v)
        lay.setSpacing(0 if dock_mode else FD_SPACE_SM)

        # ── 金线品牌横栏（标题 + 汇总）──
        self.lbl_summary = QLabel("")
        self.lbl_summary.setObjectName("FdMutedLabel")
        bar = fd_section_bar(
            i18n.t("fd_ledger_title"),
            action_widgets=[self.lbl_summary],
            show_gold=not dock_mode,
        )
        lay.addWidget(bar)
        self._section_bar = bar
        if dock_mode:
            from ui_surface import fd_apply_section_bar_embedded
            fd_apply_section_bar_embedded(bar, bg_key="bg_container")

        # ── 快速筛选条 ──
        filter_bar = QFrame()
        filter_bar.setObjectName("FdLedgerFilterBar")
        from ui_surface import fd_apply_ledger_filter_bar
        fd_apply_ledger_filter_bar(filter_bar)
        filter_row = QHBoxLayout(filter_bar)
        filter_row.setContentsMargins(FD_SPACE_SM, 4, FD_SPACE_SM, 4)
        filter_row.setSpacing(FD_SPACE_SM)
        self._filter_btns: dict[str, QPushButton] = {}
        for key, label in [("all", "全部"), ("revenue", "入账"), ("deposit", "押金"), ("expense", "支出"), ("ci_co", "入住/退房")]:
            btn = QPushButton(label)
            btn.setObjectName("FdFilterChip")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(28); btn.setMaximumHeight(30)
            btn.setCheckable(True)
            btn.clicked.connect(self._make_filter_handler(key))
            self._filter_btns[key] = btn
            filter_row.addWidget(btn)
        filter_row.addStretch()
        lay.addWidget(filter_bar)
        self._filter_bar = filter_bar
        self._update_filter_chip_style()

        self.tbl = QTableWidget(0, 6)
        self.tbl.setObjectName("FdLedgerTable")
        self.tbl.setHorizontalHeaderLabels(
            [
                i18n.t("fd_ledger_col_time"),
                i18n.t("fd_ledger_col_type"),
                i18n.t("table_room"),
                "操作人",
                i18n.t("table_amount"),
                i18n.t("fd_ledger_col_note"),
            ]
        )
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        hdr.setMinimumHeight(28)
        hdr.setMaximumHeight(FD_LEDGER_TABLE_HEADER_H)
        hdr.setMinimumSectionSize(48)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setCornerButtonEnabled(False)
        self.tbl.verticalHeader().setDefaultSectionSize(FD_LEDGER_ROW_H)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setAlternatingRowColors(False)
        self.tbl.setShowGrid(False)
        if dock_mode:
            self.tbl.setMinimumHeight(
                FD_LEDGER_TABLE_HEADER_H + FD_LEDGER_ROW_H * FD_LEDGER_VISIBLE_ROWS
            )
            self.tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        else:
            self.tbl.setMinimumHeight(100)
            self.tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        table_shell = QFrame()
        table_shell.setObjectName("DataTableShell")
        table_shell.setFrameShape(QFrame.Shape.NoFrame)
        ts_lay = QVBoxLayout(table_shell)
        ts_lay.setContentsMargins(0, 0, 0, 0)
        ts_lay.addWidget(self.tbl, 1)
        lay.addWidget(table_shell, 1)
        gold_line = not self._dock_mode
        fd_apply_data_table_shell(table_shell, self.tbl, gold_line=gold_line)
        if dock_mode:
            fd_apply_ledger_dock(self, flush_right=True)

        bus.ledger_updated.connect(lambda *_: self.refresh())
        bus.theme_changed.connect(lambda _: self._refresh_theme_styles())
        self.refresh()

    def _refresh_theme_styles(self) -> None:
        """换主题后重刷流水 dock 实底 + 行内分类色（_p token 在 refresh 时写入）。"""
        from ui_surface import (
            fd_apply_data_table_shell,
            fd_apply_ledger_dock,
            fd_apply_ledger_filter_bar,
            fd_refresh_surfaces,
        )

        fd_refresh_surfaces(self)
        if hasattr(self, "_filter_bar"):
            fd_apply_ledger_filter_bar(self._filter_bar)
        shell = self.tbl.parentWidget()
        if shell is not None and shell.objectName() == "DataTableShell":
            fd_apply_data_table_shell(shell, self.tbl, gold_line=not self._dock_mode)
        if self._dock_mode:
            fd_apply_ledger_dock(self, flush_right=True)
        if hasattr(self, "_section_bar"):
            from ui_surface import fd_apply_section_bar_embedded
            fd_apply_section_bar_embedded(
                self._section_bar,
                bg_key="bg_container" if self._dock_mode else "bg_root",
            )
        self._update_filter_chip_style()
        self.refresh()

    def apply_ui_scale(self, scale: float) -> None:
        from tools.cashier_canvas import (
            px, CHECKIN_LEDGER_ROW_H, CHECKIN_LEDGER_HEADER_H,
            CHECKIN_LEDGER_FILTER_H, CHECKIN_LEDGER_ROWS, CHECKIN_SECTION_BAR_H,
        )
        row_h = px(CHECKIN_LEDGER_ROW_H, scale)
        hdr_h = px(CHECKIN_LEDGER_HEADER_H, scale)
        self.tbl.verticalHeader().setDefaultSectionSize(row_h)
        self.tbl.horizontalHeader().setMinimumHeight(max(20, hdr_h - 4))
        self.tbl.horizontalHeader().setMaximumHeight(hdr_h)
        if self._dock_mode:
            self.tbl.setMinimumHeight(hdr_h + row_h * CHECKIN_LEDGER_ROWS)
        chip_h = px(CHECKIN_LEDGER_FILTER_H, scale)
        for btn in self._filter_btns.values():
            btn.setMinimumHeight(max(20, chip_h - 6))
            btn.setMaximumHeight(chip_h)
        bar_h = px(CHECKIN_SECTION_BAR_H, scale)
        for bar in self.findChildren(QFrame):
            if bar.objectName() == "FdSectionBar":
                bar.setFixedHeight(bar_h)

    def _make_filter_handler(self, key: str):
        def handler():
            self._active_filter = key
            self._update_filter_chip_style()
            self._apply_filter()
        return handler

    def _update_filter_chip_style(self):
        for k, btn in self._filter_btns.items():
            is_active = k == self._active_filter
            btn.setProperty("active", is_active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _apply_filter(self):
        if not self._logs_cache:
            self.tbl.setRowCount(0)
            self.tbl.setRowCount(1)
            empty = QTableWidgetItem(i18n.t("fd_ledger_empty"))
            empty.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tbl.setItem(0, 0, empty)
            self.tbl.setSpan(0, 0, 1, 5)
            return
        tx_dep = {"DEPOSIT_IN", "DEPOSIT_OUT"}
        tx_ci_co = {"ROOM_IN", "ROOM_OUT"}
        tx_revenue = tx_ci_co | {"SHOP", "HK"}
        tx_expense = {"PAYOUT", "SALARY", "REPAIR", "MISC"}
        filtered = []
        for row in self._logs_cache:
            tx_type = row[2]
            is_dep = int(row[7]) if row[7] is not None else 0
            if self._active_filter == "all":
                filtered.append(row)
            elif self._active_filter == "deposit":
                if is_dep == 1 or tx_type in tx_dep:
                    filtered.append(row)
            elif self._active_filter == "revenue":
                if tx_type in tx_revenue:
                    filtered.append(row)
            elif self._active_filter == "expense":
                if tx_type in tx_expense:
                    filtered.append(row)
            elif self._active_filter == "ci_co":
                if tx_type in tx_ci_co:
                    filtered.append(row)
        self._render_rows(filtered)

    def refresh(self) -> None:
        cur = i18n.t("currency_symbol")
        try:
            ov = db.get_daily_overview()
            rev = float(ov.get("revenue", 0) or 0)
            self.lbl_summary.setText(
                i18n.t("fd_ledger_summary").format(cur=cur, rev=rev, n=self._limit)
            )
        except Exception:
            self.lbl_summary.setText("")

        self._logs_cache = list(db.get_recent_ledger(self._limit))
        self._apply_filter()

    def _render_rows(self, logs: list) -> None:
        cur = i18n.t("currency_symbol")
        self.tbl.setRowCount(0)
        if not logs:
            self.tbl.setRowCount(1)
            empty = QTableWidgetItem(i18n.t("fd_ledger_empty"))
            empty.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tbl.setItem(0, 0, empty)
            self.tbl.setSpan(0, 0, 1, 6)
            return

        # 分类着色 — 使用 _p() 语义 token 替代硬编码色值
        _green = {"ROOM_IN", "ROOM_OUT"}
        _blue = {"DEPOSIT_IN", "DEPOSIT_OUT"}
        _orange = {"SHOP", "TIP", "SHOP_PURCHASE"}
        _red = {"PAYOUT", "EXPENSE", "SALARY", "REPAIR", "MISC"}
        _shift = {"SHIFT_END", "SHIFT_DIFF", "NIGHT_AUDIT", "CASH_RECONCILE"}

        for row in logs:
            _id, _txid, tx_type, rid, amt, _cur, _pm, is_dep, op_id, created_at, note = row
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            time_str = str(created_at)[11:19] if created_at and len(str(created_at)) > 19 else "--:--:--"
            disp = ledger_tx_type_display(tx_type, is_dep)

            # 操作人
            op_disp = str(op_id) if op_id else "-"

            items = [
                time_str,
                disp,
                str(rid or "—"),
                op_disp,
                f"{cur}{float(amt or 0):.0f}",
                (note or "")[:24],
            ]

            # 主题感知色彩 — _p() token 驱动
            if tx_type in _green:
                color = QColor(_p("amount_positive"))
            elif tx_type in _blue:
                color = QColor(_p("primary"))
            elif tx_type in _orange:
                color = QColor(_p("accent"))
            elif tx_type in _red:
                color = QColor(_p("danger"))
            elif tx_type in _shift:
                color = QColor(_p("accent"))
            else:
                color = None

            for c, v in enumerate(items):
                it = QTableWidgetItem(v)
                if c == 4:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if color and c in (1, 4):
                    it.setForeground(color)
                self.tbl.setItem(r, c, it)
