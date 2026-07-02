"""UI/UX v4 验收测试 — PREMIUM §4.2"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

from components import ButtonSystem, OptimizedButton
from ui.tokens.colors import ColorNeutral, ColorSemantic


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestButtonSizes:
    def test_button_sizes(self):
        for size, min_h in [("large", 48), ("medium", 40), ("small", 32)]:
            assert ButtonSystem.SIZES[size]["height"] >= min_h
            assert ButtonSystem.SIZES[size]["min_width"] >= 60


class TestColorContrast:
    def test_color_contrast(self):
        ratio = validate_contrast_ratio(
            ColorNeutral.TEXT_PRIMARY.value, ColorNeutral.BG_PRIMARY.value,
        )
        assert ratio >= 4.5
        ratio2 = validate_contrast_ratio(ColorSemantic.PRIMARY.value, "#FFFFFF")
        assert ratio2 >= 4.5



class TestOptimizedButton:
    def test_instantiate(self, qapp):
        btn = OptimizedButton("Test", "primary", "large")
        assert btn.minimumHeight() >= 44
