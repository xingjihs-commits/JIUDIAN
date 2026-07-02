# -*- coding: utf-8 -*-
"""themes — 主题色板层。

每套主题 = 一份纯数据 dict。无逻辑、无 Qt、无 DB。
runtime/ 查询当前主题，qss/ 编译时注入。

四时之色 v2（侧栏跟随主题色）：
    晨雾 Mist  — 雾蓝侧栏 × 蜜金
    午荫 Shade — 墨绿侧栏 × 陶土橙
    暮霞 Glow  — 玫红侧栏 × 墨绿
    夜墨 Ink   — 墨黑侧栏 × 月光金
"""
from __future__ import annotations

from .mist import MIST
from .shade import SHADE
from .glow import GLOW
from .ink import INK
from ._schema import REQUIRED_KEYS, DEFAULT_FALLBACK, validate_theme

__all__ = [
    "MIST", "SHADE", "GLOW", "INK",
    "THEMES", "DEFAULT_THEME",
    "resolve_theme_name",
    "REQUIRED_KEYS", "DEFAULT_FALLBACK", "validate_theme",
]

# 四主题注册表
THEMES: dict[str, dict] = {
    "mist": MIST,
    "shade": SHADE,
    "glow": GLOW,
    "ink": INK,
}

DEFAULT_THEME = "mist"

# 旧主题名 → 新四主题（向后兼容）
_THEME_ALIASES: dict[str, str] = {
    "old_money": "shade", "twilight_lilac": "glow",
    "zen_sand": "shade", "pink_maiden": "glow",
    "forest": "shade", "sakura": "glow",
    "daylight": "mist", "opulent_noir": "ink", "obsidian": "ink",
    "classic_white": "mist", "nordic_white": "mist",
    "cozy_hearth": "shade", "cyber_dark": "ink", "dark_geek": "ink",
    "frost": "mist", "warm_pink": "glow",
    "lavender_purple": "glow", "matcha_green": "shade", "velvet": "glow",
}


def resolve_theme_name(name: str | None) -> str:
    """解析 DB/配置中的主题键 → 四主题之一。未识别返回 DEFAULT_THEME。"""
    key = _THEME_ALIASES.get(name or "", name or DEFAULT_THEME)
    return key if key in THEMES else DEFAULT_THEME
