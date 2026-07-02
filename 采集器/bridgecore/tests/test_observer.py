"""
bridgecore/tests/test_observer.py — Observer 单元测试

覆盖：
- 会话管理（创建、记录、保存、加载）
- _call 回调挂接/解除
- 录制内容校验（入参、出参、payload）
- 多方法录制
- 异常隔离
- 并发安全
- 空会话处理
- 加载非标准 JSONL
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

from bridgecore.observer import Observer, RecordingSession, load_recording
from bridgecore.tests import MockBridge


class TestRecordingSession(unittest.TestCase):
    """录制会话：创建/记录/保存/加载/边界。"""

    def test_create_default(self):
        session = RecordingSession()
        self.assertTrue(session.session_id)
        self.assertEqual(session.record_count, 0)
        self.assertEqual(len(session.records), 0)

    def test_create_with_metadata(self):
        session = RecordingSession(
            hotel_id="HT_TEST", brand="proUSB V9",
            dll_version="2.1", dll_path="D:\\v9.dll",
            session_tag="test_session",
        )
        self.assertEqual(session.hotel_id, "HT_TEST")
        self.assertEqual(session.brand, "proUSB V9")
        self.assertEqual(session.dll_version, "2.1")
        self.assertEqual(session.dll_path, "D:\\v9.dll")
        self.assertEqual(session.session_tag, "test_session")

    def test_add_record_increases_seq(self):
        session = RecordingSession()
        session.add_record({"fn_name": "guest_card"})
        self.assertEqual(session.records[0]["session_seq"], 1)
        session.add_record({"fn_name": "master_card"})
        self.assertEqual(session.records[1]["session_seq"], 2)

    def test_thread_safety(self):
        """并发记录不丢失。"""
        session = RecordingSession()
        errors = []

        def writer(n: int):
            try:
                for i in range(100):
                    session.add_record({"fn_name": "test", "n": n, "i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(session.record_count, 500)

    def test_save_and_load(self):
        session = RecordingSession(
            hotel_id="HT_SAVE", brand="Mock", session_tag="save_load",
        )
        session.add_record({"fn_name": "guest_card", "ok": True, "payload_hex": "AABB"})
        session.add_record({"fn_name": "master_card", "ok": True})

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = f.name

        try:
            saved = session.save(tmp)
            self.assertTrue(os.path.exists(saved))

            loaded = RecordingSession.load(tmp)
            self.assertEqual(loaded.hotel_id, "HT_SAVE")
            self.assertEqual(loaded.record_count, 2)
            self.assertEqual(loaded.records[0]["fn_name"], "guest_card")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_save_to_nonexistent_dir(self):
        """保存到不存在的目录应自动创建。"""
        session = RecordingSession()
        session.add_record({"fn_name": "test"})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "test.jsonl"
            saved = session.save(str(path))
            self.assertTrue(os.path.exists(saved))
            loaded = RecordingSession.load(str(path))
            self.assertEqual(loaded.record_count, 1)

    def test_load_empty_jsonl(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            f.write(b"\n")
            tmp = f.name
        try:
            session = RecordingSession.load(tmp)
            self.assertEqual(session.record_count, 0)
        finally:
            os.unlink(tmp)

    def test_load_malformed_jsonl(self):
        """坏行应被跳过。"""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            f.write('{"_type":"session_header","session_id":"test"}\n')
            f.write('{"fn_name":"ok"}\n')
            f.write('NOT JSON\n')
            f.write('{"fn_name":"after_bad"}\n')
            tmp = f.name
        try:
            session = RecordingSession.load(tmp)
            self.assertEqual(session.record_count, 2)
        finally:
            os.unlink(tmp)


class TestObserver(unittest.TestCase):
    """Observer 挂接/录制/解除。"""

    def setUp(self):
        self.bridge = MockBridge()
        self.observer = Observer()

    def tearDown(self):
        if self.observer.attached:
            self.observer.detach()

    def test_attach_detach(self):
        self.assertFalse(self.observer.attached)
        self.observer.attach(self.bridge, auto_session=True)
        self.assertTrue(self.observer.attached)
        self.assertIsNotNone(self.observer.session)

        session = self.observer.detach()
        self.assertFalse(self.observer.attached)
        self.assertIsNotNone(session)

    def test_attach_twice(self):
        """重复 attach 不应报错。"""
        self.observer.attach(self.bridge)
        self.observer.attach(self.bridge)
        self.assertTrue(self.observer.attached)
        self.observer.detach()

    def test_attach_none_bridge(self):
        self.observer.attach(None)
        self.assertFalse(self.observer.attached)

    def test_records_guest_card(self):
        self.observer.attach(self.bridge, auto_session=True)
        self.bridge.initialize(d12=1)
        result = self.bridge.guest_card(lock_no="0101")
        self.assertTrue(result.get("ok"))

        session = self.observer.get_session()
        self.assertGreater(session.record_count, 0)

        # 检查录制了 guest_card
        fns = [r["fn_name"] for r in session.records]
        self.assertIn("guest_card", fns)

        # 验证录制内容结构
        guest_recs = [r for r in session.records if r["fn_name"] == "guest_card"]
        self.assertGreater(len(guest_recs), 0)
        rec = guest_recs[0]
        self.assertIn("args_in", rec)
        self.assertIn("lock_no", rec["args_in"])
        self.assertEqual(rec["args_in"]["lock_no"], "0101")

    def test_records_multiple_methods(self):
        self.observer.attach(self.bridge, auto_session=True)
        self.bridge.guest_card(lock_no="0101")
        self.bridge.master_card()
        self.bridge.buzzer(ms=100)

        session = self.observer.get_session()
        fns = {r["fn_name"] for r in session.records}
        self.assertIn("guest_card", fns)
        self.assertIn("master_card", fns)
        self.assertIn("buzzer", fns)

    def test_error_isolation(self):
        """录制异常不阻断调用流程。"""
        self.observer.attach(self.bridge, auto_session=True)
        # 触发一个异常
        self.bridge._simulate_crash_on = "initialize"
        with self.assertRaises(RuntimeError):
            self.bridge.initialize(d12=1)
        self.bridge._simulate_crash_on = None

        # 后续调用仍然正常录制
        self.bridge.guest_card(lock_no="0101")
        self.assertGreater(self.observer.get_session().record_count, 0)

    def test_detach_removes_hooks(self):
        self.observer.attach(self.bridge)
        self.assertEqual(len(self.bridge._call_pre_hooks), 1)
        self.assertEqual(len(self.bridge._call_post_hooks), 1)

        self.observer.detach()
        self.assertEqual(len(self.bridge._call_pre_hooks), 0)
        self.assertEqual(len(self.bridge._call_post_hooks), 0)

    def test_save_recording(self):
        self.observer.attach(self.bridge, auto_session=True)
        self.bridge.guest_card(lock_no="0101")

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = f.name

        try:
            saved = self.observer.save(tmp)
            self.assertTrue(os.path.exists(saved))

            records = load_recording(tmp)
            self.assertGreater(len(records), 0)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_get_session_before_attach(self):
        """未 attach 时 get_session 应报错。"""
        with self.assertRaises(RuntimeError):
            self.observer.get_session()

    def test_get_records_empty(self):
        self.assertEqual(self.observer.get_records(), [])

    def test_get_records_after_recording(self):
        self.observer.attach(self.bridge, auto_session=True)
        self.bridge.guest_card(lock_no="0101")
        records = self.observer.get_records()
        self.assertGreater(len(records), 0)

    def test_multiple_sessions(self):
        """多次 attach/detach 不影响。"""
        for i in range(3):
            self.observer.attach(self.bridge, auto_session=True)
            self.bridge.guest_card(lock_no=f"0{i+1}01")
            session = self.observer.detach()
            self.assertIsNotNone(session)
            self.assertGreater(session.record_count, 0)

    def test_concurrent_attach(self):
        """并发 attach/detach 安全。"""
        errors = []

        def worker():
            for _ in range(10):
                try:
                    observer = Observer()
                    observer.attach(self.bridge, auto_session=True)
                    self.bridge.guest_card(lock_no="0101")
                    observer.detach()
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)

    def test_new_session_replaces_old(self):
        self.observer.attach(self.bridge, auto_session=True)
        self.bridge.guest_card(lock_no="0101")

        # 新建会话应丢弃旧数据
        self.observer.new_session(session_tag="fresh")
        self.assertEqual(self.observer.get_session().record_count, 0)

    def test_observe_hooks_only_one_detach(self):
        """多次 attach/detach 不报错。"""
        o1 = Observer()
        o2 = Observer()

        o1.attach(self.bridge)
        self.assertTrue(o1.attached)
        # 第二个 Observer attach 前应确保先 detach 第一个
        o1.detach()
        o2.attach(self.bridge)
        self.assertTrue(o2.attached)
        self.assertFalse(o1.attached)
        o2.detach()


class TestLoadRecording(unittest.TestCase):
    """load_recording 辅助函数。"""

    def test_load_recording_skips_header(self):
        session = RecordingSession(session_tag="test")
        session.add_record({"fn_name": "guest_card"})
        session.add_record({"fn_name": "master_card"})

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = f.name

        try:
            session.save(tmp)
            records = load_recording(tmp)
            self.assertEqual(len(records), 2)
            # 不应包含 _type=session_header
            types = {r.get("_type") for r in records}
            self.assertNotIn("session_header", types)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_load_recording_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_recording("/nonexistent/path.jsonl")


if __name__ == "__main__":
    unittest.main()
