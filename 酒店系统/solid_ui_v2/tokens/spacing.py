# -*- coding: utf-8 -*-
"""间距系统 — 8pt grid。

所有间距是 4 的倍数。档位间差值固定 4px，便于推算。
"""
from __future__ import annotations
from enum import IntEnum


class Spacing(IntEnum):
    """间距档位（px）。"""
    XS = 4     # 微小内边距（chip 内、图标旁）
    SM = 8     # 标准内边距（按钮内、输入框内）
    MD = 12    # 卡片内边距、列表行间距
    LG = 16    # 区块间距、表单分组间距
    XL = 20    # 页面边距、章节间距
    XXL = 28   # 大区块间距、空状态 padding
    XXXL = 40  # 页面 hero 区、空状态大尺寸
