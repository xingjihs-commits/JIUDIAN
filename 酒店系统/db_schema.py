"""
Database schema definitions for ShadowGuard PMS.

Split from database.py to keep schema declarations separate from runtime logic.
"""

# ── Core tables (original schema, created in _init_tables) ────────────────────

TABLES = {
    "buildings": (
        "CREATE TABLE IF NOT EXISTS buildings ("
        "building_id TEXT PRIMARY KEY, "
        "bld_no INTEGER UNIQUE, "
        "name TEXT NOT NULL DEFAULT '', "
        "sort_order INTEGER DEFAULT 0, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "rooms": (
        "CREATE TABLE IF NOT EXISTS rooms ("
        "room_id TEXT PRIMARY KEY, type_id TEXT DEFAULT '', "
        "floor TEXT, room_type TEXT, "
        "status TEXT DEFAULT 'READY', note TEXT, "
        "building TEXT DEFAULT '', lock_no TEXT DEFAULT '', "
        "bld_no INTEGER DEFAULT 1, flr_no INTEGER DEFAULT 0, "
        "rom_id INTEGER DEFAULT 0, max_cards INTEGER DEFAULT 4, "
        "dai INTEGER DEFAULT 0, rate_override REAL DEFAULT NULL, "
        "last_card_no INTEGER DEFAULT 0, last_seq INTEGER DEFAULT 0, "
        "max_guests INTEGER DEFAULT 2"
        ")"
    ),
    "room_type_templates": (
        "CREATE TABLE IF NOT EXISTS room_type_templates ("
        "type_id TEXT PRIMARY KEY, type_name TEXT, base_price REAL DEFAULT 0, "
        "hourly_price REAL DEFAULT 0, consumables_json TEXT, "
        "default_deposit REAL, price_walk_in REAL, price_contract REAL, "
        "price_member REAL, cleaning_fee REAL DEFAULT NULL, "
        "hk_consumables_deep_json TEXT, icon TEXT DEFAULT ''"
        ")"
    ),
    "shop_items": (
        "CREATE TABLE IF NOT EXISTS shop_items ("
        "sku TEXT PRIMARY KEY, name TEXT, price REAL, stock INTEGER DEFAULT 0, "
        "category TEXT DEFAULT '', "
        "emoji TEXT DEFAULT '', "
        "icon_key TEXT DEFAULT '', "
        "description TEXT DEFAULT '', "
        "cost_price REAL DEFAULT 0, "
        "pack_label TEXT DEFAULT '箱', "
        "units_per_pack INTEGER DEFAULT 1, "
        "listed INTEGER DEFAULT 0, "
        "telegram_file_id TEXT DEFAULT '', "
        "sort_order INTEGER DEFAULT 9999, "
        "telegram_label TEXT DEFAULT ''"
        ")"
    ),
    "system_config": (
        "CREATE TABLE IF NOT EXISTS system_config ("
        "key TEXT PRIMARY KEY, value TEXT"
        ")"
    ),
    "members": (
        "CREATE TABLE IF NOT EXISTS members ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT UNIQUE, "
        "level TEXT, points INTEGER DEFAULT 0, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "audit_events": (
        "CREATE TABLE IF NOT EXISTS audit_events ("
        "event_id TEXT PRIMARY KEY, event_type TEXT, severity TEXT DEFAULT 'INFO', "
        "actor_id TEXT, reason TEXT, metadata_json TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "bot_subscribers": (
        "CREATE TABLE IF NOT EXISTS bot_subscribers ("
        "chat_id TEXT PRIMARY KEY, room_id TEXT DEFAULT '', "
        "subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "guests": (
        "CREATE TABLE IF NOT EXISTS guests ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT, name TEXT, "
        "id_card TEXT, phone TEXT, checkin_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "checkout_time TIMESTAMP, status TEXT DEFAULT 'INHOUSE'"
        ")"
    ),
    "inventory_audit": (
        "CREATE TABLE IF NOT EXISTS inventory_audit ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT, action_type TEXT, "
        "item_sku TEXT, qty_change INTEGER, operator_id TEXT, note TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "energy_audit": (
        "CREATE TABLE IF NOT EXISTS energy_audit ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT, kwh_consumed REAL, "
        "sold_hours REAL, kwh_per_hour REAL, is_anomaly INTEGER DEFAULT 0, "
        "electrician_id TEXT, reading_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "ledger": (
        "CREATE TABLE IF NOT EXISTS ledger ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, tx_id TEXT UNIQUE, tx_type TEXT, "
        "room_id TEXT, amount REAL, currency TEXT, pay_method TEXT DEFAULT 'CASH', "
        "is_deposit INTEGER DEFAULT 0, operator_id INTEGER, note TEXT, "
        "checkin_id TEXT, reference_no TEXT, order_id TEXT, "
        "exchange_rate REAL DEFAULT 1.0, "  # [sub-a] 记录交易时汇率，便于多币种对账与汇兑损益计算
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "prev_hash TEXT, current_hash TEXT"
        ")"
    ),
    "pending_carts": (
        "CREATE TABLE IF NOT EXISTS pending_carts ("
        "cart_id TEXT PRIMARY KEY, room_id TEXT, items_json TEXT, "
        "total_amount REAL, status TEXT DEFAULT 'PENDING', "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
}

# ── Table creation order (no formal FK constraints, but logically grouped) ───

TABLE_CREATE_ORDER = [
    # Reference / dimension tables (no dependencies)
    "buildings",
    "rooms",
    "room_type_templates",
    "shop_items",
    "system_config",
    "members",
    "audit_events",
    "bot_subscribers",
    "guests",
    # Transaction / event tables (reference above tables in queries)
    "inventory_audit",
    "energy_audit",
    "ledger",
    "pending_carts",
]

# ── Migration tables (v2.0+, created in _init_new_tables / _run_migration) ────

MIGRATIONS = {
    "card_records": """
        CREATE TABLE IF NOT EXISTS card_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT,
            room_id TEXT,
            guest_name TEXT DEFAULT '',
            issue_time TEXT,
            expire_time TEXT,
            card_type TEXT DEFAULT 'MIFARE Classic',
            status TEXT DEFAULT 'active',
            operator_id TEXT DEFAULT '',
            source_system TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "orders": """
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
    "qr_tokens": """
        CREATE TABLE IF NOT EXISTS qr_tokens (
            token TEXT PRIMARY KEY,
            room_id TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT
        )
    """,
    "custom_fields": """
        CREATE TABLE IF NOT EXISTS custom_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_key TEXT UNIQUE NOT NULL,
            field_name TEXT NOT NULL,
            field_type TEXT DEFAULT 'text',
            entity_type TEXT DEFAULT 'guest',
            options_json TEXT,
            default_value TEXT,
            is_required INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            placeholder TEXT,
            help_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "custom_field_values": """
        CREATE TABLE IF NOT EXISTS custom_field_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_key TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            entity_type TEXT DEFAULT 'guest',
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(field_key, entity_id, entity_type)
        )
    """,
    "pricing_rules": """
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
    "holiday_pricing": """
        CREATE TABLE IF NOT EXISTS holiday_pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_start TEXT NOT NULL,
            date_end TEXT NOT NULL,
            label TEXT,
            price_multiplier REAL DEFAULT 1.5,
            room_type TEXT DEFAULT '*',
            is_active INTEGER DEFAULT 1
        )
    """,
    "group_rates": """
        CREATE TABLE IF NOT EXISTS group_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT NOT NULL,
            room_type TEXT,
            negotiated_price REAL NOT NULL,
            min_rooms INTEGER DEFAULT 1,
            contact TEXT,
            note TEXT,
            is_active INTEGER DEFAULT 1
        )
    """,
    "staff_accounts": """
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "permission_overrides": """
        CREATE TABLE IF NOT EXISTS permission_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            permission_key TEXT NOT NULL,
            granted INTEGER DEFAULT 1,
            UNIQUE(username, permission_key)
        )
    """,
    "staff_roster": """
        CREATE TABLE IF NOT EXISTS staff_roster (
            staff_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT DEFAULT '前台',
            telegram_chat_id TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "staff_attendance": """
        CREATE TABLE IF NOT EXISTS staff_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id TEXT NOT NULL,
            record_date TEXT NOT NULL,
            clock_in TIMESTAMP,
            clock_out TIMESTAMP,
            status TEXT DEFAULT 'NORMAL',
            UNIQUE(staff_id, record_date)
        )
    """,
    "door_open_audit": """
        CREATE TABLE IF NOT EXISTS door_open_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT,
            card_id TEXT,
            source TEXT,
            operator_id TEXT,
            ok INTEGER DEFAULT 1,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "card_dai_map": """
        CREATE TABLE IF NOT EXISTS card_dai_map (
            card_type INTEGER PRIMARY KEY,
            dai INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "blank_card_registry": """
        CREATE TABLE IF NOT EXISTS blank_card_registry (
            card_uid TEXT PRIMARY KEY,
            card_data TEXT DEFAULT '',
            source TEXT DEFAULT 'legacy',
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "legacy_operator_permissions": """
        CREATE TABLE IF NOT EXISTS legacy_operator_permissions (
            gonghao TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            legacy_role TEXT DEFAULT '',
            bitmask TEXT DEFAULT '',
            mapped_permissions TEXT DEFAULT '',
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "legacy_open_records": """
        CREATE TABLE IF NOT EXISTS legacy_open_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            source_table TEXT DEFAULT '',
            card_no TEXT DEFAULT '',
            room_id TEXT DEFAULT '',
            bld_no INTEGER DEFAULT 0,
            flr_no INTEGER DEFAULT 0,
            rom_id INTEGER DEFAULT 0,
            open_time TEXT DEFAULT '',
            op_kind TEXT DEFAULT '',
            raw_json TEXT DEFAULT '',
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_table, source_id)
        )
    """,
    "legacy_operator_actions": """
        CREATE TABLE IF NOT EXISTS legacy_operator_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            source_table TEXT DEFAULT '',
            gonghao TEXT DEFAULT '',
            op_name TEXT DEFAULT '',
            action TEXT DEFAULT '',
            target TEXT DEFAULT '',
            happened_at TEXT DEFAULT '',
            raw_json TEXT DEFAULT '',
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_table, source_id)
        )
    """,
    "guest_service_requests": """
        CREATE TABLE IF NOT EXISTS guest_service_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT UNIQUE,
            room_id TEXT,
            chat_id TEXT DEFAULT '',
            service_type TEXT,
            request_type TEXT,
            message TEXT,
            status TEXT DEFAULT 'PENDING',
            source TEXT DEFAULT 'telegram',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            handled_at TIMESTAMP,
            handler_id TEXT DEFAULT '',
            operator_id TEXT DEFAULT ''
        )
    """,
    "housekeeping_tasks": """
        CREATE TABLE IF NOT EXISTS housekeeping_tasks (
            task_id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL,
            task_type TEXT DEFAULT 'CLEAN',
            request_id TEXT DEFAULT '',
            status TEXT DEFAULT 'PENDING',
            assigned_chat_id TEXT DEFAULT '',
            assigned_staff_id TEXT DEFAULT '',
            accepted_at TIMESTAMP,
            completed_at TIMESTAMP,
            source TEXT DEFAULT 'system',
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "local_reservations": """
        CREATE TABLE IF NOT EXISTS local_reservations (
            reservation_id TEXT PRIMARY KEY,
            room_id TEXT DEFAULT '',
            room_type TEXT DEFAULT '',
            guest_name TEXT NOT NULL,
            guest_phone TEXT DEFAULT '',
            checkin_dt TEXT NOT NULL,
            checkout_dt TEXT NOT NULL,
            deposit REAL DEFAULT 0,
            total_price REAL DEFAULT 0,
            status TEXT DEFAULT 'PENDING',
            source TEXT DEFAULT 'frontdesk',
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "business_day_audit": """
        CREATE TABLE IF NOT EXISTS business_day_audit (
            business_date TEXT PRIMARY KEY,
            status TEXT DEFAULT 'OPEN',
            room_revenue REAL DEFAULT 0,
            shop_revenue REAL DEFAULT 0,
            deposit_net REAL DEFAULT 0,
            cash_net REAL DEFAULT 0,
            pending_tasks INTEGER DEFAULT 0,
            pending_arrivals INTEGER DEFAULT 0,
            pending_departures INTEGER DEFAULT 0,
            snapshot_json TEXT DEFAULT '{}',
            closed_at TIMESTAMP,
            operator_id TEXT DEFAULT ''
        )
    """,
    "processed_notifications": """
        CREATE TABLE IF NOT EXISTS processed_notifications (
            notify_id TEXT PRIMARY KEY,
            notify_type TEXT DEFAULT '',
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'DONE',
            note TEXT DEFAULT ''
        )
    """,
    "folio_items": """
        CREATE TABLE IF NOT EXISTS folio_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT DEFAULT '',
            sku TEXT DEFAULT '',
            qty INTEGER DEFAULT 1,
            unit_price REAL DEFAULT 0,
            total REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            note TEXT DEFAULT '',
            bill_id TEXT DEFAULT ''  -- [sub-a] 关联 bill_headers.id，账单无头问题修复
        )
    """,
    "shop_purchases": """
        CREATE TABLE IF NOT EXISTS shop_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            pack_count INTEGER NOT NULL,
            units_per_pack INTEGER NOT NULL,
            total_units INTEGER NOT NULL,
            cost_per_unit REAL DEFAULT 0,
            cost_per_pack REAL DEFAULT 0,
            total_cost REAL DEFAULT 0,
            operator_id TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "inventory_items": """
        CREATE TABLE IF NOT EXISTS inventory_items (
            item_id TEXT PRIMARY KEY,
            category TEXT NOT NULL DEFAULT 'shop',
            source_sku TEXT DEFAULT '',
            name TEXT NOT NULL,
            unit TEXT DEFAULT '件',
            cost_price REAL DEFAULT 0,
            sale_price REAL DEFAULT 0,
            reorder_threshold INTEGER DEFAULT 0,
            in_monitoring INTEGER DEFAULT 1,
            skip_reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "inventory_movements": """
        CREATE TABLE IF NOT EXISTS inventory_movements (
            move_id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            move_type TEXT NOT NULL,
            qty_change INTEGER NOT NULL,
            unit_cost REAL DEFAULT 0,
            related_room TEXT DEFAULT '',
            related_order TEXT DEFAULT '',
            operator_id TEXT DEFAULT '',
            note TEXT DEFAULT '',
            prev_hash TEXT DEFAULT '',
            row_hash TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "inventory_stocktake_sessions": """
        CREATE TABLE IF NOT EXISTS inventory_stocktake_sessions (
            session_id TEXT PRIMARY KEY,
            session_type TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            operator_id TEXT DEFAULT '',
            status TEXT DEFAULT 'IN_PROGRESS',
            total_items INTEGER DEFAULT 0,
            items_with_diff INTEGER DEFAULT 0,
            items_critical INTEGER DEFAULT 0,
            snapshot_hash TEXT DEFAULT '',
            note TEXT DEFAULT ''
        )
    """,
    "inventory_stocktake_lines": """
        CREATE TABLE IF NOT EXISTS inventory_stocktake_lines (
            line_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            book_qty INTEGER DEFAULT 0,
            counted_qty INTEGER DEFAULT 0,
            diff_qty INTEGER DEFAULT 0,
            diff_rate REAL DEFAULT 0,
            is_critical INTEGER DEFAULT 0,
            explanation TEXT DEFAULT '',
            locked_at TIMESTAMP,
            resolved_at TIMESTAMP
        )
    """,
    "inventory_baseline_snapshots": """
        CREATE TABLE IF NOT EXISTS inventory_baseline_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            operator_id TEXT DEFAULT '',
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            snapshot_hash TEXT NOT NULL DEFAULT '',
            items_count INTEGER DEFAULT 0,
            monitored_count INTEGER DEFAULT 0,
            skipped_count INTEGER DEFAULT 0,
            cloud_uploaded_at TIMESTAMP,
            note TEXT DEFAULT ''
        )
    """,
    "room_type_consumable_standards": """
        CREATE TABLE IF NOT EXISTS room_type_consumable_standards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            standard_qty INTEGER DEFAULT 1,
            trigger_event TEXT DEFAULT 'CHECKIN',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(type_id, item_id, trigger_event)
        )
    """,
    "energy_meters": """
        CREATE TABLE IF NOT EXISTS energy_meters (
            meter_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            location TEXT DEFAULT '',
            multiplier REAL DEFAULT 1,
            installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    """,
    "energy_meter_readings": """
        CREATE TABLE IF NOT EXISTS energy_meter_readings (
            reading_id TEXT PRIMARY KEY,
            meter_id TEXT NOT NULL,
            reading_kwh REAL NOT NULL,
            recorded_by TEXT DEFAULT '',
            source TEXT DEFAULT 'manual',
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "energy_periods": """
        CREATE TABLE IF NOT EXISTS energy_periods (
            period_id TEXT PRIMARY KEY,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            operator_id TEXT DEFAULT '',
            status TEXT DEFAULT 'IN_PROGRESS',
            theoretical_kwh REAL DEFAULT 0,
            actual_kwh REAL DEFAULT 0,
            diff_kwh REAL DEFAULT 0,
            diff_rate REAL DEFAULT 0,
            is_anomaly INTEGER DEFAULT 0,
            note TEXT DEFAULT ''
        )
    """,
    # ── [sub-a] 财务闭环：账单头表 + 收款流水表 ───────────────────────
    "bill_headers": """
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
    "payment_records": """
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
}
