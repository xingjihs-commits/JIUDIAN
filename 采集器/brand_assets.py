"""Solid 品牌资产加载 — Collector 与 PMS 同源 Mark。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

_ASSETS = Path(__file__).resolve().parent / "assets"


def load_brand_pixmap(size: int = 0, *, prefer_sm: bool = False) -> QPixmap | None:
    use_sm = prefer_sm or (0 < size <= 32)
    candidates = (
        [_ASSETS / "mark_sm.png", _ASSETS / "mark.png"]
        if use_sm
        else [_ASSETS / "mark.png", _ASSETS / "mark_sm.png", _ASSETS / "app_icon.png"]
    )
    for path in candidates:
        if not path.is_file():
            continue
        pix = QPixmap(str(path))
        if pix.isNull():
            continue
        if size > 0:
            pix = pix.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pix
    return None


def make_brand_mark_label(
    size: int = 40,
    *,
    prefer_sm: bool = False,
    object_name: str = "BrandMark",
) -> QLabel:
    lbl = QLabel()
    lbl.setObjectName(object_name)
    pix = load_brand_pixmap(size, prefer_sm=prefer_sm)
    if pix is not None:
        lbl.setPixmap(pix)
    lbl.setFixedSize(size, size)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl
