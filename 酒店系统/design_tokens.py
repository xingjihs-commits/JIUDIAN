# [UI-REDESIGN] 2026-06-15 v3 重构: 新增金线/按键派生token + 粉系房态色板 + 紧凑度量
"""全局 UI 设计 token — 品牌体系 + 业务语义色板。

统一使用 solid_ui_v2 编译器生成 QSS，不再依赖环境变量开关。
"""
from __future__ import annotations
import logging

_log = logging.getLogger("solid.design_tokens")

# v4 设计系统 — 从 ui.tokens 重导出（兼容旧 import 路径）
from ui.tokens.colors import (  # noqa: F401
    ColorPrimary, ColorSemantic, ColorNeutral, ColorState, ColorRoomStatus,
)
from ui.tokens.spacing import (  # noqa: F401
    SpacingDesktop, SpacingTablet, SpacingMobile, get_spacing,
)
from ui.tokens.typography import (  # noqa: F401
    Typography, Fonts, BorderRadius, Shadow, Animation,
)

WARNING = "#D4A574"  # 蜜金警告（运行时 _p("warn") 兜底）
DANGER = "#C47E7A"   # 灰玫瑰红（运行时 _p("danger") 兜底）

# 旧 key → 新 key 的别名映射（保证现有 _p("background") 等调用不崩）
_KEY_ALIASES = {
    "background": "bg",
    "foreground": "text",
    "card": "surface",
    "input_bg": "surface",
    "sidebar_bg": "sidebar",
    "sidebar_fg": "surface",
    "sidebar_fg_muted": "text_dim",
    "sidebar_active_bg": "sidebar_hover",
    "sidebar_active_brighter": "sidebar_hover",
    "sidebar_active_border": "gold_thread",
    "accent_soft": "surface_alt",
    "text_subtle": "text_dim",
    "secondary": "primary",
    "link": "primary",
    "warning": "warn",
    "primary_foreground": "surface",
    "foreground": "text",
    "foreground_muted": "text_muted",
    "border_light": "border",
    "success": "amount_positive",
    # 背景层别名 — 全部映射至三色
    "bg_elevated": "elevated",
    "bg_card": "surface",
    "panel_elevated": "surface",
    "panel_well": "surface",
    "panel_border": "border",
    "checkin_canvas": "bg",
    "checkin_card": "surface",
    "checkin_well": "bg",
    "checkin_border": "border",
    "checkin_bg_card": "surface",
    "spacing_xs": "space.xs",
    "spacing_sm": "space.sm",
    "spacing_md": "space.md",
    "spacing_lg": "space.lg",
    "spacing_xl": "space.xl",
    "radius_sm": "radius.sm",
    "radius_md": "radius.md",
    "radius_lg": "radius.lg",
    # v3 新增别名
    "gold_line": "gold_thread",
    "btn_secondary": "btn_card_action",
    "btn_ghost": "btn_low_freq",
    # ── 会员等级色 ──
    "member_bronze": "member_bronze",
    "member_silver": "member_silver",
    "member_gold": "member_gold",
    "member_diamond": "member_diamond",
    "member_enterprise": "member_enterprise",
}


def invalidate_token_cache() -> None:
    """主题切换后调用，清空 _p() 的 theme_palette token 缓存。"""
    global _theme_token_cache
    _theme_token_cache = {}


# ── 模块级常量（MIST 主题默认值，运行时动态取色应使用 _p()）──
PRIMARY = "#5B8FB9"
PRIMARY_HOVER = "#4A7AA0"
PRIMARY_LIGHT = "#F4F1EB"
SUCCESS = "#7BA7C9"
INFO = "#5B8FB9"
TEXT_PRIMARY = "#2A3038"
TEXT_MUTED = "#6B7280"
BORDER = "#E8E2D8"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#F4F1EB"
GOLD_THREAD = "#C8B480"

# ── KPI 点缀色（动态取色，跟随当前主题）───────────────────────────
def kpi_accents() -> dict:
    return {
        "dash_rev": _p("warn"),
        "dash_occ": _p("primary"),
        "dash_inhouse": _p("accent"),
        "dash_tasks": _p("danger"),
        "dash_risk": _p("primary"),
    }

# ── 阴影系统 ──────────────────────────────────────────────────────
SHADOW_SM = "0 1px 2px rgba(0,0,0,0.05)"
SHADOW_MD = "0 4px 6px rgba(0,0,0,0.07)"
SHADOW_LG = "0 10px 15px rgba(0,0,0,0.1)"

