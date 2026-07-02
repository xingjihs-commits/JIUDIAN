# -*- coding: utf-8 -*-
"""qss/compiler — QSS 编译器主入口。

编译流程：
    1. 读 base.template.qss（4 主题共享骨架，含 @token@ 占位符）
    2. 顺序应用 patches/ 下所有补丁
    3. 用主题 token 字典替换 @token@ 占位符
    4. 缓存编译结果（key = theme_name）

API：
    compile_theme(name)  → str             编译指定主题
    compile_all()        → dict[str,str]   编译全部 4 主题
    clear_cache()        → None            清空缓存
    verify_compiled()    → dict[str,list]  校验未替换 token
"""
from __future__ import annotations
import re
from pathlib import Path

from ..themes import THEMES, resolve_theme_name
from ..themes._schema import DEFAULT_FALLBACK
from ..runtime.expand import expand_tokens
from ._static_tokens import STATIC_TOKENS
from ._patches import apply_all

_HERE = Path(__file__).resolve().parent
# 使用 v1 的 base.qss.template（4836 行，token 更全）
TEMPLATE_PATH = _HERE.parent.parent / "themes" / "base.qss.template"
# 如果 v1 模板不存在，回退到 v2 本地模板
if not TEMPLATE_PATH.exists():
    TEMPLATE_PATH = _HERE / "base.template.qss"
OUTPUT_DIR = _HERE.parent / "compiled"

# 缓存
_compiled_cache: dict[str, str] = {}
_patched_template: str | None = None


def clear_cache() -> None:
    """清空编译缓存（含补丁后模板缓存）。换主题或改模板后调用。"""
    _compiled_cache.clear()
    global _patched_template
    _patched_template = None


def _get_patched_template() -> str:
    """读模板 + 应用补丁。结果缓存（补丁不变时只跑一次）。"""
    global _patched_template
    if _patched_template is not None:
        return _patched_template
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"QSS 模板不存在: {TEMPLATE_PATH}")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    patched, _ = apply_all(template)
    _patched_template = patched
    return patched


def _substitute_tokens(qss: str, tokens: dict) -> str:
    """替换 @token@ 占位符。按 key 长度降序，避免短 key 误伤长 key。"""
    all_keys = set(tokens.keys()) | set(STATIC_TOKENS.keys())
    for key in sorted(all_keys, key=lambda k: -len(k)):
        placeholder = f"@{key}@"
        if placeholder in qss:
            value = _resolve_token(key, tokens)
            qss = qss.replace(placeholder, value)
    return qss


def _resolve_token(key: str, tokens: dict) -> str:
    if key in tokens:
        return str(tokens[key])
    if key in STATIC_TOKENS:
        return STATIC_TOKENS[key]
    if key in DEFAULT_FALLBACK:
        return DEFAULT_FALLBACK[key]
    return f"@{key}@"   # 未识别保持原样（verify 时报警）


def compile_theme(theme_name: str | None = None) -> str:
    """编译指定主题的 QSS。None = 当前主题。"""
    resolved = resolve_theme_name(theme_name)
    if resolved in _compiled_cache:
        return _compiled_cache[resolved]

    patched = _get_patched_template()
    pal = dict(THEMES[resolved])
    tokens = expand_tokens(pal)
    qss = _substitute_tokens(patched, tokens)

    _compiled_cache[resolved] = qss
    return qss


def compile_all() -> dict[str, str]:
    """编译全部 4 套主题。"""
    return {name: compile_theme(name) for name in THEMES}


def verify_compiled() -> dict[str, list[str]]:
    """校验编译后 QSS 是否还有未替换的 @token@。"""
    result: dict[str, list[str]] = {}
    for name in THEMES:
        qss = compile_theme(name)
        leftovers = re.findall(r"@(\w+(?:\.\w+)*)@", qss)
        result[name] = sorted(set(leftovers))
    return result


def write_compiled(output_dir: Path | None = None) -> dict[str, Path]:
    """编译并写入 .qss 文件。返回 {theme_name: file_path}。"""
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for name, qss in compile_all().items():
        path = out / f"{name}.qss"
        path.write_text(qss, encoding="utf-8")
        result[name] = path
    return result
