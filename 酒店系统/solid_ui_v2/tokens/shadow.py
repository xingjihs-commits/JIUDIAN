# -*- coding: utf-8 -*-
"""阴影系统。

注意：Qt QSS 不支持 box-shadow，此处 token 主要用于：
  1. 文档参考（设计师对照）
  2. Python 端 QPainter 自绘阴影时取值
  3. 未来若改用 QML/Web 渲染可直接复用
"""
from __future__ import annotations
from enum import Enum


class Shadow(str, Enum):
    """阴影档位。值是 CSS box-shadow 字符串。"""
    NONE = "none"
    SM = "0 1px 2px rgba(0,0,0,0.04), 0 1px 3px rgba(0,0,0,0.04)"
    MD = "0 2px 6px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)"
    LG = "0 4px 16px rgba(0,0,0,0.08), 0 2px 6px rgba(0,0,0,0.04)"
    CARD = "0 1px 2px rgba(0,0,0,0.04), 0 2px 6px rgba(0,0,0,0.04)"
    BUTTON = "0 1px 2px rgba(0,0,0,0.06)"
    OVERLAY = "0 12px 32px rgba(0,0,0,0.16), 0 4px 12px rgba(0,0,0,0.08)"