# ── 圆角系统（v9: 收敛至 5 档，消灭 10px/14px 中间值）────────────────
RADIUS_XS = "4px"    # 微小组件（chip 内元素、紧凑按钮）
RADIUS_SM = "6px"    # 按钮、输入框
RADIUS_MD = "8px"    # 普通卡片、分组框
RADIUS_LG = "12px"   # 弹窗、浮卡、空状态
RADIUS_XL = "16px"   # 圆润组件（chip、动画区）

# ── 间距系统（v3: 更紧凑，PC 端优化）──────────────────────────────
SPACE_XS = 3
SPACE_SM = 6
SPACE_MD = 8
SPACE_LG = 12
SPACE_XL = 20

# ── 字体系统 ──────────────────────────────────────────────────────
FONT_XS = "11px"
FONT_SM = "12px"
FONT_MD = "13px"
FONT_LG = "15px"
FONT_XL = "18px"
FONT_2XL = "22px"
FONT_3XL = "48px"

# ── 过渡时长 ──────────────────────────────────────────────────────
DURATION_FAST = "120ms"
DURATION_NORMAL = "200ms"
DURATION_SLOW = "360ms"

# ── 按钮高度三档标准（v3: 更紧凑）──────────────────────────────
BTN_HEIGHT_SM = 28      # 紧凑按钮（芯片、购物车、表格内操作）
BTN_HEIGHT_MD = 36      # 标准按钮（发卡/退房/确认/取消等）— 全站默认
BTN_HEIGHT_LG = 48      # 主操作按钮（收款提交、结账确认）

# ── Z-index 层级 ──────────────────────────────────────────────────
Z_BASE = 0
Z_DROPDOWN = 100
Z_STICKY = 200
Z_OVERLAY = 300
Z_MODAL = 400
Z_TOAST = 500
Z_TOOLTIP = 600

# ── 页面布局常量（v3 紧凑版）──────────────────────────────────
PAGE_MARGIN = 12
PAGE_MARGIN_EMBED = 6
PAGE_SPACING = 8


# ═══════════════════════════════════════════════════════════════════
# Token 字典（供 _p() 的 "." 分隔 fallback 链查询）
# ═══════════════════════════════════════════════════════════════════
_TOKEN_DICT = {
    "shadow": {"sm": SHADOW_SM, "md": SHADOW_MD, "lg": SHADOW_LG},
    "radius": {"sm": RADIUS_SM, "md": RADIUS_MD, "lg": RADIUS_LG},
    "space": {"xs": SPACE_XS, "sm": SPACE_SM, "md": SPACE_MD, "lg": SPACE_LG, "xl": SPACE_XL},
    "font": {"xs": FONT_XS, "sm": FONT_SM, "md": FONT_MD, "lg": FONT_LG, "xl": FONT_XL, "2xl": FONT_2XL, "3xl": FONT_3XL},
    "duration": {"fast": DURATION_FAST, "normal": DURATION_NORMAL, "slow": DURATION_SLOW},
    "z": {"base": Z_BASE, "dropdown": Z_DROPDOWN, "sticky": Z_STICKY, "overlay": Z_OVERLAY, "modal": Z_MODAL, "toast": Z_TOAST, "tooltip": Z_TOOLTIP},
    "btn": {"sm": BTN_HEIGHT_SM, "md": BTN_HEIGHT_MD, "lg": BTN_HEIGHT_LG},
}

# ── 动画节奏（由 theme_motion.py 提供 6 条节奏，此处仅做引用提示）──
# from theme_motion import (
#     attach_primary_button_glow,   # #1 主按钮辉光
#     pulse_room_select,            # #2 房卡选中
#     animate_kpi,                  # #3 KPI 数字
#     attach_stack_fade,            # #4 页面淡入
#     LovableToast,                 # #5 Toast
#     shake_invalid,                # #6 错误抖动
# )

# ═══════════════════════════════════════════════════════════════
# v8 桥接：_p() 从 theme_palette 获取四时之色 token
# ═══════════════════════════════════════════════════════════════
# 集中 fallback 字典 — 暖中性色板，四主题兼容
# 外部文件不再各自写硬编码，统一 from design_tokens import _NEUTRAL_FALLBACK
_NEUTRAL_FALLBACK = {
    "primary": "#7B8C9E", "primary_hover": "#6D7D8E", "accent": "#8A9AAA",
    "danger": "#B5705A", "warn": "#D4A574",
    "text": "#2A2A2E", "text_muted": "#6B6B70", "text_dim": "#9C9CA0",
    "bg": "#F8F6F3",
    "surface": "#FFFFFF",
    "elevated": "#FFFFFF",
    "border": "#E3E0DB",
    "sidebar": "#3A3840", "gold_thread": "#C8B480",
    "amount_positive": "#5A9E7E", "amount_negative": "#B5705A",
}

