"""总览数据组装 — 从数据库和校验引擎收集摘要。

[v2] 新增 build_full_overview() —— 一屏看全店的仪表盘数据聚合，
覆盖房态/今日动线/财务/房均/在住 Top/待办/在班员工 7 大维度。
旧 assemble() 保留以兼容 v4 tab 现有调用点，内部委托给新实现。
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from typing import Any

from database import db


# ── 货币符号默认值（get_config 失败时兜底） ────────────────────
_DEFAULT_CURRENCY = "¥"


def _today_iso() -> str:
    return date.today().isoformat()


def _safe_query(sql: str, params: tuple = ()) -> list:
    """防御性 query：异常时返回 [] 而非抛出。"""
    try:
        return db.execute(sql, params).fetchall()
    except Exception:
        return []


def _safe_scalar(sql: str, params: tuple = (), default: Any = 0) -> Any:
    """防御性标量查询：异常时返回 default。"""
    try:
        row = db.execute(sql, params).fetchone()
        if row is None:
            return default
        return row[0]
    except Exception:
        return default


def _get_today_arrivals_departures() -> dict[str, list[dict]]:
    """今日预计到达/离店（来自 local_reservations 和 guests.checkout_time）。"""
    arrivals_raw = _safe_query(
        "SELECT room_id, guest_name, guest_phone, checkin_dt, status "
        "FROM local_reservations "
        "WHERE status IN ('PENDING','CONFIRMED') "
        "AND date(checkin_dt)=date('now','localtime') "
        "ORDER BY checkin_dt LIMIT 50"
    )
    arrivals = []
    for r in arrivals_raw:
        arrivals.append({
            "room_id": r[0] or "—",
            "guest_name": r[1] or "—",
            "phone": r[2] or "",
            "checkin_dt": r[3] or "",
            "status": r[4] or "",
        })

    departures_raw = _safe_query(
        "SELECT room_id, name, phone, checkout_time, id "
        "FROM guests WHERE status='INHOUSE' "
        "AND checkout_time IS NOT NULL "
        "AND date(checkout_time)=date('now','localtime') "
        "ORDER BY checkout_time LIMIT 50"
    )
    departures = []
    for r in departures_raw:
        departures.append({
            "room_id": r[0] or "—",
            "guest_name": r[1] or "—",
            "phone": r[2] or "",
            "checkout_time": r[3] or "",
        })

    return {"arrivals": arrivals, "departures": departures}


def _get_today_actual_movements() -> dict[str, int]:
    """今日实际入住/退房计数（来自 ledger 的 ROOM_IN / ROOM_OUT）。"""
    actual_checkin = int(_safe_scalar(
        "SELECT COUNT(*) FROM ledger "
        "WHERE tx_type='ROOM_IN' AND date(created_at)=date('now','localtime')",
        default=0,
    ) or 0)
    # ROOM_OUT 是退房事务；旧库可能用 'CHECKOUT'，二者都计入
    actual_checkout = int(_safe_scalar(
        "SELECT COUNT(*) FROM ledger "
        "WHERE tx_type IN ('ROOM_OUT','CHECKOUT') "
        "AND date(created_at)=date('now','localtime')",
        default=0,
    ) or 0)
    return {"actual_checkin": actual_checkin, "actual_checkout": actual_checkout}


def _get_today_financials() -> dict[str, Any]:
    """今日财务聚合：营收(分币种) / 收款 / 退款 / 押金在押。"""
    # 营收分币种
    rev_rows = _safe_query(
        "SELECT COALESCE(currency,''), COALESCE(SUM(amount),0) "
        "FROM ledger "
        "WHERE tx_type IN ('ROOM_IN','SHOP','TIP','LEGACY_IMPORT') "
        "AND date(created_at)=date('now','localtime') "
        "GROUP BY currency"
    )
    revenue_by_currency: dict[str, float] = {}
    total_revenue = 0.0
    for r in rev_rows:
        cur = r[0] or "USD"
        amt = float(r[1] or 0)
        revenue_by_currency[cur] = revenue_by_currency.get(cur, 0.0) + amt
        total_revenue += amt

    # 今日收款（所有正金额）
    receipts = float(_safe_scalar(
        "SELECT COALESCE(SUM(amount),0) FROM ledger "
        "WHERE amount > 0 AND date(created_at)=date('now','localtime')",
        default=0,
    ) or 0)

    # 今日退款（REFUND 类型或负金额）
    refunds = float(_safe_scalar(
        "SELECT COALESCE(SUM(ABS(amount)),0) FROM ledger "
        "WHERE tx_type='REFUND' AND date(created_at)=date('now','localtime')",
        default=0,
    ) or 0)

    # 押金在押（历史 DEPOSIT_IN 总和 - DEPOSIT_OUT 总和，全期累计）
    deposit_in_total = float(_safe_scalar(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE tx_type='DEPOSIT_IN'",
        default=0,
    ) or 0)
    deposit_out_total = float(_safe_scalar(
        "SELECT COALESCE(SUM(ABS(amount)),0) FROM ledger WHERE tx_type='DEPOSIT_OUT'",
        default=0,
    ) or 0)
    deposit_held = max(deposit_in_total - deposit_out_total, 0.0)

    return {
        "revenue_total": round(total_revenue, 2),
        "revenue_by_currency": {k: round(v, 2) for k, v in revenue_by_currency.items()},
        "receipts_today": round(receipts, 2),
        "refunds_today": round(refunds, 2),
        "deposit_held": round(deposit_held, 2),
    }


def _get_inhouse_guests_top(limit: int = 8) -> list[dict]:
    """在住客人 Top 列表（房号 / 客人 / 退房日期）。"""
    rows = _safe_query(
        "SELECT room_id, name, phone, checkin_time, checkout_time "
        "FROM guests WHERE status='INHOUSE' "
        "ORDER BY checkout_time ASC LIMIT ?",
        (limit,),
    )
    out: list[dict] = []
    for r in rows:
        out.append({
            "room_id": r[0] or "—",
            "guest_name": r[1] or "—",
            "phone": r[2] or "",
            "checkin_time": r[3] or "",
            "checkout_time": r[4] or "—",
        })
    return out


def _get_todo_counts() -> dict[str, int]:
    """待办：未清扫房间 / 待发卡 / 库存预警。"""
    # 未清扫房间：status=DIRTY 或 OVERTIME
    dirty_count = int(_safe_scalar(
        "SELECT COUNT(*) FROM rooms WHERE status IN ('DIRTY','OVERTIME')",
        default=0,
    ) or 0)

    # 待发卡：guests.status='INHOUSE' 但没有对应 card_records 的客人
    pending_cards = int(_safe_scalar(
        "SELECT COUNT(DISTINCT g.id) FROM guests g "
        "LEFT JOIN card_records c "
        "  ON c.guest_name = g.name AND c.room_id = g.room_id AND c.status='active' "
        "WHERE g.status='INHOUSE' AND c.id IS NULL",
        default=0,
    ) or 0)

    # 库存预警：shop_items.stock <= 安全阈值（暂用 5 作硬阈值）
    low_stock = int(_safe_scalar(
        "SELECT COUNT(*) FROM shop_items WHERE stock <= 5",
        default=0,
    ) or 0)

    return {
        "dirty_rooms": dirty_count,
        "pending_cards": pending_cards,
        "low_stock": low_stock,
    }


def _get_onshift_staff_count() -> int:
    """当班员工数：今日已签到但未签退。"""
    today = _today_iso()
    cnt = _safe_scalar(
        "SELECT COUNT(DISTINCT staff_id) FROM staff_attendance "
        "WHERE record_date=? AND clock_in IS NOT NULL AND clock_out IS NULL",
        (today,),
        default=0,
    )
    return int(cnt or 0)


def _get_yesterday_revenue() -> float:
    """昨日营收（用于趋势箭头对比）。"""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    rev = _safe_scalar(
        "SELECT COALESCE(SUM(amount),0) FROM ledger "
        "WHERE tx_type IN ('ROOM_IN','SHOP','TIP','LEGACY_IMPORT') "
        "AND date(created_at)=?",
        (yesterday,),
        default=0,
    )
    return float(rev or 0)


def build_full_overview() -> dict[str, Any]:
    """一屏看全店：聚合房态/今日/财务/房均/在住/待办/员工 7 大维度。

    Returns:
        {
            "snapshot_time": str,
            "rooms": { total, inhouse, dirty, ready, maintenance, occupancy_pct },
            "today": { arrivals_count, departures_count, actual_checkin, actual_checkout },
            "financials": {
                revenue_total, revenue_by_currency,
                receipts_today, refunds_today, deposit_held,
                revenue_trend_pct,  # vs 昨日 +x.x% / -x.x% / "—" 
            },
            "kpi": { adr, revpar },
            "inhouse_guests_top": list[{room_id, guest_name, checkout_time}],
            "todos": { dirty_rooms, pending_cards, low_stock },
            "staff": { onshift_count },
            "alerts": list[str],
        }
    所有查询失败时各字段降级为 0 / 空列表，绝不抛异常（仪表盘必须可显示）。
    """
    # 房态分布（复用 db.get_overview_by_range，再补 dirty/maintenance 计数）
    try:
        today = _today_iso()
        ov = db.get_overview_by_range(f"{today} 00:00:00", f"{today} 23:59:59")
    except Exception:
        ov = {}

    sc = ov.get("room_status_counts", {}) or {}
    total_rooms = int(ov.get("total_rooms", 0) or 0)
    inhouse = int(sc.get("INHOUSE", 0) or sc.get("OCCUPIED", 0) or 0)
    dirty = int(sc.get("DIRTY", 0) or sc.get("VD", 0) or 0)
    ready = int(sc.get("READY", 0) or sc.get("VC", 0) or sc.get("VACANT", 0) or 0)
    maintenance = int(sc.get("MAINTENANCE", 0) or sc.get("OUT_OF_ORDER", 0) or 0)
    occupancy_pct = float(ov.get("occupancy", 0.0) or 0.0)

    rooms = {
        "total": total_rooms,
        "inhouse": inhouse,
        "dirty": dirty,
        "ready": ready,
        "maintenance": maintenance,
        "occupancy_pct": round(occupancy_pct, 1),
    }

    # 今日动线
    mv = _get_today_arrivals_departures()
    actual = _get_today_actual_movements()
    today_block = {
        "arrivals_count": len(mv["arrivals"]),
        "departures_count": len(mv["departures"]),
        "actual_checkin": actual["actual_checkin"],
        "actual_checkout": actual["actual_checkout"],
        "arrivals": mv["arrivals"],
        "departures": mv["departures"],
    }

    # 财务
    fin = _get_today_financials()
    yest = _get_yesterday_revenue()
    if yest > 0 and fin["revenue_total"] > 0:
        fin["revenue_trend_pct"] = round(
            (fin["revenue_total"] - yest) / yest * 100.0, 1
        )
    elif fin["revenue_total"] > 0 and yest <= 0:
        fin["revenue_trend_pct"] = 100.0  # 昨日零营收
    else:
        fin["revenue_trend_pct"] = None  # 今日也无营收 → 不显示

    # 房均指标
    adr = float(ov.get("adr") or 0.0)
    revpar = float(ov.get("revpar") or 0.0)
    if adr == 0.0 and inhouse > 0 and fin["revenue_total"] > 0:
        adr = fin["revenue_total"] / inhouse
    if revpar == 0.0 and total_rooms > 0 and fin["revenue_total"] > 0:
        revpar = fin["revenue_total"] / total_rooms
    kpi = {
        "adr": round(adr, 2),
        "revpar": round(revpar, 2),
    }

    # 在住客人 Top
    inhouse_top = _get_inhouse_guests_top(8)

    # 待办
    todos = _get_todo_counts()

    # 员工
    staff = {"onshift_count": _get_onshift_staff_count()}

    # 异常检查
    alerts: list[str] = []
    try:
        from reconciliation_checks import CHECKS
        for check in CHECKS:
            try:
                ok, count, detail = check.fn()
                if not ok:
                    alerts.append(f"⚠ {check.title}: {detail}")
            except Exception:
                pass
    except ImportError:
        pass

    # 基本预警
    if total_rooms > 0 and occupancy_pct > 95:
        alerts.append(f"入住率 {occupancy_pct:.0f}% > 95%")
    if dirty > 0:
        alerts.append(f"{dirty} 间脏房待清扫")
    if todos["low_stock"] > 0:
        alerts.append(f"{todos['low_stock']} 项商品库存预警")
    if todos["pending_cards"] > 0:
        alerts.append(f"{todos['pending_cards']} 间在住房待发卡")

    return {
        "snapshot_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rooms": rooms,
        "today": today_block,
        "financials": fin,
        "kpi": kpi,
        "inhouse_guests_top": inhouse_top,
        "todos": todos,
        "staff": staff,
        "alerts": alerts,
    }


def assemble(mode: str = "today", start: str = "", end: str = "") -> dict[str, Any]:
    """返回总览所需全部数据（旧接口，v4 tab 使用）。

    内部委托给 build_full_overview() + 提取兼容字段。
    """
    # 日期范围
    today = date.today()
    if not start:
        start = today.isoformat()
    if not end:
        end = today.isoformat()

    full = build_full_overview()
    rooms = full["rooms"]
    fin = full["financials"]

    # 兼容旧字段：pulse / rooms（旧字段名） / alerts / links
    return {
        "pulse": {
            "revenue": fin["revenue_total"],
            "occupancy": rooms["occupancy_pct"],
            "adr": full["kpi"]["adr"],
            "revpar": full["kpi"]["revpar"],
            "receipts": fin["receipts_today"],
            "refunds": fin["refunds_today"],
            "deposit_held": fin["deposit_held"],
            "revenue_trend_pct": fin["revenue_trend_pct"],
        },
        "rooms": {
            "total": rooms["total"],
            "ready": rooms["ready"],
            "inhouse": rooms["inhouse"],
            "dirty": rooms["dirty"],
            "maintenance": rooms["maintenance"],
            "occupancy_pct": rooms["occupancy_pct"],
        },
        "today": full["today"],
        "todos": full["todos"],
        "staff": full["staff"],
        "inhouse_guests_top": full["inhouse_guests_top"],
        "alerts": full["alerts"],
        "links": [
            {"label": "查看房态", "action": "matrix"},
            {"label": "今日收银", "action": "checkin"},
        ],
        # 同时把 full 嵌入，便于新 v4 tab 直接消费新结构
        "full": full,
    }
