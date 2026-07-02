"""
开箱即用：自动写入云端地址、授权、酒店名等，用户无需手填设置。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Cloudflare 已绑定的 Worker（与控制台一致）
DEFAULT_CLOUD_WORKER_URL = "https://shadowguard-cloud.shadowguard-hotel.workers.dev"

OUT_OF_BOX_DEFAULTS = {
    "cloud_worker_url": DEFAULT_CLOUD_WORKER_URL,
    "cloud_enabled": "1",
    "cloud_poll_interval": "3",
    "hotel_name": "Solid 演示酒店",
    "region": "SEA",
    "trial_started": "1",
    "kill_switch_date": "2099-12-31",
    "theme": "mist",
    "auto_login_username": "admin",
    "auto_login_password": "",  # 随机生成，见日志
    "language": "zh",
    "notify_checkin": "1",
    "notify_checkout": "1",
    "notify_shift": "1",
    "daily_report_enabled": "1",
    "default_deposit": "50",
    "single_user_mode": "0",
}


def apply_production_defaults(db, *, force_cloud: bool = False) -> None:
    """写入默认配置；force_cloud 时强制覆盖云端地址与启用开关。"""
    for key, value in OUT_OF_BOX_DEFAULTS.items():
        if force_cloud and key in ("cloud_worker_url", "cloud_enabled"):
            db.set_config(key, value)
            continue
        if db.get_config(key) is None or str(db.get_config(key)).strip() == "":
            db.set_config(key, value)

    if force_cloud:
        db.set_config("cloud_worker_url", DEFAULT_CLOUD_WORKER_URL)
        db.set_config("cloud_enabled", "1")

    # 预置房间扩展属性定义
    _seed_room_prop_definitions(db)

    # 种子数据：房型模板 + 超市商品
    _seed_room_type_templates(db)
    _seed_shop_items(db)


def patch_sqlite_file(db_path: Path, *, force_cloud: bool = True) -> bool:
    if not db_path.is_file():
        return False
    from secure_db import connect as _secure_connect

    conn = _secure_connect(str(db_path))
    try:
        for key, value in OUT_OF_BOX_DEFAULTS.items():
            if force_cloud or key.startswith("cloud_"):
                conn.execute(
                    "INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)",
                    (key, value),
                )
            else:
                row = conn.execute(
                    "SELECT value FROM system_config WHERE key=?", (key,)
                ).fetchone()
                if row is None or not str(row[0] or "").strip():
                    conn.execute(
                        "INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)",
                        (key, value),
                    )
        conn.commit()
        return True
    finally:
        conn.close()


def patch_all_known_databases(force_cloud: bool = True) -> list[str]:
    from deploy_paths import get_deploy_root

    roots = [
        get_deploy_root(),
        Path(__file__).resolve().parent,
        Path(r"D:\SolidHotel"),
    ]
    done: list[str] = []
    seen: set[str] = set()
    for root in roots:
        for name in ("shadow_guard.db",):
            p = (root / name).resolve()
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            if patch_sqlite_file(p, force_cloud=force_cloud):
                done.append(str(p))
    return done


if __name__ == "__main__":
    paths = patch_all_known_databases(force_cloud=True)
    print("已配置数据库:")
    for p in paths:
        print(" ", p)
    print("云端:", DEFAULT_CLOUD_WORKER_URL)


_ROOM_PROP_SEEDS: list[tuple[str, str, str, str, int]] = [
    ("meter_no", "水表号", "text", "", 10),
    ("bath_no", "浴室号", "text", "", 20),
    ("heat_no", "暖气号", "text", "", 30),
    ("no_smoking", "禁烟房", "checkbox", "", 40),
    ("has_windows", "有窗", "checkbox", "", 50),
    ("has_bathtub", "有浴缸", "checkbox", "", 60),
    ("floor_level", "楼层等级", "select", '["高","中","低"]', 70),
    ("decoration_year", "装修年份", "number", "", 80),
    ("custom_note", "特殊备注", "text", "", 90),
]


def _seed_room_prop_definitions(db) -> None:
    """写入预置房间属性定义（仅首次）。"""
    for key, label, field_type, options, sort_order in _ROOM_PROP_SEEDS:
        existing = db.execute(
            "SELECT 1 FROM room_prop_definitions WHERE key=?", (key,)
        ).fetchone()
        if not existing:
            db.execute(
                "INSERT INTO room_prop_definitions (key, label, field_type, options, sort_order) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, label, field_type, options, sort_order),
            )


# ──────────────────────────────────────────────────────────────────────
#  种子数据：房型模板
# ──────────────────────────────────────────────────────────────────────

_ROOM_TYPE_SEEDS: list[tuple[str, str, float, float, float, str]] = [
    ("double", "大床房", 150, 60, 100, "🛏"),
    ("twin", "标准间", 180, 80, 150, "🏨"),
    ("twin_bed", "双床房", 200, 100, 150, "🛌"),
    ("deluxe", "豪华间", 280, 130, 200, "⭐"),
    ("suite", "套房", 380, 180, 300, "🛋"),
    ("family", "家庭房", 250, 120, 200, "🏠"),
    ("business", "商务房", 220, 100, 150, "💼"),
    ("president", "总统套房", 888, 400, 500, "👑"),
]


def _seed_room_type_templates(db) -> None:
    """写入预置房型模板 + 定价规则（仅首次）。"""
    for tid, tname, base, hourly, deposit, icon in _ROOM_TYPE_SEEDS:
        existing = db.execute(
            "SELECT 1 FROM room_type_templates WHERE type_id=?", (tid,)
        ).fetchone()
        if existing:
            continue
        db.execute(
            "INSERT INTO room_type_templates "
            "(type_id, type_name, base_price, hourly_price, default_deposit, consumables_json, icon) "
            "VALUES (?,?,?,?,?,'{}',?)",
            (tid, tname, base, hourly, deposit, icon),
        )
        # 同时写入定价规则（银卡 0.95 / 金卡 0.9 / 钻石 0.85）
        db.execute(
            "INSERT OR IGNORE INTO pricing_rules "
            "(room_type, base_price, hourly_price, discount_silver, discount_gold, discount_diamond) "
            "VALUES (?,?,?,?,?,?)",
            (tname, base, hourly, 0.95, 0.90, 0.85),
        )


# ──────────────────────────────────────────────────────────────────────
#  种子数据：超市商品
# ──────────────────────────────────────────────────────────────────────

def _seed_shop_items(db) -> None:
    """从 assets/shop/manifest.json 同步超市总库（仅补缺失 SKU）。"""
    try:
        from shop_catalog import seed_shop_from_manifest
        seed_shop_from_manifest(db, insert_only=True)
    except Exception as exc:
        logger.warning("[production_defaults] shop manifest 种子失败，回退旧列表: %s", exc)
        _seed_shop_items_legacy(db)


def _seed_shop_items_legacy(db) -> None:
    """旧版 11 SKU 兜底（manifest 不可用时）。"""
    legacy = [
        ("WATER", "矿泉水", "饮料", 2, 1, "💧", "箱", 24),
        ("COLA", "可乐", "饮料", 3, 1.5, "🥤", "箱", 24),
        ("BEER", "啤酒", "饮料", 8, 4, "🍺", "箱", 12),
        ("REDBULL", "功能饮料", "饮料", 10, 5.5, "🥫", "箱", 12),
        ("NOODLE", "方便面", "食品", 5, 2.5, "🍜", "箱", 12),
        ("CHIPS", "薯片", "零食", 6, 3, "🥨", "箱", 20),
        ("CHOCO", "巧克力", "零食", 12, 7, "🍫", "盒", 12),
        ("CONDOM", "安全套", "日用品", 15, 8, "🛡", "盒", 10),
        ("TOOTHBRUSH", "牙刷", "日用品", 3, 1, "🪥", "支", 50),
        ("TOWEL", "毛巾", "日用品", 18, 10, "🧴", "条", 20),
        ("SOAP", "香皂", "日用品", 5, 2.5, "🧼", "块", 30),
    ]
    for sku, name, category, price, cost, emoji, pack, units in legacy:
        if db.execute("SELECT 1 FROM shop_items WHERE sku=?", (sku,)).fetchone():
            continue
        db.execute(
            "INSERT INTO shop_items (sku, name, category, price, cost_price, emoji, "
            "pack_label, units_per_pack, stock, listed) VALUES (?,?,?,?,?,?,?,?,0,1)",
            (sku, name, category, price, cost, emoji, pack, units),
        )
