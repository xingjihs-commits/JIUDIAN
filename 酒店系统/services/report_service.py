"""
Solid PMS — 报表服务（业务编排层）

日结报表、交班汇总、收入分析、房态统计等。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ReportService:
    """报表服务 — 多维度数据汇总。"""

    def __init__(self, db: Any):
        self._db = db

    def daily_revenue(self, target_date: str = "") -> dict:
        """今日/指定日期收入报告。"""
        d = target_date or date.today().isoformat()
        sql = """
            SELECT tx_type, COALESCE(SUM(amount),0) AS total, COUNT(*) AS cnt
            FROM ledger
            WHERE date(created_at)=?
            GROUP BY tx_type
            ORDER BY total DESC
        """
        rows = self._db.execute(sql, (d,)).fetchall()
        by_type = {r[0]: {"amount": float(r[1]), "count": int(r[2])} for r in rows}

        total_revenue = sum(
            v["amount"] for k, v in by_type.items()
            if k in ("ROOM_IN", "SHOP", "TIP")
        )
        total_deposits = sum(
            v["amount"] for k, v in by_type.items()
            if k in ("DEPOSIT_IN",)
        )

        return {
            "date": d,
            "by_type": by_type,
            "total_revenue": total_revenue,
            "total_deposits": total_deposits,
        }

    def occupancy_report(self) -> dict:
        """房态统计报告。"""
        rows = self._db.execute(
            "SELECT status, COUNT(*) FROM rooms GROUP BY status"
        ).fetchall()
        statuses = {r[0]: r[1] for r in rows}
        total = sum(statuses.values())

        return {
            "total_rooms": total,
            "inhouse": statuses.get("INHOUSE", 0),
            "ready": statuses.get("READY", 0),
            "dirty": statuses.get("DIRTY", 0),
            "maintenance": statuses.get("MAINTENANCE", 0),
            "occupancy_rate": (
                statuses.get("INHOUSE", 0) / total * 100 if total > 0 else 0
            ),
        }

    def shift_summary(self, since: str = "") -> dict:
        """交班汇总。"""
        return self._db.build_cashier_shift_summary(since)

    def monthly_summary(self, year: int = 0, month: int = 0) -> dict:
        """月度收入汇总。"""
        now = datetime.now()
        y = year or now.year
        m = month or now.month
        prefix = f"{y}-{m:02d}"

        sql = """
            SELECT tx_type, COALESCE(SUM(amount),0), COUNT(*)
            FROM ledger
            WHERE created_at LIKE ?
            GROUP BY tx_type
        """
        rows = self._db.execute(sql, (f"{prefix}%",)).fetchall()
        by_type = {r[0]: {"amount": float(r[1]), "count": int(r[2])} for r in rows}

        total = sum(v["amount"] for v in by_type.values())
        revenue = sum(
            v["amount"] for k, v in by_type.items()
            if k in ("ROOM_IN", "SHOP", "TIP")
        )

        return {
            "period": prefix,
            "by_type": by_type,
            "total_transactions": sum(v["count"] for v in by_type.values()),
            "total_amount": total,
            "total_revenue": revenue,
        }

    def guest_statistics(self) -> dict:
        """客人统计。"""
        inhouse = self._db.execute(
            "SELECT COUNT(*) FROM guests WHERE status='INHOUSE'"
        ).fetchone()[0]
        checked_out_today = self._db.execute(
            "SELECT COUNT(*) FROM guests WHERE status='CHECKED_OUT' "
            "AND date(checkout_time)=date('now','localtime')"
        ).fetchone()[0]
        checkins_today = self._db.execute(
            "SELECT COUNT(*) FROM guests "
            "WHERE date(checkin_time)=date('now','localtime')"
        ).fetchone()[0]

        return {
            "inhouse": inhouse,
            "checkins_today": checkins_today,
            "checkouts_today": checked_out_today,
        }

    def inventory_value_report(self) -> dict:
        """库存价值报告。"""
        items = self._db.execute(
            "SELECT i.item_id, i.name, COALESCE(SUM(m.qty_change),0) AS stock, "
            "i.cost_price, i.sell_price "
            "FROM inventory_items i "
            "LEFT JOIN inventory_movements m ON i.item_id=m.item_id "
            "WHERE i.listed=1 GROUP BY i.item_id"
        ).fetchall()

        total_cost = 0.0
        total_retail = 0.0
        breakdown = []
        for item_id, name, stock, cost, sell in items:
            stock_qty = float(stock or 0)
            item_cost = stock_qty * float(cost or 0)
            item_retail = stock_qty * float(sell or 0)
            total_cost += item_cost
            total_retail += item_retail
            if stock_qty > 0:
                breakdown.append({
                    "item_id": item_id,
                    "name": name,
                    "stock": stock_qty,
                    "cost_price": float(cost or 0),
                    "sell_price": float(sell or 0),
                    "total_cost": item_cost,
                    "total_retail": item_retail,
                })

        return {
            "total_cost": total_cost,
            "total_retail": total_retail,
            "gross_margin": total_retail - total_cost,
            "items": sorted(breakdown, key=lambda x: x["total_cost"], reverse=True),
        }

    def integrity_check(self) -> dict:
        """数据完整性检查。"""
        checks = []

        # 哈希链
        from core.ledger import LedgerHashChain
        ok, msg = LedgerHashChain.verify_chain(self._db)
        checks.append({"check": "账本哈希链", "ok": ok, "detail": msg})

        # 房间-客人一致性
        orphan_rooms = self._db.execute(
            "SELECT COUNT(*) FROM rooms r WHERE r.status='INHOUSE' "
            "AND NOT EXISTS (SELECT 1 FROM guests g WHERE g.room_id=r.room_id AND g.status='INHOUSE')"
        ).fetchone()[0]
        checks.append({
            "check": "房间-客人一致性",
            "ok": orphan_rooms == 0,
            "detail": f"孤儿房间: {orphan_rooms}" if orphan_rooms else "正常",
        })

        # 空房状态
        dirty_count = self._db.execute(
            "SELECT COUNT(*) FROM rooms WHERE status NOT IN ('READY','INHOUSE','MAINTENANCE','DIRTY')"
        ).fetchone()[0]
        checks.append({
            "check": "合法房间状态",
            "ok": dirty_count == 0,
            "detail": f"非法状态: {dirty_count}" if dirty_count else "正常",
        })

        return {
            "timestamp": datetime.now().isoformat(),
            "checks": checks,
            "all_ok": all(c["ok"] for c in checks),
        }