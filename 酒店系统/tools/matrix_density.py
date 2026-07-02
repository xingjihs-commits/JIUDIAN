"""房态密度估算 — 输入 viewport 与卡尺寸，输出列数/约滚屏数。"""
from __future__ import annotations
from dataclasses import dataclass
import math

@dataclass
class DensityReport:
    viewport_w: int
    viewport_h: int
    card_w: int
    card_h: int
    row_gap: int
    cols: int
    rows_per_screen: int
    rooms_per_screen: int
    screens_for_rooms: float  # room_count / rooms_per_screen


def estimate(
    viewport_w: int,
    viewport_h: int,
    card_w: int,
    card_h: int,
    *,
    row_gap: int = 8,
    header_h: int = 120,   # 筛选条+状态带预留，Wave 2 接入真实值
    floor_header_h: int = 36,
    room_count: int = 100,
    floor_count: int = 10,
) -> DensityReport:
    """估算房态矩阵的显示密度。

    Args:
        viewport_w: 滚动区域可视宽度 (px)
        viewport_h: 滚动区域可视高度 (px)
        card_w: 房间卡宽度 (px)
        card_h: 房间卡高度 (px)
        row_gap: 卡片行间距 (px)
        header_h: 顶部筛选条+状态带高度预留 (px)
        floor_header_h: 楼层标题高度 (px，暂未精确使用)
        room_count: 总房间数
        floor_count: 楼层数

    Returns:
        DensityReport: 包含列数、每屏房间数、预估滚屏数
    """
    slot = max(1, card_w + row_gap)
    cols = max(2, int(viewport_w // slot))
    usable_h = max(200, viewport_h - header_h)
    row_h = card_h + row_gap
    rows_per_screen = max(1, int(usable_h // row_h))
    rps = cols * rows_per_screen
    screens = room_count / rps if rps else 999.0
    return DensityReport(
        viewport_w=viewport_w,
        viewport_h=viewport_h,
        card_w=card_w,
        card_h=card_h,
        row_gap=row_gap,
        cols=cols,
        rows_per_screen=rows_per_screen,
        rooms_per_screen=rps,
        screens_for_rooms=screens,
    )
