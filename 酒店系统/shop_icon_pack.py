# -*- coding: utf-8 -*-
"""[sub-i] 超市图标包 — PNG 优先 + emoji 兜底的统一图标解析层。

设计目标：
  • 前台 (shop_frontdesk) / Telegram (telegram_handlers) 共用一套图标解析逻辑
  • 三级兜底：自定义 PNG (items/{sku}.png) → Telegram 专用图 (items/{sku}_tg.jpg) → emoji
  • 缓存机制避免重复 IO（路径探测与 manifest 加载均缓存）
  • 物理隔离：不依赖 Qt，可被 telegram_handlers (后台线程) 安全调用；
    前台需要 QPixmap/QIcon 时由调用方自行加载（本模块只返回 Path）

加载顺序：
  1. assets/shop/icon_manifest.json（本模块专用，emoji 表 + 分类映射）
  2. assets/shop/items/{SKU}.png / {SKU}_tg.jpg（PNG 优先）
  3. assets/shop/categories/{category}.png（分类图标）

用法：
    from shop_icon_pack import icon_pack
    info = icon_pack.get_icon("NOODLE", category="food")
    # -> {"emoji": "🍜", "icon_path": PosixPath(...), "category": "food", "source": "png"}

    tg_path = icon_pack.get_telegram_icon("NOODLE")
    # -> PosixPath(".../items/NOODLE_tg.jpg") or PosixPath(".../items/NOODLE.png") or None
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 资源根：本文件所在目录 / assets / shop
_SHOP_ROOT = Path(__file__).resolve().parent / "assets" / "shop"


def _resource_base() -> Path:
    """PyInstaller 打包后从 _MEIPASS 读取；开发态从源码目录读取。"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass) / "assets" / "shop"
    return _SHOP_ROOT


