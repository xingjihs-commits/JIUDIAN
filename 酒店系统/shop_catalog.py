# -*- coding: utf-8 -*-
"""超市总库 manifest → shop_items 种子同步。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from shop_assets import _resource_base

logger = logging.getLogger(__name__)

_MANIFEST_CACHE: dict | None = None


def manifest_path() -> Path:
    return _resource_base() / "manifest.json"


def load_manifest(*, force: bool = False) -> dict:
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is not None and not force:
        return _MANIFEST_CACHE
    path = manifest_path()
    if not path.is_file():
        _MANIFEST_CACHE = {"categories": [], "items": []}
        return _MANIFEST_CACHE
    try:
        _MANIFEST_CACHE = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[shop_catalog] manifest 读取失败: %s", exc)
        _MANIFEST_CACHE = {"categories": [], "items": []}
    return _MANIFEST_CACHE


def category_label(category_id: str, manifest: dict | None = None) -> str:
    data = manifest or load_manifest()
    cid = (category_id or "").strip()
    for cat in data.get("categories") or []:
        if str(cat.get("id") or "") == cid:
            return str(cat.get("name") or cid)
    return cid or "未分类"


def category_map(manifest: dict | None = None) -> dict[str, str]:
    data = manifest or load_manifest()
    out: dict[str, str] = {}
    for cat in data.get("categories") or []:
        cid = str(cat.get("id") or "").strip()
        if cid:
            out[cid] = str(cat.get("name") or cid)
    return out


def iter_manifest_items(manifest: dict | None = None) -> list[dict[str, Any]]:
    data = manifest or load_manifest()
    items = list(data.get("items") or [])
    items.sort(key=lambda x: (int(x.get("sort") or 9999), str(x.get("name") or "")))
    return items


def seed_shop_from_manifest(db, *, insert_only: bool = True) -> int:
    """把 manifest 条目写入 shop_items；已有 SKU 默认不覆盖 listed/stock/price。"""
    manifest = load_manifest()
    inserted = 0
    for it in iter_manifest_items(manifest):
        sku = str(it.get("sku") or "").strip().upper()
        if not sku:
            continue
        cat_id = str(it.get("category") or "")
        cat_name = category_label(cat_id, manifest)
        name = str(it.get("name") or sku)
        emoji = str(it.get("emoji") or "📦")
        price = float(it.get("price") or 0)
        cost = float(it.get("cost") or 0)
        pack = str(it.get("pack_label") or "件")
        upp = max(1, int(it.get("units_per_pack") or 1))
        listed = 1 if int(it.get("default_listed") or 0) else 0
        sort_order = int(it.get("sort") or 9999)
        tg_label = str(it.get("telegram_label") or name)[:12]
        # [sub-i] 图标包体系：icon_key 默认取 SKU（对应 items/{SKU}.png）；
        # description 暂留空（manifest.json 暂无 description 字段，前台可后填）。
        icon_key = str(it.get("icon_key") or sku)
        description = str(it.get("description") or "")

        existing = db.execute("SELECT sku FROM shop_items WHERE sku=?", (sku,)).fetchone()
        if existing:
            if not insert_only:
                db.execute(
                    """
                    UPDATE shop_items SET
                        name=?, category=?, emoji=?, cost_price=?, price=?,
                        pack_label=?, units_per_pack=?, sort_order=?, telegram_label=?,
                        icon_key=?, description=?
                    WHERE sku=?
                    """,
                    (name, cat_name, emoji, cost, price, pack, upp, sort_order, tg_label,
                     icon_key, description, sku),
                )
            continue

        db.execute(
            """
            INSERT INTO shop_items
            (sku, name, category, price, cost_price, emoji, pack_label, units_per_pack,
             stock, listed, sort_order, telegram_label, icon_key, description)
            VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?)
            """,
            (sku, name, cat_name, price, cost, emoji, pack, upp, listed, sort_order, tg_label,
             icon_key, description),
        )
        inserted += 1

    logger.info("[shop_catalog] manifest 种子：新增 %d 条", inserted)
    return inserted
