# -*- coding: utf-8 -*-
"""[v4 实现层] 本文件是 v4 前台 UI 度量规范的实际实现（非 re-export 垫片）。

前台 UI 度量规范 — 四时之色主题

保留目的：
- 提供完整 FD_* 常量 + BTN_COLORS / INPUT_COLORS / ROOM_STATUS_COLORS 实现。
- 由 frontdesk_ui.py（部分 re-export + 本地扩展）兼容旧 import 路径。
- 由 tabs/frontdesk/payment_v4.py 直接 import FD_BTN_H_CRITICAL。
- 由 tabs/hotel_overview_tab_v4.py 直接 import FD_SPACE_LG / FD_SPACE_MD。

注意：本文件不是 re-export 垫片，是 v4 实现本体。删了它 frontdesk_ui.py / payment_v4.py /
hotel_overview_tab_v4.py 全部 import 崩溃。v4 命名表明它是 v3 重构后的新版本，并非死代码。

当前引用方（rg 查）：
- frontdesk_ui.py:21 `from frontdesk_ui_v4 import (BTN_COLORS, FD_BTN_H, ...)`
- frontdesk_ui.py:320 `from frontdesk_ui_v4 import FD_INPUT_H_LG`（局部 import）
- tabs/frontdesk/payment_v4.py:8 `from frontdesk_ui_v4 import FD_BTN_H_CRITICAL`
- tabs/hotel_overview_tab_v4.py:26 `from frontdesk_ui_v4 import FD_SPACE_LG, FD_SPACE_MD`
"""
from __future__ import annotations

from design_tokens import SpacingDesktop, Shadow, _p

FD_SCREEN_WIDTH = 1366
FD_BTN_H_CRITICAL = 36
FD_BTN_H_PRIMARY = 36
FD_BTN_H = 36
FD_BTN_H_LOW = 36
FD_BTN_MIN_W = 64
FD_INPUT_H_SM = 28
FD_INPUT_H = 36
FD_INPUT_H_LG = 44
FD_LEDGER_ROW_H = 36
FD_LEDGER_TABLE_HEADER_H = 36
FD_SPACE_XS = SpacingDesktop.XS
FD_SPACE_SM = SpacingDesktop.SM
FD_SPACE_MD = SpacingDesktop.MD
FD_SPACE_LG = 20
FD_SPACE_XL = 28
FD_CARD_PADDING = 16
FD_CARD_RADIUS = 10
FD_CARD_RADIUS_LG = 14
FD_CARD_SHADOW = Shadow.CARD
FD_GOLD_THREAD_WIDTH = 3
FD_SECTION_BAR_H = 36


def _get_gold_thread_color():
    return _p("gold_thread")


FD_GOLD_THREAD_COLOR = lambda: _p("gold_thread")


def _get_btn_colors():
    try:
        return {
            "primary": {"bg": _p("primary"), "text": _p("surface"), "hover": _p("primary_hover") or _p("primary"), "shadow": Shadow.BUTTON},
            "secondary": {"bg": _p("bg"), "text": _p("text"), "hover": _p("bg"), "shadow": Shadow.NONE},
            "danger": {"bg": _p("danger"), "text": _p("surface"), "hover": _p("danger_hover") or _p("danger"), "shadow": Shadow.BUTTON},
        }
    except Exception: return {}


BTN_COLORS = _get_btn_colors()


def _get_input_colors():
    try:
        return {"bg": _p("surface"), "border": _p("border"), "border_focus": _p("accent"), "border_error": _p("danger"), "text": _p("text")}
    except Exception: return {}


INPUT_COLORS = _get_input_colors()


def _get_room_status_colors():
    try:
        return {"VACANT": _p("primary"), "OCCUPIED": _p("room_inhouse"), "DIRTY": _p("room_dirty"), "MAINTENANCE": _p("room_maintenance"), "OVERTIME": _p("danger")}
    except Exception: return {}


ROOM_STATUS_COLORS = _get_room_status_colors()
