# -*- coding: utf-8 -*-
"""runtime/expand — 派生 token 计算。

输入：themes/{name}.py 的原始 dict
输出：含全部派生项的完整 dict（供 QSS 编译器和 get_token() 使用）

派生职责分类：
    - 透明色（primary_10pct / accent_tint / danger_7pct）
    - 按钮色（对比度校验）
    - 选中态 / 焦点环 / 禁用态
    - Tooltip / Menu / Scrollbar
    - 阴影 / 圆角 / 控件高度
    - v1 向后兼容别名
"""
from __future__ import annotations

from .color_utils import (
    darken, lighten, hex_to_rgba, contrast_ratio, is_dark_theme,
)


def expand_tokens(pal: dict) -> dict:
    """对原始主题 dict 派生补充 token。"""
    expanded = dict(pal)
    # 确保所有值是字符串
    for k, v in list(expanded.items()):
        if not isinstance(v, str):
            expanded[k] = str(v)

    _expand_alpha_colors(expanded, pal)
    _expand_buttons(expanded, pal)
    _expand_states(expanded, pal)
    _expand_chrome(expanded, pal)
    _expand_metrics(expanded, pal)
    _expand_compat_aliases(expanded, pal)
    _expand_v1_qss_compat(expanded, pal)

    return expanded


# ═══════════════════════════════════════════════════════════════
# 透明色派生
# ═══════════════════════════════════════════════════════════════
def _expand_alpha_colors(expanded: dict, pal: dict) -> None:
    primary = pal.get("primary", "#3D6A93")
    accent = pal.get("accent", "#C89B5C")
    danger = pal.get("danger", "#B56050")

    expanded.setdefault("primary_soft", lighten(primary, 0.30))
    expanded.setdefault("primary_tint", hex_to_rgba(primary, 0.08))
    for pct in (10, 20, 30, 40, 50, 60):
        expanded.setdefault(f"primary_{pct}pct", hex_to_rgba(primary, pct / 100))

    expanded.setdefault("accent_soft", lighten(accent, 0.40))
    expanded.setdefault("accent_tint", hex_to_rgba(accent, 0.10))

    expanded.setdefault("danger_7pct", hex_to_rgba(danger, 0.07))
    expanded.setdefault("danger_hover", darken(danger, 0.15))
    expanded.setdefault("danger_pressed", darken(danger, 0.30))
    expanded.setdefault("danger_bg", danger)
    expanded.setdefault("danger_fg", pal.get("surface", "#FFFFFF"))
    expanded.setdefault("danger_border", "transparent")


# ═══════════════════════════════════════════════════════════════
# 按钮色派生（含对比度校验）
# ═══════════════════════════════════════════════════════════════
def _expand_buttons(expanded: dict, pal: dict) -> None:
    btn = _ensure_btn_contrast(pal)
    expanded["btn_primary"] = btn["btn_primary"]
    expanded["btn_card_action"] = btn["btn_card_action"]
    expanded["btn_low_freq"] = btn["btn_low_freq"]
    expanded["btn_low_freq_fg"] = btn["btn_low_freq_fg"]

    expanded.setdefault("btn_primary_bg", expanded["btn_primary"])
    expanded.setdefault("btn_primary_fg", pal.get("btn_primary_fg") or pal.get("surface", "#FFFFFF"))
    expanded.setdefault("btn_primary_border", "transparent")
    expanded.setdefault("btn_secondary", expanded["btn_card_action"])
    expanded.setdefault("btn_ghost", expanded["btn_low_freq"])
    expanded.setdefault("btn_secondary_bg", "transparent")
    expanded.setdefault("btn_secondary_fg", expanded["btn_primary"])
    expanded.setdefault("btn_secondary_border", expanded["btn_primary"])
    expanded.setdefault("btn_anchor_border", "transparent")


def _ensure_btn_contrast(pal: dict) -> dict:
    """按键色对比度校验（≥4.5:1 WCAG AA）。

    深色主题按钮文字是深色，需要按钮色足够亮；
    浅色主题按钮文字是白色，需要按钮色足够深。
    """
    primary = pal.get("primary", "#3D6A93")
    surface = pal.get("surface", "#FFFFFF")
    text = pal.get("text", "#1A2530")
    dark = is_dark_theme(pal)
    button_text = pal.get("btn_primary_fg") if dark else surface

    btn_primary = pal.get("btn_primary", primary)
    if contrast_ratio(button_text, btn_primary) < 4.5:
        btn_primary = lighten(primary, 0.15) if dark else darken(primary, 0.18)

    btn_low_freq = pal.get("btn_low_freq") or (
        darken(primary, 0.30) if dark else lighten(primary, 0.20)
    )
    btn_card_action = pal.get("btn_card_action") or btn_primary
    if contrast_ratio(button_text, btn_card_action) < 4.5:
        btn_card_action = lighten(btn_card_action, 0.12) if dark else darken(btn_card_action, 0.12)

    btn_low_freq_fg = button_text if contrast_ratio(button_text, btn_low_freq) >= 4.5 else text

    return {
        "btn_primary": btn_primary,
        "btn_card_action": btn_card_action,
        "btn_low_freq": btn_low_freq,
        "btn_low_freq_fg": btn_low_freq_fg,
    }


