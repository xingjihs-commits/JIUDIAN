"""ui/tokens/colors.py — Color token definitions (reference only)

DEPRECATED — 所有 Enum 运行时不用，实际颜色来自 theme_palette.py。
仅 brand_config_v4.py / tests/ui_ux_tests.py 做历史引用。

改颜色请编辑 theme_palette.py，不动此文件。"""

from enum import Enum


class ColorPrimary(Enum):
    """
    Original brand colors — DEPRECATED, not used at runtime"""


    GOLD_LIGHT = "#D4B896"
    GOLD_STANDARD = "#C4A86A"
    GOLD_DARK = "#8B7D3F"
    GREEN_LIGHT = "#D4E8D4"
    GREEN_STANDARD = "#2C3E36"
    GREEN_DARK = "#1A2620"
    RED_LIGHT = "#FFE8E8"
    RED_STANDARD = "#E74C3C"
    RED_DARK = "#C0392B"
    BLUE_LIGHT = "#E8F4FF"
    BLUE_STANDARD = "#3498DB"
    BLUE_DARK = "#2980B9"


class ColorSemantic(Enum):
    """
    Semantic colors — DEPRECATED, not used at runtime"""


    PRIMARY = "#2C3E36"
    SECONDARY = "#C4A86A"
    SUCCESS = "#4CAF50"
    WARNING = "#FF9800"
    DANGER = "#E74C3C"
    INFO = "#3498DB"


class ColorNeutral(Enum):
    """
    Neutral colors — DEPRECATED, not used at runtime"""


    TEXT_PRIMARY = "#1A1A1A"
    TEXT_SECONDARY = "#404040"
    TEXT_TERTIARY = "#686868"
    TEXT_DISABLED = "#AAAAAA"
    TEXT_MUTED = "#999999"
    BORDER_STRONG = "#D0D0D0"
    BORDER_MEDIUM = "#E0E0E0"
    BORDER_LIGHT = "#F0F0F0"
    BG_PRIMARY = "#FFFFFF"
    BG_SECONDARY = "#F8F8F8"
    BG_TERTIARY = "#F0F0F0"


class ColorState(Enum):
    """
    State colors — DEPRECATED, not used at runtime"""


    SUCCESS_SOFT = "rgba(76, 175, 80, 0.1)"
    WARNING_SOFT = "rgba(255, 152, 0, 0.1)"
    DANGER_SOFT = "rgba(231, 76, 60, 0.08)"
    INFO_SOFT = "rgba(52, 152, 219, 0.1)"


class ColorRoomStatus(Enum):
    """
    Room status colors — DEPRECATED; runtime uses theme_palette.py"""


    VACANT = "#4CAF50"
    OCCUPIED = "#2196F3"
    DIRTY = "#FF9800"
    MAINTENANCE = "#9C27B0"
    OVERTIME = "#F44336"
