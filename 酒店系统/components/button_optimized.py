"""v4 按键系统 — PREMIUM §2.1（颜色已迁移至 design_tokens 主题感知）

v2 修复（2026-06-24）：
  • 主题切换不刷新：监听 bus.theme_changed 信号，自动重新生成 QSS
  • 强制 44px：删除 max(h, 44) 强制，尊重 SIZES 表设计（small=32/medium=40/large=48）
  • warning 类型按钮：原用 accent(浅蓝)反语义，改用 warn(蜜金) 符合警告语义
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QPushButton

from design_tokens import _p

logger = logging.getLogger(__name__)

BUTTON_USAGE_MAP = {
    ("收款", "critical"): ("primary", "large"),
    ("发卡", "critical"): ("primary", "large"),
    ("确认", "critical"): ("primary", "large"),
    ("完成", "high"): ("success", "medium"),
    ("保存", "high"): ("primary", "medium"),
    ("打印", "normal"): ("secondary", "medium"),
    ("取消", "normal"): ("secondary", "medium"),
    ("删除", "warning"): ("warning", "small"),
    ("帮助", "low"): ("ghost", "small"),
    ("更多", "low"): ("ghost", "small"),
}


class ButtonSystem:
    SIZES = {
        "large": {"height": 48, "padding_h": 20, "font_size": 14, "font_weight": 600, "min_width": 100},
        "medium": {"height": 40, "padding_h": 16, "font_size": 13, "font_weight": 500, "min_width": 80},
        "small": {"height": 32, "padding_h": 12, "font_size": 12, "font_weight": 500, "min_width": 60},
    }

    @staticmethod
    def generate_qss(btn_type: str, size: str) -> str:
        size_def = ButtonSystem.SIZES[size]
        accent = _p("gold_thread")

        if btn_type == "primary":
            bg = _p("btn_primary"); bg_h = _p("btn_primary_hover"); bg_a = _p("btn_primary_hover")
            fg = _p("surface"); fg_h = _p("surface"); fg_a = _p("surface")
            fg_d = _p("disabled_fg"); bg_d = _p("disabled_bg")
            border = "none"; border_h = "none"
        elif btn_type == "secondary":
            bg = _p("surface"); bg_h = _p("bg"); bg_a = _p("surface")
            fg = _p("text"); fg_h = _p("text"); fg_a = _p("text")
            fg_d = _p("disabled_fg"); bg_d = _p("disabled_bg")
            border = f"1px solid {_p('border')}"; border_h = f"1px solid {_p('text_muted')}"
        elif btn_type == "success":
            bg = _p("amount_positive"); bg_h = _p("amount_positive"); bg_a = _p("amount_positive")
            fg = _p("surface"); fg_h = _p("surface"); fg_a = _p("surface")
            fg_d = _p("disabled_fg"); bg_d = _p("disabled_bg")
            border = "none"; border_h = "none"
        elif btn_type == "warning":
            # 修复：原用 accent(浅蓝)反语义，改用 warn(蜜金) 符合警告语义
            bg = _p("warn"); bg_h = _p("warn"); bg_a = _p("warn")
            fg = _p("surface"); fg_h = _p("surface"); fg_a = _p("surface")
            fg_d = _p("disabled_fg"); bg_d = _p("disabled_bg")
            border = "none"; border_h = "none"
        elif btn_type == "danger":
            bg = _p("danger"); bg_h = _p("danger"); bg_a = _p("danger")
            fg = _p("surface"); fg_h = _p("surface"); fg_a = _p("surface")
            fg_d = _p("disabled_fg"); bg_d = _p("disabled_bg")
            border = "none"; border_h = "none"
        else:  # ghost
            bg = "transparent"; bg_h = _p("bg"); bg_a = _p("surface")
            fg = _p("text_muted"); fg_h = _p("text"); fg_a = _p("text")
            fg_d = _p("disabled_fg"); bg_d = "transparent"
            border = "none"; border_h = "none"

        return f"""
        QPushButton {{
            background-color: {bg};
            color: {fg};
            border: {border};
            border-radius: 6px;
            padding: 0 {size_def['padding_h']}px;
            min-height: {size_def['height']}px;
            min-width: {size_def['min_width']}px;
            font-size: {size_def['font_size']}px;
            font-weight: {size_def['font_weight']};
        }}
        QPushButton:hover {{
            background-color: {bg_h};
            color: {fg_h};
            border: {border_h};
        }}
        QPushButton:pressed {{
            background-color: {bg_a};
            color: {fg_a};
        }}
        QPushButton:disabled {{
            background-color: {bg_d};
            color: {fg_d};
        }}
        QPushButton:focus {{
            border: 2px solid {accent};
        }}
        """


class OptimizedButton(QPushButton):
    def __init__(self, text: str, btn_type: str = "primary", size: str = "medium", parent=None):
        super().__init__(text, parent)
        self.btn_type = btn_type
        self.size = size
        self._apply_stylesheet()
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._play_feedback)
        # 修复：尊重 SIZES 表设计，不再强制 max(h, 44)
        h = ButtonSystem.SIZES[size]["height"]
        self.setMinimumHeight(h)
        # 修复：监听主题切换，自动刷新 QSS
        try:
            from event_bus import bus
            bus.theme_changed.connect(self._on_theme_changed)
        except Exception:
            logger.debug("OptimizedButton 主题信号订阅失败", exc_info=True)

    def _apply_stylesheet(self):
        """生成并应用当前主题的 QSS。"""
        try:
            self.setStyleSheet(ButtonSystem.generate_qss(self.btn_type, self.size))
        except Exception:
            logger.debug("OptimizedButton QSS 生成失败", exc_info=True)

    def _on_theme_changed(self, _theme: str = ""):
        """主题切换后重新生成 QSS，避免颜色固定在创建时的主题。"""
        self._apply_stylesheet()

    def _play_feedback(self):
        try:
            from sound_helper import play_notify
            play_notify("click")
        except Exception:
            pass

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Space):
            self.click()
        else:
            super().keyPressEvent(event)