class ShopIconPack:
    """超市图标包单例（模块级 icon_pack 实例复用）。

    所有方法均线程安全（只读 + dict 缓存，无写竞争）。
    """

    def __init__(self, manifest_path: Optional[Path] = None) -> None:
        self._root = _resource_base()
        self._manifest_path = manifest_path or (self._root / "icon_manifest.json")
        self._manifest: dict = {}
        self._loaded = False
        # 路径缓存：sku -> Path|None（None 表示已探测过且不存在）
        self._item_icon_cache: dict[str, Optional[Path]] = {}
        self._tg_icon_cache: dict[str, Optional[Path]] = {}
        self._cat_icon_cache: dict[str, Optional[Path]] = {}
        # emoji 解析缓存：sku -> emoji str
        self._emoji_cache: dict[str, str] = {}

    # ── manifest 加载 ──────────────────────────────────────────

    def _ensure_loaded(self) -> dict:
        """惰性加载 icon_manifest.json；失败时返回最小兜底结构。"""
        if self._loaded:
            return self._manifest
        try:
            if self._manifest_path.is_file():
                self._manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            else:
                logger.warning("[shop_icon_pack] manifest 不存在: %s", self._manifest_path)
                self._manifest = {}
        except Exception as exc:
            logger.warning("[shop_icon_pack] manifest 加载失败: %s", exc)
            self._manifest = {}
        # 兜底字段
        self._manifest.setdefault("default_emoji", "📦")
        self._manifest.setdefault("categories", {})
        self._manifest.setdefault("sku_emoji_map", {})
        self._loaded = True
        return self._manifest

    def reload(self) -> None:
        """强制重载 manifest（开发态或测试用）。"""
        self._loaded = False
        self._item_icon_cache.clear()
        self._tg_icon_cache.clear()
        self._cat_icon_cache.clear()
        self._emoji_cache.clear()
        self._ensure_loaded()

    # ── emoji 解析 ────────────────────────────────────────────

    def get_emoji(self, sku: str, category: str = "") -> str:
        """解析 emoji：sku 精确 > category 兜底 > default。

        优先级：
          1. sku_emoji_map[SKU] 精确匹配
          2. categories[category].emoji 分类兜底
          3. default_emoji
        """
        sku_u = (sku or "").strip().upper()
        cache_key = f"{sku_u}|{category}"
        cached = self._emoji_cache.get(cache_key)
        if cached is not None:
            return cached
        m = self._ensure_loaded()
        emoji = ""
        if sku_u:
            emoji = (m.get("sku_emoji_map") or {}).get(sku_u, "") or ""
        if not emoji and category:
            cat_info = (m.get("categories") or {}).get((category or "").strip(), {})
            emoji = cat_info.get("emoji", "") or ""
        if not emoji:
            emoji = m.get("default_emoji", "📦") or "📦"
        self._emoji_cache[cache_key] = emoji
        return emoji

    # ── PNG 路径解析 ──────────────────────────────────────────

    def get_item_icon(self, sku: str) -> Optional[Path]:
        """返回 items/{SKU}.png 路径，不存在返回 None。

        - SKU 自动大写、去空白
        - 结果缓存（包括 None，避免重复 stat）
        """
        sku_u = (sku or "").strip().upper()
        if not sku_u:
            return None
        if sku_u in self._item_icon_cache:
            return self._item_icon_cache[sku_u]
        p = self._root / "items" / f"{sku_u}.png"
        result = p if p.is_file() else None
        self._item_icon_cache[sku_u] = result
        return result

    def get_telegram_icon(self, sku: str) -> Optional[Path]:
        """返回 Telegram 专用图：优先 items/{SKU}_tg.jpg，回退 items/{SKU}.png。

        - 电报推荐正方形 jpg（体积小、压缩好）
        - 没有 _tg.jpg 时用 .png 也兼容（Telegram 支持 PNG）
        - 都没有返回 None（调用方应走 emoji 大字号兜底）
        """
        sku_u = (sku or "").strip().upper()
        if not sku_u:
            return None
        if sku_u in self._tg_icon_cache:
            return self._tg_icon_cache[sku_u]
        root_items = self._root / "items"
        tg_jpg = root_items / f"{sku_u}_tg.jpg"
        if tg_jpg.is_file():
            self._tg_icon_cache[sku_u] = tg_jpg
            return tg_jpg
        png = root_items / f"{sku_u}.png"
        if png.is_file():
            self._tg_icon_cache[sku_u] = png
            return png
        self._tg_icon_cache[sku_u] = None
        return None

    def get_category_icon(self, category: str) -> Optional[Path]:
        """返回 categories/{category}.png 路径；category 无效或文件缺失返回 None。"""
        cid = (category or "").strip().lower()
        if not cid:
            return None
        if cid in self._cat_icon_cache:
            return self._cat_icon_cache[cid]
        # 从 manifest 取 icon 文件名（兜底用 {cid}.png）
        m = self._ensure_loaded()
        cat_info = (m.get("categories") or {}).get(cid, {})
        icon_file = cat_info.get("icon") or f"{cid}.png"
        p = self._root / "categories" / icon_file
        result = p if p.is_file() else None
        self._cat_icon_cache[cid] = result
        return result

    # ── 组合 API ─────────────────────────────────────────────

    def get_icon(
        self, sku: str, category: str = ""
    ) -> dict:
        """一站式图标信息：emoji + 可选 PNG 路径 + 来源标记。

        返回结构：
            {
              "emoji": "🍜",            # 兜底用，永不为空
              "icon_path": Path|None,   # 前台 PNG 优先
              "category": "food",       # 回显分类（可能为空串）
              "source": "png"|"emoji",  # 调用方判断展示策略
            }
        """
        emoji = self.get_emoji(sku, category)
        png = self.get_item_icon(sku)
        return {
            "emoji": emoji,
            "icon_path": png,
            "category": (category or "").strip(),
            "source": "png" if png is not None else "emoji",
        }

    def get_telegram_payload(
        self, sku: str, category: str = ""
    ) -> dict:
        """Telegram 发图专用：优先 _tg.jpg，回退 .png，再回退 emoji。

        返回结构：
            {
              "photo_path": Path|None,  # 有图就发图
              "emoji": "🍜",            # 无图时大字号 caption
              "source": "tg_jpg"|"png"|"emoji",
            }
        """
        tg = self.get_telegram_icon(sku)
        emoji = self.get_emoji(sku, category)
        if tg is None:
            return {"photo_path": None, "emoji": emoji, "source": "emoji"}
        src = "tg_jpg" if tg.name.endswith("_tg.jpg") else "png"
        return {"photo_path": tg, "emoji": emoji, "source": src}

    # ── 元信息 ────────────────────────────────────────────────

    def categories(self) -> dict:
        """返回 categories dict（id -> {emoji, label_cn, label_en, icon, color})."""
        return self._ensure_loaded().get("categories", {}) or {}

    def category_label(self, category: str, *, lang: str = "cn") -> str:
        """分类本地化标签；找不到返回 category 原值。"""
        cid = (category or "").strip()
        if not cid:
            return ""
        info = self.categories().get(cid, {})
        key = f"label_{lang}" if lang in ("cn", "en") else "label_cn"
        return str(info.get(key) or cid)

    def sku_count(self) -> int:
        """manifest 中已登记 emoji 的 SKU 数量（统计用）。"""
        return len(self._ensure_loaded().get("sku_emoji_map", {}) or {})


# ── 模块级单例 ────────────────────────────────────────────────

icon_pack = ShopIconPack()


def get_icon_pack() -> ShopIconPack:
    """获取模块级单例（与 icon_pack 等价；提供函数式入口便于 mock）。"""
    return icon_pack
