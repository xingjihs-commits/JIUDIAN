# [UI-REDESIGN] 2026-06-15 改动: 新增迷你标签条 MiniTabStrip
"""mini_tab_strip.py — 迷你标签条 (32px)

设计原则：
  - 解决原 workspace.tabs.tabBar().hide() 导致的位置感知缺失问题
  - 可见标签条让用户一眼知道"我在哪"，点击即可切换
  - 32px 紧凑高度，不浪费垂直空间
  - 当前标签底部有 2px accent 色条指示器
  - 标签过多时显示左右滚动箭头
  - 质感：圆角微标签、hover 浮起效果、切换动画

布局结构：
  ┌──────────────────────────────────────────────────────────┐
  │ [◀] [前台中心] [总览] [财务] [报表] ... [▶]              │
  └──────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QPushButton, QLabel, QWidget,
    QSizePolicy, QScrollArea,
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QSize
from PySide6.QtGui import QFont

from design_tokens import _p


MINI_TAB_STRIP_HEIGHT = 42
MINI_TAB_H = 34
MINI_TAB_MAX_VISIBLE = 18


class MiniTabButton(QPushButton):
    """迷你标签按钮：底部 2px 指示条 + hover 浮起。"""

    def __init__(self, text: str, index: int, parent=None):
        super().__init__(text, parent)
        self._tab_index = index
        self.setObjectName("MiniTabButton")
        self.setFixedHeight(MINI_TAB_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setMinimumWidth(64)
        self.setMaximumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._active = False

    @property
    def tab_index(self) -> int:
        return self._tab_index

    def set_active(self, active: bool) -> None:
        """设置激活态：底部 2px accent 色条。"""
        self._active = active
        self.setChecked(active)
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class MiniTabStrip(QFrame):
    """迷你标签条：显示 WorkspaceDock 的标签页，提供位置感知。

    信号：
      tab_clicked(int) — 用户点击了第 index 个标签

    用法：
      strip = MiniTabStrip()
      strip.set_tabs(["前台中心", "总览", "财务", ...])
      strip.set_active_index(0)
      strip.tab_clicked.connect(workspace.tabs.setCurrentIndex)
    """

    tab_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MiniTabStrip")
        self.setMinimumHeight(MINI_TAB_STRIP_HEIGHT)
        self.setMaximumHeight(MINI_TAB_STRIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(0)

        # 左滚动按钮
        self.btn_scroll_left = QPushButton("◀")
        self.btn_scroll_left.setObjectName("MiniTabScrollBtn")
        self.btn_scroll_left.setFixedSize(24, MINI_TAB_H)
        self.btn_scroll_left.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scroll_left.hide()
        self.btn_scroll_left.clicked.connect(self._scroll_left)
        lay.addWidget(self.btn_scroll_left)

        # 标签滚动区
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("MiniTabScrollArea")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setFixedHeight(MINI_TAB_STRIP_HEIGHT - 2)

        self._tab_container = QWidget()
        self._tab_container.setObjectName("MiniTabContainer")
        self._tab_lay = QHBoxLayout(self._tab_container)
        self._tab_lay.setContentsMargins(0, 0, 0, 0)
        self._tab_lay.setSpacing(2)
        self._tab_lay.addStretch()

        self._scroll_area.setWidget(self._tab_container)
        lay.addWidget(self._scroll_area, stretch=1)

        from ui_surface import fd_apply_scroll_area
        fd_apply_scroll_area(self._scroll_area, bg_key="surface")

        # 右滚动按钮
        self.btn_scroll_right = QPushButton("▶")
        self.btn_scroll_right.setObjectName("MiniTabScrollBtn")
        self.btn_scroll_right.setFixedSize(24, MINI_TAB_H)
        self.btn_scroll_right.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scroll_right.hide()
        self.btn_scroll_right.clicked.connect(self._scroll_right)
        lay.addWidget(self.btn_scroll_right)

        self._tab_buttons: list[MiniTabButton] = []
        self._active_index = -1

    def set_tabs(self, labels: list[str]) -> None:
        """设置标签列表（全量替换）。"""
        # 清除旧标签
        while self._tab_lay.count() > 1:  # 保留末尾 stretch
            item = self._tab_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._tab_buttons.clear()

        # 创建新标签
        for i, label in enumerate(labels[:MINI_TAB_MAX_VISIBLE]):
            btn = MiniTabButton(label, i)
            btn.clicked.connect(lambda checked=False, idx=i: self._on_tab_click(idx))
            self._tab_lay.insertWidget(i, btn)
            self._tab_buttons.append(btn)

        # 如果标签数超过可见区域，显示滚动按钮
        self._update_scroll_buttons()
        self.updateGeometry()

    def set_active_index(self, index: int) -> None:
        """设置当前激活的标签索引。"""
        self._active_index = index
        for i, btn in enumerate(self._tab_buttons):
            btn.set_active(i == index)

        # 滚动到激活标签可见
        if 0 <= index < len(self._tab_buttons):
            self._ensure_visible(index)

    def _on_tab_click(self, index: int) -> None:
        """标签点击处理。"""
        self.set_active_index(index)
        self.tab_clicked.emit(index)

    def _scroll_left(self) -> None:
        sb = self._scroll_area.horizontalScrollBar()
        sb.setValue(sb.value() - 100)

    def _scroll_right(self) -> None:
        sb = self._scroll_area.horizontalScrollBar()
        sb.setValue(sb.value() + 100)

    def _ensure_visible(self, index: int) -> None:
        """确保指定标签在可见区域内。"""
        if index >= len(self._tab_buttons):
            return
        btn = self._tab_buttons[index]
        self._scroll_area.ensureWidgetVisible(btn, 50, 0)

    def _update_scroll_buttons(self) -> None:
        """根据标签数量决定是否显示滚动按钮。"""
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._check_scroll_need)

    def _check_scroll_need(self) -> None:
        try:
            content_w = self._tab_container.width()
            viewport_w = self._scroll_area.viewport().width()
            needs_scroll = content_w > viewport_w + 10
            self.btn_scroll_left.setVisible(needs_scroll)
            self.btn_scroll_right.setVisible(needs_scroll)
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._check_scroll_need()
