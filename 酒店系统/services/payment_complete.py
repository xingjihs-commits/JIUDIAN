"""支付完成 — 写 ledger 关联 + payment_records 收款流水 + 库存扣减事件。

[sub-d Task2] 在发 inventory_deduct 事件之前，先写 payment_records：
  - payment_records 是对账辅助表，不参与 ledger 哈希链
  - 失败仅 log，不阻断主流程
  - payment_id 用 uuid4 生成（写入 payment_tx_id 列）
"""
from __future__ import annotations

import logging
import uuid

from event_bus import bus

logger = logging.getLogger(__name__)


def _write_payment_record(payment_data: dict) -> None:
    """[sub-d Task2] 把收款流水写入 payment_records 表。

    payment_records 表（db_schema.py / db_migration v22）实际字段：
      payment_tx_id, guest_id, checkin_id, order_id, reference_no,
      amount, currency, exchange_rate, pay_method, created_at, note
    任务说明里的 payment_id / operator_id 列在实际 schema 中不存在，故：
      - payment_id：用 uuid4 生成，写入 payment_tx_id（若调用方未提供 tx_id）
      - operator_id：写入 note 字段（拼接 operator + items 描述）
    """
    try:
        from database import db
        from money_utils import base_currency, quantize_money
    except Exception as e:
        logger.warning("[payment_complete] 数据库模块不可用，跳过 payment_records: %s", e)
        return

    tx_id = (payment_data.get("tx_id") or "").strip()
    if not tx_id:
        # 调用方未提供 tx_id 时用 uuid4 生成 payment_id 落到 payment_tx_id 列
        tx_id = f"PAY_{uuid.uuid4().hex[:12]}"

    room_id = payment_data.get("room_id") or ""
    checkin_id = payment_data.get("checkin_id") or None
    reference_no = payment_data.get("reference_no") or ""
    order_id = payment_data.get("order_id") or ""
    amount = float(payment_data.get("amount") or 0)
    # 兼容两种 key：currency / 直接根据 pay_method 推断
    currency = (payment_data.get("currency") or "").strip().upper()
    if not currency:
        # 从 pay_method 推断：CASH_KHR → KHR，其他默认 USD
        pm = str(payment_data.get("pay_method") or payment_data.get("method") or "").upper()
        currency = "KHR" if "KHR" in pm else base_currency()
    pay_method = (payment_data.get("pay_method") or payment_data.get("method") or "CASH").strip()
    operator_id = str(payment_data.get("operator_id") or "")
    items = payment_data.get("items") or []

    # 汇率：从 services.exchange_rate 取（外币折本位币）；失败回退 1.0
    exchange_rate = 1.0
    try:
        from services.exchange_rate import get_rate
        rate_dec = get_rate(currency)
        exchange_rate = float(rate_dec)
    except Exception:
        pass

    # 拼接 note：operator + items 数量 + 房间
    item_count = len(items)
    note_parts = []
    if operator_id:
        note_parts.append(f"op={operator_id}")
    if room_id:
        note_parts.append(f"room={room_id}")
    if item_count:
        note_parts.append(f"items={item_count}")
    note = " ".join(note_parts)

    try:
        amount_q = float(quantize_money(amount))
    except Exception:
        amount_q = round(amount, 2)

    try:
        db.execute(
            "INSERT INTO payment_records "
            "(payment_tx_id, checkin_id, order_id, reference_no, amount, "
            " currency, exchange_rate, pay_method, note) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (tx_id, checkin_id, order_id, reference_no, amount_q,
             currency, exchange_rate, pay_method, note),
        )
    except Exception as e:
        # 对账辅助表，失败仅 log，不阻断
        logger.warning("[payment_complete] 写 payment_records 失败（不阻断主流程）: %s", e)


def complete_payment(payment_data: dict) -> None:
    """收银完成后触发库存扣减等业务事件。

    [sub-d Task2] 在发 inventory_deduct 事件之前，先尝试写 payment_records。
    payment_records 写入失败不影响库存扣减与后续主流程。
    """
    # [sub-d Task2] 写收款流水到 payment_records（对账辅助表）
    try:
        _write_payment_record(payment_data)
    except Exception as e:
        logger.warning("[payment_complete] _write_payment_record 异常（不阻断）: %s", e)

    items = payment_data.get("items") or []
    room_id = payment_data.get("room_id") or ""
    tx_id = payment_data.get("tx_id") or ""
    for item in items:
        product_id = item.get("product_id") or item.get("sku")
        qty = float(item.get("quantity") or item.get("qty") or 0)
        if not product_id or qty <= 0:
            continue
        bus.inventory_deduct.emit({
            "product_id": str(product_id),
            "quantity": qty,
            "room_id": room_id,
            "tx_id": tx_id,
        })
