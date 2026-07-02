"""账单明细写入 — Phase2 #6"""
from __future__ import annotations

from database import db


def add_bill_detail(
    payment_tx_id: str,
    item_type: str,
    description: str,
    quantity: float,
    unit_price: float,
    *,
    conn=None,
) -> None:
    total = round(float(quantity) * float(unit_price), 2)
    sql = (
        "INSERT INTO bill_details(payment_tx_id, item_type, description, quantity, unit_price, total) "
        "VALUES (?,?,?,?,?,?)"
    )
    params = (payment_tx_id, item_type, description, quantity, unit_price, total)
    if conn is not None:
        conn.execute(sql, params)
    else:
        db.execute(sql, params)


def list_bill_details(payment_tx_id: str) -> list[dict]:
    rows = db.execute(
        "SELECT item_type, description, quantity, unit_price, total FROM bill_details "
        "WHERE payment_tx_id=? ORDER BY id",
        (payment_tx_id,),
    ).fetchall()
    return [
        {
            "item_type": r[0], "description": r[1],
            "quantity": r[2], "unit_price": r[3], "total": r[4],
        }
        for r in rows
    ]
