"""
transactions.checkin ── 入住事务

把"房间状态变更 + guests 写入 + ledger 落账"绑成一个原子操作。

设计要点：
- 任何一步失败（UPDATE 影响行数 != 1、INSERT 抛异常、ledger callback 抛异常），
  整个 with 块自动 rollback，绝不留下半个状态。
- ledger 写入由调用方传回调（因为支付明细/折扣理由是 UI 状态，不应跨层污染）。
- 房态前置：只接受当前为 READY/DIRTY 的房间；INHOUSE 直接拒。

[sub-a] 预订关联：
  - 入住时若 guests.booking_id 为空，尝试按 客人名+手机+入住日期 匹配
    local_reservations，自动填 booking_id；
  - 匹配不到不阻断，仅日志；后续可由服务员手工补关联。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable, Optional

from database import db

logger = logging.getLogger(__name__)


class CheckinError(RuntimeError):
    """入住事务专用异常，UI 层捕获后给出"入住未完成"之类的提示。"""


class CheckinTransaction:
    """入住事务。

    用法：
        def _post_ledger(conn):
            # 调用方专属的 ledger 写入逻辑
            db.append_ledger_conn(conn, "ROOM_IN", ...)

        try:
            CheckinTransaction(
                room_id="101",
                guest_name="张三",
                id_card="110...",
                phone="13800001111",
                ledger_callback=_post_ledger,
            ).commit()
        except CheckinError as e:
            show_warning(self, "提示", f"入住未完成：{e}")
    """

    def __init__(
        self,
        room_id: str,
        guest_name: str,
        id_card: str = "",
        phone: str = "",
        ledger_callback: Optional[Callable] = None,
    ):
        self.room_id = str(room_id).strip()
        self.guest_name = (guest_name or "").strip() or "散客"
        self.id_card = (id_card or "").strip() or "未填写"
        self.phone = (phone or "").strip() or "未填写"
        self.ledger_callback = ledger_callback

        if not self.room_id:
            raise CheckinError("room_id 不能为空")

    def commit(self) -> None:
        """提交事务。失败抛异常；成功无返回值（界面自行处理后续刷新/通知）。"""
        try:
            with db.transaction() as conn:
                cur = conn.execute(
                    "UPDATE rooms SET status='INHOUSE' "
                    "WHERE room_id=? AND COALESCE(status,'READY') <> 'INHOUSE'",
                    (self.room_id,),
                )
                if (cur.rowcount or 0) != 1:
                    raise CheckinError("房间状态已变化，请刷新房态后重试")

                conn.execute(
                    "INSERT INTO guests (room_id, name, id_card, phone, flag) VALUES (?, ?, ?, ?, 'WalkIn')",
                    (self.room_id, self.guest_name, self.id_card, self.phone),
                )

                # [sub-a] 预订关联：按 客人名+手机+入住日期 匹配 local_reservations
                # 匹配不到不阻断，仅 info 日志，便于服务员后续手工补关联
                self._try_link_booking(conn)

                if self.ledger_callback is not None:
                    # 任何回调里的异常都会让外层 with 触发 rollback
                    self.ledger_callback(conn)
        except CheckinError:
            raise
        except Exception as exc:
            # 把底层异常包成自定义错误，界面层只须捕获即可
            raise CheckinError(str(exc)) from exc

        from event_bus import bus
        bus.toast_requested.emit(f"✅ {self.room_id} 入住成功 · {self.guest_name}")

    def _try_link_booking(self, conn) -> None:
        """[sub-a] 入住时按 客人名+手机+入住日期 匹配 local_reservations，
        自动回填 guests.booking_id。

        匹配规则（按优先级）：
          1. guest_name 完全匹配 + phone 完全匹配 + checkin_dt 当天
          2. guest_name 完全匹配 + checkin_dt 当天（手机缺失场景）
          3. guest_name 完全匹配 + checkin_dt ±1 天（宽容跨日入住）

        匹配命中后 UPDATE 当前 guests 行的 booking_id = reservation_id。
        任何异常都仅日志，绝不阻断入住主流程（业务核心是入住成功）。
        """
        if not self.guest_name or self.guest_name == "散客":
            return  # 散客无需匹配
        today_str = date.today().isoformat()
        try:
            # 拿到刚 INSERT 的 guests.id（同事务内 lastrowid）
            guest_id = conn.execute(
                "SELECT id FROM guests WHERE room_id=? ORDER BY id DESC LIMIT 1",
                (self.room_id,),
            ).fetchone()
            if not guest_id:
                return
            gid = int(guest_id[0])

            # 优先级 1：name + phone + 当天
            row = None
            if self.phone and self.phone != "未填写":
                row = conn.execute(
                    "SELECT reservation_id FROM local_reservations "
                    "WHERE guest_name=? AND guest_phone=? AND checkin_dt=? "
                    "AND status IN ('PENDING','CONFIRMED') "
                    "ORDER BY created_at DESC LIMIT 1",
                    (self.guest_name, self.phone, today_str),
                ).fetchone()
            # 优先级 2：name + 当天
            if not row:
                row = conn.execute(
                    "SELECT reservation_id FROM local_reservations "
                    "WHERE guest_name=? AND checkin_dt=? "
                    "AND status IN ('PENDING','CONFIRMED') "
                    "ORDER BY created_at DESC LIMIT 1",
                    (self.guest_name, today_str),
                ).fetchone()
            # 优先级 3：name + ±1 天（跨日入住宽容）
            if not row:
                row = conn.execute(
                    "SELECT reservation_id FROM local_reservations "
                    "WHERE guest_name=? "
                    "AND date(checkin_dt) BETWEEN date(?, '-1 day') AND date(?, '+1 day') "
                    "AND status IN ('PENDING','CONFIRMED') "
                    "ORDER BY created_at DESC LIMIT 1",
                    (self.guest_name, today_str, today_str),
                ).fetchone()
            if row and row[0]:
                conn.execute(
                    "UPDATE guests SET booking_id=? WHERE id=?",
                    (str(row[0]), gid),
                )
                logger.info(
                    "[checkin] 预订关联成功: room=%s guest=%s booking_id=%s",
                    self.room_id, self.guest_name, row[0],
                )
            else:
                logger.debug(
                    "[checkin] 无匹配预订: room=%s guest=%s phone=%s date=%s",
                    self.room_id, self.guest_name, self.phone, today_str,
                )
        except Exception as e:
            logger.debug("[checkin] 预订关联异常（不阻断入住）: %s", e)
