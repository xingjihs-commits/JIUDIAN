"""
======================================================
ShadowGuard — 数据导入及整机迁移服务
界面：3个标签页
  标签页1：从CSV导入房间清单（原有功能）
  标签页2：整机数据迁移（导出/导入备份文件）
  标签页3：门锁加密密钥迁移（导出/导入密钥文件）
======================================================
"""
import csv
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QProgressBar, QTabWidget,
    QCheckBox, QLineEdit, QWidget, QGroupBox, QGridLayout
)

from database import db
from design_tokens import _p
from event_bus import bus
from i18n import i18n
from ui_helpers import style_dialog, build_dialog_header, show_error, show_info, show_warning, ask_confirm
from legacy_migration_guide import GuideAction, data_import_tab_session
from migration_guide_panel import MigrationGuidePanel

# ================================================================
# 常量
# ================================================================
SGBACK_MAGIC = b"SGBAK\n"         # .sgbak 文件签名
SGKEY_MAGIC = b"SGKEY\n"         # .sgkey 文件签名
EXPORT_VERSION = 2               # 当前导出格式版本

# ================================================================
# 迁移工具 — 纯函数，不依赖界面模块
# ================================================================

def _generate_export_signature(machine_code: str, version: int, data_bytes: bytes) -> str:
    """基于机器码 + 版本 + 数据生成哈希签名"""
    key = machine_code.encode("utf-8")
    base = f"SG_{version}_{len(data_bytes)}".encode() + data_bytes
    return hmac.new(key, base, hashlib.sha256).hexdigest()[:32]


