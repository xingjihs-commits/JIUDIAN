# -*- coding: utf-8 -*-
"""库存模块：超市商品采购入库（箱数×每箱件数、进价）。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from database import db
from i18n import i18n
from ui_helpers import show_error, show_info, show_warning, style_dialog, build_dialog_header


def list_shop_items_for_combo():
    return db.execute(
        """
        SELECT sku, name,
               COALESCE(price, 0),
               COALESCE(cost_price, 0),
               COALESCE(pack_label, '箱'),
               COALESCE(units_per_pack, 1),
               COALESCE(stock, 0)
        FROM shop_items
        ORDER BY name
        """
    ).fetchall()


class ShopPurchaseDialog(QDialog):
    """采购入库：箱数 × 每箱数量 → 增加库存，并记录进价。"""

    def __init__(self, parent=None, *, sku: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("shop_purchase_title"))
        style_dialog(self, size="small")
        self._items = list_shop_items_for_combo()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)
        outer.addWidget(
            build_dialog_header(
                i18n.t("shop_purchase_title"),
                i18n.t("shop_purchase_sub"),
            )
        )

        form = QFormLayout()
        self.cmb_sku = QComboBox()
        for row in self._items:
            sku_v, name, *_rest = row
            self.cmb_sku.addItem(f"{sku_v} · {name}", sku_v)
        if sku:
            idx = self.cmb_sku.findData(sku)
            if idx >= 0:
                self.cmb_sku.setCurrentIndex(idx)
        self.cmb_sku.currentIndexChanged.connect(self._on_sku_changed)
        form.addRow(i18n.t("inventory_col_sku") + ":", self.cmb_sku)

        self.lbl_spec = QLabel()
        self.lbl_spec.setWordWrap(True)
        self.lbl_spec.setObjectName("FdMutedLabel")
        form.addRow(i18n.t("shop_pack_spec") + ":", self.lbl_spec)

        self.spn_boxes = QSpinBox()
        self.spn_boxes.setRange(1, 9999)
        self.spn_boxes.setValue(1)
        self.spn_boxes.valueChanged.connect(self._recalc)
        form.addRow(i18n.t("shop_pack_count") + ":", self.spn_boxes)

        self.spn_per_pack = QSpinBox()
        self.spn_per_pack.setRange(1, 99999)
        self.spn_per_pack.setValue(1)
        self.spn_per_pack.valueChanged.connect(self._recalc)
        form.addRow(i18n.t("shop_units_per_pack") + ":", self.spn_per_pack)

        self.spn_cost_unit = QDoubleSpinBox()
        self.spn_cost_unit.setRange(0, 999999)
        self.spn_cost_unit.setDecimals(2)
        self.spn_cost_unit.setPrefix(i18n.t("currency_symbol"))
        self.spn_cost_unit.valueChanged.connect(self._on_cost_unit_changed)
        form.addRow(i18n.t("shop_cost_per_unit") + ":", self.spn_cost_unit)

        self.spn_cost_pack = QDoubleSpinBox()
        self.spn_cost_pack.setRange(0, 999999)
        self.spn_cost_pack.setDecimals(2)
        self.spn_cost_pack.setPrefix(i18n.t("currency_symbol"))
        self.spn_cost_pack.valueChanged.connect(self._on_cost_pack_changed)
        form.addRow(i18n.t("shop_cost_per_pack") + ":", self.spn_cost_pack)

        self.lbl_sale = QLabel()
        form.addRow(i18n.t("shop_sale_price") + ":", self.lbl_sale)

        self.lbl_summary = QLabel()
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet("font-weight:600;")
        form.addRow(i18n.t("shop_purchase_summary") + ":", self.lbl_summary)

        outer.addLayout(form)

        row = QHBoxLayout()
        row.addStretch()
        btn_ok = QPushButton(i18n.t("shop_purchase_confirm"))
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_ok.clicked.connect(self._confirm)
        btn_cancel = QPushButton(i18n.t("word_cancel"))
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        outer.addLayout(row)

        self._on_sku_changed()
        self._recalc()

    def _current_row(self):
        sku = self.cmb_sku.currentData()
        for row in self._items:
            if row[0] == sku:
                return row
        return self._items[0]

    def _on_sku_changed(self):
        row = self._current_row()
        sku, name, sale, cost, pack_label, units_per_pack, stock = row
        pack_label = (pack_label or i18n.t("shop_pack_default")).strip() or i18n.t("shop_pack_default")
        upp = max(1, int(units_per_pack or 1))
        self.spn_per_pack.blockSignals(True)
        self.spn_per_pack.setValue(upp)
        self.spn_per_pack.blockSignals(False)
        self.spn_cost_unit.blockSignals(True)
        self.spn_cost_unit.setValue(float(cost or 0))
        self.spn_cost_unit.blockSignals(False)
        self._sync_cost_pack_from_unit()
        cur = i18n.t("currency_symbol")
        self.lbl_sale.setText(f"{cur}{float(sale or 0):.2f}")
        self.lbl_spec.setText(
            i18n.t("shop_spec_line").format(
                pack=pack_label,
                upp=upp,
                stock=int(stock or 0),
                cost=f"{cur}{float(cost or 0):.2f}",
            )
        )
        self._recalc()

    def _on_cost_unit_changed(self):
        self._sync_cost_pack_from_unit()
        self._recalc()

    def _on_cost_pack_changed(self):
        upp = max(1, self.spn_per_pack.value())
        pack_cost = self.spn_cost_pack.value()
        self.spn_cost_unit.blockSignals(True)
        self.spn_cost_unit.setValue(pack_cost / upp if upp else 0)
        self.spn_cost_unit.blockSignals(False)
        self._recalc()

    def _sync_cost_pack_from_unit(self):
        upp = max(1, self.spn_per_pack.value())
        unit = self.spn_cost_unit.value()
        self.spn_cost_pack.blockSignals(True)
        self.spn_cost_pack.setValue(unit * upp)
        self.spn_cost_pack.blockSignals(False)

    def _recalc(self):
        boxes = self.spn_boxes.value()
        upp = max(1, self.spn_per_pack.value())
        total_units = boxes * upp
        total_cost = self.spn_cost_pack.value() * boxes
        cur = i18n.t("currency_symbol")
        row = self._current_row()
        pack_label = (row[4] or i18n.t("shop_pack_default")).strip()
        self.lbl_summary.setText(
            i18n.t("shop_purchase_calc").format(
                boxes=boxes,
                pack=pack_label,
                upp=upp,
                units=total_units,
                cost=f"{cur}{total_cost:.2f}",
            )
        )

    def _confirm(self):
        sku = self.cmb_sku.currentData()
        if not sku:
            return
        boxes = self.spn_boxes.value()
        upp = max(1, self.spn_per_pack.value())
        cost_unit = self.spn_cost_unit.value()
        cost_pack = self.spn_cost_pack.value()
        try:
            from permission_system import PermissionManager

            op = PermissionManager.current_user()
            op_id = str(op.get("id", "SYSTEM")) if op else "SYSTEM"
            total_units, total_cost = db.record_shop_purchase(
                sku,
                pack_count=boxes,
                units_per_pack=upp,
                cost_per_unit=cost_unit,
                cost_per_pack=cost_pack,
                operator_id=op_id,
            )
            show_info(
                self,
                i18n.t("msg_success"),
                i18n.t("shop_purchase_done").format(units=total_units, cost=f"{i18n.t('currency_symbol')}{total_cost:.2f}"),
            )
            self.accept()
        except Exception as e:
            show_error(self, i18n.t("dlg_tip"), str(e))


def open_shop_purchase_dialog(parent=None, *, sku: str | None = None) -> bool:
    if not list_shop_items_for_combo():
        show_warning(parent, i18n.t("dlg_tip"), i18n.t("shop_empty_catalog"))
        return False
    dlg = ShopPurchaseDialog(parent, sku=sku)
    return dlg.exec() == QDialog.DialogCode.Accepted


# ═══════════════════════════════════════════════════════════════
#  Round 3.3 增强：库存预警 / ABC 分析 / 定额管理 / 过期锁定
# ═══════════════════════════════════════════════════════════════

def check_low_stock_alerts() -> list[dict]:
    """检查库存低于安全库存的商品列表。"""
    alerts = []
    try:
        rows = db.execute(
            "SELECT sku, name, COALESCE(stock,0), COALESCE(safety_stock,10) "
            "FROM shop_items WHERE stock < safety_stock"
        ).fetchall()
        for r in rows:
            alerts.append({
                "sku": r[0], "name": r[1], "current": int(r[2]), "safety": int(r[3] or 10),
            })
    except Exception:
        pass
    return alerts


def send_low_stock_telegram():
    """低库存通知。"""
    try:
        from telegram_notify import send_telegram
        alerts = check_low_stock_alerts()
        if alerts:
            lines = ["⚠️ 库存预警", ""]
            for a in alerts[:5]:
                lines.append(f"• {a['name']} — 现存 {a['current']} / 安全线 {a['safety']}")
            if len(alerts) > 5:
                lines.append(f"...共 {len(alerts)} 件商品低于安全库存")
            send_telegram("\n".join(lines))
    except Exception:
        pass


def abc_analysis() -> dict:
    """ABC 分类法：A类（前70%销售额）、B类（70-90%）、C类（90-100%）。"""
    rows = db.execute(
        "SELECT s.name, COALESCE(SUM(l.amount),0) as revenue, COUNT(*) as cnt "
        "FROM shop_items s LEFT JOIN ledger l ON l.note=s.name AND l.tx_type='SHOP' "
        "GROUP BY s.sku ORDER BY revenue DESC"
    ).fetchall()
    total_rev = sum(float(r[1] or 0) for r in rows)
    result = {"A": [], "B": [], "C": [], "total_revenue": total_rev}
    cumulative = 0.0
    for r in rows:
        rev = float(r[1] or 0)
        cumulative += rev
        pct = cumulative / max(total_rev, 0.01) * 100
        item = {"name": r[0], "revenue": rev, "count": r[2], "margin_pct": pct}
        if pct <= 70:
            result["A"].append(item)
        elif pct <= 90:
            result["B"].append(item)
        else:
            result["C"].append(item)
    return result


def get_consumable_standards(room_type: str) -> dict:
    """获取指定房型的消耗品定额。"""
    row = db.execute(
        "SELECT hk_consumables_deep_json FROM room_type_templates WHERE type_id=?",
        (room_type,),
    ).fetchone()
    if row and row[0]:
        import json
        try:
            return json.loads(row[0])
        except Exception:
            pass
    return {"towels": 2, "toothbrush": 1, "shampoo": 1, "soap": 1, "slippers": 1}


def check_expired_items() -> list[dict]:
    """检查过期库存，自动锁定。"""
    expired = []
    try:
        now = datetime.now().strftime("%Y-%m-%d")
        rows = db.execute(
            "SELECT sku, name, stock, expiry_date FROM shop_items WHERE expiry_date < ? AND stock > 0",
            (now,),
        ).fetchall()
        for r in rows:
            expired.append({"sku": r[0], "name": r[1], "stock": int(r[2] or 0), "expiry": r[3]})
            # 自动锁定过期商品
            db.execute("UPDATE shop_items SET stock=0, status='LOCKED' WHERE sku=?", (r[0],))
    except Exception:
        pass
    return expired


def stocktake_needs_approval(session_id: str, variance_threshold: float = 5.0) -> bool:
    """盘点差异 > threshold% 是否需要审批。"""
    try:
        row = db.execute(
            "SELECT SUM(ABS(expected_qty - counted_qty)), SUM(expected_qty) "
            "FROM inventory_stocktake_lines WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row and row[1] and row[1] > 0:
            variance_pct = (float(row[0] or 0) / float(row[1])) * 100
            return variance_pct > variance_threshold
    except Exception:
        pass
    return False


def deduct_stock_from_event(event: dict) -> None:
    """收银 inventory.deduct 事件 — 扣减 shop_items.stock。"""
    sku = str(event.get("product_id") or event.get("sku") or "").strip()
    qty = float(event.get("quantity") or event.get("qty") or 0)
    if not sku or qty <= 0:
        return
    db.execute(
        "UPDATE shop_items SET stock = MAX(0, COALESCE(stock,0) - ?) WHERE sku=?",
        (qty, sku),
    )
    row = db.execute(
        "SELECT name, COALESCE(stock,0), COALESCE(min_stock,0) FROM shop_items WHERE sku=?",
        (sku,),
    ).fetchone()
    if row and float(row[1] or 0) < float(row[2] or 0):
        try:
            from event_bus import bus
            bus.audit_alert.emit("inventory_low", f"{row[0]} 库存低: {row[1]}")
        except Exception:
            pass


# [sub-a] 库存闭环：销售退货时回补库存的统一入口
def restore_stock_for_refund(sku: str, qty: int) -> bool:
    """销售退货 / 订单作废时回补 shop_items.stock。

    Args:
        sku: 商品 SKU
        qty: 回补数量（正整数）

    Returns:
        True 表示回补成功（SKU 存在且 qty > 0）

    为什么需要这个函数：
      - 原代码在 shop_frontdesk._cancel_pending 里直接调 db.adjust_shop_stock(sku, +qty)，
        分散在各处难审计；
      - 集中到本函数后，退款回补逻辑可被 transactions/refund.py 复用，
        也便于在调试面板统一查询"库存回补历史"。
    """
    sku = (sku or "").strip()
    try:
        q = int(qty)
    except (TypeError, ValueError):
        return False
    if not sku or q <= 0:
        return False
    if not db.execute("SELECT 1 FROM shop_items WHERE sku=?", (sku,)).fetchone():
        return False
    db.execute(
        "UPDATE shop_items SET stock = COALESCE(stock,0) + ? WHERE sku=?",
        (q, sku),
    )
    return True


def _register_inventory_listeners() -> None:
    try:
        from event_bus import bus
        bus.inventory_deduct.connect(deduct_stock_from_event)
    except Exception:
        pass


_register_inventory_listeners()
