# [UI-REDESIGN] 2026-06-20 v4: 金线宽度/色值对齐 design_tokens_v4
"""[v4 实现层] 本文件是 v4 产品品牌常量的实际实现（非 re-export 垫片）。

Solid PMS — 产品品牌常量（打包名、窗口标题、关于页等统一引用）

v4 变化:
- GOLD_THREAD_BRAND.width_px 改为 4
- 新增 color 字段，对齐 ColorPrimary.GOLD_STANDARD

保留目的：
- 提供完整品牌常量 + load_brand_json()/effective_brand() 实现。
- 由 brand_config.py（纯 re-export 垫片）兼容旧 import 路径。

注意：本文件不是 re-export 垫片，是 v4 实现本体。删了它 brand_config.py 全部
re-export 崩溃。v4 命名表明它是 v3 重构后的新版本，并非死代码。

当前引用方（rg 查）：
- brand_config.py:5 `from brand_config_v4 import (APP_NAME, APP_NAME_FULL, ...)`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_BRAND_GOLD = "#C4A86A"

_BRAND_DEFAULTS: dict[str, Any] = {
    "app_name": "Solid",
    "app_name_full": "Solid 酒店管理系统",
    "app_name_en": "Solid Hotel PMS",
    "tagline": "专注中小酒店 · 厂家直供",
    "vendor_company": "xxxxx",
    "vendor_publisher": "xxxxx",
    "vendor_website": "https://www.example.com",
    "vendor_support_url": "https://www.example.com/support",
    "vendor_contact_wechat": "xxxxx",
    "vendor_contact_phone": "xxx-xxxx-xxxx",
    "version": "1.0.0",
    "copyright_year": "2026",
    "selling_points": [
        "厂家直供 · 省去中间商",
        "房态 / 收银 / 门锁 / 报表 一套搞定",
        "数据本机加密 · 不上传不外泄",
        "兼容 14 个品牌门锁系统接管",
        "厂家工程师 7×24 微信售后",
    ],
}

LEGACY_ALIASES = ("ShadowGuard", "Shadow-Guard", "影盾")


def _candidate_brand_paths() -> list[Path]:
    """按开发目录、安装目录、打包后内部目录依次寻找品牌配置。"""
    paths: list[Path] = []
    try:
        exe_dir = Path(sys.executable).resolve().parent
        paths.append(exe_dir / "brand.json")
    except Exception:
        pass
    paths.append(Path(__file__).resolve().parent / "brand.json")
    try:
        exe_dir = Path(sys.executable).resolve().parent
        paths.append(exe_dir / "_internal" / "brand.json")
    except Exception:
        pass
    try:
        bundle_dir = Path(getattr(sys, "_MEIPASS"))
        paths.append(bundle_dir / "brand.json")
    except Exception:
        pass
    paths.append(Path.cwd() / "brand.json")
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def load_brand_json() -> dict[str, Any]:
    """启动时读取 brand.json；缺失或坏文件时回退到默认值。"""
    data: dict[str, Any] = {}
    for path in _candidate_brand_paths():
        if not path.is_file():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
                break
        except Exception:
            data = {}
            break

    merged = dict(_BRAND_DEFAULTS)
    merged.update({k: v for k, v in data.items() if v not in (None, "")})
    points = merged.get("selling_points")
    if not isinstance(points, list):
        merged["selling_points"] = list(_BRAND_DEFAULTS["selling_points"])
    else:
        merged["selling_points"] = [str(x) for x in points][:5]
    return merged


_BRAND = load_brand_json()

# 老常量继续导出，避免已有 import 失效。
APP_NAME = str(_BRAND["app_name"])
APP_NAME_FULL = str(_BRAND["app_name_full"])
APP_TAGLINE = str(_BRAND["tagline"])
APP_VENDOR = str(_BRAND["vendor_company"])
APP_VERSION = str(_BRAND["version"])


def brand() -> dict[str, Any]:
    """返回当前品牌配置副本，新代码优先用这个。"""
    return dict(_BRAND)


def backup_file_prefix() -> str:
    """导出备份/密钥包默认文件名前缀。"""
    return f"{APP_NAME}_Backup"


# ── 金线品牌元素（v4 — 跨主题通用，色值对齐 design_tokens_v4）──────────
GOLD_THREAD_BRAND: dict[str, Any] = {
    "concept": "金线",
    "description": "Solid PMS 品牌DNA — 跨主题通用装饰线，体现官方辨识度",
    "color": _BRAND_GOLD,
    "width_px": 4,                    # 横栏左侧金线宽度
    "sidebar_active_width_px": 2,     # 侧栏激活条宽度
    "kpi_top_width_px": 1,            # KPI 卡片顶部装饰线宽度
    "dialog_separator_width_px": 1,   # 弹窗标题底部分隔线宽度
    "flow_arrow_color_key": "gold_thread",   # 流程条箭头颜色 token key
    "sidebar_active_color_key": "gold_thread",  # 侧栏激活条颜色 token key
    "section_bar_left_color_key": "gold_thread",  # 横栏左装饰线颜色 token key
}


def effective_brand(db=None) -> dict:
    """酒店端显示品牌；程序/安装包仍保留原名，避免影响升级路径。"""
    try:
        if db is None:
            from database import db as _db
            db = _db
        hotel_name = (db.get_config("hotel_name") or "").strip()
        short_name = (db.get_config("hotel_short_name") or "").strip()
        logo_path = (db.get_config("hotel_logo_path") or "").strip()
        vendor_visible = (db.get_config("vendor_entry_visible") or "0").strip() == "1"
    except Exception:
        hotel_name = ""
        short_name = ""
        logo_path = ""
        vendor_visible = False
    title = hotel_name or APP_NAME_FULL
    short = short_name or hotel_name or APP_NAME
    return {
        "title": title,
        "short": short,
        "logo_path": logo_path,
        "vendor_visible": vendor_visible,
    }
