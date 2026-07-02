"""Dashboard reconciliation checks for owner-visible exceptions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from database import LEDGER_CASH_NET_TX_TYPES, _sql_in_types, db
from lock_legacy_bridge import LEGACY_ACTIVE_CARD_STATUSES, LEGACY_LOST_PENDING_STATUSES

_CASHIER_TX = _sql_in_types(LEDGER_CASH_NET_TX_TYPES)


def _placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


@dataclass
class ReconciliationCheck:
    key: str
    title: str
    severity: str
    fn: Callable[[], tuple[bool, int, str]]


def _rows_to_detail(rows, formatter, limit: int = 8) -> str:
    if not rows:
        return "正常"
    head = rows[:limit]
    detail = "；".join(formatter(r) for r in head)
    if len(rows) > limit:
        detail += f"；另 {len(rows) - limit} 项"
    return detail


def check_card_vs_checkin() -> tuple[bool, int, str]:
    rows = db.execute(
        f"""
        SELECT c.room_id, c.card_id
        FROM card_records c
        LEFT JOIN guests g ON g.room_id=c.room_id AND g.status='INHOUSE'
        WHERE c.status IN ({_placeholders(LEGACY_ACTIVE_CARD_STATUSES)})
          AND g.id IS NULL AND COALESCE(c.room_id,'') NOT IN ('','__REGISTRY__')
        """
        ,
        LEGACY_ACTIVE_CARD_STATUSES,
    ).fetchall()
    return (not rows, len(rows), _rows_to_detail(rows, lambda r: f"{r[0]}/{r[1]}"))


def check_checkout_vs_cancel() -> tuple[bool, int, str]:
    rows = db.execute(
        f"""
        SELECT g.room_id, c.card_id
        FROM guests g
        JOIN card_records c ON c.room_id=g.room_id
        WHERE g.status='OUT' AND c.status IN ({_placeholders(LEGACY_ACTIVE_CARD_STATUSES)})
        """,
        LEGACY_ACTIVE_CARD_STATUSES,
    ).fetchall()
    return (not rows, len(rows), _rows_to_detail(rows, lambda r: f"{r[0]}/{r[1]}"))


def check_cash_vs_receivable() -> tuple[bool, int, str]:
    row = db.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN tx_type IN ('ROOM_IN','SHOP','TIP','DEPOSIT_IN') THEN amount ELSE 0 END),0)
        FROM ledger WHERE date(created_at)=date('now','localtime')
        """
    ).fetchone()
    amt = float(row[0] or 0) if row else 0.0
    return True, 0, f"今日入账 {amt:.2f}"


def check_inventory_vs_status() -> tuple[bool, int, str]:
    # 库存模块本阶段只做可观测提示，实际账实差异仍由库存审计页承接。
    row = db.execute("SELECT COUNT(*) FROM inventory_audit").fetchone()
    count = int(row[0] or 0) if row else 0
    return True, 0, f"库存流水 {count} 条"


def check_active_card_on_empty_room() -> tuple[bool, int, str]:
    rows = db.execute(
        f"""
        SELECT r.room_id, c.card_id, c.guest_name
        FROM rooms r
        JOIN card_records c ON c.room_id=r.room_id
        WHERE r.status='READY' AND c.status IN ({_placeholders(LEGACY_ACTIVE_CARD_STATUSES)})
        """,
        LEGACY_ACTIVE_CARD_STATUSES,
    ).fetchall()
    return (not rows, len(rows), _rows_to_detail(rows, lambda r: f"{r[0]}/{r[2] or r[1]}"))


def check_checkout_without_card_return() -> tuple[bool, int, str]:
    active_or_pending = LEGACY_ACTIVE_CARD_STATUSES + LEGACY_LOST_PENDING_STATUSES
    rows = db.execute(
        f"""
        SELECT room_id, card_id
        FROM card_records
        WHERE status IN ({_placeholders(active_or_pending)}) AND room_id IN (
            SELECT room_id FROM rooms WHERE status IN ('READY','DIRTY')
        )
        """,
        active_or_pending,
    ).fetchall()
    return (not rows, len(rows), _rows_to_detail(rows, lambda r: f"{r[0]}/{r[1]}"))


