"""services/risk_service.py — 风控和审计查询服务（从 database.py 拆分）"""
from __future__ import annotations
from typing import Any


def get_staff_risk_stats(db, limit: int = 10) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT COALESCE(CAST(operator_id AS TEXT),'?') AS op,COUNT(*) AS cnt,"
        "SUM(CASE WHEN tx_type='ROOM_IN' AND amount<60 THEN 1 ELSE 0 END) AS d,"
        "SUM(CASE WHEN tx_type='PAYOUT_PENDING' THEN 1 ELSE 0 END) AS p "
        "FROM ledger WHERE date(created_at)=date('now','localtime') "
        "GROUP BY op ORDER BY d DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        {"operator": op, "total_ops": cnt, "discount_ops": int(d or 0),
         "payout_ops": int(p or 0), "risk_score": int(d or 0) * 5 + int(p or 0) * 3}
        for op, cnt, d, p in rows
    ]


def get_inventory_comparison(db, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT item_sku,"
        " SUM(CASE WHEN qty_change<0 THEN -qty_change ELSE 0 END) AS oq,"
        " SUM(CASE WHEN qty_change>0 THEN qty_change ELSE 0 END) AS iq,"
        " SUM(CASE WHEN action_type IN ('CHECKOUT_DEDUCT','HK_DEEP_DEDUCT') THEN -qty_change ELSE 0 END) AS hk,"
        " SUM(CASE WHEN action_type='LINEN_ISSUE' THEN -qty_change ELSE 0 END) AS li"
        " FROM inventory_audit GROUP BY item_sku ORDER BY item_sku LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {"sku": sk, "total_out": float(oq or 0), "total_in": float(iq or 0),
         "hk_deduct": float(hk or 0),
         "abnormal": abs(float(oq or 0) - float(hk or 0) - float(li or 0)) > 5}
        for sk, oq, iq, hk, li in rows
    ]


def build_daily_risk_report(db) -> dict[str, Any]:
    ov = db.get_audit_overview()
    ok, msg = db.verify_ledger_integrity()
    score = max(0, 100 - ov['today_discount_count'] * 4 -
                ov['energy_anomaly_count'] * 6 - ov['pending_carts'] * 2 -
                (0 if ok else 30))
    level = "GREEN" if score >= 80 else ("YELLOW" if score >= 60 else "RED")
    return {
        "score": score, "level": level,
        "report_text": (
            f"【风控日报】\n评级:{level}({score}/100)\n"
            f"营业额:{ov['today_income']:.2f} 押金属性净:{ov['today_deposit_net']:.2f}\n"
            f"降价:{ov['today_discount_count']}\n"
            f"能耗异常:{ov['energy_anomaly_count']}\n"
            f"待结:{ov['pending_carts']}\n账本:{msg}"
        ),
    }
