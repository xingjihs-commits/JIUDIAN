"""
db_access/__init__.py — 数据库访问层（按域拆分）

本层从 database.py 拆出，每个模块封装一个业务域的 SQL 操作。

模块:
- shop_db.py       — 超市/库存 CRUD
- migration_db.py  — 数据库迁移（ALTER TABLE / CREATE INDEX）
"""

from db_access.shop_db import (
    record_shop_purchase, adjust_shop_stock,
    reserve_shop_stock, update_shop_item_icon,
)
from db_access.migration_db import run_init_new_tables, run_migrate

__all__ = [
    "record_shop_purchase", "adjust_shop_stock",
    "reserve_shop_stock", "update_shop_item_icon",
    "run_init_new_tables", "run_migrate",
]
