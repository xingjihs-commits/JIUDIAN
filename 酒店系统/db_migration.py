"""
数据库迁移引擎。支持向上迁移，不支持回退。

用法：
    from db_migration import run_migrations
    run_migrations(db_instance)

迁移列表按版本号递增。每个迁移包含：
- version: 递增版本号
- description: 迁移描述
- sql: 要执行的 SQL 语句列表
- check (可选): 检查 SQL，用于判断是否已应用
- 检查异常表示完成（可选）：真值表示查抛异常等于尚未迁移
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


MIGRATIONS: list[dict[str, Any]] = [
    {
        "version": 1,
        "description": "初始库 (建库时默认版本)",
        "sql": [],
    },
    {
        "version": 2,
        "description": "新增 pricing_rules 表",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS pricing_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_type TEXT NOT NULL,
                base_price REAL NOT NULL DEFAULT 100,
                hourly_price REAL DEFAULT 50,
                hourly_first_hours INTEGER DEFAULT 3,
                hourly_extend_price REAL DEFAULT 20,
                overtime_rate REAL DEFAULT 0.5,
                late_checkout_hour INTEGER DEFAULT 14,
                early_checkin_hour INTEGER DEFAULT 10,
                discount_silver REAL DEFAULT 0.95,
                discount_gold REAL DEFAULT 0.90,
                discount_diamond REAL DEFAULT 0.80,
                currency TEXT DEFAULT 'RMB',
                is_active INTEGER DEFAULT 1,
                UNIQUE(room_type)
            )
            """,
        ],
        "check": "SELECT name FROM sqlite_master WHERE type='table' AND name='pricing_rules'",
    },
    {
        "version": 3,
        "description": "新增 members 表",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT,
                id_card TEXT,
                level TEXT DEFAULT 'bronze',
                points REAL DEFAULT 0,
                total_spent REAL DEFAULT 0,
                join_date TEXT,
                remark TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ],
        "check": "SELECT name FROM sqlite_master WHERE type='table' AND name='members'",
    },
    {
        "version": 4,
        "description": "rooms 表新增 rate_override 字段",
        "sql": [
            "ALTER TABLE rooms ADD COLUMN rate_override REAL DEFAULT 0",
        ],
        "check": "SELECT rate_override FROM rooms LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        "version": 5,
        "description": "rooms 表新增 lock_no 字段 (兼容门锁系统)",
        "sql": [
            "ALTER TABLE rooms ADD COLUMN lock_no TEXT DEFAULT ''",
        ],
        "check": "SELECT lock_no FROM rooms LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        "version": 6,
        "description": "rooms 表新增 deposit 押金字段",
        "sql": [
            "ALTER TABLE rooms ADD COLUMN deposit REAL DEFAULT 0",
        ],
        "check": "SELECT deposit FROM rooms LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        "version": 7,
        "description": "rooms 表新增 floor/building 字段",
        "sql": [
            "ALTER TABLE rooms ADD COLUMN floor TEXT DEFAULT ''",
            "ALTER TABLE rooms ADD COLUMN building TEXT DEFAULT ''",
        ],
        "check": "SELECT floor FROM rooms LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        "version": 8,
        "description": "rooms 表新增 room_type 字段",
        "sql": [
            "ALTER TABLE rooms ADD COLUMN room_type TEXT DEFAULT 'STD'",
        ],
        "check": "SELECT room_type FROM rooms LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        "version": 9,
        "description": "rooms 表新增 max_guests 字段（status 由 _migrate 单独处理）",
        "sql": [
            "ALTER TABLE rooms ADD COLUMN max_guests INTEGER DEFAULT 2",
        ],
        "check": "SELECT max_guests FROM rooms LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        "version": 10,
        "description": "card_records 表新增 card_label 字段",
        "sql": [
            "ALTER TABLE card_records ADD COLUMN card_label TEXT DEFAULT ''",
        ],
        "check": "SELECT card_label FROM card_records LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        "version": 11,
        "description": "ledger 表索引优化",
        "sql": [
            "CREATE INDEX IF NOT EXISTS idx_ledger_room_id ON ledger(room_id)",
            "CREATE INDEX IF NOT EXISTS idx_ledger_tx_type ON ledger(tx_type)",
            "CREATE INDEX IF NOT EXISTS idx_ledger_created_at ON ledger(created_at)",
        ],
        "check": "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_ledger_room_id'",
    },
    {
        "version": 12,
        "description": "guests 表索引",
        "sql": [
            "CREATE INDEX IF NOT EXISTS idx_guests_room_id ON guests(room_id)",
            "CREATE INDEX IF NOT EXISTS idx_guests_status ON guests(status)",
        ],
        "check": "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_guests_room_id'",
    },
    {
        "version": 13,
        "description": "订单/服务请求表",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                chat_id TEXT DEFAULT '',
                hotel_id TEXT DEFAULT '',
                room_id TEXT,
                order_status TEXT DEFAULT 'PENDING',
                total_amount REAL DEFAULT 0,
                items_json TEXT DEFAULT '[]',
                note TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS guest_service_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT,
                request_type TEXT,
                note TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ],
        "check": "SELECT name FROM sqlite_master WHERE type='table' AND name='orders'",
    },
    {
        "version": 14,
        "description": "库存/能耗相关表",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS inventory_items (
                sku TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT,
                unit TEXT DEFAULT '个',
                current_stock REAL DEFAULT 0,
                min_stock REAL DEFAULT 0,
                price REAL DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS energy_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                meter_id TEXT,
                reading REAL,
                reading_date TEXT,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ],
        "check": "SELECT name FROM sqlite_master WHERE type='table' AND name='inventory_items'",
    },
    {
        "version": 15,
        "description": "rooms 表新增 extra_props + room_prop_definitions 表 + buildings 表",
        "sql": [
            "ALTER TABLE rooms ADD COLUMN extra_props TEXT DEFAULT '{}'",
            """
            CREATE TABLE IF NOT EXISTS room_prop_definitions (
                key TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                field_type TEXT DEFAULT 'text',
                options TEXT,
                sort_order INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS buildings (
                building_id TEXT PRIMARY KEY,
                bld_no INTEGER NOT NULL DEFAULT 1,
                name TEXT NOT NULL DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ],
        "check": "SELECT extra_props FROM rooms LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        "version": 16,
        "description": "ledger 关联字段 + adjustments_audit 表",
        "sql": [
            "ALTER TABLE ledger ADD COLUMN checkin_id TEXT",
            "ALTER TABLE ledger ADD COLUMN reference_no TEXT",
            "ALTER TABLE ledger ADD COLUMN order_id TEXT",
            """
            CREATE TABLE IF NOT EXISTS adjustments_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ledger_tx_id TEXT,
                original_amount REAL NOT NULL,
                new_amount REAL NOT NULL,
                reason TEXT,
                operator_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ],
        "check": "SELECT checkin_id FROM ledger LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        "version": 17,
        "description": "退款流程表 refunds / refund_lines / refund_audit_log",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS refunds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refund_id TEXT UNIQUE NOT NULL,
                room_id TEXT NOT NULL,
                guest_id INTEGER,
                original_tx_id TEXT UNIQUE,
                original_amount REAL NOT NULL,
                refund_amount REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                refund_reason TEXT NOT NULL,
                status TEXT DEFAULT 'PENDING',
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                requested_by TEXT,
                approved_at TIMESTAMP,
                approved_by TEXT,
                reject_reason TEXT,
                completed_at TIMESTAMP,
                payment_method TEXT,
                reference_number TEXT,
                note TEXT DEFAULT ''
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS refund_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refund_id TEXT NOT NULL,
                line_type TEXT,
                description TEXT,
                amount REAL,
                quantity INTEGER DEFAULT 1,
                unit_price REAL,
                reason TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS refund_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refund_id TEXT,
                from_status TEXT,
                to_status TEXT,
                action_by TEXT,
                action_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                comment TEXT
            )
            """,
        ],
        "check": "SELECT name FROM sqlite_master WHERE type='table' AND name='refunds'",
    },
    {
        "version": 18,
        "description": "bill_details 账单明细",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS bill_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_tx_id TEXT,
                item_type TEXT,
                description TEXT,
                quantity REAL DEFAULT 1,
                unit_price REAL DEFAULT 0,
                total REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ],
        "check": "SELECT name FROM sqlite_master WHERE type='table' AND name='bill_details'",
    },
    {
        "version": 19,
        "description": "exchange_rates 汇率历史",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS exchange_rates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_currency TEXT NOT NULL,
                to_currency TEXT NOT NULL,
                rate REAL NOT NULL,
                effective_date TEXT NOT NULL,
                source TEXT DEFAULT ''
            )
            """,
        ],
        "check": "SELECT name FROM sqlite_master WHERE type='table' AND name='exchange_rates'",
    },
    {
        "version": 20,
        "description": "guests.booking_id + ota_bookings",
        "sql": [
            "ALTER TABLE guests ADD COLUMN booking_id INTEGER",
            """
            CREATE TABLE IF NOT EXISTS ota_bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_no TEXT,
                check_in TEXT,
                check_out TEXT,
                guest_name TEXT,
                status TEXT DEFAULT 'confirmed',
                payload_json TEXT DEFAULT ''
            )
            """,
        ],
        "check": "SELECT booking_id FROM guests LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        "version": 21,
        "description": "deposit_transactions 押金流水",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS deposit_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checkin_id INTEGER NOT NULL,
                txn_type TEXT NOT NULL,
                amount REAL NOT NULL,
                operator_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ],
        "check": "SELECT name FROM sqlite_master WHERE type='table' AND name='deposit_transactions'",
    },
    {
        # [sub-a] 财务闭环迁移：
        # 1. ledger 增加 exchange_rate / currency 列（旧库可能缺 currency）
        # 2. folio_items 增加 bill_id 列（账单无头问题）
        # 3. 新建 bill_headers / payment_records 表（如果 db_schema 未创建）
        # 4. staff_accounts 增加 must_change_password 列（强制改密 B7）
        # 所有 ALTER TABLE 容忍已存在列（框架已 try/except，但仍冗余加 PRAGMA 容错）
        "version": 22,
        "description": "财务闭环: ledger 汇率列 + 账单头/收款表 + 强制改密标记",
        "sql": [
            "ALTER TABLE ledger ADD COLUMN exchange_rate REAL DEFAULT 1.0",
            "ALTER TABLE ledger ADD COLUMN currency TEXT DEFAULT 'USD'",
            "ALTER TABLE folio_items ADD COLUMN bill_id TEXT DEFAULT ''",
            "ALTER TABLE staff_accounts ADD COLUMN must_change_password INTEGER DEFAULT 0",
            "ALTER TABLE staff_accounts ADD COLUMN last_pw_change TIMESTAMP",
            """
            CREATE TABLE IF NOT EXISTS bill_headers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_no TEXT UNIQUE NOT NULL,
                guest_id INTEGER,
                checkin_id INTEGER,
                issue_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_amount REAL DEFAULT 0,
                currency TEXT DEFAULT 'USD',
                exchange_rate REAL DEFAULT 1.0,
                status TEXT DEFAULT 'OPEN',
                operator_id TEXT DEFAULT '',
                note TEXT DEFAULT ''
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS payment_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_tx_id TEXT,
                guest_id INTEGER,
                checkin_id INTEGER,
                order_id TEXT DEFAULT '',
                reference_no TEXT DEFAULT '',
                amount REAL DEFAULT 0,
                currency TEXT DEFAULT 'USD',
                exchange_rate REAL DEFAULT 1.0,
                pay_method TEXT DEFAULT 'CASH',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note TEXT DEFAULT ''
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_bill_headers_guest ON bill_headers(guest_id)",
            "CREATE INDEX IF NOT EXISTS idx_bill_headers_checkin ON bill_headers(checkin_id)",
            "CREATE INDEX IF NOT EXISTS idx_payment_records_tx ON payment_records(payment_tx_id)",
            "CREATE INDEX IF NOT EXISTS idx_payment_records_checkin ON payment_records(checkin_id)",
            "CREATE INDEX IF NOT EXISTS idx_payment_records_order ON payment_records(order_id)",
            "CREATE INDEX IF NOT EXISTS idx_ledger_exchange_rate ON ledger(currency, exchange_rate)",
        ],
        # check_on_error_means_done=True：PRAGMA 探测列，已存在时查询成功 → 视为已迁移
        "check": "SELECT exchange_rate FROM ledger LIMIT 1",
        "check_on_error_means_done": True,
    },
    {
        # [sub-i] 超市图标包体系：shop_items 新增 icon_key/description 列
        # category/emoji 已由 database.py:_migrate() 兜底添加，这里只补 2 个新列；
        # 旧库可能已存在 category/emoji（来自 _migrate），不会冲突。
        # 框架逐条 try/except 容忍已存在列，但 PRAGMA 探测更高效。
        "version": 23,
        "description": "[sub-i] shop_items 加 icon_key/description 列（图标包体系）",
        "sql": [
            "ALTER TABLE shop_items ADD COLUMN icon_key TEXT DEFAULT ''",
            "ALTER TABLE shop_items ADD COLUMN description TEXT DEFAULT ''",
        ],
        # 探测 icon_key 列，已存在 → 查询成功 → 视为已迁移
        "check": "SELECT icon_key FROM shop_items LIMIT 1",
        "check_on_error_means_done": True,
    },
]


def _ensure_schema_version_table(db) -> None:
    """确保 schema_version 表存在。"""
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY,"
        "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "  description TEXT"
        ")"
    )


def _current_version(db) -> int:
    """查询当前库的迁移版本。没有记录时为 0。"""
    row = db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
    return int(row[0]) if row else 0


def _migration_applies(db, m: dict[str, Any]) -> bool:
    """通过 check 判断迁移是否需要执行。"""
    check = m.get("check")
    if not check:
        return True
    check_on_error = m.get("check_on_error_means_done", False)
    try:
        db.execute(check)
        # 没抛异常 = 已存在
        return False if check_on_error else True
    except Exception:
        # 抛异常 = 不存在
        return True if check_on_error else False


def run_migrations(db) -> list[dict[str, Any]]:
    """执行所有未跑过的迁移。

    Args:
        db: 数据库实例，需要提供 execute() 和 fetchone() 方法。

    Returns:
        已应用的迁移版本列表。
    """
    _ensure_schema_version_table(db)
    current = _current_version(db)
    applied: list[dict[str, Any]] = []

    for m in MIGRATIONS:
        version = m["version"]

        if version <= current:
            continue

        # 用 check 二次验证
        if not _migration_applies(db, m):
            continue

        logger.info("数据库迁移 v%s: %s", version, m["description"])

        for stmt in m["sql"]:
            stmt = stmt.strip()
            if stmt:
                try:
                    db.execute(stmt)
                except Exception as exc:
                    logger.warning("迁移 v%s SQL 失败: %s (已忽略)", version, exc)

        try:
            db.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, m["description"]),
            )
        except Exception as exc:
            logger.warning("迁移 v%s 版本记录失败: %s", version, exc)

        applied.append(m)

    if applied:
        logger.info("数据库迁移完成: 已应用 %s 个迁移", len(applied))
    else:
        logger.info("数据库迁移: 无新迁移，当前版本 v%s", current)

    return applied
