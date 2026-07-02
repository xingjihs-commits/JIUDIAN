"""宾客信息区 — 姓名/手机/备注/黑名单/费率/账单明细"""

import datetime
from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtGui import QColor
from database import db
from design_tokens import _p
from i18n import i18n
from ui_helpers import show_warning, show_info
from ui_surface import fd_apply_table_palette
from permission_system import PermissionManager

logger = __import__("logging").getLogger(__name__)


class GuestInfoMixin:
    """宾客信息、费率档位、账单明细、押金调节、费用计算"""

    # ── 费率档位 ──────────────────────────────────────────────

    def _configure_rate_tier_combo(self):
        self.cmb_rate_tier.blockSignals(True)
        self.cmb_rate_tier.clear()
        self.cmb_rate_tier.addItem(i18n.t("rate_tier_standard"), "standard")
        self.cmb_rate_tier.addItem(i18n.t("rate_tier_walkin"), "walkin")
        self.cmb_rate_tier.addItem(i18n.t("rate_tier_member"), "member")
        if PermissionManager.has_permission("select_contract_rate"):
            self.cmb_rate_tier.addItem(i18n.t("rate_tier_contract"), "contract")
        self.cmb_rate_tier.blockSignals(False)

    def _tier_from_combo(self) -> str:
        d = self.cmb_rate_tier.currentData()
        return str(d) if d else "standard"

    def _on_rate_tier_changed(self):
        if self._forced_unit_price is not None or not self._current_rt:
            return
        tier = self._tier_from_combo()
        unit = db.get_rate_for_room_type(self._current_rt, tier)
        self._expected_room_line_total = unit * self._stay_nights
        self._set_folio_room_charge(self._expected_room_line_total)
        self.txt_rate_reason.clear()
        self._calc()
        self._refresh_rate_override_ui()

    def _rate_override_active(self) -> bool:
        return abs(self._get_folio_room_charge() - self._expected_room_line_total) > 0.02

    def _refresh_rate_override_ui(self):
        active = bool(self.current_room) and self._rate_override_active()
        self.lbl_rate_override.setVisible(active)
        self.txt_rate_reason.setVisible(active)
        if not active:
            self.txt_rate_reason.clear()

    # ── 账单操作（房费/押金）─────────────────────────────────

    def _room_charge_label(self) -> str:
        return i18n.t("room_charge")

    def _deposit_line_label(self):
        return i18n.t("deposit_preauth")

    def _set_folio_room_charge(self, total: float):
        label = self._room_charge_label()
        self.tbl_folio.blockSignals(True)
        try:
            for i in range(self.tbl_folio.rowCount()):
                it = self.tbl_folio.item(i, 0)
                if it and it.text() == label:
                    c1 = self.tbl_folio.item(i, 1)
                    if c1:
                        c1.setText(f"{total:.2f}")
                    return
        finally:
            self.tbl_folio.blockSignals(False)

    def _get_folio_room_charge(self) -> float:
        label = self._room_charge_label()
        for i in range(self.tbl_folio.rowCount()):
            it = self.tbl_folio.item(i, 0)
            if it and it.text() == label:
                c1 = self.tbl_folio.item(i, 1)
                if c1:
                    try:
                        return float(c1.text())
                    except ValueError:
                        return 0.0
        return 0.0

    def _add_folio_item(self, name, price):
        r = self.tbl_folio.rowCount()
        self.tbl_folio.insertRow(r)
        self.tbl_folio.setItem(r, 0, QTableWidgetItem(name))
        amount_item = QTableWidgetItem(f"{price:.2f}")
        if float(price or 0) < 0:
            amount_item.setForeground(QColor(_p("amount_negative")))
        else:
            amount_item.setForeground(QColor(_p("amount_positive")))
        self.tbl_folio.setItem(r, 1, amount_item)
        self._resize_folio_to_content()

    def _resize_folio_to_content(self):
        if hasattr(self, "_sync_folio_table_height"):
            self._sync_folio_table_height()
            return
        try:
            header_h = getattr(self, "_folio_header_h", 34)
            row_h = getattr(self, "_folio_row_h", 36)
            min_rows = getattr(self, "_folio_min_rows", 2)
            rows = max(min_rows, self.tbl_folio.rowCount())
            self.tbl_folio.setFixedHeight(header_h + row_h * rows)
        except Exception:
            pass

    def _apply_folio_deposit(self, amt: float):
        label = self._deposit_line_label()
        self.tbl_folio.blockSignals(True)
        try:
            for i in range(self.tbl_folio.rowCount()):
                it = self.tbl_folio.item(i, 0)
                if it and it.text() == label:
                    c1 = self.tbl_folio.item(i, 1)
                    if c1:
                        c1.setText(f"{amt:.2f}")
                    return
            self._add_folio_item(label, amt)
        finally:
            self.tbl_folio.blockSignals(False)

    def _on_deposit_spin_changed(self, v: float):
        self._apply_folio_deposit(float(v))
        self._calc()

    def _adjust_deposit(self, delta: float):
        from money_utils import to_money
        cur = to_money(str(self.spn_deposit.value()))
        d = to_money(str(delta))
        self.spn_deposit.setValue(max(0.0, float(cur + d)))

    def _round_room_charge(self):
        label = self._room_charge_label()
        self.tbl_folio.blockSignals(True)
        try:
            for i in range(self.tbl_folio.rowCount()):
                it = self.tbl_folio.item(i, 0)
                if it and it.text() == label:
                    c1 = self.tbl_folio.item(i, 1)
                    if c1:
                        try:
                            v = float(c1.text())
                        except ValueError:
                            return
                        c1.setText(f"{int(v // 10) * 10:.2f}")
                    break
        finally:
            self.tbl_folio.blockSignals(False)
        self._calc()
        self._refresh_rate_override_ui()

    def _on_folio_item_changed(self, item: QTableWidgetItem):
        if item.column() != 1:
            return
        row = item.row()
        it0 = self.tbl_folio.item(row, 0)
        if it0 and it0.text() == self._deposit_line_label():
            try:
                v = float(item.text())
            except ValueError:
                return
            self.spn_deposit.blockSignals(True)
            self.spn_deposit.setValue(v)
            self.spn_deposit.blockSignals(False)
            self._calc()
            self._refresh_rate_override_ui()
            return
        self._calc()
        self._refresh_rate_override_ui()

    def _get_folio_deposit_amount(self) -> float:
        label = self._deposit_line_label()
        for i in range(self.tbl_folio.rowCount()):
            it = self.tbl_folio.item(i, 0)
            if it and it.text() == label:
                c1 = self.tbl_folio.item(i, 1)
                if c1:
                    try:
                        return float(c1.text())
                    except ValueError:
                        return 0.0
        return 0.0

    # ── 费用计算 ──────────────────────────────────────────────

    def _calc(self):
        from money_utils import to_money, add_money
        values = []
        for i in range(self.tbl_folio.rowCount()):
            text = self.tbl_folio.item(i, 1).text()
            values.append(to_money(text))
        net_total = float(add_money(*values))

        tax_rate_value = to_money(db.get_config("tax_rate") or "0.07")
        tax_rate = float(tax_rate_value)
        tax_amount = net_total * tax_rate
        total_with_tax = net_total + tax_amount

        phone = self.txt_member.text().strip()
        m = db.get_member_info(phone)
        if m:
            disc = db.get_level_discount(m[1])
            dep_exempt = self._get_folio_deposit_amount()
            total_with_tax = (total_with_tax - dep_exempt) * disc + dep_exempt
            self.lbl_member_info.setText(i18n.t("member_info").format(m[0], m[1], int(disc * 100)))
        else:
            self.lbl_member_info.setText("-")

        self.lbl_tax.setText(f"{i18n.t('tax_vat')} ({int(tax_rate * 100)}%): {i18n.t('currency_symbol')}{tax_amount:.2f}")
        self.lbl_total.setText(f"{i18n.t('label_total')}: {i18n.t('currency_symbol')}{total_with_tax:.2f}")
        self._total_cache = total_with_tax
        self._update_payment_summary()
        if hasattr(self, '_update_room_charge_est'):
            self._update_room_charge_est()

    def _sync_amount_from_balance(self):
        remaining = max(0.0, self._total_cache - sum(i[0] for i in self.paid_items))
        if remaining > 0.009:
            self.txt_amount.setText(f"{remaining:.2f}")
        else:
            self.txt_amount.clear()

    def _update_payment_summary(self):
        paid_sum = sum(i[0] for i in self.paid_items)
        balance = self._total_cache - paid_sum
        cur = i18n.t("currency_symbol")
        if balance > 0:
            self.lbl_paid.setObjectName("FdAmountPositive")
            self.lbl_paid.setText(
                f"{i18n.t('label_paid')}: {cur}{paid_sum:.2f}{i18n.t('payment_summary_due')}{cur}{balance:.2f}"
            )
        else:
            self.lbl_paid.setObjectName("FdAmountNegative" if balance < 0 else "FdAmountPositive")
            self.lbl_paid.setText(
                f"{i18n.t('label_paid')}: {cur}{paid_sum:.2f}{i18n.t('payment_summary_change')}{cur}{abs(balance):.2f}"
            )
        self.lbl_paid.style().unpolish(self.lbl_paid)
        self.lbl_paid.style().polish(self.lbl_paid)
        self._sync_amount_from_balance()
        self._refresh_action_gates()

    def _folio_fully_covered(self) -> bool:
        paid_sum = sum(i[0] for i in self.paid_items)
        return paid_sum + 1e-9 >= self._total_cache

    def _deferral_remark_ok(self) -> bool:
        return len(self.txt_deferral_remark.text().strip()) >= 4

    def _refresh_action_gates(self):
        inh = False
        if self.current_room:
            st_row = db.execute(
                "SELECT status, COALESCE(lock_no,'') FROM rooms WHERE room_id=?",
                (self.current_room,),
            ).fetchone()
            inh = bool(st_row and st_row[0] == "INHOUSE")
        has_receive = len(self.paid_items) >= 1
        fully = self._folio_fully_covered()
        has_card = self._card_issued_session
        d_ok = self._deferral_remark_ok()
        vacant_ctx = bool(self.current_room) and not inh

        self.btn_issue_card.setEnabled(bool(self.current_room))
        self.btn_cancel_card.setEnabled(inh)
        self.btn_extend_stay.setEnabled(inh)
        self.btn_lost_card.setEnabled(inh)
        self.btn_co.setEnabled(inh)
        self.btn_quick_co.setEnabled(inh)

        self.btn_issue_card.setToolTip(
            "" if not self.current_room else (
                i18n.t("hint_issue_card_ready") if vacant_ctx and has_receive and fully
                else i18n.t("hint_issue_card_after_pay")
            )
        )
        self._sync_flow_strip()

    # ── 会员/黑名单 ───────────────────────────────────────────

    def _check_member(self):
        self._calc()

    def _check_blacklist(self, phone: str) -> bool:
        if not phone or not phone.strip():
            return False
        phone = phone.strip()
        issues = []

        overtime_rows = db.execute(
            "SELECT room_id, name, checkout_time FROM guests "
            "WHERE phone=? AND status='OVERTIME' ORDER BY id DESC LIMIT 5",
            (phone,),
        ).fetchall()
        for r in overtime_rows:
            issues.append(f"超时未退房：{r[0]}（{r[1] or '-'}）应退：{r[2] or '-'}")

        debtor_rows = db.execute("""
            SELECT l.room_id, COALESCE(SUM(l.amount), 0) as net_deposit
            FROM ledger l
            JOIN guests g ON g.phone = ? AND l.room_id = g.room_id
            WHERE l.is_deposit = 1 AND l.tx_type IN ('DEPOSIT_IN', 'DEPOSIT_OUT')
            GROUP BY l.room_id
            HAVING net_deposit < 0
            ORDER BY net_deposit ASC
        """, (phone,)).fetchall()
        for r in debtor_rows:
            issues.append(f"欠款: {r[0]} 净押金 {r[1]:.2f} 元")

        if issues:
            show_warning(self, "⚠️ 黑名单命中",
                         f"手机号 {phone} 存在以下历史问题：\n\n" + "\n".join(issues) + "\n\n建议核实身份后再办理入住。")
            return True
        return False

    # ── 账单明细 ──────────────────────────────────────────────

    def _show_bill_details(self):
        """显示当前房间的账单明细"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QPushButton, QHeaderView
        from ui_helpers import style_dialog
        from services.bill_detail import list_bill_details

        if not self.current_room:
            show_warning(self, "提示", "请先选择房间")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("账单明细")
        style_dialog(dlg, size="medium")
        lay = QVBoxLayout(dlg)

        tbl = QTableWidget(0, 4)
        tbl.setHorizontalHeaderLabels(["项目", "数量", "单价", "合计"])
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        fd_apply_table_palette(tbl)

        tx_rows = db.execute(
            "SELECT payment_tx_id FROM payments WHERE room_id=? ORDER BY created_at DESC",
            (self.current_room,),
        ).fetchall()

        for (tx_id,) in tx_rows:
            details = list_bill_details(tx_id)
            for d in details:
                r = tbl.rowCount()
                tbl.insertRow(r)
                tbl.setItem(r, 0, QTableWidgetItem(d.get("description", d.get("item_type", ""))))
                tbl.setItem(r, 1, QTableWidgetItem(str(d.get("quantity", ""))))
                tbl.setItem(r, 2, QTableWidgetItem(fmt_money(to_money(d.get('unit_price', 0)), '$')))
                tbl.setItem(r, 3, QTableWidgetItem(fmt_money(to_money(d.get('total', 0)), '$')))

        lay.addWidget(tbl)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)
        lay.addWidget(btn_close)

        dlg.exec()
