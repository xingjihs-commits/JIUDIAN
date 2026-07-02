from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
import sys
from contextlib import contextmanager
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)  # 数据库层统一日志
from pathlib import Path
from secure_db import connect as secure_db_connect
from db_schema import TABLES, TABLE_CREATE_ORDER, MIGRATIONS
from db_migration import run_migrations as run_schema_migrations

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# [sub-e] SQL 注入加固：表名/列名白名单
# ─────────────────────────────────────────────────────────────────────────────
# 反推校验发现 28 处 execute(f"...") 字符串拼接 SQL，其中 5 处是表名/列名
# 拼接（{table}/{tbl}/{name}/{col}）。SQLite 不能用占位符 ? 替换表名/列名，
# 必须用字符串拼接；为防注入，统一通过白名单 + 方括号包裹处理。
# 白名单来源：db_schema.TABLES + db_schema.MIGRATIONS + db_migration.MIGRATIONS
# 中所有 CREATE TABLE 的表名（静态提取，构建时一次性 frozenset）。
# ─────────────────────────────────────────────────────────────────────────────

# 合法表名白名单：包含核心表 + 历次迁移新增表 + schema_version 系统表
_ALLOWED_TABLES: frozenset[str] = frozenset({
    # db_schema.TABLES 核心表
    "buildings", "rooms", "room_type_templates", "shop_items", "system_config",
    "members", "audit_events", "bot_subscribers", "guests",
    "inventory_audit", "energy_audit", "ledger", "pending_carts",
    # db_schema.MIGRATIONS 新增表
    "card_records", "orders", "qr_tokens", "custom_fields", "custom_field_values",
    "pricing_rules", "holiday_pricing", "group_rates",
    "staff_accounts", "permission_overrides", "staff_roster", "staff_attendance",
    "door_open_audit", "card_dai_map", "blank_card_registry",
    "legacy_operator_permissions", "legacy_open_records", "legacy_operator_actions",
    "guest_service_requests", "housekeeping_tasks", "local_reservations",
    "business_day_audit", "processed_notifications", "folio_items",
    "shop_purchases", "inventory_items", "inventory_movements",
    "inventory_stocktake_sessions", "inventory_stocktake_lines",
    "inventory_baseline_snapshots", "room_type_consumable_standards",
    "energy_meters", "energy_meter_readings", "energy_periods",
    "bill_headers", "payment_records",
    # db_migration.MIGRATIONS 历次版本新增表
    "energy_readings", "room_prop_definitions", "adjustments_audit",
    "refunds", "refund_lines", "refund_audit_log", "bill_details",
    "exchange_rates", "ota_bookings", "deposit_transactions",
    # 系统表
    "schema_version",
})

# 合法列类型白名单：SQLite 仅允许以下基础类型（含 DEFAULT 子句由调用方拼）
_ALLOWED_COL_TYPES: frozenset[str] = frozenset({
    "TEXT", "INTEGER", "REAL", "TIMESTAMP", "BLOB",
})

# 列名正则：必须以字母/下划线开头，仅含字母/数字/下划线
_COL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_identifier(name: str, allowed_set: "frozenset[str]") -> str:
    """[sub-e] 校验表名/列名是否在白名单中，返回用方括号包裹的标识符。

    SQLite 防 SQL 注入的标准做法：白名单 + 方括号包裹（即 "[name]"）。
    方括号语法在 SQLite 中表示"标识符"，可避免任何特殊字符被解析为 SQL 关键字。

    Args:
        name: 待校验的表名/列名（来自调用方硬编码或 migrations 列表）
        allowed_set: 允许的标识符白名单（如 _ALLOWED_TABLES）

    Returns:
        形如 "[name]" 的安全标识符字符串

    Raises:
        ValueError: name 不在 allowed_set 中，或 name 为空/类型错误
    """
    if not isinstance(name, str) or not name:
        raise ValueError(
            f"[sub-e] 标识符校验失败：name={name!r} 不是非空字符串"
        )
    if name not in allowed_set:
        raise ValueError(
            f"[sub-e] 标识符校验失败：name={name!r} 不在白名单中"
            f"（共 {len(allowed_set)} 个合法标识符）。疑似 SQL 注入或代码 bug。"
        )
    # 方括号包裹：SQLite 标识符语法，防任何特殊字符被解析为 SQL 关键字
    return f"[{name}]"


def _validate_column_name(col: str) -> str:
    """[sub-e] 校验列名是否符合标识符规范（不要求在白名单中，因为列名太多）。

    列名不在固定白名单里（每张表列不同），但必须匹配 ^[a-zA-Z_][a-zA-Z0-9_]*$ 正则。
    返回方括号包裹的列名。
    """
    if not isinstance(col, str) or not col:
        raise ValueError(
            f"[sub-e] 列名校验失败：col={col!r} 不是非空字符串"
        )
    if not _COL_NAME_RE.match(col):
        raise ValueError(
            f"[sub-e] 列名校验失败：col={col!r} 不符合 ^[a-zA-Z_][a-zA-Z0-9_]*$ 正则"
        )
    return f"[{col}]"


def _validate_col_type(col_type: str) -> str:
    """[sub-e] 校验列类型，仅允许 TEXT/INTEGER/REAL/TIMESTAMP/BLOB 基础类型。

    注意：调用方传入的 col_type 形如 "TEXT DEFAULT 'CASH'" 或 "REAL DEFAULT 0"，
    本函数只校验"类型部分"（split 后第一个 token），其余 DEFAULT 子句原样保留。
    """
    if not isinstance(col_type, str) or not col_type.strip():
        raise ValueError(
            f"[sub-e] 列类型校验失败：col_type={col_type!r} 不是非空字符串"
        )
    # 只取第一个 token 作为类型关键字，DEFAULT 子句保留原样
    type_keyword = col_type.strip().split()[0].upper()
    # 去掉可能的括号（如 VARCHAR(255)）
    type_base = type_keyword.split("(")[0]
    if type_base not in _ALLOWED_COL_TYPES:
        raise ValueError(
            f"[sub-e] 列类型校验失败：col_type={col_type!r} 的类型关键字 "
            f"{type_base!r} 不在白名单 {_ALLOWED_COL_TYPES} 中"
        )
    return col_type

# 使用可重入锁，防止 run_transaction 内部调用 execute() 时死锁
# RLock 允许同一线程多次获取锁，不会自我阻塞

