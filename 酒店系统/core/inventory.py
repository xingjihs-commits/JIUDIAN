"""
Solid PMS — 库存服务

库存管理、进出库、盘点、自动扣减（超市收银联动）。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from core.exceptions import InsufficientStockError, ValidationError

logger = logging.getLogger(__name__)


class InventoryService:
    """库存服务 — 库存 CRUD + 自动扣减。"""

    def __init__(self, db: Any):
        self._db = db

    def get_stock(self, item_id: str) -> float:
        """获取当前库存量。"""
        row = self._db.execute(
            "SELECT COALESCE(SUM(qty_change), 0) FROM inventory_movements WHERE item_id=?",
            (item_id,),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def check_availability(self, item_id: str, required_qty: float) -> bool:
        """检查库存是否充足。"""
        return self.get_stock(item_id) >= required_qty

    def deduct(
        self,
        item_id: str,
        qty: float,
        *,
        move_type: str = "SALES",
        related_room: str = "",
        unit_cost: Optional[float] = None,
        operator_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """库存扣减（如超市销售）。

        Returns:
            (success, message)
        """
        if qty <= 0:
            return False, "扣减数量必须大于 0"

        current = self.get_stock(item_id)
        if current < qty:
            return False, (
                f"库存不足：{item_id} 当前 {current}，需要 {qty}"
            )

        cost = unit_cost if unit_cost is not None else self._get_unit_cost(item_id)
        move_id = f"OUT_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        try:
            self._db.execute(
                "INSERT INTO inventory_movements "
                "(move_id, item_id, move_type, qty_change, unit_cost, "
                "related_room, operator_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))",
                (move_id, item_id, move_type, -qty, cost,
                 related_room or "", operator_id or ""),
            )
            return True, f"{item_id} 已扣减 {qty}"
        except Exception as e:
            logger.exception("库存扣减失败")
            return False, str(e)

    def add_stock(
        self,
        item_id: str,
        qty: float,
        *,
        move_type: str = "PURCHASE",
        unit_cost: float = 0.0,
        operator_id: Optional[str] = None,
        note: str = "",
    ) -> tuple[bool, str]:
        """库存入库。"""
        if qty <= 0:
            return False, "入库数量必须大于 0"
        move_id = f"IN_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        try:
            self._db.execute(
                "INSERT INTO inventory_movements "
                "(move_id, item_id, move_type, qty_change, unit_cost, "
                "operator_id, note, created_at) "
                "VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))",
                (move_id, item_id, move_type, qty, unit_cost,
                 operator_id or "", note or ""),
            )
            return True, f"{item_id} 已入库 {qty}"
        except Exception as e:
            logger.exception("入库失败")
            return False, str(e)

    def get_movements(
        self, item_id: Optional[str] = None, since: str = "", limit: int = 200
    ) -> list:
        """查询库存流水。"""
        conditions = []
        params = []
        if item_id:
            conditions.append("item_id=?")
            params.append(item_id)
        if since:
            conditions.append("created_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return self._db.execute(
            f"SELECT * FROM inventory_movements {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params + [limit]),
        ).fetchall()

    def get_low_stock_items(self, threshold: float = 10.0) -> list:
        """获取低库存商品列表。"""
        items = self._db.execute(
            "SELECT item_id, name FROM inventory_items WHERE listed=1"
        ).fetchall()
        low = []
        for item_id, name in items:
            stock = self.get_stock(item_id)
            if stock <= threshold:
                low.append({"item_id": item_id, "name": name, "stock": stock})
        return low

    def _get_unit_cost(self, item_id: str) -> float:
        """获取商品最近成本价。"""
        row = self._db.execute(
            "SELECT unit_cost FROM inventory_movements WHERE item_id=? "
            "AND qty_change > 0 ORDER BY created_at DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        if row and row[0]:
            return float(row[0])
        # fallback: shop_items.cost_price
        row2 = self._db.execute(
            "SELECT cost_price FROM shop_items WHERE sku=? LIMIT 1",
            (item_id,),
        ).fetchone()
        return float(row2[0]) if row2 and row2[0] else 0.0

    def get_inventory_summary(self) -> list:
        """库存汇总（按 item 分组）。"""
        return self._db.execute(
            "SELECT i.item_id, i.name, COALESCE(SUM(m.qty_change),0) as stock, "
            "i.unit, i.cost_price, i.sell_price "
            "FROM inventory_items i "
            "LEFT JOIN inventory_movements m ON i.item_id=m.item_id "
            "WHERE i.listed=1 "
            "GROUP BY i.item_id ORDER BY stock ASC"
        ).fetchall()