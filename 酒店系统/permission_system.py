"""
======================================================
ShadowGuard — 权限分级系统 (v1.0)
口号: 前台看房态，经理看报表，老板看全局

角色:
  - boss       管理员 — 全部权限
  - manager    店长 — 日常运营 + 管理权限
  - frontdesk  前台 — 日常运营（入住/退房/保洁）
  - finance    财务 — 财务流水、审计、报表
  - vendor     厂家 — 调试面板、熔断、安全扫描、迁移

权限控制:
  - 界面功能按钮显隐
  - 敏感操作拦截
  - 调试面板访问
======================================================
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import sys
import time
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QCheckBox,
    QScrollArea, QFrame,
)

from database import db
from event_bus import bus
from i18n import i18n
from brand_config_v4 import effective_brand
from ui_helpers import style_dialog, build_dialog_header, show_info, show_warning
from ui_surface import fd_apply_table_palette
from sound_helper import play_fail, play_warn
from crypto_utils import encrypt, decrypt
import logging
logger = logging.getLogger(__name__)


def _role_perms_config_key(role: str) -> str:
    return f"role_permissions_{role}"


def get_role_permissions(role: str) -> List[str]:
    """角色权限模板（数据库覆盖优先，否则 ROLES 内置）。"""
    raw = (db.get_config(_role_perms_config_key(role)) or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(p) for p in data]
        except json.JSONDecodeError:
            pass
    perms = ROLES.get(role, {}).get("permissions", [])
    if perms == "*":
        return list(PERMISSION_LABELS.keys())
    return list(perms)


def save_role_permissions(role: str, permissions: List[str]) -> None:
    db.set_config(_role_perms_config_key(role), json.dumps(permissions, ensure_ascii=False))


# ================================================================
# 角色定义 & 权限清单
# ================================================================
ROLES = {
    "boss": {
        "name": "管理员",
        "permissions": "*",  # 全部
    },
    "manager": {
        "name": "店长",
        "permissions": [
            "view_dashboard", "view_rooms", "view_guests",
            "checkin", "checkout", "room_change",
            "housekeeping", "energy_monitor",
            "shift_settle", "payout",
            "view_audit", "view_ledger", "security_scan",
            "view_reports", "export_data",
            "manage_room_types", "manage_shop",
            "manage_staff", "manage_glossary",
            "manage_pricing", "manage_custom_fields",
            "manage_holidays", "manage_group_rates", "select_contract_rate",
            "settings_view", "backup_restore",
            "batch_create", "import_data",
            "refund.approve",
        ],
    },
    "frontdesk": {
        "name": "前台",
        "permissions": [
            "view_rooms", "view_guests",
            "checkin", "checkout",
            "view_shop", "fulfill_order", "manage_shop",
            "manage_pricing", "settings_view",
            "shift_settle", "payout",
        ],
    },
    "finance": {
        "name": "财务",
        "permissions": [
            "view_dashboard",
            "view_ledger", "view_audit",
            "view_reports", "export_data",
            "shift_settle", "payout",
            "manage_pricing", "manage_group_rates",
            "settings_view",
        ],
    },
    "vendor": {
        "name": "厂家",
        "permissions": [
            "view_dashboard", "view_rooms", "view_guests",
            "debug_panel", "kill_switch",
            "view_audit", "security_scan",
            "settings_view", "settings_edit",
            "migration", "batch_create", "import_data",
            "backup_restore",
            "refund.approve",
        ],
    },
}

PERMISSION_LABELS = {
    "view_dashboard": "看仪表盘",
    "view_rooms": "看房态",
    "view_guests": "看客人列表",
    "checkin": "办理入住",
    "checkout": "办理退房",
    "room_change": "换房操作",
    "housekeeping": "保洁操作",
    "energy_monitor": "电表录入",
    "shift_settle": "交班对账",
    "payout": "资金支出",
    "refund.approve": "批准退款",
    "view_shop": "看商品",
    "fulfill_order": "处理订单",
    "view_audit": "看审计面板",
    "view_door_open_audit": "门卡与门禁事件轨迹（默认仅老板；经理等需在「单独权限」中勾选授予）",
    "view_ledger": "看财务流水",
    "security_scan": "安全扫描",
    "view_reports": "看报表",
    "export_data": "导出数据",
    "manage_room_types": "管理房型",
    "manage_shop": "管理商品",
    "manage_staff": "管理员工",
    "manage_glossary": "管理叫法",
    "manage_pricing": "管理定价",
    "manage_custom_fields": "管理自定义字段",
    "manage_holidays": "管理节假日定价",
    "manage_group_rates": "管理协议价",
    "select_contract_rate": "入住选用协议价档位",
    "settings_view": "查看设置",
    "settings_edit": "修改设置",
    "backup_restore": "备份恢复",
    "batch_create": "批量建房",
    "import_data": "数据导入",
    "migration": "老系统迁移",
    "debug_panel": "调试面板",
    "kill_switch": "熔断控制",
}


# ================================================================
# 密码哈希工具（PBKDF2 + 随机盐，格式: pbkdf2$<hex_salt>$<hex_hash>）
# ================================================================
def _hash_password(password: str) -> str:
    """使用 PBKDF2-HMAC-SHA256 + 随机盐生成密码哈希"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations=100_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """验证密码（兼容旧版单次 SHA-256 格式）"""
    if not password or not stored:
        return False
    if stored.startswith("pbkdf2$"):
        # 新格式：pbkdf2$<salt_hex>$<hash_hex>
        try:
            _, salt_hex, hash_hex = stored.split("$", 2)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations=100_000)
            return hmac.compare_digest(dk, expected)
        except Exception:
            return False
    else:
        # 旧格式：单次 SHA-256（兼容已有账号，登录成功后自动升级）
        input_hash = hashlib.sha256(password.encode()).hexdigest()
        return hmac.compare_digest(input_hash, stored)


# ================================================================
# 数据库初始化
# ================================================================
def init_permission_tables():
    """初始化权限相关表"""
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS staff_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT DEFAULT 'frontdesk',
                phone TEXT,
                employee_id TEXT,
                is_active INTEGER DEFAULT 1,
                last_login TIMESTAMP,
                last_pw_change TIMESTAMP,
                must_change_password INTEGER DEFAULT 0,  -- [sub-a] 强制改密 B7
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS permission_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                permission_key TEXT NOT NULL,
                granted INTEGER DEFAULT 1,
                UNIQUE(username, permission_key)
            )
        """)
        _ensure_vendor_account()
    except Exception as e:
        logger.warning("权限表初始化失败: %s", e)