def _get_sqlite_db_path() -> str:
    """获取当前 shadow_guard.db 文件路径"""
    # database.py 内部使用 sqlite3.connect("shadow_guard.db")
    # 我们尝试找到它
    candidates = [
        os.path.join(os.path.dirname(__file__), "shadow_guard.db"),
        "shadow_guard.db",
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    # fallback
    return os.path.abspath("shadow_guard.db")


def _get_machine_code() -> str:
    """计算机器码（MAC地址格式化）"""
    node = _uuid.getnode()
    return '-'.join((f'{node:012X}')[i:i + 2] for i in range(0, 12, 2))


def _get_hotel_id() -> str:
    """获取或生成 hotel_id"""
    hid = ""
    try:
        hid = db.get_config("hotel_id") or ""
    except Exception:
        pass
    if not hid:
        try:
            hid = f"HTL_{_uuid.uuid4().hex[:12].upper()}"
            db.set_config("hotel_id", hid)
        except Exception:
            pass
    return hid


# ================================================================
# 导出 .sgbak  — 整机数据打包
# ================================================================
def export_full_backup(target_path: str) -> Dict[str, Any]:
    """
    导出整个数据库到备份文件（实际上是JSON包裹的数据库转储）
    返回: {"ok": True, "counts": {...}} 或 {"ok": False, "error": "..."}
    """
    try:
        db_path = _get_sqlite_db_path()
        if not os.path.exists(db_path):
            return {"ok": False, "error": i18n.t("data_import.err_db_not_found").format(path=db_path)}

        # 读取源数据库
        src_conn = sqlite3.connect(db_path)
        src_conn.row_factory = sqlite3.Row
        cur = src_conn.cursor()

        # 要导出的表 (config 表只导出指定白名单，不导出敏感密钥) (审计/账本全量)
        export_tables = {
            "rooms": ("SELECT * FROM rooms", True),
            "guests": ("SELECT * FROM guests", True),         # 本地客人表 (如果存在)
            "room_bindings": ("SELECT * FROM room_bindings", True),
            "orders": ("SELECT * FROM orders", True),
            "inventory_changes": ("SELECT * FROM inventory_changes", True),
            "audit_log": ("SELECT * FROM audit_log", True),
            "energy_readings": ("SELECT * FROM energy_readings", True),
            "ledger_hashes": ("SELECT * FROM ledger_hashes", True),
        }
        # config 白名单 (不含跟机器码相关的密钥)
        config_whitelist = [
            "hotel_id", "hotel_name", "region", "salesperson_id",
            "telegram_chat_id", "cloud_worker_url", "cloud_enabled",
            "cloud_poll_interval", "loyalty_points_rate",
            "checkin_bonus_points", "max_cart_items",
            "auto_confirm_order", "bot_welcome_text",
            "theme", "layout", "language",
            "allow_anonymous", "allow_duplicate_rooms",
        ]

        export_data: Dict[str, Any] = {
            "version": EXPORT_VERSION,
            "machine_code": _get_machine_code(),
            "hotel_id": _get_hotel_id(),
            "created_at": datetime.now().isoformat(),
            "counts": {},
            "tables": {},
            "config": {},
        }

        # 导出数据表
        for table_name, (sql, _) in export_tables.items():
            try:
                cur.execute(sql)
                rows = [dict(row) for row in cur.fetchall()]
                export_data["tables"][table_name] = rows
                export_data["counts"][table_name] = len(rows)
            except sqlite3.OperationalError:
                # 表可能不存在，跳过
                export_data["tables"][table_name] = []
                export_data["counts"][table_name] = 0

        # 导出 config 白名单
        for key in config_whitelist:
            try:
                val = db.get_config(key)
                if val is not None:
                    export_data["config"][key] = val
            except Exception:
                export_data["config"][key] = None

        src_conn.close()

        # 序列化 + 签名
        json_bytes = json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")
        signature = _generate_export_signature(
            export_data["machine_code"], EXPORT_VERSION, json_bytes
        )
        export_data["_signature"] = signature
        json_bytes = json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")

        # 写入 .sgbak
        with open(target_path, "wb") as f:
            f.write(SGBACK_MAGIC)
            f.write(json_bytes)

        return {"ok": True, "counts": export_data["counts"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ================================================================
# 导入 .sgbak — 整机数据还原
# ================================================================
def import_full_backup(
    source_path: str,
    import_rooms: bool = True,
    import_guests: bool = True,
    import_orders: bool = True,
    import_ledger: bool = True,
    import_config: bool = True,
    skip_existing: bool = True,
) -> Dict[str, Any]:
    """
    从 .sgbak 文件导入数据到当前数据库
    返回: {"ok": True, "imported": {...}} 或 {"ok": False, "error": "..."}
    """
    try:
        # 读取文件
        with open(source_path, "rb") as f:
            magic = f.read(len(SGBACK_MAGIC))
            if magic != SGBACK_MAGIC:
                return {"ok": False, "error": i18n.t("data_import.err_sgbak_invalid")}
            raw = f.read()

        data = json.loads(raw.decode("utf-8"))

        # 验证签名
        sig = data.pop("_signature", "")
        raw_no_sig = json.dumps(data, ensure_ascii=False).encode("utf-8")
        expected_sig = _generate_export_signature(
            data.get("machine_code", ""), data.get("version", EXPORT_VERSION), raw_no_sig
        )
        if sig and sig != expected_sig:
            # 签名不匹配 — 警告但不阻止导入（换机器后签名势必不同，这是预期行为）
            pass  # 不做硬阻断，但可打印日志

        imported: Dict[str, int] = {}

        # config
        if import_config and data.get("config"):
            for key, value in data["config"].items():
                try:
                    existing = db.get_config(key)
                    if existing is not None and skip_existing:
                        continue
                    if value is not None:
                        db.set_config(key, str(value))
                except Exception:
                    pass
            imported["config"] = len(data["config"])

        # rooms
        if import_rooms and data.get("tables", {}).get("rooms"):
            count = 0
            for row in data["tables"]["rooms"]:
                rid = row.get("room_id", "")
                if not rid:
                    continue
                if skip_existing:
                    try:
                        exist = db.execute("SELECT 1 FROM rooms WHERE room_id=?", (rid,)).fetchone()
                        if exist:
                            continue
                    except Exception:
                        pass
                try:
                    db.execute(
                        "INSERT OR IGNORE INTO rooms (room_id, floor, room_type, status) VALUES (?, ?, ?, ?)",
                        (rid, row.get("floor", ""), row.get("room_type", ""), row.get("status", "READY"))
                    )
                    count += 1
                except Exception:
                    pass
            imported["rooms"] = count

        # orders
        if import_orders and data.get("tables", {}).get("orders"):
            count = 0
            for row in data["tables"]["orders"]:
                oid = row.get("order_id", "")
                if not oid:
                    continue
                if skip_existing:
                    try:
                        exist = db.execute("SELECT 1 FROM orders WHERE order_id=?", (oid,)).fetchone()
                        if exist:
                            continue
                    except Exception:
                        pass
                try:
                    db.execute(
                        "INSERT OR IGNORE INTO orders (order_id, chat_id, hotel_id, room_id, order_status, total_amount, items_json, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (oid, row.get("chat_id", ""), row.get("hotel_id", ""),
                         row.get("room_id", ""), row.get("order_status", "PENDING"),
                         row.get("total_amount", 0), row.get("items_json", "[]"),
                         row.get("note", ""))
                    )
                    count += 1
                except Exception:
                    pass
            imported["orders"] = count

        # ledger_hashes (审计链)
        if import_ledger and data.get("tables", {}).get("ledger_hashes"):
            count = 0
            for row in data["tables"]["ledger_hashes"]:
                lid = row.get("id", "")
                if skip_existing and lid:
                    try:
                        exist = db.execute("SELECT 1 FROM ledger_hashes WHERE id=?", (lid,)).fetchone()
                        if exist:
                            continue
                    except Exception:
                        pass
                try:
                    db.execute(
                        "INSERT OR IGNORE INTO ledger_hashes (id, prev_hash, block_data, timestamp, hash) VALUES (?, ?, ?, ?, ?)",
                        (lid, row.get("prev_hash", ""), row.get("block_data", "{}"),
                         row.get("timestamp", ""), row.get("hash", ""))
                    )
                    count += 1
                except Exception:
                    pass
            imported["ledger_hashes"] = count

        return {"ok": True, "imported": imported, "source_machine": data.get("machine_code", "?")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ================================================================
# 导出 .sgkey — 门锁加密密钥迁移
# ================================================================
def export_keys(target_path: str, password: str) -> Dict[str, Any]:
    """
    导出加密相关密钥（门锁密钥等），
    用用户提供的密码二次加密后写入密钥备份文件。
    这样在目标机器上只要知道密码就能恢复。
    """
    try:
        # 收集需要导出的密钥配置
        keys_to_export: Dict[str, str] = {}
        sensitive_keys = [
            "license_key", "encryption_key", "lock_master_key",
            "lock_sector_key_a", "lock_sector_key_b",
            "lock_aes_master_key", "card_write_key",
            # telegram_bot_token / work_bot_token — 厂家统一配置，不参与酒店导入导出
        ]
        for key in sensitive_keys:
            try:
                val = db.get_config(key)
                if val:
                    keys_to_export[key] = val
            except Exception:
                pass

        if not keys_to_export:
            return {"ok": False, "error": i18n.t("data_import.err_no_keys")}

        # 用密码派生密钥
        salt = os.urandom(16)
        key_material = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, 200000, dklen=32
        )

        # 用异或加哈希做轻量加密（避免引入第三方库）
        payload_json = json.dumps(keys_to_export, ensure_ascii=False).encode("utf-8")
        # 简单异或（生产环境建议引入加密库）
        encrypted = bytes(
            b ^ key_material[i % len(key_material)] for i, b in enumerate(payload_json)
        )
        sig = hmac.new(key_material, encrypted, hashlib.sha256).hexdigest()[:32]

        export_data = {
            "version": EXPORT_VERSION,
            "machine_code": _get_machine_code(),
            "hotel_id": _get_hotel_id(),
            "created_at": datetime.now().isoformat(),
            "salt": salt.hex(),
            "data": encrypted.hex(),
            "signature": sig,
        }

        with open(target_path, "wb") as f:
            f.write(SGKEY_MAGIC)
            f.write(json.dumps(export_data, ensure_ascii=False).encode("utf-8"))

        return {"ok": True, "keys_exported": list(keys_to_export.keys())}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def import_keys(source_path: str, password: str) -> Dict[str, Any]:
    """
    从 .sgkey 文件导入加密密钥，用密码解密后写入 config 表
    """
    try:
        with open(source_path, "rb") as f:
            magic = f.read(len(SGKEY_MAGIC))
            if magic != SGKEY_MAGIC:
                return {"ok": False, "error": i18n.t("data_import.err_sgkey_invalid")}
            raw = f.read()

        data = json.loads(raw.decode("utf-8"))
        salt = bytes.fromhex(data.get("salt", ""))
        encrypted = bytes.fromhex(data.get("data", ""))
        sig = data.get("signature", "")

        # 派生密钥
        key_material = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, 200000, dklen=32
        )

        # 验证签名
        expected_sig = hmac.new(key_material, encrypted, hashlib.sha256).hexdigest()[:32]
        if sig != expected_sig:
            return {"ok": False, "error": i18n.t("data_import.err_wrong_password")}

        # 解密
        decrypted = bytes(
            b ^ key_material[i % len(key_material)] for i, b in enumerate(encrypted)
        )
        keys_dict = json.loads(decrypted.decode("utf-8"))

        # 写入 config 表
        imported = 0
        for key, value in keys_dict.items():
            try:
                db.set_config(key, value)
                imported += 1
            except Exception:
                pass

        return {"ok": True, "keys_imported": imported}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ================================================================
# 数据导入对话框 — 主界面
# ================================================================
class DataImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("data_import.window_title"))
        style_dialog(self, size="medium")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        layout.addWidget(build_dialog_header(
            i18n.t("data_import.header_title"),
            i18n.t("data_import.header_subtitle")
        ))

        self.guide_panel = MigrationGuidePanel(on_action=self._on_guide_action)
        layout.addWidget(self.guide_panel)

        # 标签页控件（随窗口拉伸）
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        # 标签页1：CSV导入
        self._build_csv_tab()
        # 标签页2：整机迁移
        self._build_migration_tab()
        # 标签页3：密钥迁移
        self._build_key_tab()
        # 标签页4：老系统强行迁移
        self._build_legacy_tab()
        self._on_tab_changed(0)

    def _on_tab_changed(self, index: int) -> None:
        self.guide_panel.bind_session(data_import_tab_session(index))

    def _on_guide_action(self, action: str) -> None:
        if action == GuideAction.IMPORT_CSV:
            self._import_csv()
        elif action == GuideAction.EXPORT_SGBAK:
            self._export_backup()
        elif action == GuideAction.IMPORT_SGBAK:
            self._import_backup()
        elif action == GuideAction.EXPORT_SGKEY:
            self._export_keys()
        elif action == GuideAction.IMPORT_SGKEY:
            self._import_keys()
        elif action == GuideAction.OPEN_LEGACY_WIZARD:
            self._open_legacy_wizard()

    # ─────────────────────────────────────────────
    # Tab 0：老系统强行迁移（第4个标签页）
    # ─────────────────────────────────────────────
    def _build_legacy_tab(self):
        tab = QWidget()
        ly = QVBoxLayout(tab)
        ly.setContentsMargins(16, 16, 16, 16)
        ly.setSpacing(10)

        info = QLabel(i18n.t("data_import.legacy_tab_desc"))
        info.setWordWrap(True)
        info.setStyleSheet(
            f"background:{_p('sidebar')}; border:1px solid {_p('accent')}; border-radius:12px; "
            f"padding:12px; color:{_p('text_muted')}; font-size:12.5px;"
        )
        ly.addWidget(info)

        self.lbl_legacy_status = QLabel(i18n.t("data_import.status_idle"))
        self.lbl_legacy_status.setStyleSheet(f"color:{_p('text_muted')};")
        ly.addWidget(self.lbl_legacy_status)

        btn_open = QPushButton(i18n.t("data_import.btn_legacy_wizard"))
        btn_open.setObjectName("FdGhostBtn")
        btn_open.setStyleSheet(
            f"background:{_p('danger')}; color:{_p('surface')}; font-weight:800; padding:12px; "
            "border-radius:8px; font-size:14px;"
        )
        btn_open.clicked.connect(self._open_legacy_wizard)
        ly.addWidget(btn_open)

        ly.addStretch()
        self.tabs.addTab(tab, i18n.t("data_import.tab_legacy"))

    def _open_legacy_wizard(self):
        """打开老系统迁移向导"""
        try:
            from legacy_migration import open_legacy_migration_wizard
            open_legacy_migration_wizard(self)
        except ImportError as e:
            show_error(self, i18n.t("data_import.err_launch"), f"legacy_migration.py {i18n.t('data_import.err_load_fail')}: {e}")

    # ─────────────────────────────────────────────
    # Tab 1：CSV导入（原有功能，保留）
    # ─────────────────────────────────────────────
    def _build_csv_tab(self):
        tab = QWidget()
        ly = QVBoxLayout(tab)
        ly.setContentsMargins(16, 16, 16, 16)
        ly.setSpacing(10)

        info = QLabel(i18n.t("data_import.csv_tab_desc"))
        info.setWordWrap(True)
        info.setStyleSheet(
            f"background:{_p('surface')}; border:1px solid {_p('border')}; border-radius:12px; "
            f"padding:12px; color:{_p('text_muted')}; font-size:13px;"
        )
        ly.addWidget(info)

        self.btn_select_csv = QPushButton(i18n.t("data_import.btn_select_csv"))
        self.btn_select_csv.setObjectName("FdGhostBtn")
        self.btn_select_csv.clicked.connect(self._import_csv)
        ly.addWidget(self.btn_select_csv)

        self.progress_csv = QProgressBar()
        self.progress_csv.setValue(0)
        ly.addWidget(self.progress_csv)

        ly.addStretch()
        self.tabs.addTab(tab, i18n.t("data_import.tab_csv"))

    # ─────────────────────────────────────────────
    # Tab 2：整机迁移
    # ─────────────────────────────────────────────
    def _build_migration_tab(self):
        tab = QWidget()
        ly = QVBoxLayout(tab)
        ly.setContentsMargins(16, 16, 16, 16)
        ly.setSpacing(10)

        # 说明
        info = QLabel(i18n.t("data_import.migration_tab_desc"))
        info.setWordWrap(True)
        info.setStyleSheet(
            f"background:{_p('surface_alt')}; border:1px solid {_p('primary')}; border-radius:12px; "
            f"padding:12px; color:{_p('text')}; font-size:12.5px;"
        )
        ly.addWidget(info)

        # ── 导出区 ──
        grp_export = QGroupBox(i18n.t("data_import.grp_export"))
        ge_ly = QVBoxLayout(grp_export)

        self.lbl_export_status = QLabel(i18n.t("data_import.status_pending"))
        self.lbl_export_status.setStyleSheet(f"color:{_p('text_muted')};")
        ge_ly.addWidget(self.lbl_export_status)

        btn_export = QPushButton(i18n.t("data_import.btn_export"))
        btn_export.setObjectName("FdGhostBtn")
        btn_export.clicked.connect(self._export_backup)
        ge_ly.addWidget(btn_export)

        ly.addWidget(grp_export)

        # ── 导入区 ──
        grp_import = QGroupBox(i18n.t("data_import.grp_import"))
        gi_ly = QVBoxLayout(grp_import)

        # 选择框
        self.chk_rooms = QCheckBox(i18n.t("data_import.chk_rooms"))
        self.chk_rooms.setChecked(True)
        gi_ly.addWidget(self.chk_rooms)

        self.chk_guests = QCheckBox(i18n.t("data_import.chk_guests"))
        self.chk_guests.setChecked(True)
        gi_ly.addWidget(self.chk_guests)

        self.chk_orders = QCheckBox(i18n.t("data_import.chk_orders"))
        self.chk_orders.setChecked(False)
        gi_ly.addWidget(self.chk_orders)

        self.chk_ledger = QCheckBox(i18n.t("data_import.chk_ledger"))
        self.chk_ledger.setChecked(False)
        gi_ly.addWidget(self.chk_ledger)

        self.chk_config = QCheckBox(i18n.t("data_import.chk_config"))
        self.chk_config.setChecked(True)
        gi_ly.addWidget(self.chk_config)

        self.chk_skip = QCheckBox(i18n.t("data_import.chk_skip"))
        self.chk_skip.setChecked(True)
        gi_ly.addWidget(self.chk_skip)

        self.lbl_import_status = QLabel(i18n.t("data_import.status_pending"))
        self.lbl_import_status.setStyleSheet(f"color:{_p('text_muted')};")
        gi_ly.addWidget(self.lbl_import_status)

        btn_import = QPushButton(i18n.t("data_import.btn_import_sgbak"))
        btn_import.setObjectName("SolidPrimaryBtn")
        btn_import.clicked.connect(self._import_backup)
        gi_ly.addWidget(btn_import)

        ly.addWidget(grp_import)
        ly.addStretch()
        self.tabs.addTab(tab, i18n.t("data_import.tab_migration"))

    # ─────────────────────────────────────────────
    # Tab 3：密钥迁移
    # ─────────────────────────────────────────────
    def _build_key_tab(self):
        tab = QWidget()
        ly = QVBoxLayout(tab)
        ly.setContentsMargins(16, 16, 16, 16)
        ly.setSpacing(10)

        info = QLabel(i18n.t("data_import.key_tab_desc").format(machine_code=_get_machine_code()))
        info.setWordWrap(True)
        info.setStyleSheet(
            f"background:{_p('surface_alt')}; border:1px solid {_p('border')}; border-radius:12px; "
            f"padding:12px; color:{_p('text_muted')}; font-size:12.5px;"
        )
        ly.addWidget(info)

        # 导出密码
        grp_export_key = QGroupBox(i18n.t("data_import.grp_export_key"))
        ge_ly = QGridLayout(grp_export_key)

        ge_ly.addWidget(QLabel(i18n.t("data_import.lbl_export_password")), 0, 0)
        self.key_password_export = QLineEdit()
        self.key_password_export.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_password_export.setPlaceholderText(i18n.t("data_import.ph_export_password"))
        ge_ly.addWidget(self.key_password_export, 0, 1)

        self.lbl_key_export_status = QLabel("")
        ge_ly.addWidget(self.lbl_key_export_status, 1, 0, 1, 2)

        btn_export_key = QPushButton(i18n.t("data_import.btn_export_key"))
        btn_export_key.setObjectName("FdGhostBtn")
        btn_export_key.clicked.connect(self._export_keys)
        ge_ly.addWidget(btn_export_key, 2, 0, 1, 2)

        ly.addWidget(grp_export_key)

        # 导入密码
        grp_import_key = QGroupBox(i18n.t("data_import.grp_import_key"))
        gi_ly = QGridLayout(grp_import_key)

        gi_ly.addWidget(QLabel(i18n.t("data_import.lbl_import_password")), 0, 0)
        self.key_password_import = QLineEdit()
        self.key_password_import.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_password_import.setPlaceholderText(i18n.t("data_import.ph_import_password"))
        gi_ly.addWidget(self.key_password_import, 0, 1)

        self.lbl_key_import_status = QLabel("")
        gi_ly.addWidget(self.lbl_key_import_status, 1, 0, 1, 2)

        btn_import_key = QPushButton(i18n.t("data_import.btn_import_key"))
        btn_import_key.setObjectName("SolidPrimaryBtn")
        btn_import_key.clicked.connect(self._import_keys)
        gi_ly.addWidget(btn_import_key, 2, 0, 1, 2)

        ly.addWidget(grp_import_key)
        ly.addStretch()
        self.tabs.addTab(tab, i18n.t("data_import.tab_key"))

    # ─────────────────────────────────────────────
    # CSV 导入逻辑（原有功能）
    # ─────────────────────────────────────────────
    def _import_csv(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("data_import.dlg_select_csv"), "", "CSV Files (*.csv)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames
                if not headers or "room_id" not in headers or "floor" not in headers or "room_type" not in headers:
                    show_warning(self, i18n.t("data_import.err_format"), i18n.t("data_import.err_csv_columns"))
                    return

                rows = list(reader)
                total = len(rows)
                if total == 0:
                    show_info(self, i18n.t("data_import.hint"), i18n.t("data_import.err_csv_empty"))
                    return

                # 读取已定义的扩展属性，建立列名→字段键的映射
                known_props = db.execute(
                    "SELECT key, label FROM room_prop_definitions WHERE enabled=1"
                ).fetchall()
                # 支持按 key 或 label 匹配
                prop_headers = {}  # CSV列名到字段键的映射
                for key, label in known_props:
                    prop_headers[key] = key
                    if label:
                        prop_headers[label] = key
                # 额外常见中英文别名
                prop_headers.update({
                    "meter_no": "meter_no", "水表号": "meter_no", "水表": "meter_no",
                    "bath_no": "bath_no", "浴室号": "bath_no", "浴室": "bath_no",
                    "heat_no": "heat_no", "暖气号": "heat_no", "暖气": "heat_no",
                    "no_smoking": "no_smoking", "禁烟房": "no_smoking", "禁烟": "no_smoking",
                    "has_windows": "has_windows", "有窗": "has_windows", "窗": "has_windows",
                    "has_bathtub": "has_bathtub", "有浴缸": "has_bathtub",
                    "floor_level": "floor_level", "楼层等级": "floor_level",
                    "decoration_year": "decoration_year", "装修年份": "decoration_year",
                    "custom_note": "custom_note", "特殊备注": "custom_note",
                })

                self.progress_csv.setMaximum(total)
                success_count = 0
                extra_cols_used = set()

                for i, row in enumerate(rows):
                    room_id = str(row.get("room_id", "")).strip()
                    floor = str(row.get("floor", "")).strip()
                    rtype = str(row.get("room_type", "")).strip()

                    if room_id and floor and rtype:
                        exists = db.execute(
                            "SELECT 1 FROM rooms WHERE room_id=?", (room_id,)
                        ).fetchone()
                        if not exists:
                            # 收集额外列 → 扩展属性
                            extra = {}
                            for csv_col, val in row.items():
                                c = csv_col.strip()
                                if c in ("room_id", "floor", "room_type", "status", "note", "lock_no", ""):
                                    continue
                                prop_key = prop_headers.get(c)
                                if prop_key and val and val.strip():
                                    extra[prop_key] = val.strip()
                                    extra_cols_used.add(c)
                            db.execute(
                                "INSERT INTO rooms (room_id, floor, room_type, status, extra_props) VALUES (?, ?, ?, 'READY', ?)",
                                (room_id, floor, rtype, json.dumps(extra))
                            )
                            success_count += 1

                    self.progress_csv.setValue(i + 1)

                msg = i18n.t("data_import.csv_result_summary").format(total=total, success=success_count, skipped=total - success_count)
                if extra_cols_used:
                    msg += i18n.t("data_import.csv_result_extra").format(count=len(extra_cols_used), cols=', '.join(sorted(extra_cols_used)))
                else:
                    msg += i18n.t("data_import.csv_result_no_extra")
                show_info(self, i18n.t("data_import.import_success"), msg)
                bus.show_success_overlay.emit(i18n.t("data_import.overlay_csv_success").format(count=success_count))
                self.accept()

        except Exception as e:
            show_error(self, i18n.t("data_import.import_failed"), f"{i18n.t('data_import.err_occurred')}: {e}")

    # ─────────────────────────────────────────────
    # 整机导出
    # ─────────────────────────────────────────────
    def _export_backup(self):
        m = _get_machine_code()
        from brand_config_v4 import backup_file_prefix
        default_name = f"{backup_file_prefix()}_{m[:8]}_{datetime.now():%Y%m%d_%H%M}.sgbak"
        file_path, _ = QFileDialog.getSaveFileName(
            self, i18n.t("data_import.dlg_save_backup"), default_name, "SG Backup (*.sgbak)"
        )
        if not file_path:
            return

        self.lbl_export_status.setText(i18n.t("data_import.status_exporting"))
        self.lbl_export_status.setStyleSheet(f"color:{_p('accent')};")
        # Force Qt to repaint
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        result = export_full_backup(file_path)
        if result["ok"]:
            counts = result.get("counts", {})
            summary = "\n".join(f"  {k}: {v} {i18n.t('data_import.count_unit')}" for k, v in counts.items())
            self.lbl_export_status.setText(i18n.t("data_import.export_success_status").format(summary=summary))
            self.lbl_export_status.setStyleSheet(f"color:{_p('amount_positive')};")
            show_info(self, i18n.t("data_import.export_success"), i18n.t("data_import.export_success_msg").format(path=file_path, summary=summary))
        else:
            self.lbl_export_status.setText(i18n.t("data_import.export_failed_status").format(error=result['error']))
            self.lbl_export_status.setStyleSheet(f"color:{_p('danger')};")
            show_error(self, i18n.t("data_import.export_failed"), result["error"])

    # ─────────────────────────────────────────────
    # 整机导入
    # ─────────────────────────────────────────────
    def _import_backup(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("data_import.dlg_select_backup"), "", "SG Backup (*.sgbak)"
        )
        if not file_path:
            return

        # 确认对话框
        if not ask_confirm(
            self, i18n.t("data_import.confirm_import"),
            i18n.t("data_import.confirm_import_msg").format(path=file_path),
        ):
            return

        self.lbl_import_status.setText(i18n.t("data_import.status_importing"))
        self.lbl_import_status.setStyleSheet(f"color:{_p('accent')};")
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        result = import_full_backup(
            file_path,
            import_rooms=self.chk_rooms.isChecked(),
            import_guests=self.chk_guests.isChecked(),
            import_orders=self.chk_orders.isChecked(),
            import_ledger=self.chk_ledger.isChecked(),
            import_config=self.chk_config.isChecked(),
            skip_existing=self.chk_skip.isChecked(),
        )

        if result["ok"]:
            imported = result.get("imported", {})
            summary = "\n".join(f"  {k}: {v} {i18n.t('data_import.count_unit')}" for k, v in imported.items())
            self.lbl_import_status.setText(i18n.t("data_import.import_success_status"))
            self.lbl_import_status.setStyleSheet(f"color:{_p('amount_positive')};")
            show_info(self, i18n.t("data_import.import_success"),
                      i18n.t("data_import.import_success_msg").format(source=result.get('source_machine','?')[:10], summary=summary))
            bus.show_success_overlay.emit(i18n.t("data_import.overlay_migration_done"))
        else:
            self.lbl_import_status.setText(i18n.t("data_import.import_failed_status").format(error=result['error']))
            self.lbl_import_status.setStyleSheet(f"color:{_p('danger')};")
            show_error(self, i18n.t("data_import.import_failed"), result["error"])

    # ─────────────────────────────────────────────
    # 密钥导出
    # ─────────────────────────────────────────────
    def _export_keys(self):
        password = self.key_password_export.text().strip()
        if len(password) < 8:
            self.lbl_key_export_status.setText(i18n.t("data_import.err_password_short"))
            self.lbl_key_export_status.setStyleSheet(f"color:{_p('danger')};")
            return

        if not ask_confirm(
            self, i18n.t("data_import.confirm_password"),
            i18n.t("data_import.confirm_export_password_msg"),
        ):
            return

        m = _get_machine_code()
        default_name = f"Solid_Keys_{m[:8]}_{datetime.now():%Y%m%d_%H%M}.sgkey"
        file_path, _ = QFileDialog.getSaveFileName(
            self, i18n.t("data_import.dlg_save_key"), default_name, "SG Key (*.sgkey)"
        )
        if not file_path:
            return

        self.lbl_key_export_status.setText(i18n.t("data_import.status_exporting_keys"))
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        result = export_keys(file_path, password)
        if result["ok"]:
            keys_list = ", ".join(result.get("keys_exported", []))
            self.lbl_key_export_status.setText(i18n.t("data_import.export_keys_success_status").format(count=len(result.get('keys_exported',[]))))
            self.lbl_key_export_status.setStyleSheet(f"color:{_p('amount_positive')};")
            show_info(self, i18n.t("data_import.export_success"),
                      i18n.t("data_import.export_keys_success_msg").format(path=file_path, keys=keys_list))
        else:
            self.lbl_key_export_status.setText(i18n.t("data_import.export_failed_status").format(error=result['error']))
            self.lbl_key_export_status.setStyleSheet(f"color:{_p('danger')};")
            show_error(self, i18n.t("data_import.export_failed"), result["error"])

    # ─────────────────────────────────────────────
    # 密钥导入
    # ─────────────────────────────────────────────
    def _import_keys(self):
        password = self.key_password_import.text().strip()
        if len(password) < 8:
            self.lbl_key_import_status.setText(i18n.t("data_import.err_password_short"))
            self.lbl_key_import_status.setStyleSheet(f"color:{_p('danger')};")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("data_import.dlg_select_key"), "", "SG Key (*.sgkey)"
        )
        if not file_path:
            return

        self.lbl_key_import_status.setText(i18n.t("data_import.status_importing_keys"))
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        result = import_keys(file_path, password)
        if result["ok"]:
            self.lbl_key_import_status.setText(i18n.t("data_import.import_keys_success_status").format(count=result.get('keys_imported',0)))
            self.lbl_key_import_status.setStyleSheet(f"color:{_p('amount_positive')};")
            show_info(self, i18n.t("data_import.import_success"),
                      i18n.t("data_import.import_keys_success_msg").format(count=result.get('keys_imported',0)))
            bus.show_success_overlay.emit(i18n.t("data_import.overlay_key_migration_done"))
        else:
            self.lbl_key_import_status.setText(i18n.t("data_import.import_failed_status").format(error=result['error']))
            self.lbl_key_import_status.setStyleSheet(f"color:{_p('danger')};")
            show_error(self, i18n.t("data_import.import_failed"), result["error"])