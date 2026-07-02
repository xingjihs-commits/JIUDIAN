"""
Solid PMS — 客人服务

管理 guests 表的 CRUD、会员关联、入住/退房状态变更。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from core.exceptions import BusinessRuleError, ValidationError

logger = logging.getLogger(__name__)


class GuestService:
    """客人服务 — 统一客人/会员操作入口。"""

    def __init__(self, db: Any):
        self._db = db

    def checkin(
        self,
        room_id: str,
        guest_name: str,
        *,
        phone: str = "",
        id_type: str = "",
        id_no: str = "",
        sex: str = "",
        flag: str = "WalkIn",
        price: float = 0.0,
        deposit: float = 0.0,
        note: str = "",
        member_id: Optional[int] = None,
        operator_id: Optional[str] = None,
    ) -> tuple[bool, str, Optional[int]]:
        """客人入住。返回 (success, message, guest_db_id)。"""
        from services.trace_context import trace

        room = self._db.execute(
            "SELECT room_id, status FROM rooms WHERE room_id=?", (room_id,)
        ).fetchone()
        if not room:
            return False, f"房间 {room_id} 不存在", None
        if room[1] in ("INHOUSE", "OUTOFORDER"):
            return False, f"房间 {room_id} 当前状态 {room[1]}", None

        name = (guest_name or "").strip()
        if not name:
            return False, "客人姓名不能为空", None

        with trace("checkin", room_id=room_id, guest=name):
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO guests (room_id, name, phone, c_type, c_no, sex, "
                    "flag, price, deposit, note, status, checkin_time) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))",
                    (room_id, name, phone or "", id_type or "", id_no or "",
                     sex or "", flag or "WalkIn", float(price or 0),
                     float(deposit or 0), note or "", "INHOUSE"),
                )
                guest_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute("UPDATE rooms SET status='INHOUSE' WHERE room_id=?", (room_id,))
                if member_id:
                    conn.execute(
                        "UPDATE guests SET member_id=? WHERE id=?", (member_id, guest_id)
                    )

        try:
            from event_bus import bus
            bus.guest_checkin.emit(room_id, guest_name)
        except Exception:
            logger.debug("guest_checkin event emit failed", exc_info=True)

        return True, f"客人 {name} 入住 {room_id} 成功", guest_id

    def checkout(
        self,
        guest_id: int,
        *,
        operator_id: Optional[str] = None,
        damage_charges: float = 0.0,
        note: str = "",
    ) -> tuple[bool, str]:
        """客人退房。返回 (success, message)。"""
        from services.trace_context import trace

        guest = self._db.execute(
            "SELECT id, room_id, name, deposit FROM guests WHERE id=? AND status='INHOUSE'",
            (guest_id,),
        ).fetchone()
        if not guest:
            return False, "未找到该在住客人"

        _id, room_id, name, deposit = guest
        deposit_val = float(deposit or 0)

        with trace("checkout", guest_id=str(guest_id), room_id=room_id):
            with self._db.transaction() as conn:
                conn.execute(
                    "UPDATE guests SET status='CHECKED_OUT', checkout_time=datetime('now','localtime'), "
                    "note=COALESCE(NULLIF(?,''),note) WHERE id=?",
                    (note, guest_id),
                )
                conn.execute(
                    "UPDATE rooms SET status='DIRTY' WHERE room_id=? AND status='INHOUSE'",
                    (room_id,),
                )
                task_id = f"HK_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
                conn.execute(
                    "INSERT INTO housekeeping_tasks (task_id, room_id, task_type, source, note) "
                    "VALUES (?,?,'CLEAN','checkout',?)",
                    (task_id, room_id, note or "退房清扫"),
                )

        try:
            from event_bus import bus
            bus.guest_checkout.emit(room_id, name)
        except Exception:
            logger.debug("guest_checkout event emit failed", exc_info=True)

        return True, f"客人 {name} 已退房，房间 {room_id} 待清扫"

    def get_inhouse_guests(self) -> list:
        """获取所有在住客人列表。"""
        return self._db.execute(
            "SELECT g.*, r.building, r.room_number "
            "FROM guests g LEFT JOIN rooms r ON g.room_id=r.room_id "
            "WHERE g.status='INHOUSE' ORDER BY g.checkin_time DESC"
        ).fetchall()

    def get_guest_by_room(self, room_id: str) -> Optional[dict]:
        """根据房间号获取在住客人。"""
        row = self._db.execute(
            "SELECT * FROM guests WHERE room_id=? AND status='INHOUSE' LIMIT 1",
            (room_id,),
        ).fetchone()
        if row is None:
            return None
        # 兼容 sqlite3.Row 和普通 tuple（使用 cursor.description 获取列名）
        cur = self._db.execute(
            "SELECT * FROM guests WHERE room_id=? AND status='INHOUSE' LIMIT 1",
            (room_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def get_guest_history(self, guest_name: str = "", phone: str = "", limit: int = 50) -> list:
        """查询客人历史记录。"""
        conditions = ["status='CHECKED_OUT'"]
        params = []
        if guest_name:
            conditions.append("name LIKE ?")
            params.append(f"%{guest_name}%")
        if phone:
            conditions.append("phone LIKE ?")
            params.append(f"%{phone}%")
        where = " AND ".join(conditions)
        return self._db.execute(
            f"SELECT * FROM guests WHERE {where} ORDER BY checkout_time DESC LIMIT ?",
            tuple(params + [limit]),
        ).fetchall()

    def add_deposit(
        self, guest_id: int, amount: float, operator_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """追加押金。"""
        if amount <= 0:
            return False, "押金金额必须大于 0"
        guest = self._db.execute(
            "SELECT deposit FROM guests WHERE id=? AND status='INHOUSE'",
            (guest_id,),
        ).fetchone()
        if not guest:
            return False, "未找到在住客人"
        new_deposit = float(guest[0] or 0) + amount
        self._db.execute(
            "UPDATE guests SET deposit=? WHERE id=?", (new_deposit, guest_id)
        )
        return True, f"押金已追加，当前押金 {new_deposit:.2f}"