_theme_token_cache = {}

def _p(key: str, fallback: str = "") -> str:
    """从 theme_palette 动态获取当前主题 token。

    自动走 _KEY_ALIASES 别名映射，旧 key 自动转新 key。
    异常时走 _NEUTRAL_FALLBACK 集中兜底并记录日志。

    统一使用 solid_ui_v2 runtime。
    """
    return _p_v2(key, fallback)


def _p_v1(key: str, fallback: str = "") -> str:
    """v1: 从 theme_palette 获取 token。"""
    resolved_key = _KEY_ALIASES.get(key, key)
    try:
        from theme_palette import theme_tokens, resolve_theme_name
        import database as _db
        theme_name = None
        try:
            theme_name = _db.db.get_config("theme") or None
        except Exception:
            pass
        resolved = resolve_theme_name(theme_name)
        if resolved not in _theme_token_cache:
            _theme_token_cache[resolved] = theme_tokens(resolved)
        tokens = _theme_token_cache[resolved]
        result = tokens.get(resolved_key)
        if result is None:
            result = tokens.get(key)
        return result if result is not None else (_NEUTRAL_FALLBACK.get(resolved_key, "") or fallback)
    except Exception:
        _log.warning("_p(%r) 主题查色失败，走集中兜底", key, exc_info=True)
        return fallback or _NEUTRAL_FALLBACK.get(resolved_key, "")


def _p_v2(key: str, fallback: str = "") -> str:
    """v2: 从 solid_ui_v2.runtime 获取 token。"""
    resolved_key = _KEY_ALIASES.get(key, key)
    try:
        import solid_ui_v2.runtime as v2rt
        return v2rt.get_token(resolved_key, fallback) or fallback
    except Exception:
        _log.warning("_p(%r) v2 runtime 查色失败，走集中兜底", key, exc_info=True)
        return fallback or _NEUTRAL_FALLBACK.get(resolved_key, "")

def active_room_status_theme() -> dict:
    """房态色板 — 从 theme_palette 获取。"""
    try:
        from theme_palette import theme_tokens
        tokens = theme_tokens()
        return {
            "READY": {"color": tokens.get("primary","#7B8C9E"), "border": tokens.get("primary","#7B8C9E"), "soft": tokens.get("primary_10pct","rgba(123,140,158,0.1)"), "bg": tokens.get("surface","#FFFFFF")},
            "INHOUSE": {"color": tokens.get("room_inhouse","#6B8E74"), "border": tokens.get("room_inhouse","#6B8E74"), "soft": "rgba(107,142,116,0.1)", "bg": tokens.get("surface","#FFFFFF")},
            "DIRTY": {"color": tokens.get("room_dirty","#D49572"), "border": tokens.get("room_dirty","#D49572"), "soft": "rgba(212,149,114,0.1)", "bg": tokens.get("surface","#FFFFFF")},
            "OVERTIME": {"color": tokens.get("room_overtime","#B5586A"), "border": tokens.get("room_overtime","#B5586A"), "soft": "rgba(181,88,106,0.1)", "bg": tokens.get("surface","#FFFFFF")},
            "MAINTENANCE": {"color": tokens.get("room_maintenance","#9070A0"), "border": tokens.get("room_maintenance","#9070A0"), "soft": "rgba(144,112,160,0.1)", "bg": tokens.get("surface","#FFFFFF")},
            "RESERVED": {"color": tokens.get("room_reserved","#CC9E78"), "border": tokens.get("room_reserved","#CC9E78"), "soft": "rgba(204,158,120,0.1)", "bg": tokens.get("surface","#FFFFFF")},
        }
    except Exception:
        return {}

def pick_grid_cols(screen_width: int) -> int:
    """按屏幕宽度返回网格列数。"""
    if screen_width >= 1920:
        return 6
    elif screen_width >= 1680:
        return 4
    return 3

def pick_card_size(screen_w: int = 1440, viewport_w: int | None = None) -> tuple:
    """房卡尺寸。"""
    return (192, 140)
