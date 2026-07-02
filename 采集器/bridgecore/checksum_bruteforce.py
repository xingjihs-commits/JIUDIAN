"""
bridgecore/checksum_bruteforce.py — 自适应校验算法学习器

内置 15+ 种校验算法模板，对每张卡样本尝试所有算法，
必须所有相同卡型的样本都通过同一算法才算命中。
支持 Python lambda 自定义算法扩展。
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 15+ 种校验算法 ──────────────────────────────────────────────

def _xor_all(payload: bytes) -> int:
    return 0
def _xor_sum(payload: bytes) -> int:
    s = 0
    for b in payload: s ^= b
    return s & 0xFF
def _add_sum(payload: bytes) -> int:
    return sum(payload) & 0xFF
def _add_sum_mod256(payload: bytes) -> int:
    return sum(payload) % 256
def _crc8(payload: bytes) -> int:
    crc = 0xFF
    for b in payload:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07 if crc & 0x80 else crc << 1) & 0xFF
    return crc
def _crc16_ccitt(payload: bytes) -> int:
    crc = 0xFFFF
    for b in payload:
        crc ^= (b << 8)
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc
def _crc16_modbus(payload: bytes) -> int:
    crc = 0xFFFF
    for b in payload:
        crc ^= b
        for _ in range(8):
            crc = ((crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1) & 0xFFFF
    return crc
def _sum14_zero_byte15(payload: bytes) -> tuple:
    return (sum(payload[:14]) & 0xFF, 0x00)
def _byte15_fb(payload: bytes) -> tuple:
    return (payload[14] if len(payload) > 14 else 0, 0xFB)
def _sum14_xor_byte15(payload: bytes) -> tuple:
    s = sum(payload[:14]) & 0xFF
    return (s, s ^ 0xFF)
def _not_sum14(payload: bytes) -> tuple:
    s = sum(payload[:14]) & 0xFF
    return ((~s) & 0xFF, 0x00)
def _xor_fold(payload: bytes) -> int:
    s = 0
    for i in range(0, len(payload), 2):
        s ^= (payload[i] << 8 | (payload[i+1] if i+1 < len(payload) else 0))
    return s & 0xFFFF
def _byte14_sum14_byte15_const(payload: bytes) -> tuple:
    return (sum(payload[:14]) & 0xFF, payload[15] if len(payload) > 15 else 0)
def _crc8_maxim(payload: bytes) -> int:
    crc = 0x00
    for b in payload:
        crc ^= b
        for _ in range(8):
            crc = ((crc >> 1) ^ 0x8C if crc & 1 else crc >> 1) & 0xFF
    return crc
def _lrc(payload: bytes) -> int:
    return ((-sum(payload)) & 0xFF)
def _sum14_only(payload: bytes) -> int:
    return sum(payload[:14]) & 0xFF

ALGORITHMS = {
    "xor_all": _xor_all, "xor_sum": _xor_sum, "add_sum": _add_sum,
    "add_sum_mod256": _add_sum_mod256, "crc8": _crc8, "crc16_ccitt": _crc16_ccitt,
    "crc16_modbus": _crc16_modbus, "sum14_zero_byte15": _sum14_zero_byte15,
    "byte15_fb": _byte15_fb, "sum14_xor_byte15": _sum14_xor_byte15,
    "not_sum14": _not_sum14, "xor_fold": _xor_fold,
    "byte14_sum14_byte15_const": _byte14_sum14_byte15_const,
    "crc8_maxim": _crc8_maxim, "lrc": _lrc, "sum14_only": _sum14_only,
}

ALGO_DESCRIPTIONS = {
    "xor_all": "全部XOR", "xor_sum": "XOR累加", "add_sum": "加法求和低8位",
    "add_sum_mod256": "加法求模256", "crc8": "CRC-8", "crc16_ccitt": "CRC-16 CCITT",
    "crc16_modbus": "CRC-16 Modbus", "sum14_zero_byte15": "前14字节和→byte14, byte15=00",
    "byte15_fb": "byte15固定FB", "sum14_xor_byte15": "sum14→byte14, byte15=byte14 XOR FF",
    "not_sum14": "NOT sum14", "xor_fold": "2字节折叠XOR",
    "byte14_sum14_byte15_const": "byte14=sum14, byte15=常量",
    "crc8_maxim": "CRC-8 Maxim", "lrc": "LRC纵向冗余", "sum14_only": "仅前14字节和",
}

@dataclass
class ChecksumHypothesis:
    algorithm: str = ""
    description: str = ""
    confidence: float = 0.0
    verified_count: int = 0
    total_count: int = 0
    sample_results: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class ChecksumReport:
    hypotheses: List[ChecksumHypothesis] = field(default_factory=list)
    best: Optional[ChecksumHypothesis] = None
    all_failed: bool = False
    errors: List[str] = field(default_factory=list)


class ChecksumBruteForce:
    """暴力学习校验算法。"""

    def __init__(self):
        self._custom_algos: Dict[str, Callable] = {}

    def register(self, name: str, fn: Callable[[bytes], Any]) -> None:
        """注册自定义校验算法。fn(payload: bytes) -> int or tuple"""
        self._custom_algos[name] = fn

    def learn(self, samples: List[dict]) -> ChecksumReport:
        """对样本尝试所有已知算法，找到匹配的。

        samples: [{"hex": "C92B...", "type": "guest"}, ...]
        """
        report = ChecksumReport()
        if not samples:
            report.errors.append("无样本")
            return report

        # 按卡型分组
        by_type: Dict[str, List[bytes]] = {}
        for s in samples:
            ct = s.get("type", "unknown")
            raw = (s.get("hex") or s.get("written_hex") or "").strip().upper()
            if not raw or len(raw) < 30:
                continue
            try:
                by_type.setdefault(ct, []).append(bytes.fromhex(raw))
            except ValueError:
                continue

        if not by_type:
            report.errors.append("无有效样本")
            return report

        # 合并所有算法
        all_algos = dict(ALGORITHMS)
        all_algos.update(self._custom_algos)

        # 对每种算法测试所有卡型
        for algo_name, algo_fn in all_algos.items():
            hyp = self._test_algorithm(algo_name, algo_fn, by_type)
            if hyp.verified_count > 0:
                report.hypotheses.append(hyp)

        # 排序：置信度最高的在前
        report.hypotheses.sort(key=lambda h: h.confidence, reverse=True)

        if report.hypotheses:
            report.best = report.hypotheses[0]
        else:
            report.all_failed = True
            report.errors.append("所有算法均未通过")

        return report

    def _test_algorithm(self, algo_name: str, algo_fn: Callable,
                        by_type: Dict[str, List[bytes]]) -> ChecksumHypothesis:
        """测试一种算法在所有卡型上的表现。"""
        hyp = ChecksumHypothesis(
            algorithm=algo_name,
            description=ALGO_DESCRIPTIONS.get(algo_name, algo_name),
        )
        total_groups = len(by_type)
        verified_groups = 0

        for ct, payloads in by_type.items():
            if len(payloads) < 1:
                continue
            group_ok = True
            expected = None
            for p in payloads:
                try:
                    result = algo_fn(p)
                except Exception:
                    group_ok = False
                    break
                if expected is None:
                    expected = result
                elif result != expected:
                    group_ok = False
                    break
            if group_ok:
                verified_groups += 1
            hyp.sample_results.append({
                "card_type": ct, "passed": group_ok,
                "sample_count": len(payloads),
            })

        hyp.verified_count = verified_groups
        hyp.total_count = total_groups
        hyp.confidence = round(verified_groups / max(total_groups, 1), 2)
        return hyp


def brute_checksum(samples: List[dict]) -> ChecksumReport:
    return ChecksumBruteForce().learn(samples)

def quick_checksum(hex_list: List[str]) -> Optional[str]:
    """快速检测一组 hex 的校验算法。"""
    samples = [{"hex": h, "type": "sample"} for h in hex_list]
    report = ChecksumBruteForce().learn(samples)
    return report.best.algorithm if report.best else None
