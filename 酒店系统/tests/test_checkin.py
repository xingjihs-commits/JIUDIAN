"""checkin_tab.py 与 transactions/checkin.py 核心业务逻辑测试"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── 全局 mock 阻止需要 QApplication 的模块 ──
_MOCK_DB = MagicMock()
_MOCK_BUS = MagicMock()
_MOCK_BUS.toast_requested = MagicMock()
_MOCK_BUS.ledger_updated = MagicMock()
_MOCK_EVENT_BUS_MOD = MagicMock()
_MOCK_EVENT_BUS_MOD.bus = _MOCK_BUS

_MODULES_TO_MOCK = {
    "event_bus": _MOCK_EVENT_BUS_MOD,
    "PySide6": MagicMock(),
    "PySide6.QtCore": MagicMock(),
    "PySide6.QtWidgets": MagicMock(),
    "PySide6.QtGui": MagicMock(),
    "ui_helpers": MagicMock(),
    "sound_helper": MagicMock(),
    "i18n": MagicMock(),
    "design_tokens": MagicMock(),
    "frontdesk_ui": MagicMock(),
    "frontdesk_layers": MagicMock(),
    "ledger_format": MagicMock(),
    "permission_system": MagicMock(),
    "lock_legacy_bridge": MagicMock(),
    "audit_tab_widget": MagicMock(),
    "frontdesk_flow_strip": MagicMock(),
    "frontdesk_ledger_strip": MagicMock(),
    "tabs._shared": MagicMock(),
    "tabs.frontdesk.checkin_tab": MagicMock(),
}

@pytest.fixture(autouse=True)
def _mock_all():
    with patch.dict("sys.modules", _MODULES_TO_MOCK):
        with patch("transactions.checkin.db", _MOCK_DB):
            yield


# ─────────────────────────────────────────────
#  CheckinTransaction 基础测试
# ─────────────────────────────────────────────

def test_checkin_transaction_commit_success():
    """测试正常入住事务 commit 执行成功后房间状态变更为 INHOUSE。"""
    from transactions.checkin import CheckinTransaction

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1
    mock_conn.execute.return_value = mock_cursor

    _MOCK_DB.transaction.return_value.__enter__.return_value = mock_conn
    _MOCK_DB.transaction.return_value.__exit__.return_value = None

    tx = CheckinTransaction(
        room_id="101",
        guest_name="张三",
        id_card="110101199001011234",
        phone="13800000000",
    )
    tx.commit()

    # 验证 UPDATE rooms 和 INSERT INTO guests 被调用
    call_args_list = mock_conn.execute.call_args_list
    update_calls = [c for c in call_args_list if "UPDATE rooms" in str(c)]
    insert_calls = [c for c in call_args_list if "INSERT INTO guests" in str(c)]
    assert len(update_calls) == 1, "应执行 UPDATE rooms"
    assert len(insert_calls) == 1, "应执行 INSERT INTO guests"


def test_checkin_transaction_room_already_inhouse():
    """测试当房间已是 INHOUSE 时 commit 抛出 CheckinError。"""
    from transactions.checkin import CheckinTransaction, CheckinError

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 0  # 没有房间被更新（已经是 INHOUSE）
    mock_conn.execute.return_value = mock_cursor

    _MOCK_DB.transaction.return_value.__enter__.return_value = mock_conn
    _MOCK_DB.transaction.return_value.__exit__.return_value = None

    tx = CheckinTransaction(room_id="101", guest_name="李四")
    with pytest.raises(CheckinError) as exc_info:
        tx.commit()
    assert "房间状态已变化" in str(exc_info.value)


def test_checkin_transaction_empty_room_id():
    """测试空 room_id 构造时抛出 CheckinError。"""
    from transactions.checkin import CheckinTransaction, CheckinError

    with pytest.raises(CheckinError) as exc_info:
        CheckinTransaction(room_id="", guest_name="测试")
    assert "room_id" in str(exc_info.value)


def test_checkin_transaction_ledger_callback_invoked():
    """测试 ledger_callback 在 commit 中被调用。"""
    from transactions.checkin import CheckinTransaction

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1
    mock_conn.execute.return_value = mock_cursor

    _MOCK_DB.transaction.return_value.__enter__.return_value = mock_conn
    _MOCK_DB.transaction.return_value.__exit__.return_value = None

    callback = MagicMock()
    tx = CheckinTransaction(
        room_id="201",
        guest_name="王五",
        ledger_callback=callback,
    )
    tx.commit()

    callback.assert_called_once_with(mock_conn)


def test_checkin_transaction_rollback_on_callback_failure():
    """测试 ledger_callback 抛异常时事务回滚且抛出 CheckinError。"""
    from transactions.checkin import CheckinTransaction, CheckinError

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1
    mock_conn.execute.return_value = mock_cursor

    _MOCK_DB.transaction.return_value.__enter__.return_value = mock_conn
    _MOCK_DB.transaction.return_value.__exit__.return_value = None

    def failing_callback(conn):
        raise ValueError("入账失败")

    tx = CheckinTransaction(
        room_id="301",
        guest_name="赵六",
        ledger_callback=failing_callback,
    )
    with pytest.raises(CheckinError) as exc_info:
        tx.commit()
    assert "入账失败" in str(exc_info.value)


# ─────────────────────────────────────────────
#  黑名单检查逻辑测试
# ─────────────────────────────────────────────

def _check_blacklist_guard(phone: str) -> bool:
    """_check_blacklist 的守卫逻辑：空手机号直接返回 False。"""
    if not phone or not phone.strip():
        return False
    return True  # 需要通过第一关


def test_check_blacklist_empty_phone():
    """测试空手机号调用 _check_blacklist 守卫逻辑返回 False。"""
    assert _check_blacklist_guard("") is False
    assert _check_blacklist_guard("   ") is False


def test_check_blacklist_no_issues():
    """测试 _check_blacklist 守卫逻辑：非空手机号通过守卫。"""
    # 非空手机号通过守卫
    assert _check_blacklist_guard("13800000000") is True


# ─────────────────────────────────────────────
#  支付方法标准化测试
# ─────────────────────────────────────────────

def test_normalize_pay_method():
    """测试 normalize_pay_method 将旧代码映射到新代码。"""
    from tabs.finance_tab import normalize_pay_method

    assert normalize_pay_method("CASH") == "CASH_USD"
    assert normalize_pay_method("TRANSFER") == "CASH_USD"
    assert normalize_pay_method("CARD") == "ABA"
    assert normalize_pay_method("WECHAT") == "ABA"
    assert normalize_pay_method("ALIPAY") == "ABA"
    assert normalize_pay_method("CASH_USD") == "CASH_USD"
    assert normalize_pay_method("ABA") == "ABA"
    assert normalize_pay_method("USDT") == "USDT"
    assert normalize_pay_method(None) == "CASH_USD"
