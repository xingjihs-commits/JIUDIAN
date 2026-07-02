# -*- coding: utf-8 -*-
"""brand_assets.py — Solid 品牌 LOGO v8.4 磨砂玻璃 + 金属浮雕

LOGO 设计（参考 Firefly 风格）：
  1. 磨砂玻璃底板（半透明深色 + 径向渐变）
  2. 玻璃顶部高光（左上来光）
  3. 金属边框（accent 色，左上亮右下暗渐变）
  4. 金属浮雕 S 字母（阴影 + 渐变 + 高光）
  5. 底部 accent 色条（三面亮渐隐）
  6. 跟随主题变色（_p() token 驱动）
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QFont, QPen, QBrush,
    QLinearGradient, QRadialGradient, QPainterPath,
)
from PySide6.QtWidgets import QLabel


def _get_theme_color(key, fallback="#7B8C9E"):
    """获取主题色 — 委托 design_tokens._p()，内部已有集中兜底。"""
    from design_tokens import _p
    val = _p(key)
    return val if val else fallback


def _draw_frosted_glass_logo(painter, size, *, primary, accent, dark_bg=True):
    """绘制磨砂玻璃 + 金属浮雕 LOGO。"""
    margin = size * 0.06
    radius = size * 0.19
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)

    # 1. 磨砂玻璃底板
    glass_color = QColor(45, 55, 68, 180) if dark_bg else QColor(250, 248, 244, 200)
    glass_grad = QRadialGradient(size / 2, size / 2, size * 0.5)
    center = QColor(glass_color); center.setAlpha(max(0, glass_color.alpha() - 30))
    edge = QColor(glass_color); edge.setAlpha(min(255, glass_color.alpha() + 40))
    glass_grad.setColorAt(0, center)
    glass_grad.setColorAt(1, edge)
    painter.setBrush(QBrush(glass_grad))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(rect, radius, radius)

    # 2. 顶部高光
    shine = QLinearGradient(margin, margin, size * 0.7, size * 0.5)
    shine.setColorAt(0, QColor(255, 255, 255, 50))
    shine.setColorAt(0.5, QColor(255, 255, 255, 20))
    shine.setColorAt(1, QColor(255, 255, 255, 0))
    painter.setBrush(QBrush(shine))
    painter.drawRoundedRect(QRectF(margin, margin, size - 2 * margin, size * 0.4), radius, radius)

    # 3. 金属边框
    border_w = max(2, size // 50)
    aq = QColor(accent)
    a_light = QColor(aq).lighter(140)
    a_dark = QColor(aq).darker(120)
    bgrad = QLinearGradient(margin, margin, size - margin, size - margin)
    bgrad.setColorAt(0, a_light)
    bgrad.setColorAt(0.5, aq)
    bgrad.setColorAt(1, a_dark)
    painter.setBrush(QBrush(bgrad))
    outer = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    inner = QRectF(margin + border_w, margin + border_w, size - 2 * margin - 2 * border_w, size - 2 * margin - 2 * border_w)
    path = QPainterPath()
    path.addRoundedRect(outer, radius, radius)
    path.addRoundedRect(inner, max(0, radius - border_w * 0.5), max(0, radius - border_w * 0.5))
    path.setFillRule(Qt.FillRule.OddEvenFill)
    painter.drawPath(path)

    # 4. S 字母阴影
    font = QFont("Segoe UI", int(size * 0.45), QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor(0, 0, 0, 100))
    painter.drawText(QRectF(3, 3, size, size), Qt.AlignmentFlag.AlignCenter, "S")

    # 5. S 字母金属渐变
    tgrad = QLinearGradient(0, margin + border_w, 0, size - margin - border_w)
    tgrad.setColorAt(0, a_light)
    tgrad.setColorAt(1, aq)
    painter.setPen(QPen(QBrush(tgrad), 1))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "S")

    # 6. S 字母高光
    painter.setPen(QColor(255, 255, 255, 60))
    painter.drawText(QRectF(0, -1, size, size), Qt.AlignmentFlag.AlignCenter, "S")

    # 7. 底部 accent 色条
    bar_h = max(3, size // 28)
    bar_y = size - margin - bar_h - 2
    bargrad = QLinearGradient(0, 0, size, 0)
    bt = QColor(accent); bt.setAlpha(0)
    bs = QColor(accent)
    bargrad.setColorAt(0, bt)
    bargrad.setColorAt(0.15, bs)
    bargrad.setColorAt(0.85, bs)
    bargrad.setColorAt(1, bt)
    painter.setBrush(QBrush(bargrad))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(margin + border_w + 2, bar_y, size - 2 * margin - 2 * border_w - 4, bar_h), bar_h / 2, bar_h / 2)


def make_brand_mark_label(size=32, *, prefer_sm=False, object_name="", parent=None):
    lbl = QLabel(parent)
    if object_name: lbl.setObjectName(object_name)
    lbl.setFixedSize(size, size)
    from design_tokens import _p
    primary = _p("primary")
    accent = _p("accent")
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    _draw_frosted_glass_logo(p, size, primary=primary, accent=accent, dark_bg=True)
    p.end()
    lbl.setPixmap(pixmap)
    return lbl


def make_brand_icon(size=64):
    from design_tokens import _p
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    _draw_frosted_glass_logo(p, size, primary=_p("primary"), accent=_p("accent"), dark_bg=True)
    p.end()
    return pixmap


def load_brand_pixmap(size=64):
    """加载品牌 LOGO pixmap（供 role_ui.brand_logo_label 使用）。"""
    return make_brand_icon(size)


def make_role_avatar(role, size=24, parent=None):
    lbl = QLabel(parent)
    lbl.setFixedSize(size, size)
    # 角色色标从主题色派生
    from design_tokens import _p
    colors = {
        "frontdesk": (_p("primary"), "前"),
        "manager": (_p("amount_positive"), "经"),
        "boss": (_p("accent"), "老"),
        "vendor": (_p("sidebar"), "厂"),
        "finance": (_p("warn"), "财"),
        "guest": (_p("text_muted"), "客"),
    }
    bg, char = colors.get(role, (_p("text_muted"), "?"))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setBrush(QColor(bg)); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(0, 0, size, size)
    shine = QLinearGradient(0, 0, 0, size * 0.5)
    shine.setColorAt(0, QColor(255, 255, 255, 50)); shine.setColorAt(1, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(shine)); p.drawEllipse(0, 0, size, size)
    p.setFont(QFont("Microsoft YaHei UI", int(size * 0.5), QFont.Weight.Bold))
    p.setPen(QColor(_p("surface")))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, char)
    p.end()
    lbl.setPixmap(pixmap)
    return lbl
