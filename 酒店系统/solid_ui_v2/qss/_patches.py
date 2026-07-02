# -*- coding: utf-8 -*-
"""qss/_patches — 补丁加载器。

补丁规范：
    - 每个补丁是一个 .py 文件，含 apply(qss: str) -> str 函数
    - 文件名格式：NN_short_name.py（NN 是序号，决定执行顺序）
    - 文件名按字母序加载，跳过 __init__.py
    - 补丁内禁止硬编码颜色（必须用 @token@ 占位符）
    - 必须幂等（同一补丁跑多次结果一致）
"""
from __future__ import annotations
import importlib.util
from pathlib import Path
from typing import Callable

_HERE = Path(__file__).resolve().parent
PATCHES_DIR = _HERE / "patches"

# 补丁函数签名
PatchFn = Callable[[str], str]


def load_patches() -> list[tuple[str, PatchFn]]:
    """加载 patches/ 目录下所有补丁，按文件名排序。

    返回 [(patch_name, apply_fn), ...]
    """
    if not PATCHES_DIR.exists():
        return []
    patches: list[tuple[str, PatchFn]] = []
    for p in sorted(PATCHES_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue
        modname = f"_qss_patch_{p.stem}"
        spec = importlib.util.spec_from_file_location(modname, p)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "apply"):
            raise RuntimeError(f"补丁 {p.name} 缺少 apply(qss: str) -> str 函数")
        patches.append((p.stem, mod.apply))
    return patches


def apply_all(qss: str) -> tuple[str, list[str]]:
    """顺序应用所有补丁。返回 (patched_qss, [patch_names])。"""
    applied: list[str] = []
    for name, fn in load_patches():
        qss = fn(qss)
        applied.append(name)
    return qss, applied