# 厂家专属账号（出厂内置，拥有 debug_panel 权限做驻店与远程诊断）
VENDOR_USERNAME = "admin"
VENDOR_PASSWORD = "admin"
VENDOR_DISPLAY_NAME = "厂家工程师"
VENDOR_USERNAMES = {VENDOR_USERNAME}


def is_vendor_account(username: str) -> bool:
    return bool(username) and username in VENDOR_USERNAMES


def _ensure_vendor_account() -> None:
    """
    保证存在厂家账号 admin：
    每次都同步密码到 VENDOR_PASSWORD，确保密码与代码定义一致。

    [sub-a] 强制改密 B7：admin/admin 是出厂默认弱口令，每次同步密码后
    置 must_change_password=1，提示厂家工程师首次登录后立即改密。
    """
    pw_hash = _hash_password(VENDOR_PASSWORD)
    row = db.execute(
        "SELECT id FROM staff_accounts WHERE username=?", (VENDOR_USERNAME,)
    ).fetchone()

    if row:
        db.execute(
            "UPDATE staff_accounts SET role='boss', display_name=?, is_active=1, password_hash=?, "
            "must_change_password=1 WHERE id=?",
            (VENDOR_DISPLAY_NAME, pw_hash, row[0]),
        )
        return

    db.execute(
        "INSERT INTO staff_accounts (username, password_hash, display_name, role, is_active, must_change_password) "
        "VALUES (?, ?, ?, 'boss', 1, 1)",
        (VENDOR_USERNAME, pw_hash, VENDOR_DISPLAY_NAME),
    )

def warn_default_passwords() -> None:
    """启动时检测弱口令账号并 CRITICAL 告警（不阻断，仅提示）。

    修复记录(2026-06-22): 原 admin/admin 出厂即弱口令，且无告警。
    现启动时扫描所有使用默认密码的账号，CRITICAL 级别写日志，
    提示运维尽快通过设置向导改密。

    [sub-a] 强制改密 B7 增强：扫描到使用默认密码的账号时，
    同时 UPDATE staff_accounts.must_change_password=1，登录后由 UI 强制弹改密框。
    使用 try/except 兼容旧库（列可能尚未迁移完成）。
    """
    try:
        default_hashes = {
            _hash_password("admin"),
            _hash_password("1234"),
        }
        rows = db.execute(
            "SELECT username, display_name FROM staff_accounts WHERE is_active=1"
        ).fetchall()
        weak = []
        for username, display_name in rows:
            ph = db.execute(
                "SELECT password_hash FROM staff_accounts WHERE username=?", (username,)
            ).fetchone()
            if ph and ph[0] in default_hashes:
                weak.append(f"{username}({display_name})")
                # [sub-a] 标记强制改密；列不存在时忽略（旧库未迁移完）
                try:
                    db.execute(
                        "UPDATE staff_accounts SET must_change_password=1 WHERE username=?",
                        (username,),
                    )
                except Exception:
                    pass
        if weak:
            logger.critical(
                "⚠️ 安全告警：以下账号使用默认/弱口令，请立即通过设置向导改密：%s",
                ", ".join(weak),
            )
    except Exception as e:
        logger.debug("弱口令检测失败: %s", e)


def force_change_password(username: str, new_password: str) -> Tuple[bool, str]:
    """[sub-a] 强制改密 B7：用户首次登录或被标记 must_change_password=1 时调用。

    与 PermissionManager.reset_password 的区别：
      - reset_password：boss/manager 重置他人密码，需要 manage_staff 权限
      - force_change_password：当前用户改自己的密码，无需 manage_staff 权限，
        但要求调用方先验证旧密码（本函数只校验新密码强度）

    Args:
        username: 要改密的账号
        new_password: 新密码（≥4 位）

    Returns:
        (ok, msg) 元组
    """
    if not username or not username.strip():
        return False, "用户名不能为空"
    if len(new_password) < 4:
        return False, "密码至少4位"
    username = username.strip()
    # 禁止改成已知默认密码
    if new_password in ("admin", "1234"):
        return False, "新密码不能使用默认口令"

    row = db.execute(
        "SELECT id FROM staff_accounts WHERE username=? AND is_active=1",
        (username,)
    ).fetchone()
    if not row:
        return False, "账号不存在或已停用"

    pw_hash = _hash_password(new_password)
    try:
        db.execute(
            "UPDATE staff_accounts SET password_hash=?, last_pw_change=CURRENT_TIMESTAMP, "
            "must_change_password=0 WHERE id=?",
            (pw_hash, row[0])
        )
    except Exception as e:
        # 旧库无 must_change_password 列时回退到不含该列的 UPDATE
        try:
            db.execute(
                "UPDATE staff_accounts SET password_hash=?, last_pw_change=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (pw_hash, row[0])
            )
        except Exception as e2:
            return False, f"改密失败: {e2}"

    db.log_action(
        PermissionManager.current_user().get("username") if PermissionManager.current_user() else "SYSTEM",
        "FORCE_CHANGE_PASSWORD",
        f"target={username}",
    )

    # 同步刷新当前会话的标记（如果是自己改自己）
    cu = PermissionManager.current_user()
    if cu and cu.get("username") == username:
        cu["must_change_password"] = 0

    return True, "密码已修改"



# 各岗位测试账号：中文账号，密码统一 1234（仅演示/验收，上线请改密或删除）
DEMO_ROLE_ACCOUNTS = (
    ("管理员", "1234", "管理员", "boss"),
    ("店长", "1234", "店长", "manager"),
    ("前台", "1234", "前台", "frontdesk"),
    ("财务", "1234", "财务", "finance"),
)
# 旧版英文测试号（停用，避免与中文号并存混淆）
_LEGACY_DEMO_USERNAMES = ("boss", "manager", "frontdesk", "finance")


