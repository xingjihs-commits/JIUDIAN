"""ui/branding/brand.py — 品牌配置"""

from __future__ import annotations

from brand_config_v4 import APP_NAME, APP_NAME_FULL

# BRAND_COLOR 硬编码，避免从 ui.tokens.colors (DEPRECATED) 导入
BRAND_COLOR = "#C4A86A"  # ColorPrimary.GOLD_STANDARD


def effective_brand(db=None) -> dict:
    """返回当前生效的品牌配置。"""
    try:
        if db is None:
            from database import db as _db
            db = _db
        custom_title = db.get_config("brand_title") or ""
        custom_short = db.get_config("brand_short") or ""
        return {
            "title": custom_title or APP_NAME_FULL,
            "short": custom_short or APP_NAME,
            "color": BRAND_COLOR,
        }
    except Exception:
        return {"title": APP_NAME_FULL, "short": APP_NAME, "color": BRAND_COLOR}
