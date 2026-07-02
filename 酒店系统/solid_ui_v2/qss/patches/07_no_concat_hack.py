# -*- coding: utf-8 -*-
"""07_no_concat_hack — 清理 v1 拼接 hack。

v1 在模板里大量使用 `3@radius.sm@`（= 18px）、`2@radius.md@`（= 16px）
这类"数字 + token"拼接 hack。这是坏味道：
    1. 读模板时看不出实际值
    2. 改 radius token 会引发连锁不可控变化
    3. 违反"规规矩矩清清楚楚"原则

v2：把所有 `N@radius.xxx@` / `N@font.xxx@` 拼接替换为计算后的字面值。
    radius.sm=6, radius.md=8, radius.lg=12
    font.sm=12, font.md=13, font.lg=14, font.xl=16, font.2xl=20

注意：本补丁只处理"数字 + @token@" 拼接，不处理纯 @token@ 引用。
"""
from __future__ import annotations
import re

# token → 像素值映射（用于计算 N@token@ 拼接）
_TOKEN_PX: dict[str, int] = {
    "radius.sm": 6, "radius.md": 8, "radius.lg": 12,
    "font.sm": 12, "font.md": 13, "font.lg": 14,
    "font.xl": 16, "font.2xl": 20, "font.xs": 11,
}

# 匹配 N@token@ 拼接（N 是 1-2 位数字）
_CONCAT = re.compile(r"(\d+)@(radius\.(?:sm|md|lg)|font\.(?:xs|sm|md|lg|xl|2xl))@")


def apply(qss: str) -> str:
    def _replacer(m: re.Match) -> str:
        n = int(m.group(1))
        token = m.group(2)
        px = _TOKEN_PX.get(token, 0)
        return f"{n * px}px"

    return _CONCAT.sub(_replacer, qss)