def seed_demo_role_accounts(*, reset_passwords: bool = False) -> None:
    """确保五种角色各有一个可登录测试号；已存在则默认不改密。

    一旦厂家通过向导设置过真实老板账号（vendor_seeded_boss_at 已记录），
    本函数变为 no-op，避免把演示号又复活回来。
    """
    init_permission_tables()

    try:
        if db.get_config("vendor_seeded_boss_at"):
            return
    except Exception:
        pass

    for legacy in _LEGACY_DEMO_USERNAMES:
        try:
            db.execute(
                "UPDATE staff_accounts SET is_active=0 WHERE username=?",
                (legacy,),
            )
        except Exception:
            pass

    for username, password, display_name, role in DEMO_ROLE_ACCOUNTS:
        if role not in ROLES:
            continue
        row = db.execute(
            "SELECT id FROM staff_accounts WHERE username=?", (username,)
        ).fetchone()
        pw_hash = _hash_password(password)
        if row:
            if reset_passwords:
                db.execute(
                    "UPDATE staff_accounts SET password_hash=?, display_name=?, role=?, is_active=1 "
                    "WHERE username=?",
                    (pw_hash, display_name, role, username),
                )
            continue
        try:
            db.execute(
                "INSERT INTO staff_accounts "
                "(username, password_hash, display_name, role, is_active) VALUES (?, ?, ?, ?, 1)",
                (username, pw_hash, display_name, role),
            )
        except Exception:
            pass


def finalize_vendor_boss_account(*, username: str, password: str, display_name: str) -> None:
    """
    厂家向导步骤：创建/更新酒店老板账号，并停用所有演示账号。
    完成后写入 `vendor_seeded_boss_at`，登录面板由此切换到「真实账号」模式。

    注意：禁止把厂家专属账号（admin）当成酒店老板账号 —— 否则
    厂家工程师下次回访就没法用厂家身份登录了。
    """
    init_permission_tables()

    username = (username or "").strip()
    display_name = (display_name or "").strip() or username
    if not username:
        raise ValueError("用户名不能为空")
    if is_vendor_account(username):
        raise ValueError(
            f"账号「{username}」是厂家专属账号，不能用作酒店老板账号。\n"
            "请另起一个名字（如「老板」或店主拼音）。"
        )
    if not password or len(password) < 4:
        raise ValueError("密码至少 4 位")

    pw_hash = _hash_password(password)
    row = db.execute(
        "SELECT id FROM staff_accounts WHERE username=?", (username,)
    ).fetchone()
    if row:
        db.execute(
            "UPDATE staff_accounts SET password_hash=?, display_name=?, role='boss', is_active=1 "
            "WHERE id=?",
            (pw_hash, display_name, row[0]),
        )
    else:
        db.execute(
            "INSERT INTO staff_accounts "
            "(username, password_hash, display_name, role, is_active) VALUES (?, ?, ?, 'boss', 1)",
            (username, pw_hash, display_name),
        )

    # 停用所有演示账号（含老板演示号「老板/1234」），除非用户名恰好就是它
    demo_usernames = [u for u, _p, _d, _r in DEMO_ROLE_ACCOUNTS]
    for u in demo_usernames:
        if u == username:
            continue
        try:
            db.execute("UPDATE staff_accounts SET is_active=0 WHERE username=?", (u,))
        except Exception:
            pass

    # 厂家账号（admin）保留启用 —— 厂家回访诊断用
    try:
        db.execute(
            "UPDATE staff_accounts SET is_active=0 WHERE username='admin' AND username!=?",
            (VENDOR_USERNAME,),
        )
    except Exception:
        pass

    db.set_config(
        "vendor_seeded_boss_at",
        _dt.datetime.now().isoformat(timespec="seconds"),
    )
    db.set_config("vendor_seeded_boss_username", username)


