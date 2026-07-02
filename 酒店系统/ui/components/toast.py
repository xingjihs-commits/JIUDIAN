"""ui/components/toast.py — Toast 通知组件（单例）

合并 toast_widget.py + toast_notify.py，统一为单例 Toast 管理。
用法:
    from ui.components.toast import toast
    toast.info("操作成功")
    toast.warning("库存不足")
"""

from __future__ import annotations

from enum import IntEnum
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QPoint, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QGraphicsOpacityEffect


class ToastType(IntEnum):
    INFO = 0
    SUCCESS = 1
    WARNING = 2
    ERROR = 3


_TOAST_COLORS = {
    "info": ("card", "text", "border", ""),
    "success": ("amount_positive", "surface", "amount_positive", "✅ "),
    "warning": ("warn", "surface", "warn", "⚠ "),
    "error": ("danger", "surface", "danger", "❌ "),
}

_DEFAULT_DUR = {ToastType.INFO: 3000, ToastType.SUCCESS: 2400, ToastType.WARNING: 3600, ToastType.ERROR: 4500}

_LEVEL_TO_STR = {ToastType.INFO: "info", ToastType.SUCCESS: "success", ToastType.WARNING: "warning", ToastType.ERROR: "error"}


def _p_safe(key: str, default: str) -> str:
    try:
        from design_tokens import _p
        return _p(key, default)
    except Exception:
        return default


class _ToastWidget(QWidget):
    """浮层 toast — 动画进出，四态色板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ToastOverlay")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(f"QWidget#ToastOverlay{{background:{_p_safe('card','#FFF')};border:1px solid {_p_safe('border','#E0E0E0')};border-radius:12px;}}")
        self.setMinimumHeight(52)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        self.lbl = QLabel(font=QFont("Segoe UI", 12), styleSheet=f"color:{_p_safe('text','#1A1A1A')};background:transparent;", alignment=Qt.AlignCenter)
        layout.addWidget(self.lbl)
        self.hide()

    def show_toast(self, text: str, dur: int = 0, level: str = "info") -> None:
        bg_key, fg_key, border_key, prefix = _TOAST_COLORS.get(level, _TOAST_COLORS["info"])
        bg = _p_safe(bg_key, "#FFF")
        fg = _p_safe(fg_key, "#1A1A1A")
        border = _p_safe(border_key, "#E0E0E0")
        if dur <= 0:
            dur = _DEFAULT_DUR.get(ToastType.__members__.get(level.upper(), ToastType.INFO), 3000)
        self.setStyleSheet(f"QWidget#ToastOverlay{{background:{bg};border:1px solid {border};border-radius:12px;}}")
        self.lbl.setStyleSheet(f"color:{fg};background:transparent;font-weight:600;")
        self.lbl.setText(f"{prefix}{text}")
        self.adjustSize()
        if self.parent():
            pr = self.parent().rect()
            w = min(400, pr.width() - 40); self.setGeometry((pr.width() - w) // 2, -10, w, 44)
        self.show()
        self.raise_()
        target_y = 12
        fade_in = QPropertyAnimation(self._opacity, b"opacity", self)
        fade_in.setDuration(240); fade_in.setStartValue(0.0); fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)
        slide_in = QPropertyAnimation(self, b"pos", self)
        slide_in.setDuration(240); slide_in.setStartValue(QPoint(self.x(), target_y + 12)); slide_in.setEndValue(QPoint(self.x(), target_y))
        slide_in.setEasingCurve(QEasingCurve.OutCubic)
        enter = QParallelAnimationGroup(self)
        enter.addAnimation(fade_in); enter.addAnimation(slide_in)
        enter.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)
        QTimer.singleShot(dur, self._hide)

    def _hide(self):
        fade_out = QPropertyAnimation(self._opacity, b"opacity", self)
        fade_out.setDuration(200); fade_out.setStartValue(1.0); fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.InCubic)
        slide_out = QPropertyAnimation(self, b"pos", self)
        slide_out.setDuration(200); slide_out.setStartValue(self.pos()); slide_out.setEndValue(QPoint(self.x(), 24))
        slide_out.setEasingCurve(QEasingCurve.InCubic)
        leave = QParallelAnimationGroup(self)
        leave.addAnimation(fade_out); leave.addAnimation(slide_out)
        leave.finished.connect(self.hide)
        leave.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)


class ToastManager:
    """单例 Toast 管理器 — 同时支持浮层 Widget 和全局消息。"""

    _instance: Optional[ToastManager] = None
    _parent: Optional[QWidget] = None

    @classmethod
    def instance(cls) -> ToastManager:
        if cls._instance is None:
            cls._instance = ToastManager()
        return cls._instance

    @classmethod
    def set_parent(cls, parent: QWidget) -> None:
        cls._parent = parent

    def show(self, text: str, ttype: ToastType = ToastType.INFO, duration: int = 3000) -> None:
        level = _LEVEL_TO_STR.get(ttype, "info")
        if duration <= 0:
            duration = _DEFAULT_DUR.get(ttype, 3000)
        w = _ToastWidget(self._parent)
        w.show_toast(text, duration, level)

    def info(self, text: str, dur: int = 0): self.show(text, ToastType.INFO, dur)
    def success(self, text: str, dur: int = 0): self.show(text, ToastType.SUCCESS, dur)
    def warning(self, text: str, dur: int = 0): self.show(text, ToastType.WARNING, dur)
    def error(self, text: str, dur: int = 0): self.show(text, ToastType.ERROR, dur)


# 全局单例
toast = ToastManager.instance()
