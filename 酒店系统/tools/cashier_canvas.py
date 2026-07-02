"""收银 Checkin 参考画布 — 先定版后排版，再按视口等比缩放。

设计基准：右侧工作台内 CheckinTab 视口 520×720（split 约 55% 时常见宽度）。
所有尺寸为 scale=1.0 时的像素值；运行时 multiply by compute_checkin_scale().
"""
from __future__ import annotations

# ── 参考画布（排版稿）────────────────────────────────────────────
CHECKIN_DESIGN_W = 520
CHECKIN_DESIGN_H = 720

SCALE_MIN = 0.82
SCALE_MAX = 1.15

# 垂直分区（设计稿 Y，不含 Hub 顶栏）
CHECKIN_BANNER_H = 110          # 房号 + 身份 + 快捷 三行
CHECKIN_TOP_RATIO = 0.58        # 上：办单区
CHECKIN_BOTTOM_RATIO = 0.42     # 下：流水|交班

# ── 控件基准高度（scale=1.0）──────────────────────────────────
CHECKIN_BANNER_PAD_V = 8
CHECKIN_INPUT_H = 30
CHECKIN_INPUT_AMOUNT_H = 44  # v7.8: 与 FD_INPUT_H_LG 统一
CHECKIN_BTN_QUICK_H = 36  # v7.8: 统一 36px
CHECKIN_BTN_COMMIT_H = 36  # v7.8: 统一 36px
CHECKIN_STICKY_FOOTER_H = 49
CHECKIN_SECTION_BAR_H = 32
CHECKIN_LEDGER_ROW_H = 30
CHECKIN_LEDGER_HEADER_H = 28
CHECKIN_LEDGER_FILTER_H = 28
CHECKIN_LEDGER_ROWS = 5
CHECKIN_PAY_TILE_H = 44
CHECKIN_PAY_MORE_H = 30
CHECKIN_SHIFT_INFO_H = 36
CHECKIN_SHIFT_NOTE_H = 44
CHECKIN_FOLIO_HEADER_H = 34
CHECKIN_FOLIO_ROW_H = 36
CHECKIN_FOLIO_MIN_ROWS = 3

# 身份输入框设计宽度
CHECKIN_IDENTITY_W = {
    "name": 200,
    "phone": 200,
    "id_card": 240,
    "more_btn": 36,
}


def compute_checkin_scale(viewport_w: int, viewport_h: int) -> float:
    """按参考画布等比缩放，限制在 SCALE_MIN~SCALE_MAX。"""
    if viewport_w < 80 or viewport_h < 80:
        return 1.0
    s = min(viewport_w / CHECKIN_DESIGN_W, viewport_h / CHECKIN_DESIGN_H)
    return max(SCALE_MIN, min(SCALE_MAX, s))


def px(value: int | float, scale: float) -> int:
    return max(1, round(float(value) * scale))


def checkin_bottom_dock_min(scale: float) -> int:
    """底栏最小高度（随 scale）。"""
    inner = (
        CHECKIN_SECTION_BAR_H + 6 + CHECKIN_LEDGER_FILTER_H + 6
        + CHECKIN_LEDGER_HEADER_H + CHECKIN_LEDGER_ROW_H * CHECKIN_LEDGER_ROWS
        + 12
    )
    return px(inner, scale)


def checkin_zone_heights(viewport_h: int, scale: float) -> dict[str, int]:
    """整页高度 → banner + split 可用高度（供初始化估算）。"""
    banner_h = px(CHECKIN_BANNER_H, scale)
    sep = 1
    rest = max(0, viewport_h - banner_h - sep)
    top, bottom = checkin_split_sizes(rest, scale)
    return {"banner": banner_h, "top": top, "bottom": bottom, "rest": rest}


def checkin_split_sizes(split_h: int, scale: float) -> tuple[int, int]:
    """垂直分屏区内按 58:42 切上办单 / 下流水|交班。"""
    dock_min = checkin_bottom_dock_min(scale)
    if split_h < 80:
        return max(120, split_h // 2), dock_min
    bottom = max(dock_min, int(split_h * CHECKIN_BOTTOM_RATIO / (CHECKIN_TOP_RATIO + CHECKIN_BOTTOM_RATIO)))
    top = max(120, split_h - bottom)
    return top, bottom
