"""
Solid PMS — Services Layer（业务编排层）

本层组合 core 层服务，实现完整的业务流程编排。
UI 层应通过 services 层调用业务逻辑，而非直接操作 database。

单例入口（推荐用法）:
    from services import checkout_svc, card_svc, report_svc
    ok, msg = checkout_svc.execute(guest_id=123, payment_method="CASH")
"""

from services.checkout_service import CheckoutService
from services.card_service import CardService
from services.report_service import ReportService

__all__ = [
    "CheckoutService",
    "CardService",
    "ReportService",
    # 模块级单例（懒加载）
    "checkout_svc", "card_svc", "report_svc",
]

# ── 模块级单例 ──

_checkout_svc = None
_card_svc = None
_report_svc = None


def _get_db():
    """获取数据库实例 — 通过 core 层间接访问（避免 services 直调 db）。"""
    from core import guest_svc
    return guest_svc._db


def __getattr__(name: str):
    global _checkout_svc, _card_svc, _report_svc

    if name == "checkout_svc":
        if _checkout_svc is None:
            _checkout_svc = CheckoutService(_get_db())
        return _checkout_svc
    if name == "card_svc":
        if _card_svc is None:
            _card_svc = CardService(_get_db())
        return _card_svc
    if name == "report_svc":
        if _report_svc is None:
            _report_svc = ReportService(_get_db())
        return _report_svc
    raise AttributeError(f"module 'services' has no attribute {name!r}")