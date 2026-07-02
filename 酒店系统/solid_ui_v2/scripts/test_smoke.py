# -*- coding: utf-8 -*-
"""test_smoke.py — 冒烟测试。

不依赖 pytest，直接 python scripts/test_smoke.py 运行。
覆盖 8 项：tokens 独立性 / 主题完整性 / 主题名解析 / runtime 派生 /
QSS 编译 / 对比度 / 侧栏跟随 / 补丁幂等。
"""
from __future__ import annotations
import sys
import os
from pathlib import Path

# Windows GBK 兼容：强制 stdout 使用 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_PARENT = _ROOT.parent
sys.path.insert(0, str(_PARENT))


def test_tokens_independent():
    """tokens 包不依赖 Qt / DB。"""
    from solid_ui_v2 import tokens
    assert tokens.Spacing.MD == 12
    assert tokens.FontSize.MD == "13px"
    assert tokens.Radius.MD == "8px"
    assert tokens.ZIndex.MODAL == 400
    print("  ✓ tokens 包独立可用")


def test_themes_complete():
    """4 套主题色板键完整。"""
    from solid_ui_v2.themes import THEMES
    from solid_ui_v2.themes._schema import validate_theme
    assert len(THEMES) == 4
    for name, pal in THEMES.items():
        missing = validate_theme(name, pal)
        assert not missing, f"{name} 缺失: {missing}"
    print("  ✓ 4 套主题色板完整")


def test_theme_resolve():
    """旧主题名能正确解析到新四主题。"""
    from solid_ui_v2.themes import resolve_theme_name
    assert resolve_theme_name("old_money") == "shade"
    assert resolve_theme_name("sakura") == "glow"
    assert resolve_theme_name("daylight") == "mist"
    assert resolve_theme_name("obsidian") == "ink"
    assert resolve_theme_name(None) == "mist"
    assert resolve_theme_name("unknown_xyz") == "mist"
    print("  ✓ 旧主题名解析正确")


def test_runtime_tokens():
    """runtime 派生 token 正确。"""
    from solid_ui_v2.runtime import set_theme_resolver, current_tokens, get_token, invalidate_cache

    set_theme_resolver(lambda: "mist")
    invalidate_cache()
    tokens = current_tokens()

    assert tokens["primary"] == "#3D6A93"
    assert tokens["accent"] == "#C89B5C"
    assert tokens["sidebar"] == "#1F3D5C"
    assert "primary_10pct" in tokens
    assert tokens["primary_10pct"].startswith("rgba(")
    assert "btn_primary" in tokens
    assert get_token("bg_root") == tokens["bg"]
    assert get_token("primary") == "#3D6A93"
    print("  ✓ runtime 派生 token + 别名正确")


def test_qss_compile():
    """QSS 编译 4 套通过，无未替换 token，补丁全部生效。"""
    from solid_ui_v2.qss import compile_all, verify_compiled, clear_cache
    import re
    clear_cache()

    all_qss = compile_all()
    assert len(all_qss) == 4

    leftover = verify_compiled()
    for name, items in leftover.items():
        assert not items, f"{name} 有未替换 token: {items}"

    mist_qss = all_qss["mist"]
    assert "HarmonyOS Sans SC" in mist_qss
    assert "border-left: 3px solid #C89B5C" in mist_qss

    header_heights = re.findall(r'QFrame#SmartHeader\s*\{[^}]*?min-height:\s*(\S+?)[;\s}]', mist_qss)
    assert len(header_heights) > 0, "Header 高度未找到"
    print(f"    Header 高度: {header_heights}")

    kpi_sizes = re.findall(r'QLabel#ReportKpiValue\s*\{[^}]*?font-size:\s*(\d+)px', mist_qss)
    assert len(kpi_sizes) > 0, "KPI 数字未找到"
    print(f"    KPI 数字: {kpi_sizes}px")

    assert not re.findall(r'\d+@[a-z_]+(?:\.[a-z]+)?@', mist_qss), "07 号补丁未生效：仍有 N@token@ 拼接"
    print("  ✓ QSS 编译通过，7 个补丁全部生效")


def test_patches_idempotent():
    """每个补丁幂等：跑两次结果一致。"""
    from solid_ui_v2.qss._patches import load_patches
    template_path = _ROOT.parent / "themes" / "base.qss.template"
    if not template_path.exists():
        template_path = _ROOT / "qss" / "base.template.qss"
    template = template_path.read_text(encoding="utf-8")
    for name, fn in load_patches():
        once = fn(template)
        twice = fn(once)
        assert once == twice, f"补丁 {name} 不幂等"
    print(f"  ✓ {len(load_patches())} 个补丁全部幂等")


def test_contrast():
    """WCAG AA 对比度全部通过。"""
    from solid_ui_v2.runtime import set_theme_resolver, invalidate_cache
    from solid_ui_v2.runtime.contrast import validate_contrast
    from solid_ui_v2.themes import THEMES

    for name in THEMES:
        set_theme_resolver(lambda n=name: n)
        invalidate_cache()
        issues = validate_contrast()
        assert not issues, f"{name} 对比度问题: {issues}"
    print("  ✓ 4 套主题对比度全部达标")


def test_sidebar_follows_theme():
    """侧栏色跟随主题——4 套主题侧栏色不同。"""
    from solid_ui_v2.runtime import set_theme_resolver, current_tokens, invalidate_cache
    from solid_ui_v2.themes import THEMES

    sidebar_colors = {}
    for name in THEMES:
        set_theme_resolver(lambda n=name: n)
        invalidate_cache()
        tokens = current_tokens()
        sidebar_colors[name] = tokens["sidebar"]

    assert len(set(sidebar_colors.values())) == 4, f"侧栏色重复: {sidebar_colors}"
    assert sidebar_colors["mist"] == "#1F3D5C"
    assert sidebar_colors["shade"] == "#21402F"
    assert sidebar_colors["glow"] == "#4A2535"
    assert sidebar_colors["ink"] == "#0C1218"
    print("  ✓ 侧栏跟随主题色，4 套互不相同")


def main() -> int:
    print("=" * 60)
    print("Solid UI v2 · 冒烟测试")
    print("=" * 60)

    tests = [
        ("tokens 独立性", test_tokens_independent),
        ("主题色板完整性", test_themes_complete),
        ("主题名解析", test_theme_resolve),
        ("runtime 派生 token", test_runtime_tokens),
        ("QSS 编译", test_qss_compile),
        ("补丁幂等性", test_patches_idempotent),
        ("对比度校验", test_contrast),
        ("侧栏跟随主题", test_sidebar_follows_theme),
    ]

    failed = 0
    for name, fn in tests:
        print(f"\n[测试] {name}")
        try:
            fn()
        except Exception as e:
            print(f"  ✗ 失败: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print("✓ 全部通过" if failed == 0 else f"✗ {failed} 个测试失败")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
