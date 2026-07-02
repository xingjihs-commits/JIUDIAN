"""db_access/aggregation.py — 数据库聚合查询（从 database.py 拆分）

提供:
  - get_audit_overview — 今日审计概览
  - get_overview_by_range — 时间范围审计概览
  - run_night_audit — 夜审日结
  - get_staff_risk_stats — 员工风控统计（委托到 services/risk_service）
  - get_inventory_comparison — 库存对比分析（委托到 services/risk_service）
  - build_daily_risk_report — 每日风控报告（委托到 services/risk_service）

这些函数本质上是带业务语义的 SQL 聚合查询，放在 db_access/ 层
介于 database.py（纯连接/事务/迁移）和 services/（纯业务编排）之间。
"""
from __future__ import annotations

from typing import Any


def _sql_in_types(types: tuple[str, ...]) -> str:
    return ",".join(f"'{t}'" for t in types)


LEDGER_REVENUE_TX_TYPES = ("ROOM_IN", "SHOP", "TIP", "LEGACY_IMPORT")
LEDGER_DEPOSIT_TX_TYPES = ("DEPOSIT_IN", "DEPOSIT_OUT")
LEDGER_CASH_NET_TX_TYPES = (
    "ROOM_IN", "DEPOSIT_IN", "DEPOSIT_OUT", "SHOP",
    "CASH_IN", "PAYOUT", "EXPENSE", "TIP",
)


def get_audit_overview(db) -> dict[str, Any]:
    inc_sql = _sql_in_types(LEDGER_REVENUE_TX_TYPES)
    dep_sql = _sql_in_types(LEDGER_DEPOSIT_TX_TYPES)
    inc = db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM ledger WHERE tx_type IN ({inc_sql}) "
        f"AND date(created_at)=date('now','localtime')"
    ).fetchone()[0]
    dep_net = db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM ledger WHERE tx_type IN ({dep_sql}) "
        f"AND date(created_at)=date('now','localtime')"
    ).fetchone()[0]
    disc = db.execute(
        "SELECT COUNT(*) FROM ledger WHERE tx_type='ROOM_IN' AND amount<60 "
        "AND date(created_at)=date('now','localtime')"
    ).fetchone()[0]
    ener = db.execute(
        "SELECT COUNT(*) FROM energy_audit WHERE is_anomaly=1 "
        "AND date(reading_time)=date('now','localtime')"
    ).fetchone()[0]
    cart = db.execute(
        "SELECT COUNT(*) FROM pending_carts WHERE status='PENDING'"
    ).fetchone()[0]
    return {
        "today_income": float(inc or 0),
        "today_deposit_net": float(dep_net or 0),
        "today_discount_count": int(disc or 0),
        "energy_anomaly_count": int(ener or 0),
        "pending_carts": int(cart or 0),
    }


def get_overview_by_range(db, start: str, end: str) -> dict[str, Any]:
    inc = _sql_in_types(LEDGER_REVENUE_TX_TYPES)
    dep = _sql_in_types(LEDGER_DEPOSIT_TX_TYPES)
    revenue = float(db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM ledger "
        f"WHERE created_at BETWEEN ? AND ? AND tx_type IN ({inc})",
        (start, end),
    ).fetchone()[0] or 0)
    deposit_net = float(db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM ledger "
        f"WHERE created_at BETWEEN ? AND ? AND tx_type IN ({dep})",
        (start, end),
    ).fetchone()[0] or 0)
    total_rooms = int(db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] or 0)
    inhouse_count = int(db.execute(
        "SELECT COUNT(*) FROM rooms WHERE status='INHOUSE'"
    ).fetchone()[0] or 0)
    ready_count = int(db.execute(
        "SELECT COUNT(*) FROM rooms WHERE status='READY'"
    ).fetchone()[0] or 0)
    room_rows = db.execute(
        "SELECT status, COUNT(*) FROM rooms GROUP BY status"
    ).fetchall()
    room_status_counts = dict(room_rows)
    type_rows = db.execute(
        f"SELECT tx_type, COALESCE(SUM(amount),0) FROM ledger "
        f"WHERE created_at BETWEEN ? AND ? GROUP BY tx_type",
        (start, end),
    ).fetchall()
    by_type = {t: float(a or 0) for t, a in type_rows}
    pay_rows = db.execute(
        f"SELECT pay_method, COALESCE(SUM(amount),0) FROM ledger "
        f"WHERE created_at BETWEEN ? AND ? GROUP BY pay_method",
        (start, end),
    ).fetchall()
    by_pay = {p or "CASH": float(a or 0) for p, a in pay_rows}
    revpar = revenue / total_rooms if total_rooms > 0 else 0.0
    adr = revenue / inhouse_count if inhouse_count > 0 else None
    anoms = int(db.execute(
        "SELECT COUNT(*) FROM energy_audit WHERE reading_time BETWEEN ? AND ? AND is_anomaly=1",
        (start, end),
    ).fetchone()[0] or 0)
    return {
        "revenue": revenue, "deposit_net": deposit_net,
        "occupancy": (inhouse_count / total_rooms * 100) if total_rooms > 0 else 0.0,
        "inhouse_count": inhouse_count, "ready_count": ready_count,
        "total_rooms": total_rooms, "room_status_counts": room_status_counts,
        "by_type": by_type, "by_pay": by_pay,
        "revpar": revpar, "adr": adr, "energy_anomaly_count": anoms,
    }


def run_night_audit(db) -> str:
    today = db.execute("SELECT date('now','localtime')").fetchone()[0]
    inc = _sql_in_types(LEDGER_REVENUE_TX_TYPES)
    dep_sql = _sql_in_types(LEDGER_DEPOSIT_TX_TYPES)
    revenue = db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM ledger WHERE date(created_at)=? AND tx_type IN ({inc})",
        (today,),
    ).fetchone()[0] or 0
    deposit_net = db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM ledger WHERE date(created_at)=? AND tx_type IN ({dep_sql})",
        (today,),
    ).fetchone()[0] or 0
    occ = db.execute("SELECT COUNT(*) FROM rooms WHERE status='INHOUSE'").fetchone()[0]
    total_rooms = db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    occ_rate = (occ / total_rooms) * 100 if total_rooms > 0 else 0
    report = (
        f"📅 [{today}] 过夜审计报表\n------------------\n"
        f"💰 营业额(不含押): ${float(revenue or 0):.2f}\n"
        f"🧾 押金进出净额: ${float(deposit_net or 0):.2f}\n"
        f"🏨 出租率: {occ_rate:.1f}% ({occ}/{total_rooms})\n"
        f"⚠️ 风控事件: {db.execute('SELECT COUNT(*) FROM audit_events WHERE date(created_at)=?', (today,)).fetchone()[0]}\n"
    )
    db.append_ledger(
        "NIGHT_AUDIT", 0, "SYSTEM", 1,
        note=f"日结完成 - 营业额:{float(revenue or 0):.2f} 押金属性净流:{float(deposit_net or 0):.2f} OCC:{occ_rate:.1f}%",
    )
    return report