# ================================================================
# 权限管理器
# ================================================================
class PermissionManager:
    """权限检查和管理"""

    # 当前登录用户（全局单例）
    _current_user: Optional[Dict] = None

    @classmethod
    def login(cls, username: str, password: str) -> Tuple[bool, str]:
        """登录验证（使用 _verify_password 支持 PBKDF2 + 旧版 SHA-256 兼容）

        [sub-a] 强制改密 B7：登录成功后读取 must_change_password 标志写入
        cls._current_user，UI 层可通过 current_user().get("must_change_password")
        判断是否需弹改密框。读取失败（旧库列不存在）默认 0，不阻断登录。
        """
        user = db.execute(
            "SELECT id, username, password_hash, display_name, role, is_active "
            "FROM staff_accounts WHERE username=?",
            (username,)
        ).fetchone()
        if not user:
            return False, "用户不存在"
        if not user[5]:
            return False, "账号已被停用"
        if not _verify_password(password, user[2]):
            return False, "密码错误"

        # [sub-a] 读取强制改密标志；列不存在时容错为 0
        must_change = 0
        try:
            mc_row = db.execute(
                "SELECT COALESCE(must_change_password, 0) FROM staff_accounts WHERE id=?",
                (user[0],)
            ).fetchone()
            if mc_row:
                must_change = int(mc_row[0] or 0)
        except Exception:
            pass

        cls._current_user = {
            "id": user[0], "username": user[1], "display_name": user[3],
            "role": user[4],
            "must_change_password": must_change,  # [sub-a] UI 据此弹改密框
        }

        db.execute(
            "UPDATE staff_accounts SET last_login=CURRENT_TIMESTAMP WHERE id=?",
            (user[0],)
        )

        # 密码过期检查 (>90天未修改)
        pw_expired_warning = ""
        try:
            pw_row = db.execute(
                "SELECT COALESCE(last_pw_change, created_at) FROM staff_accounts WHERE id=?",
                (user[0],)
            ).fetchone()
            if pw_row and pw_row[0]:
                from datetime import datetime, timedelta
                last_pw = datetime.fromisoformat(str(pw_row[0]).replace("T", " ").strip()[:19])
                days_ago = (datetime.now() - last_pw).days
                if days_ago > 90:
                    pw_expired_warning = f" ⚠️ 密码已 {days_ago} 天未修改，建议尽快修改。"
        except Exception:
            pass

        # 若旧账号仍使用单次 SHA-256，登录成功后自动升级为 PBKDF2
        if not user[2].startswith("pbkdf2$"):
            try:
                new_hash = _hash_password(password)
                db.execute(
                    "UPDATE staff_accounts SET password_hash=? WHERE id=?",
                    (new_hash, user[0])
                )
            except Exception:
                pass  # 升级失败不影响登录

        from event_bus import bus

        bus.user_logged_in.emit(user[1], user[4])
        # [sub-a] 强制改密提示
        must_change_hint = " ⚠️ 检测到默认密码，请立即修改密码。" if must_change else ""
        return True, f"欢迎 {user[3]}{pw_expired_warning}{must_change_hint}"

    @classmethod
    def bootstrap_local_boss(cls) -> None:
        """单机免登录：仅开发/演示；生产应关闭 single_user_mode。"""
        cls._current_user = {
            "id": 0,
            "username": "local",
            "display_name": i18n.t("role_name_boss"),
            "role": "boss",
        }

    @classmethod
    def logout(cls):
        cls._current_user = None

    @classmethod
    def current_user(cls) -> Optional[Dict]:
        return cls._current_user

    @classmethod
    def current_role(cls) -> str:
        if cls._current_user:
            return cls._current_user["role"]
        # 未登录时：单机免登录只允许在 debug_mode=1 的开发环境启用。
        # 生产环境即使误开 single_user_mode，也不能拿到 boss role。
        try:
            from database import db as _db
            if (
                _db.get_config("single_user_mode") == "1"
                and _db.get_config("debug_mode") == "1"
            ):
                return "boss"
        except Exception:
            pass
        return "guest"  # 默认无权限角色，防止未登录绕过权限检查

    @classmethod
    def has_permission(cls, permission_key: str) -> bool:
        """检查当前用户是否有某权限"""
        role = cls.current_role()
        role_perms = get_role_permissions(role)

        # boss 拥有全部权限
        if ROLES.get(role, {}).get("permissions") == "*":
            return True

        # 检查个人覆盖
        if cls._current_user:
            override = db.execute(
                "SELECT granted FROM permission_overrides WHERE username=? AND permission_key=?",
                (cls._current_user["username"], permission_key)
            ).fetchone()
            if override is not None:
                return bool(override[0])

        return permission_key in role_perms

    @classmethod
    def require_permission(cls, permission_key: str, error_msg: str = None):
        """检查权限，无权限则抛出异常"""
        if not cls.has_permission(permission_key):
            raise PermissionError(
                error_msg or f"权限不足：需要 {PERMISSION_LABELS.get(permission_key, permission_key)}"
            )

    @classmethod
    def get_all_staff(cls) -> List[Dict]:
        """获取所有员工账号"""
        rows = db.execute(
            "SELECT id, username, display_name, role, phone, employee_id, is_active, last_login "
            "FROM staff_accounts ORDER BY role, username"
        ).fetchall()
        return [
            {
                "id": r[0], "username": r[1], "display_name": r[2],
                "role": r[3], "phone": r[4], "employee_id": r[5],
                "is_active": bool(r[6]), "last_login": r[7],
            }
            for r in rows
        ]

    @classmethod
    def create_account(cls, username: str, password: str, display_name: str,
                       role: str = "frontdesk", phone: str = "",
                       employee_id: str = "") -> Tuple[bool, str]:
        """创建员工账号（使用 PBKDF2 哈希）"""
        if len(password) < 4:
            return False, "密码至少4位"
        # 校验操作者权限：只有 boss/manager 可以创建账号
        if not cls.has_permission("manage_staff"):
            return False, "权限不足：需要员工管理权限"
        pw_hash = _hash_password(password)
        try:
            db.execute(
                "INSERT INTO staff_accounts (username, password_hash, display_name, role, phone, employee_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, pw_hash, display_name, role, phone, employee_id)
            )
            db.log_action(
                cls._current_user["username"] if cls._current_user else "SYSTEM",
                "CREATE_STAFF",
                f"username={username} role={role}"
            )
            return True, f"账号 {username} 创建成功"
        except Exception as e:
            return False, f"创建失败: {e}"

    @classmethod
    def reset_password(cls, username: str, new_password: str) -> Tuple[bool, str]:
        """重置密码（使用 PBKDF2 哈希，并记录审计日志）"""
        if len(new_password) < 4:
            return False, "密码至少4位"
        # 校验操作者权限：只有 boss/manager 可以重置他人密码
        operator = cls._current_user["username"] if cls._current_user else None
        if operator != username and not cls.has_permission("manage_staff"):
            return False, "权限不足：只能重置自己的密码或需要员工管理权限"
        pw_hash = _hash_password(new_password)
        db.execute(
            "UPDATE staff_accounts SET password_hash=?, last_pw_change=CURRENT_TIMESTAMP WHERE username=?",
            (pw_hash, username)
        )
        db.log_action(
            operator or "SYSTEM",
            "RESET_PASSWORD",
            f"target={username}"
        )
        return True, "密码已重置"

    @classmethod
    def verify_critical_action(cls, action_label: str, parent_widget=None) -> bool:
        """
        关键操作后端二次验证：要求当前用户重新输入密码确认身份。
        适用于：资金支出、删除数据、修改系统设置等高风险操作。
        验证通过返回真，取消或验证失败返回假。
        """
        if cls._current_user is None:
            return False
        from PySide6.QtWidgets import QInputDialog
        username = cls._current_user["username"]
        display_name = cls._current_user.get("display_name", username)
        pwd, ok = QInputDialog.getText(
            parent_widget, "🔐 身份二次验证",
            f"操作【{action_label}】需要验证身份。\n请 {display_name} 重新输入登录密码：",
            QLineEdit.EchoMode.Password
        )
        if not ok or not pwd:
            return False
        user = db.execute(
            "SELECT password_hash FROM staff_accounts WHERE username=? AND is_active=1",
            (username,)
        ).fetchone()
        if not user:
            return False
        if not _verify_password(pwd, user[0]):
            play_fail()
            show_warning(parent_widget, "验证失败", "密码错误，操作已取消。")
            db.log_action(username, "CRITICAL_AUTH_FAIL", f"action={action_label}")
            return False
        db.log_action(username, "CRITICAL_AUTH_OK", f"action={action_label}")
        return True

    @classmethod
    def toggle_active(cls, username: str) -> bool:
        user = db.execute(
            "SELECT is_active FROM staff_accounts WHERE username=?", (username,)
        ).fetchone()
        if not user:
            return False
        new_state = 0 if user[0] else 1
        db.execute(
            "UPDATE staff_accounts SET is_active=? WHERE username=?", (new_state, username)
        )
        return True

    @classmethod
    def set_override(cls, username: str, permission_key: str, granted: bool):
        """为特定用户设置权限覆盖"""
        db.execute(
            "INSERT OR REPLACE INTO permission_overrides (username, permission_key, granted) "
            "VALUES (?, ?, ?)",
            (username, permission_key, 1 if granted else 0)
        )

    @classmethod
    def remove_override(cls, username: str, permission_key: str):
        db.execute(
            "DELETE FROM permission_overrides WHERE username=? AND permission_key=?",
            (username, permission_key)
        )

    @staticmethod
    def can_view_field(role: str, field_name: str) -> bool:
        if role == "frontdesk" and field_name == "cost_price":
            return False
        return True

    @staticmethod
    def needs_manager_approval(amount: float, action_type: str = "") -> bool:
        if action_type == "REFUND" and amount > 500:
            return True
        if action_type == "PAYOUT" and amount > 1000:
            return True
        return False

    @staticmethod
    def is_action_allowed_in_time_window(role: str, action: str) -> bool:
        from datetime import datetime
        now = datetime.now()
        if role == "frontdesk" and action in ("pricing", "rate_change"):
            if now.hour >= 22 or now.hour < 6:
                return False
        return True


