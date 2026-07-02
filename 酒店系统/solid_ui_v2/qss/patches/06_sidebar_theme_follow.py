# -*- coding: utf-8 -*-
"""06_sidebar_theme_follow — 侧栏背景跟随主题色。

v1: 侧栏固定中性深色（4 主题都是 #2C3E50 系），区分度低
v2: 侧栏取 primary 深色调（@sidebar@ token 已在主题 dict 中定义）
    - mist:  #1F3D5C 雾蓝
    - shade: #21402F 墨绿
    - glow:  #4A2535 玫红
    - ink:   #0C1218 墨黑

本补丁不直接改 QSS（@sidebar@ token 已在模板中被引用），
仅作迁移期保险：若 QSS 中残留 v1 硬编码侧栏色，替换为 @sidebar@。
"""
from __future__ import annotations
import re

# v1 旧侧栏色（迁移期保险）
_V1_LEGACY = ["#2C3E50", "#2D3D35", "#3A2A32", "#151C22"]

# QFrame#LeftSidebar 块内的硬编码侧栏色 → @sidebar@
_SIDEBAR_BG = re.compile(
    r"(QFrame#LeftSidebar[\s\S]*?background-color:\s*)(#[0-9A-Fa-f]{6})",
    re.MULTILINE,
)


def apply(qss: str) -> str:
    def _replacer(m: re.Match) -> str:
        color = m.group(2)
        if color in _V1_LEGACY:
            return f"{m.group(1)}@sidebar@"
        return m.group(0)

    return _SIDEBAR_BG.sub(_replacer, qss)
