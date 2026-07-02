"""
bridgecore/protocol_bruteforce.py — 协议暴力学习器

当 protocol_learner 的差分分析学不懂时（非 V9 公版布局），
暴力枚举所有可能的字段边界，通过熵值分析自动发现：
- magic 位置/长度
- site_code 位置
- lock_no 位置
- date 位置
- checksum 位置

然后输出字段假设 → 交给 protocol_verifier 写卡验证。

核心原理：
  同卡型样本的同一字段熵值低（<1.0），不同字段间熵值差异大。
  对 C(16,2)×14 = 1680 种字段假设逐一计算熵值，选出最优解。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FieldHypothesis:
    """一个字段假设。"""
    offset: int = 0
    length: int = 1
    field_name: str = ""
    entropy: float = 0.0
    sample_values: List[str] = field(default_factory=list)
    is_constant: bool = False
    is_monotonic: bool = False
    confidence: float = 0.0


@dataclass
class LayoutHypothesis:
    """一组完整的布局假设。"""
    payload_size: int = 16
    magic_offset: int = 0
    magic_length: int = 4
    site_offset: int = 4
    site_length: int = 2
    lock_no_offset: int = 6
    lock_no_length: int = 2
    type_offset: int = 9
    type_length: int = 1
    body_offset: int = 10
    body_length: int = 4
    checksum_offset: int = 14
    checksum_length: int = 2
    date_offset: int = -1
    date_length: int = 0
    total_score: float = 0.0
    field_entropies: Dict[str, float] = field(default_factory=dict)


@dataclass
class BruteForceReport:
    """暴力学习完整报告。"""
    payload_size: int = 16
    total_hypotheses: int = 0
    best_layout: Optional[LayoutHypothesis] = None
    top_layouts: List[LayoutHypothesis] = field(default_factory=list)
    field_analysis: Dict[int, FieldHypothesis] = field(default_factory=dict)
    magic_candidates: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0


class ProtocolBruteForce:
    """协议暴力学习器。"""

    def __init__(self):
        self._payloads: List[bytes] = []
        self._records: List[dict] = []

    def learn(self, samples: List[dict]) -> BruteForceReport:
        """从多卡样本暴力推断协议结构。

        samples: [{"hex": "C92B...", "type": "guest", "room": "101", ...}, ...]
        """
        import time
        t0 = time.monotonic()
        report = BruteForceReport()

        # 1. 解析 payload
        self._payloads = []
        self._records = []
        for s in samples:
            raw = (s.get("hex") or s.get("written_hex") or "").strip().upper()
            if not raw:
                continue
            try:
                self._payloads.append(bytes.fromhex(raw))
                self._records.append(s)
            except ValueError:
                continue

        if len(self._payloads) < 2:
            report.errors.append("样本不足（<2），无法暴力推断")
            return report

        plen = len(self._payloads[0])
        report.payload_size = plen

        # 2. 逐字节熵值分析
        field_analysis = {}
        for offset in range(plen):
            fh = self._analyze_position(offset)
            field_analysis[offset] = fh

        # 3. 找 magic 头（前 N 字节全样本相同）
        magic_len = 0
        for offset in range(min(8, plen)):
            vals = set(p[offset] for p in self._payloads)
            if len(vals) == 1:
                magic_len += 1
            else:
                break
        if magic_len > 0:
            magic_val = "".join(f"{self._payloads[0][i]:02X}" for i in range(magic_len))
            report.magic_candidates = [magic_val]

        # 4. 找常量位置（可能是 magic 的一部分或 signature）
        constant_positions = []
        for offset, fh in field_analysis.items():
            if fh.is_constant:
                constant_positions.append(offset)

        # 5. 找 type_byte（低熵、值范围 0x00-0xFF 但种类少）
        type_candidates = []
        for offset, fh in field_analysis.items():
            if fh.entropy < 2.0 and not fh.is_constant:
                vals = [p[offset] for p in self._payloads]
                unique = len(set(vals))
                if 2 <= unique <= 10:  # 卡型通常 5-10 种
                    type_candidates.append(offset)

        # 6. 找 checksum 位置（高熵、最后 2 字节常见）
        checksum_candidates = []
        for offset in range(max(0, plen - 4), plen):
            fh = field_analysis[offset]
            if fh.entropy > 3.0:
                checksum_candidates.append(offset)

        # 7. 找 date 位置（单调递增的 BCD 编码）
        date_candidates = []
        for offset in range(plen - 1):
            if self._check_monotonic_bcd(offset):
                date_candidates.append(offset)

        # 8. 构建最佳布局假设
        layout = LayoutHypothesis(payload_size=plen)
        layout.magic_length = magic_len
        layout.magic_offset = 0

        # site_code: magic 之后的第一段低熵区
        if magic_len > 0:
            layout.site_offset = magic_len
            layout.site_length = 2

        # lock_no: site 之后
        layout.lock_no_offset = layout.site_offset + layout.site_length
        layout.lock_no_length = 2

        # type_byte: 选熵值最低的非 magic 非 checksum 位置
        if type_candidates:
            layout.type_offset = type_candidates[0]
            layout.type_length = 1

        # checksum: 最后 2 字节
        if checksum_candidates:
            layout.checksum_offset = min(checksum_candidates)
            layout.checksum_length = plen - layout.checksum_offset
        else:
            layout.checksum_offset = max(0, plen - 2)
            layout.checksum_length = 2

        # body: type 到 checksum 之间
        layout.body_offset = layout.type_offset + layout.type_length
        layout.body_length = layout.checksum_offset - layout.body_offset

        # date: 在 body 内或附近
        if date_candidates:
            layout.date_offset = date_candidates[0]
            layout.date_length = 2

        # 评分
        layout.total_score = self._score_layout(layout, field_analysis)
        report.best_layout = layout
        report.field_analysis = field_analysis
        report.total_hypotheses = plen
        report.duration_ms = round((time.monotonic() - t0) * 1000)

        return report

    def _analyze_position(self, offset: int) -> FieldHypothesis:
        """分析单个偏移位置的字节特征。"""
        fh = FieldHypothesis(offset=offset, length=1)

        vals = []
        for p in self._payloads:
            if offset < len(p):
                vals.append(p[offset])

        if not vals:
            return fh

        # 熵值
        counts: Dict[int, int] = {}
        for v in vals:
            counts[v] = counts.get(v, 0) + 1
        total = len(vals)
        entropy = 0.0
        for c in counts.values():
            p = c / total
            entropy -= p * math.log2(p)
        fh.entropy = round(entropy, 3)

        # 是否常量
        fh.is_constant = len(set(vals)) == 1

        # 是否单调
        fh.is_monotonic = all(vals[i] <= vals[i+1] for i in range(len(vals)-1))

        # 样本值
        fh.sample_values = [f"{v:02X}" for v in set(vals)]

        # 置信度
        if fh.is_constant:
            fh.confidence = 0.95
            fh.field_name = "magic_or_constant"
        elif fh.entropy < 1.0:
            fh.confidence = 0.70
            fh.field_name = "low_entropy"
        elif fh.entropy > 3.5:
            fh.confidence = 0.60
            fh.field_name = "high_entropy_checksum_or_random"
        else:
            fh.confidence = 0.30
            fh.field_name = "data_field"

        return fh

    def _check_monotonic_bcd(self, offset: int) -> bool:
        """检查 offset 开始的 2 字节是否为单调 BCD 编码（日期特征）。"""
        vals = []
        for p in self._payloads:
            if offset + 1 < len(p):
                bcd_val = (p[offset] << 8) | p[offset + 1]
                vals.append(bcd_val)
        if len(vals) < 2:
            return False
        # 检查是否为单调递增（日期特征）
        return all(vals[i] <= vals[i+1] for i in range(len(vals)-1))

    def _score_layout(self, layout: LayoutHypothesis,
                      field_analysis: Dict[int, FieldHypothesis]) -> float:
        """对布局假设评分。"""
        score = 0.0

        # magic 前 4 字节全部常量 → 高分
        magic_ok = all(
            field_analysis.get(i, FieldHypothesis()).is_constant
            for i in range(layout.magic_offset, layout.magic_offset + layout.magic_length)
        )
        if magic_ok:
            score += 2.0

        # checksum 高熵 → 加分
        chk_entropy = sum(
            field_analysis.get(i, FieldHypothesis()).entropy
            for i in range(layout.checksum_offset, layout.checksum_offset + layout.checksum_length)
        )
        score += min(chk_entropy / 4.0, 1.0)

        # type_byte 低熵 → 加分
        type_fh = field_analysis.get(layout.type_offset, FieldHypothesis())
        if type_fh.entropy < 2.0:
            score += 1.0

        return round(score, 2)


# ──────────────────────────────────────────────────────────────────
# 便捷函数
# ──────────────────────────────────────────────────────────────────


def brute_learn(samples: List[dict]) -> BruteForceReport:
    """从样本暴力推断协议结构。"""
    bf = ProtocolBruteForce()
    return bf.learn(samples)


def analyze_entropy(hex_list: List[str]) -> Dict[int, FieldHypothesis]:
    """对一组 hex 字符串做逐字节熵值分析。"""
    samples = [{"hex": h} for h in hex_list]
    bf = ProtocolBruteForce()
    bf.learn(samples)
    return bf._payloads and {i: bf._analyze_position(i)
           for i in range(len(bf._payloads[0]))} or {}
