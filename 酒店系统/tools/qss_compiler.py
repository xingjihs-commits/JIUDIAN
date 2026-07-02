# -*- coding: utf-8 -*-
"""QSS模板编译器 — 将 base.template.qss + 主题色板 → 编译为最终 QSS。

用法:
    from tools.qss_compiler import compile_qss, compile_all_themes

    # 编译单个主题
    qss = compile_qss("mist")

    # 预编译全部四主题（启动时一次调用）
    THEME_QSS = compile_all_themes()

设计：
    - 模板中 @token_name@ 占位符被对应主题色板值替换
    - @font.sm@ / @font.md@ / @radius.sm@ 等结构化 token 也被替换
    - 替换后的 QSS 被缓存，主题切换无需重新编译
"""

from __future__ import annotations

import os
import re
import sys
# tools/ 目录下执行时需要项目根目录在 sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from theme_palette import THEMES, resolve_theme_name, _replace_qss_vars, theme_tokens

# ── 结构化Token（非色板，固定值）──
_STATIC_TOKENS = {
    # 字体大小
    "font.2xl": "26px",
    "font.xl": "20px",
    "font.lg": "16px",
    "font.md": "14px",
    "font.sm": "12px",
    "font.xs": "10px",
    # 圆角
    "radius.sm": "6px",
    "radius.md": "8px",
    "radius.lg": "12px",
    # 危险色/错误色（回退用，实际来自色板）
    "error": "#C47E7A",
}

# ── 缓存 ──
_COMPILED_CACHE: dict[str, str] = {}


def clear_qss_cache() -> None:
    """清除QSS编译缓存（色板热更新后调用）。"""
    _COMPILED_CACHE.clear()


def _resolve_token(key: str, tokens: dict) -> str:
    """解析单个 @token@ 占位符。"""
    if key in tokens:
        return str(tokens[key])
    if key in _STATIC_TOKENS:
        return _STATIC_TOKENS[key]
    # 未匹配的token保持原样（避免白屏）
    return f"@{key}@"


def _compile(template: str, tokens: dict) -> str:
    """将模板中的 @token@ 替换为 tokens 字典中的值。"""
    def replacer(match):
        return _resolve_token(match.group(1), tokens)
    return re.sub(r"@(\w+(?:\.\w+)*)@", replacer, template)


def compile_qss(theme_name: str | None = None) -> str:
    """编译指定主题的 QSS。

    Args:
        theme_name: 主题名 ("mist" / "shade" / "glow" / "ink")，默认当前DB主题

    Returns:
        完整的 QSS 字符串
    """
    resolved = resolve_theme_name(theme_name)

    if resolved in _COMPILED_CACHE:
        return _COMPILED_CACHE[resolved]

    tokens = theme_tokens(resolved)

    # 读取模板文件
    import os
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(base_dir)
    template_path = os.path.join(project_root, "themes", "base.template.qss")

    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()
    else:
        # 模板尚未创建时的回退 — 加载现成主题QSS
        fallback_path = os.path.join(project_root, "themes", f"{resolved}.qss")
        if os.path.exists(fallback_path):
            with open(fallback_path, "r", encoding="utf-8") as f:
                qss = f.read()
        else:
            qss = "/* QSS: no template or fallback found */"

    if os.path.exists(template_path):
        qss = _compile(template, tokens)

    _COMPILED_CACHE[resolved] = qss
    return qss


def compile_all_themes() -> dict[str, str]:
    """预编译全部四主题。

    Returns:
        {"mist": qss_string, "shade": qss_string, ...}
    """
    return {name: compile_qss(name) for name in THEMES}


# ── 工具：从现有 QSS 反向生成模板 ──
def extract_template_from_existing(reference_theme: str = "mist") -> str:
    """以指定主题QSS为基础，将色值替换为 @token_name@ 占位符，生成模板。

    用于一次性迁移：从4个重复QSS → 1个模板。
    """
    import os
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(base_dir)
    qss_path = os.path.join(project_root, "themes", f"{reference_theme}.qss")

    if not os.path.exists(qss_path):
        raise FileNotFoundError(f"未找到参考QSS: {qss_path}")

    with open(qss_path, "r", encoding="utf-8") as f:
        qss = f.read()

    # 获取参考主题的色板（用于反向查找 hex值 → token名）
    tokens = theme_tokens(reference_theme)

    # 按值长度降序排列（长色值可能包含短色值前缀，优先匹配长的）
    sorted_tokens = sorted(tokens.items(), key=lambda x: -len(str(x[1])))

    result = qss
    for token_name, token_value in sorted_tokens:
        token_str = str(token_value)
        # 跳过太短的值（<4字符容易误匹配如 "bg" "0"等）
        if len(token_str) < 4:
            continue
        # 仅替换十六进制颜色值和 rgba() 值
        if token_str.startswith("#") or token_str.startswith("rgba"):
            result = result.replace(token_str, f"@{token_name}@")

    # 替换静态 token
    for static_key, static_val in sorted(_STATIC_TOKENS.items(), key=lambda x: -len(x[1])):
        result = result.replace(static_val, f"@{static_key}@")

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "extract":
        template = extract_template_from_existing()
        out_path = sys.argv[2] if len(sys.argv) > 2 else "base.template.qss"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(template)
        print(f"模板已写入 {out_path} ({len(template)} 字符)")
    else:
        for name, qss in compile_all_themes().items():
            print(f"{name}: {len(qss)} 字符")