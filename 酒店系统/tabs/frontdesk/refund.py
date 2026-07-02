"""退款流程 — 走 RefundTransaction 审批表。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QLineEdit, QFormLayout,
)

from i18n import i18n
from sound_helper import play_notify
from transactions.refund import RefundTransaction
from ui_helpers import show_warning, show_info, style_dialog, build_dialog_header

from ._shared import PAYMENT_METHODS, pay_method_label


class RefundMixin:
    """退款流程"""

    def _refund(self):
        if not self.paid_items:
            show_warning(self, i18n.t("refund_title"), i18n.t("refund_no_paid"))
            return
        cur = i18n.t("currency_symbol")
        items_str = "\n".join(
            f"  [{i}] {pay_method_label(method)}: {cur}{amt:.2f}"
            for i, (amt, method) in enumerate(self.paid_items)
        )
        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("refund_title"))
        style_dialog(dlg, size="compact")
        lv = QVBoxLayout(dlg)
        lv.setContentsMargins(16, 16, 16, 16)
        lv.setSpacing(12)
        lv.addWidget(build_dialog_header(i18n.t("refund_header"), i18n.t("refund_header_sub")))

        lv.addWidget(QLabel(i18n.t("refund_paid_label", "已收款项：")))
        lbl_items = QLabel(items_str)
        lbl_items.setObjectName("FdMutedLabel")
        lv.addWidget(lbl_items)

        f = QFormLayout()
        cmb_item = QComboBox()
        for i, (amt, method) in enumerate(self.paid_items):
            cmb_item.addItem(f"[{i}] {pay_method_label(method)}: {cur}{amt:.2f}", i)
        f.addRow(i18n.t("refund_item", "退款项："), cmb_item)

        cmb_refund_method = QComboBox()
        for code, icon, label_key, sub in PAYMENT_METHODS:
            cmb_refund_method.addItem(f"{icon} {i18n.t(label_key)} — {i18n.t(sub)}", code)
        f.addRow(i18n.t("refund_method", "退款方式："), cmb_refund_method)

        txt_reason = QLineEdit()
        txt_reason.setPlaceholderText(i18n.t("refund_reason_ph"))
        f.addRow(i18n.t("refund_reason", "原因："), txt_reason)
        lv.addLayout(f)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton(i18n.t("btn_cancel"))
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok = QPushButton(i18n.t("refund_btn_confirm", "确认退款"))
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_ok.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(btn_ok)
        lv.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = cmb_item.currentData()
        if selected is None:
            show_warning(self, i18n.t("refund_title"), i18n.t("refund_select_item"))
            return
        amt, _method = self.paid_items[selected]
        refund_method = cmb_refund_method.currentData() or "CASH_USD"
        reason = txt_reason.text().strip()
        if not reason:
            show_warning(self, i18n.t("refund_title"), i18n.t("refund_reason_req"))
            return

        op_id = self._current_operator_id()
        try:
            refund_id = RefundTransaction.request_refund(
                room_id=str(self.current_room or ""),
                original_tx_id=None,
                amount=float(amt),
                reason=reason,
                requested_by=str(op_id),
            )
        except Exception as exc:
            show_warning(self, i18n.t("refund_title"), str(exc))
            return

        self.paid_items.pop(selected)
        self._update_payment_summary()
        play_notify("success")
        show_info(
            self,
            i18n.t("refund_complete", default="退款申请"),
            i18n.t(
                "refund_pending_msg",
                default="退款申请已提交（{id}），等待经理批准。方式：{method}",
            ).format(id=refund_id, method=pay_method_label(refund_method)),
        )
