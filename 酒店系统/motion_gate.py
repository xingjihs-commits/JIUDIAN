"""motion_gate.py — 高性能动效网关

统一控制所有 theme_motion 动效的启用/关闭。
消除 10+ 处散落的 try/except 导入，改为集中判断 performance_mode。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtWidgets import QWidget, QLabel, QPushButton, QStackedWidget


def _motion_enabled() -> bool:
    """动效全局开关。MOTION_ENABLED=True 启用所有装饰性动效。"""
    return True


def _lazy_import(name: str) -> Any:
    """延迟导入 theme_motion 的指定函数。"""
    try:
        from theme_motion import __dict__ as _tm
        return _tm.get(name)
    except Exception:
        return None


# ── 6 条动效的包装方法 ────────────────────────────────

def attach_primary_button_glow(btn: QPushButton) -> None:
    """主按钮悬浮辉光呼吸"""
    if not _motion_enabled():
        return
    fn = _lazy_import("attach_primary_button_glow")
    if fn:
        fn(btn)


def pulse_room_select(widget: QWidget) -> None:
    """#2 房卡 tile 选中边框过渡"""
    if not _motion_enabled():
        return
    fn = _lazy_import("pulse_room_select")
    if fn:
        fn(widget)


def animate_kpi(label: QLabel, new_value: str) -> None:
    """#3 KPI 数字变化动画"""
    if not _motion_enabled():
        label.setText(new_value)
        return
    fn = _lazy_import("animate_kpi")
    if fn:
        fn(label, new_value)
    else:
        label.setText(new_value)


def attach_stack_fade(stack: QStackedWidget) -> None:
    """#4 页面切换淡入"""
    if not _motion_enabled():
        return
    fn = _lazy_import("attach_stack_fade")
    if fn:
        fn(stack)


def shake_invalid(widget: QWidget) -> None:
    """#6 表单错误抖动"""
    if not _motion_enabled():
        return
    fn = _lazy_import("shake_invalid")
    if fn:
        fn(widget)


def install_workspace_dock_motion(workspace) -> None:
    """前台按钮呼吸辉光"""
    if not _motion_enabled():
        return
    fn = _lazy_import("install_workspace_dock_motion")
    if fn:
        fn(workspace)


def attach_primary_button_glow_many(buttons: list) -> None:
    """批量挂载按钮辉光"""
    if not _motion_enabled():
        return
    fn = _lazy_import("attach_primary_button_glow_many")
    if fn:
        fn(buttons)


class LovableToast:
    """占位类。动效关闭时返回空，让调用方走备用方案。"""

    def __new__(cls, *args, **kwargs):
        if not _motion_enabled():
            return None
        real = _lazy_import("LovableToast")
        if real is None:
            return None
        return real(*args, **kwargs)


def attach_card_shadow(card: QWidget, level: str = "sm") -> None:
    """卡片阴影 — Phase 4 影子层次"""
    if not _motion_enabled():
        return
    fn = _lazy_import("attach_card_shadow")
    if fn:
        fn(card, level)


# ── Phase 2 微交互网关 ─────────────────────────────────────

def attach_button_press_effect(btn: QPushButton) -> None:
    """2.1 按钮按压缩放"""
    if not _motion_enabled():
        return
    fn = _lazy_import("attach_button_press_effect")
    if fn:
        fn(btn)


def attach_input_glow(input_widget) -> None:
    """2.4 输入框焦点光晕"""
    if not _motion_enabled():
        return
    fn = _lazy_import("attach_input_glow")
    if fn:
        fn(input_widget)


def attach_table_hover_transition(table) -> None:
    """2.5 表格行 hover 过渡"""
    if not _motion_enabled():
        return
    fn = _lazy_import("attach_table_hover_transition")
    if fn:
        fn(table)


def attach_sidebar_pulse(btn: QPushButton) -> None:
    """2.6 侧栏 active 指示条脉冲"""
    if not _motion_enabled():
        return
    fn = _lazy_import("attach_sidebar_pulse")
    if fn:
        fn(btn)
