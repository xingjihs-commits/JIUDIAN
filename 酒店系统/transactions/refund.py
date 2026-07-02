"""transactions.refund — 退款申请 / 审批 / 执行。"""
from __future__ import annotations

import datetime as _dt
import uuid
from typing import Optional

from database import db


class RefundTransaction:
    """退款状态机：PENDING → APPROVED/REJECTED → COMPLETED。"""

    @staticmethod
    def _new_refund_id() -> str:
        return f"REF_{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _audit(
        refund_id: str,
        from_status: str,
        to_status: str,
        action_by: str,
        comment: str = "",
        *,
        conn=None,
    ) -> None:
        sql = (
            "INSERT INTO refund_audit_log(refund_id, from_status, to_status, action_by, comment) "
            "VALUES (?,?,?,?,?)"
        )
        params = (refund_id, from_status, to_status, action_by, comment)
        if conn is not None:
            conn.execute(sql, params)
        else:
            db.execute(sql, params)

    @staticmethod
    def _get_refund(refund_id: str, *, conn=None):
        sql = (
            "SELECT refund_id, room_id, original_tx_id, original_amount, refund_amount, "
            "currency, status, requested_by, approved_by "
            "FROM refunds WHERE refund_id=?"
        )
        if conn is not None:
            return conn.execute(sql, (refund_id,)).fetchone()
        return db.execute(sql, (refund_id,)).fetchone()

    @classmethod
    def request_refund(
        cls,
        room_id: str,
        original_tx_id: Optional[str],
        amount: float,
        reason: str,
        requested_by: str,
        *,
        currency: str = "USD",
        guest_id: Optional[int] = None,
        note: str = "",
    ) -> str:
        """提交退款申请，返回 refund_id（状态 PENDING）。"""
        room_id = str(room_id or "").strip()
        requested_by = str(requested_by or "").strip()
        reason = str(reason or "").strip()
        if not room_id:
            raise ValueError("room_id 不能为空")
        if not requested_by:
            raise ValueError("requested_by 不能为空")
        if not reason:
            raise ValueError("reason 不能为空")
        amount = float(amount)
        if amount <= 0:
            raise ValueError("退款金额必须大于 0")

        original_amount = amount
        if original_tx_id:
            tx_row = db.execute(
                "SELECT amount, COALESCE(currency, 'USD') FROM ledger WHERE tx_id=?",
                (original_tx_id,),
            ).fetchone()
            if tx_row:
                original_amount = float(tx_row[0])
                currency = str(tx_row[1] or currency)
            if amount > original_amount:
                raise ValueError("退款金额不能超过原始交易金额")

        refund_id = cls._new_refund_id()
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO refunds(
                    refund_id, room_id, guest_id, original_tx_id,
                    original_amount, refund_amount, currency, refund_reason,
                    requested_by, note, status
                ) VALUES (?,?,?,?,?,?,?,?,?,?, 'PENDING')
                """,
                (
                    refund_id,
                    room_id,
                    guest_id,
                    original_tx_id,
                    original_amount,
                    amount,
                    currency,
                    reason,
                    requested_by,
                    note,
                ),
            )
            conn.execute(
                """
                INSERT INTO refund_lines(
                    refund_id, line_type, description, amount, reason
                ) VALUES (?, 'ROOM_CHARGE', ?, ?, ?)
                """,
                (refund_id, reason, amount, reason),
            )
            cls._audit(refund_id, "NONE", "PENDING", requested_by, "申请", conn=conn)
        return refund_id

    @classmethod
    def approve(cls, refund_id: str, approver: str) -> None:
        """经理批准：PENDING → APPROVED。"""
        approver = str(approver or "").strip()
        if not approver:
            raise ValueError("approver 不能为空")
        with db.transaction() as conn:
            row = cls._get_refund(refund_id, conn=conn)
            if not row:
                raise RuntimeError("退款单不存在")
            if str(row[6]) != "PENDING":
                raise RuntimeError(f"当前状态 {row[6]} 不可批准")
            conn.execute(
                """
                UPDATE refunds
                SET status='APPROVED', approved_at=CURRENT_TIMESTAMP, approved_by=?
                WHERE refund_id=? AND status='PENDING'
                """,
                (approver, refund_id),
            )
            cls._audit(refund_id, "PENDING", "APPROVED", approver, "批准", conn=conn)

    @classmethod
    def reject(cls, refund_id: str, approver: str, reason: str) -> None:
        """经理拒绝：PENDING → REJECTED。"""
        approver = str(approver or "").strip()
        reason = str(reason or "").strip()
        if not approver:
            raise ValueError("approver 不能为空")
        if not reason:
            raise ValueError("拒绝原因不能为空")
        with db.transaction() as conn:
            row = cls._get_refund(refund_id, conn=conn)
            if not row:
                raise RuntimeError("退款单不存在")
            if str(row[6]) != "PENDING":
                raise RuntimeError(f"当前状态 {row[6]} 不可拒绝")
            conn.execute(
                """
                UPDATE refunds
                SET status='REJECTED', reject_reason=?, approved_by=?, approved_at=CURRENT_TIMESTAMP
                WHERE refund_id=? AND status='PENDING'
                """,
                (reason, approver, refund_id),
            )
            cls._audit(refund_id, "PENDING", "REJECTED", approver, reason, conn=conn)

    @classmethod
    def complete(
        cls,
        refund_id: str,
        payment_method: str,
        reference_number: str = "",
        *,
        operator_id: Optional[str] = None,
    ) -> None:
        """执行退款：APPROVED → COMPLETED，并写入冲账流水。"""
        payment_method = str(payment_method or "").strip() or "CASH_USD"
        reference_number = str(reference_number or "").strip()
        with db.transaction() as conn:
            row = cls._get_refund(refund_id, conn=conn)
            if not row:
                raise RuntimeError("退款单不存在")
            _, room_id, original_tx_id, _, refund_amount, currency, status, _, approved_by = row
            if str(status) != "APPROVED":
                raise RuntimeError(f"当前状态 {status} 不可完成")
            op = str(operator_id or approved_by or "").strip()
            conn.execute(
                """
                UPDATE refunds
                SET status='COMPLETED', completed_at=CURRENT_TIMESTAMP,
                    payment_method=?, reference_number=?
                WHERE refund_id=? AND status='APPROVED'
                """,
                (payment_method, reference_number, refund_id),
            )
            note = f"冲账: {original_tx_id or refund_id}"
            if reference_number:
                note += f" ref={reference_number}"
            db.append_ledger_conn(
                conn,
                "REFUND",
                -float(refund_amount),
                str(currency or "USD"),
                op,
                str(room_id or ""),
                note,
                pay_method=payment_method,
                tx_id_override=f"REFUND_{refund_id}",
            )
            cls._audit(refund_id, "APPROVED", "COMPLETED", op, "完成退款", conn=conn)
