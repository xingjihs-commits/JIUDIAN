# -*- coding: utf-8 -*-
"""02_navbtn_active_accent — 侧栏 active 态改用 accent。

v1: QFrame#NavBtnFrame[active="true"] 用 @gold_thread@ 作左色条
v2: 改用 @accent@ — 让侧栏 active 跟随主题碰撞色

为什么改：金线收敛到 3 锚点（BrandSep / LedgerDock / btn_anchor），
侧栏 active 是高频交互，应用主题 accent 色，4 套主题在侧栏就能看出性格。

幂等性：所有正则用 [^}]*? 限定在单个 QSS 块内，避免跨块误伤。
"""
from __future__ import annotations
import re

# active 块内的左色条：@gold_thread@ → @accent@
# 用 [^}]*? 限定在 NavBtnFrame[active] 块内（不跨 }）
_BORDER_LEFT = re.compile(
    r'(QFrame#NavBtnFrame\[active="true"\]\s*\{[^}]*?'
    r'border-left:\s*3px\s*solid\s*)@gold_thread@',
)

# active 块内的背景：@primary_10pct@ → @sidebar_nav_active_bg@
_BG = re.compile(
    r'(QFrame#NavBtnFrame\[active="true"\]\s*\{[^}]*?'
    r'background-color:\s*)@primary_10pct@',
)

# active 块内 NavBtnIcon 颜色：@accent@ → @sidebar_active_icon@
# 精确匹配 "QFrame#NavBtnFrame[active="true"] QLabel#NavBtnIcon { ... color: @accent@"
_ICON = re.compile(
    r'(QFrame#NavBtnFrame\[active="true"\]\s+QLabel#NavBtnIcon\s*\{[^}]*?'
    r'color:\s*)@accent@',
)


def apply(qss: str) -> str:
    qss, _ = _BORDER_LEFT.subn(r"\1@accent@", qss)
    qss, _ = _BG.subn(r"\1@sidebar_nav_active_bg@", qss)
    qss, _ = _ICON.subn(r"\1@sidebar_active_icon@", qss)
    return qss
