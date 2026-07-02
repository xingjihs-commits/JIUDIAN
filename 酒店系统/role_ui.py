"""角色头像与顶栏/侧栏展示辅助。"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import QLabel, QToolButton, QHBoxLayout, QVBoxLayout, QWidget

from design_tokens import _p

# (emoji, 背景色, 文字色)
ROLE_AVATAR: Dict[str, Tuple[str, str]] = {
    "boss": ("👔", "#FFFFFF"),
    "manager": ("📋", "#FFFFFF"),
    "frontdesk": ("🛎", "#FFFFFF"),
    "guest": ("👤", "#FFFFFF"),
}

# ── 品牌色常量（白文字不变，底色/金线随主题）────────────────────
BRAND_LIGHT = "#FFFFFF"   # 品牌白文字


def role_avatar_meta(role: str) -> Tuple[str, str]:
    emoji, fg = ROLE_AVATAR.get(role, ROLE_AVATAR["guest"])
    return emoji, fg


def apply_avatar_to_toolbutton(btn: QToolButton, role: str, tooltip: str = "") -> None:
    emoji, fg = role_avatar_meta(role)
    btn.setText(emoji)
    btn.setToolTip(tooltip or btn.toolTip())
    btn.setStyleSheet(
        f"QToolButton#TopAvatarInner {{"
        f"  background: transparent; color: {fg}; border: none; border-radius: 16px;"
        f"  font-size: 16px; font-weight: 600;"
        f"}}"
    )


def make_role_avatar_label(role: str, size: int = 36) -> QLabel:
    emoji, fg = role_avatar_meta(role)
    lbl = QLabel(emoji)
    lbl.setObjectName("RoleAvatarLabel")
    lbl.setFixedSize(size, size)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"background:transparent; color:{fg}; border-radius:{size // 2}px; font-size:{max(14, size // 2)}px;"
    )
    return lbl


def brand_logo_label(size: int = 36) -> QLabel:
    """Solid Seal 品牌 Mark — 与桌面图标同源。"""
    from brand_assets import load_brand_pixmap

    logo = QLabel()
    logo.setObjectName("BrandLogo")
    pix = load_brand_pixmap(size)
    if pix is not None:
        logo.setPixmap(pix)
    logo.setFixedSize(size, size)
    logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
    logo.setStyleSheet(
        f"QLabel#BrandLogo {{ "
        f"  background: {_p('sidebar')}; "
        f"  border-radius: 8px; "
        f"}}"
    )
    return logo


def make_brand_wordmark(parent=None):
    """品牌字标：Solid（白色粗体）+ PMS（烫金小字）纵向排列。"""
    w = QWidget(parent)
    w.setObjectName("BrandWordmark")
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

    sidebar_bg = _p('sidebar')
    # 「Solid」— 白色加粗主字
    title_en = QLabel("Solid")
    title_en.setObjectName("BrandWordTitle")
    title_en.setStyleSheet(
        f"QLabel#BrandWordTitle {{"
        f"  color: {BRAND_LIGHT}; font-size: 15px; font-weight: 800;"
        f"  letter-spacing: 1.5px; background: {sidebar_bg};"
        f"}}"
    )
    lay.addWidget(title_en)

    # 「PMS」— 烫金小字副标题
    title_pms = QLabel("PMS")
    title_pms.setObjectName("BrandWordSub")
    title_pms.setStyleSheet(
        f"QLabel#BrandWordSub {{"
        f"  color: {_p('gold_thread')}; font-size: 10px; font-weight: 600;"
        f"  letter-spacing: 3px; background: {sidebar_bg};"
        f"}}"
    )
    lay.addWidget(title_pms)
    return w

