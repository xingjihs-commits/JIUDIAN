"""待审核退款列表 — 经理批准/拒绝。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QInputDialog,
)
from PySide6.QtCore import Qt

from components import OptimizedButton
from ui_surface import fd_apply_table_palette, fd_sync_table_height
from database import db
from i18n import i18n
from permission_system import PermissionManager
from tabs._shared import current_operator_id
from transactions.refund import RefundTransaction
from ui_helpers import show_info, show_warning


class RefundsTab(QWidget):
    """PENDING 退款审批面板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RefundsTab")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(QLabel(i18n.t("refund_pending_title", default="待审核退款")))
        header.addStretch()
        self.btn_refresh = OptimizedButton(i18n.t("btn_refresh", default="刷新"), "secondary", "small")
        self.btn_refresh.clicked.connect(self.refresh)
        header.addWidget(self.btn_refresh)
        root.addLayout(header)

        # ── 操作按钮行（选中行后批准/拒绝）──
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.btn_approve = OptimizedButton(
            i18n.t("refund_btn_approve", default="✅ 批准"), "primary", "large"
        )
        self.btn_reject = OptimizedButton(
            i18n.t("refund_btn_reject", default="❌ 拒绝"), "danger", "medium"
        )
        self.btn_approve.clicked.connect(self._on_approve_selected)
        self.btn_reject.clicked.connect(self._on_reject_selected)
        action_row.addWidget(self.btn_approve)
        action_row.addWidget(self.btn_reject)
        action_row.addStretch()
        root.addLayout(action_row)

        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels([
            i18n.t("refund_col_id", default="退款单号"),
            i18n.t("refund_col_room", default="房间"),
            i18n.t("refund_col_orig", default="原始金额"),
            i18n.t("refund_col_amount", default="退款额"),
            i18n.t("refund_col_reason", default="原因"),
            i18n.t("refund_col_requested", default="申请时间"),
        ])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        fd_apply_table_palette(self.tbl)
        root.addWidget(self.tbl)

        self.refresh()

    def refresh(self) -> None:
        rows = db.execute(
            """
            SELECT refund_id, room_id, original_amount, refund_amount,
                   refund_reason, requested_at, requested_by
            FROM refunds
            WHERE status='PENDING'
            ORDER BY requested_at DESC
            """
        ).fetchall()

        self.tbl.setRowCount(len(rows))
        can_approve = PermissionManager.has_permission("refund.approve")
        cur = i18n.t("currency_symbol", default="$")

        for row_idx, row in enumerate(rows):
            refund_id, room_id, orig_amt, refund_amt, reason, requested_at, _requested_by = row
            self.tbl.setItem(row_idx, 0, QTableWidgetItem(str(refund_id)))
            self.tbl.setItem(row_idx, 1, QTableWidgetItem(str(room_id or "")))
            self.tbl.setItem(row_idx, 2, QTableWidgetItem(f"{cur}{float(orig_amt or 0):.2f}"))
            self.tbl.setItem(row_idx, 3, QTableWidgetItem(f"{cur}{float(refund_amt or 0):.2f}"))
            self.tbl.setItem(row_idx, 4, QTableWidgetItem(str(reason or "")))
            self.tbl.setItem(row_idx, 5, QTableWidgetItem(str(requested_at or "")[:19]))
        fd_sync_table_height(self.tbl, min_rows=2, max_rows=12, row_h=44)

    def _on_approve_selected(self):
        row = self.tbl.currentRow()
        if row < 0:
            show_warning(self, i18n.t("dlg_tip", default="提示"),
                         i18n.t("refund_select_first", default="请先选择一条退款"))
            return
        refund_id = self.tbl.item(row, 0).text()
        self._on_approve(refund_id)

    def _on_reject_selected(self):
        row = self.tbl.currentRow()
        if row < 0:
            show_warning(self, i18n.t("dlg_tip", default="提示"),
                         i18n.t("refund_select_first", default="请先选择一条退款"))
            return
        refund_id = self.tbl.item(row, 0).text()
        self._on_reject(refund_id)

    def _on_approve(self, refund_id: str) -> None:
        if not PermissionManager.has_permission("refund.approve"):
            show_warning(
                self,
                i18n.t("refund_title", default="退款"),
                i18n.t("refund_no_perm", default="无批准退款权限"),
            )
            return
        op = current_operator_id()
        try:
            RefundTransaction.approve(refund_id, op)
        except Exception as exc:
            show_warning(self, i18n.t("refund_title", default="退款"), str(exc))
            return
        show_info(
            self,
            i18n.t("refund_title", default="退款"),
            i18n.t("refund_approved_msg", default="退款已批准，待执行完成"),
        )
        self.refresh()

    def _on_reject(self, refund_id: str) -> None:
        if not PermissionManager.has_permission("refund.approve"):
            show_warning(
                self,
                i18n.t("refund_title", default="退款"),
                i18n.t("refund_no_perm", default="无批准退款权限"),
            )
            return
        reason, ok = QInputDialog.getText(
            self,
            i18n.t("refund_reject_title", default="拒绝退款"),
            i18n.t("refund_reject_reason", default="请输入拒绝原因："),
        )
        if not ok or not str(reason or "").strip():
            return
        op = current_operator_id()
        try:
            RefundTransaction.reject(refund_id, op, str(reason).strip())
        except Exception as exc:
            show_warning(self, i18n.t("refund_title", default="退款"), str(exc))
            return
        show_info(
            self,
            i18n.t("refund_title", default="退款"),
            i18n.t("refund_rejected_msg", default="退款申请已拒绝"),
        )
        self.refresh()
