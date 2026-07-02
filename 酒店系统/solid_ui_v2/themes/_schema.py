# -*- coding: utf-8 -*-
"""themes/_schema — 主题色板键名规范 + 默认值 + 校验。

所有主题 dict 必须包含 REQUIRED_KEYS 列出的全部键。
未显式提供的键，runtime/expand.py 会用 DEFAULT_FALLBACK 兜底。
"""
from __future__ import annotations

# 主题色板必须包含的键（按字母序）
REQUIRED_KEYS: tuple[str, ...] = (
    "theme_title",
    # 主色系
    "primary", "primary_hover", "accent", "accent_hover",
    "danger", "danger_muted",
    "amount_positive", "amount_negative",
    # 背景层级（2 层 + 凹陷 + 悬浮）
    "bg", "surface", "surface_sunken", "surface_hover",
    # 文字
    "text", "text_muted", "text_dim",
    # 侧栏（跟随主题色）
    "sidebar", "sidebar_hover",
    "sidebar_text", "sidebar_text_active", "sidebar_text_dim",
    "sidebar_active_icon",
    "sidebar_nav_hover_bg", "sidebar_nav_active_bg",
    # 描边
    "border", "border_strong",
    # 金线 + 警告
    "gold_thread", "warn",
    # 房态语义色
    "room_inhouse", "room_dirty", "room_overtime",
    "room_maintenance", "room_reserved",
    "room_checking", "room_locked",
    # 会员等级
    "member_bronze", "member_silver", "member_gold",
    "member_diamond", "member_enterprise",
    # 按钮
    "btn_primary", "btn_primary_hover",
    "btn_card_action", "btn_low_freq",
    "btn_anchor_bg", "btn_anchor_fg",
    "btn_anchor_hover_bg", "btn_anchor_pressed_bg",
    # 焦点
    "focus_ring",
)

# 兜底默认值（暖中性色板，四主题兼容）
DEFAULT_FALLBACK: dict[str, str] = {
    "theme_title": "默认",
    "primary": "#3D6A93", "primary_hover": "#345C80",
    "accent": "#C89B5C", "accent_hover": "#B88A4D",
    "danger": "#B56050", "danger_muted": "#8E4A3E",
    "amount_positive": "#5B8A6A", "amount_negative": "#B56050",
    "bg": "#EEF2F6", "surface": "#FFFFFF",
    "surface_sunken": "#E1E8EF", "surface_hover": "#F5F8FB",
    "text": "#1A2530", "text_muted": "#5A6878", "text_dim": "#8A95A4",
    "sidebar": "#1F3D5C", "sidebar_hover": "#2A4D70",
    "sidebar_text": "#C5D5E3", "sidebar_text_active": "#FFFFFF",
    "sidebar_text_dim": "#7B8FA3", "sidebar_active_icon": "#C89B5C",
    "sidebar_nav_hover_bg": "rgba(200,155,92,0.10)",
    "sidebar_nav_active_bg": "rgba(200,155,92,0.16)",
    "border": "#D5DEE6", "border_strong": "#B8C5D1",
    "gold_thread": "#C89B5C", "warn": "#C89B5C",
    "room_inhouse": "#5B8A6A", "room_dirty": "#C8895A",
    "room_overtime": "#B56050", "room_maintenance": "#8B6FA8",
    "room_reserved": "#C89B5C", "room_checking": "#5B9BD5",
    "room_locked": "#8A95A4",
    "member_bronze": "#8B7355", "member_silver": "#9CA3AF",
    "member_gold": "#C89B5C", "member_diamond": "#3D6A93",
    "member_enterprise": "#1F2C3A",
    "btn_primary": "#3D6A93", "btn_primary_hover": "#345C80",
    "btn_card_action": "#345C80", "btn_low_freq": "#5B89AC",
    "btn_anchor_bg": "#1A2530", "btn_anchor_fg": "#FFFFFF",
    "btn_anchor_hover_bg": "#2A3540", "btn_anchor_pressed_bg": "#0F1820",
    "focus_ring": "#C89B5C",
    # ── v1 兼容 token（QSS 模板引用但 v2 色板未显式定义）──
    "btn_primary_bg": "#3D6A93", "btn_primary_fg": "#FFFFFF",
    "btn_primary_border": "transparent",
    "btn_secondary": "#345C80", "btn_secondary_bg": "transparent",
    "btn_secondary_fg": "#3D6A93", "btn_secondary_border": "#3D6A93",
    "btn_ghost": "#5B89AC",
    "btn_anchor_border": "transparent",
    "btn.sm": "32", "btn.md": "36",
    "danger_bg": "#B56050", "danger_fg": "#FFFFFF", "danger_border": "transparent",
    "disabled_bg": "#E1E8EF", "disabled_fg": "#8A95A4", "disabled_border": "#D5DEE6",
    "selected_bg": "rgba(61,106,147,0.10)", "selected_fg": "#1A2530",
    "scrollbar_bg": "#E1E8EF", "scrollbar_handle": "#D5DEE6",
    "scrollbar_handle_hover": "#8A95A4", "scrollbar_handle_pressed": "#5A6878",
    "menu_bg": "#FFFFFF", "menu_border": "#D5DEE6",
    "menu_separator": "#E1E8EF", "menu_radius": "10px",
    "sidebar_border_right": "#D5DEE6", "sidebar_divider": "#D5DEE6",
    "sidebar_group_label": "#8A95A4", "sidebar_icon": "#C5D5E3",
    "sidebar_role_strip_border": "#D5DEE6",
}


def validate_theme(name: str, pal: dict) -> list[str]:
    """校验主题 dict 是否包含所有必需键。返回缺失键列表（空 = 通过）。"""
    return [k for k in REQUIRED_KEYS if k not in pal]
