# -*- coding: utf-8 -*-
"""客房消耗品开箱预填标准 — 期初盘点向导用。"""
from __future__ import annotations

# (名称, 单位, 参考成本 USD, 补货阈值)
DEFAULT_CONSUMABLE_ROWS: list[tuple[str, str, float, int]] = [
    ("毛巾", "条", 2.0, 20),
    ("浴巾", "条", 4.0, 10),
    ("洗发水（小瓶）", "瓶", 0.5, 30),
    ("沐浴露（小瓶）", "瓶", 0.5, 30),
    ("香皂", "块", 0.3, 30),
    ("牙刷", "套", 0.5, 40),
    ("梳子", "把", 0.3, 20),
    ("矿泉水", "瓶", 0.5, 48),
    ("卷纸", "卷", 0.3, 30),
    ("拖鞋", "双", 1.5, 20),
]

# 房型 tier → 消耗品名称 → 每次入住配备数量
_TIER_STANDARD: dict[str, int] = {
    "毛巾": 2,
    "浴巾": 1,
    "洗发水（小瓶）": 1,
    "沐浴露（小瓶）": 1,
    "香皂": 1,
    "牙刷": 2,
    "梳子": 1,
    "矿泉水": 2,
    "卷纸": 1,
    "拖鞋": 2,
}

_TIER_DOUBLE: dict[str, int] = {
    "毛巾": 2,
    "浴巾": 1,
    "洗发水（小瓶）": 1,
    "沐浴露（小瓶）": 1,
    "香皂": 1,
    "牙刷": 2,
    "梳子": 1,
    "矿泉水": 2,
    "卷纸": 2,
    "拖鞋": 2,
}

_TIER_PREMIUM: dict[str, int] = {
    "毛巾": 4,
    "浴巾": 2,
    "洗发水（小瓶）": 2,
    "沐浴露（小瓶）": 2,
    "香皂": 2,
    "牙刷": 4,
    "梳子": 2,
    "矿泉水": 4,
    "卷纸": 2,
    "拖鞋": 4,
}

_TYPE_TIER: dict[str, str] = {
    "twin": "standard",
    "twin_bed": "standard",
    "business": "standard",
    "double": "double",
    "deluxe": "double",
    "family": "premium",
    "suite": "premium",
    "president": "premium",
}

_TIER_MATRIX: dict[str, dict[str, int]] = {
    "standard": _TIER_STANDARD,
    "double": _TIER_DOUBLE,
    "premium": _TIER_PREMIUM,
}


def tier_for_type_id(type_id: str) -> str:
    return _TYPE_TIER.get((type_id or "").strip(), "standard")


def standard_qty_for(type_id: str, consumable_name: str) -> int:
    tier = tier_for_type_id(type_id)
    matrix = _TIER_MATRIX.get(tier, _TIER_STANDARD)
    return int(matrix.get(consumable_name, 0))


def default_consumable_seed_rows() -> list[tuple[str, str, float, int]]:
    return list(DEFAULT_CONSUMABLE_ROWS)
