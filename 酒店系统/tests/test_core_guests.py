"""core/guests.py — GuestService 单元测试"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

_MOCK_BUS = MagicMock()
_MOCK_BUS.guest_checkin = MagicMock(); _MOCK_BUS.guest_checkin.emit = MagicMock()
_MOCK_BUS.guest_checkout = MagicMock(); _MOCK_BUS.guest_checkout.emit = MagicMock()
_MOCK_BUS.ledger_updated = MagicMock(); _MOCK_BUS.ledger_updated.emit = MagicMock()
_MOCK_EVENT_BUS_MOD = MagicMock(); _MOCK_EVENT_BUS_MOD.bus = _MOCK_BUS

@pytest.fixture(autouse=True)
def _mock_event_bus():
    with patch.dict("sys.modules", {"event_bus": _MOCK_EVENT_BUS_MOD}):
        yield

@pytest.fixture
def test_db():
    tmpdir = tempfile.mkdtemp()
    with patch("database._get_app_dir", return_value=Path(tmpdir)):
        from database import ShadowDatabase
        db = ShadowDatabase("test.db")
        for rid in ("101", "202", "303", "404", "505", "606", "707", "808", "909"):
            db.execute("INSERT OR IGNORE INTO rooms (room_id, status) VALUES (?, 'READY')", (rid,))
        yield db

def test_instantiate(test_db):
    from core.guests import GuestService
    assert GuestService(test_db) is not None

def test_checkin_success(test_db):
    from core.guests import GuestService
    svc = GuestService(test_db)
    ok, msg, gid = svc.checkin("101", "测试客人", price=200.0, deposit=300.0)
    assert ok is True, f"入住失败: {msg}"
    assert gid is not None and gid > 0

def test_checkin_updates_room_status(test_db):
    from core.guests import GuestService
    svc = GuestService(test_db)
    svc.checkin("202", "客人2")
    room = test_db.execute("SELECT status FROM rooms WHERE room_id=?", ("202",)).fetchone()
    assert room is not None and room[0] == "INHOUSE"

def test_checkin_rejects_occupied(test_db):
    from core.guests import GuestService
    svc = GuestService(test_db)
    svc.checkin("303", "A")
    ok, msg, gid = svc.checkin("303", "B")
    assert ok is False

def test_add_deposit(test_db):
    from core.guests import GuestService
    svc = GuestService(test_db)
    ok, msg, gid = svc.checkin("606", "押金测试", deposit=100.0)
    assert ok is True
    ok, msg = svc.add_deposit(gid, 50.0)
    assert ok is True
    row = test_db.execute("SELECT deposit FROM guests WHERE id=?", (gid,)).fetchone()
    assert float(row[0]) == 150.0

def test_get_guest_by_room(test_db):
    from core.guests import GuestService
    svc = GuestService(test_db)
    svc.checkin("909", "房号查询")
    guest = svc.get_guest_by_room("909")
    assert guest is not None
    # sqlite3.Row 支持下标访问
    assert guest["name"] == "房号查询" or guest.get("name") == "房号查询"
