"""
bridgecore/protocol_processor.py — 协议处理器

根据 JSON 配置，自动套用校验算法和字段偏移。
纯数学逻辑单元，不触碰任何 IO。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .operator_lib import OPERATOR_REGISTRY, compute_checksum

logger = logging.getLogger(__name__)


class ProtocolValidationError(ValueError):
    """协议配置验证失败时抛出。"""


class ProtocolProcessor:
    """
    协议处理器 — 根据 JSON 配置，动态加载协议偏移与校验算子。

    使用方式：

        config = {
            "checksum_algorithm": "crc16_modbus",
            "offset_map": {
                "room_id": 2,
                "expire": 4,
                "auth": 8,
            },
            "payload_size": 16,
            "byte_order": "little",
        }
        proc = ProtocolProcessor(config)
        checksum = proc.apply_checksum(raw_payload)
        field = proc.extract_field(raw_payload, "room_id")
    """

    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._checksum_algo_name = config.get("checksum_algorithm", "none")
        self._offset_map = config.get("offset_map", {})
        self._payload_size = config.get("payload_size", 16)
        self._byte_order = config.get("byte_order", "big")

        # 验证算法存在
        if self._checksum_algo_name not in OPERATOR_REGISTRY:
            raise ProtocolValidationError(
                f"未知校验算法: {self._checksum_algo_name}，"
                f"可用算法: {list(OPERATOR_REGISTRY.keys())}"
            )

    # ── 属性 ────────────────────────────────────────────────

    @property
    def checksum_algorithm(self) -> str:
        return self._checksum_algo_name

    @property
    def offset_map(self) -> dict[str, Any]:
        return dict(self._offset_map)

    @property
    def payload_size(self) -> int:
        return self._payload_size

    # ── 校验和 ──────────────────────────────────────────────

    def apply_checksum(self, payload: bytes) -> bytes:
        """对数据包应用配置中的校验算法。"""
        return compute_checksum(self._checksum_algo_name, payload)

    def verify_checksum(self, payload: bytes, expected: bytes) -> bool:
        """验证数据包的校验和是否与期望值匹配。"""
        actual = self.apply_checksum(payload)
        return actual == expected

    # ── 字段提取 ────────────────────────────────────────────

    def extract_field(self, payload: bytes, field_name: str) -> bytes:
        """从数据包中按偏移映射提取指定字段。

        Args:
            payload: 完整的数据包字节
            field_name: 字段名（如 "room_id", "expire", "auth"）

        Returns:
            字段值（子字节切片）

        Raises:
            ProtocolValidationError: 字段未在 offset_map 中定义
        """
        offset_def = self._offset_map.get(field_name)
        if offset_def is None:
            raise ProtocolValidationError(
                f"字段 '{field_name}' 未在 offset_map 中定义。"
                f"可用字段: {list(self._offset_map.keys())}"
            )

        if isinstance(offset_def, int):
            # 简单的字节偏移
            if offset_def < 0 or offset_def >= len(payload):
                return b""
            return bytes([payload[offset_def]])
        elif isinstance(offset_def, dict):
            # 复杂定义：{offset, length}
            off = offset_def.get("offset", 0)
            length = offset_def.get("length", 1)
            return payload[off:off + length]
        elif isinstance(offset_def, (list, tuple)) and len(offset_def) == 2:
            # (offset, length)
            off, length = int(offset_def[0]), int(offset_def[1])
            return payload[off:off + length]
        else:
            raise ProtocolValidationError(f"不支持的 offset 定义格式: {offset_def}")

    def set_field(self, payload: bytearray, field_name: str, value: bytes) -> bytearray:
        """在数据包中设置指定字段的值。

        Args:
            payload: 要修改的数据包（bytearray）
            field_name: 字段名
            value: 要写入的值

        Returns:
            修改后的 payload
        """
        offset_def = self._offset_map.get(field_name)
        if offset_def is None:
            raise ProtocolValidationError(
                f"字段 '{field_name}' 未在 offset_map 中定义"
            )

        if isinstance(offset_def, int):
            payload[offset_def:offset_def + len(value)] = value
        elif isinstance(offset_def, dict):
            off = offset_def.get("offset", 0)
            payload[off:off + len(value)] = value
        elif isinstance(offset_def, (list, tuple)) and len(offset_def) == 2:
            off = int(offset_def[0])
            payload[off:off + len(value)] = value

        return payload

    # ── 工厂方法 ────────────────────────────────────────────

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "ProtocolProcessor":
        """从品牌配置字典创建 ProtocolProcessor。"""
        checksum_config = profile.get("checksum", {})
        algorithm = checksum_config.get("algorithm", "none")

        # 构建偏移映射
        layout = profile.get("layout", {})
        offset_map: dict[str, Any] = {}
        for field, spec in layout.items():
            if isinstance(spec, dict):
                offset_map[field] = spec
            elif isinstance(spec, int):
                offset_map[field] = spec

        # 补充数据包元信息
        custom_map = {
            "payload_size": profile.get("payload_size", 16),
            "byte_order": profile.get("byte_order", "big"),
            "checksum_algorithm": algorithm,
            "offset_map": offset_map,
        }

        return cls(custom_map)

    @classmethod
    def from_profile_file(cls, filepath: str | Path) -> "ProtocolProcessor":
        """从配置 JSON 文件创建。"""
        path = Path(filepath)
        with open(path, "r", encoding="utf-8") as f:
            profile = json.load(f)
        return cls.from_profile(profile)
