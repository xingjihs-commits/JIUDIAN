"""
bridgecore/protocol_learner.py — 协议学习器（Collector 独立版）

从采集的读卡样本差分分析推断协议结构。
无 PMS / bridgecore 依赖。

改进：
- _learn_checksum 改为多卡交叉验证（必须所有样本通过同一个算法）
- 新增 learn_from_pair() 利用空白/已写 XOR 差分直接定位被改字节
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, Optional
from .operator_lib import OPERATOR_REGISTRY, compute_checksum
from .observer import RecordingSession

logger = logging.getLogger(__name__)


class ProtocolLearnResult:
    checksum_algorithm: str = ""
    checksum_offset: int = 14
    checksum_length: int = 2
    payload_size: int = 16
    layout: dict[str, Any] = None
    card_types: dict[str, Any] = None
    confidence: float = 0.0
    magic_hex: str = ""
    site_mask_hex: str = "0x3FFF"
    emergency_bit_hex: str = "0x4000"
    lock_no_encoding: str = "hex_be"
    date_encoding: str = ""               # 由学习器推断，不预设品牌
    begin_time_encoding: str = ""         # begin_time 编码方式
    salt_default_hex: str = "00"
    pair_diff: dict[int, str] = None       # [byte_pos]: "changed"|"unchanged" 来自配对 XOR
    # 加密卡检测降级（v1）：当 pair_diff 显示 > 80% 字节变化时，
    # 几乎可以确定是加密卡（卡面随机化）或 SLE4442 类安全芯片，
    # 此时毕业自动否决，提示操作员改走 DLL 代理模式。
    encrypted_suspected: bool = False

    def __init__(self):
        self.layout = {}
        self.card_types = {}
        self.pair_diff = {}

    @property
    def has_valid_result(self) -> bool:
        return bool(self.checksum_algorithm) and len(self.card_types) > 0


class ProtocolLearner:
    """从读卡样本学习协议结构。"""

    def learn_from_payloads(self, samples: list[dict]) -> ProtocolLearnResult:
        """
        samples 格式:
            [
                {"hex": "C92B20B7...", "type": "guest", "room": "101",
                 "b_date": "260610", "e_date": "260611"},
                {"hex": "C92B20B7...", "type": "master"},
            ]
        """
        result = ProtocolLearnResult()
        if not samples:
            return result

        payloads: list[bytes] = []
        records: list[dict] = []
        for s in samples:
            raw_hex = (s.get("hex") or s.get("written_hex") or "").strip().upper()
            if not raw_hex:
                continue
            try:
                pb = bytes.fromhex(raw_hex)
                payloads.append(pb)
            except ValueError:
                continue
            records.append({
                "_type": "call_complete",
                "fn_name": f"{s.get('type', 'unknown')}_card",
                "payload_hex": raw_hex,
                "args_in": {
                    "room": s.get("room", ""),
                    "b_date": s.get("b_date", ""),
                    "e_date": s.get("e_date", ""),
                },
                "ret": {"ok": True, "ret": 0},
            })

        if not payloads:
            logger.warning("[ProtocolLearner] 没有有效的 payload hex")
            return result

        result.payload_size = len(payloads[0])

        self._learn_checksum(payloads, result)
        self._learn_layout(payloads, records, result)
        self._learn_card_types(records, result)
        self._learn_constants(payloads, result)
        self._refine_layout_from_metadata(samples, payloads, result)

        scores = []
        if result.checksum_algorithm:
            # 多卡交叉验证通过的校验和权重更高
            scores.append(0.45)
        if result.layout.get("lock_no_offset") is not None:
            scores.append(0.25)
        if len(result.card_types) > 0:
            scores.append(0.3)
        # 配对差分带来额外置信度
        if result.pair_diff:
            scores.append(0.15)
        result.confidence = min(sum(scores), 1.0)

        # 加密卡强制 confidence 上限 0.30（毕业否决信号）
        if result.encrypted_suspected:
            result.confidence = min(result.confidence, 0.30)

        return result

    # ── 从 RecordingSession 录制学习（PMS 兼容接口） ──────────

    def learn(self, session: RecordingSession) -> ProtocolLearnResult:
        """从一次录制会话学习协议。

        兼容 PMS bridgecore 接口：接受 RecordingSession 对象，
        提取写卡记录的 payload_hex 后交给 learn_from_payloads 处理。

        Args:
            session: RecordingSession 录制会话

        Returns:
            ProtocolLearnResult
        """
        if session is None:
            logger.warning("[ProtocolLearner] 无录制会话")
            return ProtocolLearnResult()

        records = session.records
        if not records:
            logger.warning("[ProtocolLearner] 无录制记录")
            return ProtocolLearnResult()

        # 提取写卡操作
        write_records = [r for r in records
                         if r.get("_type") == "call_complete"
                         and r.get("fn_name", "") in _WRITE_METHODS
                         and r.get("payload_hex")]

        session_tag = getattr(session, "session_tag", "") or ""
        samples: list[dict[str, Any]] = []
        if write_records:
            for wr in write_records:
                sample: dict[str, Any] = {
                    "hex": wr.get("payload_hex", ""),
                    "type": wr.get("fn_name", "unknown").replace("_card", ""),
                }
                args_in = wr.get("args_in", {}) or {}
                if isinstance(args_in, dict):
                    sample["room"] = args_in.get("lock_no", args_in.get("room", ""))
                    sample["b_date"] = args_in.get("b_date", args_in.get("begin_date", ""))
                    sample["e_date"] = args_in.get("e_date", args_in.get("end_date", ""))
                samples.append(sample)

        read_samples = _samples_from_read_records(records, session_tag)
        if read_samples:
            samples.extend(read_samples)

        if not samples:
            logger.warning("[ProtocolLearner] 录制中没有可用的读/写卡 payload")
            return ProtocolLearnResult()

        return self.learn_from_payloads(samples)

    def learn_from_file(self, filepath: str | Path) -> ProtocolLearnResult:
        """从 JSONL 录制文件学习。

        Args:
            filepath: JSONL 录制文件路径

        Returns:
            ProtocolLearnResult
        """
        session = RecordingSession.load(filepath)
        return self.learn(session)

    def _refine_layout_from_metadata(self, samples: list[dict],
                                      payloads: list[bytes],
                                      result: ProtocolLearnResult) -> None:
        if len(samples) < 2:
            return
        guest_samples = [(s, p) for s, p in zip(samples, payloads)
                         if s.get("type") == "guest"]
        for i in range(len(guest_samples)):
            for j in range(i + 1, len(guest_samples)):
                s1, p1 = guest_samples[i]
                s2, p2 = guest_samples[j]
                if (s1.get("room") != s2.get("room") and
                        s1.get("b_date") == s2.get("b_date") and
                        s1.get("e_date") == s2.get("e_date")):
                    for pos in range(min(len(p1), len(p2))):
                        if p1[pos] != p2[pos]:
                            result.layout["lock_no_offset"] = pos
                            result.layout["lock_no_length"] = 2 if pos + 1 < len(p1) and p1[pos + 1] != p2[pos + 1] else 1
                            break
                    break

        for i in range(len(guest_samples)):
            for j in range(i + 1, len(guest_samples)):
                s1, p1 = guest_samples[i]
                s2, p2 = guest_samples[j]
                if (s1.get("room") == s2.get("room") and
                        s1.get("e_date") != s2.get("e_date")):
                    for pos in range(min(len(p1), len(p2))):
                        if p1[pos] != p2[pos]:
                            result.layout["date_offset"] = pos
                            result.layout["date_len"] = 2 if pos + 1 < len(p1) and p1[pos + 1] != p2[pos + 1] else 1
                            break
                    break

    def learn_from_pair(self, blank_hex: str, written_hex: str) -> ProtocolLearnResult:
        """从空白卡 / 已写卡配对直接 XOR 差分分析。

        这是最可靠的推断方式——空白卡和已写卡的差异直接告诉你
        原厂软件改了哪些字节，没改哪些字节就是固定结构（magic/salt/校验和字段）。

        Args:
            blank_hex: 空白卡 32 hex 字符。
            written_hex: 原厂写卡后读回的 32 hex 字符。

        Returns:
            填充好 diff 字段的 ProtocolLearnResult。
        """
        result = ProtocolLearnResult()
        try:
            blank = bytes.fromhex(blank_hex.strip().upper())
            written = bytes.fromhex(written_hex.strip().upper())
        except ValueError:
            logger.warning("[ProtocolLearner] 配对样本 hex 格式无效")
            return result

        if len(blank) != len(written):
            logger.warning("[ProtocolLearner] 配对样本长度不一致")
            return result

        result.payload_size = len(blank)

        # XOR 差分：1 = 变了，0 = 没变
        changed: set[int] = set()
        unchanged: set[int] = set()
        for i in range(len(blank)):
            if blank[i] != written[i]:
                changed.add(i)
            else:
                unchanged.add(i)
            result.pair_diff[i] = "changed" if blank[i] != written[i] else "unchanged"

        # 加密卡检测降级（v1）：
        # - 空白卡和已写卡之间 > 80% 字节都发生变化 → 几乎确定是加密卡
        #   （正常明文卡通常只有 4-6 字节变化：site/lock_no/date/checksum）
        # - 置 encrypted_suspected=True、confidence 上限 0.30
        # - 让毕业教练自动否决，提示操作员走 DLL 代理模式
        if result.payload_size > 0:
            change_ratio = len(changed) / result.payload_size
            if change_ratio > 0.50:
                result.encrypted_suspected = True
                logger.warning(
                    "[ProtocolLearner] 疑似加密卡: %d/%d 字节变化 (%.0f%%) "
                    "— 建议改走 DLL 代理模式，纯协议学习不可靠",
                    len(changed), result.payload_size, change_ratio * 100,
                )

        # 从差分推断 layout
        # 前 4 字节几乎一定是 magic（空白=全是0xFF或随机，写完不变）
        magic_end = 4
        for i in range(4):
            if i not in changed:
                result.layout[f"byte{i}_constant"] = f"{written[i]:02X}"

        # 找到第一个连续 2+ 字节变化区 = 可能是 lock_no 或 site
        changed_list = sorted(changed)
        if len(changed_list) >= 2:
            # 找第一个连续段
            run_start = changed_list[0]
            run_end = run_start
            for i in range(1, len(changed_list)):
                if changed_list[i] == changed_list[i-1] + 1:
                    run_end = changed_list[i]
                else:
                    break
            if run_end - run_start >= 1:  # 至少 2 字节连续变化
                result.layout["lock_no_offset"] = run_start
                result.layout["lock_no_length"] = run_end - run_start + 1

        # site 通常在 lock_no 之前（偏移 4-5），key 是它在不同卡间恒定
        if 4 in unchanged and 5 in unchanged:
            result.layout["site_offset"] = 4
            result.layout["site_length"] = 2

        # 类型 byte = 通常 byte[9]，高半字节变低半字节不变（同类型卡）
        # 但配对中没有多张卡数据，这个由 learn_from_payloads 负责

        # 校验和字段推测：最后 2 字节如果变了，大概率是校验和
        last_two_changed = (len(blank) - 2) in changed and (len(blank) - 1) in changed
        if last_two_changed:
            result.checksum_offset = len(blank) - 2
            result.checksum_length = 2

        # 置信度：加密卡强制上限 0.30，否则正常给 0.5
        if result.encrypted_suspected:
            result.confidence = 0.30
        else:
            result.confidence = 0.5  # 配对分析本身可靠，但需多卡验证补全
        logger.info("[ProtocolLearner] 配对差分: %d 字节变化, %d 字节恒定, "
                    "encrypted_suspected=%s",
                    len(changed), len(unchanged), result.encrypted_suspected)
        return result

    def _learn_checksum(self, payloads: list[bytes],
                        result: ProtocolLearnResult) -> None:
        """多卡交叉验证校验和：必须所有样本都通过同一算法才算。"""
        if len(payloads) < 1:
            return
        for offset, length in [(14, 2), (15, 1), (12, 2), (13, 1), (10, 2)]:
            if offset + length > len(payloads[0]):
                continue
            for algo_name in OPERATOR_REGISTRY:
                matches = 0
                for p in payloads:
                    if offset + length > len(p):
                        continue
                    try:
                        expected = p[offset:offset + length]
                        body = p[:offset] + p[offset + length:]
                        actual = compute_checksum(algo_name, body)
                        if actual == expected:
                            matches += 1
                    except Exception:
                        continue
                # 必须所有样本都通过
                if matches == len(payloads):
                    result.checksum_algorithm = algo_name
                    result.checksum_offset = offset
                    result.checksum_length = length
                    logger.info("[ProtocolLearner] 校验和(多卡验证): %s (offset=%d, len=%d, 通过=%d张)",
                                 algo_name, offset, length, matches)
                    return
        # 降级：单卡保守签名检测
        if len(payloads[0]) >= 16:
            last_byte = payloads[0][15]
            if last_byte in (0xFB, 0x00, 0xFF):
                result.checksum_algorithm = "signature_byte15"
                result.checksum_offset = 15
                result.checksum_length = 1
                logger.info("[ProtocolLearner] 降级签名: byte[15]=0x%02X", last_byte)
            else:
                logger.warning("[ProtocolLearner] 校验和未通过多卡交叉验证，留空待配对验证")

    def _learn_layout(self, payloads: list[bytes],
                      records: list[dict],
                      result: ProtocolLearnResult) -> None:
        if len(payloads) < 2:
            return
        baseline = payloads[0]
        n = len(baseline)
        changed_positions: set[int] = set()
        fixed_positions: set[int] = set()
        for p in payloads[1:]:
            for i in range(min(n, len(p))):
                if p[i] != baseline[i]:
                    changed_positions.add(i)
                else:
                    fixed_positions.add(i)
        if changed_positions:
            for start in range(0, n - 1):
                if start in changed_positions and (start + 1) in changed_positions:
                    result.layout["lock_no_offset"] = start
                    result.layout["lock_no_length"] = 2
                    break
        if fixed_positions:
            for i in range(min(4, n)):
                if i in fixed_positions:
                    if "site_offset" not in result.layout:
                        result.layout["site_offset"] = i
                        result.layout["site_length"] = 4 - i
        result.layout["payload_size"] = n

    def _learn_card_types(self, records: list[dict],
                          result: ProtocolLearnResult) -> None:
        type_offset = 9
        known_types = {
            "guest_card": "guest",
            "master_card": "master",
            "building_card": "building",
            "floor_card": "floor",
            "emergency_card": "emergency",
        }
        seen_fns: set[str] = set()
        for rec in records:
            fn = rec.get("fn_name", "")
            payload_hex = rec.get("payload_hex", "")
            if not fn or not payload_hex:
                continue
            if fn in seen_fns:
                continue
            seen_fns.add(fn)
            card_type = known_types.get(fn, fn)
            payload_bytes = bytes.fromhex(payload_hex)
            type_byte = payload_bytes[type_offset] if type_offset < len(payload_bytes) else 0
            type_high = (type_byte >> 4) & 0x0F
            result.card_types[card_type] = {
                "fn_name": fn,
                "type_byte_high": type_high,
                "type_byte_offset": type_offset,
            }
        logger.info("[ProtocolLearner] 识别到 %d 种卡型: %s",
                     len(result.card_types), list(result.card_types.keys()))

    def _learn_constants(self, payloads: list[bytes],
                         result: ProtocolLearnResult) -> None:
        if not payloads:
            return
        baseline = payloads[0]
        magic_end = 4
        for p in payloads[1:]:
            for i in range(min(4, len(p), len(baseline))):
                if p[i] != baseline[i]:
                    magic_end = min(magic_end, i)
                    break
        if magic_end > 0:
            result.magic_hex = baseline[:magic_end].hex().upper()

        salt_offset = result.layout.get("salt_offset", 8)
        if salt_offset < len(baseline):
            salt_val = baseline[salt_offset]
            result.salt_default_hex = f"{salt_val:02X}"

        lno = result.layout.get("lock_no_offset", 6)
        if lno + 2 <= len(baseline):
            result.lock_no_encoding = "hex_be"


def merge_learn_results(base: ProtocolLearnResult, extra: ProtocolLearnResult) -> None:
    """把 JSONL/录制学习结果合并进主分析结果（原地修改 base）。"""
    if extra is None:
        return
    for key, val in (extra.card_types or {}).items():
        if key not in base.card_types:
            base.card_types[key] = val
    if not base.checksum_algorithm and extra.checksum_algorithm:
        base.checksum_algorithm = extra.checksum_algorithm
        base.checksum_offset = extra.checksum_offset
        base.checksum_length = extra.checksum_length
    for key, val in (extra.layout or {}).items():
        if val is not None and base.layout.get(key) is None:
            base.layout[key] = val
    if extra.magic_hex and not base.magic_hex:
        base.magic_hex = extra.magic_hex
    if extra.site_mask_hex and base.site_mask_hex == "0x3FFF":
        base.site_mask_hex = extra.site_mask_hex
    if extra.emergency_bit_hex and base.emergency_bit_hex == "0x4000":
        base.emergency_bit_hex = extra.emergency_bit_hex
    if extra.pair_diff:
        base.pair_diff.update(extra.pair_diff)
    # 加密卡标志透传：只要任一来源标记，base 也标记
    if getattr(extra, "encrypted_suspected", False):
        base.encrypted_suspected = True
    if extra.confidence > 0:
        base.confidence = min(max(base.confidence, extra.confidence) + 0.05, 1.0)
    # 合并后若标记为加密卡，强制 confidence 上限 0.30
    if base.encrypted_suspected:
        base.confidence = min(base.confidence, 0.30)


def merge_recordings_into_result(
    learner: ProtocolLearner,
    base: ProtocolLearnResult,
    recording_dir: str | Path,
) -> int:
    """合并录制目录下全部 JSONL 到 base，返回合并会话数。"""
    from .observer import list_sessions

    path = Path(recording_dir)
    if not path.is_dir():
        return 0

    merged = 0
    for meta in list_sessions(path):
        if int(meta.get("record_count") or 0) <= 0:
            continue
        try:
            extra = learner.learn_from_file(meta["path"])
        except Exception as exc:
            logger.debug("[ProtocolLearner] 跳过录制 %s: %s", meta.get("filename"), exc)
            continue
        if not (extra.card_types or extra.checksum_algorithm or extra.pair_diff):
            continue
        merge_learn_results(base, extra)
        merged += 1
    return merged


_TAG_DEFAULT_CARD_TYPE = {
    "read_written": "guest",
    "readback_grad": "guest",
    "read_erase_verify": "guest",
}


def _samples_from_read_records(
    records: list[dict],
    session_tag: str = "",
) -> list[dict[str, Any]]:
    """从读卡 RPC 录制提取 hex 样本（read_card / generic_read 等）。"""
    default_type = _TAG_DEFAULT_CARD_TYPE.get(session_tag, "")
    samples: list[dict[str, Any]] = []
    for rec in records:
        if rec.get("_type") != "call_complete":
            continue
        fn = rec.get("fn_name", "")
        if fn not in _READ_METHODS:
            continue
        hex_str = (rec.get("payload_hex") or "").strip().upper()
        if not hex_str:
            ret = rec.get("ret") or {}
            out = ret.get("out") if isinstance(ret.get("out"), dict) else {}
            hex_str = (
                out.get("payload") or out.get("card_hex") or out.get("hex") or ""
            ).strip().upper()
        if not hex_str:
            continue
        if hex_str == "0" * len(hex_str) and session_tag != "read_blank":
            continue
        if fn in _READ_METHODS:
            card_type = default_type or "guest"
        elif fn.endswith("_card"):
            card_type = fn.replace("_card", "")
        elif session_tag == "read_blank":
            card_type = "blank"
        else:
            card_type = "guest"
        samples.append({"hex": hex_str, "type": card_type})
    return samples


def boost_from_dll_traces(result: ProtocolLearnResult, traces: list[dict]) -> None:
    """从 DLL/proxy 调用记录提升置信度。"""
    if not traces:
        return
    bonus = 0.0
    fns = {t.get("fn_name", "") for t in traces if isinstance(t, dict)}
    if "guest_card" in fns:
        bonus += 0.2
    if fns & _WRITE_METHODS:
        bonus += 0.1
    if result.pair_diff:
        bonus += 0.15
    type_count = len({s.get("type") for s in traces if s.get("type")})
    bonus += min(type_count * 0.05, 0.2)
    result.confidence = min(result.confidence + bonus, 1.0)


# 写卡方法名列表（learn() 方法筛选写卡记录用）
_WRITE_METHODS = frozenset({
    "guest_card", "guest_card_v2", "compose_guest_card",
    "master_card", "building_card", "floor_card",
    "emergency_card", "group_card", "auth_card", "ini_card",
    "limit_card", "card_erase", "write_card",
})

_READ_METHODS = frozenset({
    "read_card", "generic_read", "direct_read_usb", "read",
})
