"""
bridgecore/profile_generator.py — Profile 生成器（Collector 版）

从 protocol_learner 的学习结果生成品牌配置 JSON。
使用 analysis_types.ProbeResult / ChannelInfo。
"""

from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from .analysis_types import ChannelInfo, ProbeResult
from .protocol_learner import ProtocolLearnResult

logger = logging.getLogger(__name__)


def generate_profile(
    learn_result: ProtocolLearnResult,
    probe_result: ProbeResult,
    channel_info: Optional[ChannelInfo] = None,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    brand = probe_result.brand_guess or "auto_detected"
    adapter_id = f"auto_{timestamp}"

    profile: dict[str, Any] = {
        "brand": brand,
        "adapter_id": adapter_id,
        "description": f"Solid Collector 自动生成 — {timestamp}",
        "payload_size": learn_result.payload_size or 16,
        "detect": {
            "files": [probe_result.dll_name] if probe_result.dll_name else [],
        },
        "dll": {
            "path": probe_result.dll_name,
        },
        "physical_channel": channel_info.channel_type if channel_info else "dll",
        "magic": learn_result.magic_hex or "",
        "site_code": {
            "field": "bytes_2_at_offset_4",
            "mask": learn_result.site_mask_hex or "0x3FFF",
            "emergency_bit": learn_result.emergency_bit_hex or "0x4000",
        },
        "lock_no": {
            "field": "bytes_2_at_offset_6",
            "encoding": learn_result.lock_no_encoding or "hex_be",
        },
        "date_encoding": learn_result.date_encoding or "",
        "begin_time_encoding": "prousb" if learn_result.date_encoding == "legacy_prousb" else "",
        "salt": {
            "offset": learn_result.layout.get("salt_offset", 8),
            "default": learn_result.salt_default_hex or "00",
        },
        "layout": _build_layout(learn_result),
        "card_types": _build_card_types(learn_result),
        "checksum": {
            "algorithm": learn_result.checksum_algorithm or "none",
            "offset": learn_result.checksum_offset,
            "length": learn_result.checksum_length,
        },
        "supported": True,
        "confidence": round(learn_result.confidence, 2),
        "probe_meta": {
            "generated_by": "SolidCollector",
            "generated_at": timestamp,
        },
    }
    return profile


def _build_layout(learn: ProtocolLearnResult) -> dict[str, Any]:
    layout: dict[str, Any] = {}
    if learn.layout.get("site_offset") is not None:
        layout["site_offset"] = learn.layout["site_offset"]
        layout["site_len"] = learn.layout.get("site_length", 2)
    if learn.layout.get("lock_no_offset") is not None:
        layout["lock_no_offset"] = learn.layout["lock_no_offset"]
        layout["lock_no_len"] = learn.layout.get("lock_no_length", 2)
    layout.setdefault("site_offset", 4)
    layout.setdefault("site_len", 2)
    layout.setdefault("lock_no_offset", 6)
    layout.setdefault("lock_no_len", 2)
    layout.setdefault("salt_offset", 8)
    layout.setdefault("type_offset", 9)
    layout.setdefault("body_offset", 10)
    layout.setdefault("body_len", 4)
    layout.setdefault("chk_offset", 14)
    layout.setdefault("chk_len", 2)
    layout.setdefault("date_offset", learn.layout.get("date_offset", 12))
    layout.setdefault("date_len", learn.layout.get("date_len", 2))
    return layout


def _build_card_types(learn: ProtocolLearnResult) -> dict[str, Any]:
    card_types: dict[str, Any] = {}
    for card_type, info in learn.card_types.items():
        entry = {"type_byte_high": info.get("type_byte_high", 0), "body_len": 4}
        # 对齐 GenericLockAdapter 格式：携带校验覆盖和签名字节
        if info.get("checksum_override"):
            entry["checksum_override"] = info["checksum_override"]
        if info.get("signature_byte15"):
            entry["signature_byte15"] = info["signature_byte15"]
        if info.get("auth_token_repeat"):
            entry["auth_token_repeat"] = True
        if info.get("description"):
            entry["description"] = info["description"]
        card_types[card_type] = entry
    if not card_types:
        card_types = {
            "guest":      {"type_byte_high": 0x6, "body_len": 4},
            "master":     {"type_byte_high": 0xB, "body_len": 4, "signature_byte15": "FB"},
            "building":   {"type_byte_high": 0xC, "body_len": 4, "signature_byte15": "FB"},
            "floor":      {"type_byte_high": 0xD, "body_len": 4, "signature_byte15": "FB"},
            "emergency":  {"type_byte_high": 0xA, "body_len": 4},
        }
    return card_types


def save_profile(profile: dict[str, Any], output_path: str | Path) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    logger.info("[ProfileGenerator] Profile 已保存到 %s", path)
    return str(path)
