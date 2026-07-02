"""Daily ledger reconciliation service (Track-B W1D2).

[sub-a] 增强：
  - daily_reconcile(): 保留原单币种对账逻辑（向后兼容）
  - daily_reconcile_multi_currency(): 多币种对账
      * 按 ledger.currency 分组汇总原币金额
      * 按 ledger.exchange_rate 折算到本位币
      * 与按对账日汇率折算的本位币金额比对，输出汇兑损益
      * 输出对账差异报告（dict）
  - 所有金额运算走 money_utils（Decimal），不再用 float 累加
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from database import (
    LEDGER_CASH_NET_TX_TYPES,
    _sql_in_types,
    db,
)

_CASHIER_TX = _sql_in_types(LEDGER_CASH_NET_TX_TYPES)


def daily_reconcile(date_str: str) -> dict[str, Any]:
    """按营业日对账 ledger 收收银流水，返回汇总与异常明细（单币种，向后兼容）。

    [sub-a] 内部改用 money_utils.Decimal 累加，外层接口不变（输出 float 兼容旧调用方）。
    """
    from money_utils import quantize_money

    date_filter = "date(created_at)=?"
    params = (date_str,)

    ledger_total = float(
        db.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM ledger "
            f"WHERE {date_filter} AND tx_type IN ({_CASHIER_TX})",
            params,
        ).fetchone()[0]
        or 0
    )

    room_revenue_total = float(
        db.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM ledger "
            f"WHERE {date_filter} AND tx_type='ROOM_IN'",
            params,
        ).fetchone()[0]
        or 0
    )

    mismatches: list[dict[str, Any]] = []

    bad_pay_rows = db.execute(
        f"""
        SELECT id, room_id, tx_type, amount, COALESCE(pay_method,'')
        FROM ledger
        WHERE {date_filter} AND tx_type IN ({_CASHIER_TX})
          AND (pay_method IS NULL OR TRIM(pay_method)='' OR pay_method='UNKNOWN')
        ORDER BY id
        """,
        params,
    ).fetchall()
    for lid, room_id, tx_type, amount, pay_method in bad_pay_rows:
        mismatches.append(
            {
                "kind": "missing_pay_method",
                "ledger_id": int(lid),
                "room_id": room_id or "",
                "tx_type": tx_type or "",
                "amount": float(quantize_money(amount or 0)),
                "pay_method": pay_method,
            }
        )

    room_mix_rows = db.execute(
        f"""
        SELECT room_id,
               COALESCE(SUM(CASE WHEN tx_type='ROOM_IN' THEN amount ELSE 0 END),0) AS room_in,
               COALESCE(SUM(amount),0) AS room_cash_total
        FROM ledger
        WHERE {date_filter} AND tx_type IN ({_CASHIER_TX})
          AND COALESCE(room_id,'') NOT IN ('','__REGISTRY__')
        GROUP BY room_id
        HAVING ABS(room_in - room_cash_total) > 0.01
        ORDER BY room_id
        """,
        params,
    ).fetchall()
    for room_id, room_in, room_cash_total in room_mix_rows:
        mismatches.append(
            {
                "kind": "room_payment_mix",
                "room_id": room_id,
                "room_in": float(quantize_money(room_in or 0)),
                "room_cash_total": float(quantize_money(room_cash_total or 0)),
                "delta": float(quantize_money(float(room_cash_total or 0) - float(room_in or 0))),
            }
        )

    audit_row = db.execute(
        "SELECT room_revenue FROM business_day_audit WHERE business_date=?",
        (date_str,),
    ).fetchone()
    if audit_row is not None:
        audit_room = float(audit_row[0] or 0)
        audit_delta = float(quantize_money(float(room_revenue_total) - audit_room))
        if abs(audit_delta) > 0.01:
            mismatches.append(
                {
                    "kind": "audit_room_revenue",
                    "ledger_room_in": float(quantize_money(room_revenue_total)),
                    "audit_room_revenue": float(quantize_money(audit_room)),
                    "delta": audit_delta,
                }
            )

    diff = float(quantize_money(float(ledger_total) - float(room_revenue_total)))

    return {
        "date": date_str,
        "ledger_total": float(quantize_money(ledger_total)),
        "room_revenue_total": float(quantize_money(room_revenue_total)),
        "diff": diff,
        "mismatches": mismatches,
    }


# ── [sub-a] 多币种对账 + 汇兑损益 ───────────────────────────────────


def daily_reconcile_multi_currency(date_str: str) -> dict[str, Any]:
    """[sub-a] 多币种对账：按 ledger.currency 分组 + 按当时汇率折算本位币 + 汇兑损益。

    业务背景：原 daily_reconcile 把所有金额当本位币相加，外币收款被低估/高估，
    财务月报与银行对账单差异无法解释。

    本函数：
      1. 按 (currency, tx_type) 分组汇总原币金额
      2. 按 ledger.exchange_rate（当时记账汇率）折算本位币小计
      3. 调 services.exchange_rate.get_rate_at(currency, date_str) 取对账日汇率
      4. 计算汇兑损益 = (对账日汇率 - 记账汇率) × 原币金额
      5. 输出差异行清单 + 本位币合计 + 汇兑损益合计

    所有金额走 money_utils（Decimal），无 float 累计误差。
    """
    from money_utils import (
        base_currency,
        exchange_gain_loss,
        quantize_money,
        to_base,
        to_money,
    )
    from services.exchange_rate import get_rate_at

    base = base_currency()

    # 按币种分组的收银流水（含收银 tx_type）
    rows = db.execute(
        f"""
        SELECT COALESCE(currency, ?) AS cur,
               tx_type,
               COALESCE(SUM(amount), 0) AS orig_total,
               AVG(COALESCE(exchange_rate, 1.0)) AS avg_rate
        FROM ledger
        WHERE date(created_at)=? AND tx_type IN ({_CASHIER_TX})
        GROUP BY cur, tx_type
        ORDER BY cur, tx_type
        """,
        (base, date_str),
    ).fetchall()

    by_currency: dict[str, dict[str, Any]] = {}
    grand_total_base = Decimal("0")
    exchange_gain_loss_total = Decimal("0")
    diff_lines: list[dict[str, Any]] = []

    for cur, tx_type, orig_total, avg_rate in rows:
        currency = (cur or base).upper()
        orig_dec = to_money(orig_total)
        recorded_rate = to_money(avg_rate) or Decimal("1")
        # 折本位币小计（按记账汇率）
        base_subtotal = to_base(orig_dec, currency, recorded_rate)
        # 对账日汇率折算
        actual_rate = get_rate_at(currency, date_str) if currency != base else Decimal("1")
        actual_base = to_base(orig_dec, currency, actual_rate)
        # 汇兑损益
        gain_loss = exchange_gain_loss(orig_dec, recorded_rate, actual_rate)

        if currency not in by_currency:
            by_currency[currency] = {
                "currency": currency,
                "orig_subtotal": Decimal("0"),
                "base_subtotal": Decimal("0"),
                "actual_base": Decimal("0"),
                "exchange_gain_loss": Decimal("0"),
                "by_tx_type": [],
            }
        c = by_currency[currency]
        c["orig_subtotal"] += orig_dec
        c["base_subtotal"] += base_subtotal
        c["actual_base"] += actual_base
        c["exchange_gain_loss"] += gain_loss
        c["by_tx_type"].append({
            "tx_type": tx_type or "",
            "orig": float(quantize_money(orig_dec)),
            "recorded_rate": float(quantize_money(recorded_rate)),
            "actual_rate": float(quantize_money(actual_rate)),
            "base_subtotal": float(quantize_money(base_subtotal)),
            "actual_base": float(quantize_money(actual_base)),
            "exchange_gain_loss": float(quantize_money(gain_loss)),
        })

        grand_total_base += base_subtotal
        exchange_gain_loss_total += gain_loss

        # 单笔差异超阈值（0.01 本位币）才输出到 diff_lines
        if abs(gain_loss) > Decimal("0.01"):
            diff_lines.append({
                "kind": "exchange_rate_diff",
                "currency": currency,
                "tx_type": tx_type or "",
                "orig": float(quantize_money(orig_dec)),
                "recorded_rate": float(quantize_money(recorded_rate)),
                "actual_rate": float(quantize_money(actual_rate)),
                "exchange_gain_loss": float(quantize_money(gain_loss)),
            })

    # 缺失 pay_method 的异常（复用原逻辑）
    bad_pay_rows = db.execute(
        f"""
        SELECT id, room_id, tx_type, amount, COALESCE(pay_method,''), COALESCE(currency,?)
        FROM ledger
        WHERE date(created_at)=? AND tx_type IN ({_CASHIER_TX})
          AND (pay_method IS NULL OR TRIM(pay_method)='' OR pay_method='UNKNOWN')
        ORDER BY id
        """,
        (base, date_str),
    ).fetchall()
    for lid, room_id, tx_type, amount, pay_method, cur in bad_pay_rows:
        diff_lines.append({
            "kind": "missing_pay_method",
            "ledger_id": int(lid),
            "room_id": room_id or "",
            "tx_type": tx_type or "",
            "amount": float(quantize_money(amount or 0)),
            "currency": (cur or base).upper(),
            "pay_method": pay_method,
        })

    return {
        "date": date_str,
        "base_currency": base,
        "by_currency": [
            {
                "currency": c["currency"],
                "orig_subtotal": float(quantize_money(c["orig_subtotal"])),
                "base_subtotal": float(quantize_money(c["base_subtotal"])),
                "actual_base": float(quantize_money(c["actual_base"])),
                "exchange_gain_loss": float(quantize_money(c["exchange_gain_loss"])),
                "by_tx_type": c["by_tx_type"],
            }
            for c in by_currency.values()
        ],
        "grand_total_base": float(quantize_money(grand_total_base)),
        "exchange_gain_loss_total": float(quantize_money(exchange_gain_loss_total)),
        "diff_lines": diff_lines,
    }
