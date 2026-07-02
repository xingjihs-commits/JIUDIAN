# -*- coding: utf-8 -*-
"""超市内置图库加载 — PMS 前台 / Telegram 共用。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

_SHOP_ROOT = Path(__file__).resolve().parent / "assets" / "shop"


def shop_root() -> Path:
    return _SHOP_ROOT


def _resource_base() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass) / "assets" / "shop"
    return _SHOP_ROOT


def resolve_shop_image(sku: str, *, telegram: bool = False) -> Path | None:
    root = _resource_base()
    sku_u = (sku or "").strip().upper()
    if not sku_u:
        return None
    if telegram:
        p = root / "items" / f"{sku_u}_tg.jpg"
        if p.is_file():
            return p
    p = root / "items" / f"{sku_u}.png"
    return p if p.is_file() else None


def load_shop_pixmap(sku: str, size: int = 48, *, telegram: bool = False) -> QPixmap | None:
    path = resolve_shop_image(sku, telegram=telegram)
    if path is None:
        return None
    pix = QPixmap(str(path))
    if pix.isNull():
        return None
    if size > 0:
        pix = pix.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pix


def load_shop_icon(sku: str, size: int = 48) -> QIcon | None:
    pix = load_shop_pixmap(sku, size=size, telegram=False)
    if pix is None or pix.isNull():
        return None
    return QIcon(pix)
