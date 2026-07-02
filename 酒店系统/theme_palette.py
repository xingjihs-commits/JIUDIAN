# -*- coding: utf-8 -*-
# [UI-REVAMP] 2026-06-20 v7 — 四时之色主题系列（莫兰迪低饱和 + 柔和浮起）
# [sub-j] 2026-06-22 v7.7 — 四主题名副其实：底色与主色贴合名字意境
"""主题语义色板 — 四时之色（晨雾/午荫/暮霞/夜墨）。

v7 设计哲学：
  • 4 主题共享同一套暖象牙中性灰阶（夜墨用深墨）
  • 莫兰迪低饱和主色，年轻态但不浮躁
  • 柔和浮起质感，1px 细线分层，不靠阴影堆砌
  • 金线收敛到 3 处品牌锚点（ContentBox/LedgerDock/SectionBar）
  • WCAG AA 对比度（按钮白字 ≥ 4.5:1）
  • 3 布局可搭配（紧凑/标准/宽松）

v7.7 [sub-j] 主题意境（名副其实）：
  晨雾 Mist   — 清晨薄雾，冷蓝象牙，宁静致远
  午荫 Shade  — 午后树荫，暖绿象牙，沉稳典雅
  暮霞 Glow   — 黄昏霞光，暖粉象牙，温柔绮丽
  夜墨 Ink    — 子夜墨色，深墨底色，幽静护眼

4 主题色相分布：
  晨雾 Mist   — 雾蓝 #5B8FB9，冷蓝象牙底 #F7F9FA，清新理性，默认主题
  午荫 Shade  — 苔绿 #6B8E7B，暖绿象牙底 #F7F9F7，沉稳质感，经典商务
  暮霞 Glow   — 玫瑰粉 #C47E8A，暖粉象牙底 #FBF7F8，温柔浪漫，前台福利
  夜墨 Ink    — 浅墨蓝 #7BA7C9，深墨底 #1E242C，深邃护眼，夜班场景
"""
from __future__ import annotations


# ═══════════════════════════════════════════════════════════════
# 晨雾 Mist — 主题意境：清晨薄雾，冷蓝象牙，宁静致远
# ═══════════════════════════════════════════════════════════════
MIST = {
    "theme_title": "晨雾",
    # ── 主色系（保持原莫兰迪雾蓝，微调灰阶）──
    "primary":         "#5B8FB9",   # 雾蓝主色
    "primary_hover":   "#4A7AA0",
    "accent":          "#7BA7C9",   # 浅雾蓝点缀
    "danger":          "#C47E7A",   # 灰玫瑰红
    "danger_muted":    "#9B6A66",
    "amount_positive": "#5A9E7E",
    "amount_negative": "#C47E7A",
    # ── 房态色条 ──
    "room_inhouse":      "#5A9E7E",
    "room_dirty":        "#E8A87C",
    "room_overtime":     "#C47E7A",
    "room_maintenance":  "#9B6BA8",
    "room_reserved":     "#D4A574",
    "room_checking":     "#64B5F6",
    "room_locked":       "#9E9E9E",
    # ── 莫兰迪五层空间（保留原始浅色基底，微调层次差）──
    "bg":              "#F2F5F8",   # L0 画布 — 比原色微深 1%，有存在感
    "surface_alt":     "#E8EEF4",   # L1 面板 — 比 bg 深 3%（工具栏/筛选条/右栏底）
    "elevated":        "#F8FBFE",   # L2 浮卡 — 比 bg 亮（卡片浮起）
    "surface":         "#FDFEFF",   # L3 数据井 — 最白
    "border":          "#DEE5EC",   # 描边
    # ── 文字 ──
    "text_dim":        "#6B7280",
    "text_muted":      "#4B5563",
    "text":            "#2A3038",
    # ── 侧栏 — 由主色派生（深沉底色 + 主色点缀） ──
    "sidebar":         "#1F3D5C",   # primary #3D6A93 → 加深至深海蓝
    "sidebar_hover":   "#2A4E6E",
    "sidebar_text":    "#C8D6E3",
    "sidebar_icon":    "#8BAFC8",
    "sidebar_active_text":  "#D8E8F6",
    "sidebar_active_icon":  "#6A9FC0",
    "sidebar_group_label":   "#8AA0B8",
    "sidebar_border_right":  "#2A4E6E",
    "sidebar_divider": "#2A4E6E",
    "sidebar_role_strip_border": "#2A4E6E",
    # ── 品牌 ──
    "gold_thread":     "#C8B480",
    "warn":            "#D4A574",
    # ── 莫兰迪辅色（不与主色冲突的点缀色）──
    "morandi_blue":    "#A0B8CC",   # 雾蓝灰
    "morandi_green":   "#9EB0A4",   # 苔灰
    "morandi_pink":    "#C4A8AE",   # 尘粉
    "morandi_warm":    "#B8ADA5",   # 暖灰
    # ── 会员 ──
    "member_bronze":   "#8B7355",
    "member_silver":   "#9CA3AF",
    "member_gold":     "#D4A574",
    "member_diamond":  "#5B8FB9",
    "member_enterprise": "#3A4458",
    # ── 按钮 ──
    "btn_primary":     "#5B8FB9",
    "btn_card_action": "#4A7AA0",
    "btn_low_freq":    "#7BA7C9",
    "btn_anchor_bg":        "#2A3441",
    "btn_anchor_fg":        "#FAF7F2",
    "btn_anchor_border":    "#2A3441",
    "btn_anchor_hover_bg":  "#3A4458",
    "btn_anchor_pressed_bg":"#1E2832",
}


