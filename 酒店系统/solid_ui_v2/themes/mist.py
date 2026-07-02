# -*- coding: utf-8 -*-
"""晨雾 Mist — 雾蓝侧栏 × 蜜金。

清晨薄雾，冷雾蓝主调，蜜金碰撞出暖意。
侧栏取 primary 深色调（#1F3D5C）。
"""
from __future__ import annotations

MIST: dict[str, str] = {
    "theme_title": "晨雾",
    # 主色
    "primary": "#3D6A93", "primary_hover": "#345C80",
    "accent": "#C89B5C", "accent_hover": "#B88A4D",
    "danger": "#B56050", "danger_muted": "#8E4A3E",
    "amount_positive": "#5B8A6A", "amount_negative": "#B56050",
    # 房态
    "room_inhouse": "#5B8A6A", "room_dirty": "#C8895A",
    "room_overtime": "#B56050", "room_maintenance": "#8B6FA8",
    "room_reserved": "#C89B5C", "room_checking": "#5B9BD5",
    "room_locked": "#8A95A4",
    # 背景
    "bg": "#EEF2F6", "surface": "#FFFFFF",
    "surface_sunken": "#E1E8EF", "surface_hover": "#F5F8FB",
    # 文字
    "text": "#1A2530", "text_muted": "#5A6878", "text_dim": "#8A95A4",
    # 侧栏（雾蓝深色调）
    "sidebar": "#1F3D5C", "sidebar_hover": "#2A4D70",
    "sidebar_text": "#C5D5E3", "sidebar_text_active": "#FFFFFF",
    "sidebar_text_dim": "#7B8FA3", "sidebar_active_icon": "#C89B5C",
    "sidebar_nav_hover_bg": "rgba(200, 155, 92, 0.10)",
    "sidebar_nav_active_bg": "rgba(200, 155, 92, 0.16)",
    # 描边
    "border": "#D5DEE6", "border_strong": "#B8C5D1",
    # 金线 + 警告
    "gold_thread": "#C89B5C", "warn": "#C89B5C",
    # 会员
    "member_bronze": "#8B7355", "member_silver": "#9CA3AF",
    "member_gold": "#C89B5C", "member_diamond": "#3D6A93",
    "member_enterprise": "#1F2C3A",
    # 按钮
    "btn_primary": "#3D6A93", "btn_primary_hover": "#345C80",
    "btn_card_action": "#345C80", "btn_low_freq": "#5B89AC",
    "btn_anchor_bg": "#1A2530", "btn_anchor_fg": "#FFFFFF",
    "btn_anchor_hover_bg": "#2A3540", "btn_anchor_pressed_bg": "#0F1820",
    # 焦点
    "focus_ring": "#C89B5C",
}
