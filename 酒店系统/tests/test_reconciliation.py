"""reconciliation_service / reconciliation_checks 轻量测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_event_bus():
    mock_bus = MagicMock()
    mock_bus.ledger_updated = MagicMock()
    mock_event_bus_mod = MagicMock()
    mock_event_bus_mod.bus = mock_bus
    with patch.dict("sys.modules", {"event_bus": mock_event_bus_mod}):
        yield


def test_daily_reconcile_returns_expected_keys():
    """daily_reconcile 返回固定结构字段。"""
    with patch("reconciliation_service.db") as mock_db:
        mock_db.execute.side_effect = [
            MagicMock(fetchone=lambda: (350.0,)),  # ledger_total
            MagicMock(fetchone=lambda: (200.0,)),  # room_revenue_total
            MagicMock(fetchall=lambda: []),  # bad_pay_rows
            MagicMock(fetchall=lambda: []),  # room_mix_rows
            MagicMock(fetchone=lambda: None),  # audit_row
        ]

        from reconciliation_service import daily_reconcile

        result = daily_reconcile("2026-06-20")

    assert set(result.keys()) == {"date", "ledger_total", "room_revenue_total", "diff", "mismatches"}
    assert result["date"] == "2026-06-20"
    assert result["ledger_total"] == 350.0
    assert result["room_revenue_total"] == 200.0
    assert result["diff"] == 150.0
    assert result["mismatches"] == []


def test_daily_reconcile_collects_mismatches():
    """daily_reconcile 汇总支付方式缺失等异常。"""
    with patch("reconciliation_service.db") as mock_db:
        mock_db.execute.side_effect = [
            MagicMock(fetchone=lambda: (100.0,)),
            MagicMock(fetchone=lambda: (100.0,)),
            MagicMock(
                fetchall=lambda: [(1, "101", "ROOM_IN", 100.0, "")]
            ),
            MagicMock(fetchall=lambda: []),
            MagicMock(fetchone=lambda: None),
        ]

        from reconciliation_service import daily_reconcile

        result = daily_reconcile("2026-06-20")

    assert len(result["mismatches"]) == 1
    assert result["mismatches"][0]["kind"] == "missing_pay_method"
    assert result["mismatches"][0]["ledger_id"] == 1


def test_check_ledger_payment_mismatch_callable():
    """check_ledger_payment_mismatch 可调用并返回三元组。"""
    with patch("reconciliation_checks.db") as mock_db:
        mock_db.execute.side_effect = [
            MagicMock(fetchall=lambda: []),
            MagicMock(fetchall=lambda: []),
        ]

        from reconciliation_checks import ALL_CHECKS, check_ledger_payment_mismatch

        ok, count, detail = check_ledger_payment_mismatch()

    assert ok is True
    assert count == 0
    assert isinstance(detail, str)

    registered = next(c for c in ALL_CHECKS if c.key == "ledger_payment_mismatch")
    assert registered.fn is check_ledger_payment_mismatch
    assert registered.severity == "yellow"


def test_check_ledger_payment_mismatch_detects_missing_pay_method():
    """缺少支付方式时 check 返回失败。"""
    with patch("reconciliation_checks.db") as mock_db:
        mock_db.execute.return_value.fetchall.return_value = [
            (9, "102", "DEPOSIT_IN", 50.0),
        ]

        from reconciliation_checks import check_ledger_payment_mismatch

        ok, count, detail = check_ledger_payment_mismatch()

    assert ok is False
    assert count == 1
    assert "#9" in detail
