"""收款区 — 支付方式/快捷金额/组合支付"""

import json as _json
import time
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel,
    QLineEdit, QDoubleSpinBox, QDialog, QFormLayout, QPlainTextEdit,
    QInputDialog, QComboBox, QGroupBox,
)
from PySide6.QtCore import Qt, QObject, QEvent
from database import db
from i18n import i18n
from ui_helpers import (
    show_warning, show_info, ask_confirm,
    style_dialog, build_dialog_header,
)
from frontdesk_ui import fd_apply_compact_input, fd_apply_action_btn
from design_tokens import _p
from event_bus import bus
from sound_helper import play_success, play_fail, play_warn, play_notify
from ._shared import PAYMENT_METHODS, _checkin_pay_methods_combo

logger = __import__("logging").getLogger(__name__)


class PaymentMethodTiles(QWidget):
    """付款方式：4 默认 + 4 折叠。"""

    def __init__(self, parent=None, *, compact: bool = False):
        super().__init__(parent)
        self.setObjectName("PaymentMethodTiles")
        self._buttons: dict[str, QPushButton] = {}
        self._current = PAYMENT_METHODS[0][0]
        self._expanded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)  # [sub-j] 8 → 4 像素级紧凑

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)  # [sub-j] 8 → 4 像素级紧凑

        from ._shared import PAYMENT_DEFAULT_COUNT as D
        self._default_count = D

        for idx, (code, _icon, label_key, sub) in enumerate(PAYMENT_METHODS):
            btn = QPushButton(i18n.t(label_key))
            btn.setObjectName("PayMethodTile")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            # [sub-j] 像素级高度：40px → 48px，与 v4 一致；min-width 80px
            btn.setFixedHeight(48)
            btn.setMinimumWidth(80)
            btn.setProperty("selected", code == self._current)
            btn.setToolTip(f"{code} — {i18n.t(sub)}")
            btn.clicked.connect(lambda checked=False, c=code: self.setCurrentData(c))
            self._buttons[code] = btn
            if idx < 4:
                self._grid.addWidget(btn, 0, idx)
            elif idx < D:
                if idx == 4:
                    self._row1_wrap = QWidget()
                    self._row1_lay = QHBoxLayout(self._row1_wrap)
                    self._row1_lay.setContentsMargins(0, 0, 0, 0)
                    self._row1_lay.setSpacing(4)  # [sub-j] 8 → 4
                    self._grid.addWidget(self._row1_wrap, 1, 0, 1, 4)
                self._row1_lay.addWidget(btn, 1)
            else:
                if idx == D:
                    self._extra_wrap = QWidget()
                    self._extra_lay = QHBoxLayout(self._extra_wrap)
                    self._extra_lay.setContentsMargins(0, 0, 0, 0)
                    self._extra_lay.setSpacing(4)  # [sub-j] 8 → 4
                    self._grid.addWidget(self._extra_wrap, 2, 0, 1, 4)
                    self._extra_wrap.setVisible(False)
                self._extra_lay.addWidget(btn, 1)
                btn.setVisible(False)

        root.addLayout(self._grid)

        self._btn_more = QPushButton(i18n.t("payment_btn_more_pay", "更多支付方式 ▼"))
        self._btn_more.setObjectName("FdGhostBtn")
        self._btn_more.setMaximumHeight(30)
        self._btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_more.clicked.connect(self._toggle_expand)
        root.addWidget(self._btn_more, 0, Qt.AlignmentFlag.AlignHCenter)

        self.setCurrentData(self._current)

    def _toggle_expand(self):
        self._expanded = not self._expanded
        if hasattr(self, "_extra_wrap"):
            self._extra_wrap.setVisible(self._expanded)
        for idx, (code, _, _, _) in enumerate(PAYMENT_METHODS):
            if idx >= self._default_count:
                btn = self._buttons.get(code)
                if btn:
                    btn.setVisible(self._expanded)
        self._btn_more.setText(i18n.t("payment_btn_less_pay", "收起 ▲") if self._expanded else i18n.t("payment_btn_more_pay", "更多支付方式 ▼"))

    def currentData(self) -> str:
        return self._current

    def setCurrentData(self, code: str) -> None:
        self._current = code
        for k, btn in self._buttons.items():
            sel = k == code
            btn.setChecked(sel)
            btn.setProperty("selected", sel)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def apply_ui_scale(self, scale: float) -> None:
        from tools.cashier_canvas import px, CHECKIN_PAY_TILE_H, CHECKIN_PAY_MORE_H
        tile_h = px(CHECKIN_PAY_TILE_H, scale)
        for btn in self._buttons.values():
            btn.setMinimumHeight(tile_h)
            btn.setMaximumHeight(px(CHECKIN_PAY_TILE_H + 8, scale))
        self._btn_more.setMaximumHeight(px(CHECKIN_PAY_MORE_H, scale))