# ═══════════════════════════════════════════════════════════════
# 午荫 Shade — 主题意境：午后树荫，暖绿象牙，沉稳典雅
# ═══════════════════════════════════════════════════════════════
SHADE = {
    "theme_title": "午荫",
    "primary":         "#6B8E7B",
    "primary_hover":   "#5A7D6A",
    "accent":          "#8AA898",
    "danger":          "#B5705A",
    "danger_muted":    "#8E5644",
    "amount_positive": "#5A9E7E",
    "amount_negative": "#B5705A",
    "room_inhouse":      "#6B8E7B",
    "room_dirty":        "#C4956A",
    "room_overtime":     "#B5705A",
    "room_maintenance":  "#8470A0",
    "room_reserved":     "#C4A070",
    "room_checking":     "#64B5F6",
    "room_locked":       "#9E9E9E",
    "bg":              "#F1F5F2",   # L0 — 暖绿灰底
    "surface_alt":     "#E6F0EA",   # L1 — 暖绿中层
    "elevated":        "#F8FCF9",   # L2 — 暖绿浮纸
    "surface":         "#FDFEFD",   # L3 — 纯白数据面
    "border":          "#DBE5DD",
    "text_dim":        "#6B7268",
    "text_muted":      "#4B5248",
    "text":            "#2A3028",
    # ── 侧栏 — 由主色派生（深沉底色 + 主色点缀） ──
    "sidebar":         "#1E352A",   # primary #6B8E7B → 加深至深林绿
    "sidebar_hover":   "#2E453A",
    "sidebar_text":    "#C4D6CC",
    "sidebar_icon":    "#8AB09C",
    "sidebar_active_text":  "#D8EEE2",
    "sidebar_active_icon":  "#6A9E82",
    "sidebar_group_label":   "#88A896",
    "sidebar_border_right":  "#2E453A",
    "sidebar_divider": "#2E453A",
    "sidebar_role_strip_border": "#2E453A",
    "gold_thread":     "#C8B480",
    "warn":            "#D4A574",
    "morandi_blue":    "#A0B8C4",
    "morandi_green":   "#9EB0A4",
    "morandi_pink":    "#C4A8AE",
    "morandi_warm":    "#B8ADA5",
    "member_bronze":   "#8B7355",
    "member_silver":   "#9CA39A",
    "member_gold":     "#D4A574",
    "member_diamond":  "#6B8E7B",
    "member_enterprise": "#3A453E",
    "btn_primary":     "#6B8E7B",
    "btn_card_action": "#5A7D6A",
    "btn_low_freq":    "#8AA898",
    "btn_anchor_bg":        "#2A352E",
    "btn_anchor_fg":        "#FAF7F2",
    "btn_anchor_border":    "#2A352E",
    "btn_anchor_hover_bg":  "#3A453E",
    "btn_anchor_pressed_bg":"#1E2820",
}


