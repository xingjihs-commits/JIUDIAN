"""core/ledger.py — LedgerHashChain + LedgerService 测试"""
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
        yield ShadowDatabase("test.db")

def test_hash_chain_genesis():
    from core.ledger import LedgerHashChain
    h = LedgerHashChain.compute_current("GENESIS", "TX001", "ROOM_IN", 100.0, "USD", "CASH", 0, "op1", "101", 1.0)
    assert isinstance(h, str) and len(h) == 64

def test_hash_chain_deterministic():
    from core.ledger import LedgerHashChain
    h1 = LedgerHashChain.compute_current("GENESIS", "TX001", "ROOM_IN", 100.0, "USD", "CASH", 0, "op1", "101", 1.0)
    h2 = LedgerHashChain.compute_current("GENESIS", "TX001", "ROOM_IN", 100.0, "USD", "CASH", 0, "op1", "101", 1.0)
    assert h1 == h2

def test_hash_chain_different_input():
    from core.ledger import LedgerHashChain
    h1 = LedgerHashChain.compute_current("GENESIS", "TX001", "ROOM_IN", 100.0, "USD", "CASH", 0, "op1", "101", 1.0)
    h2 = LedgerHashChain.compute_current("GENESIS", "TX001", "ROOM_IN", 200.0, "USD", "CASH", 0, "op1", "101", 1.0)
    assert h1 != h2

def test_hash_chain_detects_tampering(test_db):
    from core.ledger import LedgerHashChain
    test_db.append_ledger("ROOM_IN", 100.0, "USD", room_id="101", emit_event=False)
    test_db.conn.execute("UPDATE ledger SET amount=9999 WHERE id=1")
    test_db.conn.commit()
    ok, msg = LedgerHashChain.verify_chain(test_db)
    assert ok is False

def test_ledger_service_append(test_db):
    from core.ledger import LedgerService
    svc = LedgerService(test_db)
    tx_id = svc.append("ROOM_IN", 150.0, room_id="101", emit_event=False)
    assert tx_id is not None
    row = test_db.execute("SELECT amount FROM ledger WHERE tx_id=?", (tx_id,)).fetchone()
    assert float(row[0]) == 150.0

def test_ledger_service_verify_integrity(test_db):
    from core.ledger import LedgerService
    svc = LedgerService(test_db)
    svc.append("ROOM_IN", 50.0, room_id="101", emit_event=False)
    ok, msg = svc.verify_integrity()
    assert ok is True
