"""
======================================================
card_data.py — 门卡数据查询、统计（从 card_system.py 拆分）
======================================================
"""
from __future__ import annotations

import logging
from datetime import datetime

from database import db
from lock_legacy_bridge import (
    CARD_STATUS_ACTIVE,
    CARD_STATUS_ERASED,
    CARD_STATUS_EXPIRED,
    CARD_STATUS_LOST,
    CARD_STATUS_LOST_PENDING,
    CARD_STATUS_PENDING,
    CARD_TYPE_LABELS,
    LEGACY_ACTIVE_CARD_STATUSES,
    LEGACY_ERASED_CARD_STATUSES,
    LEGACY_EXPIRED_CARD_STATUSES,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  共享工具函数
# ─────────────────────────────────────────────


def _sql_placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


def _card_status_display(status: str) -> str:
    return {
        CARD_STATUS_ACTIVE: "✅ 客人卡",
        CARD_STATUS_PENDING: "⏳ 待写入",
        CARD_STATUS_ERASED: "🚫 已注销",
        CARD_STATUS_EXPIRED: "⏰ 已过期",
        CARD_STATUS_LOST_PENDING: "⚠️ 待刷挂失卡",
        CARD_STATUS_LOST: "⚠️ 已挂失",
        "ACTIVE": "✅ 客人卡",
        "PENDING": "⏳ 待写入",
        "CANCELLED": "🚫 已注销",
        "EXPIRED": "⏰ 已过期",
        "LOST_PENDING_PHYSICAL": "⚠️ 待刷挂失卡",
        "LOST": "⚠️ 已挂失",
        "BLACKLISTED": "⛔ 已拉黑",
    }.get(str(status or ""), str(status or "未知"))


def _registry_kind_display(kind: str) -> str:
    m = {
        "master": "总卡",
        "auth": "授权卡",
        "housekeeping": "保洁总卡",
        "floor": "楼层卡",
        "building": "楼栋卡",
        "emergency": "应急卡",
        "group": "组控卡",
        "groupset": "组号设置卡",
        "record": "记录卡",
        "roomset": "房号设置卡",
        "timeset": "时钟设置卡",
        "checkout": "退房卡",
        "loss": "挂失卡",
        "guest": "客房卡",
    }
    return m.get((kind or "guest").lower(), kind or "客房卡")


REGISTRY_CARD_KINDS = (
    "master", "auth", "housekeeping", "floor", "building",
    "emergency", "group", "groupset", "record", "roomset", "timeset", "checkout", "loss",
)


def _agent_debug_log(hypothesis_id: str, message: str, data: dict) -> None:
    return


def _is_registry_record(rec: dict) -> bool:
    rk = (rec.get("registry_kind") or "guest").lower()
    if rk in REGISTRY_CARD_KINDS:
        return True
    rid = (rec.get("room_id") or "").strip()
    return rid == "__REGISTRY__"


# ─────────────────────────────────────────────
#  门卡数据查询函数
# ─────────────────────────────────────────────


def get_room_cards(room_id: str) -> list[dict]:
    """查询某房间的所有门卡记录"""
    rows = db.execute(
        """SELECT card_id, guest_name, issue_time, expire_time, status, operator_id,
                  COALESCE(registry_kind, 'guest') AS rk
           FROM card_records WHERE room_id=? ORDER BY issue_time DESC""",
        (room_id,)
    ).fetchall()
    return [
        {
            "card_id": r[0], "guest_name": r[1],
            "issue_time": r[2], "expire_time": r[3],
            "status": r[4], "operator_id": r[5],
            "registry_kind": r[6],
        }
        for r in rows
    ]


def get_all_cards(status_filter: str = "ALL") -> list[dict]:
    """查询所有门卡记录"""
    if status_filter == "ALL":
        rows = db.execute(
            """SELECT card_id, room_id, guest_name, issue_time, expire_time, status, operator_id,
                      COALESCE(registry_kind, 'guest') AS rk
               FROM card_records ORDER BY issue_time DESC LIMIT 200"""
        ).fetchall()
    elif status_filter == "ACTIVE":
        rows = db.execute(
            f"""SELECT card_id, room_id, guest_name, issue_time, expire_time, status, operator_id,
                      COALESCE(registry_kind, 'guest') AS rk
               FROM card_records WHERE status IN ({_sql_placeholders(LEGACY_ACTIVE_CARD_STATUSES)})
               ORDER BY issue_time DESC LIMIT 200""",
            LEGACY_ACTIVE_CARD_STATUSES,
        ).fetchall()
    elif status_filter == "CANCELLED":
        rows = db.execute(
            f"""SELECT card_id, room_id, guest_name, issue_time, expire_time, status, operator_id,
                      COALESCE(registry_kind, 'guest') AS rk
               FROM card_records WHERE status IN ({_sql_placeholders(LEGACY_ERASED_CARD_STATUSES)})
               ORDER BY issue_time DESC LIMIT 200""",
            LEGACY_ERASED_CARD_STATUSES,
        ).fetchall()
    elif status_filter == "EXPIRED":
        rows = db.execute(
            f"""SELECT card_id, room_id, guest_name, issue_time, expire_time, status, operator_id,
                      COALESCE(registry_kind, 'guest') AS rk
               FROM card_records WHERE status IN ({_sql_placeholders(LEGACY_EXPIRED_CARD_STATUSES)})
               ORDER BY issue_time DESC LIMIT 200""",
            LEGACY_EXPIRED_CARD_STATUSES,
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT card_id, room_id, guest_name, issue_time, expire_time, status, operator_id,
                      COALESCE(registry_kind, 'guest') AS rk
               FROM card_records WHERE status=? ORDER BY issue_time DESC LIMIT 200""",
            (status_filter,)
        ).fetchall()
    return [
        {
            "card_id": r[0], "room_id": r[1], "guest_name": r[2],
            "issue_time": r[3], "expire_time": r[4],
            "status": r[5], "operator_id": r[6],
            "registry_kind": r[7],
        }
        for r in rows
    ]


def expire_overdue_cards():
    """将已过期的客人卡标记为已过期。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        f"UPDATE card_records SET status=? WHERE status IN ({_sql_placeholders(LEGACY_ACTIVE_CARD_STATUSES)}) AND expire_time < ?",
        (CARD_STATUS_EXPIRED, *LEGACY_ACTIVE_CARD_STATUSES, now)
    )
