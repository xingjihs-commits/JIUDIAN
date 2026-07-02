# -*- coding: utf-8 -*-
"""build.py — 构建脚本。

流程：校验主题 → 编译 QSS → 校验未替换 token → 写文件 → 对比度校验。
用法：python scripts/build.py
"""
from __future__ import annotations
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
# 将 solid_ui_v2 父目录加入路径，使包内 from solid_ui_v2.xxx import ... 可用
_PARENT = _ROOT.parent
sys.path.insert(0, str(_PARENT))

from solid_ui_v2.themes import THEMES
from solid_ui_v2.themes._schema import validate_theme
from solid_ui_v2.runtime import set_theme_resolver, invalidate_cache
from solid_ui_v2.runtime.contrast import validate_contrast
from solid_ui_v2.qss import compile_all, verify_compiled, write_compiled


def main() -> int:
    print("=" * 64)
    print("Solid UI v2 · 构建脚本")
    print("=" * 64)

    # 1. 校验主题 dict
    print("\n[1/5] 校验主题色板完整性")
    for name, pal in THEMES.items():
        missing = validate_theme(name, pal)
        if missing:
            print(f"  ✗ {name}: 缺失键 {missing}")
            return 1
        print(f"  ✓ {name}: 完整")

    # 2. 编译 QSS
    print("\n[2/5] 编译 4 套主题 QSS")
    try:
        all_qss = compile_all()
    except Exception as e:
        print(f"  ✗ 编译失败: {e}")
        return 1
    for name, qss in all_qss.items():
        print(f"  ✓ {name}.qss ({len(qss.encode('utf-8')) / 1024:.1f} KB)")

    # 3. 校验未替换 token
    print("\n[3/5] 校验未替换 token")
    leftover_map = verify_compiled()
    leftover_ok = True
    for name, leftovers in leftover_map.items():
        if leftovers:
            print(f"  ⚠ {name}: {len(leftovers)} 处未替换 → {leftovers[:5]}")
            leftover_ok = False
        else:
            print(f"  ✓ {name}: 全部替换完成")

    # 4. 写入 compiled/
    print("\n[4/5] 写入 compiled/ 目录")
    output_dir = _ROOT / "compiled"
    paths = write_compiled(output_dir)
    for name, p in paths.items():
        print(f"  ✓ {p.relative_to(_ROOT)}")

    # 5. 对比度校验
    print("\n[5/5] WCAG AA 对比度校验")
    contrast_ok = True
    for name in THEMES:
        set_theme_resolver(lambda n=name: n)
        invalidate_cache()
        issues = validate_contrast()
        if issues:
            print(f"  ⚠ {name}:")
            for i in issues:
                print(f"      {i}")
            contrast_ok = False
        else:
            print(f"  ✓ {name}: 全部通过")

    # 报告
    print("\n" + "=" * 64)
    print("✓ 构建成功" if leftover_ok else "⚠ 构建完成但有警告")
    print(f"  产物目录: {output_dir.relative_to(_ROOT)}")
    print(f"  主题数: {len(THEMES)}")
    print(f"  补丁数: {len(list((_ROOT / 'qss' / 'patches').glob('*.py'))) - 1}")
    print("=" * 64)
    return 0 if leftover_ok else 2


if __name__ == "__main__":
    sys.exit(main())
