"""
======================================================
ShadowGuard — 门卡系统 (v1.0)
支持：USB读卡器 / 模拟模式（无硬件时可测试）
======================================================

此文件现为兼容垫片，真实实现移至 tabs/card_system/。
原有导入路径保持不变：
    from card_system import CardSystemTab, CardService, get_driver
"""

from __future__ import annotations

import sys as _sys
import importlib as _importlib

_sys.modules.pop(__name__, None)
_pkg = _importlib.import_module('tabs.card_system')
_sys.modules[__name__] = _pkg
for _attr in dir(_pkg):
    if not _attr.startswith('_'):
        globals()[_attr] = getattr(_pkg, _attr)
del _sys, _importlib, _pkg, _attr
