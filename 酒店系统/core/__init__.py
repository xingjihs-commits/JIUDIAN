"""
Solid PMS — Core Layer (零 Qt 依赖，纯业务逻辑)

本层提供所有数据访问和业务逻辑的纯 Python 实现。
UI 层（tabs/、main_window/）不应直接操作 database，应通过 core 层服务。

单例入口（推荐用法）:
    from core import guest_svc, inventory_svc, pricing_svc, ledger_svc
    ok, msg = guest_svc.checkin(room_id, guest_name)
"""

from core.exceptions import SolidError, DatabaseError, ValidationError
from core.ledger import LedgerService, LedgerHashChain
from core.guests import GuestService
from core.inventory import InventoryService
from core.pricing import PricingService

__all__ = [
    "SolidError", "DatabaseError", "ValidationError",
    "LedgerService", "LedgerHashChain",
    "GuestService",
    "InventoryService",
    "PricingService",
    # 模块级单例（懒加载）
    "guest_svc", "inventory_svc", "pricing_svc", "ledger_svc",
]

# ── 模块级单例（懒加载，替代 from database import db 的裸 SQL 调用）──

_guest_svc = None
_inventory_svc = None
_pricing_svc = None
_ledger_svc = None


def _get_db():
    from database import db
    return db


def __getattr__(name: str):
    """懒加载 core 服务单例，避免循环导入。"""
    global _guest_svc, _inventory_svc, _pricing_svc, _ledger_svc

    if name == "guest_svc":
        if _guest_svc is None:
            _guest_svc = GuestService(_get_db())
        return _guest_svc
    if name == "inventory_svc":
        if _inventory_svc is None:
            _inventory_svc = InventoryService(_get_db())
        return _inventory_svc
    if name == "pricing_svc":
        if _pricing_svc is None:
            _pricing_svc = PricingService(_get_db())
        return _pricing_svc
    if name == "ledger_svc":
        if _ledger_svc is None:
            _ledger_svc = LedgerService(_get_db())
        return _ledger_svc
    raise AttributeError(f"module 'core' has no attribute {name!r}")