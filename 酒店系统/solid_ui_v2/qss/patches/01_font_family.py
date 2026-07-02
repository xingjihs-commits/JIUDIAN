# -*- coding: utf-8 -*-
"""01_font_family — 字体族升级。

v1: Microsoft YaHei UI（Windows 默认气质，偏传统办公软件）
v2: HarmonyOS Sans SC（现代感、跨平台一致、中英文混排协调）

副作用：仅修改 font-family 声明，不改字号字重。
"""
from __future__ import annotations

# v1 主字体族（多种写法）
_V1_FONT_VARIANTS = [
    '"Microsoft YaHei UI", "Khmer OS", "Leang Eng", "PingFang SC", "Segoe UI", sans-serif',
    '"Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif',
]


def apply(qss: str) -> str:
    for v1 in _V1_FONT_VARIANTS:
        if v1 in qss:
            qss = qss.replace(v1, "@font.sans@")
    return qss
