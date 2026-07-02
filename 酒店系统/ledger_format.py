# -*- coding: utf-8 -*-
"""统一账本流水类型展示（P1）：原始 tx_type + 经营分类后缀，与 database 营业额/押金口径一致。"""
from __future__ import annotations


_TX_LABEL_KEYS = {
    "ROOM_IN": "ledger_tx_room_in",
    "ROOM_OUT": "ledger_tx_room_out",
    "DEPOSIT_IN": "ledger_tx_deposit",
    "DEPOSIT_OUT": "ledger_tx_deposit",
    "SHOP": "ledger_tx_shop",
    "SHIFT_END": "ledger_tx_shift_end",
    "SHIFT_DIFF": "ledger_tx_shift_end",
    "PAYOUT": "ledger_tx_payout",
    "EXPENSE": "ledger_tx_payout",
    "TIP": "ledger_tx_shop",
    "LEGACY_IMPORT": "ledger_tx_adjust",
    "SHOP_PURCHASE": "ledger_tx_shop_purchase",
    "CASH_RECONCILE": "ledger_tx_cash_reconcile",
}


def ledger_tx_type_display(tx_type: str | None, is_deposit: int | None = 0) -> str:
    """
    返回「中文类型 · 分类」供财务表、审计表、看板流水等共用。
    is_deposit：ledger 行上的押金标记（与 tx_type 并列展示时一并考虑）。
    """
    from i18n import i18n

    t = (tx_type or "").strip() or "—"
    dep = int(is_deposit or 0)

    key = _TX_LABEL_KEYS.get(t)
    label = i18n.t(key) if key else t

    if t in ("DEPOSIT_IN", "DEPOSIT_OUT"):
        tag = i18n.t("ledger_tag_deposit")
    elif t in ("ROOM_IN", "SHOP", "TIP", "LEGACY_IMPORT"):
        tag = i18n.t("ledger_tag_revenue")
    elif t in ("PAYOUT", "EXPENSE"):
        tag = i18n.t("ledger_tag_expense")
    elif t in ("SHIFT_DIFF", "SHIFT_END", "NIGHT_AUDIT", "CASH_RECONCILE"):
        tag = i18n.t("ledger_tag_shift_audit")
    elif t in ("CASH_IN",):
        tag = i18n.t("ledger_tag_cash_in")
    elif t in ("PAYOUT_PENDING",):
        tag = i18n.t("ledger_tag_pending")
    else:
        tag = i18n.t("ledger_tag_other")

    if dep and t not in ("DEPOSIT_IN", "DEPOSIT_OUT"):
        tag = f"{tag}+{i18n.t('ledger_flag_deposit_col')}"
    return f"{label} · {tag}"
