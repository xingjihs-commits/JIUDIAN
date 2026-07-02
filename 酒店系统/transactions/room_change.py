"""transactions.room_change - atomic room move."""
from __future__ import annotations

from dataclasses import dataclass

from database import db


@dataclass
class RoomChangeResult:
    ok: bool
    guest_id: int | None = None
    error: str = ""


class RoomChangeTransaction:
    """Move an in-house guest from one room to another and mark old room DIRTY."""

    def __init__(self, old_room: str, new_room: str, operator: str):
        self.old_room = str(old_room or "").strip()
        self.new_room = str(new_room or "").strip()
        self.operator = str(operator or "").strip()
        if not self.old_room or not self.new_room:
            raise ValueError("old_room/new_room cannot be empty")
        if not self.operator:
            raise ValueError("operator cannot be empty")

    def execute(self) -> RoomChangeResult:
        try:
            with db.transaction() as conn:
                guest = conn.execute(
                    "SELECT id FROM guests WHERE room_id=? AND status='INHOUSE' ORDER BY id DESC LIMIT 1",
                    (self.old_room,),
                ).fetchone()
                if not guest:
                    raise RuntimeError("当前房间没有在住客人")
                target = conn.execute(
                    "SELECT COALESCE(lock_no,''), COALESCE(status,'READY') FROM rooms WHERE room_id=?",
                    (self.new_room,),
                ).fetchone()
                if not target:
                    raise RuntimeError("目标房不存在")
                if str(target[1]) != "READY":
                    raise RuntimeError("目标房不是空净房")
                if not str(target[0] or "").strip():
                    raise RuntimeError("目标房缺锁号")
                conn.execute("UPDATE guests SET room_id=?, flag='Copy' WHERE id=?", (self.new_room, guest[0]))
                conn.execute("UPDATE rooms SET status='DIRTY' WHERE room_id=?", (self.old_room,))
                cur = conn.execute(
                    "UPDATE rooms SET status='INHOUSE' WHERE room_id=? AND status='READY'",
                    (self.new_room,),
                )
                if (cur.rowcount or 0) != 1:
                    raise RuntimeError("目标房状态已变化")
                db.append_ledger_conn(
                    conn, "ROOM_CHANGE", 0, "SYSTEM", 0, self.new_room,
                    f"换房：{self.old_room} -> {self.new_room}",
                )
                db.log_action(self.operator, "ROOM_CHANGE", f"{self.old_room}->{self.new_room}")
                return RoomChangeResult(ok=True, guest_id=int(guest[0]))
        except Exception as exc:
            return RoomChangeResult(ok=False, error=str(exc))
