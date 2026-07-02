# -*- coding: utf-8 -*-
"""runtime/_aliases — v1 → v2 token 别名映射（纯数据）。

v1 残留键 → v2 规范键。get_token() 查询时自动走别名。
"""
from __future__ import annotations

# v1 键 → v2 键
KEY_ALIASES: dict[str, str] = {
    # v1 4 层背景 → v2 2 层
    "surface_alt": "surface_sunken",
    "elevated": "surface",
    "bg_root": "bg",
    "bg_container": "bg",
    "bg_card": "surface",
    "panel_elevated": "surface",
    "panel_well": "bg",
    "checkin_canvas": "bg",
    "checkin_card": "surface",
    "checkin_well": "bg",
    "checkin_bg_card": "surface",
    "checkin_border": "border",
    "border_light": "surface_sunken",
    "panel_border": "border",
    # v1 缺失的派生
    "link": "primary",
    "secondary": "primary",
    "success": "amount_positive",
    "warning": "warn",
    "input_bg": "surface",
    "card": "surface",
    "foreground": "text",
    "foreground_muted": "text_muted",
    "text_subtle": "text_dim",
    "sidebar_bg": "sidebar",
    "sidebar_fg": "sidebar_text",
    "sidebar_fg_muted": "sidebar_text_dim",
    "sidebar_active_bg": "sidebar_nav_active_bg",
    "sidebar_active_brighter": "sidebar_nav_active_bg",
    "sidebar_active_border": "accent",
    "primary_foreground": "surface",
    "gold_line": "gold_thread",
    "btn_secondary": "btn_card_action",
    "btn_ghost": "btn_low_freq",
}


def resolve_alias(key: str) -> str:
    """返回别名解析后的键。无别名返回原键。"""
    return KEY_ALIASES.get(key, key)
