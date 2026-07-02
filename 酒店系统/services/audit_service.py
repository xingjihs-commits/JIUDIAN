"""services/audit_service.py — 审计查询服务（从 database.py 拆分）

提供:
  - search_audit_logs — 查询操作审计日志
  - detect_anomalous_behavior — 检测异常行为
"""
from __future__ import annotations

from datetime import date


def search_audit_logs(
    actor: str = "",
    time_start: str = "",
    time_end: str = "",
    action_type: str = "",
    keyword: str = "",
    limit: int = 50,
) -> list:
    """查询操作审计日志。"""
    from database import db

    sql = "SELECT * FROM audit_events WHERE 1=1"
    params = []
    if actor:
        sql += " AND actor_id=?"
        params.append(actor)
    if time_start:
        sql += " AND created_at>=?"
        params.append(time_start)
    if time_end:
        sql += " AND created_at<=?"
        params.append(time_end)
    if action_type:
        sql += " AND event_type=?"
        params.append(action_type)
    if keyword:
        sql += " AND (reason LIKE ? OR metadata_json LIKE ?)"
        kw = f"%{keyword}%"
        params.extend([kw, kw])
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    try:
        return db.execute(sql, tuple(params)).fetchall()
    except Exception:
        return []


def detect_anomalous_behavior(actor_id: str) -> dict:
    """检测异常行为：连续密码错误 / 单日退款超3笔。"""
    from database import db

    today = date.today().isoformat()
    try:
        wrong_pw = db.execute(
            "SELECT COUNT(*) FROM audit_events WHERE actor_id=? "
            "AND event_type='LOGIN_FAILED' AND date(created_at)=?",
            (actor_id, today),
        ).fetchone()
        refunds = db.execute(
            "SELECT COUNT(*) FROM ledger WHERE operator_id=? "
            "AND tx_type='REFUND' AND date(created_at)=?",
            (actor_id, today),
        ).fetchone()
        return {
            "login_failures": int(wrong_pw[0]) if wrong_pw else 0,
            "refund_count": int(refunds[0]) if refunds else 0,
            "flagged": (int(wrong_pw[0] or 0) >= 3) or (int(refunds[0] or 0) >= 3),
        }
    except Exception:
        return {"login_failures": 0, "refund_count": 0, "flagged": False}
