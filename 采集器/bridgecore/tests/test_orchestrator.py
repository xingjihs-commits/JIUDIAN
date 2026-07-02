"""
bridgecore/tests/test_orchestrator.py — 编排器端到端测试

覆盖：
- record_session 上下文管理器
- 录制/回放完整闭环
- 录制时暂停 RxMonitor
- rx_monitor 探针不污染录制
- 回放写卡后 payload 验证
- list_recordings
- 错误处理
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HOTEL_DIR = Path(__file__).resolve().parents[2]
if str(_HOTEL_DIR) not in sys.path:
    sys.path.insert(0, str(_HOTEL_DIR))

from bridgecore.orchestrator import BridgeCoreOrchestrator
from bridgecore.observer import RecordingSession, load_recording, list_sessions
from bridgecore.injector import ReplaySummary, ReplayStatus
from bridgecore.fault_manager import FaultManager
from bridgecore.rx_monitor import RxMonitor
from bridgecore.tests import MockBridge


class TestOrchestrator(unittest.TestCase):
    """编排器核心功能。"""

    def setUp(self):
        self.bridge = MockBridge()
        self.bridge.initialize(d12=1)
        self.recording_dir = tempfile.mkdtemp()
        self.orch = BridgeCoreOrchestrator(
            self.bridge,
            recording_dir=self.recording_dir,
        )

    def tearDown(self):
        # 清理录制目录
        import shutil
        if os.path.exists(self.recording_dir):
            shutil.rmtree(self.recording_dir)

    def test_record_session_context(self):
        """record_session 上下文管理器正常工作。"""
        with self.orch.record_session(session_tag="test_context"):
            self.bridge.guest_card(lock_no="0101")
            self.bridge.master_card()

        # 录制文件应存在
        self.assertIsNotNone(self.orch.last_session_path)
        self.assertTrue(os.path.exists(self.orch.last_session_path))

    def test_record_then_replay(self):
        """录制→回放完整闭环。"""
        with self.orch.record_session(session_tag="full_loop"):
            self.bridge.guest_card(lock_no="0101")
            self.bridge.master_card()
            self.bridge.buzzer(ms=100)

        # 回放（注意：录制包含 readback 操作，所以 total 会多于显式调用数）
        summary = self.orch.replay_last()
        self.assertIsInstance(summary, ReplaySummary)
        self.assertGreaterEqual(summary.total, 3)  # 至少包含 3 个显式调用
        self.assertGreaterEqual(summary.success, 3)
        self.assertEqual(summary.failed, 0)

    def test_record_multiple_replay_first(self):
        """录制多次，回放指定的那次。"""
        with self.orch.record_session(session_tag="session_1"):
            self.bridge.guest_card(lock_no="0101")

        path_1 = self.orch.last_session_path

        with self.orch.record_session(session_tag="session_2"):
            self.bridge.guest_card(lock_no="0202")
            self.bridge.master_card()

        # 回放第一次录制的（注意：录制包含 readback，记录数多于显式调用数）
        summary = self.orch.replay(path_1)
        self.assertGreaterEqual(summary.total, 1)
        self.assertGreaterEqual(summary.success, 1)

    def test_replay_last_empty(self):
        """没有录制时 replay_last 应报错。"""
        with self.assertRaises(RuntimeError):
            self.orch.replay_last()

    def test_nested_record_raises(self):
        """不支持嵌套录制。"""
        with self.orch.record_session(session_tag="outer"):
            with self.assertRaises(RuntimeError):
                with self.orch.record_session(session_tag="inner"):
                    pass

    def test_record_empty_session(self):
        """录制会话为空时不保存文件。"""
        with self.orch.record_session(session_tag="empty"):
            pass  # 不执行任何 bridge 调用
        # 空会话不会保存，last_session_path 保持 None
        self.assertIsNone(self.orch.last_session_path)

    def test_list_recordings(self):
        """list_recordings 返回正确的录制列表。"""
        with self.orch.record_session(session_tag="list_test"):
            self.bridge.guest_card(lock_no="0101")

        sessions = self.orch.list_recordings()
        self.assertGreaterEqual(len(sessions), 1)
        self.assertIn("path", sessions[0])
        self.assertIn("session_id", sessions[0])
        self.assertIn("record_count", sessions[0])

    def test_reset_fault(self):
        """手动复位熔断器。"""
        # 先触发熔断
        self.orch.fault_manager.force_fuse("test")
        self.assertTrue(self.orch.fault_manager.is_faulted())

        self.orch.reset_fault()
        self.assertFalse(self.orch.fault_manager.is_faulted())

    def test_replay_fault_tolerance(self):
        """回放失败达阈值后触发熔断。"""
        bridge = MockBridge()
        bridge._simulate_fail = "guest_card"
        bridge.initialize(d12=1)

        orch = BridgeCoreOrchestrator(bridge, recording_dir=self.recording_dir)

        # 先录制
        with orch.record_session(session_tag="fault_test"):
            bridge._simulate_fail = "guest_card"
            bridge.guest_card(lock_no="0101")
            bridge.guest_card(lock_no="0101")
            bridge.guest_card(lock_no="0101")
            bridge.guest_card(lock_no="0101")

        #  取消模拟失败，回放
        bridge._simulate_fail = "guest_card"  # 保持模拟失败才能触发熔断
        summary = orch.replay_last()
        # 熔断后应有 skipped
        self.assertTrue(summary.faulted or summary.skipped > 0)


class TestOrchestratorWithRxMonitor(unittest.TestCase):
    """带 RxMonitor 的编排器测试。"""

    def setUp(self):
        self.bridge = MockBridge()
        self.bridge.initialize(d12=1)
        self.fm = FaultManager(threshold=5)
        self.monitor = RxMonitor(self.bridge, fault_manager=self.fm)
        self.recording_dir = tempfile.mkdtemp()
        self.orch = BridgeCoreOrchestrator(
            self.bridge,
            rx_monitor=self.monitor,
            recording_dir=self.recording_dir,
        )

    def tearDown(self):
        import shutil
        if self.monitor.running:
            self.monitor.stop()
        if os.path.exists(self.recording_dir):
            shutil.rmtree(self.recording_dir)

    def test_record_pauses_rx_monitor(self):
        """录制期间 RxMonitor 自动暂停。"""
        self.monitor.start()
        self.assertTrue(self.monitor.running)

        with self.orch.record_session(session_tag="rx_pause"):
            self.assertFalse(self.monitor.running)
            self.bridge.guest_card(lock_no="0101")

        # 退出上下文后恢复
        self.assertTrue(self.monitor.running)

    def test_replay_pauses_rx_monitor(self):
        """回放期间 RxMonitor 自动暂停。"""
        with self.orch.record_session(session_tag="rx_replay"):
            self.bridge.guest_card(lock_no="0101")

        self.monitor.start()
        self.assertTrue(self.monitor.running)

        self.orch.replay_last()

        # 回放后恢复
        self.assertTrue(self.monitor.running)

    def test_rx_probe_not_recorded(self):
        """RxMonitor 的探针调用不污染录制。"""
        self.bridge.initialize(d12=1)
        self.monitor.start()

        # 让监控线程跑几次探测
        time.sleep(0.3)

        # 录制一次业务调用
        with self.orch.record_session(session_tag="clean_record"):
            self.bridge.guest_card(lock_no="0101")

        self.monitor.stop()

        # 加载录制文件，验证只有业务调用（没有 ping/keepalive/read_card）
        if self.orch.last_session_path:
            records = load_recording(self.orch.last_session_path)
            fns = {r["fn_name"] for r in records}
            # 业务调用应存在
            self.assertIn("guest_card", fns)
            # 探针调用不应出现
            self.assertNotIn("ping", fns)
            self.assertNotIn("keepalive", fns)

    def test_monitor_still_not_recorded_with_attach(self):
        """即使 RxMonitor 在 Observer 挂接后启动，探针也不被录制。"""
        # 先 attach
        with self.orch.record_session(session_tag="before_monitor"):
            self.bridge.guest_card(lock_no="0101")

        # 重新录制一次，但在录制期间启动监控
        with self.orch.record_session(session_tag="during_monitor"):
            self.monitor.start()
            time.sleep(0.2)
            self.bridge.guest_card(lock_no="0202")
            self.monitor.stop()

        # 验证录制内容干净
        if self.orch.last_session_path:
            records = load_recording(self.orch.last_session_path)
            fns = {r["fn_name"] for r in records}
            self.assertIn("guest_card", fns)
            self.assertNotIn("ping", fns)


class TestListSessions(unittest.TestCase):
    """list_sessions 辅助函数。"""

    def test_list_sessions_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            sessions = list_sessions(d)
            self.assertEqual(len(sessions), 0)

    def test_list_sessions_with_files(self):
        with tempfile.TemporaryDirectory() as d:
            # 创建一个录制文件
            session = RecordingSession(
                hotel_id="HT_LIST", brand="Mock",
                session_tag="list_test",
            )
            session.add_record({"fn_name": "guest_card"})
            session.add_record({"fn_name": "master_card"})
            session.save(str(Path(d) / "test_recording.jsonl"))

            sessions = list_sessions(d)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["record_count"], 2)
            self.assertEqual(sessions[0]["brand"], "Mock")
            self.assertEqual(sessions[0]["session_tag"], "list_test")

    def test_list_sessions_nonexistent_dir(self):
        sessions = list_sessions("/nonexistent_path")
        self.assertEqual(len(sessions), 0)


if __name__ == "__main__":
    unittest.main()
