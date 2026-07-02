# -*- coding: utf-8 -*-
"""04_header_height — SmartHeader 高度 48 → 52px。

v1: SmartHeader 48px + MiniTabStrip 42px = 90px 铬条
v2: 合并 MiniTabStrip 进面包屑，SmartHeader 提升至 52px

为什么改：1366×768 前台显示器上原 90px 吃掉 12% 垂直空间，
v2 把"我在哪"做成面包屑一部分，删掉 MiniTabStrip，省 38px。

注意：模板里 SmartHeader 出现 2 处定义，且 v1 用了 `4@radius.md@`
拼接 hack（= 48px）。本补丁同时处理：
    1. min-height: 40px → 52px（第一处定义）
    2. min-height/max-height: 4@radius.md@ → 52px（第二处定义）
"""
from __future__ import annotations
import re

# 第一处：min-height: 40px（无 max-height 跟随）
_HEIGHT_40 = re.compile(
    r"(QFrame#SmartHeader\s*\{[^}]*?min-height:\s*)40px",
    re.MULTILINE,
)

# 第二处：min/max-height: 4@radius.md@（拼接 hack，编译后 = 48px）
_HEIGHT_RADIUS_HACK = re.compile(
    r"(QFrame#SmartHeader\s*\{[^}]*?min-height:\s*)4@radius\.md@(\s*;\s*max-height:\s*)4@radius\.md@",
    re.MULTILINE,
)

# 兜底：QWidget#SmartHeader, QFrame#SmartHeader 联合选择器
_HEIGHT_COMBO = re.compile(
    r"(QWidget#SmartHeader,\s*QFrame#SmartHeader\s*\{[^}]*?min-height:\s*)4@radius\.md@(\s*;\s*max-height:\s*)4@radius\.md@",
    re.MULTILINE,
)


def apply(qss: str) -> str:
    # 处理 40px → 52px
    qss, n1 = _HEIGHT_40.subn(r"\g<1>52px", qss)
    # 处理 4@radius.md@ 拼接 hack → 52px
    qss, n2 = _HEIGHT_COMBO.subn(r"\g<1>52px\g<2>52px", qss)
    qss, n3 = _HEIGHT_RADIUS_HACK.subn(r"\g<1>52px\g<2>52px", qss)
    return qss
