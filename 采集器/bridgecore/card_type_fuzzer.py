"""
bridgecore/card_type_fuzzer.py — 卡型穷举器

对 type_byte（payload[9]）高 4 bits 穷举 0x0-0xF（共 16 种），
每种尝试写卡 + 读回验证，发现界面不提供的隐藏卡型。

用法：
    fuzzer = CardTypeFuzzer(bridge, profile)
    discovered = fuzzer.fuzz()
    # → {"0x6": "guest", "0xB": "master", "0xE": "hidden_elevator_card", ...}
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 已知卡型映射（高 4 bits → 卡型名）
_KNOWN_TYPE_MAP: Dict[int, str] = {
    0x0: "auth",        # 授权卡
    0x1: "ini",         # 初始化卡
    0x2: "time_set",    # 时钟设置卡
    0x3: "room_set",    # 房号设置卡
    0x4: "group_set",   # 组号设置卡
    0x5: "loss",        # 挂失卡
    0x6: "guest",       # 客人卡
    0x7: "checkout",    # 退房卡
    0x8: "record",      # 记录卡
    0x9: "group",       # 组控卡
    0xA: "emergency",   # 应急卡
    0xB: "master",      # 总卡
    0xC: "building",    # 楼栋卡
    0xD: "floor",       # 楼层卡
    0xE: "unknown_e",   # 保留/隐藏卡型
    0xF: "unknown_f",   # 保留/隐藏卡型
}


@dataclass
class FuzzResult:
    type_high: int = 0
    type_name: str = ""
    write_ok: bool = False
    readback_match: bool = False
    written_hex: str = ""
    readback_hex: str = ""
    error: str = ""
    note: str = ""


@dataclass
class FuzzReport:
    total_tested: int = 0
    successful: int = 0
    discovered: Dict[str, FuzzResult] = field(default_factory=dict)
    hidden_types: List[FuzzResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0


class CardTypeFuzzer:
    """卡型穷举器 — 暴力发现所有可用卡型。"""

    def __init__(self, bridge=None, profile: Optional[Dict[str, Any]] = None):
        self._bridge = bridge
        self._profile = profile or {}
        self._site_code: int = 0
        self._lock_no: int = 0x0101

    def set_bridge(self, bridge):
        self._bridge = bridge

    def configure(self, site_code: int = 0, lock_no: int = 0x0101):
        """设置 site_code 和 lock_no（用于构造测试 payload）。"""
        self._site_code = site_code
        self._lock_no = lock_no

    def fuzz(self, start: int = 0x0, end: int = 0xF) -> FuzzReport:
        """穷举 type_byte 高 4 bits，返回发现的卡型。"""
        import time as _time
        t0 = _time.monotonic()
        report = FuzzReport()

        bridge = self._get_bridge()
        if bridge is None:
            report.errors.append("无 bridge 实例")
            return report

        # 初始化
        try:
            resp = bridge.initialize(d12=1)
            if not resp.get("ok") or int(resp.get("ret", -1)) != 0:
                report.errors.append("发卡器初始化失败")
                return report
        except Exception as e:
            report.errors.append(f"初始化异常: {e}")
            return report

        # 对每种 type_high 尝试写卡
        for th in range(start, end + 1):
            report.total_tested += 1
            type_name = _KNOWN_TYPE_MAP.get(th, f"unknown_{th:X}")

            try:
                result = self._fuzz_one(bridge, th, type_name)
                if result.write_ok:
                    report.successful += 1
                    report.discovered[f"0x{th:X}"] = result
                    if th >= 0xE or type_name.startswith("unknown"):
                        report.hidden_types.append(result)
                        logger.info("[Fuzzer] 发现隐藏卡型: 0x%X (%s)", th, type_name)
            except Exception as e:
                report.errors.append(f"fuzz 0x{th:X}: {e}")

        # 关闭
        try:
            bridge.close_usb()
        except Exception:
            pass

        report.duration_ms = round((_time.monotonic() - t0) * 1000)
        return report

    def _fuzz_one(self, bridge, type_high: int, type_name: str) -> FuzzResult:
        """穷举一种卡型。"""
        result = FuzzResult(type_high=type_high, type_name=type_name)

        # 构造 payload
        payload = self._build_fuzz_payload(type_high)
        result.written_hex = payload

        # 写卡
        try:
            resp = bridge.direct_write_usb(d12=1, card_hex=payload, timeout=6.0)
            ret = int(resp.get("ret", -1))
            if ret != 0:
                result.error = f"写卡返回 {ret}"
                return result
        except Exception as e:
            result.error = f"写卡异常: {e}"
            return result

        result.write_ok = True

        # 读回验证
        time.sleep(0.3)
        try:
            resp = bridge.direct_read_usb(d12=1, timeout=5.0)
            readback = (resp.get("card_hex") or resp.get("payload_hex") or "").strip().upper()
            result.readback_hex = readback
            result.readback_match = readback == payload.upper()
            if not result.readback_match:
                result.note = "读回不匹配，可能被门锁固件修改"
        except Exception as e:
            result.error = f"读回异常: {e}"

        return result

    def _build_fuzz_payload(self, type_high: int) -> str:
        """构造穷举用的 16 字节 payload。"""
        # 默认 V9 公版布局
        magic = self._profile.get("magic", "C92B20B7")
        site = self._site_code & 0x3FFF
        lock = self._lock_no & 0xFFFF
        salt = 0x00
        type_byte = (type_high << 4) | 0x01  # seq=1
        body = 0x00000000

        payload_bytes = bytearray(16)
        # Magic (4 bytes)
        try:
            mb = bytes.fromhex(magic)
            for i, b in enumerate(mb[:4]):
                payload_bytes[i] = b
        except ValueError:
            payload_bytes[0:4] = b'\xC9\x2B\x20\xB7'

        # Site (2 bytes, big-endian)
        payload_bytes[4] = (site >> 8) & 0xFF
        payload_bytes[5] = site & 0xFF

        # LockNo (2 bytes, big-endian)
        payload_bytes[6] = (lock >> 8) & 0xFF
        payload_bytes[7] = lock & 0xFF

        # Salt (1 byte)
        payload_bytes[8] = salt

        # Type (1 byte)
        payload_bytes[9] = type_byte

        # Body (4 bytes)
        body_bytes = body.to_bytes(4, 'big')
        payload_bytes[10:14] = body_bytes

        # Checksum (2 bytes, simple sum14)
        s = sum(payload_bytes[0:14]) & 0xFF
        payload_bytes[14] = s
        payload_bytes[15] = 0x00

        return payload_bytes.hex().upper()

    def _get_bridge(self):
        if self._bridge is not None:
            return self._bridge
        try:
            from ..collector_bridge import get_bridge
            return get_bridge()
        except Exception:
            return None


# ──────────────────────────────────────────────────────────────────
# 便捷函数
# ──────────────────────────────────────────────────────────────────


def fuzz_card_types(bridge=None, profile=None) -> FuzzReport:
    """穷举发现所有卡型。"""
    fuzzer = CardTypeFuzzer(bridge, profile)
    return fuzzer.fuzz()


def discover_hidden_cards(fuzz_report: FuzzReport) -> List[str]:
    """从穷举结果中提取隐藏卡型名。"""
    return [r.type_name for r in fuzz_report.hidden_types]
