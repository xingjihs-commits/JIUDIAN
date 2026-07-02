"""tabs/frontdesk/_shared.py — 前台模块共享常量与工具函数"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from i18n import i18n

PAYMENT_DEFAULT_COUNT = 6

PAYMENT_METHODS: list[dict[str, Any]] = [
    {"id": "CASH_USD", "label": "美元现金", "icon": "$"},
    {"id": "CASH_KHR", "label": "柬币现金", "icon": "R"},
    {"id": "ABA",      "label": "ABA 转账", "icon": "B"},
    {"id": "WECHAT",   "label": "微信支付", "icon": "W"},
    {"id": "ALIPAY",   "label": "支付宝",  "icon": "A"},
    {"id": "CARD",     "label": "银行卡刷", "icon": "C"},
]


def pay_method_label(method_id: str) -> str:
    for m in PAYMENT_METHODS:
        if m["id"] == method_id:
            return m["label"]
    return method_id


def _checkin_pay_methods_combo() -> list[tuple[str, str]]:
    return [(m["id"], m["label"]) for m in PAYMENT_METHODS]


LEGACY_ACTIVE_CARD_STATUSES = ("ACTIVE", "INHOUSE")


def _status_placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


def _legacy_card_status_display(status: str) -> str:
    mapping = {
        "ACTIVE": "有效",
        "ERASED": "已擦除",
        "EXPIRED": "已过期",
        "LOST": "挂失",
        "LOST_PENDING": "挂失中",
        "INHOUSE": "在住",
    }
    return mapping.get(status, status)


def _make_collapsible_section(
    title: str,
    widget: QWidget,
    collapsed: bool = True,
) -> QFrame:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QHBoxLayout

    from design_tokens import _p

    section = QFrame()
    layout = QVBoxLayout(section)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    header = QFrame()
    header.setCursor(Qt.CursorShape.PointingHandCursor)
    header.setStyleSheet(
        f"QFrame{{background:{_p('surface_alt')};border-radius:6px;padding:8px 12px;}}"
        f"QFrame:hover{{background:{_p('hover')};}}"
    )

    hl = QHBoxLayout(header)
    hl.setContentsMargins(8, 6, 8, 6)
    arrow = QLabel(chr(9654) if collapsed else chr(9660))
    hl.addWidget(arrow)
    hl.addWidget(QLabel(title), 1)

    content = QFrame()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 4, 0, 0)
    content_layout.addWidget(widget)
    content.setVisible(not collapsed)

    layout.addWidget(header)
    layout.addWidget(content)

    def toggle():
        expanded = content.isVisible()
        content.setVisible(not expanded)
        arrow.setText(chr(9660) if not expanded else chr(9654))

    header.mousePressEvent = lambda e: toggle()
    return section
