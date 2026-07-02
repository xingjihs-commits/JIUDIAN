"""
inventory_audit_engine.py — C0-gamma 账实差异审计引擎

核心公式（来自 商业重构执行清单.md / C0-gamma）：

    账面库存 = 期初库存 + 入库 - 销售 - 客房消耗 - 报损 - 调整
    差异     = 实物盘点库存 - 账面库存
    差异率   = abs(差异) / max(账面库存, 1)

触发：
- 每一笔库存变动通过 inventory_baseline.append_movement 入哈希链流水（已实现于 C0-beta）。
- 每 15 天做一次手动盘点（由 stocktake_scheduler 推送 / 客户端唤起）。
- 差异率 >= 5% → 标红 / 推送老板 / 锁定 SKU 等待解释。

本模块责任：
- 开启盘点会话（start_periodic_session）
- 写入实盘数量 → 自动算账面与差异（commit_counted_quantities）
- 收尾会话 → 汇总差异 → 写报表所需指标（finalize_session）
- 提供解释 / 解锁 SKU 的入口（explain_line / unlock_line）
- 提供给 UI 与机器人用的差异列表查询
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from typing import Optional

from cloud_security import signature_headers
from database import db
from inventory_baseline import (
    MOVE_PERIODIC_RECONCILE, append_movement, book_qty,
    book_qty_for_shop, list_all_items, make_item_id, CATEGORY_SHOP,
)


CRITICAL_DIFF_RATE = 0.05  # 5% 报警阈值
SESSION_PERIODIC = "PERIODIC"
SESSION_INITIAL = "INITIAL"


# ─────────────────────────────────────────────────────────────────────────────
#  会话生命周期
# ─────────────────────────────────────────────────────────────────────────────

def start_periodic_session(operator_id: str = "SYSTEM", *,
                           note: str = "") -> str:
    """开启一次周期盘点会话。返回 session_id。"""
    sid = uuid.uuid4().hex
    db.execute(
        """INSERT INTO inventory_stocktake_sessions
           (session_id, session_type, operator_id, status, note)
           VALUES (?,?,?,?,?)""",
        (sid, SESSION_PERIODIC, operator_id or "SYSTEM", "IN_PROGRESS", note or ""),
    )
    return sid


def has_open_periodic_session() -> Optional[str]:
    row = db.execute(
        """SELECT session_id FROM inventory_stocktake_sessions
           WHERE session_type=? AND status='IN_PROGRESS'
           ORDER BY started_at DESC LIMIT 1""",
        (SESSION_PERIODIC,),
    ).fetchone()
    return row[0] if row else None


def book_qty_of(item_id: str) -> int:
    """账面库存：超市 SKU 用兼容回落（含老 shop_items.stock）；其他直接累计哈希链流水。"""
    if item_id.startswith("shop:"):
        sku = item_id[5:]
        return book_qty_for_shop(db, sku)
    return book_qty(db, item_id)


def _critical(diff_qty: int, book: int) -> tuple[bool, float]:
    if book <= 0:
        # 账面=0 但实物有差异（盘出未授权货 or 实物=0 而账面=0 时差=0）：
        #   - 实物 > 0 → 一定标红（来路不明）
        #   - 实物 = 0 → 不标红
        return (abs(diff_qty) > 0, 1.0 if abs(diff_qty) > 0 else 0.0)
    rate = abs(diff_qty) / max(book, 1)
    return (rate >= CRITICAL_DIFF_RATE, rate)


def upload_periodic_diff_to_cloud(session_id: str, summary: dict) -> bool:
    """把周期账实差异摘要推送给云端。失败不影响本地盘点收尾。"""
    try:
        import urllib.request as _ur
        import urllib.error as _ue

        worker = (db.get_config("cloud_worker_url") or "").strip().rstrip("/")
        if not worker:
            return False
        hotel_id = (db.get_config("hotel_id") or db.get_config("hotel_name") or "UNKNOWN").strip()
        rows = db.execute(
            """SELECT diff_rate FROM inventory_stocktake_lines
               WHERE session_id=? ORDER BY diff_rate DESC LIMIT 1""",
            (session_id,),
        ).fetchall()
        max_diff_rate = float(rows[0][0] or 0) if rows else 0.0
        payload = {
            "hotel_id": hotel_id,
            "session_id": session_id,
            "status": summary.get("status", ""),
            "total_items": int(summary.get("total_items") or 0),
            "items_with_diff": int(summary.get("items_with_diff") or 0),
            "items_critical": int(summary.get("items_critical") or 0),
            "unresolved_critical": int(summary.get("unresolved_critical") or 0),
            "max_diff_rate": max_diff_rate,
            "started_at": summary.get("started_at", ""),
            "finished_at": summary.get("finished_at", ""),
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        url = f"{worker}/api/periodic-diff"
        headers = {"Content-Type": "application/json"}
        headers.update(signature_headers("POST", url, body, subject=hotel_id))
        req = _ur.Request(
            url,
            data=body,
            method="POST",
            headers=headers,
        )
        with _ur.urlopen(req, timeout=8) as resp:
            ok = 200 <= resp.status < 300
    except (_ue.URLError, _ue.HTTPError, TimeoutError, OSError, ValueError):
        return False

    if ok:
        db.set_config("last_periodic_diff_uploaded_at", _dt.datetime.now().isoformat(timespec="seconds"))
    return ok


def commit_counted_quantities(session_id: str,
                              counted: dict,
                              *,
                              operator_id: str = "SYSTEM") -> dict:
    """提交一批"实盘数量"到指定会话。
    counted: {item_id: 实物盘点数量}
    会：
      1) 算出当时的账面库存（基于哈希链流水累计）
      2) 算 diff / rate / is_critical
      3) 写入 inventory_stocktake_lines（每个 item_id 一条；会话内若已存在则覆盖）
      4) 不立即回补账面差异；统一在 finalize_session 时按"解释 / 调整"决定是否冲账

    返回：{lines_total, lines_critical}
    """
    if not has_open_periodic_session() and session_id != has_open_periodic_session():
        # 防御：传入了已关闭的会话 id
        pass

    now_iso = _dt.datetime.now().isoformat(timespec="seconds")
    lines_total = 0
    lines_critical = 0

    for item_id, qty in (counted or {}).items():
        try:
            counted_qty = int(qty)
        except (TypeError, ValueError):
            continue
        book = book_qty_of(item_id)
        diff = counted_qty - book
        is_crit, rate = _critical(diff, book)

        # 同 item 同会话覆盖一行
        existing = db.execute(
            """SELECT line_id FROM inventory_stocktake_lines
               WHERE session_id=? AND item_id=?""",
            (session_id, item_id),
        ).fetchone()
        if existing:
            db.execute(
                """UPDATE inventory_stocktake_lines SET
                       book_qty=?, counted_qty=?, diff_qty=?, diff_rate=?,
                       is_critical=?, locked_at=?
                   WHERE line_id=?""",
                (book, counted_qty, diff, rate,
                 1 if is_crit else 0,
                 now_iso if is_crit else None,
                 existing[0]),
            )
        else:
            db.execute(
                """INSERT INTO inventory_stocktake_lines
                   (session_id, item_id, book_qty, counted_qty, diff_qty,
                    diff_rate, is_critical, locked_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (session_id, item_id, book, counted_qty, diff, rate,
                 1 if is_crit else 0, now_iso if is_crit else None),
            )
        lines_total += 1
        if is_crit:
            lines_critical += 1

    # 实时更新会话头指标
    aggregate = db.execute(
        """SELECT COUNT(*), SUM(CASE WHEN diff_qty<>0 THEN 1 ELSE 0 END),
                  SUM(CASE WHEN is_critical=1 THEN 1 ELSE 0 END)
           FROM inventory_stocktake_lines WHERE session_id=?""",
        (session_id,),
    ).fetchone()
    db.execute(
        """UPDATE inventory_stocktake_sessions SET
              total_items=?, items_with_diff=?, items_critical=?
           WHERE session_id=?""",
        (int(aggregate[0] or 0), int(aggregate[1] or 0),
         int(aggregate[2] or 0), session_id),
    )

    return {"lines_total": lines_total, "lines_critical": lines_critical}