def _get_app_dir() -> Path:
    """获取应用程序的实际运行目录（兼容打包后程序和直接运行）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后，EXE 所在目录
        return Path(sys.executable).parent
    else:
        # 直接运行 .py 文件时，脚本所在目录
        return Path(__file__).parent


# 计入「营业额 / P&L 经营收入」— 不含押金（押金进钱箱但单列，与财务卡「营业额」一致）
LEDGER_REVENUE_TX_TYPES = ("ROOM_IN", "SHOP", "TIP", "LEGACY_IMPORT")
# 押金收取与退还（净额用于「今日押金进出」等，不计入营业额）
LEDGER_DEPOSIT_TX_TYPES = ("DEPOSIT_IN", "DEPOSIT_OUT")
# 历史兼容名：报表/财务模块统一 import；语义 = 营业额口径（2026 起不含 DEPOSIT_IN）
LEDGER_INCOME_TX_TYPES = LEDGER_REVENUE_TX_TYPES
# 计入「资金池 / 交班应有现金」的净现金流类型（含押金与小费；不含 NIGHT_AUDIT、SHIFT_DIFF 等非抽屉流水）
LEDGER_CASH_NET_TX_TYPES = ("ROOM_IN", "DEPOSIT_IN", "DEPOSIT_OUT", "SHOP", "CASH_IN", "PAYOUT", "EXPENSE", "TIP")


def _sql_in_types(types: tuple[str, ...]) -> str:
    return ",".join(f"'{t}'" for t in types)


def _resolve_operator() -> str:
    """获取当前登录操作员，用于 ledger 记录人追踪。"""
    try:
        from permission_system import PermissionManager
        u = PermissionManager.current_user()
        if u:
            return str(u.get("username") or u.get("id") or "unknown")
        return PermissionManager.current_role() or "guest"
    except Exception:
        logger.exception("数据库操作异常")
        return "unknown"


class ShadowDatabase:
    CARD_ROOM_OPEN_SOURCES: tuple[str, ...]
    def __init__(self, db_path: str = "shadow_guard.db") -> None:
        self.db_path = str(_get_app_dir() / db_path)
        self.conn = secure_db_connect(self.db_path, check_same_thread=False, timeout=10)
        self._lock = threading.RLock()  # 改为可重入锁，防止 run_transaction 内部调用 execute() 死锁
        self._tx_local = threading.local()  # Phase 4: 每线程独立的事务深度计数

        # ── 容灾加固：WAL 模式 + 性能优化 ──
        # WAL (Write-Ahead Logging) 提供：
        #   1. 更好的并发读写（写不阻塞读）
        #   2. 更强的崩溃恢复能力（WAL 文件可回放）
        #   3. 减少磁盘写入次数（页级写入而非完整事务日志）
        # synchronous=NORMAL: WAL 模式下安全（WAL 本身保证原子性），
        # 且比 FULL 快 2-50 倍。
        self.execute("PRAGMA journal_mode=WAL")
        self.execute("PRAGMA synchronous=NORMAL")
        # cache_size 设为 -8000（约 8MB），减少磁盘 IO
        self.execute("PRAGMA cache_size=-8000")
        # 启用内存中的临时表（避免磁盘 IO）
        self.execute("PRAGMA temp_store=MEMORY")
        logger.info("[DB] WAL 模式已启用，synchronous=NORMAL, cache=8MB")

        self._init_tables()

    # ── 容灾加固：数据库完整性校验 ──
    def check_integrity(self) -> tuple[bool, str]:
        """运行 PRAGMA integrity_check，返回 (是否通过, 详情)。

        建议每日自动调用（health_monitor.py 已接入）。
        如果返回 False，应立即触发紧急备份并告警。
        """
        try:
            row = self.execute("PRAGMA integrity_check").fetchone()
            if row and row[0] == "ok":
                return True, "ok"
            detail = row[0] if row else "unknown"
            logger.critical("[DB] integrity_check 失败: %s", detail)
            return False, detail or "unknown error"
        except Exception as exc:
            logger.critical("[DB] integrity_check 异常: %s", exc)
            return False, str(exc)

    def checkpoint_wal(self) -> None:
        """将 WAL 内容写回主数据库文件（被动模式，不阻塞读写）。

        用于备份前确保数据完整性。
        """
        try:
            self.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            logger.warning("[DB] WAL checkpoint 失败", exc_info=True)

    def _in_tx(self) -> bool:
        """当前线程是否在 db.transaction() 块内。"""
        return getattr(self._tx_local, "depth", 0) > 0

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            _start = time.time()
            cur = self.conn.execute(sql, params)
            _elapsed = (time.time() - _start) * 1000
            # 慢查询日志：超过 100ms 的 SQL 记录耗时和截断后的语句
            if _elapsed > 100:
                _sql_short = sql[:120] + "..." if len(sql) > 120 else sql
                logger.warning("慢查询 (%.0fms): %s | params=%s", _elapsed, _sql_short, str(params)[:80])
            # Phase 4: 处于 transaction() 块时不要 auto-commit（由 with 块统一 commit）
            if cur.description is None and not self._in_tx():
                self.conn.commit()
            return cur

    @contextmanager
    def transaction(self) -> Any:
        """显式事务上下文。块内所有 db.execute() 不会自动 commit，
        块正常结束 → 一次性 commit；抛异常 → rollback。

        支持同线程嵌套（仅最外层真的 BEGIN/COMMIT）。
        """
        with self._lock:
            depth = getattr(self._tx_local, "depth", 0)
            if depth == 0:
                self.conn.execute("BEGIN")
            self._tx_local.depth = depth + 1
            try:
                yield self.conn
                self._tx_local.depth -= 1
                if self._tx_local.depth == 0:
                    self.conn.commit()
            except Exception:
                self._tx_local.depth = 0
                try:
                    self.conn.rollback()
                except Exception:
                    logger.exception("数据库操作异常")
                raise

    def _init_tables(self) -> None:
        # Core tables (original schema)
        for tbl_name in TABLE_CREATE_ORDER:
            self.execute(TABLES[tbl_name])
        # Migration tables (v2.0+)
        for version, sql in MIGRATIONS.items():
            self._run_migration(version, sql)
        # Add columns to rooms (compatibility with older databases) — 用 PRAGMA 避免必然失败
        _room_new_cols = [
            ("building", "TEXT DEFAULT ''"),
            ("lock_no", "TEXT DEFAULT ''"),
            ("bld_no", "INTEGER DEFAULT 1"),
            ("flr_no", "INTEGER DEFAULT 0"),
            ("rom_id", "INTEGER DEFAULT 0"),
            ("max_cards", "INTEGER DEFAULT 4"),
            ("dai", "INTEGER DEFAULT 0"),
            ("rate_override", "REAL DEFAULT NULL"),
            ("last_card_no", "INTEGER DEFAULT 0"),
            ("last_seq", "INTEGER DEFAULT 0"),
        ]
        _room_existing = self._table_has_columns("rooms")
        for _col, _typ in _room_new_cols:
            if _col not in _room_existing:
                try:
                    self.execute(f"ALTER TABLE rooms ADD COLUMN {_col} {_typ}")
                except Exception:
                    logger.exception("数据库操作异常")
        try:
            self.execute("CREATE INDEX IF NOT EXISTS idx_rooms_bldflr ON rooms(bld_no, flr_no, rom_id)")
        except Exception:
            logger.exception("数据库操作异常")
        try:
            self.execute(
                "INSERT OR IGNORE INTO buildings (building_id, bld_no, name, sort_order) "
                "VALUES ('1', 1, '01', 1)"
            )
        except Exception:
            logger.exception("数据库操作异常")
        # Add columns to guests — 同样用 PRAGMA
        _guest_new_cols = [
            ("sex", "TEXT DEFAULT ''"),
            ("c_type", "TEXT DEFAULT ''"),
            ("c_no", "TEXT DEFAULT ''"),
            ("flag", "TEXT DEFAULT 'WalkIn'"),
            ("price", "REAL DEFAULT 0"),
            ("deposit", "REAL DEFAULT 0"),
            ("note", "TEXT DEFAULT ''"),
            ("card_id", "TEXT DEFAULT ''"),
        ]
        _guest_existing = self._table_has_columns("guests")
        for _col, _typ in _guest_new_cols:
            if _col not in _guest_existing:
                try:
                    self.execute(f"ALTER TABLE guests ADD COLUMN {_col} {_typ}")
                except Exception:
                    logger.exception("数据库操作异常")
        self._migrate()
        self._init_new_tables()
        self.init_system_variables()
        # 版本号驱动的迁移引擎（管理 schema_version 表 + 增量 ALTER TABLE / CREATE INDEX）
        run_schema_migrations(self)
        self._migration_done = True

    def _run_migration(self, version: str, sql: str) -> None:
        """Execute a migration CREATE TABLE once; track in system_config."""
        if self.get_config(f"db_migration_{version}"):
            return
        self.execute(sql)
        self.set_config(f"db_migration_{version}", "1")

    def _init_new_tables(self) -> None:
        """Non-CREATE TABLE migrations for v2.0+ tables (ALTER TABLE, CREATE INDEX, etc.).
        用 PRAGMA table_info 避免必然失败的 ALTER TABLE。"""

        # ── 补建缺失的核心业务表（应在 MIGRATIONS 中但可能因依赖顺序未创建）──
        self.execute("""
            CREATE TABLE IF NOT EXISTS housekeeping_tasks (
                task_id TEXT PRIMARY KEY,
                room_id TEXT NOT NULL,
                task_type TEXT DEFAULT 'CLEAN',
                source TEXT DEFAULT '',
                note TEXT DEFAULT '',
                status TEXT DEFAULT 'PENDING',
                assigned_to TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        """)
        self.execute("""
            CREATE TABLE IF NOT EXISTS folio_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                item_name TEXT DEFAULT '',
                quantity INTEGER DEFAULT 1,
                unit_price REAL DEFAULT 0,
                total REAL DEFAULT 0,
                paid INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 迁移旧版 card_records 表，补充缺失列
        if self._table_exists("card_records"):
            _cr_cols = self._table_has_columns("card_records")
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
                    self.execute(f"ALTER TABLE card_records ADD COLUMN {_col} {_typ}")
        # 迁移 bot_subscribers 表，补充缺失列
        _bot_cols = self._table_has_columns("bot_subscribers")
        _bot_new = [
            ("room_id", "TEXT DEFAULT ''"),
            ("subscribed_at", "TIMESTAMP"),
            ("last_active", "TIMESTAMP"),
        ]
        for _col, _typ in _bot_new:
            if _col not in _bot_cols:
                try:
                    self.execute(f"ALTER TABLE bot_subscribers ADD COLUMN {_col} {_typ}")
                except Exception:
                    logger.exception("数据库操作异常")
        # 会员消费历史表
        try:
            self.execute("""
                CREATE TABLE IF NOT EXISTS member_consumption (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    member_id INTEGER,
                    room_id TEXT,
                    amount REAL,
                    points_earned INTEGER DEFAULT 0,
                    checkin_date TEXT,
                    checkout_date TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(member_id) REFERENCES members(id)
                )
            """)
        except Exception:
            logger.exception("数据库操作异常")
        # 老系统开房记录索引
        if self._table_exists("legacy_open_records"):
            self.execute("CREATE INDEX IF NOT EXISTS idx_legacy_open_card ON legacy_open_records(card_no)")
            self.execute("CREATE INDEX IF NOT EXISTS idx_legacy_open_room ON legacy_open_records(room_id)")
            self.execute("CREATE INDEX IF NOT EXISTS idx_legacy_open_time ON legacy_open_records(open_time)")
        # 老系统操作员行为索引
        if self._table_exists("legacy_operator_actions"):
            self.execute("CREATE INDEX IF NOT EXISTS idx_legacy_op_action_user ON legacy_operator_actions(gonghao)")
            self.execute("CREATE INDEX IF NOT EXISTS idx_legacy_op_action_time ON legacy_operator_actions(happened_at)")
        # 迁移 guest_service_requests 表，补充缺失列
        if self._table_exists("guest_service_requests"):
            _gsr_cols = self._table_has_columns("guest_service_requests")
            _gsr_new = [
                ("request_id", "TEXT"),
                ("chat_id", "TEXT DEFAULT ''"),
                ("service_type", "TEXT"),
                ("request_type", "TEXT"),
                ("source", "TEXT DEFAULT 'telegram'"),
                ("handler_id", "TEXT DEFAULT ''"),
                ("operator_id", "TEXT DEFAULT ''"),
            ]
            for _col, _typ in _gsr_new:
                if _col not in _gsr_cols:
                    self.execute(f"ALTER TABLE guest_service_requests ADD COLUMN {_col} {_typ}")
        # 迁移 local_reservations 表，补充缺失列
        if self._table_exists("local_reservations"):
            _lr_cols = self._table_has_columns("local_reservations")
            _lr_new = [
                ("source", "TEXT DEFAULT 'frontdesk'"),
                ("note", "TEXT DEFAULT ''"),
                ("updated_at", "TIMESTAMP"),
            ]
            for _col, _typ in _lr_new:
                if _col not in _lr_cols:
                    self.execute(f"ALTER TABLE local_reservations ADD COLUMN {_col} {_typ}")
        # 借物追踪表
        try:
            self.execute("""
                CREATE TABLE IF NOT EXISTS borrowed_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT,
                    item_type TEXT NOT NULL,
                    qty INTEGER DEFAULT 1,
                    qty_returned INTEGER DEFAULT 0,
                    note TEXT,
                    borrowed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    returned_at TIMESTAMP
                )
            """)
        except Exception:
            logger.exception("数据库操作异常")
        # door_open_audit 索引
        if self._table_exists("door_open_audit"):
            self.execute("CREATE INDEX IF NOT EXISTS idx_door_audit_card ON door_open_audit(card_id)")
            self.execute("CREATE INDEX IF NOT EXISTS idx_door_audit_card_room ON door_open_audit(card_id, room_id)")
        # 迁移 shop_items 表，添加 emoji 列
        _si_cols = self._table_has_columns("shop_items")
        if "emoji" not in _si_cols:
            try:
                self.execute("ALTER TABLE shop_items ADD COLUMN emoji TEXT DEFAULT ''")
            except Exception:
                logger.exception("数据库操作异常")
        # C0-beta 库存/能耗索引
        if self._table_exists("inventory_movements"):
            self.execute("CREATE INDEX IF NOT EXISTS idx_inv_mv_item ON inventory_movements(item_id)")
            self.execute("CREATE INDEX IF NOT EXISTS idx_inv_mv_time ON inventory_movements(created_at)")
        if self._table_exists("inventory_stocktake_lines"):
            self.execute("CREATE INDEX IF NOT EXISTS idx_inv_lines_session ON inventory_stocktake_lines(session_id)")
        if self._table_exists("room_type_consumable_standards"):
            self.execute("CREATE INDEX IF NOT EXISTS idx_rtcs_type ON room_type_consumable_standards(type_id)")
        if self._table_exists("energy_meter_readings"):
            self.execute("CREATE INDEX IF NOT EXISTS idx_energy_readings_meter_time ON energy_meter_readings(meter_id, created_at)")
        if self._table_exists("energy_periods"):
            self.execute("CREATE INDEX IF NOT EXISTS idx_energy_period_time ON energy_periods(started_at, finished_at)")
        # 性能优化索引（核心表一定存在）
        self.execute("CREATE INDEX IF NOT EXISTS idx_ledger_created ON ledger(created_at)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_rooms_status ON rooms(status)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_rooms_floor ON rooms(floor)")
        if self._table_exists("card_records"):
            self.execute("CREATE INDEX IF NOT EXISTS idx_cards_issue_time ON card_records(issue_time)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_guests_room ON guests(room_id, status)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_ledger_room_id ON ledger(room_id)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_ledger_tx_type ON ledger(tx_type)")
        if self._table_exists("local_reservations"):
            self.execute("CREATE INDEX IF NOT EXISTS idx_reservations_guest ON local_reservations(guest_name)")
            self.execute("CREATE INDEX IF NOT EXISTS idx_reservations_checkin ON local_reservations(checkin_dt)")
        # 创建默认老板账号
        if self._table_exists("staff_accounts"):
            try:
                existing = self.execute("SELECT id FROM staff_accounts WHERE username='admin'").fetchone()
                if not existing:
                    from permission_system import VENDOR_PASSWORD
                    import hashlib
                    pw_hash = hashlib.sha256(VENDOR_PASSWORD.encode()).hexdigest()
                    self.execute(
                        "INSERT INTO staff_accounts (username, password_hash, display_name, role) "
                        "VALUES (?, ?, ?, ?)",
                        ("admin", pw_hash, "系统管理员", "boss")
                    )
            except Exception as e:
                logger.warning("默认管理员账号初始化跳过: %s", e)

    def _table_exists(self, table: str) -> bool:
        """检查表是否存在于数据库中（用于 ALTER 前的安全检查）。"""
        try:
            safe_table = _validate_identifier(table, _ALLOWED_TABLES)
            row = self.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name={safe_table}"
            ).fetchone()
            return row is not None
        except Exception:
            return False

    def _table_has_columns(self, table: str) -> set:
        """用一次 PRAGMA 查询获取表已有列名集合。

        [sub-e] SQL 注入加固：table 必须在 _ALLOWED_TABLES 白名单中，
        否则 raise ValueError；PRAGMA 中的表名用 [table] 方括号包裹防注入。

        表不存在时静默返回空 set（上层应先用 _table_exists 判断）。
        """
        try:
            # [sub-e] 白名单校验 + 方括号包裹（SQLite 标识符语法）
            safe_table = _validate_identifier(table, _ALLOWED_TABLES)
            rows = self.execute(f"PRAGMA table_info({safe_table})").fetchall()
            return {r[1] for r in rows}
        except Exception:
            return set()

    def _migrate(self) -> None:
        """Safely add columns that may be missing from older database versions.
        改用 PRAGMA table_info 一次性获取列清单，避免大量必然失败的 ALTER TABLE。"""
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
            ("shop_items", "listed", "INTEGER DEFAULT 0"),
            ("shop_items", "telegram_file_id", "TEXT DEFAULT ''"),
            ("shop_items", "sort_order", "INTEGER DEFAULT 9999"),
            ("shop_items", "telegram_label", "TEXT DEFAULT ''"),
            # [sub-i] 图标包体系：icon_key 自定义图标 key + description 商品描述
            # category/emoji 已由 _init_new_tables 顶部 ALTER 段单独处理（旧库迁移路径），
            # 此处冗余加一遍幂等：列已存在时跳过。
            ("shop_items", "icon_key", "TEXT DEFAULT ''"),
            ("shop_items", "description", "TEXT DEFAULT ''"),
            ("pending_carts", "payment_method", "TEXT DEFAULT 'CASH'"),
            ("pending_carts", "cash_received", "REAL DEFAULT 0"),
            ("pending_carts", "cash_change", "REAL DEFAULT 0"),
            ("pending_carts", "delivery_status", "TEXT DEFAULT ''"),
            ("pending_carts", "deliverer_id", "TEXT DEFAULT ''"),
            ("pending_carts", "notified_at", "TIMESTAMP"),
            ("pending_carts", "chat_id", "TEXT DEFAULT ''"),
            ("members", "birthday", "TEXT DEFAULT ''"),
            ("members", "preferences", "TEXT DEFAULT ''"),
            ("members", "remark", "TEXT DEFAULT ''"),
            ("staff_accounts", "last_pw_change", "TIMESTAMP"),
            # [sub-a] 财务闭环：旧库补列（与 db_migration v22 等效，幂等）
            ("ledger", "exchange_rate", "REAL DEFAULT 1.0"),
            ("ledger", "currency", "TEXT DEFAULT 'USD'"),
            ("folio_items", "bill_id", "TEXT DEFAULT ''"),
            ("staff_accounts", "must_change_password", "INTEGER DEFAULT 0"),
            # rooms 列兜底（新库 CREATE TABLE 已含，旧库由 v9 迁移 + 此兜底补全）
            ("rooms", "max_guests", "INTEGER DEFAULT 2"),
            ("rooms", "status", "TEXT DEFAULT 'READY'"),
            # ota_bookings 列兜底（旧库可能用旧 CREATE TABLE 无此行，16 列全覆盖）
            ("ota_bookings", "booking_no", "TEXT"),
            ("ota_bookings", "ota_source", "TEXT DEFAULT 'manual'"),
            ("ota_bookings", "ota_order_id", "TEXT"),
            ("ota_bookings", "guest_name", "TEXT DEFAULT ''"),
            ("ota_bookings", "guest_phone", "TEXT"),
            ("ota_bookings", "room_type", "TEXT"),
            ("ota_bookings", "room_id", "TEXT"),
            ("ota_bookings", "checkin_dt", "TEXT DEFAULT ''"),
            ("ota_bookings", "checkout_dt", "TEXT DEFAULT ''"),
            ("ota_bookings", "nights", "INTEGER DEFAULT 1"),
            ("ota_bookings", "total_price", "REAL DEFAULT 0"),
            ("ota_bookings", "status", "TEXT DEFAULT 'PENDING'"),
            ("ota_bookings", "raw_payload", "TEXT"),
            ("ota_bookings", "created_at", "TEXT DEFAULT (datetime('now','localtime'))"),
            ("ota_bookings", "updated_at", "TEXT DEFAULT (datetime('now','localtime'))"),
        ]
        from collections import defaultdict
        by_table = defaultdict(list)
        for table, col, col_type in migrations:
            by_table[table].append((col, col_type))
        for table, cols in by_table.items():
            # 表不存在则跳过所有 ALTER（由 _init_new_tables 补建表）
            if not self._table_exists(table):
                continue
            existing = self._table_has_columns(table)
            for col, col_type in cols:
                if col in existing:
                    continue
                try:
                    # [sub-e] SQL 注入加固：table 走白名单，col 走正则，col_type 走类型白名单
                    safe_table = _validate_identifier(table, _ALLOWED_TABLES)
                    safe_col = _validate_column_name(col)
                    safe_col_type = _validate_col_type(col_type)
                    self.execute(
                        f"ALTER TABLE {safe_table} ADD COLUMN {safe_col} {safe_col_type}"
                    )
                except Exception:
                    logger.exception("数据库操作异常")

        try:
            from shop_catalog import seed_shop_from_manifest
            seed_shop_from_manifest(self, insert_only=True)
        except Exception:
            logger.exception("数据库操作异常")

    def init_system_variables(self) -> None:
        defaults = {
            "api_camera_endpoint": "",
            "remote_boss_dashboard_url": "",
            "language_code": "zh-CN",
            "language": "zh",
            "kill_switch_date": "2099-12-31",
            "license_key": "",
            "manufacturer_chat_id": "",
            "default_deposit": "50",
            "notify_rate_override": "0",
            "energy_default_sold_hours": "24",
            "energy_kwh_per_hour_warn": "2.0",
            "tg_staff_route_default": "prefer_dm",
        }
        for k, v in defaults.items():
            if self.get_config(k) is None:
                self.set_config(k, v)

    def get_deposit_for_room_type(self, type_id: Optional[str]) -> float:
        """默认押金：房型若配置了 default_deposit（含 0）则用之，否则用全局 default_deposit 配置。"""
        global_default = self.get_config_float("default_deposit", 50.0)
        if not type_id:
            return global_default
        row = self.execute(
            "SELECT default_deposit FROM room_type_templates WHERE type_id=?",
            (type_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return global_default
        return float(row[0])

    def get_rate_for_room_type(self, type_id: Optional[str], tier: str = "standard") -> float:
        """单晚参考价：tier=standard|walkin|contract|member；未配置的分档价回落到 base_price。"""
        if not type_id:
            return 0.0
        row = self.execute(
            "SELECT base_price, price_walk_in, price_contract, price_member "
            "FROM room_type_templates WHERE type_id=?",
            (type_id,),
        ).fetchone()
        if not row:
            return 0.0
        base = float(row[0] or 0)
        pw, pc, pm = row[1], row[2], row[3]
        t = (tier or "standard").lower()
        if t == "walkin" and pw is not None:
            return float(pw)
        if t == "contract" and pc is not None:
            return float(pc)
        if t == "member" and pm is not None:
            return float(pm)
        return base

    def log_action(self, actor: str, action: str, details: str = "") -> None:
        self.execute("INSERT INTO audit_events (event_id, event_type, severity, actor_id, reason, metadata_json) VALUES (?,?,?,?,?,?)",
                    (f"ACT_{int(time.time()*1000)}", "USER_ACTION", "INFO", actor, action, json.dumps({"details": details})))

    def get_member_info(self, phone: str) -> Any:
        return self.execute("SELECT name, level, points FROM members WHERE phone=?", (phone,)).fetchone()

    def get_level_discount(self, level: str) -> float:
        discounts = {"BRONZE": 1.0, "SILVER": 0.95, "GOLD": 0.9, "DIAMOND": 0.8, "ENTERPRISE": 0.75}
        return discounts.get(level, 1.0)

    def get_member_level(self, pts: int) -> str:
        """根据积分返回会员等级。"""
        if pts >= 50000:
            return "DIAMOND"
        if pts >= 20000:
            return "GOLD"
        if pts >= 5000:
            return "SILVER"
        return "BRONZE"

    def calculate_birthday_discount(self, member_id: int) -> dict:
        """检查会员是否今天生日，返回折扣信息。
        返回: {"is_birthday": bool, "discount": float, "name": str}
        """
        from datetime import date as _date
        row = self.execute(
            "SELECT name, birthday FROM members WHERE id=?",
            (member_id,),
        ).fetchone()
        if not row or not row[1]:
            return {"is_birthday": False, "discount": 1.0, "name": ""}
        try:
            bday = _date.fromisoformat(str(row[1]).strip()[:10])
            today = _date.today()
            if bday.month == today.month and bday.day == today.day:
                return {"is_birthday": True, "discount": 0.9, "name": str(row[0] or "")}
        except (ValueError, TypeError):
            pass
        return {"is_birthday": False, "discount": 1.0, "name": str(row[0] or "")}

    def get_staff_performance(self, staff_id: str, start: str, end: str) -> dict:
        """统计员工绩效：入住数/退房数/收款额/夜审次数。"""
        try:
            checkins = self.execute(
                """SELECT COUNT(*) FROM audit_events
                   WHERE actor_id=? AND reason='CHECKIN'
                   AND created_at BETWEEN ? AND ?""",
                (staff_id, start, end),
            ).fetchone()[0] or 0
        except Exception:
            checkins = 0
        try:
            checkouts = self.execute(
                """SELECT COUNT(*) FROM audit_events
                   WHERE actor_id=? AND reason='CHECKOUT'
                   AND created_at BETWEEN ? AND ?""",
                (staff_id, start, end),
            ).fetchone()[0] or 0
        except Exception:
            checkouts = 0
        try:
            revenue_row = self.execute(
                """SELECT COALESCE(SUM(amount),0) FROM ledger
                   WHERE operator_id=? AND tx_type IN ('ROOM_IN','SHOP','TIP')
                   AND created_at BETWEEN ? AND ?""",
                (staff_id, start, end),
            ).fetchone()
            revenue = float(revenue_row[0]) if revenue_row else 0.0
        except Exception:
            revenue = 0.0
        try:
            night_audits = self.execute(
                """SELECT COUNT(*) FROM audit_events
                   WHERE actor_id=? AND reason='NIGHT_AUDIT'
                   AND created_at BETWEEN ? AND ?""",
                (staff_id, start, end),
            ).fetchone()[0] or 0
        except Exception:
            night_audits = 0
        return {
            "checkins": int(checkins),
            "checkouts": int(checkouts),
            "revenue": float(revenue),
            "night_audits": int(night_audits),
        }

    def append_ledger(
        self,
        tx_type: str,
        amount: float,
        currency: str,
        operator_id: str | int | None = None,
        room_id: str | None = None,
        note: str = "",
        pay_method: str = "CASH",
        is_deposit: int = 0,
        *,
        tx_id_override: str | None = None,
        emit_event: bool = True,
        checkin_id: str | None = None,
        reference_no: str | None = None,
        order_id: str | None = None,
        exchange_rate: float | None = None,  # [sub-a] 交易时汇率，默认 1.0；多币种对账必需
        write_payment_record: bool = False,  # [sub-d Task2] 可选 hook：True 则同步插 payment_records
    ) -> str | None:
        """写入账本流水（原子操作，防止并发竞态导致哈希链断裂）。

        操作者标识为空或'1'时自动回退到当前登录用户。
        事务标识若提供且已存在相同事务标识，则跳过写入（幂等，用于老系统迁移对账）。
        事件信号在批量迁移时可关闭，由调用方结束时统一刷新界面。

        [sub-a] exchange_rate：交易发生时的本位币汇率（foreign→base）。
        传 None 时默认 1.0；多币种场景调用方应从 services.exchange_rate.get_rate 取值后传入。

        [sub-d Task2] write_payment_record：默认 False（向后兼容，不影响哈希链）。
        True 时在哈希链写入成功后，同步把本笔收款插入 payment_records 对账辅助表
        （checkout 等关键路径可显式开启；payment_records 写失败仅 log，不回滚 ledger）。
        """
        if operator_id is None or str(operator_id) == "1":
            operator_id = _resolve_operator()
        # [sub-a] 汇率缺省 1.0（同币种或单币种库），并防御负数/零
        try:
            rate_v = float(exchange_rate) if exchange_rate is not None else 1.0
            if rate_v <= 0 or not (rate_v == rate_v):  # NaN 防御
                rate_v = 1.0
        except (TypeError, ValueError):
            rate_v = 1.0
        # 修复竞态条件：使用可重入锁保证读取上一哈希和写入新记录是原子操作
        # 由于已改用可重入锁，事务内部调用执行语句不会死锁
        with self._lock:
            if tx_id_override:
                row = self.conn.execute("SELECT tx_id FROM ledger WHERE tx_id=?", (tx_id_override,)).fetchone()
                if row:
                    return None
            last = self.conn.execute("SELECT current_hash FROM ledger ORDER BY id DESC LIMIT 1").fetchone()
            prev = last[0] if last else "GENESIS"
            tx_id = tx_id_override or f"TX{int(time.time()*1000)}"
            data = f"{tx_id}{tx_type}{amount}{currency}{pay_method}{is_deposit}{operator_id}{room_id}{rate_v}{prev}"
            h = hashlib.sha256(data.encode()).hexdigest()
            self.conn.execute(
                "INSERT INTO ledger (tx_id, tx_type, room_id, amount, currency, pay_method, is_deposit, operator_id, note, prev_hash, current_hash, checkin_id, reference_no, order_id, exchange_rate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tx_id, tx_type, room_id, amount, currency, pay_method, is_deposit, operator_id, note, prev, h, checkin_id, reference_no, order_id, rate_v)
            )
            self.conn.commit()
        # [sub-d Task2] 可选 hook：哈希链成功后同步插 payment_records（对账辅助表）
        # 失败仅 log 不阻断；默认 False 保持向后兼容
        if write_payment_record:
            try:
                self._insert_payment_record(
                    tx_id=tx_id, amount=amount, currency=currency,
                    pay_method=pay_method, exchange_rate=rate_v,
                    checkin_id=checkin_id, reference_no=reference_no,
                    order_id=order_id, operator_id=operator_id,
                    room_id=room_id, note=note,
                )
            except Exception as _e:
                logger.warning(
                    "[append_ledger] write_payment_record 失败（不影响 ledger）: %s", _e
                )
        if emit_event:
            from event_bus import bus
            bus.ledger_updated.emit(tx_type, {"tx_id": tx_id, "amount": amount, "room_id": room_id, "note": note})
        return tx_id

    def _insert_payment_record(
        self, *, tx_id: str, amount, currency: str, pay_method: str,
        exchange_rate: float, checkin_id, reference_no, order_id,
        operator_id, room_id, note,
    ) -> None:
        """[sub-d Task2] 把一笔 ledger 收款同步写入 payment_records 对账辅助表。

        payment_records 实际 schema：payment_tx_id / checkin_id / order_id / reference_no
        / amount / currency / exchange_rate / pay_method / created_at / note
        （无 payment_id / operator_id 列；operator_id 落到 note 字段）
        """
        try:
            amt = float(amount)
        except Exception:
            amt = 0.0
        note_parts = []
        if operator_id:
            note_parts.append(f"op={operator_id}")
        if room_id:
            note_parts.append(f"room={room_id}")
        if note:
            note_parts.append(str(note)[:80])
        full_note = " ".join(note_parts)
        self.execute(
            "INSERT INTO payment_records "
            "(payment_tx_id, checkin_id, order_id, reference_no, amount, "
            " currency, exchange_rate, pay_method, note) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (tx_id, checkin_id, order_id, reference_no, amt,
             currency or "USD", float(exchange_rate or 1.0),
             pay_method or "CASH", full_note),
        )

    def append_ledger_conn(
        self,
        conn: sqlite3.Connection,
        tx_type: str,
        amount: float,
        currency: str,
        operator_id: str | int | None = None,
        room_id: str | None = None,
        note: str = "",
        pay_method: str = "CASH",
        is_deposit: int = 0,
        *,
        tx_id_override: str | None = None,
        checkin_id: str | None = None,
        reference_no: str | None = None,
        order_id: str | None = None,
        exchange_rate: float | None = None,  # [sub-a] 事务内版同步加汇率参数
    ) -> str | None:
        """事务内写账本；调用方负责 commit/rollback 与事件刷新。

        [sub-a] exchange_rate：同 append_ledger；checkout 事务内调用时由 services.exchange_rate.get_rate 提供。
        """
        if operator_id is None or str(operator_id) == "1":
            operator_id = _resolve_operator()
        try:
            rate_v = float(exchange_rate) if exchange_rate is not None else 1.0
            if rate_v <= 0 or rate_v != rate_v:
                rate_v = 1.0
        except (TypeError, ValueError):
            rate_v = 1.0
        if tx_id_override:
            row = conn.execute("SELECT tx_id FROM ledger WHERE tx_id=?", (tx_id_override,)).fetchone()
            if row:
                return None
        last = conn.execute("SELECT current_hash FROM ledger ORDER BY id DESC LIMIT 1").fetchone()
        prev = last[0] if last else "GENESIS"
        tx_id = tx_id_override or f"TX{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        data = f"{tx_id}{tx_type}{amount}{currency}{pay_method}{is_deposit}{operator_id}{room_id}{rate_v}{prev}"
        h = hashlib.sha256(data.encode()).hexdigest()
        conn.execute(
            "INSERT INTO ledger (tx_id, tx_type, room_id, amount, currency, pay_method, is_deposit, operator_id, note, prev_hash, current_hash, checkin_id, reference_no, order_id, exchange_rate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tx_id, tx_type, room_id, amount, currency, pay_method, is_deposit, operator_id, note, prev, h, checkin_id, reference_no, order_id, rate_v)
        )
        return tx_id

    def record_guest_service_request(
        self,
        room_id: str,
        request_type: str,
        message: str,
        chat_id: str = "",
        source: str = "telegram",
    ) -> str:
        rid = (room_id or "").strip() or "UNKNOWN"
        req_id = f"REQ_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        self.execute(
            "INSERT INTO guest_service_requests (request_id, room_id, chat_id, service_type, request_type, message, source) VALUES (?,?,?,?,?,?,?)",
            (req_id, rid, str(chat_id or ""), (request_type or "").strip(), (request_type or "").strip(), (message or "").strip(), source),
        )
        return req_id

    def create_housekeeping_task(
        self,
        room_id: str,
        task_type: str = "CLEAN",
        request_id: str = "",
        source: str = "system",
        note: str = "",
    ) -> str:
        task_id = f"HK_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        self.execute(
            "INSERT INTO housekeeping_tasks (task_id, room_id, task_type, request_id, source, note) VALUES (?,?,?,?,?,?)",
            (task_id, (room_id or "").strip(), task_type or "CLEAN", request_id or "", source or "system", note or ""),
        )
        return task_id

    def accept_housekeeping_task(self, task_id: str, chat_id: str = "", staff_id: str = "") -> bool:
        cur = self.execute(
            "UPDATE housekeeping_tasks SET status='ACCEPTED', assigned_chat_id=?, assigned_staff_id=?, accepted_at=CURRENT_TIMESTAMP "
            "WHERE task_id=? AND status='PENDING'",
            (str(chat_id or ""), str(staff_id or ""), task_id),
        )
        return (cur.rowcount or 0) == 1

    def complete_housekeeping_task(self, task_id: str, chat_id: str = "", staff_id: str = "") -> Optional[str]:
        def _tx(conn):
            row = conn.execute(
                "SELECT room_id, request_id FROM housekeeping_tasks WHERE task_id=? AND status IN ('PENDING','ACCEPTED')",
                (task_id,),
            ).fetchone()
            if not row:
                return None
            room_id, request_id = row
            conn.execute(
                "UPDATE housekeeping_tasks SET status='DONE', assigned_chat_id=COALESCE(NULLIF(assigned_chat_id,''),?), "
                "assigned_staff_id=COALESCE(NULLIF(assigned_staff_id,''),?), completed_at=CURRENT_TIMESTAMP WHERE task_id=?",
                (str(chat_id or ""), str(staff_id or ""), task_id),
            )
            conn.execute("UPDATE rooms SET status='READY' WHERE room_id=? AND status IN ('DIRTY','OVERTIME','MAINTENANCE')", (room_id,))
            if request_id:
                conn.execute(
                    "UPDATE guest_service_requests SET status='DONE', handled_at=CURRENT_TIMESTAMP, handler_id=? "
                    "WHERE request_id=? AND status='PENDING'",
                    (str(staff_id or chat_id or "housekeeping"), request_id),
                )
            return room_id
        return self.run_transaction(_tx)

    def create_local_reservation(
        self,
        guest_name: str,
        checkin_dt: str,
        checkout_dt: str,
        *,
        room_type: str = "",
        room_id: str = "",
        guest_phone: str = "",
        deposit: float = 0.0,
        total_price: float = 0.0,
        source: str = "frontdesk",
        note: str = "",
    ) -> tuple[bool, str, str]:
        rid = f"RSV_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        target_clause = "room_id=?" if room_id else "room_type=?"
        target_value = room_id or room_type
        if target_value:
            conflict = self.execute(
                f"SELECT reservation_id, guest_name FROM local_reservations WHERE {target_clause} "
                "AND status IN ('PENDING','CONFIRMED') AND checkin_dt < ? AND checkout_dt > ? LIMIT 1",
                (target_value, checkout_dt, checkin_dt),
            ).fetchone()
            if conflict:
                return False, f"与预订 {conflict[0]}({conflict[1]}) 时间冲突", ""
        self.execute(
            "INSERT INTO local_reservations (reservation_id, room_id, room_type, guest_name, guest_phone, checkin_dt, checkout_dt, deposit, total_price, source, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, room_id or "", room_type or "", guest_name, guest_phone or "", checkin_dt, checkout_dt, float(deposit or 0), float(total_price or 0), source or "frontdesk", note or ""),
        )
        try:
            from telegram_shadow import telegram_thread
            if telegram_thread.isRunning():
                telegram_thread.notify_reservation(rid, guest_name, room_type, room_id, checkin_dt, checkout_dt, deposit)
        except Exception:
            logger.exception("数据库操作异常")
        return True, "预订已创建", rid

    def update_local_reservation_status(self, reservation_id: str, status: str, note: str = "") -> bool:
        cur = self.execute(
            "UPDATE local_reservations SET status=?, note=COALESCE(NULLIF(?,''),note), updated_at=CURRENT_TIMESTAMP WHERE reservation_id=?",
            (status, note or "", reservation_id),
        )
        return (cur.rowcount or 0) == 1

    def list_today_reservation_alerts(self) -> dict:
        arr = self.execute(
            "SELECT reservation_id, room_id, room_type, guest_name, guest_phone, checkin_dt FROM local_reservations "
            "WHERE status IN ('PENDING','CONFIRMED') AND date(checkin_dt)=date('now','localtime') ORDER BY checkin_dt"
        ).fetchall()
        dep = self.execute(
            "SELECT room_id, name, phone, checkout_time FROM guests WHERE status='INHOUSE' "
            "AND checkout_time IS NOT NULL AND date(checkout_time)=date('now','localtime') ORDER BY checkout_time"
        ).fetchall()
        return {"arrivals": arr, "departures": dep}

    def build_cashier_shift_summary(self, since: str = "") -> dict:
        where = "created_at >= ?" if since else "date(created_at)=date('now','localtime')"
        params = (since,) if since else ()
        rows = self.execute(
            f"SELECT tx_type, pay_method, COALESCE(SUM(amount),0), COUNT(*) FROM ledger WHERE {where} GROUP BY tx_type, pay_method",
            params,
        ).fetchall()
        by_type = {}
        by_pay = {}
        for tx_type, pay_method, amount, count in rows:
            by_type[tx_type] = by_type.get(tx_type, 0.0) + float(amount or 0)
            by_pay[pay_method or "CASH"] = by_pay.get(pay_method or "CASH", 0.0) + float(amount or 0)
        usd_rate = float(self.get_config("usd_khr_rate") or 4100)
        cash_net = sum(float(v or 0) for k, v in by_type.items() if k in LEDGER_CASH_NET_TX_TYPES)
        return {
            "currency": self.get_config("currency_symbol") or "$",
            "secondary_currency": self.get_config("secondary_currency_symbol") or "៛",
            "usd_khr_rate": usd_rate,
            "by_type": by_type,
            "by_pay": by_pay,
            "cash_net": cash_net,
            "cash_net_secondary": cash_net * usd_rate,
        }

    def close_business_day(self, business_date: str = "", operator_id: str = "night_audit") -> tuple[bool, str]:
        bdate = business_date or time.strftime("%Y-%m-%d")
        existing = self.execute("SELECT status FROM business_day_audit WHERE business_date=?", (bdate,)).fetchone()
        if existing and existing[0] == "CLOSED":
            return False, f"{bdate} 已夜审锁定"
        summary = self.build_cashier_shift_summary(f"{bdate} 00:00:00")
        pending_tasks = self.execute("SELECT COUNT(*) FROM guest_service_requests WHERE status='PENDING'").fetchone()[0]
        alerts = self.list_today_reservation_alerts()
        room_rev = float(summary["by_type"].get("ROOM_IN", 0.0))
        shop_rev = float(summary["by_type"].get("SHOP", 0.0))
        deposit_net = float(summary["by_type"].get("DEPOSIT_IN", 0.0)) + float(summary["by_type"].get("DEPOSIT_OUT", 0.0))
        snap = {
            "summary": summary,
            "pending_tasks": pending_tasks,
            "arrivals": len(alerts.get("arrivals") or []),
            "departures": len(alerts.get("departures") or []),
        }
        self.execute(
            "INSERT OR REPLACE INTO business_day_audit "
            "(business_date,status,room_revenue,shop_revenue,deposit_net,cash_net,pending_tasks,pending_arrivals,pending_departures,snapshot_json,closed_at,operator_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?)",
            (
                bdate,
                "CLOSED",
                room_rev,
                shop_rev,
                deposit_net,
                float(summary["cash_net"]),
                int(pending_tasks or 0),
                len(alerts.get("arrivals") or []),
                len(alerts.get("departures") or []),
                json.dumps(snap, ensure_ascii=False),
                operator_id,
            ),
        )
        return True, f"{bdate} 夜审已锁定"

    def notification_processed(self, notify_id: str) -> bool:
        nid = (notify_id or "").strip()
        if not nid:
            return False
        row = self.execute("SELECT 1 FROM processed_notifications WHERE notify_id=?", (nid,)).fetchone()
        return bool(row)

    def mark_notification_processed(self, notify_id: str, notify_type: str = "", status: str = "DONE", note: str = "") -> None:
        nid = (notify_id or "").strip()
        if not nid:
            return
        self.execute(
            "INSERT OR REPLACE INTO processed_notifications (notify_id, notify_type, status, note, processed_at) VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
            (nid, notify_type or "", status or "DONE", note or ""),
        )

    # ── 考勤模块 ──
    def log_attendance(self, telegram_chat_id: str, is_clock_in: bool) -> tuple[bool, str]:
        """处理考勤打卡"""
        import datetime
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 根据 chat_id 查找员工
        staff = self.execute("SELECT staff_id, name FROM staff_roster WHERE telegram_chat_id = ?", (str(telegram_chat_id),)).fetchone()
        if not staff:
            return False, "未找到关联员工，请先在系统中绑定您的 Telegram 账号。"
        
        staff_id, name = staff

        # 检查今日是否已有记录
        record = self.execute("SELECT clock_in, clock_out FROM staff_attendance WHERE staff_id = ? AND record_date = ?", (staff_id, date_str)).fetchone()
        
        if is_clock_in:
            if record and record[0]:
                return False, f"您今天 ({date_str}) 已经打过上班卡了。"
            self.execute("INSERT OR IGNORE INTO staff_attendance (staff_id, record_date, clock_in) VALUES (?, ?, ?)", (staff_id, date_str, time_str))
            return True, f"✅ {name}，上班打卡成功！时间：{now.strftime('%H:%M:%S')}"
        else:
            if not record or not record[0]:
                return False, "您今天还没有上班打卡记录，无法下班打卡。"
            if record[1]:
                return False, f"您今天 ({date_str}) 已经打过下班卡了。"
            self.execute("UPDATE staff_attendance SET clock_out = ? WHERE staff_id = ? AND record_date = ?", (time_str, staff_id, date_str))
            return True, f"✅ {name}，下班打卡成功！辛苦了！时间：{now.strftime('%H:%M:%S')}"

    def log_inventory_change(self, room_id: str, action: str, sku: str, qty: int, op: str, note: str = "") -> None:
        self.execute("INSERT INTO inventory_audit (room_id, action_type, item_sku, qty_change, operator_id, note) VALUES (?,?,?,?,?,?)",
                    (room_id, action, sku, qty, op, note))

    def apply_opening_stocktake(self, sku_to_counted: dict, operator_id: str, action_type: str = "INIT_OPENING") -> None:
        """将实盘数量写入 shop_items.stock，并按差额写入 inventory_audit（action_type 默认 INIT_OPENING）。
        C0-beta 接入：同步把差额作为哈希链流水写入（OPENING / ADJUST）。"""
        room_tag = "__OPENING__" if action_type == "INIT_OPENING" else "__SHIFT__"
        try:
            from inventory_baseline import (
                record_shop_movement, MOVE_OPENING, MOVE_ADJUST,
            )
            chain_move_type = MOVE_OPENING if action_type == "INIT_OPENING" else MOVE_ADJUST
        except Exception:
            record_shop_movement = None  # type: ignore[assignment]
            chain_move_type = None  # type: ignore[assignment]
        for sku, counted in sku_to_counted.items():
            sku = str(sku).strip()
            if not sku:
                continue
            try:
                target = int(counted)
            except (TypeError, ValueError):
                continue
            row = self.execute("SELECT COALESCE(stock,0) FROM shop_items WHERE sku=?", (sku,)).fetchone()
            if not row:
                continue
            old = int(row[0] or 0)
            delta = target - old
            if delta == 0:
                continue
            self.execute("UPDATE shop_items SET stock=? WHERE sku=?", (target, sku))
            self.log_inventory_change(
                room_tag,
                action_type,
                sku,
                delta,
                operator_id,
                f"stocktake {old}->{target}",
            )
            if record_shop_movement and chain_move_type:
                try:
                    record_shop_movement(
                        self,
                        sku=sku,
                        move_type=chain_move_type,
                        qty_change=delta,
                        operator_id=operator_id or "SYSTEM",
                        note=f"{action_type} {old}->{target}",
                    )
                except Exception as _e:
                    logger.warning("期初/交班盘点入链失败 sku=%s: %s", sku, _e)

    def record_shop_purchase(
        self,
        sku: str,
        *,
        pack_count: int,
        units_per_pack: int,
        cost_per_unit: float = 0.0,
        cost_per_pack: float = 0.0,
        operator_id: str = "SYSTEM",
        note: str = "",
        update_item_pack_spec: bool = True,
    ) -> tuple[int, float]:
        """
        采购入库：箱数 × 每箱数量 → 增加库存；更新加权平均进价；写入 shop_purchases 与 inventory_audit。
        返回 (入库总件数, 本次采购总额)。
        """
        sku = (sku or "").strip()
        if not sku:
            raise ValueError("SKU 不能为空")
        boxes = max(1, int(pack_count))
        upp = max(1, int(units_per_pack))
        total_units = boxes * upp
        cpu = max(0.0, float(cost_per_unit or 0))
        if cost_per_pack and float(cost_per_pack) > 0:
            cpp = float(cost_per_pack)
            cpu = cpp / upp if upp else cpu
        else:
            cpp = cpu * upp
        total_cost = cpp * boxes

        row = self.execute(
            "SELECT COALESCE(stock,0), COALESCE(cost_price,0), COALESCE(units_per_pack,1), COALESCE(pack_label,'箱') "
            "FROM shop_items WHERE sku=?",
            (sku,),
        ).fetchone()
        if not row:
            raise ValueError(f"商品不存在: {sku}")

        old_stock, old_cost, old_upp, pack_label = int(row[0] or 0), float(row[1] or 0), int(row[2] or 1), row[3]
        new_stock = old_stock + total_units
        if cpu > 0 and new_stock > 0:
            if old_stock > 0 and old_cost > 0:
                avg_cost = (old_stock * old_cost + total_units * cpu) / new_stock
            else:
                avg_cost = cpu
        else:
            avg_cost = old_cost

        sets = ["stock=?", "cost_price=?"]
        params: list = [new_stock, avg_cost]
        if update_item_pack_spec:
            sets.append("units_per_pack=?")
            params.append(upp)
        params.append(sku)
        self.execute(f"UPDATE shop_items SET {', '.join(sets)} WHERE sku=?", tuple(params))

        detail = (
            f"{pack_label or '箱'}×{boxes} @{cpp:.2f}/{pack_label or '箱'} "
            f"({upp}件/箱, +{total_units}件)"
        )
        if note:
            detail = f"{detail}; {note}"

        self.log_inventory_change(
            "__SHOP__",
            "SHOP_PURCHASE",
            sku,
            total_units,
            operator_id,
            detail,
        )
        self.execute(
            """
            INSERT INTO shop_purchases
            (sku, pack_count, units_per_pack, total_units, cost_per_unit, cost_per_pack, total_cost, operator_id, note)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (sku, boxes, upp, total_units, cpu, cpp, total_cost, operator_id, note or ""),
        )
        # C0-beta：哈希链流水（与 inventory_audit 并行，慢慢替换老表）
        try:
            from inventory_baseline import record_shop_movement, MOVE_PURCHASE
            record_shop_movement(
                self,
                sku=sku,
                move_type=MOVE_PURCHASE,
                qty_change=total_units,
                unit_cost=cpu,
                operator_id=operator_id or "SYSTEM",
                note=detail,
            )
        except Exception as _e:
            logger.warning("采购流水入链失败 sku=%s: %s", sku, _e)
        return total_units, total_cost

    def adjust_shop_stock(self, sku: str, delta: int) -> None:
        """按 delta 调整 shop_items.stock（可负），SKU 不存在则忽略，库存不低于 0。"""
        sku = (sku or "").strip()
        if not sku:
            return
        try:
            d = int(delta)
        except (TypeError, ValueError):
            return
        row = self.execute("SELECT COALESCE(stock,0) FROM shop_items WHERE sku=?", (sku,)).fetchone()
        if not row:
            return
        newv = max(0, int(row[0] or 0) + d)
        self.execute("UPDATE shop_items SET stock=? WHERE sku=?", (newv, sku))

    def reserve_shop_stock(self, sku: str, qty: int = 1) -> bool:
        """客人点单等场景：若库存足够则原子扣减，成功返回真值。"""
        sku = (sku or "").strip()
        if not sku:
            return False
        try:
            q = int(qty)
        except (TypeError, ValueError):
            return False
        if q < 1:
            return False
        cur = self.execute(
            "UPDATE shop_items SET stock = COALESCE(stock,0) - ? WHERE sku = ? AND COALESCE(stock,0) >= ?",
            (q, sku, q),
        )
        return (cur.rowcount or 0) > 0

    # [sub-i] 图标包体系：更新商品图标元数据（icon_key/emoji/category/description）
    # 供前台"超市商品管理"或 CLI 工具调用；传入 None 表示不修改该字段。
    def update_shop_item_icon(
        self,
        sku: str,
        *,
        icon_key: str | None = None,
        emoji: str | None = None,
        category: str | None = None,
        description: str | None = None,
    ) -> bool:
        """按 SKU 更新 shop_items 的图标元数据字段。

        任意字段传 None 表示不修改；空字符串表示清空。
        返回是否真的命中了一行（SKU 不存在返回 False）。
        """
        sku = (sku or "").strip()
        if not sku:
            return False
        sets: list[str] = []
        params: list = []
        for col, val in (
            ("icon_key", icon_key),
            ("emoji", emoji),
            ("category", category),
            ("description", description),
        ):
            if val is None:
                continue
            sets.append(f"{col}=?")
            params.append(str(val))
        if not sets:
            # 没有要更新的字段，仅检查 SKU 是否存在
            row = self.execute("SELECT 1 FROM shop_items WHERE sku=?", (sku,)).fetchone()
            return bool(row)
        params.append(sku)
        try:
            cur = self.execute(
                f"UPDATE shop_items SET {', '.join(sets)} WHERE sku=?",
                tuple(params),
            )
            return (cur.rowcount or 0) > 0
        except Exception as exc:
            logger.warning("update_shop_item_icon 失败 sku=%s: %s", sku, exc)
            return False

    def log_energy_reading(self, room_id: str, kwh: float, hrs: float, eid: str, note: str = "", reading_mode: str = "") -> bool:
        warn = self.get_config_float("energy_kwh_per_hour_warn", 2.0)
        ratio = kwh / hrs if hrs > 0 else kwh
        is_anom = ratio > warn or (hrs == 0 and kwh > 1.0)
        self.execute(
            "INSERT INTO energy_audit (room_id, kwh_consumed, sold_hours, kwh_per_hour, is_anomaly, electrician_id, note, reading_mode) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (room_id, kwh, hrs, ratio, 1 if is_anom else 0, eid, note or "", reading_mode or ""),
        )
        return is_anom

    def list_recent_energy_readings(self, limit: int = 40) -> list:
        """最近电表抄录（供审计页）。"""
        try:
            return self.execute(
                "SELECT created_at, room_id, kwh_consumed, sold_hours, kwh_per_hour, is_anomaly, electrician_id, "
                "COALESCE(note,''), COALESCE(reading_mode,'') "
                "FROM energy_audit ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        except Exception:
            return []

    def log_door_open_event(
        self,
        room_id: str,
        card_id: str,
        source: str,
        operator_id: str,
        ok: int = 1,
        note: str = "",
    ) -> None:
        """门卡/门禁相关事件（制卡、注销、前台读卡登记等），供老板审计视图。"""
        self.execute(
            "INSERT INTO door_open_audit (room_id, card_id, source, operator_id, ok, note) VALUES (?,?,?,?,?,?)",
            (
                (room_id or "").strip(),
                (card_id or "").strip(),
                (source or "").strip(),
                (operator_id or "").strip(),
                1 if ok else 0,
                (note or "").strip(),
            ),
        )

    def list_door_open_audit(self, limit: int = 120) -> list:
        try:
            return self.execute(
                "SELECT created_at, room_id, card_id, source, operator_id, ok, note "
                "FROM door_open_audit ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        except Exception:
            return []

    # 计入「开门/开房」次数的来源（制卡、登记、注销不计入）
    CARD_ROOM_OPEN_SOURCES = (
        "lock_open",
        "door_open",
        "read_swipe",
        "guest_unlock",
        "hk_unlock",
        "master_open",
        "card_swipe",
    )

    @staticmethod
    def is_trackable_room_id(room_id: str) -> bool:
        rid = (room_id or "").strip()
        return bool(rid) and rid not in ("__REGISTRY__", "-", "—")

    def get_card_open_stats(self, card_id: str) -> dict:
        """某卡累计开门次数、涉及房间数、按房间汇总。"""
        cid = (card_id or "").strip().upper()
        if not cid:
            return {"total": 0, "room_count": 0, "last_at": "", "by_room": []}
        placeholders = ",".join("?" * len(self.CARD_ROOM_OPEN_SOURCES))
        sql = f"""
            SELECT room_id, COUNT(*) AS cnt, MAX(created_at) AS last_at
            FROM door_open_audit
            WHERE UPPER(card_id)=?
              AND ok=1
              AND source IN ({placeholders})
              AND room_id IS NOT NULL AND TRIM(room_id) != ''
              AND room_id != '__REGISTRY__'
            GROUP BY room_id
            ORDER BY cnt DESC, room_id ASC
        """
        params = [cid, *self.CARD_ROOM_OPEN_SOURCES]
        try:
            rows = self.execute(sql, tuple(params)).fetchall()
        except Exception:
            rows = []
        by_room = [
            {"room_id": str(r[0]), "count": int(r[1]), "last_at": str(r[2] or "")}
            for r in rows
            if self.is_trackable_room_id(str(r[0]))
        ]
        total = sum(x["count"] for x in by_room)
        last_at = ""
        if by_room:
            last_at = max((x["last_at"] for x in by_room if x["last_at"]), default="")
        return {
            "total": total,
            "room_count": len(by_room),
            "last_at": last_at,
            "by_room": by_room,
        }

    def get_cards_open_stats_batch(self) -> dict[str, dict]:
        """批量汇总所有卡号的开门统计（门卡列表刷新用）。"""
        placeholders = ",".join("?" * len(self.CARD_ROOM_OPEN_SOURCES))
        sql = f"""
            SELECT UPPER(card_id), room_id, COUNT(*) AS cnt, MAX(created_at) AS last_at
            FROM door_open_audit
            WHERE ok=1
              AND source IN ({placeholders})
              AND room_id IS NOT NULL AND TRIM(room_id) != ''
              AND room_id != '__REGISTRY__'
            GROUP BY UPPER(card_id), room_id
        """
        out: dict[str, dict] = {}
        try:
            rows = self.execute(sql, tuple(self.CARD_ROOM_OPEN_SOURCES)).fetchall()
        except Exception:
            return out
        for cid, rid, cnt, last_at in rows:
            cid = str(cid or "")
            rid = str(rid or "")
            if not cid or not self.is_trackable_room_id(rid):
                continue
            if cid not in out:
                out[cid] = {"total": 0, "room_count": 0, "last_at": "", "by_room": []}
            entry = out[cid]
            entry["by_room"].append(
                {"room_id": rid, "count": int(cnt), "last_at": str(last_at or "")}
            )
            entry["total"] += int(cnt)
            if str(last_at or "") > entry["last_at"]:
                entry["last_at"] = str(last_at)
        for entry in out.values():
            entry["room_count"] = len(entry["by_room"])
            entry["by_room"].sort(key=lambda x: (-x["count"], x["room_id"]))
        return out

    def list_card_open_events(self, card_id: str, limit: int = 300) -> list:
        """某卡开门明细（时间、房间、来源、操作员、备注）。"""
        cid = (card_id or "").strip().upper()
        if not cid:
            return []
        placeholders = ",".join("?" * len(self.CARD_ROOM_OPEN_SOURCES))
        sql = f"""
            SELECT created_at, room_id, source, operator_id, note
            FROM door_open_audit
            WHERE UPPER(card_id)=?
              AND ok=1
              AND source IN ({placeholders})
              AND room_id IS NOT NULL AND TRIM(room_id) != ''
              AND room_id != '__REGISTRY__'
            ORDER BY id DESC
            LIMIT ?
        """
        params = [cid, *self.CARD_ROOM_OPEN_SOURCES, int(limit)]
        try:
            return self.execute(sql, tuple(params)).fetchall()
        except Exception:
            return []

    def _staff_role_group_chat_id(self, role: str) -> str:
        """岗位对应的群聊标识（未配置则空）。"""
        r = (role or "").strip().lower()
        if r in ("保洁", "housekeeping", "cleaner", "清洁"):
            return (
                self.get_config("housekeeping_group_id")
                or self.get_config("housekeeping_chat_id")
                or ""
            ).strip()
        if r in ("前台", "frontdesk", "fd", "front desk"):
            return (
                self.get_config("front_desk_group_id")
                or self.get_config("front_desk_chat_id")
                or ""
            ).strip()
        if r in ("电工", "electrician"):
            return (self.get_config("electrician_group_chat_id") or "").strip()
        return (self.get_config("staff_misc_group_chat_id") or "").strip()

    def resolve_staff_notify_chats(self, staff_id: str) -> list[str]:
        """按花名册 telegram_route + 全局默认，解析要推送的 chat_id 列表（去重顺序）。"""
        row = self.execute(
            "SELECT telegram_chat_id, role, COALESCE(NULLIF(TRIM(telegram_route),''),'inherit') "
            "FROM staff_roster WHERE staff_id=? AND COALESCE(is_active,1)=1",
            (staff_id,),
        ).fetchone()
        if not row:
            return []
        personal = (row[0] or "").strip()
        role = row[1] or ""
        route = (row[2] or "inherit").strip().lower()
        group = (self._staff_role_group_chat_id(role) or "").strip()
        inherit_mode = (self.get_config("tg_staff_route_default") or "prefer_dm").strip().lower()
        eff = inherit_mode if route == "inherit" else route

        out: list[str] = []

        def add(x: str) -> None:
            x = (x or "").strip()
            if x and x not in out:
                out.append(x)

        if eff == "both":
            add(personal)
            add(group)
        elif eff == "group":
            add(group or personal)
        elif eff == "personal":
            add(personal or group)
        elif eff == "prefer_group":
            add(group or personal)
        else:
            add(personal or group)
        return out

    def backup_to(self, path: str) -> None:
        with self._lock:
            self.conn.commit()
            dst = secure_db_connect(path, check_same_thread=False, timeout=10)
            self.conn.backup(dst)
            dst.close()

    def restore_from(self, path: str) -> None:
        with self._lock:
            src = secure_db_connect(path, check_same_thread=False, timeout=10)
            src.backup(self.conn)
            src.close()
            self.conn.commit()

    # ── 配置缓存（30秒 TTL，减少高频 get_config 查询）──
    _config_cache: dict[str, tuple[float, str]] = {}

    def get_config(self, key: str) -> Optional[str]:
        """读取系统配置（带 30 秒缓存）。"""
        now = time.time()
        cached = self._config_cache.get(key)
        if cached and (now - cached[0]) < 30:
            return cached[1]
        r = self.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        val = r[0] if r else None
        self._config_cache[key] = (now, val)
        return val

    def set_config(self, key: str, value: str) -> None:
        """写入系统配置（同步清除缓存）。"""
        self._config_cache.pop(key, None)
        self.execute(
            "INSERT OR REPLACE INTO system_config (key, value) VALUES (?,?)",
            (key, value),
        )

    def invalidate_config_cache(self) -> None:
        """清除全部配置缓存（用于批量更新后）。"""
        self._config_cache.clear()

    def get_config_float(self, key: str, default: float = 0.0) -> float:
        """类型安全的浮点数配置读取，防止格式错误导致运行时崩溃"""
        val = self.get_config(key)
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_config_int(self, key: str, default: int = 0) -> int:
        """类型安全的整数配置读取，防止格式错误导致运行时崩溃"""
        val = self.get_config(key)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_config_bool(self, key: str, default: bool = False) -> bool:
        """类型安全的布尔配置读取（'1'/'true'/'yes' 视为真值）"""
        val = self.get_config(key)
        if val is None:
            return default
        return str(val).strip().lower() in ("1", "true", "yes", "on")

    def run_transaction(self, fn: Callable) -> Any:
        """Execute a function within a database transaction.
        
        Args:
            fn: A callable that takes a connection object and performs database operations.
                Should return a result value.
        
        Returns:
            The return value of fn.
        
        Raises:
            Any exception raised by fn (after rollback).
        """
        with self._lock:
            self.conn.execute("BEGIN")
            try:
                result = fn(self.conn)
                self.conn.commit()
                return result
            except Exception:
                self.conn.rollback()
                raise

    def get_today_checkin_count(self) -> int:
        return self.execute("SELECT COUNT(*) FROM ledger WHERE tx_type='ROOM_IN' AND date(created_at)=date('now','localtime')").fetchone()[0]

    def get_total_revenue(self) -> float:
        """今日营业额（ROOM_IN/SHOP/TIP），与 LEDGER_REVENUE_TX_TYPES、报表 P&L 收入线一致。"""
        inc = _sql_in_types(LEDGER_REVENUE_TX_TYPES)
        return float(self.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM ledger "
            f"WHERE tx_type IN ({inc}) AND date(created_at)=date('now','localtime')"
        ).fetchone()[0] or 0)

    def get_today_deposit_net(self) -> float:
        """今日押金进出净额（DEPOSIT_IN + DEPOSIT_OUT，含负的退还）。"""
        dep = _sql_in_types(LEDGER_DEPOSIT_TX_TYPES)
        row = self.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM ledger "
            f"WHERE tx_type IN ({dep}) AND date(created_at)=date('now','localtime')"
        ).fetchone()
        return float(row[0] or 0) if row else 0.0

    def get_fund_pool(self) -> float:
        """计算资金池余额：所有实际现金流入/流出之和
        排除 STOCK_DEDUCT（库存扣减，非现金流）和 NIGHT_AUDIT/SHIFT_DIFF（汇总/差异记录，非实际交易）
        含 DEPOSIT_IN / DEPOSIT_OUT、小费 TIP 等（与交班「应有现金」同口径）
        """
        cash = _sql_in_types(LEDGER_CASH_NET_TX_TYPES)
        total = float(self.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM ledger "
            f"WHERE tx_type IN ({cash})"
        ).fetchone()[0] or 0)
        return total

    def get_room_status_counts(self) -> dict:
        rows = self.execute("SELECT status, COUNT(*) FROM rooms GROUP BY status").fetchall()
        return dict(rows)

    def get_recent_ledger(self, limit: int = 50) -> list:
        # Return all columns to match the AuditTab expectations
        return self.execute("SELECT id, tx_id, tx_type, room_id, amount, currency, pay_method, is_deposit, operator_id, created_at, note FROM ledger ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def get_daily_overview(self) -> dict[str, Any]:
        today = self.execute("SELECT date('now','localtime')").fetchone()[0]
        inc = _sql_in_types(LEDGER_REVENUE_TX_TYPES)
        dep_sql = _sql_in_types(LEDGER_DEPOSIT_TX_TYPES)
        revenue = self.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM ledger WHERE date(created_at)=? AND tx_type IN ({inc})",
            (today,),
        ).fetchone()[0] or 0
        deposit_net = self.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM ledger WHERE date(created_at)=? AND tx_type IN ({dep_sql})",
            (today,),
        ).fetchone()[0] or 0
        occ = self.execute("SELECT COUNT(*) FROM rooms WHERE status='INHOUSE'").fetchone()[0]
        total = self.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
        anoms = self.execute("SELECT COUNT(*) FROM energy_audit WHERE date(reading_time)=? AND is_anomaly=1", (today,)).fetchone()[0]
        dirty = self.execute("SELECT COUNT(*) FROM rooms WHERE status='DIRTY'").fetchone()[0]
        overtime = self.execute("SELECT COUNT(*) FROM rooms WHERE status='OVERTIME'").fetchone()[0]
        pending_orders = self.execute("SELECT COUNT(*) FROM pending_carts WHERE status='PENDING'").fetchone()[0]
        return {
            "revenue": float(revenue),
            "deposit_net_today": float(deposit_net or 0),
            "occupancy": (occ/total*100) if total > 0 else 0,
            "energy_anomaly_count": anoms,
            "pending_tasks": int(dirty or 0) + int(overtime or 0) + int(pending_orders or 0),
        }

    def get_shift_start_time(self) -> str:
        """取最近一次 SHIFT_END 的时间，作为当前班的起始时间。"""
        row = self.execute(
            "SELECT created_at FROM ledger WHERE tx_type='SHIFT_END' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        return self.execute("SELECT datetime('now','localtime','start of day')").fetchone()[0]

    def get_ledger_by_range(self, start: str, end: str, limit: int = 50) -> list:
        """按时间范围取最近的账本流水。"""
        return self.execute(
            "SELECT id, tx_id, tx_type, room_id, amount, currency, pay_method, "
            "is_deposit, created_at, note FROM ledger "
            "WHERE created_at BETWEEN ? AND ? ORDER BY id DESC LIMIT ?",
            (start, end, limit),
        ).fetchall()

    def get_shift_expected(self) -> float:
        """账面应有现金：与资金池净值同口径（已扣支出、含押金与小费等），对齐「数字=钱」交班核对。"""
        return self.get_fund_pool()

    def get_audit_overview(self) -> dict[str, Any]:
        """今日审计概览（已迁移到 db_access/aggregation.py）。"""
        from db_access.aggregation import get_audit_overview as _impl
        return _impl(self)

    def get_overview_by_range(self, start: str, end: str) -> dict[str, Any]:
        """时间范围审计概览（已迁移到 db_access/aggregation.py）。"""
        from db_access.aggregation import get_overview_by_range as _impl
        return _impl(self, start, end)

    def run_night_audit(self) -> str:
        """夜审日结（已迁移到 db_access/aggregation.py）。"""
        from db_access.aggregation import run_night_audit as _impl
        return _impl(self)

    def verify_ledger_integrity(self) -> tuple[bool, str]:
        """验证账本哈希链完整性。

        修复：同时验证 prev_hash 链路连续性 AND 重新计算 current_hash 与存储值比对，
        防止单条记录字段被篡改（哈希未更新）的情况被漏检。

        哈希拼接公式与 append_ledger:794 保持一致：
        {tx_id}{tx_type}{amount}{currency}{pay_method}{is_deposit}{operator_id}{room_id}{exchange_rate}{prev_hash}
        """
        rows = self.execute(
            "SELECT id, tx_id, tx_type, amount, currency, pay_method, is_deposit, "
            "operator_id, room_id, COALESCE(exchange_rate, 1.0), prev_hash, current_hash FROM ledger "
            "WHERE current_hash IS NOT NULL ORDER BY id"
        ).fetchall()
        if not rows:
            return True, "无记录"
        prev = "GENESIS"
        for row in rows:
            (rid, tx_id, tx_type, amount, currency, pay_method,
             is_deposit, operator_id, room_id, rate_v, ph, ch) = row
            # 1. 验证链路连续性
            if ph != prev:
                return False, f"链路断裂（记录ID:{rid}）"
            # 2. 重新计算哈希 — 字段顺序与 append_ledger:794 完全一致
            data = f"{tx_id}{tx_type}{amount}{currency}{pay_method}{is_deposit}{operator_id}{room_id}{rate_v}{ph}"
            expected_hash = hashlib.sha256(data.encode()).hexdigest()
            if ch != expected_hash:
                return False, f"记录被篡改（记录ID:{rid}，哈希不匹配）"
            prev = ch
        return True, f"共{len(rows)}条，完整"

    def get_staff_risk_stats(self, limit: int = 10) -> list[dict[str, Any]]:
        """员工风控统计（已迁移到 services/risk_service.py）。"""
        from services.risk_service import get_staff_risk_stats as _impl
        return _impl(self, limit)

    def get_inventory_comparison(self, limit: int = 20) -> list[dict[str, Any]]:
        """库存对比分析（已迁移到 services/risk_service.py）。"""
        from services.risk_service import get_inventory_comparison as _impl
        return _impl(self, limit)

    def build_daily_risk_report(self) -> dict[str, Any]:
        """每日风控报告（已迁移到 services/risk_service.py）。"""
        from services.risk_service import build_daily_risk_report as _impl
        return _impl(self)

db = ShadowDatabase()


# ── 向后兼容：以下函数已迁移到 services/audit_service.py ──
# 保留在此以避免破坏现有 import，实际逻辑委托给 services 层。
# 新代码请直接 from services.audit_service import search_audit_logs, detect_anomalous_behavior


def search_audit_logs(actor: str = "", time_start: str = "", time_end: str = "",
                       action_type: str = "", keyword: str = "", limit: int = 50) -> list:
    """查询操作审计日志（已迁移到 services/audit_service.py）。"""
    from services.audit_service import search_audit_logs as _impl
    return _impl(actor, time_start, time_end, action_type, keyword, limit)


def detect_anomalous_behavior(actor_id: str) -> dict:
    """检测异常行为（已迁移到 services/audit_service.py）。"""
    from services.audit_service import detect_anomalous_behavior as _impl
    return _impl(actor_id)
