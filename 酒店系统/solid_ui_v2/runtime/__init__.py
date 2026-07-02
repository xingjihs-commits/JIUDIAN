# -*- coding: utf-8 -*-
"""runtime — 主题运行时层。

职责：查询当前主题、缓存、派生 token、对比度校验。
不读 QSS、不读 DB 业务数据（仅读 theme 配置项）、不依赖 Qt。

子模块（每个单一职责，< 200 行）：
    _resolver.py   — DB 读取注入点 + 当前主题名查询
    _aliases.py    — v1 → v2 token 别名映射（纯数据）
    color_utils.py — 颜色工具函数（hex 转换 / 亮度 / 对比度）
    expand.py      — 派生 token 计算（透明色 / 按钮 / 兼容别名）
    contrast.py    — WCAG AA 对比度校验
    api.py         — 对外 API（current_tokens / get_token）
"""
from .api import (
    current_theme_name,
    current_tokens,
    get_token,
    invalidate_cache,
    set_theme_resolver,
)
from .contrast import validate_contrast

__all__ = [
    "current_theme_name",
    "current_tokens",
    "get_token",
    "invalidate_cache",
    "set_theme_resolver",
    "validate_contrast",
]