# ================================================================
# 登录对话框
# ================================================================
def ensure_authenticated(parent=None) -> bool:
    """启动时登录；单机模式仅在 debug_mode=1 的开发环境等同老板本地会话。"""
    if PermissionManager.current_user():
        return True
    try:
        if db.get_config("single_user_mode") == "1" and db.get_config("debug_mode") == "1":
            PermissionManager.bootstrap_local_boss()
            bus.user_logged_in.emit("local", "boss")
            return True
    except Exception:
        pass
    try:
        if db.get_config("auto_login_enabled") == "1":
            auto_user = (db.get_config("auto_login_username") or "").strip()
            auto_pwd = db.get_config("auto_login_password") or ""
            if auto_user and auto_pwd:
                ok, _msg = PermissionManager.login(auto_user, auto_pwd)
                if ok:
                    role = PermissionManager.current_role() or "frontdesk"
                    bus.user_logged_in.emit(auto_user, role)
                    return True
    except Exception:
        pass
    dlg = LoginDialog(parent)
    return dlg.exec() == QDialog.DialogCode.Accepted


class LoginDialog(QDialog):
    """员工登录对话框 — v7 四时之色入口（左品牌+右表单）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LoginDialog")
        self.setWindowTitle(i18n.t("login_win_title"))
        style_dialog(self, size="large")

        # v7：左右分栏布局 — 左品牌区 + 右表单区
        root_lay = QHBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ══ 左侧：品牌展示区（sidebar 深色底）══
        brand_panel = QFrame()
        brand_panel.setObjectName("LoginBrandPanel")
        brand_panel.setFixedWidth(320)
        brand_lay = QVBoxLayout(brand_panel)
        brand_lay.setContentsMargins(40, 48, 40, 40)
        brand_lay.setSpacing(16)
        brand_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        from brand_assets import make_brand_mark_label
        icon_lbl = make_brand_mark_label(56, object_name="LoginBrandIcon")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        brand_lay.addWidget(icon_lbl)

        brand_name = QLabel(effective_brand(db)["short"])
        brand_name.setObjectName("LoginBrandName")
        brand_lay.addWidget(brand_name)

        brand_tagline = QLabel(i18n.t("login_tagline", default="中小酒店 · 智能运营"))
        brand_tagline.setObjectName("LoginBrandTagline")
        brand_lay.addWidget(brand_tagline)

        brand_lay.addStretch()

        # 主题预览（4 色点）
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_title = QLabel(i18n.t("login_theme_preview", default="四时之色"))
        theme_title.setObjectName("LoginThemeTitle")
        theme_row.addWidget(theme_title)
        theme_row.addStretch()
        from design_tokens import _p
        theme_colors = [_p("primary"), _p("amount_positive"), _p("accent"), _p("sidebar")]
        for color in theme_colors:
            dot = QLabel()
            dot.setFixedSize(12, 12)
            dot.setStyleSheet(f"background: {color}; border-radius: 6px;")
            theme_row.addWidget(dot)
        brand_lay.addLayout(theme_row)

        root_lay.addWidget(brand_panel)

        # ══ 右侧：表单区 ══
        form_panel = QFrame()
        form_panel.setObjectName("LoginFormPanel")
        form_lay = QVBoxLayout(form_panel)
        form_lay.setContentsMargins(40, 48, 40, 40)
        form_lay.setSpacing(16)

        # 标题
        title = QLabel(i18n.t("login_title", default="欢迎回来"))
        title.setObjectName("LoginTitle")
        form_lay.addWidget(title)

        subtitle = QLabel(i18n.t("login_subtitle", default="选择岗位快速登录，或输入账号密码"))
        subtitle.setObjectName("LoginSubtitle")
        form_lay.addWidget(subtitle)

        form_lay.addSpacing(8)

        # 厂商隐藏入口
        from PySide6.QtGui import QShortcut, QKeySequence
        sc = QShortcut(QKeySequence("Ctrl+Shift+F10"), self)
        sc.activated.connect(self._show_vendor_login)

        # 岗位快捷按钮
        from role_ui import make_role_avatar_label
        qrow = QHBoxLayout()
        qrow.setSpacing(10)
        for u, p, disp, role, _demo in self._panel_accounts(False):
            btn = QPushButton()
            btn.setObjectName("LoginRoleBtn")
            bl = QVBoxLayout(btn)
            bl.setContentsMargins(8, 12, 8, 10)
            bl.setSpacing(6)
            av = make_role_avatar_label(role, 28)
            bl.addWidget(av, 0, Qt.AlignmentFlag.AlignHCenter)
            cap = QLabel(disp)
            cap.setObjectName("LoginRoleCaption")
            cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            bl.addWidget(cap, 0, Qt.AlignmentFlag.AlignHCenter)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, nu=u, np=p: self._quick_login(nu, np))
            qrow.addWidget(btn, 1)
        form_lay.addLayout(qrow)

        form_lay.addSpacing(12)

        # 账号输入
        user_lbl = QLabel(i18n.t("login_label_user", default="账号"))
        user_lbl.setObjectName("LoginFieldLabel")
        form_lay.addWidget(user_lbl)
        self.txt_user = QLineEdit()
        self.txt_user.setObjectName("LoginInput")
        self.txt_user.setPlaceholderText(i18n.t("login_ph_user", default="请输入用户名"))
        self.txt_user.setMinimumHeight(40)
        form_lay.addWidget(self.txt_user)

        # 密码输入
        pass_lbl = QLabel(i18n.t("login_label_pass", default="密码"))
        pass_lbl.setObjectName("LoginFieldLabel")
        form_lay.addWidget(pass_lbl)
        self.txt_pass = QLineEdit()
        self.txt_pass.setObjectName("LoginInput")
        self.txt_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_pass.setPlaceholderText(i18n.t("login_ph_pass", default="请输入密码"))
        self.txt_pass.returnPressed.connect(self._login)
        self.txt_pass.setMinimumHeight(40)
        form_lay.addWidget(self.txt_pass)

        # 记住密码
        from PySide6.QtCore import QSettings
        self._settings = QSettings("ShadowGuard", "SolidPMS")
        self.chk_remember = QCheckBox("记住密码")
        self.chk_remember.setObjectName("LoginCheckbox")
        is_dev = not getattr(sys, "frozen", False)
        self.chk_remember.setVisible(is_dev)
        if is_dev:
            saved_user = self._settings.value("login/username", "")
            saved_pwd = self._settings.value("login/password", "")
            if saved_user:
                self.txt_user.setText(saved_user)
            if saved_pwd:
                self.txt_pass.setText(saved_pwd)
                self.chk_remember.setChecked(True)
        form_lay.addWidget(self.chk_remember)

        # 错误提示
        self.lbl_error = QLabel("")
        self.lbl_error.setObjectName("LoginErrorLabel")
        self.lbl_error.setMinimumHeight(20)
        form_lay.addWidget(self.lbl_error)

        form_lay.addSpacing(4)

        # 登录按钮
        self.btn_login = QPushButton(i18n.t("login_btn", default="登 录"))
        self.btn_login.setObjectName("SolidPrimaryBtn")
        self.btn_login.setMinimumHeight(44)
        self.btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_login.clicked.connect(self._login)
        form_lay.addWidget(self.btn_login)

        form_lay.addStretch()

        root_lay.addWidget(form_panel, 1)

    def _quick_login(self, user: str, pwd: str) -> None:
        self.txt_user.setText(user)
        self.txt_pass.setText(pwd)
        self._login()

    @staticmethod
    def _panel_accounts(vendor_seeded: bool) -> List[Tuple[str, str, str, str, bool]]:
        """返回登录面板按钮数据 (username, pwd, display, role, is_demo)。

        vendor_seeded=False → 还没设置过真实老板，仅显示厂家 admin
        vendor_seeded=True  → 显示所有已激活账号（无预填密码）
        """
        if not vendor_seeded:
            # 仅显示厂家 admin（密码自动填充方便快速登录）
            return [(VENDOR_USERNAME, VENDOR_PASSWORD, VENDOR_DISPLAY_NAME, "boss", False)]
        try:
            rows = db.execute(
                "SELECT username, display_name, role FROM staff_accounts "
                "WHERE is_active=1 "
                "ORDER BY CASE role WHEN 'boss' THEN 0 WHEN 'manager' THEN 1 "
                "WHEN 'frontdesk' THEN 2 WHEN 'finance' THEN 3 ELSE 4 END, id"
            ).fetchall()
        except Exception:
            rows = []
        return [
            (u, "", (d or u), r, False)
            for (u, d, r) in rows
        ]

    def _show_vendor_login(self):
        """隐藏厂家入口，弹出厂家登录浮窗。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("厂家诊断登录")
        style_dialog(dlg, size="small")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        lay.addWidget(build_dialog_header("厂家诊断", "输入厂家密码后登录"))

        pwd = QLineEdit()
        pwd.setEchoMode(QLineEdit.EchoMode.Password)
        pwd.setPlaceholderText("厂家密码")
        lay.addWidget(pwd)

        btn = QPushButton("登录")
        btn.setObjectName("BtnSave")
        btn.clicked.connect(lambda: (
            self.txt_user.setText(VENDOR_USERNAME),
            self.txt_pass.setText(pwd.text()),
            self._login(),
            dlg.accept(),
        ))
        pwd.returnPressed.connect(btn.click)
        lay.addWidget(btn)

        dlg.exec()

    def _login(self):
        user = self.txt_user.text().strip()
        pwd = self.txt_pass.text().strip()
        if not user or not pwd:
            self.lbl_error.setText(i18n.t("login_err_empty"))
            return
        # 加 loading 反馈
        self.btn_login.setEnabled(False)
        self.btn_login.setText("登录中…")
        self.lbl_error.setText("")
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        ok, msg = PermissionManager.login(user, pwd)

        self.btn_login.setEnabled(True)
        self.btn_login.setText(i18n.t("login_btn"))
        if ok:
            # 记住密码（仅开发模式）
            if getattr(self, "_settings", None):
                if self.chk_remember.isChecked():
                    self._settings.setValue("login/username", user)
                    self._settings.setValue("login/password", pwd)
                else:
                    self._settings.remove("login/username")
                    self._settings.remove("login/password")
            self.accept()
        else:
            self.lbl_error.setText(msg)
            try:
                from motion_gate import shake_invalid
                shake_invalid(self.lbl_error)
            except Exception:
                pass


