"""
Solid PMS — 定价服务

房费计算、节假日定价、会员折扣、团价等。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PricingService:
    """定价服务 — 价格计算引擎。"""

    def __init__(self, db: Any):
        self._db = db

    def get_room_rate(
        self, room_id: str, checkin_date: str = "", nights: int = 1, tier: str = "standard"
    ) -> float:
        """获取房间每晚价格。

        Args:
            room_id: 房间 ID
            checkin_date: 入住日期 (YYYY-MM-DD)，默认今天
            nights: 入住天数
            tier: 价格档位 (standard/walkin/contract/member)

        Returns:
            每晚安价格
        """
        room = self._db.execute(
            "SELECT type_id, rate_override FROM rooms WHERE room_id=?",
            (room_id,),
        ).fetchone()
        if not room:
            return 0.0

        type_id, rate_override = room
        if rate_override is not None and float(rate_override) > 0:
            return float(rate_override)

        base = self._db.get_rate_for_room_type(type_id, tier)
        if base <= 0:
            return 0.0

        # 检查节假日定价
        if checkin_date:
            try:
                d = date.fromisoformat(checkin_date[:10])
                holiday_rate = self._get_holiday_rate(type_id, d)
                if holiday_rate is not None:
                    return float(holiday_rate)
            except (ValueError, TypeError):
                pass

        return base

    def get_total(
        self,
        room_id: str,
        checkin_date: str = "",
        nights: int = 1,
        tier: str = "standard",
        member_discount: float = 1.0,
    ) -> float:
        """计算总房费。"""
        rate_per_night = self.get_room_rate(room_id, checkin_date, nights, tier)
        return rate_per_night * max(1, nights) * member_discount

    def get_deposit_default(self, room_id: str) -> float:
        """获取房间默认押金。"""
        room = self._db.execute(
            "SELECT type_id FROM rooms WHERE room_id=?", (room_id,)
        ).fetchone()
        if not room:
            return float(self._db.get_config_float("default_deposit", 50.0))
        return self._db.get_deposit_for_room_type(room[0])

    def calculate_member_discount(self, member_id: int) -> float:
        """计算会员折扣倍数（1.0 = 全价）。"""
        member = self._db.execute(
            "SELECT level, points FROM members WHERE id=?",
            (member_id,),
        ).fetchone()
        if not member:
            return 1.0
        level = (member[0] or "BRONZE").upper()
        return self._db.get_level_discount(level)

    def calculate_birthday_discount(self, member_id: int) -> dict:
        """检查会员生日折扣。"""
        row = self._db.execute(
            "SELECT name, birthday FROM members WHERE id=?", (member_id,)
        ).fetchone()
        if not row or not row[1]:
            return {"is_birthday": False, "discount": 1.0, "name": ""}
        try:
            bday = date.fromisoformat(str(row[1]).strip()[:10])
            today = date.today()
            if bday.month == today.month and bday.day == today.day:
                return {"is_birthday": True, "discount": 0.9, "name": str(row[0] or "")}
        except (ValueError, TypeError):
            pass
        return {"is_birthday": False, "discount": 1.0, "name": str(row[0] or "")}

    def _get_holiday_rate(self, type_id: str, checkin: date) -> Optional[float]:
        """查询节假日定价。"""
        rows = self._db.execute(
            "SELECT start_date, end_date, multiplier FROM holiday_pricing "
            "WHERE type_id=? OR type_id='' OR type_id IS NULL",
            (type_id,),
        ).fetchall()
        for start_str, end_str, multiplier in rows:
            try:
                start = date.fromisoformat(str(start_str)[:10])
                end = date.fromisoformat(str(end_str)[:10])
                if start <= checkin <= end:
                    base = self._db.get_rate_for_room_type(type_id, "standard")
                    return base * float(multiplier or 1.0)
            except (ValueError, TypeError):
                continue
        return None

    def get_all_room_rates(self, tier: str = "standard") -> list:
        """获取所有房间的当前价格。"""
        rooms = self._db.execute(
            "SELECT room_id, type_id, rate_override FROM rooms ORDER BY room_id"
        ).fetchall()
        result = []
        for room_id, type_id, rate_override in rooms:
            rate = float(rate_override) if rate_override else self._db.get_rate_for_room_type(type_id, tier)
            result.append({"room_id": room_id, "type_id": type_id or "", "rate": rate})
        return result