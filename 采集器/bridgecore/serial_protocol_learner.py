"""
串口协议学习器 — 升级版本
新增：空白卡/已写卡差分对比、命令结构推断
"""

from typing import Dict, Any, List, Tuple, Optional
from collections import Counter


def learn_from_pair(blank_rx: bytes, written_rx: bytes) -> Dict[str, Any]:
    """
    对比空白卡和已写卡的串口应答，XOR 找变化字节，推断数据场布局。
    """
    min_len = min(len(blank_rx), len(written_rx))
    diff_bytes = []
    diff_positions = []

    for i in range(min_len):
        if blank_rx[i] != written_rx[i]:
            diff_bytes.append((i, blank_rx[i], written_rx[i]))
            diff_positions.append(i)

    data_ranges = []
    if diff_positions:
        start = diff_positions[0]
        prev = diff_positions[0]
        for pos in diff_positions[1:]:
            if pos == prev + 1:
                prev = pos
            else:
                data_ranges.append((start, prev + 1))
                start = pos
                prev = pos
        data_ranges.append((start, prev + 1))

    if diff_positions:
        static_prefix = blank_rx[:diff_positions[0]]
        static_suffix = blank_rx[diff_positions[-1] + 1:]
    else:
        static_prefix = blank_rx
        static_suffix = b''

    ratio = len(diff_positions) / max(len(blank_rx), len(written_rx)) if max(len(blank_rx), len(written_rx)) > 0 else 0
    confidence = 1.0 - abs(ratio - 0.45) * 2
    confidence = max(0.0, min(1.0, confidence))

    return {
        "diff_bytes": diff_bytes,
        "diff_positions": diff_positions,
        "data_field_ranges": data_ranges,
        "static_prefix": static_prefix,
        "static_suffix": static_suffix,
        "confidence": round(confidence, 2),
    }


def learn_command_structure(tx_frames: List[bytes], rx_frames: List[bytes]) -> Dict[str, Any]:
    """
    多组 TX->RX 对照，推断命令字节、应答模式。
    """
    if not tx_frames or not rx_frames:
        return {}

    min_tx = min(len(f) for f in tx_frames)
    prefix_len = 0
    for i in range(min_tx):
        if all(f[i] == tx_frames[0][i] for f in tx_frames):
            prefix_len = i + 1
        else:
            break

    suffix_len = 0
    for i in range(1, min_tx + 1):
        if all(f[-i] == tx_frames[0][-i] for f in tx_frames):
            suffix_len = i
        else:
            break

    template = bytearray(tx_frames[0])
    for i in range(prefix_len, len(template) - suffix_len):
        values = set(f[i] for f in tx_frames if i < len(f))
        if len(values) > 1:
            template[i] = 0x3F

    checksum_byte = None
    if len(tx_frames) > 2:
        last_pos = min(len(f) for f in tx_frames) - 1
        checksum_byte = last_pos

    data_offset = prefix_len
    rx_lengths = Counter(len(f) for f in rx_frames)
    most_common_len = rx_lengths.most_common(1)[0][0] if rx_lengths else 0

    return {
        "command_template": bytes(template),
        "response_length": most_common_len,
        "checksum_byte": checksum_byte,
        "data_offset": data_offset,
        "tx_patterns": {
            "prefix_len": prefix_len,
            "suffix_len": suffix_len,
            "variable_positions": [i for i in range(prefix_len, len(template) - suffix_len)],
        },
        "rx_patterns": {
            "common_lengths": dict(rx_lengths.most_common(3)),
        },
    }


def learn_protocol(blank_samples: List[bytes], written_samples: List[bytes]) -> Dict[str, Any]:
    """兼容旧接口：综合学习协议"""
    if not blank_samples or not written_samples:
        return {"error": "insufficient samples"}

    pair_result = learn_from_pair(blank_samples[0], written_samples[0])
    cmd_result = learn_command_structure(
        [b'\x00' * 16] * len(blank_samples),
        blank_samples + written_samples,
    )

    return {
        "pair_analysis": pair_result,
        "command_structure": cmd_result,
        "command_template": cmd_result.get("command_template", b''),
        "response_length": cmd_result.get("response_length", 0),
        "checksum_byte": cmd_result.get("checksum_byte"),
        "data_offset": cmd_result.get("data_offset", 0),
    }
