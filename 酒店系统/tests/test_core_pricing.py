"""core/pricing.py — PricingService 单元测试"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

_MOCK_BUS = MagicMock()
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
        db.execute("INSERT OR REPLACE INTO room_type_templates (type_id, type_name, base_price, price_member) VALUES ('STD','标准间',150.0,120.0)")
        db.execute("INSERT OR REPLACE INTO room_type_templates (type_id, type_name, base_price, price_member) VALUES ('DLX','豪华间',300.0,240.0)")
        db.execute("INSERT OR IGNORE INTO rooms (room_id, type_id, status) VALUES ('101','STD','READY')")
        db.execute("INSERT OR IGNORE INTO rooms (room_id, type_id, status) VALUES ('201','DLX','READY')")
        yield db

def test_instantiate(test_db):
    from core.pricing import PricingService
    assert PricingService(test_db) is not None

def test_get_room_rate(test_db):
    from core.pricing import PricingService
    svc = PricingService(test_db)
    rate = svc.get_room_rate("101")
    assert rate == 150.0

def test_get_member_rate(test_db):
    from core.pricing import PricingService
    svc = PricingService(test_db)
    member_rate = svc.get_room_rate("201", tier="member")
    assert member_rate == 240.0

def test_get_total(test_db):
    from core.pricing import PricingService
    svc = PricingService(test_db)
    total = svc.get_total("101", nights=3)
    assert total == 450.0

def test_get_all_room_rates(test_db):
    from core.pricing import PricingService
    svc = PricingService(test_db)
    rates = svc.get_all_room_rates()
    assert len(rates) >= 1
