"""permission_system.py 核心功能测试"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── 全局 mock 阻止重型依赖 ──
_MOCK_DB = MagicMock()
_MOCK_DB.get_config.return_value = None  # 避免 get_role_permissions 内 json.loads 报错
_MOCK_BUS = MagicMock()
_MOCK_BUS.user_logged_in = MagicMock()
_MOCK_EVENT_BUS_MOD = MagicMock()
_MOCK_EVENT_BUS_MOD.bus = _MOCK_BUS

_MODULES_TO_MOCK = {
    "event_bus": _MOCK_EVENT_BUS_MOD,
    "PySide6": MagicMock(),
    "PySide6.QtCore": MagicMock(),
    "PySide6.QtWidgets": MagicMock(),
    "PySide6.QtGui": MagicMock(),
    "crypto_utils": MagicMock(),          # 阻止 cryptography（PyO3 多进程初始化问题）
    "ui_helpers": MagicMock(),
    "sound_helper": MagicMock(),
    "i18n": MagicMock(),
}


@pytest.fixture(autouse=True)
def _mock_all():
    with patch.dict("sys.modules", _MODULES_TO_MOCK):
        # permission_system 的 from crypto_utils import encrypt, decrypt → 走 mock
        with patch("permission_system.db", _MOCK_DB):
            yield


def _get_perm_manager():
    """在 mock 环境中动态导入 PermissionManager。"""
    from permission_system import PermissionManager, ROLES, get_role_permissions, is_vendor_account, VENDOR_USERNAME, _hash_password
    return PermissionManager, ROLES, get_role_permissions, is_vendor_account, VENDOR_USERNAME, _hash_password


# ─────────────────────────────────────────────
#  1. Boss 拥有全部权限
# ─────────────────────────────────────────────
def test_has_permission_boss_returns_true():
    """测试 boss（role 含 *）调用 has_permission 对任意 perm 返回 True。"""
    PermissionManager, ROLES, _, _, _, _ = _get_perm_manager()
    PermissionManager._current_user = None
    with patch.object(PermissionManager, "current_role", return_value="boss"):
        assert PermissionManager.has_permission("view_dashboard") is True
        assert PermissionManager.has_permission("debug_panel") is True
        assert PermissionManager.has_permission("nonexistent_perm") is True


# ─────────────────────────────────────────────
#  2. 前台权限有限
# ─────────────────────────────────────────────
def test_has_permission_frontdesk_limited():
    """测试 frontdesk 有 checkin 但没有 debug_panel 权限。"""
    PermissionManager, ROLES, get_role_permissions, _, _, _ = _get_perm_manager()
    PermissionManager._current_user = None
    with patch.object(PermissionManager, "current_role", return_value="frontdesk"):
        assert PermissionManager.has_permission("checkin") is True
        assert PermissionManager.has_permission("view_rooms") is True
        assert PermissionManager.has_permission("debug_panel") is False
        assert PermissionManager.has_permission("manage_staff") is False
        assert PermissionManager.has_permission("view_reports") is False


# ─────────────────────────────────────────────
#  3. 登录成功
# ─────────────────────────────────────────────
def test_login_success():
    """测试 PermissionManager.login 在正确凭据下返回 (True, msg)。"""
    PermissionManager, _, _, _, VENDOR_USERNAME, _hash_password = _get_perm_manager()

    real_hash = _hash_password("correct_password")
    _MOCK_DB.execute.return_value.fetchone.return_value = (
        1, VENDOR_USERNAME, real_hash, "管理员", "boss", 1,
    )

    ok, msg = PermissionManager.login(VENDOR_USERNAME, "correct_password")
    assert ok is True
    assert "欢迎" in msg


# ─────────────────────────────────────────────
#  4. 密码错误登录失败
# ─────────────────────────────────────────────
def test_login_failure_wrong_password():
    """测试 PermissionManager.login 在错误密码时返回 (False, 密码错误)。"""
    PermissionManager, _, _, _, VENDOR_USERNAME, _hash_password = _get_perm_manager()

    real_hash = _hash_password("correct_password")
    _MOCK_DB.execute.return_value.fetchone.return_value = (
        1, VENDOR_USERNAME, real_hash, "管理员", "boss", 1,
    )

    ok, msg = PermissionManager.login(VENDOR_USERNAME, "wrong_password")
    assert ok is False
    assert "密码" in msg


# ─────────────────────────────────────────────
#  5. 已停用账号登录失败
# ─────────────────────────────────────────────
def test_login_failure_disabled_account():
    """测试 is_active=0 的账号登录时返回 (False, 账号已被停用)。"""
    PermissionManager, _, _, _, _, _ = _get_perm_manager()

    _MOCK_DB.execute.return_value.fetchone.return_value = (
        2, "disabled_user", "irrelevant_hash", "被停用", "frontdesk", 0,
    )

    ok, msg = PermissionManager.login("disabled_user", "anything")
    assert ok is False
    assert "停用" in msg


# ─────────────────────────────────────────────
#  6. 厂家账号识别
# ─────────────────────────────────────────────
def test_vendor_account_check():
    """测试 is_vendor_account("admin") 返回 True。"""
    _, _, _, is_vendor_account, VENDOR_USERNAME, _ = _get_perm_manager()

    assert is_vendor_account(VENDOR_USERNAME) is True
    assert is_vendor_account("") is False
    assert is_vendor_account("前台") is False