# ═══════════════════════════════════════════════════════════════
# 暮霞 Glow — 主题意境：黄昏霞光，暖粉象牙，温柔绮丽
# ═══════════════════════════════════════════════════════════════
GLOW = {
    "theme_title": "暮霞",
    "primary":         "#C08890",   # 莫兰迪玫瑰粉
    "primary_hover":   "#AE7680",
    "accent":          "#D4A8AE",
    "danger":          "#B56872",
    "danger_muted":    "#8E505A",
    "amount_positive": "#7EAE92",
    "amount_negative": "#B56872",
    "room_inhouse":      "#6E927A",
    "room_dirty":        "#D49A7A",
    "room_overtime":     "#B56872",
    "room_maintenance":  "#9478A4",
    "room_reserved":     "#C8A27E",
    "room_checking":     "#64B5F6",
    "room_locked":       "#9E9E9E",
    "bg":              "#F2EEF0",   # L0 暖粉灰底
    "surface_alt":     "#EBE0E4",   # L1 暖粉中层
    "elevated":        "#F9F5F6",   # L2 暖粉浮纸
    "surface":         "#FEFCFD",   # L3 纯白数据面
    "border":          "#E0D5D8",
    "text_dim":        "#7A7278",
    "text_muted":      "#524A50",
    "text":            "#2E2628",
    # ── 侧栏 — 由主色派生（深沉底色 + 主色点缀） ──
    "sidebar":         "#342028",   # primary #C08890 → 加深至深玫灰
    "sidebar_hover":   "#443038",
    "sidebar_text":    "#DCC8CC",
    "sidebar_icon":    "#C098A0",
    "sidebar_active_text":  "#F0D8DC",
    "sidebar_active_icon":  "#B07882",
    "sidebar_group_label":   "#B098A0",
    "sidebar_border_right":  "#443038",
    "sidebar_divider": "#443038",
    "sidebar_role_strip_border": "#443038",
    "gold_thread":     "#C0AE80",
    "warn":            "#C4A87A",
    "morandi_blue":    "#9AA8B8",
    "morandi_green":   "#9CADA0",
    "morandi_pink":    "#C4A8AD",
    "morandi_warm":    "#B8ADA5",
    "member_bronze":   "#8B7355",
    "member_silver":   "#9C99AA",
    "member_gold":     "#C4A060",
    "member_diamond":  "#C08890",
    "member_enterprise": "#4A3A45",
    "btn_primary":     "#C08890",
    "btn_card_action": "#AE7680",
    "btn_low_freq":    "#D4A8AE",
    "btn_anchor_bg":        "#3A2A35",
    "btn_anchor_fg":        "#FAF7F2",
    "btn_anchor_border":    "#3A2A35",
    "btn_anchor_hover_bg":  "#4A3A45",
    "btn_anchor_pressed_bg":"#2A1E28",
}


# ═══════════════════════════════════════════════════════════════
# 夜墨 Ink — 主题意境：子夜墨色，深墨底色，幽静护眼
# ═══════════════════════════════════════════════════════════════
INK = {
    "theme_title": "夜墨",
    "primary":         "#5B8FB9",
    "primary_hover":   "#4A7AA0",
    "accent":          "#7BA7C9",
    "danger":          "#C47E7A",
    "danger_muted":    "#9B6A66",
    "amount_positive": "#7EAE92",
    "amount_negative": "#C47E7A",
    "room_inhouse":      "#7EAE92",
    "room_dirty":        "#D4A87A",
    "room_overtime":     "#C47E7A",
    "room_maintenance":  "#A08ABA",
    "room_reserved":     "#C4A87E",
    "room_checking":     "#64B5F6",
    "room_locked":       "#8A96A2",
    "bg":              "#2A3038",
    "surface_alt":     "#343E48",
    "elevated":        "#3A4450",
    "surface":         "#44505E",
    "border":          "#4A5662",
    "text_dim":        "#7A8690",
    "text_muted":      "#AAB4BC",
    "text":            "#E8EEF2",
    "sidebar":         "#1A2430",
    "sidebar_hover":   "#2A3A48",
    "sidebar_text":    "#C0CCD8",
    "sidebar_icon":    "#7EA8C0",
    "sidebar_active_text":  "#D0E0F0",
    "sidebar_active_icon":  "#88B0D0",
    "sidebar_group_label":   "#7A8E9E",
    "sidebar_border_right":  "#2A3A48",
    "sidebar_divider": "#2A3A48",
    "sidebar_role_strip_border": "#2A3A48",
    "gold_thread":     "#9A9078",
    "warn":            "#C4A87A",
    "morandi_blue":    "#8A9EB0",
    "morandi_green":   "#8A9E94",
    "morandi_pink":    "#B0989E",
    "morandi_warm":    "#9A9288",
    "member_bronze":   "#8B7355",
    "member_silver":   "#9AA4B0",
    "member_gold":     "#A89878",
    "member_diamond":  "#7BA7C9",
    "member_enterprise": "#3A4550",
    "btn_primary":     "#5B8FB9",
    "btn_card_action": "#4A7AA0",
    "btn_low_freq":    "#3A4450",
    "btn_primary_fg":  "#E8EEF2",
    "btn_danger_fg":   "#E8EEF2",
    "btn_anchor_bg":        "#3A4550",
    "btn_anchor_fg":        "#E8EEF2",
    "btn_anchor_border":    "#3A4550",
    "btn_anchor_hover_bg":  "#4A5560",
    "btn_anchor_pressed_bg":"#2A3540",
}


