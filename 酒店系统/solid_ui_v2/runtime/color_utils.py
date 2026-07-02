# -*- coding: utf-8 -*-
"""runtime/color_utils — 颜色工具函数。

纯函数，无副作用，无状态。供 expand.py 和 contrast.py 共用。
"""
from __future__ import annotations


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """#RRGGBB → (r, g, b)。支持 #RGB 缩写。"""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    """#RRGGBB + alpha → rgba(r,g,b,alpha)。"""
    r, g, b = hex_to_rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha})"


def darken(hex_color: str, factor: float) -> str:
    """颜色加深。factor 0~1，0 不变，1 全黑。"""
    r, g, b = hex_to_rgb(hex_color)
    r = max(0, min(255, int(r * (1 - factor))))
    g = max(0, min(255, int(g * (1 - factor))))
    b = max(0, min(255, int(b * (1 - factor))))
    return f"#{r:02x}{g:02x}{b:02x}"


def lighten(hex_color: str, factor: float) -> str:
    """颜色提亮。factor 0~1，0 不变，1 全白。"""
    r, g, b = hex_to_rgb(hex_color)
    r = min(255, int(r + (255 - r) * factor))
    g = min(255, int(g + (255 - g) * factor))
    b = min(255, int(b + (255 - b) * factor))
    return f"#{r:02x}{g:02x}{b:02x}"


def relative_luminance(hex_color: str) -> float:
    """相对亮度（WCAG 定义）。0=全黑，1=全白。"""
    r, g, b = hex_to_rgb(hex_color)
    channels = []
    for c in (r, g, b):
        s = c / 255.0
        channels.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """对比度比值（WCAG 定义）。1~21，≥4.5 达 AA 标准。"""
    l1 = relative_luminance(fg_hex)
    l2 = relative_luminance(bg_hex)
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)


def is_dark_theme(pal: dict) -> bool:
    """检测是否深色主题。

    判定：btn_primary_fg 存在且不等于 surface。
    深色主题按钮文字色为深色，浅色主题为白色（surface）。
    """
    btn_fg = pal.get("btn_primary_fg")
    surface = pal.get("surface", "#FFFFFF")
    return bool(btn_fg and btn_fg != surface)
