"""
bridgecore/tests/test_panic_recovery.py — 恐慌恢复模块测试

测试 PanicRecovery 的双重冗余恢复机制：
- Level 1 软复位（重启 bridge 子进程）
- Level 2 强制断电复位（PowerController 抽象）
- 反馈闭环（Initialize 帧）
- 失败计数与自动触发
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from bridgecore.panic_recovery import (
    PanicRecovery, RecoveryRecord, RecoverySummary,
    PowerController, WinUsbPowerController, SerialRelayPowerController,
)
from bridgecore.fault_manager import FaultManager, FaultKind


# ──────────────────────────────────────────────────────────────────
# Mock Bridge
# ──────────────────────────────────────────────────────────────────

class MockBridgeForPanic:
    """模拟 RflBridge 行为，支持软复位测试。"""

    def __init__(self):
        self._last_dll_path = "C:\\test\\TestDll.dll"
        self._last_extra_paths = ["C:\\test"]
        self._last_init_d12 = 1
        self._dll_loaded = False
        self._started = False
        self._call_count = 0
        self._stop_called = False

        # 控制模拟行为
        self.soft_reset_should_fail = False
        self.initialize_should_fail = False
        self.load_dll_should_fail = False

    def stop(self) -> None:
        self._stop_called = True
        self._started = False
        self._dll_loaded = False

    def start(self, *, force_restart: bool = False) -> None:
        self._started = True
        self._call_count += 1

    @property
    def dll_loaded(self) -> bool:
        return self._dll_loaded

    def load_dll(self, dll_path: str, extra_paths: list[str]) -> dict:
        if self.load_dll_should_fail:
            return {"ok": False, "loaded": False, "error": "模拟 load_dll 失败"}
        self._dll_loaded = True
        return {"ok": True, "loaded": True}

    def initialize(self, d12: int, *, timeout: float = 5.0) -> dict:
        if self.initialize_should_fail:
            return {"ok": False, "ret": -1, "error": "模拟 initialize 失败"}
        return {"ok": True, "ret": 0}

    def ping(self, *, timeout: float = 2.0) -> bool:
        return self._started and self._dll_loaded


class MockPowerController(PowerController):
    """模拟 PowerController，支持失败模式。"""

    def __init__(self):
        self.should_fail = False
        self.cycle_count = 0

    def cycle_port(self, vid: str = "", pid: str = "") -> bool:
        self.cycle_count += 1
        if self.should_fail:
            return False
        return True

    def name(self) -> str:
        return "MockPower"


class MockFaultyBridge(MockBridgeForPanic):
    """所有操作都失败的 bridge。"""

    def stop(self) -> None:
        raise RuntimeError("模拟 stop 失败")

    def start(self, *, force_restart: bool = False) -> None:
        raise RuntimeError("模拟 start 失败")

    def load_dll(self, dll_path: str, extra_paths: list[str]) -> dict:
        return {"ok": False, "loaded": False, "error": "模拟 load_dll 失败"}

    def initialize(self, d12: int, *, timeout: float = 5.0) -> dict:
        return {"ok": False, "ret": -1, "error": "模拟 initialize 失败"}


# ──────────────────────────────────────────────────────────────────
# 测试
# ──────────────────────────────────────────────────────────────────

class TestRecoveryRecord(unittest.TestCase):
    def test_create(self):
        r = RecoveryRecord(level="soft_reset", success=True, triggered_by="test")
        self.assertEqual(r.level, "soft_reset")
        self.assertTrue(r.success)
        self.assertGreater(r.timestamp, 0)

    def test_auto_timestamp(self):
        r = RecoveryRecord(level="power_cycle", success=False, error="fail")
        self.assertGreater(r.timestamp, 0)


class TestRecoverySummary(unittest.TestCase):
    def test_defaults(self):
        s = RecoverySummary()
        self.assertFalse(s.recovered)
        self.assertFalse(s.level_1_attempted)
        self.assertFalse(s.level_2_success)
        self.assertEqual(s.duration, 0.0)
        self.assertEqual(s.error, "")


class TestMockPowerController(unittest.TestCase):
    def test_cycle_success(self):
        ctrl = MockPowerController()
        self.assertTrue(ctrl.cycle_port())
        self.assertEqual(ctrl.cycle_count, 1)

    def test_cycle_failure(self):
        ctrl = MockPowerController()
        ctrl.should_fail = True
        self.assertFalse(ctrl.cycle_port())
        self.assertEqual(ctrl.cycle_count, 1)

    def test_name(self):
        ctrl = MockPowerController()
        self.assertEqual(ctrl.name(), "MockPower")


# ──────────────────────────────────────────────────────────────────
# PanicRecovery 核心测试（手动触发模式）
# ──────────────────────────────────────────────────────────────────

class TestPanicRecoveryExecute(unittest.TestCase):
    def setUp(self):
        self.bridge = MockBridgeForPanic()
        self.power_ctrl = MockPowerController()
        self.panic = PanicRecovery(
            self.bridge,
            self.power_ctrl,
            soft_reset_threshold=3,
            max_soft_resets=2,
            max_power_cycles=1,
            recovery_pause=0.01,
        )

    def test_initial_state(self):
        self.assertFalse(self.panic.is_recovering)
        self.assertEqual(self.panic.last_recovery_time, 0.0)
        self.assertEqual(len(self.panic.history), 0)

    def test_level_1_success(self):
        """Level 1 软复位成功。"""
        summary = self.panic.execute("test_level1")
        self.assertTrue(summary.level_1_attempted)
        self.assertTrue(summary.level_1_success)
        self.assertTrue(summary.recovered)
        self.assertEqual(summary.level_1_record.level, "soft_reset")

    def test_level_1_failure_triggers_level_2(self):
        """Level 1 失败 → 自动执行 Level 2。"""
        self.bridge.soft_reset_should_fail = True
        # 但实际的软复位是靠 stop/start/load_dll/initialize，
        # 这些方法在我们修改后的 bridge 上会失败
        class FailBridge:
            def __init__(self):
                self._last_dll_path = "C:\\test.dll"
                self._last_extra_paths = []
                self._last_init_d12 = 1
                self.dll_loaded = False
            def stop(self): raise RuntimeError("stop fail")
            def start(self, **kw): raise RuntimeError("start fail")
            def load_dll(self, *a): return {"ok": False, "loaded": False, "error":"fail"}
            def initialize(self, d12, **kw): return {"ok": False, "ret": -1, "error":"fail"}

        panic = PanicRecovery(
            FailBridge(), self.power_ctrl,
            soft_reset_threshold=3, max_soft_resets=1, max_power_cycles=1,
            recovery_pause=0.01,
        )
        summary = panic.execute("test_auto_l2")
        self.assertTrue(summary.level_1_attempted)
        self.assertFalse(summary.level_1_success)
        self.assertTrue(summary.level_2_attempted)

    def test_level_2_success(self):
        """Level 2 强制断电复位成功。"""
        # 让 Level 1 失败，但 Level 2 成功
        class L1FailBridge:
            def __init__(self):
                self._last_dll_path = "C:\\test.dll"
                self._last_extra_paths = []
                self._last_init_d12 = 1
            def stop(self): raise RuntimeError("stop fail")
            def start(self, **kw): pass
            def load_dll(self, *a): return {"ok": True, "loaded": True}
            def initialize(self, d12, **kw): return {"ok": True, "ret": 0}

        panic = PanicRecovery(
            L1FailBridge(), self.power_ctrl,
            soft_reset_threshold=3, max_soft_resets=1, max_power_cycles=1,
            recovery_pause=0.01,
        )
        summary = panic.execute("test_l2_success")
        self.assertTrue(summary.level_1_attempted)
        self.assertFalse(summary.level_1_success)
        self.assertTrue(summary.level_2_attempted)
        self.assertTrue(summary.level_2_success)
        self.assertTrue(summary.recovered)

    def test_all_fail(self):
        """所有恢复手段都失败。"""
        ctrl = MockPowerController()
        ctrl.should_fail = True

        class AllFailBridge:
            def __init__(self):
                self._last_dll_path = "C:\\test.dll"
                self._last_extra_paths = []
                self._last_init_d12 = 1
            def stop(self): raise RuntimeError("fail")
            def start(self, **kw): raise RuntimeError("fail")
            def load_dll(self, *a): return {"ok": False, "loaded": False}
            def initialize(self, d12, **kw): return {"ok": False, "ret": -1}

        panic = PanicRecovery(
            AllFailBridge(), ctrl,
            soft_reset_threshold=3, max_soft_resets=1, max_power_cycles=1,
            recovery_pause=0.01,
        )
        summary = panic.execute("test_all_fail")
        self.assertFalse(summary.recovered)
        self.assertNotEqual(summary.error, "")

    def test_concurrent_recovery_blocked(self):
        """并发恢复被阻塞。"""
        self.panic._recovering = True
        summary = self.panic.execute("test_concurrent")
        self.assertFalse(summary.recovered)
        self.assertIn("已在执行中", summary.error)

    def test_send_initialize_after_recovery(self):
        """恢复后发送 Initialize。"""
        summary = self.panic.execute("test_init")
        self.assertTrue(summary.initialized)

    def test_history_recorded(self):
        """恢复记录写入历史。"""
        self.panic.execute("test_history")
        self.assertGreater(len(self.panic.history), 0)


# ──────────────────────────────────────────────────────────────────
# 失败计数与自动触发
# ──────────────────────────────────────────────────────────────────

class TestPanicRecoveryAutoTrigger(unittest.TestCase):
    def setUp(self):
        self.bridge = MockBridgeForPanic()
        self.power_ctrl = MockPowerController()
        self.panic = PanicRecovery(
            self.bridge, self.power_ctrl,
            soft_reset_threshold=3, max_soft_resets=1, max_power_cycles=1,
            recovery_pause=0.01,
        )

    def test_hw_fails_accumulate(self):
        """连续硬件失败累加计数。"""
        for i in range(2):
            triggered = self.panic._increment_and_maybe_recover(
                FaultKind.HARDWARE, triggered_by="test"
            )
            self.assertFalse(triggered, f"第 {i+1} 次不应触发")
        # 第 3 次触发
        triggered = self.panic._increment_and_maybe_recover(
            FaultKind.HARDWARE, triggered_by="test"
        )
        self.assertTrue(triggered)

    def test_timeout_fails_accumulate(self):
        """连续超时失败累加计数。"""
        for i in range(2):
            self.assertFalse(
                self.panic._increment_and_maybe_recover(FaultKind.TIMEOUT)
            )
        self.assertTrue(
            self.panic._increment_and_maybe_recover(FaultKind.TIMEOUT)
        )

    def test_hw_and_timeout_mixed(self):
        """硬件+超时混合累计达到阈值。"""
        self.assertFalse(
            self.panic._increment_and_maybe_recover(FaultKind.HARDWARE)
        )
        self.assertFalse(
            self.panic._increment_and_maybe_recover(FaultKind.TIMEOUT)
        )
        # 第 3 次触发
        self.assertTrue(
            self.panic._increment_and_maybe_recover(FaultKind.HARDWARE)
        )

    def test_protocol_errors_not_counted(self):
        """协议错误不触发恢复。"""
        self.assertFalse(
            self.panic._increment_and_maybe_recover(FaultKind.PROTOCOL)
        )
        self.assertEqual(
            self.panic._consecutive_hw_fails, 0
        )

    def test_counters_reset_after_recovery(self):
        """恢复后失败计数清零。"""
        for _ in range(3):
            self.panic._increment_and_maybe_recover(FaultKind.HARDWARE)
        self.panic._reset_counters()
        self.assertEqual(self.panic._consecutive_hw_fails, 0)


# ──────────────────────────────────────────────────────────────────
# 注册 Injectable 挂钩
# ──────────────────────────────────────────────────────────────────

class TestPanicRecoveryHooks(unittest.TestCase):
    def setUp(self):
        self.bridge = MockBridgeForPanic()
        self.power_ctrl = MockPowerController()
        self.panic = PanicRecovery(
            self.bridge, self.power_ctrl,
            soft_reset_threshold=2,  # 较低阈值方便测试
            max_soft_resets=1, max_power_cycles=0,
            recovery_pause=0.01,
        )

    def test_notify_failure_triggers_recovery(self):
        """notify_failure 累加计数，达阈值触发恢复。"""
        self.panic._soft_reset_threshold = 1
        self.panic._max_soft_resets = 1
        triggered = self.panic.notify_failure(FaultKind.HARDWARE, triggered_by="test_fn")
        self.assertTrue(triggered)
        time.sleep(0.5)

    def test_notify_fuse_triggers_recovery(self):
        """notify_fuse 直接触发恢复。"""
        self.panic._max_soft_resets = 1
        triggered = self.panic.notify_fuse("hardware", triggered_by="fuse_test")
        self.assertTrue(triggered)
        time.sleep(0.5)

    def test_get_diagnostic_report(self):
        """诊断报告包含恢复统计。"""
        report = self.panic.get_diagnostic_report()
        self.assertIn("status", report)
        self.assertIn("soft_reset_count", report)
        self.assertIn("max_soft_resets", report)
        self.assertIn("power_controller", report)

    def test_reset_stats(self):
        """重置统计。"""
        self.panic._soft_reset_count = 5
        self.panic._consecutive_hw_fails = 3
        self.panic.reset_stats()
        self.assertEqual(self.panic._soft_reset_count, 0)
        self.assertEqual(self.panic._consecutive_hw_fails, 0)


# ──────────────────────────────────────────────────────────────────
# PowerController 抽象层
# ──────────────────────────────────────────────────────────────────

class TestPowerControllerAbstract(unittest.TestCase):
    def test_winusb_exists(self):
        """WinUsbPowerController 可实例化（需要 pywinusb/win32 才真正工作）。"""
        ctrl = WinUsbPowerController()
        self.assertEqual(ctrl.name(), "WinUSB_PowerCycle")

    def test_serial_relay_exists(self):
        """SerialRelayPowerController 可实例化（需要 pyserial + 配置）。"""
        ctrl = SerialRelayPowerController()
        self.assertIn("SerialRelay", ctrl.name())

    def test_auto_select_controller(self):
        """自动选择控制器不抛异常。"""
        ctrl = PanicRecovery._auto_select_controller()
        self.assertIsNotNone(ctrl)
        self.assertIsInstance(ctrl, PowerController)

    def test_recovery_summary_after_execute(self):
        """恢复摘要属性。"""
        bridge = MockBridgeForPanic()
        ctrl = MockPowerController()
        panic = PanicRecovery(
            bridge, ctrl,
            soft_reset_threshold=3, max_soft_resets=1, max_power_cycles=0,
            recovery_pause=0.01,
        )
        summary = panic.execute()
        self.assertIsInstance(summary, RecoverySummary)
        self.assertIn("soft_reset", str(summary.level_1_record))


class TestPanicRecoveryCallbacks(unittest.TestCase):
    def test_success_callback(self):
        """恢复成功回调。"""
        bridge = MockBridgeForPanic()
        ctrl = MockPowerController()
        panic = PanicRecovery(bridge, ctrl, recovery_pause=0.01)

        called = []
        panic.set_on_recovery_success(lambda level: called.append(level))
        panic.set_on_recovery_failure(lambda err: called.append(f"fail:{err}"))

        panic.execute()
        self.assertGreater(len(called), 0)

    def test_failure_callback(self):
        """恢复失败回调。"""
        class FailBridge:
            def __init__(self):
                self._last_dll_path = "C:\\test.dll"
                self._last_extra_paths = []
                self._last_init_d12 = 1
            def stop(self): raise RuntimeError("fail")
            def start(self, **kw): raise RuntimeError("fail")
            def load_dll(self, *a): return {"ok": False, "loaded": False}
            def initialize(self, d12, **kw): return {"ok": False, "ret": -1}

        ctrl = MockPowerController()
        ctrl.should_fail = True
        panic = PanicRecovery(FailBridge(), ctrl, recovery_pause=0.01)

        called = []
        panic.set_on_recovery_failure(lambda err: called.append(err))
        panic.execute()
        self.assertGreater(len(called), 0)


if __name__ == "__main__":
    unittest.main()
