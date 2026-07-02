# -*- coding: utf-8 -*-
"""qss — QSS 模板与编译器。

职责：管理 base.template.qss + 应用补丁 + 编译输出 4 套主题 QSS。
不读 DB / 不导出 token 查询函数（在 runtime）。

子模块：
    _static_tokens.py — 静态 token（字体大小 / 圆角，非色板）
    _patches.py       — 补丁加载器
    compiler.py       — 编译器主入口
"""
from .compiler import compile_theme, compile_all, clear_cache, verify_compiled, write_compiled

__all__ = ["compile_theme", "compile_all", "clear_cache", "verify_compiled", "write_compiled"]
