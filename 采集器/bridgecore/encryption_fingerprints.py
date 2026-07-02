"""
bridgecore/encryption_fingerprints.py — 加密指纹识别

识别门锁系统使用的加密体系，决定采集策略：
- 无加密/简单校验 → 差分学习即可
- SLE4442 安全芯片 → 需 DLL 代理，不能裸写
- AES/DES 加密 → 差分失效，需寄生回放
- MIFARE Classic 密钥保护 → 需弱密钥爆破

参考：原厂门锁系统/knowledge/综合成果.md §二 六层加密体系
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EncryptionFingerprint:
    encrypted: bool = False
    encryption_type: str = ""
    confidence: float = 0.0
    evidence: List[str] = field(default_factory=list)
    recommended_strategy: str = ""
    risk_warnings: List[str] = field(default_factory=list)
    card_type_hint: str = ""


@dataclass
class EncryptionReport:
    fingerprints: List[EncryptionFingerprint] = field(default_factory=list)
    overall_encrypted: bool = False
    overall_strategy: str = "diff_learn"
    overall_confidence: float = 0.0
    summary: str = ""


_SLE4442_DLL_PATTERNS = ["Mwic_32.dll", "sle4442.dll", "at88sc.dll", "sec_chip.dll"]


def detect_sle4442(dll_init_result=None, install_dir="", dll_list=None):
    fp = EncryptionFingerprint()
    evidence = []
    if dll_init_result:
        ret = dll_init_result.get("ret", 0)
        if ret == 259:
            evidence.append("initializeUSB(d12=0) ret=259 — SLE4442写保护")
        elif ret == 3:
            evidence.append("initializeUSB ret=3 — 设备异常(可能SLE4442)")
    if dll_list:
        for dll in dll_list:
            for pat in _SLE4442_DLL_PATTERNS:
                if pat.lower() in dll.lower():
                    evidence.append(f"SLE4442相关DLL: {dll}")
    if install_dir:
        try:
            for entry in Path(install_dir).iterdir():
                if entry.name.lower() in ('mwic_32.dll', 'd12.dll', 'd12c.dll'):
                    evidence.append(f"USB层DLL: {entry.name}")
        except Exception:
            pass
    if evidence:
        fp.encrypted = True
        fp.encryption_type = "sle4442"
        fp.confidence = min(0.5 + 0.15 * len(evidence), 0.95)
        fp.evidence = evidence
        fp.recommended_strategy = "dll_proxy"
        fp.card_type_hint = "sle4442"
        fp.risk_warnings.append("SLE4442:d12=0会触发写保护(EC倒计数)不可逆,必须d12=1+DLL代理")
    else:
        fp.encryption_type = "none"
        fp.recommended_strategy = "diff_learn"
    return fp


def detect_encryption_from_pair_diff(blank_hex, written_hex, threshold=0.80):
    fp = EncryptionFingerprint()
    try:
        b = bytes.fromhex(blank_hex.strip().upper())
        w = bytes.fromhex(written_hex.strip().upper())
    except ValueError:
        fp.evidence.append("hex解析失败")
        return fp
    if len(b) != len(w):
        fp.evidence.append(f"长度不一致:{len(b)} vs {len(w)}")
        return fp
    total = len(b)
    changed = sum(1 for i in range(total) if b[i] != w[i])
    ratio = changed / max(total, 1)
    if ratio > threshold:
        fp.encrypted = True
        fp.encryption_type = "likely_aes_or_randomized"
        fp.confidence = min(ratio, 0.95)
        fp.evidence.append(f"XOR差分:{changed}/{total}({ratio:.0%})变化,超过阈值{threshold:.0%}")
        fp.recommended_strategy = "parasitic_replay"
        fp.risk_warnings.append("卡面高度随机化,差分学习不可行,建议寄生回放")
    elif ratio < 0.15:
        fp.encryption_type = "none"
        fp.confidence = 0.85
        fp.evidence.append(f"XOR差分:仅{changed}/{total}({ratio:.0%})变化,疑似明文")
        fp.recommended_strategy = "diff_learn"
    else:
        fp.encryption_type = "partial_structure"
        fp.confidence = 0.50
        fp.evidence.append(f"XOR差分:{changed}/{total}({ratio:.0%})变化,部分结构")
        fp.recommended_strategy = "diff_learn"
    return fp


def detect_encryption_from_samples(samples, entropy_threshold=3.5):
    fp = EncryptionFingerprint()
    if len(samples) < 3:
        return fp
    by_type = {}
    for s in samples:
        ct = s.get("type", "unknown")
        raw = (s.get("hex") or s.get("written_hex") or "").strip().upper()
        if not raw or len(raw) < 16:
            continue
        try:
            by_type.setdefault(ct, []).append(bytes.fromhex(raw))
        except ValueError:
            continue
    import math
    for ct, payloads in by_type.items():
        if len(payloads) < 3:
            continue
        min_len = min(len(p) for p in payloads)
        high_entropy_positions = 0
        for offset in range(min_len):
            counts = {}
            for p in payloads:
                b = p[offset]
                counts[b] = counts.get(b, 0) + 1
            total = len(payloads)
            entropy = -sum((c/total)*math.log2(c/total) for c in counts.values())
            if entropy > entropy_threshold:
                high_entropy_positions += 1
        ratio = high_entropy_positions / max(min_len, 1)
        if ratio > 0.6:
            fp.encrypted = True
            fp.encryption_type = "likely_encrypted_by_type"
            fp.confidence = min(ratio, 0.90)
            fp.evidence.append(f"卡型'{ct}':{high_entropy_positions}/{min_len}({ratio:.0%})高熵,疑似加密")
            fp.recommended_strategy = "parasitic_replay"
    if not fp.encrypted:
        fp.encryption_type = "none"
        fp.recommended_strategy = "diff_learn"
    return fp


class EncryptionAnalyzer:
    def __init__(self):
        self._fingerprints = []

    def analyze(self, install_dir="", dll_list=None, dll_init_result=None,
                pair_diff_samples=None, multi_samples=None):
        self._fingerprints = []
        fp1 = detect_sle4442(dll_init_result, install_dir, dll_list)
        self._fingerprints.append(fp1)
        if pair_diff_samples:
            for pair in pair_diff_samples:
                self._fingerprints.append(detect_encryption_from_pair_diff(
                    pair.get("blank_hex", ""), pair.get("written_hex", "")))
        if multi_samples and len(multi_samples) >= 3:
            self._fingerprints.append(detect_encryption_from_samples(multi_samples))
        encrypted_fps = [fp for fp in self._fingerprints if fp.encrypted]
        overall_encrypted = len(encrypted_fps) > 0
        if overall_encrypted:
            sle = [fp for fp in encrypted_fps if fp.encryption_type == "sle4442"]
            strategy = "dll_proxy" if sle else "parasitic_replay"
        else:
            strategy = "diff_learn"
        max_conf = max((fp.confidence for fp in self._fingerprints), default=0.0)
        all_evidence = []
        all_warnings = []
        for fp in self._fingerprints:
            all_evidence.extend(fp.evidence)
            all_warnings.extend(fp.risk_warnings)
        return EncryptionReport(
            fingerprints=self._fingerprints,
            overall_encrypted=overall_encrypted,
            overall_strategy=strategy,
            overall_confidence=round(max_conf, 2),
            summary=f"{'加密' if overall_encrypted else '明文'}, 策略={strategy}, 置信度={max_conf:.0%}",
        )


def quick_check(pair_diff=None, dll_init_ret=0):
    a = EncryptionAnalyzer()
    kw = {}
    if pair_diff:
        kw["pair_diff_samples"] = [pair_diff]
    if dll_init_ret:
        kw["dll_init_result"] = {"ret": dll_init_ret}
    r = a.analyze(**kw)
    return {"encrypted": r.overall_encrypted, "strategy": r.overall_strategy,
            "confidence": r.overall_confidence, "summary": r.summary}
