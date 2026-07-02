"""tabs/_shared.py — 标签页共享工具函数

被 workspace_dock / inventory_tab / member_tab / frontdesk/* 等模块引用。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QWidget, QScrollArea, QFrame


def current_operator_id() -> str:
    """获取当前操作员 ID。"""
    try:
        from database import db
        return db.get_config("current_operator") or "SYSTEM"
    except Exception:
        return "SYSTEM"


def _wrap_scroll(inner: QWidget) -> QWidget:
    """将内部 widget 包装到 QScrollArea 中返回。"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(inner)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    return scroll
