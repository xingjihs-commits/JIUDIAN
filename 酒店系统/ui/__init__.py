"""ui/ — Solid PMS UI 层

分层:
  ui/tokens/      设计令牌（颜色/间距/排版）
  ui/components/  原子组件（Toast 通知）
  ui/layout/      布局常量（前台度量 FD_*）
  ui/branding/    品牌配置

注意：顶层不导入 ui.layout / ui.components，避免 design_tokens 循环依赖。
需要时直接从子模块导入。
"""

# 仅导入无循环依赖的 tokens 子模块
from ui.tokens import (
    ColorPrimary, ColorSemantic, ColorNeutral, ColorState, ColorRoomStatus,
    SpacingDesktop, SpacingTablet, SpacingMobile, get_spacing,
    Typography, Fonts, BorderRadius, Shadow, Animation,
)
from ui.branding.brand import APP_NAME, APP_NAME_FULL, effective_brand

# ── 延迟导出：避免顶层 import 触发 design_tokens 循环 ──

_LAYOUT_LAZY = None
_COMPONENTS_LAZY = None


def _get_layout():
    global _LAYOUT_LAZY
    if _LAYOUT_LAZY is None:
        from ui.layout import (
            FD_BTN_H, FD_BTN_H_CRITICAL, FD_BTN_H_PRIMARY, FD_BTN_H_LOW, FD_BTN_MIN_W,
            FD_INPUT_H_SM, FD_INPUT_H, FD_INPUT_H_LG,
            FD_SPACE_XS, FD_SPACE_SM, FD_SPACE_MD, FD_SPACE_LG, FD_SPACE_XL,
            FD_CARD_PADDING, FD_CARD_RADIUS, FD_CARD_SHADOW,
            FD_GOLD_THREAD_WIDTH, FD_SECTION_BAR_H,
            BTN_COLORS, INPUT_COLORS, ROOM_STATUS_COLORS, FD_GOLD_THREAD_COLOR,
        )
        _LAYOUT_LAZY = (
            FD_BTN_H, FD_BTN_H_CRITICAL, FD_BTN_H_PRIMARY, FD_BTN_H_LOW, FD_BTN_MIN_W,
            FD_INPUT_H_SM, FD_INPUT_H, FD_INPUT_H_LG,
            FD_SPACE_XS, FD_SPACE_SM, FD_SPACE_MD, FD_SPACE_LG, FD_SPACE_XL,
            FD_CARD_PADDING, FD_CARD_RADIUS, FD_CARD_SHADOW,
            FD_GOLD_THREAD_WIDTH, FD_SECTION_BAR_H,
            BTN_COLORS, INPUT_COLORS, ROOM_STATUS_COLORS, FD_GOLD_THREAD_COLOR,
        )
    return _LAYOUT_LAZY


def _get_components():
    global _COMPONENTS_LAZY
    if _COMPONENTS_LAZY is None:
        from ui.components import ToastManager, ToastType, toast
        _COMPONENTS_LAZY = (ToastManager, ToastType, toast)
    return _COMPONENTS_LAZY


def __getattr__(name: str):
    # layout 导出
    layout_names = {
        "FD_BTN_H", "FD_BTN_H_CRITICAL", "FD_BTN_H_PRIMARY", "FD_BTN_H_LOW", "FD_BTN_MIN_W",
        "FD_INPUT_H_SM", "FD_INPUT_H", "FD_INPUT_H_LG",
        "FD_SPACE_XS", "FD_SPACE_SM", "FD_SPACE_MD", "FD_SPACE_LG", "FD_SPACE_XL",
        "FD_CARD_PADDING", "FD_CARD_RADIUS", "FD_CARD_SHADOW",
        "FD_GOLD_THREAD_WIDTH", "FD_SECTION_BAR_H",
        "BTN_COLORS", "INPUT_COLORS", "ROOM_STATUS_COLORS", "FD_GOLD_THREAD_COLOR",
    }
    if name in layout_names:
        vals = _get_layout()
        # 映射名→值
        idx_map = {
            "FD_BTN_H": 0, "FD_BTN_H_CRITICAL": 1, "FD_BTN_H_PRIMARY": 2,
            "FD_BTN_H_LOW": 3, "FD_BTN_MIN_W": 4,
            "FD_INPUT_H_SM": 5, "FD_INPUT_H": 6, "FD_INPUT_H_LG": 7,
            "FD_SPACE_XS": 8, "FD_SPACE_SM": 9, "FD_SPACE_MD": 10,
            "FD_SPACE_LG": 11, "FD_SPACE_XL": 12,
            "FD_CARD_PADDING": 13, "FD_CARD_RADIUS": 14, "FD_CARD_SHADOW": 15,
            "FD_GOLD_THREAD_WIDTH": 16, "FD_SECTION_BAR_H": 17,
            "BTN_COLORS": 18, "INPUT_COLORS": 19, "ROOM_STATUS_COLORS": 20,
            "FD_GOLD_THREAD_COLOR": 21,
        }
        return vals[idx_map[name]]

    # components 导出
    if name in ("ToastManager", "ToastType", "toast"):
        vals = _get_components()
        idx = {"ToastManager": 0, "ToastType": 1, "toast": 2}[name]
        return vals[idx]

    raise AttributeError(f"module 'ui' has no attribute '{name}'")


__all__ = [
    "ColorPrimary", "ColorSemantic", "ColorNeutral", "ColorState", "ColorRoomStatus",
    "SpacingDesktop", "SpacingMobile", "get_spacing",
    "Typography", "Fonts", "BorderRadius", "Shadow", "Animation",
    "FD_BTN_H", "FD_BTN_H_CRITICAL", "FD_BTN_H_PRIMARY", "FD_BTN_MIN_W",
    "FD_INPUT_H", "FD_INPUT_H_LG",
    "FD_SPACE_XS", "FD_SPACE_SM", "FD_SPACE_MD", "FD_SPACE_LG",
    "FD_CARD_PADDING", "FD_CARD_RADIUS", "FD_CARD_SHADOW",
    "FD_GOLD_THREAD_WIDTH", "FD_SECTION_BAR_H",
    "BTN_COLORS", "INPUT_COLORS", "ROOM_STATUS_COLORS", "FD_GOLD_THREAD_COLOR",
    "APP_NAME", "APP_NAME_FULL", "effective_brand",
    "ToastManager", "ToastType", "toast",
]