THEMES = {
    "mist": MIST,
    "shade": SHADE,
    "glow": GLOW,
    "ink": INK,
}

DEFAULT_THEME = "mist"

# 兼容旧 design_tokens 的四种主题常量导出
OLD_MONEY = SHADE
TWILIGHT_LILAC = GLOW
ZEN_SAND = SHADE
PINK_MAIDEN = GLOW

# 兼容旧主题键 → 新四主题
_THEME_ALIASES: dict[str, str] = {
    "old_money": "shade",
    "twilight_lilac": "glow",
    "zen_sand": "shade",
    "pink_maiden": "glow",
    "forest": "shade",
    "sakura": "glow",
    "daylight": "mist",
    "opulent_noir": "ink",
    "obsidian": "ink",
    "classic_white": "mist",
    "nordic_white": "mist",
    "cozy_hearth": "shade",
    "cyber_dark": "ink",
    "dark_geek": "ink",
    "frost": "mist",
    "warm_pink": "glow",
    "lavender_purple": "glow",
    "matcha_green": "shade",
    "velvet": "glow",
}


def resolve_theme_name(name: str | None) -> str:
    """解析 DB/配置中的主题键 → 四主题之一。"""
    key = _THEME_ALIASES.get(name or "", name or DEFAULT_THEME)
    return key if key in THEMES else DEFAULT_THEME




# ═══════════════════════════════════════════════════════════════
# v7.6 性能优化：主题 token 缓存
# ═══════════════════════════════════════════════════════════════
_TOKEN_CACHE: dict[str, dict] = {}


def clear_token_cache() -> None:
    """清除主题 token 缓存（换主题后调用）。"""
    _TOKEN_CACHE.clear()


def theme_tokens(theme_name: str | None = None) -> dict:
    """返回指定主题的完整 QSS 令牌（含缓存，换主题后自动失效）。"""
    resolved = resolve_theme_name(theme_name)
    if resolved in _TOKEN_CACHE:
        return _TOKEN_CACHE[resolved]
    tokens = _replace_qss_vars(THEMES[resolved])
    _TOKEN_CACHE[resolved] = tokens
    return tokens



# ═══════════════════════════════════════════════════════════════
# 颜色工具函数
# ═══════════════════════════════════════════════════════════════

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(hex_color: str) -> float:
    r, g, b = _hex_to_rgb(hex_color)
    channels = []
    for c in (r, g, b):
        s = c / 255.0
        channels.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = _relative_luminance(fg_hex)
    l2 = _relative_luminance(bg_hex)
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)


