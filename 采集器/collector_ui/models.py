"""
models.py — Solid 学习助手数据模型

纯数据类，不依赖 Qt。序列化/反序列化都在这里。
"""

from __future__ import annotations
from typing import Optional, Any

from .constants import CARD_KEY_MAP, CARD_FIELDS


class SampleCapture:
    """单次卡采样数据。"""

    __slots__ = (
        "card_type", "card_type_key",
        "blank_hex", "written_hex", "erased_hex",
        "room", "b_date", "e_date",
        "building_no", "floor_no", "group_no",
        "done",
    )

    def __init__(self, card_type: str):
        self.card_type     = card_type
        self.card_type_key = CARD_KEY_MAP.get(card_type, card_type)
        self.blank_hex     = ""
        self.written_hex   = ""
        self.erased_hex    = ""
        self.room          = ""
        self.b_date        = ""
        self.e_date        = ""
        self.building_no   = 0
        self.floor_no      = 0
        self.group_no      = 0
        self.done          = False

    # ── 序列化 ──────────────────────────────────────────

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的字典。

        修复：原版 to_dict() 缺少 card_type 和 done 字段，
        导致 _try_restore_autosave() 恢复时丢失卡型名和完成状态。
        """
        d: dict[str, Any] = {
            "hex": self.written_hex,
            "written_hex": self.written_hex,
            "type": self.card_type_key,
            "card_type": self.card_type,          # [FIX] 新增：恢复时需要
            "done": self.done,                     # [FIX] 新增：恢复时需要
        }
        if self.blank_hex:
            d["blank_hex"] = self.blank_hex
        if self.erased_hex:
            d["erased_hex"] = self.erased_hex
        if self.room:
            d["room"] = self.room
        if self.b_date:
            d["b_date"] = self.b_date
        if self.e_date:
            d["e_date"] = self.e_date
        if self.building_no:
            d["building_no"] = self.building_no
        if self.floor_no:
            d["floor_no"] = self.floor_no
        if self.group_no:
            d["group_no"] = self.group_no
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "SampleCapture":
        """从字典恢复 SampleCapture。

        修复：原版恢复时用 sd.get("card_type", "guest") 但 to_dict()
        不输出 card_type，导致永远默认为 "guest"。
        """
        # 优先用 card_type（新版 to_dict 有），回退到 type key 反查
        card_type = data.get("card_type", "")
        if not card_type:
            from .constants import CARD_KEY_TO_NAME
            type_key = data.get("type", "guest")
            card_type = CARD_KEY_TO_NAME.get(type_key, "客人卡")

        obj = cls(card_type)
        obj.blank_hex   = data.get("blank_hex", "")
        obj.written_hex = data.get("written_hex", "") or data.get("hex", "")
        obj.erased_hex  = data.get("erased_hex", "")
        obj.room        = data.get("room", "")
        obj.b_date      = data.get("b_date", "")
        obj.e_date      = data.get("e_date", "")
        obj.building_no = data.get("building_no", 0)
        obj.floor_no    = data.get("floor_no", 0)
        obj.group_no    = data.get("group_no", 0)
        obj.done        = data.get("done", False)
        return obj

    def is_pair_complete(self) -> bool:
        """是否已有完整的空白+已写对照。"""
        return bool(self.blank_hex and self.written_hex)

    def __repr__(self) -> str:
        return (
            f"SampleCapture({self.card_type!r}, "
            f"blank={'Y' if self.blank_hex else 'N'}, "
            f"written={'Y' if self.written_hex else 'N'}, "
            f"done={self.done})"
        )
