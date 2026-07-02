# -*- coding: utf-8 -*-
"""布局常量 — 页面边距、组件高度、Z-index 层级。

这些是"硬尺度"，不随主题变化。
"""
from __future__ import annotations
from enum import IntEnum


class Layout(IntEnum):
    """页面布局常量（px）。"""
    PAGE_MARGIN = 16
    PAGE_MARGIN_COMPACT = 8
    PAGE_SPACING = 12
    SIDEBAR_WIDTH = 220
    SIDEBAR_COLLAPSED = 64
    HEADER_HEIGHT = 52        # v2：合并 miniTabStrip 后 48→52
    STATUSBAR_HEIGHT = 24
    CONTENT_MAX_WIDTH = 1480


class ComponentHeight(IntEnum):
    """组件标准高度（px）。"""
    BTN_SM = 28
    BTN_MD = 36
    BTN_LG = 44
    INPUT_SM = 28
    INPUT_MD = 36
    INPUT_LG = 44
    NAV_ITEM = 38
    TABLE_ROW = 40
    KPI_CARD = 96


class ZIndex(IntEnum):
    """Z-index 层级。每档差 100，预留扩展空间。"""
    BASE = 0
    DROPDOWN = 100
    STICKY = 200
    OVERLAY = 300
    MODAL = 400
    TOAST = 500
    TOOLTIP = 600
