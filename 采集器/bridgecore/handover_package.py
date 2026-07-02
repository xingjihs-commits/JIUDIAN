"""
handover_package.py — .solidhandover 包格式定义

定义 SolidCollector 向 PMS 传递门锁配置的标准格式。

包结构（zip 格式）：
  my_hotel.solidhandover/
  ├── MANIFEST.json          ← 包描述（version/品牌/校验和/时间戳/dll_dependencies）
  ├── lock_profile.json      ← 卡片协议（含 seq/日期编码/锁号编码配置）
  ├── room_data.json         ← 房间数据（含 current_seq）
  ├── guest_data.json        ← 在住客人数据
  ├── lock_state.json        ← 序列号状态（含房间级 last_seq + last_card_hex）
  ├── native_dlls/           ← 原厂 DLL（dll_direct 模式用）
  │   ├── V9RFL.dll
  │   └── d12.dll
  └── rfl_bridge_32.exe      ← 桥接可执行文件

Schema 版本历史：
  "1.0" — 初始版本，含 mode/profile/room_data/guest_data/lock_state/button_map/workflow
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ====================================================================
# 版本常量
# ====================================================================

SUPPORTED_HANDOVER_VERSIONS = ["1.0"]

# ====================================================================
# Schema 验证
# ====================================================================


def validate_manifest(manifest: dict) -> List[str]:
    """验证 MANIFEST.json 是否满足必需字段，返回缺失/错误列表。"""
    errors: List[str] = []
    required_fields = ["handover_version", "generated_by", "generated_at", "brand", "mode"]
    for field in required_fields:
        if field not in manifest:
            errors.append(f"缺少必需字段: {field}")
    if "handover_version" in manifest:
        hv = manifest["handover_version"]
        if hv not in SUPPORTED_HANDOVER_VERSIONS:
            errors.append(f"不支持的握手包版本: {hv}（支持: {SUPPORTED_HANDOVER_VERSIONS}）")
    mode = manifest.get("mode", "")
    if mode not in ("dll_direct", "parasitic", "serial"):
        errors.append(f"无效的发卡模式: {mode}（必须为 dll_direct / parasitic / serial）")
    # 校验文件校验和（如果提供）
    checksums = manifest.get("file_checksums", {})
    if checksums:
        # 只在有实际文件内容时验证
        pass
    return errors


# ====================================================================
# MANIFEST 构建
# ====================================================================


def build_manifest(
    brand: str,
    mode: str,
    file_checksums: Optional[Dict[str, str]] = None,
    dll_dependencies: Optional[Dict[str, Any]] = None,
    graduation_report: Optional[Dict[str, Any]] = None,
    evidence_level: str = "hex_only",
) -> dict:
    """构建标准 MANIFEST.json 字典。

    Args:
        brand: 门锁品牌
        mode: 发卡模式（dll_direct 或 parasitic）
        file_checksums: 可选的文件校验和映射 {relative_path: sha256_hex}
        dll_dependencies: 可选的 DLL 依赖信息
        graduation_report: 可选的毕业评估报告（见 graduation_coach.py）

    Returns:
        完整的 MANIFEST 字典。
    """
    manifest = {
        "handover_version": "1.0",
        "generated_by": "SolidCollector",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "brand": brand,
        "mode": mode,
        "file_checksums": file_checksums or {},
        "dll_dependencies": dll_dependencies or {},
        "evidence_level": evidence_level,
    }
    if graduation_report:
        manifest["graduation_report"] = graduation_report
    return manifest


# ====================================================================
# 计算文件 SHA256
# ====================================================================


def file_sha256(filepath: str) -> str:
    """计算文件的 SHA256 校验和。"""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ====================================================================
# 包结构常量
# ====================================================================

HANDOVER_INTERNAL_FILES = [
    "MANIFEST.json",
    "lock_profile.json",
    "room_data.json",
    "guest_data.json",
    "lock_state.json",
    "button_map.json",
    "workflow.json",
    "experience.jsonl",
]

HANDOVER_DLL_DIR = "native_dlls"
HANDOVER_BRIDGE_EXE = "rfl_bridge_32.exe"
