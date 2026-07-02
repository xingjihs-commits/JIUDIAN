# -*- coding: utf-8 -*-
"""runtime/contrast — WCAG AA 对比度校验。"""
from __future__ import annotations

from .color_utils import contrast_ratio, is_dark_theme
from .api import current_tokens


def validate_contrast() -> list[str]:
    """对当前主题做 WCAG AA 对比度校验。返回问题列表（空 = 通过）。

    深色主题自动用 btn_primary_fg 作按钮文字色校验。
    """
    tokens = current_tokens()
    issues: list[str] = []
    dark = is_dark_theme(tokens)
    button_text = tokens.get("btn_primary_fg") if dark else tokens.get("surface", "#FFFFFF")

    checks = [
        ("btn_primary vs button_text", tokens["btn_primary"], button_text, 4.5),
        ("btn_card_action vs button_text", tokens["btn_card_action"], button_text, 4.5),
        ("text vs surface", tokens["text"], tokens["surface"], 4.5),
        ("text_muted vs surface", tokens["text_muted"], tokens["surface"], 4.5),
        ("sidebar_text_active vs sidebar", tokens["sidebar_text_active"], tokens["sidebar"], 4.5),
    ]
    for name, fg, bg, threshold in checks:
        try:
            ratio = contrast_ratio(fg, bg)
            if ratio < threshold:
                issues.append(f"⚠ {name}: 对比度 {ratio:.2f} < {threshold} ({fg} on {bg})")
        except Exception as e:
            issues.append(f"⚠ {name}: 校验失败 {e}")
    return issues