def _interpolate_hex(hex_a: str, hex_b: str, t: float) -> str:
    ra, ga, ba = _hex_to_rgb(hex_a)
    rb, gb, bb = _hex_to_rgb(hex_b)
    r = int(ra + (rb - ra) * t)
    g = int(ga + (gb - ga) * t)
    b = int(ba + (bb - ba) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _ensure_btn_contrast(pal: dict) -> dict:
    """按键色推导 + 白字对比度校验（≥4.5:1）。"""
    result = dict(pal)
    primary = pal.get("primary", "#5B8FB9")
    surface = pal.get("surface", "#FFFFFF")
    text = pal.get("text", "#2A3038")

    if "btn_primary" in pal:
        btn_primary = pal["btn_primary"]
    else:
        candidate = primary
        if _contrast_ratio(surface, candidate) < 4.5:
            candidate = _darken_hex(primary, 0.18)
        btn_primary = candidate

    btn_low_freq = pal.get("btn_low_freq") or _lighten_hex(primary, 0.28)
    if "btn_card_action" in pal:
        btn_card_action = pal["btn_card_action"]
    else:
        btn_card_action = _interpolate_hex(btn_primary, btn_low_freq, 0.35)
        if _contrast_ratio(surface, btn_card_action) < 4.5:
            btn_card_action = _darken_hex(btn_card_action, 0.12)

    btn_low_freq_fg = surface if _contrast_ratio(surface, btn_low_freq) >= 4.5 else text

    result["btn_primary"] = btn_primary
    result["btn_card_action"] = btn_card_action
    result["btn_low_freq"] = btn_low_freq
    result["btn_low_freq_fg"] = btn_low_freq_fg
    return result


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _darken_hex(hex_color: str, factor: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = max(0, min(255, int(int(h[0:2], 16) * (1 - factor))))
    g = max(0, min(255, int(int(h[2:4], 16) * (1 - factor))))
    b = max(0, min(255, int(int(h[4:6], 16) * (1 - factor))))
    return f"#{r:02x}{g:02x}{b:02x}"


def _lighten_hex(hex_color: str, factor: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = min(255, int(int(h[0:2], 16) + (255 - int(h[0:2], 16)) * factor))
    g = min(255, int(int(h[2:4], 16) + (255 - int(h[2:4], 16)) * factor))
    b = min(255, int(int(h[4:6], 16) + (255 - int(h[4:6], 16)) * factor))
    return f"#{r:02x}{g:02x}{b:02x}"


def _replace_qss_vars(pal: dict) -> dict:
    """为样式表变量注入补充派生令牌。"""
    expanded = dict(pal)
    for k, v in list(expanded.items()):
        if not isinstance(v, str):
            expanded[k] = str(v)

    # 按键色推导 + 对比度校验
    btn_ready = _ensure_btn_contrast(pal)
    expanded["btn_primary"] = btn_ready["btn_primary"]
    expanded["btn_card_action"] = btn_ready["btn_card_action"]
    expanded["btn_low_freq"] = btn_ready["btn_low_freq"]
    expanded["btn_low_freq_fg"] = btn_ready["btn_low_freq_fg"]
    expanded["btn_primary_hover"] = pal.get("btn_primary_hover") or pal.get("primary", "#5B8FB9")

    # 金线品牌元素
    expanded.setdefault("gold_thread", pal.get("accent", "#C8B480"))

    # 侧栏 — 完整 token 链（四主题各自定义主要值，此处补默认回退）
    expanded.setdefault("sidebar_text", pal.get("sidebar_text", "#C8D6E3"))
    expanded.setdefault("sidebar_icon", pal.get("sidebar_icon", "#8BAFC8"))
    expanded.setdefault("sidebar_active_text", pal.get("sidebar_active_text", "#D8E8F6"))
    expanded.setdefault("sidebar_active_icon", pal.get("sidebar_active_icon", "#6A9FC0"))
    expanded.setdefault("sidebar_group_label", pal.get("sidebar_group_label", "#8AA0B8"))
    expanded.setdefault("sidebar_border_right", pal.get("sidebar_border_right", pal.get("sidebar_hover", "#2A4E6E")))
    expanded.setdefault("sidebar_divider", pal.get("sidebar_divider", pal.get("sidebar_hover", "#2A4E6E")))
    expanded.setdefault("sidebar_role_strip_border", pal.get("sidebar_role_strip_border", pal.get("sidebar_hover", "#2A4E6E")))
    expanded.setdefault("sidebar_hover", pal.get("sidebar_hover", "#2A4E6E"))

    # Tooltip
    expanded.setdefault("tooltip_bg", "#1E1A1E")
    expanded.setdefault("tooltip_fg", "#F0EDE8")
    expanded.setdefault("tooltip_radius", "8px")

    # 菜单/弹出框
    expanded.setdefault("menu_bg", pal.get("surface", "#FFFFFF"))
    expanded.setdefault("menu_border", pal.get("border", "#E8E2D8"))
    expanded.setdefault("menu_separator", pal.get("surface_alt", "#F4F1EB"))
    expanded.setdefault("menu_radius", "10px")

    # 滚动条
    expanded.setdefault("scrollbar_bg", pal.get("surface_alt", "#F4F1EB"))
    expanded.setdefault("scrollbar_handle", pal.get("border", "#E8E2D8"))
    expanded.setdefault("scrollbar_handle_hover", pal.get("text_dim", "#9CA3AF"))
    expanded.setdefault("scrollbar_handle_pressed", pal.get("text_muted", "#6B7280"))

    # 焦点环 — 统一用 accent 色
    expanded.setdefault("focus_ring", pal.get("accent", "#7BA7C9"))
    expanded.setdefault("focus_ring_alpha", "80")

    # 禁用状态统一
    expanded.setdefault("disabled_bg", pal.get("surface_alt", "#F4F1EB"))
    expanded.setdefault("disabled_fg", pal.get("text_muted", "#9CA3AF"))
    expanded.setdefault("disabled_border", pal.get("border", "#E8E2D8"))

    # 控件高度标准 — v7 统一 36px
    expanded.setdefault("btn_sm", "32")
    expanded.setdefault("btn_md", "36")   # 全站统一按钮高度
    expanded.setdefault("btn_lg", "36")   # v7: 不再分档，统一 36

    # 选中状态表格/列表高亮 — 中性化
    expanded.setdefault("selected_bg", _hex_to_rgba(pal.get("primary", "#5B8FB9"), 0.10))
    expanded.setdefault("selected_fg", pal.get("text", "#2A3038"))

    # 阴影（Qt 不支持 box-shadow，此处作文档用途）
    expanded.setdefault("shadow_sm", "0 1px 3px rgba(0,0,0,0.04)")
    expanded.setdefault("shadow_md", "0 4px 12px rgba(0,0,0,0.06)")
    expanded.setdefault("shadow_lg", "0 8px 24px rgba(0,0,0,0.08)")

    # 圆角 — v7 升级
    expanded.setdefault("radius_sm", "6px")
    expanded.setdefault("radius_md", "8px")
    expanded.setdefault("radius_lg", "12px")

    # 设计规范语义派生
    expanded.setdefault("border_light", pal.get("surface_alt", "#F4F1EB"))
    pal_primary = pal.get("primary", "#5B8FB9")
    expanded.setdefault("primary_10pct", _hex_to_rgba(pal_primary, 0.10))
    expanded.setdefault("primary_20pct", _hex_to_rgba(pal_primary, 0.20))
    expanded.setdefault("primary_30pct", _hex_to_rgba(pal_primary, 0.30))
    expanded.setdefault("primary_40pct", _hex_to_rgba(pal_primary, 0.40))
    expanded.setdefault("primary_50pct", _hex_to_rgba(pal_primary, 0.50))
    expanded.setdefault("primary_60pct", _hex_to_rgba(pal_primary, 0.60))

    expanded.setdefault("warn", pal.get("warn", pal.get("accent", "#D4A574")))
    pal_accent = pal.get("accent", "#7BA7C9")
    expanded.setdefault("accent_hover", _darken_hex(pal_accent, 0.15))

    # 危险按钮悬浮与按下
    danger_color = pal.get("danger", "#C47E7A")
    expanded.setdefault("danger_hover", _darken_hex(danger_color, 0.15))
    expanded.setdefault("danger_pressed", _darken_hex(danger_color, 0.30))

    # 按键色（已在 _ensure_btn_contrast 中写入，此处仅兜底兼容旧键）
    expanded.setdefault("btn_primary", _lighten_hex(pal_primary, 0.18))
    expanded.setdefault("btn_card_action", _lighten_hex(pal_primary, 0.28))
    expanded.setdefault("btn_low_freq", _lighten_hex(pal_primary, 0.42))
    expanded.setdefault("btn_low_freq_fg", pal.get("surface", "#FFFFFF"))
    expanded.setdefault("btn_secondary", expanded["btn_card_action"])
    expanded.setdefault("btn_ghost", expanded["btn_low_freq"])

    # 双档标准按钮 bg/fg/border 派生
    expanded.setdefault("btn_primary_bg", expanded["btn_primary"])
    expanded.setdefault("btn_primary_fg", pal.get("btn_primary_fg") or pal.get("surface", "#FFFFFF"))
    expanded.setdefault("btn_primary_border", "transparent")
    expanded.setdefault("btn_secondary_bg", "transparent")
    expanded.setdefault("btn_secondary_fg", expanded["btn_primary"])
    expanded.setdefault("btn_secondary_border", expanded["btn_primary"])

    # 锚点按钮
    expanded.setdefault("btn_anchor_bg", pal.get("btn_anchor_bg", "#2A3441"))
    expanded.setdefault("btn_anchor_fg", pal.get("btn_anchor_fg", "#FAF7F2"))
    expanded.setdefault("btn_anchor_border", pal.get("btn_anchor_border", "transparent"))
    expanded.setdefault("btn_anchor_hover_bg", pal.get("btn_anchor_hover_bg", _lighten_hex(expanded["btn_anchor_bg"], 0.10)))
    expanded.setdefault("btn_anchor_pressed_bg", pal.get("btn_anchor_pressed_bg", _darken_hex(expanded["btn_anchor_bg"], 0.05)))

    # 危险静音文字
    expanded.setdefault("danger_muted", pal.get("danger_muted", "#9B6A66"))

    # danger 按钮 bg/fg/border 派生
    expanded.setdefault("danger_bg", pal.get("danger", "#C47E7A"))
    expanded.setdefault("danger_fg", pal.get("btn_danger_fg") or pal.get("surface", "#FFFFFF"))
    expanded.setdefault("danger_border", "transparent")

    # danger 7% 透明度（FdCardGroupInhouse 底板）
    expanded.setdefault("danger_7pct", _hex_to_rgba(danger_color, 0.07))

    # 三色背景围栏 — 直接映射调色板写死的色值，零派生
    bg_v = pal.get("bg", "#F7F9FA")
    sf_v = pal.get("surface", "#FFFFFF")
    el_v = pal.get("elevated", "#FFFFFF")
    expanded.setdefault("bg_root", bg_v)
    expanded.setdefault("bg_container", bg_v)     # 别名到 bg（旧代码垫片）
    expanded.setdefault("bg_card", sf_v)          # 别名到 surface
    expanded.setdefault("surface", sf_v)
    expanded.setdefault("elevated", el_v)
    expanded.setdefault("panel_border", pal.get("border", "#E2E8ED"))

    # 收银台专属 token — 全映射至三色
    expanded.setdefault("checkin_canvas", bg_v)
    expanded.setdefault("checkin_card", sf_v)
    expanded.setdefault("checkin_well", bg_v)
    expanded.setdefault("checkin_border", pal.get("border", "#E2E8ED"))
    expanded.setdefault("checkin_bg_card", sf_v)

    # 会员等级色
    expanded.setdefault("member_bronze", pal.get("member_bronze", "#8B7355"))
    expanded.setdefault("member_silver", pal.get("member_silver", "#9CA3AF"))
    expanded.setdefault("member_gold", pal.get("member_gold", "#D4A574"))
    expanded.setdefault("member_diamond", pal.get("member_diamond", "#5B8FB9"))
    expanded.setdefault("member_enterprise", pal.get("member_enterprise", "#3A4458"))

    # v7 布局 token（3 布局可搭配，默认 standard）
    expanded.setdefault("layout_density", "standard")  # compact / standard / airy
    expanded.setdefault("layout_padding_sm", "8")
    expanded.setdefault("layout_padding_md", "12")
    expanded.setdefault("layout_padding_lg", "16")
    expanded.setdefault("layout_padding_xl", "20")

    return expanded


# ═══════════════════════════════════════════════════════════════
# Solid UI v2 色板覆盖 v1 常量（默认启用）
# ═══════════════════════════════════════════════════════════════
from solid_ui_v2.themes import MIST as _V2_MIST, SHADE as _V2_SHADE
from solid_ui_v2.themes import GLOW as _V2_GLOW, INK as _V2_INK
from solid_ui_v2.themes import resolve_theme_name as _v2_resolve
from solid_ui_v2.runtime import current_tokens as _v2_tokens
from solid_ui_v2.runtime import get_token as _v2_get_token

MIST = _V2_MIST
SHADE = _V2_SHADE
GLOW = _V2_GLOW
INK = _V2_INK

resolve_theme_name = _v2_resolve

def theme_tokens(theme_name: str | None = None) -> dict:
    import solid_ui_v2.runtime as _v2rt
    if theme_name is not None:
        _v2rt.set_theme_resolver(lambda: theme_name)
        _v2rt.invalidate_cache()
    return _v2rt.current_tokens()

    # 覆盖 _p 兼容函数
    def _p_v2_compat(key: str, fallback: str = "") -> str:
        return _v2_get_token(key, fallback)
