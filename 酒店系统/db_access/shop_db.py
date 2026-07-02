"""
db_access/shop_db.py — 超市/库存数据库操作

从 database.py 拆出:
- record_shop_purchase  (95行)
- adjust_shop_stock
- reserve_shop_stock
- update_shop_item_icon
"""

from __future__ import annotations
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def record_shop_purchase(
    db: Any,
    room_id: str,
    cart_items: list[dict],
    payment_method: str = "room_charge",
    operator_id: str = "",
    exchange_rate: float = 1.0,
    note: str = "",
) -> tuple[bool, str, str]:
    """记录超市购买。返回 (ok, msg, order_id)。"""
    if not cart_items:
        return False, "购物车为空", ""
    order_id = f"SHOP_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
    total = sum(float(item.get("sell_price", 0)) * float(item.get("qty", 1))
                for item in cart_items)
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO shop_purchases (order_id, room_id, total, pay_method, "
                "operator_id, note, created_at) VALUES (?,?,?,?,?,?,datetime('now','localtime'))",
                (order_id, room_id, total, payment_method, operator_id, note),
            )
            for item in cart_items:
                sku = item.get("sku", "")
                qty = float(item.get("qty", 1))
                price = float(item.get("sell_price", 0))
                line_total = qty * price
                conn.execute(
                    "INSERT INTO folio_items (room_id, sku, description, qty, "
                    "unit_price, total, created_at) VALUES (?,?,?,?,?,?,datetime('now','localtime'))",
                    (room_id, sku, item.get("name", sku), qty, price, line_total),
                )
                # 自动扣库存
                conn.execute(
                    "INSERT INTO inventory_movements (move_id, item_id, move_type, "
                    "qty_change, unit_cost, related_room, operator_id, created_at) "
                    "VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))",
                    (f"OUT_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}",
                     sku, "SALES", -qty, float(item.get("cost_price", 0)),
                     room_id, operator_id),
                )
        return True, f"订单 {order_id} 已记录", order_id
    except Exception as e:
        logger.exception("超市购买记录失败")
        return False, str(e), ""


def adjust_shop_stock(db: Any, sku: str, delta: int) -> None:
    """调整超市库存（手动加减）。"""
    db.execute(
        "UPDATE shop_items SET stock=MAX(0, COALESCE(stock,0)+?) WHERE sku=?",
        (delta, sku),
    )


def reserve_shop_stock(db: Any, sku: str, qty: int = 1) -> bool:
    """预留库存。返回是否预留成功。"""
    row = db.execute(
        "SELECT stock FROM shop_items WHERE sku=?", (sku,)
    ).fetchone()
    if not row or (row[0] or 0) < qty:
        return False
    db.execute(
        "UPDATE shop_items SET stock=stock-? WHERE sku=? AND stock>=?",
        (qty, sku, qty),
    )
    return True


def update_shop_item_icon(
    db: Any,
    sku: str,
    emoji: str = "",
    custom_emoji_file_id: str = "",
    icon_key: str = "",
    description: str = "",
) -> None:
    """更新超市商品图标/表情。"""
    sets = []
    params = []
    if emoji:
        sets.append("emoji=?"); params.append(emoji)
    if custom_emoji_file_id:
        sets.append("telegram_file_id=?"); params.append(custom_emoji_file_id)
    if icon_key:
        sets.append("icon_key=?"); params.append(icon_key)
    if description:
        sets.append("description=?"); params.append(description)
    if sets:
        params.append(sku)
        db.execute(f"UPDATE shop_items SET {', '.join(sets)} WHERE sku=?", tuple(params))
