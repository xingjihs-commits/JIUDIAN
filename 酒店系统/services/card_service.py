"""
Solid PMS — 发卡服务（业务编排层）

组合 GuestService + CardSystem + LockAdapter，
实现入住发卡、退房退卡、挂失、续卡等流程。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CardService:
    """发卡服务 — 门锁卡全生命周期管理。"""

    def __init__(self, db: Any):
        self._db = db

    def issue_guest_card(
        self,
        room_id: str,
        guest_name: str = "",
        *,
        operator_id: Optional[str] = None,
        dls_co_id: int = 1,
        card_no: int = 1,
        dai: int = 0,
        b_date: str = "",
        e_date: str = "",
        lock_no: str = "",
        max_cards: int = 4,
    ) -> tuple[bool, str]:
        """为客人发卡。

        Returns:
            (success, message)
        """
        # 检查是否已有卡
        existing = self._db.execute(
            "SELECT COUNT(*) FROM card_records "
            "WHERE room_id=? AND status='ACTIVE'",
            (room_id,),
        ).fetchone()
        if existing and existing[0] >= max_cards:
            return False, f"房间 {room_id} 已有 {existing[0]} 张活跃卡（上限 {max_cards}）"

        try:
            card_id = f"CARD_{int(__import__('time').time()*1000)}"

            # 调用门锁适配器发卡
            from lock_adapters.base import get_active_adapter
            adapter = get_active_adapter()
            if adapter is None:
                return False, "未找到可用的门锁适配器"

            result = adapter.issue_guest_card(
                room_id=room_id,
                checkin=b_date,
                checkout=e_date,
                lock_no=lock_no,
                dls_co_id=dls_co_id,
                card_no=card_no,
                dai=dai,
            )

            if not result.get("ok"):
                return False, f"发卡失败: {result.get('error', '未知错误')}"

            # 记录到数据库
            self._db.execute(
                "INSERT INTO card_records "
                "(card_id, room_id, guest_name, issue_time, status, "
                "operator_id, registry_kind, sequence) "
                "VALUES (?,?,?,datetime('now','localtime'),'ACTIVE',?,'guest',?)",
                (card_id, room_id, guest_name or "", operator_id or "",
                 (existing[0] + 1) if existing else 1),
            )

            # 更新房间最后发卡号
            self._db.execute(
                "UPDATE rooms SET last_card_no=?, last_seq=last_seq+1 WHERE room_id=?",
                (card_no, room_id),
            )

            return True, f"已为 {room_id} 发卡（第 {(existing[0]+1) if existing else 1} 张）"

        except Exception as e:
            logger.exception("发卡失败")
            return False, f"发卡异常: {e}"

    def erase_card(
        self,
        card_id: str,
        *,
        operator_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """退卡/擦除。

        Returns:
            (success, message)
        """
        card = self._db.execute(
            "SELECT card_id, room_id, status FROM card_records WHERE card_id=?",
            (card_id,),
        ).fetchone()
        if not card:
            return False, f"卡 {card_id} 不存在"
        if card[2] != "ACTIVE":
            return False, f"卡 {card_id} 当前状态 {card[2]}，无法退卡"

        try:
            from lock_adapters.base import get_active_adapter
            adapter = get_active_adapter()
            if adapter:
                adapter.erase_card(card_id)

            self._db.execute(
                "UPDATE card_records SET status='ERASED', "
                "erase_time=datetime('now','localtime'), operator_id=? "
                "WHERE card_id=?",
                (operator_id or "", card_id),
            )
            return True, f"卡 {card_id} 已退卡"
        except Exception as e:
            logger.exception("退卡失败")
            return False, f"退卡异常: {e}"

    def get_active_cards(self, room_id: Optional[str] = None) -> list:
        """获取活跃卡列表。"""
        if room_id:
            return self._db.execute(
                "SELECT * FROM card_records WHERE status='ACTIVE' AND room_id=?",
                (room_id,),
            ).fetchall()
        return self._db.execute(
            "SELECT * FROM card_records WHERE status='ACTIVE' ORDER BY issue_time DESC"
        ).fetchall()

    def get_card_history(self, room_id: str = "", limit: int = 100) -> list:
        """查询发卡历史。"""
        if room_id:
            return self._db.execute(
                "SELECT * FROM card_records WHERE room_id=? "
                "ORDER BY issue_time DESC LIMIT ?",
                (room_id, limit),
            ).fetchall()
        return self._db.execute(
            "SELECT * FROM card_records ORDER BY issue_time DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def report_lost(
        self,
        card_id: str,
        *,
        operator_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """挂失。"""
        card = self._db.execute(
            "SELECT card_id, status FROM card_records WHERE card_id=?",
            (card_id,),
        ).fetchone()
        if not card:
            return False, f"卡 {card_id} 不存在"
        if card[1] != "ACTIVE":
            return False, f"卡 {card_id} 不是活跃状态"

        self._db.execute(
            "UPDATE card_records SET status='LOST', "
            "erase_time=datetime('now','localtime'), operator_id=? "
            "WHERE card_id=?",
            (operator_id or "", card_id),
        )
        return True, f"卡 {card_id} 已挂失"
