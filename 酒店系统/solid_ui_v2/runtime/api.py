# -*- coding: utf-8 -*-
"""runtime/api — 对外查询 API。

current_tokens() / get_token() 是核心入口。
缓存策略：按主题名缓存，换主题后调用 invalidate_cache() 清空。
"""
from __future__ import annotations

from ..themes import THEMES, DEFAULT_THEME
from ..themes._schema import DEFAULT_FALLBACK
from ._resolver import current_theme_name, set_theme_resolver
from ._aliases import resolve_alias
from .expand import expand_tokens

# 主题 token 缓存（key = theme_name）
_token_cache: dict[str, dict] = {}


def invalidate_cache() -> None:
    """清空 token 缓存。换主题后必须调用。"""
    _token_cache.clear()


def current_tokens() -> dict:
    """返回当前主题完整 token dict（含派生）。带缓存。"""
    name = current_theme_name()
    if name in _token_cache:
        return _token_cache[name]
    pal = dict(THEMES.get(name, THEMES[DEFAULT_THEME]))
    tokens = expand_tokens(pal)
    _token_cache[name] = tokens
    return tokens


def get_token(key: str, fallback: str = "") -> str:
    """查询单个 token。等价于 v1 的 _p(key)。

    查询顺序：直接键 → 别名映射 → DEFAULT_FALLBACK → fallback 参数。
    """
    tokens = current_tokens()
    if key in tokens:
        return str(tokens[key])
    aliased = resolve_alias(key)
    if aliased in tokens:
        return str(tokens[aliased])
    if fallback:
        return fallback
    return DEFAULT_FALLBACK.get(key, "")