class PaymentMixin:
    """收款操作"""

    # ── 收款 ──────────────────────────────────────────────────

    def _do_pay(self):
        try:
            amt = float(self.txt_amount.text())
        except Exception:
            show_warning(self, i18n.t("table_amount"), i18n.t("invalid_number"))
            return
        if amt < 0:
            show_warning(self, i18n.t("dlg_tip"), i18n.t("invalid_number"))
            return
        if amt == 0:
            show_warning(self, i18n.t("dlg_tip"), i18n.t("pay_hint_amt_zero"))
            return
        method = self.pay_tiles.currentData() or "CASH_USD"

        if method == "CREDIT":
            company, ok = QInputDialog.getText(self, i18n.t("pay_credit"), i18n.t("pay_credit_prompt"))
            if not ok or not (company or "").strip():
                show_warning(self, i18n.t("pay_credit"), i18n.t("pay_credit_name_req"))
                return

        self.paid_items.append((amt, method))
        self.txt_amount.clear()
        self._update_payment_summary()
        if self._folio_fully_covered() and self.current_room and not self._card_issued_session:
            st_row = db.execute(
                "SELECT status FROM rooms WHERE room_id=?", (self.current_room,)
            ).fetchone()
            if st_row and st_row[0] != "INHOUSE":
                self._auto_issue_and_checkin()

    def _auto_issue_and_checkin(self):
        """收款足额 → 自动连发卡 + 自动入住"""
        if not self.current_room:
            return
        st_row = db.execute(
            "SELECT status, COALESCE(lock_no,'') FROM rooms WHERE room_id=?",
            (self.current_room,),
        ).fetchone()
        if not st_row:
            show_warning(self, i18n.t("dlg_tip"), i18n.t("pay_room_not_found"))
            return
        if st_row[0] == "INHOUSE":
            return
        if not str(st_row[1] or "").strip():
            show_warning(self, i18n.t("pay_auto_issue_fail"), i18n.t("pay_auto_issue_no_lock"))
            return
        if self._rate_override_active() and len(self.txt_rate_reason.text().strip()) < 4:
            show_warning(self, i18n.t("dlg_tip"), i18n.t("msg_rate_override_needs_reason"))
            return

        from card_ritual_dialog import CardRitualDialog
        guest = self.txt_name.text().strip() or i18n.t("guest_walk_in")
        dlg = CardRitualDialog(self, room_id=self.current_room, guest_name=guest, mode="issue")
        if dlg.exec() != QDialog.Accepted:
            show_warning(self, i18n.t("pay_auto_issue_fail"), i18n.t("pay_card_not_issued"))
            return
        self._card_issued_session = True
        self._refresh_action_gates()
        self._commit()

    # ── 快速金额 ──────────────────────────────────────────────

    def _fill_remaining(self):
        remaining = max(0, self._total_cache - sum(i[0] for i in self.paid_items))
        if remaining > 0:
            self.txt_amount.setText(f"{remaining:.2f}")

    def _fill_amount(self, amount: float):
        self.txt_amount.setText(f"{amount:.2f}")

    def _init_amount_shortcuts(self):
        class _AmtKeyFilter(QObject):
            def __init__(self, tab):
                super().__init__(tab)
                self._tab = tab

            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.KeyPress:
                    txt = obj.text().strip()
                    key_text = event.text().strip()
                    if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key.Key_A:
                        obj.clear()
                        return True
                    if not txt:
                        if key_text == "5":
                            obj.setText("50.00")
                            return True
                        elif key_text == "1":
                            obj.setText("100.00")
                            return True
                        elif key_text == "2":
                            obj.setText("200.00")
                            return True
                    if key_text.lower() == "t":
                        self._tab._fill_remaining()
                        return True
                    if key_text in ("\r", "\n"):
                        self._tab._do_pay()
                        return True
                return super().eventFilter(obj, event)

        self._amount_key_filter = _AmtKeyFilter(self)
        self.txt_amount.installEventFilter(self._amount_key_filter)

    # ── 清空收款 ──────────────────────────────────────────────

    def _clear_payments(self):
        if self._posted_ledger_pay_idx > 0:
            show_warning(self, i18n.t("dlg_tip"), i18n.t("msg_cannot_clear_after_ledger"))
            return
        self.paid_items = []
        self._update_payment_summary()

    # ── 入账 ──────────────────────────────────────────────────

    def _post_new_payments_ledger(self, start_idx: int, guest_name: str, extra_note: str, conn=None):
        rid = self.current_room
        if not rid or start_idx >= len(self.paid_items):
            return
        folio_items = []
        for i in range(self.tbl_folio.rowCount()):
            item_name = self.tbl_folio.item(i, 0).text()
            item_price = float(self.tbl_folio.item(i, 1).text())
            folio_items.append((item_name, item_price))
        dep_tag = self._deposit_line_label()
        deposit_total = sum(p for n, p in folio_items if n == dep_tag)
        non_deposit_total = sum(p for n, p in folio_items if n != dep_tag)
        total_folio = deposit_total + non_deposit_total
        tail = (" " + extra_note.strip()) if extra_note and extra_note.strip() else ""
        for j in range(start_idx, len(self.paid_items)):
            amt, method = self.paid_items[j]
            if amt <= 0:
                continue
            credit_note = ""
            if method == "CREDIT":
                credit_note = i18n.t("pay_credit_note")
            if total_folio > 0 and deposit_total > 0:
                dep_portion = round(amt * deposit_total / total_folio, 2)
                room_portion = round(amt - dep_portion, 2)
                if dep_portion > 0:
                    if conn is not None:
                        db.append_ledger_conn(conn, "DEPOSIT_IN", dep_portion, "CASH", 1, rid, i18n.t("ledger_note_deposit_in").format(guest_name) + tail + credit_note, pay_method=method, is_deposit=1)
                    else:
                        db.append_ledger("DEPOSIT_IN", dep_portion, "CASH", 1, rid, i18n.t("ledger_note_deposit_in").format(guest_name) + tail + credit_note, pay_method=method, is_deposit=1)
                if room_portion > 0:
                    if conn is not None:
                        db.append_ledger_conn(conn, "ROOM_IN", room_portion, "CASH", 1, rid, i18n.t("ledger_note_room_in").format(guest_name) + tail + credit_note, pay_method=method)
                    else:
                        db.append_ledger("ROOM_IN", room_portion, "CASH", 1, rid, i18n.t("ledger_note_room_in").format(guest_name) + tail + credit_note, pay_method=method)
            else:
                if conn is not None:
                    db.append_ledger_conn(conn, "ROOM_IN", amt, "CASH", 1, rid, i18n.t("ledger_note_room_in").format(guest_name) + tail + credit_note, pay_method=method)
                else:
                    db.append_ledger("ROOM_IN", amt, "CASH", 1, rid, i18n.t("ledger_note_room_in").format(guest_name) + tail + credit_note, pay_method=method)

    # ── 组合支付 ──────────────────────────────────────────────

    def _combined_pay(self):
        remaining = max(0.0, self._total_cache - sum(i[0] for i in self.paid_items))
        cur = i18n.t("currency_symbol")

        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("pay_combined"))
        style_dialog(dlg, size="compact")
        lv = QVBoxLayout(dlg)
        # [sub-j] 像素级紧凑：margins (16,16,16,16) → (8,8,8,8)，spacing 12 → 8
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(8)
        lv.addWidget(build_dialog_header(i18n.t("pay_combined"), i18n.t("pay_combined_sub", "应付总额: {}{:.2f} | 待付: {}{:.2f}").format(cur, self._total_cache, cur, remaining)))

        f = QFormLayout()
        spn_cash = QDoubleSpinBox()
        spn_cash.setRange(0, 999999)
        spn_cash.setDecimals(2)
        spn_cash.setPrefix(cur)
        spn_cash.setValue(0)
        f.addRow(i18n.t("payment_usd_cash") + "：", spn_cash)

        spn_wechat = QDoubleSpinBox()
        spn_wechat.setRange(0, 999999)
        spn_wechat.setDecimals(2)
        spn_wechat.setPrefix(cur)
        spn_wechat.setValue(0)
        f.addRow(i18n.t("payment_wechat") + "：", spn_wechat)

        spn_alipay = QDoubleSpinBox()
        spn_alipay.setRange(0, 999999)
        spn_alipay.setDecimals(2)
        spn_alipay.setPrefix(cur)
        spn_alipay.setValue(0)
        f.addRow(i18n.t("payment_alipay") + "：", spn_alipay)

        spn_card = QDoubleSpinBox()
        spn_card.setRange(0, 999999)
        spn_card.setDecimals(2)
        spn_card.setPrefix(cur)
        spn_card.setValue(0)
        f.addRow(i18n.t("payment_bank_card") + "：", spn_card)
        lv.addLayout(f)

        lbl_total = QLabel(i18n.t("pay_combined_total", "合计: {}0.00").format(cur))
        lbl_total.setObjectName("H4Title")

        def _update_total():
            t = spn_cash.value() + spn_wechat.value() + spn_alipay.value() + spn_card.value()
            lbl_total.setText(i18n.t("pay_combined_total", "合计: {}{:.2f}").format(cur, t))

        for s in (spn_cash, spn_wechat, spn_alipay, spn_card):
            s.valueChanged.connect(lambda _: _update_total())
        lv.addWidget(lbl_total)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton(i18n.t("btn_cancel"))
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok = QPushButton(i18n.t("pay_combined_confirm", "确认组合支付"))
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_ok.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(btn_ok)
        lv.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        total = spn_cash.value() + spn_wechat.value() + spn_alipay.value() + spn_card.value()
        if total < remaining - 0.01:
            show_warning(self, i18n.t("pay_combined"), i18n.t("pay_combined_short", "合计 {}{:.2f} 未达到待付金额 {}{:.2f}").format(cur, total, cur, remaining))
            return

        parts = [
            (spn_cash.value(), "CASH_USD"),
            (spn_wechat.value(), "WECHAT"),
            (spn_alipay.value(), "ALIPAY"),
            (spn_card.value(), "BANK_CARD"),
        ]
        for amt, method in parts:
            if amt > 0:
                self.paid_items.append((amt, method))
        self._update_payment_summary()
        if self._folio_fully_covered() and self.current_room and not self._card_issued_session:
            st_row = db.execute(
                "SELECT status FROM rooms WHERE room_id=?", (self.current_room,)
            ).fetchone()
            if st_row and st_row[0] != "INHOUSE":
                self._auto_issue_and_checkin()

