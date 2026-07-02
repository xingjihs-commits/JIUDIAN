"""
bridgecore/tests/test_injector.py — Injector 单元测试

覆盖：
- FaultManager: 成功/失败/熔断/错误分类/诊断报告/指数退避
- Injector: 回放成功/回放失败/熔断跳过/进度回调/batch 回放
- RxMonitor: 正常探测/故障检测/多策略探测
- 并发安全
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_HOTEL_DIR = Path(__file__).resolve().parents[2]
if str(_HOTEL_DIR) not in sys.path:
    sys.path.insert(0, str(_HOTEL_DIR))

from bridgecore.injector import Injector, ReplaySummary, ReplayStatus
from bridgecore.fault_manager import FaultManager, FaultTriggered, FaultKind
from bridgecore.rx_monitor import RxMonitor
from bridgecore.tests import MockBridge, MockBridgeFaulty


# ──────────────────────────────────────────────────────────────────
# FaultManager 测试
# ──────────────────────────────────────────────────────────────────

class TestFaultManager(unittest.TestCase):
    """熔断管理器核心逻辑。"""

    def setUp(self):
        self.fm = FaultManager(threshold=3)

    def test_success(self):
        self.fm.record_attempt("guest_card", {"ok": True, "ret": 0})
        self.assertEqual(self.fm.consecutive_fails, 0)
        self.assertFalse(self.fm.is_faulted())

    def test_one_failure_no_fuse(self):
        self.fm.record_attempt("guest_card", {"ok": False, "ret": -1})
        self.assertEqual(self.fm.consecutive_fails, 1)
        self.assertFalse(self.fm.is_faulted())

    def test_three_failures_triggers_fuse(self):
        for i in range(3):
            self.fm.record_attempt("guest_card", {"ok": False, "ret": -1})
        self.assertEqual(self.fm.consecutive_fails, 3)
        self.assertTrue(self.fm.is_faulted())

    def test_success_resets_counter(self):
        self.fm.record_attempt("guest_card", {"ok": False, "ret": -1})
        self.fm.record_attempt("guest_card", {"ok": False, "ret": -1})
        self.assertEqual(self.fm.consecutive_fails, 2)
        self.fm.record_attempt("guest_card", {"ok": True, "ret": 0})
        self.assertEqual(self.fm.consecutive_fails, 0)
        self.assertFalse(self.fm.is_faulted())

    def test_error_classification(self):
        """自动错误分类。"""
        self.assertTrue(FaultManager._is_ok({"ok": True, "ret": 0}))
        self.assertFalse(FaultManager._is_ok(None))
        self.assertFalse(FaultManager._is_ok({"ok": False}))
        self.assertFalse(FaultManager._is_ok({"ok": True, "error": "fail"}))
        self.assertFalse(FaultManager._is_ok({"ok": True, "ret": -1}))
        self.assertTrue(FaultManager._is_ok({"ok": True, "ret": 0, "out": {}}))

    def test_kind_classification(self):
        self.assertEqual(
            FaultManager._classify_error({"error": "timeout after 5s"}),
            FaultKind.TIMEOUT,
        )
        self.assertEqual(
            FaultManager._classify_error({"error": "bridge subprocess crashed"}),
            FaultKind.NETWORK,
        )
        self.assertEqual(
            FaultManager._classify_error({"error": "USB device not found"}),
            FaultKind.HARDWARE,
        )
        self.assertEqual(
            FaultManager._classify_error({"error": "DLL ret = -1"}),
            FaultKind.PROTOCOL,
        )

    def test_force_fuse(self):
        self.assertFalse(self.fm.is_faulted())
        self.fm.force_fuse("manual test", kind=FaultKind.HARDWARE)
        self.assertTrue(self.fm.is_faulted())

    def test_diagnostic_report(self):
        """诊断报告包含正确统计。"""
        self.fm.record_attempt("test", {"ok": False, "ret": -1})
        self.fm.record_attempt("test", {"ok": True, "ret": 0})
        self.fm.record_attempt("test", {"ok": False, "ret": -1})
        report = self.fm.get_diagnostic_report()
        self.assertEqual(report["total_attempts"], 3)
        self.assertEqual(report["total_fails"], 2)
        self.assertEqual(report["consecutive_fails"], 1)  # 第 3 次失败后计数为 1

    def test_save_diagnostic_log(self):
        self.fm.record_attempt("test", {"ok": False, "ret": -1})
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            saved = self.fm.save_diagnostic_log(tmp)
            self.assertTrue(os.path.exists(saved))
            with open(saved, "r") as f:
                data = json.load(f)
            self.assertIn("total_attempts", data)
            self.assertIn("events", data)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_concurrent_record_safe(self):
        """并发记录不丢失且线程安全。"""
        errors = []

        def writer():
            for _ in range(100):
                try:
                    self.fm.record_attempt("test", {"ok": True, "ret": 0})
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(errors), 0)
        self.assertEqual(self.fm.total_attempts, 500)

    def test_different_threshold(self):
        fm = FaultManager(threshold=5)
        for i in range(4):
            fm.record_attempt("test", {"ok": False})
        self.assertFalse(fm.is_faulted())
        fm.record_attempt("test", {"ok": False})
        self.assertTrue(fm.is_faulted())

    def test_is_ok_edge_cases(self):
        self.assertFalse(FaultManager._is_ok({}))
        self.assertFalse(FaultManager._is_ok({"ret": 0}))  # ok 缺失
        self.assertTrue(FaultManager._is_ok({"ok": True, "ret": "abc"}))  # ret 不可解析时不判定失败
        self.assertFalse(FaultManager._is_ok({"ok": True, "error": "err"}))  # 有 error
        self.assertTrue(FaultManager._is_ok({"ok": True, "ret": 0}))
        self.assertTrue(FaultManager._is_ok({"ok": True}))  # ret 缺失也可以

    def test_error_classify_empty(self):
        k = FaultManager._classify_error({})
        self.assertEqual(k, FaultKind.UNKNOWN)

    def test_error_classify_timeout_cn(self):
        k = FaultManager._classify_error({"error": "调用超时"})
        self.assertEqual(k, FaultKind.TIMEOUT)

    def test_backoff_value(self):
        fm = FaultManager(threshold=5)
        # 未失败时退避为 0
        self.assertEqual(fm.get_backoff(), 0.0)

        # 第 1 次失败: base * 2^0 = 1.0
        fm.record_attempt("test", {"ok": False})
        self.assertAlmostEqual(fm.get_backoff(), 1.0, places=1)

        # 第 2 次失败: base * 2^1 = 2.0
        fm.record_attempt("test", {"ok": False})
        self.assertAlmostEqual(fm.get_backoff(), 2.0, places=1)

        # 成功复位后为 0
        fm.record_attempt("test", {"ok": True})
        self.assertEqual(fm.get_backoff(), 0.0)

    def test_remaining_cooldown(self):
        fm = FaultManager(threshold=2)
        fm.record_attempt("test", {"ok": False})
        fm.record_attempt("test", {"ok": False})
        self.assertTrue(fm.is_faulted())
        self.assertGreater(fm.remaining_cooldown(), 0)

    def test_get_last_success(self):
        self.assertIsNone(self.fm.get_last_success_ago())
        self.fm.record_attempt("test", {"ok": True, "ret": 0})
        self.assertIsNotNone(self.fm.get_last_success_ago())

    def test_on_fuse_callback(self):
        """熔断触发后，on_fuse 回调被调用。"""
        cb_calls = []
        fm = FaultManager(threshold=2)
        fm.on_fuse(lambda kind: cb_calls.append(kind))
        fm.record_attempt("test", {"ok": False})
        self.assertEqual(len(cb_calls), 0)  # 未触发熔断
        fm.record_attempt("test", {"ok": False})
        self.assertGreaterEqual(len(cb_calls), 1)
        self.assertIn(cb_calls[0], ("hardware", "timeout", "protocol", "unknown"))


# ──────────────────────────────────────────────────────────────────
# Injector 测试
# ──────────────────────────────────────────────────────────────────

class TestInjector(unittest.TestCase):
    """注入核回放功能。"""

    def setUp(self):
        self.bridge = MockBridge()
        self.bridge.initialize(d12=1)
        self.injector = Injector(self.bridge)

    def test_empty_records(self):
        summary = self.injector.replay([])
        self.assertEqual(summary.total, 0)
        self.assertEqual(summary.success, 0)
        self.assertEqual(summary.failed, 0)
        self.assertFalse(summary.faulted)

    def test_replay_guest_card(self):
        records = [
            {"fn_name": "guest_card", "args_in": {"lock_no": "0101"}},
        ]
        summary = self.injector.replay(records)
        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.success, 1)
        self.assertEqual(summary.failed, 0)
        self.assertGreaterEqual(summary.duration, 0)

    def test_replay_multiple(self):
        records = [
            {"fn_name": "guest_card", "args_in": {"lock_no": "0101"}},
            {"fn_name": "master_card", "args_in": {}},
            {"fn_name": "buzzer", "args_in": {"ms": 100}},
        ]
        summary = self.injector.replay(records)
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.success, 3)
        self.assertEqual(len(summary.operations), 3)
        for op in summary.operations:
            self.assertEqual(op.status, ReplayStatus.SUCCESS)

    def test_replay_unknown_method(self):
        """未知方法应 report failed。"""
        records = [
            {"fn_name": "non_existent_method", "args_in": {}},
        ]
        summary = self.injector.replay(records)
        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.failed, 1)

    def test_fault_tolerance(self):
        """连续失败达阈值后触发熔断，剩余操作跳过。"""
        bridge = MockBridge()
        bridge._simulate_fail = "guest_card"
        injector = Injector(bridge)

        records = [
            {"fn_name": "guest_card", "args_in": {}},
            {"fn_name": "guest_card", "args_in": {}},
            {"fn_name": "guest_card", "args_in": {}},
            {"fn_name": "guest_card", "args_in": {}},  # 这步应被跳过
        ]
        summary = injector.replay(records)
        self.assertTrue(summary.faulted)
        self.assertIsNotNone(summary.fault_report)
        self.assertGreater(summary.failed, 0)

    def test_progress_callback(self):
        """进度回调应正确触发。"""
        records = [
            {"fn_name": "guest_card", "args_in": {"lock_no": "0101"}},
            {"fn_name": "master_card", "args_in": {}},
        ]
        callbacks = []

        def on_progress(current, total, result):
            callbacks.append((current, total, result.status))

        summary = self.injector.replay(records, progress_callback=on_progress)
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(callbacks[0][1], 2)  # total

    def test_batch_callback(self):
        records = [
            {"fn_name": "guest_card", "args_in": {"lock_no": "0101"}},
            {"fn_name": "master_card", "args_in": {}},
        ]
        batches = []

        def on_batch(batch_idx, total_batches):
            batches.append((batch_idx, total_batches))

        injector = Injector(self.bridge)
        injector._batch_size = 1
        injector.replay(records, batch_callback=on_batch)
        self.assertEqual(len(batches), 2)

    def test_rate_control_no_negative(self):
        """快速回放不报错。"""
        records = []
        for i in range(10):
            records.append({"fn_name": "buzzer", "args_in": {"ms": i * 10}})
        summary = self.injector.replay(records)
        self.assertEqual(summary.success, 10)

    def test_result_structure(self):
        """OpResult 包含所有必需字段。"""
        records = [{"fn_name": "buzzer", "args_in": {"ms": 20}}]
        summary = self.injector.replay(records)
        op = summary.operations[0]
        self.assertGreater(op.seq, 0)
        self.assertTrue(op.fn_name)
        self.assertIn(op.status, (ReplayStatus.SUCCESS,))
        self.assertGreaterEqual(op.duration, 0)
        self.assertIsNotNone(op.ret)

    def test_pause_resume_keepalive(self):
        """Injector 在回放前后应切换 keepalive。"""
        try:
            from lock_adapters.bridge_client import RflBridge
        except ModuleNotFoundError:
            self.skipTest("lock_adapters 在采集器独立环境中不可用")
        # 默认 unpaused
        self.assertFalse(RflBridge.is_keepalive_paused())
        records = [{"fn_name": "buzzer", "args_in": {"ms": 20}}]
        summary = self.injector.replay(records)
        # 回放后恢复
        self.assertFalse(RflBridge.is_keepalive_paused())
        self.assertEqual(summary.success, 1)


# ──────────────────────────────────────────────────────────────────
# RxMonitor 测试
# ──────────────────────────────────────────────────────────────────

class TestRxMonitor(unittest.TestCase):
    """RX 监控线程。"""

    def test_healthy_monitor(self):
        bridge = MockBridge()
        bridge.initialize(d12=1)
        fm = FaultManager(threshold=3)
        monitor = RxMonitor(bridge, fault_manager=fm)

        monitor.start()
        time.sleep(0.3)
        monitor.stop()

        self.assertFalse(monitor.running)
        self.assertFalse(fm.is_faulted())

    def test_detects_unhealthy(self):
        """ping 持续失败应触发熔断。"""
        bridge = MockBridgeFaulty(fail_rate=1.0)  # 100% 失败
        bridge.initialize(d12=1)
        fm = FaultManager(threshold=2)
        monitor = RxMonitor(bridge, fault_manager=fm)
        monitor._interval = 0.1
        monitor._fail_threshold = 2

        monitor.start()
        time.sleep(0.35)  # 足够让监控线程触发熔断
        monitor.stop()

        # 最终应该熔断
        self.assertTrue(fm.is_faulted())

    def test_stop_idempotent(self):
        monitor = RxMonitor(MockBridge(), FaultManager())
        monitor.stop()  # 未 start 也能 stop
        monitor.stop()

    def test_start_twice(self):
        monitor = RxMonitor(MockBridge(), FaultManager())
        monitor.start()
        monitor.start()  # 不会重复启动线程
        monitor.stop()

    def test_config_respected(self):
        """配置的 interval 和 threshold 应生效。"""
        import bridgecore.config
        bridge = MockBridgeFaulty(fail_rate=1.0)
        bridge.initialize(d12=1)
        fm = FaultManager(threshold=3)
        monitor = RxMonitor(bridge, fault_manager=fm)
        self.assertAlmostEqual(monitor._interval, bridgecore.config.DEFAULT_RX_INTERVAL)
        self.assertEqual(monitor._fail_threshold, bridgecore.config.DEFAULT_RX_FAIL_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
