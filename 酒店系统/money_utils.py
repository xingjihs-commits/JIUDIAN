# [AUDIT-FIX] 2026-06-22 — Decimal 金额工具模块
# 全项目金额计算统一入口，替代 float 的浮点舍入误差
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

# 金额精度：两位小数（柬埔寨瑞尔通常整数，但预留）
_MONEY_PLACES = Decimal("0.01")


def to_money(value: str | float | int | Decimal | None, default: str = "0") -> Decimal:
    """将任意输入转为 Decimal 金额。None/空/异常返回默认值。"""
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def fmt_money(value: Decimal | float | int | None, prefix: str = "") -> str:
    """格式化金额为显示字符串，如 '50.00' 或 '$50.00'。"""
    d = to_money(value)
    return f"{prefix}{d.quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP)}"


def fmt_money_int(value: Decimal | float | int | None, prefix: str = "") -> str:
    """格式化金额为整数显示（瑞尔无小数），如 '50' 或 '$50'。"""
    d = to_money(value)
    return f"{prefix}{d.quantize(Decimal('1'), rounding=ROUND_HALF_UP)}"


def add_money(*values) -> Decimal:
    """安全累加多个金额。"""
    total = Decimal("0")
    for v in values:
        total += to_money(v)
    return total


# [sub-a] 多币种支持：本位币折算与汇兑损益计算 ===============
_BASE_CURRENCY_DEFAULT = "USD"


def base_currency() -> str:
    """读取系统配置的本位币代码；未配置时回退 USD。

    为什么需要本位币：多币种收款时必须有一个统一的折算口径才能合计，
    对账逻辑与报表按本位币汇总，外币收款按当时汇率折算。
    """
    try:
        from database import db  # 延迟导入避免循环依赖
        v = db.get_config("base_currency")
        if v and isinstance(v, str) and v.strip():
            return v.strip().upper()
    except Exception:
        pass
    return _BASE_CURRENCY_DEFAULT


def to_base(amount, currency: str, rate) -> Decimal:
    """把外币金额按汇率折算为本位币。

    Args:
        amount: 原币种金额（任意可转 Decimal 的类型）
        currency: 原币种代码（仅用于日志/调试，本函数不查表）
        rate: 汇率（foreign→base），可为 Decimal/float/int/str

    Returns:
        本位币金额（Decimal，未量化）。调用方按需 quantize。

    为什么放在 money_utils：所有金额运算统一入口，避免在多处重复
    float(amount) * float(rate) 的浮点误差。
    """
    amt = to_money(amount)
    r = to_money(rate)
    if r <= 0:
        # 汇率无效时按 1:1 处理，避免除零或负数污染账本
        r = Decimal("1")
    return (amt * r).quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP)


def exchange_gain_loss(amount, recorded_rate, actual_rate) -> Decimal:
    """汇兑损益 = 实际收款折本位币 - 记账折本位币。

    正数 = 实际收款多于记账（汇兑收益）；负数 = 汇兑损失。
    用于 reconciliation_service 比对 ledger.exchange_rate 与对账时汇率。
    """
    amt = to_money(amount)
    rr = to_money(recorded_rate) or Decimal("1")
    ar = to_money(actual_rate) or Decimal("1")
    recorded_base = (amt * rr).quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP)
    actual_base = (amt * ar).quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP)
    return actual_base - recorded_base


def quantize_money(value) -> Decimal:
    """统一量化到两位小数（ROUND_HALF_UP）。"""
    return to_money(value).quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP)
