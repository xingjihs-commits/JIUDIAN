"""
db_access/migration_db.py — 数据库迁移操作

从 database.py 拆出:
- _init_new_tables  (156行)
- _migrate          (96行)
"""

from __future__ import annotations
import logging
import hashlib
from typing import Any

logger = logging.getLogger(__name__)


def run_init_new_tables(db: Any) -> None:
    """Non-CREATE TABLE migrations (ALTER TABLE, CREATE INDEX, etc.)."""
    # card_records 迁移
    _cr_cols = _table_has_columns(db, "card_records")
    _cr_new = [
        ("guest_name", "TEXT DEFAULT ''"),
        ("operator_id", "TEXT DEFAULT ''"),
        ("registry_kind", "TEXT DEFAULT 'guest'"),
        ("sequence", "INTEGER DEFAULT 0"),
        ("booking_id", "INTEGER DEFAULT NULL"),
        ("physical_blacklist_card_id", "TEXT DEFAULT ''"),
    ]
    for _col, _typ in _cr_new:
        if _col not in _cr_cols:
            try:
                db.execute(f"ALTER TABLE card_records ADD COLUMN {_col} {_typ}")
            except Exception:
                logger.exception("card_records 迁移 fail")

    # bot_subscribers 迁移
    _bot_cols = _table_has_columns(db, "bot_subscribers")
    _bot_new = [
        ("room_id", "TEXT DEFAULT ''"),
        ("subscribed_at", "TIMESTAMP"),
        ("last_active", "TIMESTAMP"),
    ]
    for _col, _typ in _bot_new:
        if _col not in _bot_cols:
            try:
                db.execute(f"ALTER TABLE bot_subscribers ADD COLUMN {_col} {_typ}")
            except Exception:
                logger.exception("bot_subscribers 迁移 fail")

    # member_consumption 表
    try:
        db.execute("CREATE TABLE IF NOT EXISTS member_consumption ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, member_id INTEGER, room_id TEXT, "
            "amount REAL, points_earned INTEGER DEFAULT 0, checkin_date TEXT, "
            "checkout_date TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "FOREIGN KEY(member_id) REFERENCES members(id))")
    except Exception:
        logger.exception("member_consumption 创建 fail")

    # legacy 索引
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_legacy_open_card ON legacy_open_records(card_no)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_legacy_open_room ON legacy_open_records(room_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_legacy_open_time ON legacy_open_records(open_time)")
    except Exception:
        logger.exception("legacy_open 索引 fail")
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_legacy_op_action_user ON legacy_operator_actions(gonghao)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_legacy_op_action_time ON legacy_operator_actions(happened_at)")
    except Exception:
        logger.exception("legacy_op 索引 fail")

    # guest_service_requests 迁移
    _gsr_cols = _table_has_columns(db, "guest_service_requests")
    _gsr_new = [
        ("request_id", "TEXT"), ("chat_id", "TEXT DEFAULT ''"),
        ("service_type", "TEXT"), ("request_type", "TEXT"),
        ("source", "TEXT DEFAULT 'telegram'"), ("handler_id", "TEXT DEFAULT ''"),
        ("operator_id", "TEXT DEFAULT ''"),
    ]
    for _col, _typ in _gsr_new:
        if _col not in _gsr_cols:
            try:
                db.execute(f"ALTER TABLE guest_service_requests ADD COLUMN {_col} {_typ}")
            except Exception:
                logger.exception("gsr 迁移 fail")

    # local_reservations 迁移
    _lr_cols = _table_has_columns(db, "local_reservations")
    _lr_new = [("source", "TEXT DEFAULT 'frontdesk'"), ("note", "TEXT DEFAULT ''"), ("updated_at", "TIMESTAMP")]
    for _col, _typ in _lr_new:
        if _col not in _lr_cols:
            try:
                db.execute(f"ALTER TABLE local_reservations ADD COLUMN {_col} {_typ}")
            except Exception:
                logger.exception("lr 迁移 fail")

    # borrowed_items
    try:
        db.execute("CREATE TABLE IF NOT EXISTS borrowed_items ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT, item_type TEXT NOT NULL, "
            "qty INTEGER DEFAULT 1, qty_returned INTEGER DEFAULT 0, note TEXT, "
            "borrowed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, returned_at TIMESTAMP)")
    except Exception:
        logger.exception("borrowed_items 创建 fail")

    # door_open_audit 索引
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_door_audit_card ON door_open_audit(card_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_door_audit_card_room ON door_open_audit(card_id, room_id)")
    except Exception:
        logger.exception("door_audit 索引 fail")

    # shop_items emoji
    if "emoji" not in _table_has_columns(db, "shop_items"):
        try:
            db.execute("ALTER TABLE shop_items ADD COLUMN emoji TEXT DEFAULT ''")
        except Exception:
            logger.exception("shop_items emoji fail")

    # C0-beta 索引
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_inv_mv_item ON inventory_movements(item_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_inv_mv_time ON inventory_movements(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_inv_lines_session ON inventory_stocktake_lines(session_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rtcs_type ON room_type_consumable_standards(type_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_energy_meter_time ON energy_meter_readings(meter_id, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_energy_period_time ON energy_periods(started_at, finished_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ledger_created ON ledger(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rooms_status ON rooms(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rooms_floor ON rooms(floor)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_cards_issue_time ON card_records(issue_time)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_guests_room ON guests(room_id, status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ledger_room_id ON ledger(room_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ledger_tx_type ON ledger(tx_type)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_reservations_guest ON local_reservations(guest_name)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_reservations_checkin ON local_reservations(checkin_dt)")
    except Exception:
        logger.exception("C0 索引 fail")

    # admin 默认账号
    try:
        existing = db.execute("SELECT id FROM staff_accounts WHERE username='admin'").fetchone()
        if not existing:
            from permission_system import VENDOR_PASSWORD
            pw_hash = hashlib.sha256(VENDOR_PASSWORD.encode()).hexdigest()
            db.execute(
                "INSERT INTO staff_accounts (username, password_hash, display_name, role) VALUES (?,?,?,?)",
                ("admin", pw_hash, "系统管理员", "boss"))
    except Exception as e:
        logger.warning("admin 账号 init: %s", e)


def run_migrate(db: Any) -> None:
    """Safely add columns that may be missing (ALTER TABLE based on PRAGMA table_info)."""
    migrations = [
        ("ledger", "pay_method", "TEXT DEFAULT 'CASH'"),
        ("ledger", "is_deposit", "INTEGER DEFAULT 0"),
        ("ledger", "prev_hash", "TEXT"),
        ("ledger", "current_hash", "TEXT"),
        ("room_type_templates", "default_deposit", "REAL"),
        ("room_type_templates", "price_walk_in", "REAL"),
        ("room_type_templates", "price_contract", "REAL"),
        ("room_type_templates", "price_member", "REAL"),
        ("room_type_templates", "cleaning_fee", "REAL DEFAULT NULL"),
        ("room_type_templates", "hk_consumables_deep_json", "TEXT"),
        ("room_type_templates", "icon", "TEXT DEFAULT ''"),
        ("staff_roster", "base_salary", "REAL DEFAULT 0"),
        ("staff_roster", "telegram_route", "TEXT DEFAULT 'inherit'"),
        ("guests", "checkout_time", "TIMESTAMP"),
        ("energy_audit", "note", "TEXT"),
        ("energy_audit", "reading_mode", "TEXT"),
        ("shop_items", "category", "TEXT DEFAULT ''"),
        ("shop_items", "cost_price", "REAL DEFAULT 0"),
        ("shop_items", "pack_label", "TEXT DEFAULT '箱'"),
        ("shop_items", "units_per_pack", "INTEGER DEFAULT 1"),
        ("shop_items", "listed", "INTEGER DEFAULT 1"),
        ("shop_items", "telegram_file_id", "TEXT DEFAULT ''"),
        ("shop_items", "sort_order", "INTEGER DEFAULT 9999"),
        ("shop_items", "telegram_label", "TEXT DEFAULT ''"),
        ("shop_items", "icon_key", "TEXT DEFAULT ''"),
        ("shop_items", "description", "TEXT DEFAULT ''"),
        ("folio_items", "sku", "TEXT DEFAULT ''"),
        ("folio_items", "employee_id", "TEXT DEFAULT ''"),
        ("staff_roster", "chat_id", "TEXT DEFAULT ''"),
        ("staff_roster", "telegram_enabled", "INTEGER DEFAULT 1"),
        ("staff_roster", "punch_enabled", "INTEGER DEFAULT 1"),
        ("staff_roster", "position", "TEXT DEFAULT ''"),
        ("staff_roster", "vk_profile_id", "TEXT DEFAULT ''"),
        ("staff_roster", "language", "TEXT DEFAULT 'zh'"),
        ("rooms", "last_card_no", "INTEGER DEFAULT 0"),
        ("rooms", "last_seq", "INTEGER DEFAULT 0"),
        ("rooms", "last_dls_co_id", "INTEGER DEFAULT 1"),
        ("rooms", "last_fingerprint", "TEXT DEFAULT ''"),
        ("rooms", "rate_override", "REAL DEFAULT NULL"),
        ("rooms", "staff_room", "INTEGER DEFAULT 0"),
    ]
    for table, col, typ in migrations:
        cols = _table_has_columns(db, table)
        if col not in cols:
            try:
                db.execute(f"ALTER TABLE [{table}] ADD COLUMN [{col}] {typ}")
            except Exception:
                logger.exception("migrate %s.%s fail", table, col)


def _table_has_columns(db: Any, table: str) -> set:
    """PRAGMA table_info 获取已有列名集合。"""
    from database import _ALLOWED_TABLES, _validate_identifier
    try:
        safe_table = _validate_identifier(table, _ALLOWED_TABLES)
        rows = db.execute(f"PRAGMA table_info({safe_table})").fetchall()
        return {r[1] for r in rows}
    except Exception:
        return set()
