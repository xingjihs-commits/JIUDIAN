"""安全审计 — 分区标签页布局（概览 / 流水 / 风险 / 库存 / 能耗 / 门禁）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QFrame, QScrollArea, QInputDialog,
)

from database import db
from i18n import i18n
from ledger_format import ledger_tx_type_display
from permission_system import PermissionManager
from ui_helpers import show_info, show_error, style_data_table
from design_tokens import _p


class AuditTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("AuditTab")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel(i18n.t("nav_audit"))
        title.setObjectName("PageTitle")
        root.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)

        self._build_overview_tab()
        self._build_ledger_tab()
        self._build_risk_tab()
        self._build_inventory_tab()
        self._build_energy_tab()
        self._build_door_tab()

        from ui_surface import fd_apply_content_box, fd_apply_table_palette, fd_apply_label_card
        for box in self.findChildren(QFrame, "ContentBox"):
            fd_apply_content_box(box)
        for tbl in self.findChildren(QTableWidget):
            fd_apply_table_palette(tbl)
        for lbl in self.findChildren(QLabel, "AuditOverviewCard"):
            fd_apply_label_card(lbl)

        from ui_surface import fd_connect_theme_refresh
        fd_connect_theme_refresh(self)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        btn_rf = QPushButton(i18n.t("btn_refresh"))
        btn_rf.setObjectName("FdGhostBtn")
        btn_rf.clicked.connect(self._rf)
        btn_scan = QPushButton(i18n.t("btn_security_scan"))
        btn_scan.setObjectName("SolidPrimaryBtn")
        btn_scan.clicked.connect(self._security_scan)
        btn_stock = QPushButton(i18n.t("btn_inventory_in"))
        btn_stock.setObjectName("FdGhostBtn")
        btn_stock.clicked.connect(self._add_stock)
        action_row.addWidget(btn_rf)
        action_row.addWidget(btn_scan)
        action_row.addWidget(btn_stock)
        action_row.addStretch(1)
        root.addLayout(action_row)

        QTimer.singleShot(0, self._rf)

    def _refresh_theme_styles(self) -> None:
        self._rf()

    def _wrap_scroll(self, inner: QWidget) -> QWidget:
        from ui_surface import fd_apply_scroll_area

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        fd_apply_scroll_area(scroll)
        return scroll

    def _make_table(self, cols: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels([i18n.t(c) for c in cols])
        style_data_table(t)
        hdr = t.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setMinimumHeight(160)
        return t

    def _build_overview_tab(self):
        w = QFrame(); w.setObjectName("ContentBox")
        lay = QVBoxLayout(w)
        lay.setSpacing(12)
        lay.setContentsMargins(10, 10, 10, 10)
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.lo = QLabel()
        self.lo.setObjectName("AuditOverviewCard")
        self.lo.setWordWrap(True)
        self.lr = QLabel()
        self.lr.setObjectName("AuditOverviewCard")
        self.lr.setWordWrap(True)
        self.li = QLabel()
        self.li.setObjectName("AuditOverviewCard")
        self.li.setWordWrap(True)
        for lbl in (self.lo, self.lr, self.li):
            cards.addWidget(lbl, 1)
        lay.addLayout(cards)
        badge_row = QHBoxLayout()
        for object_name, text in (
            ("AuditSafe", i18n.t("audit_inv_normal")),
            ("AuditCaution", "提醒"),
            ("AuditDanger", i18n.t("audit_inv_abnormal")),
        ):
            badge = QLabel(text)
            badge.setObjectName(object_name)
            badge_row.addWidget(badge)
        badge_row.addStretch(1)
        lay.addLayout(badge_row)
        hint = QLabel(i18n.t("audit_overview_hint"))
        hint.setObjectName("FdMutedLabel")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.tabs.addTab(self._wrap_scroll(w), i18n.t("audit_tab_overview"))

    def _build_ledger_tab(self):
        w = QFrame(); w.setObjectName("ContentBox")
        lay = QVBoxLayout(w)
        self.rt = self._make_table(
            ["table_time", "table_type", "table_method", "table_amount", "table_note", "table_deposit"]
        )
        lay.addWidget(self.rt)
        self.tabs.addTab(self._wrap_scroll(w), i18n.t("audit_tab_ledger"))

    def _build_risk_tab(self):
        w = QFrame(); w.setObjectName("ContentBox")
        lay = QVBoxLayout(w)
        self.st = self._make_table(["table_staff", "table_op", "table_hk", "table_risk"])
        lay.addWidget(self.st)
        self.tabs.addTab(self._wrap_scroll(w), i18n.t("audit_tab_risk"))

    def _build_inventory_tab(self):
        w = QFrame(); w.setObjectName("ContentBox")
        lay = QVBoxLayout(w)
        self.it = self._make_table(["table_sku", "table_out", "table_in", "table_hk", "table_status"])
        lay.addWidget(self.it)
        self.tabs.addTab(self._wrap_scroll(w), i18n.t("audit_tab_inventory"))

    def _build_energy_tab(self):
        w = QFrame(); w.setObjectName("ContentBox")
        lay = QVBoxLayout(w)
        self.tbl_energy = self._make_table([
            "audit_energy_col_time", "audit_energy_col_room", "audit_energy_col_kwh",
            "audit_energy_col_hours", "audit_energy_col_ratio", "audit_energy_col_anom",
            "audit_energy_col_staff",
        ])
        lay.addWidget(self.tbl_energy)
        self.tabs.addTab(self._wrap_scroll(w), i18n.t("audit_tab_energy"))

    def _build_door_tab(self):
        w = QFrame(); w.setObjectName("ContentBox")
        lay = QVBoxLayout(w)
        self._lbl_door = QLabel(i18n.t("audit_door_title"))
        self._lbl_door.setObjectName("FdSectionTitle")
        lay.addWidget(self._lbl_door)
        self.tbl_door = self._make_table([
            "audit_door_col_time", "audit_door_col_room", "audit_door_col_card",
            "audit_door_col_source", "audit_door_col_op", "audit_door_col_ok", "audit_door_col_note",
        ])
        lay.addWidget(self.tbl_door)
        idx = self.tabs.addTab(self._wrap_scroll(w), i18n.t("audit_tab_door"))
        self._door_tab_index = idx

    def _add_stock(self):
        sku, ok1 = QInputDialog.getText(self, i18n.t("audit_stock_in_title"), i18n.t("audit_stock_in_sku_ph"))
        if ok1 and sku:
            sku = sku.strip()
            qty, ok2 = QInputDialog.getInt(
                self, i18n.t("audit_stock_in_title"), i18n.t("audit_stock_in_qty").format(sku), 1, 1, 1000
            )
            if ok2:
                row = db.execute("SELECT sku FROM shop_items WHERE sku=?", (sku,)).fetchone()
                if row:
                    db.execute("UPDATE shop_items SET stock=COALESCE(stock,0)+? WHERE sku=?", (qty, sku))
                db.log_inventory_change("WAREHOUSE", "PURCHASE_IN", sku, qty, "boss", i18n.t("audit_stock_manual_note"))
                self._rf()
                show_info(self, i18n.t("operation_completed"), i18n.t("audit_stock_success").format(sku, qty))

    def _security_scan(self):
        ok, msg = db.verify_ledger_integrity()
        if ok:
            show_info(self, i18n.t("audit_hash_ok_title"), i18n.t("audit_hash_ok_body").format(msg))
        else:
            show_error(self, i18n.t("audit_tamper_title"), i18n.t("audit_tamper_body").format(msg))

    def _rf(self):
        ov = db.get_audit_overview()
        cur = i18n.t("currency_symbol")
        self.lo.setText(
            i18n.t("audit_overview_income").format(
                cur, ov["today_income"], ov["today_deposit_net"],
                ov["today_discount_count"], ov["energy_anomaly_count"],
            )
        )
        ok, msg = db.verify_ledger_integrity()
        self.li.setText(i18n.t("audit_overview_ledger").format(msg))
        sc = max(0, 100 - ov["today_discount_count"] * 5 - ov["energy_anomaly_count"] * 10)
        self.lr.setText(i18n.t("audit_overview_risk").format(sc))

        self.rt.setRowCount(0)
        for i, r in enumerate(db.get_recent_ledger(30)):
            self.rt.insertRow(i)
            vals = [
                str(r[8] or "")[11:19],
                ledger_tx_type_display(r[2], r[7]),
                str(r[6] or ""),
                f"{cur}{float(r[4] or 0):.2f}",
                str(r[9] or ""),
                i18n.t("yes_short") if r[7] else "—",
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if j == 3:
                    amt = float(r[4] or 0)
                    item.setForeground(QColor(_p("amount_positive") if amt >= 0 else _p("amount_negative")))
                self.rt.setItem(i, j, item)

        self.st.setRowCount(0)
        for i, s in enumerate(db.get_staff_risk_stats()):
            self.st.insertRow(i)
            for j, k in enumerate(["operator", "total_ops", "discount_ops", "risk_score"]):
                self.st.setItem(i, j, QTableWidgetItem(str(s.get(k, ""))))

        self.it.setRowCount(0)
        for i, it in enumerate(db.get_inventory_comparison()):
            self.it.insertRow(i)
            self.it.setItem(i, 0, QTableWidgetItem(it["sku"]))
            self.it.setItem(i, 1, QTableWidgetItem(f"{it['total_out']:.1f}"))
            self.it.setItem(i, 2, QTableWidgetItem(f"{it['total_in']:.1f}"))
            self.it.setItem(i, 3, QTableWidgetItem(f"{it['hk_deduct']:.1f}"))
            status_item = QTableWidgetItem(i18n.t("audit_inv_abnormal") if it["abnormal"] else i18n.t("audit_inv_normal"))
            status_item.setForeground(QColor(_p("danger") if it["abnormal"] else _p("amount_positive")))
            self.it.setItem(i, 4, status_item)

        self.tbl_energy.setRowCount(0)
        for i, row in enumerate(db.list_recent_energy_readings(40)):
            r = list(row) + ["", ""]
            ts, rid, kwh, hrs, ratio, anom, eid, note, rmode = r[:9]
            self.tbl_energy.insertRow(i)
            cells = [
                str(ts or "")[:16], str(rid or ""), f"{float(kwh or 0):.2f}",
                f"{float(hrs or 0):.2f}", f"{float(ratio or 0):.2f}",
                i18n.t("yes_short") if anom else "—", str(eid or ""),
            ]
            for j, v in enumerate(cells):
                self.tbl_energy.setItem(i, j, QTableWidgetItem(v))

        show_door = PermissionManager.has_permission("view_door_open_audit")
        if hasattr(self, "_door_tab_index"):
            self.tabs.setTabVisible(self._door_tab_index, show_door)
        if show_door:
            self.tbl_door.setRowCount(0)
            for i, row in enumerate(db.list_door_open_audit(100)):
                ts, rid, cid, src, opn, okv, note = (row + ("",))[:7]
                self.tbl_door.insertRow(i)
                vals = [
                    str(ts or "")[:16], str(rid or ""), str(cid or ""), str(src or ""),
                    str(opn or ""), i18n.t("yes_short") if okv else i18n.t("no_short"),
                    (str(note or "")[:40]),
                ]
                for j, v in enumerate(vals):
                    self.tbl_door.setItem(i, j, QTableWidgetItem(v))
