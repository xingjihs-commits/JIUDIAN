# -*- coding: utf-8 -*-
"""圆角系统 — 5 档，避免中间值混乱。"""
from __future__ import annotations
from enum import Enum


class Radius(str, Enum):
    """圆角档位。"""
    XS = "4px"    # 微小组件（chip 内元素、紧凑按钮）
    SM = "6px"    # 按钮、输入框
    MD = "8px"    # 普通卡片、分组框
    LG = "12px"   # 弹窗、浮卡
    XL = "16px"   # 圆润组件（chip、动画区）
