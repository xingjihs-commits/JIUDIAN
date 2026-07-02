"""
bridgecore/tests/test_takeover.py — 接管系统测试

测试 PhysicalChannel, DllProber, ProtocolLearner, ProfileGenerator。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

# 确保可以导入 bridgecore
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from bridgecore import (
    ChannelDetector,
    DllChannel, SerialChannel, UsbHidChannel,
    probe_dll, probe_dll_by_path,
    ProtocolLearner, ProtocolLearnResult,
    generate_profile, save_profile,
)
from bridgecore.physical_channel import ChannelInfo, ProbeResult
from bridgecore.observer import RecordingSession
from . import MockBridge


# ──────────────────────────────────────────────────────────────────
# ChannelInfo & PhysicalChannel
# ──────────────────────────────────────────────────────────────────

class TestChannelInfo(unittest.TestCase):
    def test_label_dll(self):
        info = ChannelInfo(
            channel_type="dll",
            device_name="V9",
            dll_path="C:\\test\\V9RFL.dll",
        )
        self.assertIn("V9RFL.dll", info.label)

    def test_label_serial(self):
        info = ChannelInfo(
            channel_type="serial",
            device_name="COM1",
            com_port="COM1",
            baud_rate=9600,
        )
        self.assertIn("COM1", info.label)

    def test_label_usb_hid(self):
        info = ChannelInfo(
            channel_type="usb_hid",
            device_name="HID Reader",
            vid="1234",
            pid="5678",
        )
        self.assertIn("1234", info.label)


class TestProbeResult(unittest.TestCase):
    def setUp(self):
        self.result = ProbeResult(
            dll_name="test.dll",
            classified={"read_card": "ReadCard", "init_usb": "init"},
            hardcoded_match={"guest_card": "guestcard"},
            brand_guess="测试品牌",
            confidence=0.85,
        )

    def test_has_function(self):
        self.assertTrue(self.result.has_function("read_card"))
        self.assertTrue(self.result.has_function("guest_card"))
        self.assertFalse(self.result.has_function("close"))

    def test_get_function(self):
        self.assertEqual(self.result.get_function("read_card"), "ReadCard")
        self.assertEqual(self.result.get_function("guest_card"), "guestcard")


# ──────────────────────────────────────────────────────────────────
# DllChannel (requires bridge)
# ──────────────────────────────────────────────────────────────────

class TestDllChannel(unittest.TestCase):
    def setUp(self):
        self.info = ChannelInfo(
            channel_type="dll",
            device_name="Test DLL",
            dll_path="C:\\test\\TestDll.dll",
            dll_functions={"init_usb": "init", "read_card": "ReadCard"},
        )

    def test_create_channel(self):
        channel = DllChannel(self.info)
        self.assertEqual(channel.info.channel_type, "dll")
        self.assertFalse(channel.opened)

    def test_open_with_bridge(self):
        bridge = MockBridge()
        channel = DllChannel(self.info, bridge=bridge)
        # 用 MockBridge 没有真正的 DLL，open 会返回 False
        result = channel.open()
        self.assertFalse(result)


# ──────────────────────────────────────────────────────────────────
# ProtocolLearner
# ──────────────────────────────────────────────────────────────────

class TestProtocolLearner(unittest.TestCase):
    def setUp(self):
        self.learner = ProtocolLearner()

    def _make_record(self, fn_name: str, payload_hex: str) -> dict:
        return {
            "_type": "call_complete",
            "fn_name": fn_name,
            "payload_hex": payload_hex,
        }

    def test_empty_session(self):
        session = RecordingSession()
        result = self.learner.learn(session)
        self.assertFalse(result.has_valid_result)

    def test_learn_checksum_addsum(self):
        """使用 add_sum_16 校验的 payload 测试。"""
        from bridgecore.operator_lib import compute_checksum
        # 16-byte payload: 14 data bytes + 2 byte checksum at offset 14
        data = bytes(range(14))
        chk = compute_checksum("add_sum_16", data)  # 2 bytes, little-endian
        payload = data + chk  # 16 bytes total
        self.assertEqual(len(payload), 16)

        session = RecordingSession()
        session.add_record(self._make_record("guest_card", payload.hex()))

        result = self.learner.learn(session)
        self.assertEqual(result.checksum_algorithm, "add_sum_16")
        self.assertEqual(result.checksum_offset, 14)
        self.assertEqual(result.checksum_length, 2)

    def test_learn_checksum_crc8(self):
        """使用 CRC8_MAXIM 校验的 payload 测试 (16字节，CRC在 byte 15)。"""
        from bridgecore.operator_lib import compute_checksum
        data = bytes(range(15))  # 15 bytes of data
        chk = compute_checksum("crc8_maxim", data)  # 1 byte
        payload = data + chk  # 16 bytes total
        self.assertEqual(len(payload), 16)

        session = RecordingSession()
        session.add_record(self._make_record("guest_card", payload.hex()))

        result = self.learner.learn(session)
        self.assertEqual(result.checksum_algorithm, "crc8_maxim")
        self.assertEqual(result.checksum_offset, 15)
        self.assertEqual(result.checksum_length, 1)

    def test_learn_layout(self):
        """差分分析推断布局。"""
        # 两个 payload，锁号位置 (6-7) 不同
        p1 = bytes.fromhex("000102030405060708090A0B0C0D0102")
        p2 = bytes.fromhex("000102030405AABB08090A0B0C0D0102")

        session = RecordingSession()
        session.add_record(self._make_record("guest_card", p1.hex()))
        session.add_record(self._make_record("guest_card_v2", p2.hex()))

        result = self.learner.learn(session)
        self.assertIsNotNone(result.layout.get("lock_no_offset"))

    def test_learn_card_types(self):
        """卡型分类。"""
        p1 = bytes.fromhex("000102030405060708090A0B0C0D0102")
        p2 = bytes.fromhex("0001020304050607080B0A0B0C0D0102")

        session = RecordingSession()
        session.add_record(self._make_record("guest_card", p1.hex()))
        session.add_record(self._make_record("master_card", p2.hex()))

        result = self.learner.learn(session)
        self.assertIn("guest", result.card_types)
        self.assertIn("master", result.card_types)

    def test_learn_from_file(self):
        """从 JSONL 文件加载学习。"""
        # payloads with 0xFB at byte 15 (signature_byte15)
        p1 = bytes.fromhex("000102030405060708090A0B0C0D10FB")
        p2 = bytes.fromhex("0001020304050607080B0A0B0C0D10FB")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                          delete=False, encoding="utf-8") as f:
            f.write(json.dumps(self._make_record("guest_card", p1.hex()), ensure_ascii=False) + "\n")
            f.write(json.dumps(self._make_record("master_card", p2.hex()), ensure_ascii=False) + "\n")
            tmp_path = f.name

        try:
            result = self.learner.learn_from_file(tmp_path)
            self.assertTrue(result.has_valid_result)
        finally:
            os.unlink(tmp_path)


# ──────────────────────────────────────────────────────────────────
# ProfileGenerator
# ──────────────────────────────────────────────────────────────────

class TestProfileGenerator(unittest.TestCase):
    def setUp(self):
        self.learn_result = ProtocolLearnResult()
        self.learn_result.checksum_algorithm = "add_sum_16"
        self.learn_result.checksum_offset = 14
        self.learn_result.checksum_length = 2
        self.learn_result.payload_size = 16
        self.learn_result.layout = {
            "site_offset": 4, "site_length": 2,
            "lock_no_offset": 6, "lock_no_length": 2,
            "payload_size": 16,
        }
        self.learn_result.card_types = {
            "guest":    {"type_byte_high": 0x6, "fn_name": "guest_card"},
            "master":   {"type_byte_high": 0xB, "fn_name": "master_card"},
        }
        self.learn_result.confidence = 0.8

        self.probe_result = ProbeResult(
            dll_name="TestLock.dll",
            exports=[{"name": "ReadCard", "ordinal": 1, "address": 0x1000}],
            classified={"read_card": "ReadCard", "guest_card": "GuestCard"},
            brand_guess="测试品牌",
        )

    def test_generate_profile(self):
        profile = generate_profile(self.learn_result, self.probe_result)
        self.assertEqual(profile["brand"], "测试品牌")
        self.assertEqual(profile["payload_size"], 16)
        self.assertEqual(profile["checksum"]["algorithm"], "add_sum_16")
        self.assertTrue(profile["supported"])
        self.assertEqual(profile["confidence"], 0.8)
        self.assertIn("guest", profile["card_types"])
        self.assertIn("master", profile["card_types"])

    def test_save_profile(self):
        profile = generate_profile(self.learn_result, self.probe_result)
        # Use TEXT mode (no encoding arg) for tempfile, write with utf-8
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            tmp_path = f.name

        try:
            saved = save_profile(profile, tmp_path)
            self.assertEqual(saved, tmp_path)

            with open(tmp_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded["brand"], "测试品牌")
        finally:
            os.unlink(tmp_path)

    def test_profile_with_channel(self):
        info = ChannelInfo(channel_type="dll", device_name="Test")
        profile = generate_profile(self.learn_result, self.probe_result, info)
        self.assertEqual(profile["physical_channel"], "dll")


if __name__ == "__main__":
    unittest.main()
