# -*- coding: utf-8 -*-
"""runtime/_resolver — DB 读取注入点 + 当前主题名查询。

设计：DB 读取通过 injectable resolver，便于单测替换为内存实现。
生产环境启动时调用 set_theme_resolver(lambda: db.get_config("theme"))。
"""
from __future__ import annotations
from typing import Callable, Optional

from ..themes import resolve_theme_name

# 主题名解析器：默认返回 None（不连 DB）
_ThemeResolver = Callable[[], Optional[str]]
_theme_resolver: _ThemeResolver = lambda: None


def set_theme_resolver(resolver: _ThemeResolver) -> None:
    """注入主题名解析器。

    生产环境：set_theme_resolver(lambda: db.get_config("theme"))
    单测环境：set_theme_resolver(lambda: "mist")
    """
    global _theme_resolver
    _theme_resolver = resolver


def current_theme_name() -> str:
    """返回当前主题键（mist/shade/glow/ink）。

    调用注入的 resolver 读 DB；解析失败回退 DEFAULT_THEME。
    """
    try:
        raw = _theme_resolver()
        return resolve_theme_name(raw)
    except Exception:
        return resolve_theme_name(None)