# ═══════════════════════════════════════════════════════════════
# 状态色派生（选中 / 焦点 / 禁用）
# ═══════════════════════════════════════════════════════════════
def _expand_states(expanded: dict, pal: dict) -> None:
    expanded.setdefault("selected_bg", expanded.get("primary_10pct", "rgba(0,0,0,0.05)"))
    expanded.setdefault("selected_fg", pal.get("text", "#1A2530"))
    expanded.setdefault("focus_ring", pal.get("accent", "#C89B5C"))
    expanded.setdefault("focus_ring_alpha", "80")
    expanded.setdefault("disabled_bg", pal.get("surface_sunken", "#E1E8EF"))
    expanded.setdefault("disabled_fg", pal.get("text_dim", "#8A95A4"))
    expanded.setdefault("disabled_border", pal.get("border", "#D5DEE6"))


# ═══════════════════════════════════════════════════════════════
# 浏览器 chrome 派生（tooltip / menu / scrollbar）
# ═══════════════════════════════════════════════════════════════
def _expand_chrome(expanded: dict, pal: dict) -> None:
    expanded.setdefault("tooltip_bg", "#1E1A1E")
    expanded.setdefault("tooltip_fg", "#F0EDE8")
    expanded.setdefault("tooltip_radius", "8px")
    expanded.setdefault("menu_bg", pal.get("surface", "#FFFFFF"))
    expanded.setdefault("menu_border", pal.get("border", "#D5DEE6"))
    expanded.setdefault("menu_separator", pal.get("surface_sunken", "#E1E8EF"))
    expanded.setdefault("menu_radius", "10px")
    expanded.setdefault("scrollbar_bg", pal.get("surface_sunken", "#E1E8EF"))
    expanded.setdefault("scrollbar_handle", pal.get("border", "#D5DEE6"))
    expanded.setdefault("scrollbar_handle_hover", pal.get("text_dim", "#8A95A4"))
    expanded.setdefault("scrollbar_handle_pressed", pal.get("text_muted", "#5A6878"))


# ═══════════════════════════════════════════════════════════════
# 度量派生（阴影 / 圆角 / 控件高度 / 布局 padding）
# ═══════════════════════════════════════════════════════════════
def _expand_metrics(expanded: dict, pal: dict) -> None:
    expanded.setdefault("shadow_sm", "0 1px 3px rgba(0,0,0,0.04)")
    expanded.setdefault("shadow_md", "0 4px 12px rgba(0,0,0,0.06)")
    expanded.setdefault("shadow_lg", "0 8px 24px rgba(0,0,0,0.08)")
    expanded.setdefault("radius_sm", "6px")
    expanded.setdefault("radius_md", "8px")
    expanded.setdefault("radius_lg", "12px")
    expanded.setdefault("btn_sm", "32")
    expanded.setdefault("btn_md", "36")
    expanded.setdefault("btn_lg", "36")
    expanded.setdefault("layout_density", "standard")
    expanded.setdefault("layout_padding_sm", "8")
    expanded.setdefault("layout_padding_md", "12")
    expanded.setdefault("layout_padding_lg", "16")
    expanded.setdefault("layout_padding_xl", "20")


# ═══════════════════════════════════════════════════════════════
# v1 向后兼容别名
# ═══════════════════════════════════════════════════════════════
def _expand_compat_aliases(expanded: dict, pal: dict) -> None:
    """v1 残留键映射到 v2 token，保证旧代码不崩。"""
    bg = pal.get("bg", "#EEF2F6")
    surface = pal.get("surface", "#FFFFFF")
    sunken = pal.get("surface_sunken", "#E1E8EF")
    border = pal.get("border", "#D5DEE6")

    aliases = {
        "surface_alt": sunken,
        "elevated": surface,
        "bg_root": bg, "bg_container": bg,
        "bg_card": surface, "panel_elevated": surface, "panel_well": bg,
        "checkin_canvas": bg, "checkin_card": surface, "checkin_well": bg,
        "checkin_bg_card": surface, "checkin_border": border,
        "border_light": sunken, "panel_border": border,
    }
    for k, v in aliases.items():
        expanded.setdefault(k, v)


# ═══════════════════════════════════════════════════════════════
# v1 QSS 模板兼容 token（v1 模板引用但 v2 色板未显式定义的键）
# ═══════════════════════════════════════════════════════════════
def _expand_v1_qss_compat(expanded: dict, pal: dict) -> None:
    """补充 v1 QSS 模板中引用但 v2 色板未显式定义的 token。

    这些 token 在 v1 模板中用于子组件样式（侧栏 chrome、
    按钮尺寸、禁用态等），v2 按主题语义自动派生。
    """
    surface = pal.get("surface", "#FFFFFF")
    border = pal.get("border", "#D5DEE6")
    sunken = pal.get("surface_sunken", "#E1E8EF")
    text_dim = pal.get("text_dim", "#8A95A4")
    text_muted = pal.get("text_muted", "#5A6878")
    sidebar_text = pal.get("sidebar_text", "#C5D5E3")

    expanded.setdefault("btn.sm", "32")
    expanded.setdefault("btn.md", "36")
    expanded.setdefault("sidebar_border_right", border)
    expanded.setdefault("sidebar_divider", border)
    expanded.setdefault("sidebar_group_label", text_dim)
    expanded.setdefault("sidebar_icon", sidebar_text)
    expanded.setdefault("sidebar_role_strip_border", border)
    expanded.setdefault("scrollbar_bg", sunken)
    expanded.setdefault("scrollbar_handle", border)
    expanded.setdefault("scrollbar_handle_hover", text_dim)
    expanded.setdefault("scrollbar_handle_pressed", text_muted)
    expanded.setdefault("menu_bg", surface)
    expanded.setdefault("menu_border", border)
    expanded.setdefault("menu_separator", sunken)
    expanded.setdefault("menu_radius", "10px")
