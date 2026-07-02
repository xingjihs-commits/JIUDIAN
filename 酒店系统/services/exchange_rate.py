"""汇率管理 — Phase2 #7

[sub-a] 扩展：
- get_rate(currency) -> Decimal：checkout / 收款时按本位币折算的统一入口
- get_rate_at(currency, effective_date) -> Decimal：按历史日期取汇率（对账用）
- 默认本位币取自 system_config.base_currency（缺省 USD）
- 查不到汇率时返回 Decimal('1')，避免上层除零；调用方可据此告警
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from database import db

logger = logging.getLogger(__name__)


def set_exchange_rate(from_currency: str, to_currency: str, rate: float, effective_date: str, source: str = "") -> None:
    """写入一条汇率记录（from→to）。参数化 SQL，禁止字符串拼接。"""
    db.execute(
        "INSERT INTO exchange_rates(from_currency, to_currency, rate, effective_date, source) VALUES (?,?,?,?,?)",
        (from_currency.upper(), to_currency.upper(), float(rate), effective_date, source),
    )


def get_rate(from_currency: str, to_currency: str, effective_date: str) -> float | None:
    """精确按日期取汇率（旧接口，保留兼容）。返回 float 或 None。"""
    row = db.execute(
        "SELECT rate FROM exchange_rates WHERE from_currency=? AND to_currency=? AND effective_date=? ORDER BY id DESC LIMIT 1",
        (from_currency.upper(), to_currency.upper(), effective_date),
    ).fetchone()
    return float(row[0]) if row else None


# ── [sub-a] 新增：本位币折算统一入口 ───────────────────────────────


def _base_currency() -> str:
    """读取本位币代码（system_config.base_currency，缺省 USD）。

    放在此处而非 money_utils 是因为 money_utils 应保持零 DB 依赖，
    而 exchange_rate 本身就操作数据库。
    """
    try:
        v = db.get_config("base_currency")
        if v and isinstance(v, str) and v.strip():
            return v.strip().upper()
    except Exception:
        pass
    return "USD"


def get_rate_to_base(currency: str, effective_date: str | None = None) -> Decimal:
    """取 `currency → 本位币` 的汇率，返回 Decimal。

    优先按 effective_date 精确匹配；找不到时取该日期之前最近的一条；
    再找不到返回 Decimal('1')（同币种或未配置汇率场景）。

    Args:
        currency: 原币种代码，如 'USD' / 'KHR' / 'CNY'
        effective_date: ISO 日期字符串；None 则用今天

    Returns:
        Decimal 汇率，永远 > 0
    """
    cur = (currency or "").strip().upper()
    if not cur:
        return Decimal("1")
    base = _base_currency()
    if cur == base:
        return Decimal("1")
    day = effective_date or date.today().isoformat()

    # 1. 精确日期
    row = db.execute(
        "SELECT rate FROM exchange_rates "
        "WHERE from_currency=? AND to_currency=? AND effective_date=? "
        "ORDER BY id DESC LIMIT 1",
        (cur, base, day),
    ).fetchone()
    if row and row[0]:
        return Decimal(str(row[0]))

    # 2. <= effective_date 的最近一条（汇率有效期回溯）
    row = db.execute(
        "SELECT rate FROM exchange_rates "
        "WHERE from_currency=? AND to_currency=? AND effective_date<=? "
        "ORDER BY effective_date DESC, id DESC LIMIT 1",
        (cur, base, day),
    ).fetchone()
    if row and row[0]:
        return Decimal(str(row[0]))

    # 3. 任意最近一条（无日期约束）
    row = db.execute(
        "SELECT rate FROM exchange_rates "
        "WHERE from_currency=? AND to_currency=? "
        "ORDER BY effective_date DESC, id DESC LIMIT 1",
        (cur, base),
    ).fetchone()
    if row and row[0]:
        return Decimal(str(row[0]))

    logger.warning("[exchange_rate] 未找到 %s→%s 的汇率，按 1.0 处理", cur, base)
    return Decimal("1")


def get_rate(currency: str, effective_date: str | None = None) -> Decimal:
    """[sub-a] 任务要求的统一接口：`currency → 本位币` 的 Decimal 汇率。

    与 get_rate_to_base 等价；提供这个短名是为了让 transactions/checkout.py
    调用方代码更短。永远返回 > 0 的 Decimal。
    """
    return get_rate_to_base(currency, effective_date)


def get_rate_at(currency: str, effective_date: str) -> Decimal:
    """[sub-a] 按历史日期取汇率（reconciliation_service 对账时使用）。

    与 get_rate 的区别：强制要求日期参数，且找不到精确匹配时回溯到 <= 该日期的最近一条，
    便于对账时还原当时记账应使用的汇率。
    """
    return get_rate_to_base(currency, effective_date)
