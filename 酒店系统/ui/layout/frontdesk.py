"""ui/layout/frontdesk.py — 前台 UI 度量规范（FD_* 常量）

注意：BTN_COLORS / INPUT_COLORS / ROOM_STATUS_COLORS 是惰性属性，
首次访问时才调 _p()，避免模块导入时触发 design_tokens 循环依赖。
"""

from ui.tokens import SpacingDesktop, Shadow

# ── 模块级惰性存储 ──
_lazy_btn_colors: dict | None = None
_lazy_input_colors: dict | None = None
_lazy_room_status_colors: dict | None = None
_lazy_gold_thread_color = None


class _DynGoldThread:
    """惰性 gold_thread 色 — 首次 __str__ 时取色板。"""
    def __repr__(self): return _get_gold_thread_color()
    def __str__(self): return _get_gold_thread_color()


# ── 按键高度 ──
FD_BTN_H_CRITICAL = 36
FD_BTN_H_PRIMARY = 36
FD_BTN_H = 36
FD_BTN_H_LOW = 36
FD_BTN_MIN_W = 64

# ── 输入框高度 ──
FD_INPUT_H_SM = 28
FD_INPUT_H = 36
FD_INPUT_H_LG = 44

# ── 表格 ──
FD_LEDGER_ROW_H = 36
FD_LEDGER_TABLE_HEADER_H = 36

# ── 间距 ──
FD_SPACE_XS = SpacingDesktop.XS
FD_SPACE_SM = SpacingDesktop.SM
FD_SPACE_MD = SpacingDesktop.MD
FD_SPACE_LG = 20
FD_SPACE_XL = 28

# ── 卡片 ──
FD_CARD_PADDING = 16
FD_CARD_RADIUS = 10
FD_CARD_RADIUS_LG = 14
FD_CARD_SHADOW = Shadow.CARD

# ── 品牌 ──
FD_GOLD_THREAD_WIDTH = 3
FD_SECTION_BAR_H = 36
FD_SCREEN_WIDTH = 1366


def _get_gold_thread_color():
    from design_tokens import _p
    return _p("gold_thread")


FD_GOLD_THREAD_COLOR = _DynGoldThread()


def _get_btn_colors():
    global _lazy_btn_colors
    if _lazy_btn_colors is not None:
        return _lazy_btn_colors
    try:
        from design_tokens import _p
        _lazy_btn_colors = {
            "primary": {"bg": _p("primary"), "text": _p("surface"), "hover": _p("primary_hover") or _p("primary"), "shadow": Shadow.BUTTON},
            "secondary": {"bg": _p("bg"), "text": _p("text"), "hover": _p("bg"), "shadow": Shadow.NONE},
            "danger": {"bg": _p("danger"), "text": _p("surface"), "hover": _p("danger_hover") or _p("danger"), "shadow": Shadow.BUTTON},
        }
    except Exception:
        _lazy_btn_colors = {}
    return _lazy_btn_colors


BTN_COLORS = property(lambda self: _get_btn_colors())


def _get_input_colors():
    global _lazy_input_colors
    if _lazy_input_colors is not None:
        return _lazy_input_colors
    try:
        from design_tokens import _p
        _lazy_input_colors = {"bg": _p("surface"), "border": _p("border"), "border_focus": _p("accent"), "border_error": _p("danger"), "text": _p("text")}
    except Exception:
        _lazy_input_colors = {}
    return _lazy_input_colors


INPUT_COLORS = property(lambda self: _get_input_colors())


def _get_room_status_colors():
    global _lazy_room_status_colors
    if _lazy_room_status_colors is not None:
        return _lazy_room_status_colors
    try:
        from design_tokens import _p
        _lazy_room_status_colors = {"VACANT": _p("primary"), "OCCUPIED": _p("room_inhouse"), "DIRTY": _p("room_dirty"), "MAINTENANCE": _p("room_maintenance"), "OVERTIME": _p("danger")}
    except Exception:
        _lazy_room_status_colors = {}
    return _lazy_room_status_colors


ROOM_STATUS_COLORS = property(lambda self: _get_room_status_colors())
