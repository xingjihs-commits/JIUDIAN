"""services/checkout_service.py — CheckoutService 测试"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

_MOCK_BUS = MagicMock()
_MOCK_BUS.ledger_updated = MagicMock(); _MOCK_BUS.ledger_updated.emit = MagicMock()
_MOCK_BUS.guest_checkin = MagicMock(); _MOCK_BUS.guest_checkin.emit = MagicMock()
_MOCK_BUS.guest_checkout = MagicMock(); _MOCK_BUS.guest_checkout.emit = MagicMock()
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
        for rid in ("101", "202", "303", "404", "505"):
            db.execute("INSERT OR IGNORE INTO rooms (room_id, status) VALUES (?, 'READY')", (rid,))
        yield db

def test_instantiate(test_db):
    from services.checkout_service import CheckoutService
    assert CheckoutService(test_db) is not None

def test_full_checkout_flow(test_db):
    from core.guests import GuestService
    from services.checkout_service import CheckoutService
    gs = GuestService(test_db)
    cs = CheckoutService(test_db)
    ok, msg, gid = gs.checkin("101", "结账测试", price=200.0, deposit=300.0)
    assert ok is True, f"入住失败: {msg}"
    ok, msg = cs.execute(gid, payment_method="CASH")
    assert ok is True, f"结账失败: {msg}"

def test_checkout_nonexistent(test_db):
    from services.checkout_service import CheckoutService
    cs = CheckoutService(test_db)
    ok, msg = cs.execute(99999)
    assert ok is False

def test_checkout_creates_hk_task(test_db):
    from core.guests import GuestService
    from services.checkout_service import CheckoutService
    gs = GuestService(test_db)
    cs = CheckoutService(test_db)
    ok, msg, gid = gs.checkin("303", "保洁测试", price=100.0)
    assert ok is True
    ok, msg = cs.execute(gid)
    assert ok is True
    tasks = test_db.execute("SELECT room_id FROM housekeeping_tasks WHERE room_id=?", ("303",)).fetchall()
    assert len(tasks) >= 1
