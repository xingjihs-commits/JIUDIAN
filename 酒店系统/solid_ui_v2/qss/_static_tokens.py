# -*- coding: utf-8 -*-
"""qss/_static_tokens — 静态 token（非色板，全主题共享）。

这些 token 不随主题变化，直接定义在此处。
色板 token 在 themes/ 中定义，由 runtime/expand.py 派生。
"""
from __future__ import annotations

from ..tokens.typography import FontSize, Fonts

# 静态 token 字典（供 compiler 替换 @token@ 占位符）
STATIC_TOKENS: dict[str, str] = {
    # 字体大小（7 档）
    "font.xs": FontSize.XS.value,
    "font.sm": FontSize.SM.value,
    "font.md": FontSize.MD.value,
    "font.lg": FontSize.LG.value,
    "font.xl": FontSize.XL.value,
    "font.2xl": FontSize.XXL.value,
    "font.3xl": FontSize.XXXL.value,
    # 字体族
    "font.sans": Fonts.SANS.value,
    "font.mono": Fonts.MONO.value,
    # 圆角
    "radius.sm": "6px",
    "radius.md": "8px",
    "radius.lg": "12px",
    # 错误色回退
    "error": "#B56050",
}
