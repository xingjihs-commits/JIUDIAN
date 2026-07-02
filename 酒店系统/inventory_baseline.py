"""
inventory_baseline.py — C0-beta 期初基线 + 库存流水哈希链

职责：
1. 维护 `inventory_items` 字典（超市 SKU 与 shop_items 双写；客房消耗品由向导新建）。
2. 写 `inventory_movements` 流水，并维护 `prev_hash → row_hash` 哈希链（任意中间记录被改链会断）。
3. 生成期初快照 `inventory_baseline_snapshots`，落地 SHA256 哈希；可选上云。
4. 计算账面库存 = 期初库存 + 所有流水累计 qty_change。

商业意图：
- 老板或员工不能直接用数据库工具改库存数字；改了哈希链立刻断。
- 期初快照是"从此刻起开始有证据"的锚点；以前的错账不追。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import uuid
from typing import Iterable, Optional

from cloud_security import signature_headers


# ── 库存流水移动类型常量 ───────────────────────────────────────────────────────
MOVE_OPENING = "OPENING"          # 期初盘点写入
MOVE_PURCHASE = "PURCHASE"        # 采购入库
MOVE_SALE = "SALE"                # 超市销售
MOVE_ROOM_CONSUME = "ROOM_CONSUME"  # 客房消耗品被消耗
MOVE_LOSS = "LOSS"                # 报损
MOVE_ADJUST = "ADJUST"            # 人工调整（差异核销）
MOVE_PERIODIC_RECONCILE = "PERIODIC_RECONCILE"  # 周期盘点对账（差异回填）

ALL_MOVE_TYPES = (
    MOVE_OPENING,
    MOVE_PURCHASE,
    MOVE_SALE,
    MOVE_ROOM_CONSUME,
    MOVE_LOSS,
    MOVE_ADJUST,
    MOVE_PERIODIC_RECONCILE,
)

CATEGORY_SHOP = "shop"
CATEGORY_CONSUMABLE = "consumable"


# ─────────────────────────────────────────────────────────────────────────────
#  inventory_items 字典管理
# ─────────────────────────────────────────────────────────────────────────────

def make_item_id(category: str, source_key: str) -> str:
    """item_id 命名规则：shop:<sku> 或 cons:<uuid 前 8 位>"""
    cat = (category or "").strip().lower()
    src = (source_key or "").strip()
    if cat == CATEGORY_SHOP:
        return f"shop:{src}"
    if cat == CATEGORY_CONSUMABLE:
        if not src:
            src = uuid.uuid4().hex[:8]
        return f"cons:{src}"
    return f"misc:{src or uuid.uuid4().hex[:8]}"


def upsert_item(db, *, item_id: str, category: str, name: str,
                source_sku: str = "", unit: str = "件",
                cost_price: float = 0.0, sale_price: float = 0.0,
                reorder_threshold: int = 0,
                in_monitoring: bool = True,
                skip_reason: str = "") -> str:
    """新增或更新一个库存条目。返回 item_id。"""
    now = _dt.datetime.now().isoformat(timespec="seconds")
    row = db.execute(
        "SELECT item_id FROM inventory_items WHERE item_id=?", (item_id,)
    ).fetchone()
    if row:
        db.execute(
            """UPDATE inventory_items SET
                  category=?, source_sku=?, name=?, unit=?, cost_price=?, sale_price=?,
                  reorder_threshold=?, in_monitoring=?, skip_reason=?, updated_at=?
               WHERE item_id=?""",
            (category, source_sku, name, unit, float(cost_price), float(sale_price),
             int(reorder_threshold), 1 if in_monitoring else 0,
             skip_reason or "", now, item_id),
        )
    else:
        db.execute(
            """INSERT INTO inventory_items
               (item_id, category, source_sku, name, unit, cost_price, sale_price,
                reorder_threshold, in_monitoring, skip_reason, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item_id, category, source_sku, name, unit, float(cost_price),
             float(sale_price), int(reorder_threshold),
             1 if in_monitoring else 0, skip_reason or "", now, now),
        )
    return item_id