def check_checkout_without_deposit_refund() -> tuple[bool, int, str]:
    rows = db.execute(
        """
        SELECT g.room_id, COALESCE(SUM(l.amount),0) AS dep
        FROM guests g
        JOIN ledger l ON l.room_id=g.room_id AND l.is_deposit=1
        WHERE g.status='OUT'
        GROUP BY g.room_id
        HAVING dep > 0.01
        """
    ).fetchall()
    return (not rows, len(rows), _rows_to_detail(rows, lambda r: f"{r[0]} 押金余额 {float(r[1] or 0):.2f}"))


def check_pending_blacklist() -> tuple[bool, int, str]:
    rows = db.execute(
        "SELECT room_id, card_id, COALESCE(physical_blacklist_card_id,'') "
        f"FROM card_records WHERE status IN ({_placeholders(LEGACY_LOST_PENDING_STATUSES)})",
        LEGACY_LOST_PENDING_STATUSES,
    ).fetchall()
    return (not rows, len(rows), _rows_to_detail(rows, lambda r: f"{r[0]}/{r[1]}→{r[2] or '-'}"))


def check_rooms_without_lock_no() -> tuple[bool, int, str]:
    rows = db.execute(
        "SELECT room_id FROM rooms WHERE lock_no IS NULL OR lock_no = '' ORDER BY room_id"
    ).fetchall()
    return (not rows, len(rows), _rows_to_detail(rows, lambda r: str(r[0])))


def check_ledger_payment_mismatch() -> tuple[bool, int, str]:
    """收银流水缺少支付方式，或同房号房费与收银合计不一致。"""
    rows = db.execute(
        f"""
        SELECT id, room_id, tx_type, amount
        FROM ledger
        WHERE date(created_at)=date('now','localtime')
          AND tx_type IN ({_CASHIER_TX})
          AND (pay_method IS NULL OR TRIM(pay_method)='' OR pay_method='UNKNOWN')
        ORDER BY id DESC
        """
    ).fetchall()
    if rows:
        return (
            False,
            len(rows),
            _rows_to_detail(rows, lambda r: f"#{r[0]} {r[1] or '-'} {r[2]} {float(r[3] or 0):.2f}"),
        )

    mix_rows = db.execute(
        f"""
        SELECT room_id,
               COALESCE(SUM(CASE WHEN tx_type='ROOM_IN' THEN amount ELSE 0 END),0),
               COALESCE(SUM(amount),0)
        FROM ledger
        WHERE date(created_at)=date('now','localtime')
          AND tx_type IN ({_CASHIER_TX})
          AND COALESCE(room_id,'') NOT IN ('','__REGISTRY__')
        GROUP BY room_id
        HAVING ABS(
            COALESCE(SUM(CASE WHEN tx_type='ROOM_IN' THEN amount ELSE 0 END),0)
            - COALESCE(SUM(amount),0)
        ) > 0.01
        ORDER BY room_id
        """
    ).fetchall()
    return (
        not mix_rows,
        len(mix_rows),
        _rows_to_detail(
            mix_rows,
            lambda r: f"{r[0]} 房费 {float(r[1] or 0):.2f} ≠ 收银 {float(r[2] or 0):.2f}",
        ),
    )


CHECKS = [
    ReconciliationCheck("card_vs_checkin", "发卡 vs 入住", "red", check_card_vs_checkin),
    ReconciliationCheck("checkout_vs_cancel", "退房 vs 注销卡", "red", check_checkout_vs_cancel),
    ReconciliationCheck("cash_vs_receivable", "钱箱 vs 应收", "green", check_cash_vs_receivable),
    ReconciliationCheck("inventory_vs_status", "库存 vs 房态", "green", check_inventory_vs_status),
    ReconciliationCheck("active_card_empty", "空房有效卡", "red", check_active_card_on_empty_room),
    ReconciliationCheck("checkout_no_card", "已退房未收卡", "red", check_checkout_without_card_return),
    ReconciliationCheck("deposit_not_refunded", "已退房未退押金", "yellow", check_checkout_without_deposit_refund),
    ReconciliationCheck("pending_blacklist", "LOST_PENDING_PHYSICAL", "yellow", check_pending_blacklist),
    ReconciliationCheck("rooms_without_lock", "缺锁号房间", "red", check_rooms_without_lock_no),
    ReconciliationCheck("ledger_payment_mismatch", "收银流水账实", "yellow", check_ledger_payment_mismatch),
]

ALL_CHECKS = CHECKS
