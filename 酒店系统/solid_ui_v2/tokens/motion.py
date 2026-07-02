# -*- coding: utf-8 -*-
"""动效系统 — 3 档时长 × 4 档缓动。"""
from __future__ import annotations
from enum import Enum


class Duration(str, Enum):
    """动画时长（ms 字符串）。"""
    INSTANT = "0ms"     # 无动画（禁用态切换）
    FAST = "120ms"      # 微交互（hover、focus、按下）
    NORMAL = "200ms"    # 标准过渡（展开、切换）
    SLOW = "360ms"      # 大幅过渡（页签切换、抽屉）


class Easing(str, Enum):
    """缓动函数。"""
    LINEAR = "linear"
    EASE = "ease"
    STANDARD = "cubic-bezier(0.4, 0.0, 0.2, 1)"
    DECELERATE = "cubic-bezier(0.0, 0.0, 0.2, 1)"
    ACCELERATE = "cubic-bezier(0.4, 0.0, 1, 1)"
