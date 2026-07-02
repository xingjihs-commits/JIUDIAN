"""Phase1 集成测试 — 对账/退款/库存事件"""
from __future__ import annotations


class TestPhase1Integration:
    def test_reconciliation_daily(self, monkeypatch):
        """对账服务返回标准结构"""
        from reconciliation_service import daily_reconcile
        result = daily_reconcile("2026-06-20")
        assert "ledger_total" in result
        assert isinstance(result.get("mismatches"), list)

    def test_refund_flow(self, monkeypatch):
        """退款申请产生 PENDING 记录"""
        from transactions.refund import RefundTransaction
        from database import db
        rid = RefundTransaction.request_refund("101", None, 50.0, "测试", "1")
        row = db.execute("SELECT status FROM refunds WHERE refund_id=?", (rid,)).fetchone()
        assert row and row[0] == "PENDING"

    def test_inventory_deduct_event(self):
        """库存扣减事件正确触发"""
        from event_bus import bus
        from database import db
        fired = []
        bus.inventory_deduct.connect(lambda e: fired.append(e))
        from services.payment_complete import complete_payment
        complete_payment({"room_id": "101", "items": [{"product_id": "SKU001", "quantity": 1}]})
        assert len(fired) >= 1
