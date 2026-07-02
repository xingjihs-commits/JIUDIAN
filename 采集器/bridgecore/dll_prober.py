"""
bridgecore/dll_prober.py — DLL 功能探测器

包装 lock_deploy/dll_probe.py 的能力为 BridgeCore 内的结构化接口。
提供：扫描 DLL 导出表 → 按模式分类 → 返回探测结果。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

from .physical_channel import ProbeResult, _import_dll_probe

logger = logging.getLogger(__name__)


def probe_dll(install_dir: str | Path) -> ProbeResult:
    """探测安装目录中的 DLL 文件。

    复用 lock_deploy/dll_probe.py 的完整探测链：
    1. 找 DLL
    2. 枚举导出函数
    3. 按关键词匹配
    4. 按 DLL 名硬编码匹配
    5. 生成候选配置

    Returns:
        ProbeResult 结构化结果
    """
    result = ProbeResult()

    try:
        dp = _import_dll_probe()
        if dp is None:
            logger.warning("[DllProber] lock_deploy/dll_probe.py 未找到")
            return result

        raw = dp.probe(str(install_dir))
    except Exception as e:
        logger.error("[DllProber] 探测异常: %s", e)
        return result

    if not raw.get("detected"):
        return result

    result.dll_name = Path(raw.get("dll_path", "")).name
    result.dll_path = raw.get("dll_path", "")
    result.exports = raw.get("exports", [])
    result.classified = raw.get("matched_functions", {})
    result.hardcoded_match = raw.get("hardcoded_fallback", {})
    result.brand_guess = raw.get("brand_guess", "")
    result.confidence = raw.get("confidence", 0.0)
    result.can_issue = raw.get("can_issue", False)

    return result


def probe_dll_by_path(dll_path: str | Path) -> ProbeResult:
    """直接探测指定 DLL 文件（不需要安装目录）。"""
    result = ProbeResult()

    dll_path = Path(dll_path)
    if not dll_path.is_file():
        logger.warning("[DllProber] DLL 不存在: %s", dll_path)
        return result

    result.dll_name = dll_path.name
    result.dll_path = str(dll_path.resolve())

    try:
        dp = _import_dll_probe()
        if dp:
            exports = dp.enumerate_exports(str(dll_path))
            result.exports = exports

            if exports:
                result.classified = dp.match_v9_patterns(exports)

            result.hardcoded_match = dp.match_by_dll_name(dll_path.name)
            result.brand_guess = dp._guess_brand(str(dll_path.parent), dll_path.name)

            merged = dict(result.hardcoded_match)
            merged.update(result.classified)
            result.confidence = dp._compute_confidence(merged)
            result.can_issue = bool(
                result.classified.get("guest_card") or
                result.classified.get("write") or
                result.hardcoded_match.get("guest_card") or
                result.hardcoded_match.get("write")
            )
    except Exception as e:
        logger.error("[DllProber] 探测 %s 异常: %s", dll_path, e)

    return result