# ================================================================
# 员工管理对话框
# ================================================================
class StaffManagementDialog(QDialog):
    """员工账号管理"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("员工权限管理")
        style_dialog(self, size="large")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(build_dialog_header(
            "员工账号与权限",
            "创建前台/保洁/经理账号，控制各角色可见功能。\n"
            "角色权限固定，也可针对单人额外加减权限。"
        ))

        # 员工列表
        self.tbl = QTableWidget()
        self.tbl.setColumnCount(6)
        self.tbl.setHorizontalHeaderLabels(["账号", "姓名", "角色", "手机", "状态", "最后登录"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        fd_apply_table_palette(self.tbl)
        layout.addWidget(self.tbl)

        # 新增表单
        grp = QGroupBox("新增员工")
        f = QFormLayout(grp)

        self.txt_uname = QLineEdit()
        self.txt_uname.setPlaceholderText("登录用账号名")
        f.addRow("账号:", self.txt_uname)

        self.txt_pwd = QLineEdit()
        self.txt_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_pwd.setPlaceholderText("至少4位")
        f.addRow("密码:", self.txt_pwd)

        self.txt_dname = QLineEdit()
        self.txt_dname.setPlaceholderText("显示用姓名")
        f.addRow("姓名:", self.txt_dname)

        self.cmb_role = QComboBox()
        for rk, rv in ROLES.items():
            self.cmb_role.addItem(f"{rv['name']}", rk)
        f.addRow("角色:", self.cmb_role)

        self.txt_phone = QLineEdit()
        self.txt_phone.setPlaceholderText("手机号")
        f.addRow("手机:", self.txt_phone)

        self.txt_eid = QLineEdit()
        self.txt_eid.setPlaceholderText("工号")
        f.addRow("工号:", self.txt_eid)

        btn_add = QPushButton("创建账号")
        btn_add.setObjectName("SolidPrimaryBtn")
        btn_add.clicked.connect(self._add)
        f.addRow(btn_add)
        layout.addWidget(grp)

        # 操作按钮
        btn_row = QHBoxLayout()

        btn_toggle = QPushButton("启用/停用")
        btn_toggle.setObjectName("FdGhostBtn")
        btn_toggle.clicked.connect(self._toggle)
        btn_row.addWidget(btn_toggle)

        btn_reset = QPushButton("重置密码")
        btn_reset.setObjectName("FdGhostBtn")
        btn_reset.clicked.connect(self._reset_pwd)
        btn_row.addWidget(btn_reset)

        btn_perm = QPushButton("单独权限设置")
        btn_perm.setObjectName("FdGhostBtn")
        btn_perm.clicked.connect(self._perm_override)
        btn_row.addWidget(btn_perm)

        btn_tmpl = QPushButton("权限模板说明")
        btn_tmpl.setObjectName("FdGhostBtn")
        btn_tmpl.clicked.connect(self._show_templates)
        btn_row.addWidget(btn_tmpl)

        btn_row.addStretch()

        btn_close = QPushButton("关闭")
        btn_close.setObjectName("FdGhostBtn")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self._load()

    def _show_templates(self):
        """显示 5 套默认权限模板说明。"""
        d = QDialog(self); d.setWindowTitle("权限模板说明"); style_dialog(d, size="large")
        lv = QVBoxLayout(d); lv.setContentsMargins(16,16,16,16); lv.setSpacing(12)
        lv.addWidget(build_dialog_header("默认权限模板", "以下 5 套模板定义了各角色的默认权限范围"))

        template_order = ["manager", "frontdesk", "finance", "vendor"]
        for rk in template_order:
            rv = ROLES.get(rk, {})
            name = rv.get("name", rk)
            perms = rv.get("permissions", [])
            if perms == "*":
                perms = list(PERMISSION_LABELS.keys())
            perm_names = [PERMISSION_LABELS.get(p, p) for p in perms[:8]]
            perm_text = "、".join(perm_names)
            if len(perms) > 8:
                perm_text += f"… 等{len(perms)}项"
            lbl = QLabel(f"<b>{name}</b>（{rk}）：{perm_text}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("padding:4px 0;")
            lv.addWidget(lbl)

        btn_close = QPushButton("关闭"); btn_close.setObjectName("FdGhostBtn"); btn_close.clicked.connect(d.accept)
        lv.addWidget(btn_close)
        d.exec()

    def _load(self):
        staff = PermissionManager.get_all_staff()
        self.tbl.setRowCount(len(staff))
        for i, s in enumerate(staff):
            self.tbl.setItem(i, 0, QTableWidgetItem(s["username"]))
            self.tbl.setItem(i, 1, QTableWidgetItem(s["display_name"]))
            self.tbl.setItem(i, 2, QTableWidgetItem(ROLES.get(s["role"], {}).get("name", s["role"])))
            self.tbl.setItem(i, 3, QTableWidgetItem(s.get("phone", "")))
            self.tbl.setItem(i, 4, QTableWidgetItem("✅ 启用" if s["is_active"] else "⛔ 停用"))
            self.tbl.setItem(i, 5, QTableWidgetItem(str(s.get("last_login", "-")) or "-"))

    def _add(self):
        uname = self.txt_uname.text().strip()
        pwd = self.txt_pwd.text().strip()
        dname = self.txt_dname.text().strip()
        if not uname or not pwd or not dname:
            show_warning(self, "必填", "账号、密码、姓名不能为空。")
            return
        role = self.cmb_role.currentData()
        phone = self.txt_phone.text().strip()
        eid = self.txt_eid.text().strip()
        ok, msg = PermissionManager.create_account(uname, pwd, dname, role, phone, eid)
        if ok:
            show_info(self, "创建成功", msg)
            self._load()
        else:
            show_warning(self, "创建失败", msg)

    def _toggle(self):
        row = self.tbl.currentRow()
        if row < 0:
            show_warning(self, "请选择", "请先选中一个员工。")
            return
        uname = self.tbl.item(row, 0).text()
        PermissionManager.toggle_active(uname)
        self._load()

    def _reset_pwd(self):
        row = self.tbl.currentRow()
        if row < 0:
            show_warning(self, "请选择", "请先选中一个员工。")
            return
        uname = self.tbl.item(row, 0).text()
        from PySide6.QtWidgets import QInputDialog
        new_pwd, ok = QInputDialog.getText(
            self, "重置密码", f"为 {uname} 设置新密码（至少4位）:",
            echo=QLineEdit.EchoMode.Password
        )
        if ok and new_pwd:
            success, msg = PermissionManager.reset_password(uname, new_pwd)
            if success:
                show_info(self, "成功", msg)
            else:
                show_warning(self, "失败", msg)

    def _perm_override(self):
        """打开个人权限覆盖对话框"""
        row = self.tbl.currentRow()
        if row < 0:
            show_warning(self, "请选择", "请先选中一个员工。")
            return
        uname = self.tbl.item(row, 0).text()
        row_db = db.execute("SELECT role FROM staff_accounts WHERE username=?", (uname,)).fetchone()
        role_key = row_db[0] if row_db else "frontdesk"
        dlg = PermissionOverrideDialog(uname, role_key, self)
        dlg.exec()


# ================================================================
# 个人权限覆盖对话框
# ================================================================
class PermissionOverrideDialog(QDialog):
    """为单个用户启/禁用特定权限"""

    def __init__(self, username: str, role_key: str, parent=None):
        super().__init__(parent)
        self.username = username
        self.role_key = role_key or "frontdesk"
        self.setWindowTitle(i18n.t("perm_override_title").format(username))
        style_dialog(self, size="medium")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        role_perms_set = set(get_role_permissions(self.role_key))
        is_boss = ROLES.get(self.role_key, {}).get("permissions") == "*"
        role_label = ROLES.get(self.role_key, {}).get("name", self.role_key)

        info = QLabel(
            i18n.t("perm_override_intro").format(
                user=username, role=role_label, n=len(role_perms_set) if not is_boss else i18n.t("perm_all")
            )
        )
        info.setWordWrap(True)
        info.setObjectName("FdMutedLabel")
        layout.addWidget(info)

        self.lbl_effective = QLabel()
        self.lbl_effective.setWordWrap(True)
        self.lbl_effective.setObjectName("FdMutedLabel")
        layout.addWidget(self.lbl_effective)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        grid = QVBoxLayout(inner)
        grid.setSpacing(6)
        self.checkboxes: Dict[str, QCheckBox] = {}
        overrides = db.execute(
            "SELECT permission_key, granted FROM permission_overrides WHERE username=?",
            (username,),
        ).fetchall()
        override_map = {r[0]: bool(r[1]) for r in overrides}

        for pk, pl in sorted(PERMISSION_LABELS.items(), key=lambda x: x[1]):
            row = QHBoxLayout()
            cb = QCheckBox(pl)
            cb.setProperty("perm_key", pk)
            if pk in override_map:
                cb.setCheckState(Qt.CheckState.Checked if override_map[pk] else Qt.CheckState.Unchecked)
                cb.setToolTip(i18n.t("perm_override_custom"))
            elif is_boss or pk in role_perms_set:
                cb.setCheckState(Qt.CheckState.Checked)
                cb.setToolTip(i18n.t("perm_from_role"))
            else:
                cb.setCheckState(Qt.CheckState.Unchecked)
                cb.setToolTip(i18n.t("perm_from_role_denied"))
            if is_boss:
                cb.setEnabled(False)
            cb.stateChanged.connect(self._refresh_effective_preview)
            row.addWidget(cb)
            self.checkboxes[pk] = cb
            grid.addLayout(row)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)
        self._refresh_effective_preview()

        btn_row = QHBoxLayout()
        btn_save = QPushButton(i18n.t("settings_perms_save"))
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)
        btn_row.addStretch()
        btn_close = QPushButton(i18n.t("btn_close"))
        btn_close.setObjectName("FdGhostBtn")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _effective_permissions(self) -> Set[str]:
        base = set(get_role_permissions(self.role_key))
        if ROLES.get(self.role_key, {}).get("permissions") == "*":
            return set(PERMISSION_LABELS.keys())
        for pk, cb in self.checkboxes.items():
            st = cb.checkState()
            if st == Qt.CheckState.Checked:
                base.add(pk)
            elif st == Qt.CheckState.Unchecked:
                base.discard(pk)
        return base

    def _refresh_effective_preview(self) -> None:
        eff = sorted(
            PERMISSION_LABELS.get(p, p) for p in self._effective_permissions()
        )
        preview = "、".join(eff[:12])
        if len(eff) > 12:
            preview += i18n.t("perm_effective_more").format(n=len(eff) - 12)
        self.lbl_effective.setText(i18n.t("perm_effective_preview").format(preview=preview or "—"))

    def _save(self):
        role_defaults = set(get_role_permissions(self.role_key))
        is_boss = ROLES.get(self.role_key, {}).get("permissions") == "*"
        if is_boss:
            show_info(self, i18n.t("settings_perms_title"), i18n.t("settings_perms_boss_fixed"))
            return
        for pk, cb in self.checkboxes.items():
            checked = cb.isChecked()
            in_role = pk in role_defaults
            if checked == in_role:
                PermissionManager.remove_override(self.username, pk)
            elif checked:
                PermissionManager.set_override(self.username, pk, True)
            else:
                PermissionManager.set_override(self.username, pk, False)
        show_info(self, i18n.t("settings_perms_title"), i18n.t("perm_override_saved").format(user=self.username))


# ================================================================
# 权限便捷检查装饰器（用于 UI 控制）
# ================================================================
def check_perm(perm_key: str):
    """装饰器：包裹需要权限的方法"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            if PermissionManager.has_permission(perm_key):
                return func(*args, **kwargs)
            else:
                play_warn()
                show_warning(
                    args[0] if args else None,
                    "权限不足",
                    f"当前账号没有 {PERMISSION_LABELS.get(perm_key, perm_key)} 权限。",
                )
                return None
        return wrapper
    return decorator


def guard_widget(widget, perm_key: str):
    """根据权限显/隐控件"""
    widget.setVisible(PermissionManager.has_permission(perm_key))
    widget.setEnabled(PermissionManager.has_permission(perm_key))