def sync_shop_items_to_inventory(db) -> int:
    """把 shop_items 表中的所有 SKU 镜像到 inventory_items（仅新增/更新元数据，不动 stock）。
    返回同步条数。"""
    rows = db.execute(
        """SELECT sku, name,
                  COALESCE(price, 0), COALESCE(cost_price, 0),
                  COALESCE(pack_label, '件')
           FROM shop_items"""
    ).fetchall()
    count = 0
    for sku, name, sale, cost, pack_label in rows:
        sku = (sku or "").strip()
        if not sku:
            continue
        item_id = make_item_id(CATEGORY_SHOP, sku)
        upsert_item(
            db,
            item_id=item_id,
            category=CATEGORY_SHOP,
            name=name or sku,
            source_sku=sku,
            unit=pack_label or "件",
            cost_price=float(cost or 0),
            sale_price=float(sale or 0),
        )
        count += 1
    return count


# ─────────────────────────────────────────────────────────────────────────────
#  inventory_movements 哈希链
# ─────────────────────────────────────────────────────────────────────────────

def _latest_chain_head(db) -> str:
    """全局哈希链最新头部。返回最近一条 row_hash，没有则空串。
    用 ROWID 严格按"插入顺序"取，避免同一秒多条 created_at 相同时乱序断链。"""
    row = db.execute(
        "SELECT row_hash FROM inventory_movements ORDER BY ROWID DESC LIMIT 1"
    ).fetchone()
    return (row[0] or "") if row else ""


