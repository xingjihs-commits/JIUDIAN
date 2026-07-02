"""
Solid PMS — 结账服务（业务编排层）

组合 GuestService + LedgerService + InventoryService，
实现完整结账流程：生成账单 → 房费收款 → 押金结算 → 退房 → 保洁任务。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CheckoutService:
    """结账服务 — 全流程编排。

    Usage:
        svc = CheckoutService(db)
        svc.execute(guest_id=123, payment_method="CASH", damage_charges=0)
    """

    def __init__(self, db: Any):
        self._db = db
        from core.guests import GuestService
        from core.ledger import LedgerService
        from core.inventory import InventoryService
        self._guests = GuestService(db)
        self._ledger = LedgerService(db)
        self._inventory = InventoryService(db)

    def execute(
        self,
        guest_id: int,
        *,
        payment_method: str = "CASH",
        damage_charges: float = 0.0,
        operator_id: Optional[str] = None,
        note: str = "",
    ) -> tuple[bool, str]:
        """原子结账。"""
        from services.trace_context import trace

        guest = self._db.execute(
            "SELECT id, room_id, name, deposit, price, status "
            "FROM guests WHERE id=? AND status='INHOUSE'",
            (guest_id,),
        ).fetchone()
        if not guest:
            return False, "未找到在住客人"

        _id, room_id, name, deposit, price, _status = guest
        deposit_val = float(deposit or 0)
        price_val = float(price or 0)

        # 计算额外消费（folio_items）
        extras = self._db.execute(
            "SELECT COALESCE(SUM(total), 0) FROM folio_items "
            "WHERE room_id=? AND paid=0",
            (room_id,),
        ).fetchone()
        extras_total = float(extras[0]) if extras else 0.0

        # 总应收
        total_due = price_val + extras_total + damage_charges

        # 押金结算
        deposit_used = min(deposit_val, total_due)
        deposit_refund = max(0, deposit_val - total_due)

        with trace("checkout", guest_id=str(guest_id), room_id=room_id):
            try:
                with self._db.transaction() as conn:
                    # 1. 房费收入（从客人应收）
                    if price_val > 0:
                        self._ledger.append_in_transaction(
                            conn=conn,
                            tx_type="ROOM_IN",
                            amount=price_val,
                            currency="USD",
                            room_id=room_id,
                            operator_id=operator_id,
                            note=f"房费 - {name}",
                            pay_method=payment_method,
                        )

                    # 2. 超市消费收入
                    if extras_total > 0:
                        self._ledger.append_in_transaction(
                            conn=conn,
                            tx_type="SHOP",
                            amount=extras_total,
                            currency="USD",
                            room_id=room_id,
                            operator_id=operator_id,
                            note=f"房间消费 - {name}",
                            pay_method=payment_method,
                        )

                    # 3. 损坏赔偿（从押金扣）
                    if damage_charges > 0:
                        self._ledger.append_in_transaction(
                            conn=conn,
                            tx_type="DEPOSIT_OUT",
                            amount=damage_charges,
                            currency="USD",
                            room_id=room_id,
                            operator_id=operator_id,
                            note=f"损坏赔偿 - {name}",
                            is_deposit=1,
                        )

                    # 4. 退还剩余押金
                    if deposit_refund > 0:
                        self._ledger.append_in_transaction(
                            conn=conn,
                            tx_type="DEPOSIT_OUT",
                            amount=deposit_refund,
                            currency="USD",
                            room_id=room_id,
                            operator_id=operator_id,
                            note=f"退还押金 - {name}",
                            is_deposit=1,
                        )

                    # 5. 标记客人退房
                    conn.execute(
                        "UPDATE guests SET status='CHECKED_OUT', "
                        "deposit=0, checkout_time=datetime('now','localtime') "
                        "WHERE id=?",
                        (guest_id,),
                    )

                    # 6. 房间变脏
                    conn.execute(
                        "UPDATE rooms SET status='DIRTY' WHERE room_id=?",
                        (room_id,),
                    )

                    # 7. 标记 folio 已支付
                    conn.execute(
                        "UPDATE folio_items SET paid=1 WHERE room_id=?",
                        (room_id,),
                    )

                    # 8. 创建保洁任务
                    import time as _time, uuid as _uuid
                    task_id = f"HK_{int(_time.time()*1000)}_{_uuid.uuid4().hex[:6]}"
                    conn.execute(
                        "INSERT INTO housekeeping_tasks (task_id, room_id, task_type, source, note) "
                        "VALUES (?,?,'CLEAN','checkout',?)",
                        (task_id, room_id, note or "退房清扫"),
                    )

            except Exception as e:
                logger.exception("结账事务失败")
                return False, f"结账失败: {e}"

        # 事件通知
        try:
            from event_bus import bus
            bus.guest_checkout.emit(room_id, name)
        except Exception:
            pass

        summary = (
            f"客人 {name} 结账完成。"
            f"房费 {price_val:.2f}，消费 {extras_total:.2f}，"
            f"损坏 {damage_charges:.2f}，"
            f"押金抵扣 {deposit_used:.2f}，退押金 {deposit_refund:.2f}"
        )
        return True, summary

    def calculate_folio(self, room_id: str) -> dict:
        """计算房间账单明细。"""
        guest = self._db.execute(
            "SELECT name, price, deposit, checkin_time FROM guests "
            "WHERE room_id=? AND status='INHOUSE' LIMIT 1",
            (room_id,),
        ).fetchone()

        items = self._db.execute(
            "SELECT sku, description, qty, unit_price, total, created_at "
            "FROM folio_items WHERE room_id=? AND paid=0 "
            "ORDER BY created_at",
            (room_id,),
        ).fetchall()

        folio = []
        total_extras = 0.0
        for sku, desc, qty, uprice, total, ts in items:
            folio.append({
                "sku": sku or "",
                "description": desc or "消费",
                "qty": float(qty or 1),
                "unit_price": float(uprice or 0),
                "total": float(total or 0),
                "time": str(ts or ""),
            })
            total_extras += float(total or 0)

        return {
            "guest_name": guest[0] if guest else "",
            "room_rate": float(guest[1]) if guest else 0.0,
            "deposit": float(guest[2]) if guest else 0.0,
            "checkin_time": str(guest[3]) if guest else "",
            "folio_items": folio,
            "total_extras": total_extras,
            "grand_total": (float(guest[1]) if guest else 0) + total_extras,
        }

    def quick_checkout(
        self,
        room_id: str,
        *,
        operator_id: str = "",
        target_room_status: str = "DIRTY",
    ) -> tuple[bool, str, dict]:
        """一键快速退房 — 结算押金、注销所有卡、标记脏房、创建保洁任务。

        Returns:
            (success, message, detail_dict)
            detail_dict keys: guest_name, deposit_returned, charge_total, refund, active_cards_erased
        """
        from lock_legacy_bridge import LEGACY_ACTIVE_CARD_STATUSES, CARD_STATUS_ERASED

        detail = {
            "guest_name": "",
            "deposit_returned": 0.0,
            "charge_total": 0.0,
            "refund": 0.0,
            "active_cards_erased": 0,
        }

        room = self._db.execute(
            "SELECT status FROM rooms WHERE room_id=?", (room_id,)
        ).fetchone()
        if not room or room[0] != "INHOUSE":
            return False, "房间不是在住状态", detail

        try:
            with self._db.transaction() as conn:
                guest_row = conn.execute(
                    "SELECT id, name FROM guests WHERE room_id=? AND status='INHOUSE' "
                    "ORDER BY id DESC LIMIT 1",
                    (room_id,),
                ).fetchone()
                if not guest_row:
                    return False, "未找到在住客人", detail
                guest_id, guest_name = guest_row
                detail["guest_name"] = guest_name or ""

                # 计算押金净额
                dep_net = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM ledger "
                    "WHERE room_id=? AND is_deposit=1 AND tx_type IN ('DEPOSIT_IN','DEPOSIT_OUT')",
                    (room_id,),
                ).fetchone()[0]
                dep_net = float(dep_net or 0)

                # 计算消费净额
                charge_net = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM ledger "
                    "WHERE room_id=? AND is_deposit=0 AND tx_type IN ('ROOM_IN','SHOP','TIP','OTHER')",
                    (room_id,),
                ).fetchone()[0]
                charge_net = float(charge_net or 0)

                detail["deposit_returned"] = dep_net
                detail["charge_total"] = charge_net
                detail["refund"] = dep_net - charge_net

                # 退还押金
                if dep_net > 0:
                    self._ledger.append_in_transaction(
                        conn=conn, tx_type="DEPOSIT_OUT",
                        amount=-dep_net, currency="USD", room_id=room_id,
                        operator_id=operator_id, note="退房押金退还",
                        is_deposit=1,
                    )

                # 注销所有活跃卡
                from lock_legacy_bridge import LEGACY_ACTIVE_CARD_STATUSES, CARD_STATUS_ERASED
                placeholders = ",".join(["?"] * len(LEGACY_ACTIVE_CARD_STATUSES))
                active_cards = conn.execute(
                    f"SELECT card_id FROM card_records WHERE room_id=? "
                    f"AND status IN ({placeholders})",
                    (room_id, *LEGACY_ACTIVE_CARD_STATUSES),
                ).fetchall()
                for (cid,) in active_cards:
                    conn.execute(
                        "UPDATE card_records SET status=? WHERE card_id=?",
                        (CARD_STATUS_ERASED, cid),
                    )
                detail["active_cards_erased"] = len(active_cards)

                # 标记房间 + 客人
                conn.execute(
                    "UPDATE rooms SET status=? WHERE room_id=?",
                    (target_room_status, room_id),
                )
                conn.execute(
                    "UPDATE guests SET status='OUT', checkout_time=CURRENT_TIMESTAMP WHERE id=?",
                    (guest_id,),
                )

                # 审计日志
                self._db.log_action(
                    operator_id or "system", "CHECKOUT",
                    f"room={room_id} guest={guest_name} mode=quick",
                )

            # 保洁任务（在事务外创建，不影响退房事务）
            self._db.create_housekeeping_task(
                room_id, "CHECKOUT_CLEAN",
                source="checkout", note="退房后保洁（一键退房）",
            )

            return True, f"{room_id} 一键退房完成", detail

        except Exception as e:
            logger.exception("快速退房事务失败")
            return False, f"退房失败: {e}", detail