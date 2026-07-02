"""database.py 核心功能测试"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── 全局 mock：阻止 event_bus 真实导入（避免 QObject 需要 QApplication）──
_MOCK_BUS = MagicMock()
_MOCK_BUS.ledger_updated = MagicMock()
_MOCK_BUS.ledger_updated.emit = MagicMock()
_MOCK_EVENT_BUS_MOD = MagicMock()
_MOCK_EVENT_BUS_MOD.bus = _MOCK_BUS


@pytest.fixture(autouse=True)
def _mock_event_bus():
    """自动阻止真实 event_bus 模块导入，避免 QApplication 依赖。"""
    with patch.dict("sys.modules", {"event_bus": _MOCK_EVENT_BUS_MOD}):
        yield


@pytest.fixture
def test_db():
    """Create a temporary database for testing."""
    tmpdir = tempfile.mkdtemp()
    with patch("database._get_app_dir", return_value=Path(tmpdir)):
        from database import ShadowDatabase

        db = ShadowDatabase("test.db")
        yield db


def test_ledger_hash_chain(test_db):
    """测试 append_ledger 写入后哈希链连续且可被 verify_ledger_integrity 验证通过。"""
    import time
    db = test_db
    db.append_ledger("ROOM_IN", 100.0, "USD", room_id="101", note="chain1", emit_event=False, tx_id_override="CHAIN1")
    time.sleep(0.02)
    db.append_ledger("SHOP", 50.0, "USD", room_id="101", note="chain2", emit_event=False, tx_id_override="CHAIN2")
    time.sleep(0.02)
    db.append_ledger("TIP", 20.0, "USD", room_id="102", note="chain3", emit_event=False, tx_id_override="CHAIN3")
    db.conn.commit()
    ok, msg = db.verify_ledger_integrity()
    assert ok is True, f"哈希链验证失败: {msg}"


def test_ledger_integrity_detects_tampering(test_db):
    """测试 verify_ledger_integrity 能检测出单条记录字段被篡改的情况。"""
    db = test_db
    db.append_ledger("ROOM_IN", 100.0, "USD", room_id="101", emit_event=False)
    # 手动篡改第一条记录的金额（不更新哈希）
    db.conn.execute("UPDATE ledger SET amount=9999 WHERE id=1")
    db.conn.commit()
    ok, msg = db.verify_ledger_integrity()
    assert ok is False
    assert "篡改" in msg or "哈希" in msg


def test_get_config_returns_none_for_missing_key(test_db):
    """测试 get_config() 对不存在的 key 返回 None。"""
    db = test_db
    val = db.get_config("__nonexistent__")
    assert val is None


def test_set_config_and_get_config_roundtrip(test_db):
    """测试 set_config() 写入后 get_config() 能正确读取。"""
    db = test_db
    db.set_config("test_roundtrip", "hello_world")
    val = db.get_config("test_roundtrip")
    assert val == "hello_world"


def test_get_shift_start_time_returns_valid_iso(test_db):
    """测试 get_shift_start_time() 返回有效时间字符串或今日零点。"""
    db = test_db
    t = db.get_shift_start_time()
    assert t is not None
    assert len(t) >= 16  # YYYY-MM-DD HH:MM:SS
    assert ":" in t  # 包含时间部分


def test_get_overview_by_range_returns_expected_keys(test_db):
    """测试 get_overview_by_range() 返回的字典包含 revenue/deposit_net/occupancy 等键。"""
    db = test_db
    ov = db.get_overview_by_range("2000-01-01", "2099-12-31")
    expected_keys = {
        "revenue", "deposit_net", "occupancy", "inhouse_count",
        "ready_count", "total_rooms", "room_status_counts",
        "by_type", "by_pay", "revpar", "energy_anomaly_count",
    }
    for key in expected_keys:
        assert key in ov, f"返回字典缺少键: {key}"


def test_transaction_commits_on_success(test_db):
    """测试 transaction() 上下文内 execute() 修改在正常退出后持久化。"""
    db = test_db
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)",
            ("tx_commit_test", "persisted"),
        )
    val = db.get_config("tx_commit_test")
    assert val == "persisted"


def test_transaction_rollback_on_exception(test_db):
    """测试 transaction() 上下文内抛异常时修改被回滚。"""
    db = test_db
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)",
                ("tx_rollback_test", "should_not_exist"),
            )
            raise RuntimeError("force rollback")
    val = db.get_config("tx_rollback_test")
    assert val is None  # 应该被回滚


def test_run_transaction_returns_fn_result(test_db):
    """测试 run_transaction() 返回传入函数的结果。"""
    db = test_db
    result = db.run_transaction(lambda conn: "result_from_fn")
    assert result == "result_from_fn"


def test_append_ledger_emits_event(test_db):
    """测试 append_ledger() 写入后触发 ledger_updated 总线事件。"""
    # 在 system_config 插入开关让 emit_event 走 mock bus
    db = test_db
    with patch("database.event_bus", _MOCK_EVENT_BUS_MOD, create=True):
        _MOCK_BUS.ledger_updated.emit.reset_mock()
        db.append_ledger(
            "ROOM_IN", 100, "USD",
            room_id="101", note="event_test", emit_event=True,
        )
        _MOCK_BUS.ledger_updated.emit.assert_called_once()
        call_args = _MOCK_BUS.ledger_updated.emit.call_args[0]
        assert call_args[0] == "ROOM_IN"
        assert call_args[1]["amount"] == 100