def _compute_row_hash(prev_hash: str, payload: dict, timestamp: str) -> str:
    payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    raw = f"{prev_hash}|{payload_json}|{timestamp}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append_movement(db, *, item_id: str, move_type: str, qty_change: int,
                    unit_cost: float = 0.0, related_room: str = "",
                    related_order: str = "", operator_id: str = "SYSTEM",
                    note: str = "") -> dict:
    """追加一条库存流水，自动接哈希链。
    返回 {move_id, prev_hash, row_hash, created_at}。"""
    if move_type not in ALL_MOVE_TYPES:
        raise ValueError(f"未知的库存流水类型: {move_type}")
    if int(qty_change) == 0 and move_type != MOVE_OPENING:
        # 期初允许 0（说明该 SKU 当前实物就是 0）；其他类型 0 数量无意义
        raise ValueError("流水数量不能为 0（期初除外）")

    now = _dt.datetime.now().isoformat(timespec="seconds")
    prev_hash = _latest_chain_head(db)
    move_id = uuid.uuid4().hex
    payload = {
        "move_id": move_id,
        "item_id": item_id,
        "move_type": move_type,
        "qty_change": int(qty_change),
        "unit_cost": float(unit_cost or 0),
        "related_room": related_room or "",
        "related_order": related_order or "",
        "operator_id": operator_id or "SYSTEM",
        "note": note or "",
    }
    row_hash = _compute_row_hash(prev_hash, payload, now)

    db.execute(
        """INSERT INTO inventory_movements
           (move_id, item_id, move_type, qty_change, unit_cost,
            related_room, related_order, operator_id, note,
            prev_hash, row_hash, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (move_id, item_id, move_type, int(qty_change), float(unit_cost or 0),
         related_room, related_order, operator_id, note,
         prev_hash, row_hash, now),
    )
    return {
        "move_id": move_id,
        "prev_hash": prev_hash,
        "row_hash": row_hash,
        "created_at": now,
    }


def verify_chain(db) -> dict:
    """完整校验哈希链。返回 {ok, total, broken_at}。
    broken_at = None 表示无断点；否则给出第一条断链的 move_id。"""
    rows = db.execute(
        """SELECT move_id, item_id, move_type, qty_change, unit_cost,
                  related_room, related_order, operator_id, note,
                  prev_hash, row_hash, created_at
           FROM inventory_movements
           ORDER BY ROWID ASC"""
    ).fetchall()
    prev = ""
    for r in rows:
        (move_id, item_id, move_type, qty_change, unit_cost,
         related_room, related_order, operator_id, note,
         prev_hash, row_hash, created_at) = r
        payload = {
            "move_id": move_id,
            "item_id": item_id,
            "move_type": move_type,
            "qty_change": int(qty_change),
            "unit_cost": float(unit_cost or 0),
            "related_room": related_room or "",
            "related_order": related_order or "",
            "operator_id": operator_id or "SYSTEM",
            "note": note or "",
        }
        expected = _compute_row_hash(prev, payload, created_at)
        if prev_hash != prev or row_hash != expected:
            return {"ok": False, "total": len(rows), "broken_at": move_id}
        prev = row_hash
    return {"ok": True, "total": len(rows), "broken_at": None}


# ─────────────────────────────────────────────────────────────────────────────
#  账面库存计算
# ─────────────────────────────────────────────────────────────────────────────

def book_qty(db, item_id: str, *, until: Optional[str] = None) -> int:
    """账面库存 = 该 item 的所有流水 qty_change 累计。
    until 是 ISO 时间戳（含），用于"截止某一刻"快照计算。"""
    if until:
        row = db.execute(
            """SELECT COALESCE(SUM(qty_change), 0) FROM inventory_movements
               WHERE item_id=? AND created_at<=?""",
            (item_id, until),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT COALESCE(SUM(qty_change), 0) FROM inventory_movements WHERE item_id=?",
            (item_id,),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def book_qty_for_shop(db, sku: str) -> int:
    """超市 SKU：优先用 inventory_movements 累计；为兼容老数据，若无任何流水则回落 shop_items.stock。"""
    item_id = make_item_id(CATEGORY_SHOP, sku)
    has_any = db.execute(
        "SELECT 1 FROM inventory_movements WHERE item_id=? LIMIT 1", (item_id,)
    ).fetchone()
    if has_any:
        return book_qty(db, item_id)
    row = db.execute("SELECT COALESCE(stock,0) FROM shop_items WHERE sku=?", (sku,)).fetchone()
    return int(row[0] or 0) if row else 0


# ─────────────────────────────────────────────────────────────────────────────
#  期初快照
# ─────────────────────────────────────────────────────────────────────────────

def list_all_items(db) -> list[dict]:
    """返回 inventory_items 全表（dict 列表）。"""
    rows = db.execute(
        """SELECT item_id, category, source_sku, name, unit, cost_price, sale_price,
                  reorder_threshold, in_monitoring, skip_reason
           FROM inventory_items ORDER BY category, name"""
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "item_id": r[0],
            "category": r[1],
            "source_sku": r[2] or "",
            "name": r[3] or "",
            "unit": r[4] or "件",
            "cost_price": float(r[5] or 0),
            "sale_price": float(r[6] or 0),
            "reorder_threshold": int(r[7] or 0),
            "in_monitoring": bool(int(r[8] or 0)),
            "skip_reason": r[9] or "",
        })
    return out


def list_room_type_standards(db) -> list[dict]:
    rows = db.execute(
        """SELECT type_id, item_id, standard_qty, trigger_event
           FROM room_type_consumable_standards ORDER BY type_id, item_id"""
    ).fetchall()
    return [
        {"type_id": r[0], "item_id": r[1], "standard_qty": int(r[2] or 0), "trigger_event": r[3] or "CHECKIN"}
        for r in rows
    ]


def build_baseline_snapshot(db, operator_id: str, *, note: str = "") -> dict:
    """生成期初快照：把当前 inventory_items + 房型标准 + 所有 OPENING 流水序列化打哈希落盘。"""
    items = list_all_items(db)
    standards = list_room_type_standards(db)
    opening_rows = db.execute(
        """SELECT move_id, item_id, qty_change, unit_cost, operator_id, created_at, row_hash
           FROM inventory_movements WHERE move_type=? ORDER BY created_at""",
        (MOVE_OPENING,),
    ).fetchall()
    openings = [{
        "move_id": r[0], "item_id": r[1], "qty_change": int(r[2] or 0),
        "unit_cost": float(r[3] or 0), "operator_id": r[4] or "",
        "created_at": r[5] or "", "row_hash": r[6] or "",
    } for r in opening_rows]

    chain_state = verify_chain(db)

    payload = {
        "schema_version": 1,
        "captured_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "operator_id": operator_id or "SYSTEM",
        "items": items,
        "room_type_standards": standards,
        "openings": openings,
        "chain_state": chain_state,
    }
    payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    snap_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    snap_id = uuid.uuid4().hex

    monitored = sum(1 for it in items if it.get("in_monitoring"))
    skipped = len(items) - monitored

    db.execute(
        """INSERT INTO inventory_baseline_snapshots
           (snapshot_id, operator_id, snapshot_json, snapshot_hash,
            items_count, monitored_count, skipped_count, note)
           VALUES (?,?,?,?,?,?,?,?)""",
        (snap_id, operator_id or "SYSTEM", payload_json, snap_hash,
         len(items), monitored, skipped, note or ""),
    )

    # 标记期初盘点完成
    db.set_config("initial_stocktake_done_at", payload["captured_at"])
    db.set_config("initial_stocktake_snapshot_id", snap_id)
    db.set_config("initial_stocktake_snapshot_hash", snap_hash)

    return {
        "snapshot_id": snap_id,
        "snapshot_hash": snap_hash,
        "captured_at": payload["captured_at"],
        "items_count": len(items),
        "monitored_count": monitored,
        "skipped_count": skipped,
    }


def latest_snapshot(db) -> Optional[dict]:
    row = db.execute(
        """SELECT snapshot_id, snapshot_time, snapshot_hash, items_count,
                  monitored_count, skipped_count, cloud_uploaded_at
           FROM inventory_baseline_snapshots
           ORDER BY snapshot_time DESC LIMIT 1"""
    ).fetchone()
    if not row:
        return None
    return {
        "snapshot_id": row[0],
        "snapshot_time": row[1],
        "snapshot_hash": row[2],
        "items_count": int(row[3] or 0),
        "monitored_count": int(row[4] or 0),
        "skipped_count": int(row[5] or 0),
        "cloud_uploaded_at": row[6],
    }


def upload_snapshot_to_cloud(db, snapshot_id: str) -> bool:
    """把期初快照摘要推送给云端。失败不抛异常（厂家可后台补传）。"""
    try:
        import urllib.request as _ur
        import urllib.error as _ue

        worker = (db.get_config("cloud_worker_url") or "").strip().rstrip("/")
        if not worker:
            return False
        hotel_id = (db.get_config("hotel_id") or db.get_config("hotel_name") or "UNKNOWN").strip()
        row = db.execute(
            "SELECT snapshot_hash, items_count, monitored_count, skipped_count, snapshot_time "
            "FROM inventory_baseline_snapshots WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        if not row:
            return False
        payload = {
            "snapshot_id": snapshot_id,
            "hotel_id": hotel_id,
            "snapshot_hash": row[0],
            "items_count": int(row[1] or 0),
            "monitored_count": int(row[2] or 0),
            "skipped_count": int(row[3] or 0),
            "snapshot_time": row[4] or "",
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        url = f"{worker}/api/baseline-snapshot"
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
        db.execute(
            "UPDATE inventory_baseline_snapshots SET cloud_uploaded_at=? WHERE snapshot_id=?",
            (_dt.datetime.now().isoformat(timespec="seconds"), snapshot_id),
        )
    return ok


# ─────────────────────────────────────────────────────────────────────────────
#  房型标准配备
# ─────────────────────────────────────────────────────────────────────────────

def set_room_type_standard(db, *, type_id: str, item_id: str,
                           standard_qty: int, trigger_event: str = "CHECKIN") -> None:
    if not type_id or not item_id:
        return
    db.execute(
        "DELETE FROM room_type_consumable_standards "
        "WHERE type_id=? AND item_id=? AND trigger_event=?",
        (type_id, item_id, trigger_event),
    )
    if int(standard_qty) > 0:
        db.execute(
            """INSERT INTO room_type_consumable_standards
               (type_id, item_id, standard_qty, trigger_event) VALUES (?,?,?,?)""",
            (type_id, item_id, int(standard_qty), trigger_event),
        )


def standards_for_room_type(db, type_id: str, trigger_event: str = "CHECKIN") -> list[dict]:
    rows = db.execute(
        """SELECT s.item_id, s.standard_qty, i.name, i.unit
           FROM room_type_consumable_standards s
           LEFT JOIN inventory_items i ON i.item_id = s.item_id
           WHERE s.type_id=? AND s.trigger_event=?""",
        (type_id, trigger_event),
    ).fetchall()
    return [
        {"item_id": r[0], "standard_qty": int(r[1] or 0),
         "name": r[2] or r[0], "unit": r[3] or "件"}
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  对外便捷 API
# ─────────────────────────────────────────────────────────────────────────────

def record_opening_quantities(db, item_quantities: dict, *,
                              operator_id: str = "OPENING") -> int:
    """期初盘点提交时，把每个 item 的实物数量作为 OPENING 流水写入。
    item_quantities: {item_id: qty}
    返回写入条数。"""
    cnt = 0
    for item_id, qty in (item_quantities or {}).items():
        try:
            q = int(qty)
        except (TypeError, ValueError):
            continue
        # 期初允许 0（明确告诉系统"这个 SKU 当前就是 0"）
        append_movement(
            db,
            item_id=item_id,
            move_type=MOVE_OPENING,
            qty_change=q,
            operator_id=operator_id,
            note="期初盘点",
        )
        # 同步超市表 stock（如果是 shop 类）
        if item_id.startswith("shop:"):
            sku = item_id[5:]
            db.execute("UPDATE shop_items SET stock=? WHERE sku=?", (q, sku))
        cnt += 1
    return cnt


# ─────────────────────────────────────────────────────────────────────────────
#  业务模块统一入口（C0-beta/gamma 的真正落地：把哈希链流水接到日常业务）
# ─────────────────────────────────────────────────────────────────────────────

def ensure_shop_item_registered(db, sku: str) -> str:
    """确保超市 SKU 已镜像到 inventory_items；不存在则从 shop_items 拉一遍。
    返回 item_id（shop:<sku>）。"""
    sku = (sku or "").strip()
    if not sku:
        raise ValueError("SKU 不能为空")
    item_id = make_item_id(CATEGORY_SHOP, sku)
    row = db.execute(
        "SELECT 1 FROM inventory_items WHERE item_id=?", (item_id,)
    ).fetchone()
    if row:
        return item_id
    shop_row = db.execute(
        "SELECT name, COALESCE(cost_price,0), COALESCE(price,0), COALESCE(pack_label,'件') "
        "FROM shop_items WHERE sku=?",
        (sku,),
    ).fetchone()
    if shop_row:
        upsert_item(
            db,
            item_id=item_id,
            category=CATEGORY_SHOP,
            name=shop_row[0] or sku,
            source_sku=sku,
            unit=shop_row[3] or "件",
            cost_price=float(shop_row[1] or 0),
            sale_price=float(shop_row[2] or 0),
        )
    else:
        upsert_item(
            db,
            item_id=item_id,
            category=CATEGORY_SHOP,
            name=sku,
            source_sku=sku,
            unit="件",
        )
    return item_id


def record_shop_movement(db, *, sku: str, move_type: str, qty_change: int,
                         unit_cost: float = 0.0, related_room: str = "",
                         related_order: str = "", operator_id: str = "SYSTEM",
                         note: str = "") -> dict:
    """业务模块统一入口：把超市 SKU 的库存变动写入哈希链。
    - 调用前不需要先 upsert_item，本函数自动从 shop_items 拉一遍元数据。
    - 写完哈希链后不会再去动 shop_items.stock（由业务侧自己负责，避免双扣）。
    返回 append_movement 的结果 dict。"""
    item_id = ensure_shop_item_registered(db, sku)
    return append_movement(
        db,
        item_id=item_id,
        move_type=move_type,
        qty_change=int(qty_change),
        unit_cost=float(unit_cost or 0),
        related_room=related_room or "",
        related_order=related_order or "",
        operator_id=operator_id or "SYSTEM",
        note=note or "",
    )


def record_consumable_movement(db, *, item_id: str, move_type: str, qty_change: int,
                               unit_cost: float = 0.0, related_room: str = "",
                               related_order: str = "", operator_id: str = "SYSTEM",
                               note: str = "") -> dict:
    """客房消耗品（cons:xxx）的统一入口。item_id 须已存在于 inventory_items。"""
    return append_movement(
        db,
        item_id=item_id,
        move_type=move_type,
        qty_change=int(qty_change),
        unit_cost=float(unit_cost or 0),
        related_room=related_room or "",
        related_order=related_order or "",
        operator_id=operator_id or "SYSTEM",
        note=note or "",
    )


def apply_checkin_consumables(db, *, room_id: str, room_type: str,
                              operator_id: str = "SYSTEM",
                              related_order: str = "",
                              note_prefix: str = "入住补给") -> list[dict]:
    """根据 room_type_consumable_standards (trigger_event='CHECKIN') 自动写消耗流水。
    - 每个标准 SKU 写一条 MOVE_ROOM_CONSUME（数量为负的 standard_qty）
    - 如果是超市类 SKU，同步把 shop_items.stock 扣下去
    返回写入的流水列表。"""
    if not room_id or not room_type:
        return []
    standards = standards_for_room_type(db, room_type, trigger_event="CHECKIN")
    out: list[dict] = []
    for std in standards:
        item_id = std.get("item_id") or ""
        q = int(std.get("standard_qty") or 0)
        if not item_id or q <= 0:
            continue
        result = append_movement(
            db,
            item_id=item_id,
            move_type=MOVE_ROOM_CONSUME,
            qty_change=-q,
            related_room=room_id,
            related_order=related_order or "",
            operator_id=operator_id or "SYSTEM",
            note=f"{note_prefix} {room_type}".strip(),
        )
        if item_id.startswith("shop:"):
            sku = item_id[5:]
            db.execute(
                "UPDATE shop_items SET stock = MAX(0, COALESCE(stock,0) - ?) WHERE sku=?",
                (q, sku),
            )
        out.append(result)
    return out
