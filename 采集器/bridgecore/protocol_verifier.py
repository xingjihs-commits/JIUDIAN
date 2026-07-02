"""
bridgecore/protocol_verifier.py — 现场协议验证环

核心逻辑：
1. 用刚学到的协议构造一张测试卡 payload
2. 通过桥接层做 direct_write_usb（裸写，不走原厂 DLL）
3. 读回卡数据（direct_read_usb）
4. 比较"预期的 hex" vs "实际写进去的 hex"
5. 完全一致 → 协议正确，可以毕业
6. 不一致 → 记录差异，建议重学

这是采集器最重要的缺失功能——现场能修的东西不要留给 PMS。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .operator_lib import compute_checksum
from .protocol_learner import ProtocolLearnResult

logger = logging.getLogger(__name__)


class VerifyResult:
    """一次验证的结果。"""

    def __init__(self):
        self.passed: bool = False
        self.written_hex: str = ""
        self.readback_hex: str = ""
        self.expected_hex: str = ""
        self.diff_positions: list[int] = []
        self.error: str = ""

    def __repr__(self) -> str:
        if self.passed:
            return "<VerifyResult PASSED>"
        return f"<VerifyResult FAILED errors={len(self.diff_positions)}>"

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "written_hex": self.written_hex,
            "readback_hex": self.readback_hex,
            "expected_hex": self.expected_hex,
            "diff_positions": self.diff_positions,
            "error": self.error,
        }


def _build_test_payload(result: ProtocolLearnResult) -> Optional[str]:
    """从学习结果构造一个测试卡 payload hex。

    构造规则：
    - magic 从 result.magic_hex 取（前 4 字节）
    - 锁号用 0x80010101（V9 风格）或全 1
    - 日期用 今天
    - 卡类型字节用 guest 的 type_byte_high
    - 校验和字段先填 0x0000，算好再填

    返回 32 hex 字符串，或 None（信息不足）。
    """
    if not result.payload_size:
        logger.warning("[Verifier] payload_size 未知，无法构造测试卡")
        return None

    size = result.payload_size
    payload = bytearray(size)

    # 1. Magic 头
    if result.magic_hex:
        magic_bytes = bytes.fromhex(result.magic_hex)
        for i in range(min(len(magic_bytes), size)):
            payload[i] = magic_bytes[i]

    # 2. 锁号字段
    lno_off = result.layout.get("lock_no_offset")
    lno_len = result.layout.get("lock_no_length", 2)
    if lno_off is not None:
        if lno_len == 2 and lno_off + 1 < size:
            payload[lno_off] = 0x01
            payload[lno_off + 1] = 0x01
        elif lno_len == 3 and lno_off + 2 < size:
            payload[lno_off] = 0x01
            payload[lno_off + 1] = 0x01
            payload[lno_off + 2] = 0x01

    # 3. 日期字段
    date_off = result.layout.get("date_offset")
    date_len = result.layout.get("date_len", 2)
    if date_off is not None:
        if date_len == 2 and date_off + 1 < size:
            payload[date_off] = 0x26
            payload[date_off + 1] = 0x12
        elif date_len == 1 and date_off < size:
            payload[date_off] = 0x26

    # 4. 卡类型（类型字节默认在 9，取 guest 第一个卡型的 type_byte_high）
    if result.card_types:
        guest_type = result.card_types.get("guest", {})
        type_offset = guest_type.get("type_byte_offset", 9)
        type_high = guest_type.get("type_byte_high", 0)
        if type_offset < size:
            # 保留低半字节
            low_nibble = payload[type_offset] & 0x0F
            payload[type_offset] = (type_high << 4) | low_nibble

    # 5. 计算校验和
    cs_off = result.checksum_offset
    cs_len = result.checksum_length
    cs_algo = result.checksum_algorithm

    if cs_algo and cs_off is not None:
        # 先清校验和字节
        for i in range(cs_off, min(cs_off + cs_len, size)):
            payload[i] = 0x00
        body = payload[:cs_off] + payload[cs_off + cs_len:]
        try:
            cs_val = compute_checksum(cs_algo, bytes(body))
            for i in range(min(len(cs_val), cs_len)):
                if cs_off + i < size:
                    payload[cs_off + i] = cs_val[i]
        except Exception as e:
            logger.warning("[Verifier] 计算校验和失败: %s", e)
            return None

    return payload.hex().upper()


def verify_protocol(
    bridge,
    learn_result: ProtocolLearnResult,
    *,
    d12: int = 1,
) -> VerifyResult:
    """执行现场协议验证（同步，CollectorBridge API 全部同步）。

    Args:
        bridge: CollectorBridge 实例（含 direct_write_usb / direct_read_usb 方法）。
        learn_result: ProtocolLearner 学习结果。
        d12: 发卡器端口号。

    Returns:
        VerifyResult。
    """
    vr = VerifyResult()

    if not learn_result.has_valid_result:
        vr.error = "协议信息不足，无法构造测试卡"
        logger.warning("[Verifier] %s", vr.error)
        return vr

    # 1. 构造测试卡
    test_hex = _build_test_payload(learn_result)
    if not test_hex:
        vr.error = "构造测试 payload 失败"
        return vr

    vr.expected_hex = test_hex
    logger.info("[Verifier] 测试 payload: %s", test_hex)

    # 2. 先检查卡是否存在
    try:
        probe_resp = bridge.direct_read_usb(d12=d12)
        probe_ok = probe_resp.get("ok") and bool(probe_resp.get("hex") or probe_resp.get("data"))
        if not probe_ok:
            vr.error = "读卡器上未检测到卡片，请放卡后重试"
            logger.warning("[Verifier] %s", vr.error)
            return vr
    except Exception as e:
        logger.debug("[Verifier] 写前探卡跳过: %s", e)

    # 3. 裸写卡
    try:
        write_resp = bridge.direct_write_usb(d12=d12, card_hex=test_hex)
        if not write_resp.get("ok"):
            vr.error = f"写卡失败: {write_resp.get('error', '未知错误')}"
            logger.error("[Verifier] %s", vr.error)
            return vr
        vr.written_hex = test_hex
    except Exception as e:
        vr.error = f"写卡异常: {e}"
        logger.error("[Verifier] %s", vr.error, exc_info=True)
        return vr

    # 3. 读回验证
    try:
        read_resp = bridge.direct_read_usb(d12=d12)
        if not read_resp.get("ok"):
            vr.error = f"读卡失败: {read_resp.get('error', '未知错误')}"
            logger.error("[Verifier] %s", vr.error)
            return vr
        vr.readback_hex = (read_resp.get("hex") or read_resp.get("data") or "").strip().upper()
    except Exception as e:
        vr.error = f"读卡异常: {e}"
        logger.error("[Verifier] %s", vr.error, exc_info=True)
        return vr

    if not vr.readback_hex:
        vr.error = "读回数据为空"
        return vr

    # 4. 比较
    if vr.readback_hex == test_hex:
        vr.passed = True
        logger.info("[Verifier] ✓ 协议验证通过！写入=读回=%s", test_hex[:20])
    else:
        # 找差异位置
        try:
            expected_bytes = bytes.fromhex(test_hex)
            readback_bytes = bytes.fromhex(vr.readback_hex)
            for i in range(min(len(expected_bytes), len(readback_bytes))):
                if expected_bytes[i] != readback_bytes[i]:
                    vr.diff_positions.append(i)
        except ValueError:
            pass
        vr.error = (
            f"协议验证失败：{len(vr.diff_positions)} 个字节不一致"
        )
        logger.warning("[Verifier] ✗ %s", vr.error)

    return vr


def safe_verify_protocol(
    bridge,
    learn_result: ProtocolLearnResult,
    *,
    d12: int = 1,
    allow_destructive: bool = False,
    blank_hex: str = "",
) -> VerifyResult:
    """带安全门的协议验证：写测试卡前确认空白或用户授权。"""
    if not allow_destructive:
        try:
            probe = bridge.direct_read_usb(d12=d12)
            current = (probe.get("hex") or probe.get("data") or "").strip().upper()
            if current and blank_hex:
                if current != blank_hex.strip().upper():
                    vr = VerifyResult()
                    vr.error = (
                        "发卡器上的卡不是空白卡。"
                        "请换空白卡或勾选「此卡可作废」后再验证"
                    )
                    return vr
            elif current and not blank_hex:
                vr = VerifyResult()
                vr.error = "写测试卡前请提供空白卡对照样本，或勾选「此卡可作废」"
                return vr
        except Exception as e:
            logger.debug("[Verifier] 空白卡检查跳过: %s", e)

    vr = verify_protocol(bridge, learn_result, d12=d12)
    if not vr.passed and vr.written_hex:
        vr.error = (vr.error or "验证失败") + "；建议执行擦卡恢复空白"
    return vr