def finalize_session(session_id: str, *, operator_id: str = "SYSTEM") -> dict:
    """关闭会话：把没有解释的 critical 行保持 locked，把已解释 / 0 差异行写一笔
    PERIODIC_RECONCILE 调整流水来"对齐账面"。返回汇总。
    """
    now_iso = _dt.datetime.now().isoformat(timespec="seconds")

    rows = db.execute(
        """SELECT line_id, item_id, book_qty, counted_qty, diff_qty, diff_rate,
                  is_critical, explanation, resolved_at
           FROM inventory_stocktake_lines WHERE session_id=?""",
        (session_id,),
    ).fetchall()

    reconcile_lines = 0
    unresolved_critical = 0
    for r in rows:
        (line_id, item_id, book, counted, diff, rate,
         is_crit, explanation, resolved_at) = r
        if diff == 0:
            continue
        if int(is_crit or 0) == 1 and not (explanation or resolved_at):
            unresolved_critical += 1
            continue
        # 写一笔 PERIODIC_RECONCILE 把账面同步到实物
        append_movement(
            db,
            item_id=item_id,
            move_type=MOVE_PERIODIC_RECONCILE,
            qty_change=int(diff),
            operator_id=operator_id,
            note=f"周期盘点对账 session={session_id[:8]}",
        )
        # 顺手同步老 shop_items.stock
        if item_id.startswith("shop:"):
            sku = item_id[5:]
            db.execute("UPDATE shop_items SET stock=? WHERE sku=?", (int(counted), sku))
        db.execute(
            "UPDATE inventory_stocktake_lines SET resolved_at=? WHERE line_id=?",
            (now_iso, line_id),
        )
        reconcile_lines += 1

    status = "COMPLETED" if unresolved_critical == 0 else "COMPLETED_WITH_LOCKED"
    db.execute(
        """UPDATE inventory_stocktake_sessions SET
              finished_at=?, status=?
           WHERE session_id=?""",
        (now_iso, status, session_id),
    )

    # 记录到 system_config 上次盘点时间（驱动调度器）
    db.set_config("last_periodic_stocktake_at", now_iso)

    s = summarize_session(session_id) or {}
    result = {
        "session_id": session_id,
        "reconciled_lines": reconcile_lines,
        "unresolved_critical": unresolved_critical,
        "status": status,
        "total_items": int(s.get("total_items", 0) or 0),
        "items_with_diff": int(s.get("items_with_diff", 0) or 0),
        "items_critical": int(s.get("items_critical", 0) or 0),
        "started_at": s.get("started_at", ""),
        "finished_at": now_iso,
    }
    result["cloud_uploaded"] = upload_periodic_diff_to_cloud(session_id, result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  差异行：解释 / 解锁
# ─────────────────────────────────────────────────────────────────────────────

def explain_line(line_id: int, *, explanation: str,
                 operator_id: str = "SYSTEM",
                 mark_resolved: bool = True) -> None:
    """老板给一行 critical 差异写解释（如"员工调拨/赠送/破损"）。
    可选直接标 resolved（不再阻塞 finalize_session）。"""
    fields = ["explanation=?"]
    params: list = [(explanation or "").strip()]
    if mark_resolved:
        fields.append("resolved_at=?")
        params.append(_dt.datetime.now().isoformat(timespec="seconds"))
    params.append(line_id)
    db.execute(
        f"UPDATE inventory_stocktake_lines SET {', '.join(fields)} WHERE line_id=?",
        tuple(params),
    )


def unlock_line(line_id: int) -> None:
    """撤销锁定（清掉 locked_at），让 SKU 重新可用。"""
    db.execute(
        "UPDATE inventory_stocktake_lines SET locked_at=NULL WHERE line_id=?",
        (line_id,),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  查询：差异列表 / 关联时间线 / 已锁定 SKU
# ─────────────────────────────────────────────────────────────────────────────

def list_critical_lines(session_id: Optional[str] = None,
                        only_unresolved: bool = True) -> list[dict]:
    """超 5% 差异的行（默认只看未解释 / 未解决的）。session_id 为空时查所有会话。"""
    sql = (
        """SELECT l.line_id, l.session_id, l.item_id,
                  l.book_qty, l.counted_qty, l.diff_qty, l.diff_rate,
                  l.is_critical, l.explanation, l.locked_at, l.resolved_at,
                  i.name, i.unit, i.category,
                  s.started_at, s.session_type
           FROM inventory_stocktake_lines l
           LEFT JOIN inventory_items i ON i.item_id = l.item_id
           LEFT JOIN inventory_stocktake_sessions s ON s.session_id = l.session_id
           WHERE l.is_critical=1"""
    )
    params: list = []
    if session_id:
        sql += " AND l.session_id=?"
        params.append(session_id)
    if only_unresolved:
        sql += " AND (l.explanation IS NULL OR l.explanation='') AND l.resolved_at IS NULL"
    sql += " ORDER BY l.diff_rate DESC, l.line_id DESC"
    rows = db.execute(sql, tuple(params)).fetchall()
    out: list[dict] = []
    for r in rows:
        out.append({
            "line_id": r[0], "session_id": r[1], "item_id": r[2],
            "book_qty": int(r[3] or 0), "counted_qty": int(r[4] or 0),
            "diff_qty": int(r[5] or 0), "diff_rate": float(r[6] or 0),
            "is_critical": bool(int(r[7] or 0)),
            "explanation": r[8] or "", "locked_at": r[9],
            "resolved_at": r[10],
            "name": r[11] or r[2], "unit": r[12] or "件",
            "category": r[13] or "shop",
            "session_started_at": r[14] or "",
            "session_type": r[15] or "PERIODIC",
        })
    return out


def list_sessions(limit: int = 20) -> list[dict]:
    rows = db.execute(
        """SELECT session_id, session_type, started_at, finished_at,
                  operator_id, status, total_items, items_with_diff, items_critical, note
           FROM inventory_stocktake_sessions
           ORDER BY started_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [{
        "session_id": r[0], "session_type": r[1] or "",
        "started_at": r[2] or "", "finished_at": r[3] or "",
        "operator_id": r[4] or "", "status": r[5] or "",
        "total_items": int(r[6] or 0), "items_with_diff": int(r[7] or 0),
        "items_critical": int(r[8] or 0), "note": r[9] or "",
    } for r in rows]


def session_lines(session_id: str) -> list[dict]:
    rows = db.execute(
        """SELECT l.line_id, l.item_id, i.name, i.unit, i.category,
                  l.book_qty, l.counted_qty, l.diff_qty, l.diff_rate,
                  l.is_critical, l.explanation, l.locked_at, l.resolved_at
           FROM inventory_stocktake_lines l
           LEFT JOIN inventory_items i ON i.item_id = l.item_id
           WHERE l.session_id=?
           ORDER BY l.is_critical DESC, l.diff_rate DESC, l.line_id ASC""",
        (session_id,),
    ).fetchall()
    return [{
        "line_id": r[0], "item_id": r[1], "name": r[2] or r[1],
        "unit": r[3] or "件", "category": r[4] or "shop",
        "book_qty": int(r[5] or 0), "counted_qty": int(r[6] or 0),
        "diff_qty": int(r[7] or 0), "diff_rate": float(r[8] or 0),
        "is_critical": bool(int(r[9] or 0)), "explanation": r[10] or "",
        "locked_at": r[11], "resolved_at": r[12],
    } for r in rows]


def item_timeline(item_id: str, *, limit: int = 50) -> list[dict]:
    """某个 SKU 的最近流水（推给 UI / 老板做解释依据）。"""
    rows = db.execute(
        """SELECT move_id, move_type, qty_change, unit_cost,
                  related_room, related_order, operator_id, note, created_at
           FROM inventory_movements WHERE item_id=?
           ORDER BY created_at DESC LIMIT ?""",
        (item_id, limit),
    ).fetchall()
    return [{
        "move_id": r[0], "move_type": r[1], "qty_change": int(r[2] or 0),
        "unit_cost": float(r[3] or 0), "related_room": r[4] or "",
        "related_order": r[5] or "", "operator_id": r[6] or "",
        "note": r[7] or "", "created_at": r[8] or "",
    } for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
#  自动盘点（如果接到云端推送可以直接调用）
# ─────────────────────────────────────────────────────────────────────────────

def is_sku_locked(item_id: str) -> bool:
    row = db.execute(
        """SELECT 1 FROM inventory_stocktake_lines
           WHERE item_id=? AND is_critical=1 AND resolved_at IS NULL
           LIMIT 1""",
        (item_id,),
    ).fetchone()
    return bool(row)


def summarize_session(session_id: str) -> dict:
    row = db.execute(
        """SELECT total_items, items_with_diff, items_critical, status, started_at, finished_at
           FROM inventory_stocktake_sessions WHERE session_id=?""",
        (session_id,),
    ).fetchone()
    if not row:
        return {}
    return {
        "total_items": int(row[0] or 0), "items_with_diff": int(row[1] or 0),
        "items_critical": int(row[2] or 0), "status": row[3] or "",
        "started_at": row[4] or "", "finished_at": row[5] or "",
    }


def format_telegram_alert(session_id: str) -> str:
    """生成给老板用的差异报警文本。"""
    s = summarize_session(session_id) or {}
    lines = list_critical_lines(session_id=session_id, only_unresolved=False)
    head = (
        "📛 *账实差异盘点报警*\n"
        f"会话 ID：`{session_id[:12]}…`\n"
        f"开始时间：{s.get('started_at', '')}\n"
        f"参与 SKU：{s.get('total_items', 0)} / 有差异：{s.get('items_with_diff', 0)} / "
        f"≥5% 报警：{s.get('items_critical', 0)}\n"
    )
    if not lines:
        return head + "本次盘点无差异，账实一致 ✅"
    body = ["", "*超阈值的 SKU：*"]
    for line in lines[:12]:
        sign = "+" if line["diff_qty"] >= 0 else ""
        body.append(
            f"• {line['name']}（{line['category']}）"
            f" 账面 {line['book_qty']} → 实物 {line['counted_qty']}，"
            f"差 {sign}{line['diff_qty']} {line['unit']}"
            f"（{line['diff_rate'] * 100:.1f}%）"
        )
    if len(lines) > 12:
        body.append(f"  …还有 {len(lines) - 12} 条未列出，请到客户端『账实差异』页面查看")
    body.append("\n请尽快进入客户端逐条解释或调拨修正。超过 24 小时未处理会被自动通报厂家。")
    return head + "\n".join(body)
