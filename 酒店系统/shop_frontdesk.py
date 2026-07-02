# -*- coding: utf-8 -*-
"""前台超市：统一布局 · 金线分区 · 紧凑表单（支持云端订单载入、现金/押金）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import db
from design_tokens import _p
from event_bus import bus
from frontdesk_ui import (
    FD_MARGIN,
    FD_SPACE,
    FD_SPACE_SM,
    FD_SPACE_MD,
    FD_SPACE_LG,
    FD_BTN_H,
    FD_BTN_H_PRIMARY,
    FD_BTN_H_LOW,
    FD_INPUT_H,
    FD_LABEL_INPUT_GAP,
    FD_INPUT_GAP,
    FD_INPUT_WIDTHS,
    FD_SECTION_BAR_H,
    FD_CARD_PADDING,
    FD_CARD_RADIUS,
    fd_section_bar,
    fd_section_title,
    fd_apply_action_btn,
    fd_apply_card_action_btn,
    fd_apply_low_freq_btn,
    fd_apply_compact_input,
    fd_card,
    fd_card_layout,
    fd_compact_form_row,
)
from i18n import i18n
from sound_helper import play_notify, play_warn
from ui_helpers import ask_confirm, show_info, show_warning
from shop_assets import load_shop_icon
# [sub-i] 图标包：emoji 兜底 + 分类图标统一解析层
from shop_icon_pack import icon_pack
import logging
logger = logging.getLogger(__name__)


# [sub-i] 库存预警阈值：≤ 此值在前台商品名前加 ⚠️ 标记
LOW_STOCK_WARN_THRESHOLD = 3


# ── 购物车数据结构 ────────────────────────────────────────────────

@dataclass
class CartLine:
    sku: str
    name: str
    unit_price: float
    qty: int = 1
    source: str = "manual"  # manual | cloud

    @property
    def subtotal(self) -> float:
        return self.unit_price * self.qty


@dataclass
class ShopCart:
    lines: List[CartLine] = field(default_factory=list)
    pending_ids: List[str] = field(default_factory=list)
    room_id: str = ""

    def total(self) -> float:
        return sum(ln.subtotal for ln in self.lines)

    def merge_line(self, sku: str, name: str, price: float, qty: int) -> None:
        qty = max(1, int(qty))
        for ln in self.lines:
            if ln.sku == sku:
                ln.qty += qty
                return
        self.lines.append(CartLine(sku=sku, name=name, unit_price=price, qty=qty))

    def clear(self) -> None:
        self.lines.clear()
        self.pending_ids.clear()


# ── 辅助函数 ──────────────────────────────────────────────────────

def get_room_deposit_balance(room_id: str) -> float:
    if not (room_id or "").strip():
        return 0.0
    row = db.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM ledger
        WHERE room_id=? AND is_deposit=1
          AND tx_type IN ('DEPOSIT_IN', 'DEPOSIT_OUT')
        """,
        (room_id.strip(),),
    ).fetchone()
    return float(row[0] if row else 0)


# ── 页面主体 ──────────────────────────────────────────────────────

class ShopTab(QWidget):
    """统一单列布局：待处理 → 商品选购 → 购物车 & 结账。"""

    def __init__(self) -> None:
        super().__init__()
        self._cart = ShopCart()
        self._product_rows: list[tuple] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(FD_MARGIN, FD_SPACE_MD, FD_MARGIN, FD_MARGIN)
        root.setSpacing(FD_SPACE_SM)

        # ── Section 1: 待处理订单 ──
        self._lbl_pending_count = QLabel("")
        self._lbl_pending_count.setObjectName("FdPendingCountBadge")
        root.addWidget(
            fd_section_bar(
                i18n.t("shop_section_pending"),
                self,
                action_widgets=[self._lbl_pending_count],
            )
        )

        self._pending_host = QWidget()
        self._pending_lay = QVBoxLayout(self._pending_host)
        self._pending_lay.setContentsMargins(FD_MARGIN, 0, FD_MARGIN, 0)
        self._pending_lay.setSpacing(FD_SPACE_SM)
        pend_scroll = QScrollArea()
        pend_scroll.setWidgetResizable(True)
        pend_scroll.setFrameShape(QFrame.Shape.NoFrame)
        pend_scroll.setMinimumHeight(120)
        pend_scroll.setMaximumHeight(200)
        pend_scroll.setWidget(self._pending_host)
        pend_scroll.setObjectName("FdScrollArea")
        root.addWidget(pend_scroll)

        # ── Section 2: 商品选购 ──
        lbl_qty = QLabel(i18n.t("shop_qty_label"))
        self.spn_add_qty = QSpinBox()
        self.spn_add_qty.setRange(1, 999)
        self.spn_add_qty.setValue(1)
        fd_apply_compact_input(self.spn_add_qty, width_key="days")

        self.btn_add_cart = QPushButton(i18n.t("shop_add_to_cart"))
        fd_apply_action_btn(self.btn_add_cart, primary=True)

        root.addWidget(
            fd_section_bar(
                i18n.t("shop_section_catalog"),
                self,
                action_widgets=[lbl_qty, self.spn_add_qty, self.btn_add_cart],
            )
        )

        # [sub-i] 分类筛选 + 分类图标行：左侧分类下拉，右侧分类图标 QLabel
        cat_filter_row = QHBoxLayout()
        cat_filter_row.setSpacing(FD_SPACE)
        cat_filter_row.setContentsMargins(0, 0, 0, 0)
        cat_filter_row.addWidget(QLabel(i18n.t("shop_category_label") if i18n.t("shop_category_label") != "shop_category_label" else "分类"))
        self.cmb_cat_filter = QComboBox()
        self.cmb_cat_filter.addItem(i18n.t("shop_cat_all") if i18n.t("shop_cat_all") != "shop_cat_all" else "全部", "")
        # 分类项在 _reload_catalog 时动态填充（保证与 DB 一致）
        fd_apply_compact_input(self.cmb_cat_filter, width_key="room_number")
        self.cmb_cat_filter.currentIndexChanged.connect(self._on_cat_filter_changed)
        cat_filter_row.addWidget(self.cmb_cat_filter, 1)
        # 分类图标 QLabel（QPixmap 显示 categories/{cat}.png）
        self.lbl_cat_icon = QLabel()
        self.lbl_cat_icon.setFixedSize(28, 28)
        self.lbl_cat_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_cat_icon.setObjectName("FdMutedLabel")
        cat_filter_row.addWidget(self.lbl_cat_icon)
        root.addLayout(cat_filter_row)

        self.tbl_products = QTableWidget(0, 4)
        self.tbl_products.setHorizontalHeaderLabels([
            i18n.t("table_sku"),
            i18n.t("shop_col_sale"),
            i18n.t("inventory_col_stock"),
            i18n.t("inventory_col_sku"),
        ])
        self.tbl_products.setColumnHidden(3, True)
        self.tbl_products.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.tbl_products.verticalHeader().setVisible(False)
        self.tbl_products.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_products.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl_products.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_products.doubleClicked.connect(self._add_selected_to_cart)
        # [sub-j] SolidCard 卡片包裹，圆角 10px + 1px panel_border + surface 实底
        product_box = QFrame()
        product_box.setObjectName("SolidCard")
        product_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        from ui_surface import fd_apply_table_palette
        pb_lay = QVBoxLayout(product_box)
        pb_lay.setContentsMargins(10, 10, 10, 10)
        pb_lay.addWidget(self.tbl_products)
        fd_apply_table_palette(self.tbl_products)
        root.addWidget(product_box, 1)

        # ── Section 3: 购物车 & 结账 ──
        root.addWidget(
            fd_section_bar(i18n.t("shop_cart_title"), self)
        )

        # 房间 + 押金余额 紧凑行
        lbl_room = QLabel(i18n.t("table_room"))
        self.cmb_room = QComboBox()
        fd_apply_compact_input(self.cmb_room, width_key="room_number")
        self.cmb_room.currentIndexChanged.connect(self._on_room_changed)
        self.lbl_deposit = QLabel()
        self.lbl_deposit.setObjectName("FdMutedLabel")
        room_row = fd_compact_form_row(
            lbl_room, self.cmb_room,
            self.lbl_deposit,
        )
        root.addLayout(room_row)

        # 购物车表格
        self.tbl_cart = QTableWidget(0, 5)
        self.tbl_cart.setHorizontalHeaderLabels([
            i18n.t("table_sku"),
            i18n.t("shop_cart_col_price"),
            i18n.t("shop_cart_col_qty"),
            i18n.t("shop_cart_col_sub"),
            i18n.t("table_op"),
        ])
        self.tbl_cart.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.tbl_cart.verticalHeader().setVisible(False)
        self.tbl_cart.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_cart.setMaximumHeight(220)
        # [sub-j] SolidCard 卡片包裹，圆角 10px + 1px panel_border + surface 实底
        cart_box = QFrame()
        cart_box.setObjectName("SolidCard")
        cart_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cb_lay = QVBoxLayout(cart_box)
        cb_lay.setContentsMargins(10, 10, 10, 10)
        cb_lay.addWidget(self.tbl_cart)
        fd_apply_table_palette(self.tbl_cart)
        root.addWidget(cart_box)

        # 合计 + 付款方式 紧凑行
        self.lbl_total = QLabel()
        self.lbl_total.setObjectName("FdTotalLabel")
        lbl_pay = QLabel(i18n.t("payment_method"))
        self.cmb_pay = QComboBox()
        self.cmb_pay.addItem(i18n.t("shop_pay_cash"), "CASH")
        self.cmb_pay.addItem(i18n.t("shop_pay_deposit"), "DEPOSIT")
        fd_apply_compact_input(self.cmb_pay, width_key="price")
        pay_row = fd_compact_form_row(
            self.lbl_total, lbl_pay, self.cmb_pay,
        )
        root.addLayout(pay_row)

        # 操作按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(FD_SPACE)
        btn_row.setContentsMargins(0, FD_SPACE_SM, 0, 0)

        self.btn_clear = QPushButton(i18n.t("shop_cart_clear"))
        fd_apply_low_freq_btn(self.btn_clear)
        self.btn_clear.clicked.connect(self._clear_cart)
        btn_row.addWidget(self.btn_clear)

        btn_row.addStretch()

        self.btn_checkout = QPushButton(i18n.t("shop_checkout"))
        fd_apply_action_btn(self.btn_checkout, primary=True)
        self.btn_checkout.clicked.connect(self._checkout)
        btn_row.addWidget(self.btn_checkout)
        root.addLayout(btn_row)

        from ui_surface import fd_apply_scroll_area, fd_connect_theme_refresh
        fd_apply_scroll_area(pend_scroll)

        # ── 事件连接 ──
        self.setObjectName("ShopTab")
        fd_connect_theme_refresh(self)
        bus.cart_received.connect(self._on_cloud_signal)
        self.refresh()

    # ── 刷新入口 ──────────────────────────────────────────────

    def refresh(self) -> None:
        self._reload_rooms()
        self._reload_pending()
        self._reload_catalog()
        self._render_cart()

    # ── 房间下拉 ──────────────────────────────────────────────

    def _reload_rooms(self) -> None:
        cur = self.cmb_room.currentData()
        self.cmb_room.blockSignals(True)
        self.cmb_room.clear()
        inhouse = db.execute(
            "SELECT room_id FROM rooms WHERE status='INHOUSE' ORDER BY room_id"
        ).fetchall()
        ids = [str(r[0]) for r in inhouse if r and r[0]]
        if not ids:
            ids = [
                str(r[0])
                for r in db.execute(
                    "SELECT room_id FROM rooms ORDER BY room_id LIMIT 80"
                ).fetchall()
                if r and r[0]
            ]
        for rid in ids:
            self.cmb_room.addItem(rid, rid)
        if self._cart.room_id:
            idx = self.cmb_room.findData(self._cart.room_id)
            if idx >= 0:
                self.cmb_room.setCurrentIndex(idx)
        elif cur:
            idx = self.cmb_room.findData(cur)
            if idx >= 0:
                self.cmb_room.setCurrentIndex(idx)
        self.cmb_room.blockSignals(False)
        self._on_room_changed()

    def _on_room_changed(self) -> None:
        rid = self.cmb_room.currentData() or self.cmb_room.currentText()
        self._cart.room_id = str(rid).strip() if rid else ""
        bal = get_room_deposit_balance(self._cart.room_id)
        cur = i18n.t("currency_symbol")
        self.lbl_deposit.setText(
            i18n.t("shop_deposit_balance").format(cur=cur, bal=f"{bal:.2f}")
        )

    # ── 布局清理 ──────────────────────────────────────────────

    def _clear_layout(self, box: QVBoxLayout) -> None:
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    # ── 待处理订单 ────────────────────────────────────────────

    def _reload_pending(self) -> None:
        self._clear_layout(self._pending_lay)
        rows = db.execute(
            """
            SELECT cart_id, room_id, total_amount, items_json,
                   COALESCE(payment_method,'CASH'),
                   COALESCE(cash_received,0),
                   COALESCE(cash_change,0),
                   COALESCE(delivery_status,''),
                   COALESCE(deliverer_id,''),
                   COALESCE(strftime('%H:%M', created_at), '')
            FROM pending_carts WHERE status='PENDING'
            ORDER BY created_at DESC LIMIT 20
            """
        ).fetchall()
        n = len(rows)
        self._lbl_pending_count.setText(
            f"{n}{i18n.t('shop_pending_count_unit')}" if n else ""
        )
        if not rows:
            lbl = QLabel(i18n.t("shop_no_pending"))
            lbl.setObjectName("FdMutedLabel")
            self._pending_lay.addWidget(lbl)
            return
        cur = i18n.t("currency_symbol")
        for (
            cart_id, room_id, total_amount, items_json,
            pay, cash_r, change, delivery_status, deliverer_id, created_hm,
        ) in rows:
            self._pending_lay.addWidget(
                self._build_pending_card(
                    cart_id=str(cart_id),
                    room_id=str(room_id or ""),
                    total=float(total_amount or 0),
                    items_json=str(items_json or ""),
                    pay=str(pay or "CASH"),
                    cash_r=float(cash_r or 0),
                    change=float(change or 0),
                    delivery_status=str(delivery_status or ""),
                    deliverer_id=str(deliverer_id or ""),
                    created_hm=str(created_hm or ""),
                    cur=cur,
                )
            )

    def _build_pending_card(
        self,
        *,
        cart_id: str,
        room_id: str,
        total: float,
        items_json: str,
        pay: str,
        cash_r: float,
        change: float,
        delivery_status: str,
        deliverer_id: str,
        created_hm: str,
        cur: str,
    ) -> QWidget:
        """每条待处理订单一张紧凑卡片。"""
        card = QFrame()
        card.setObjectName("FdPendingCard")

        v = QVBoxLayout(card)
        v.setContentsMargins(FD_CARD_PADDING, FD_SPACE_MD, FD_CARD_PADDING, FD_SPACE_MD)
        v.setSpacing(FD_SPACE_SM)

        try:
            items = json.loads(items_json or "[]")
        except json.JSONDecodeError:
            items = []
        item_count = sum(int(it.get("qty") or 0) for it in items)
        if items:
            preview = "、".join(
                f"{(it.get('emoji') or '')}{it.get('name') or it.get('sku', '?')}×{int(it.get('qty') or 0)}"
                for it in items[:3]
            )
            if len(items) > 3:
                preview += "…"
        else:
            preview = "(无商品明细)"

        # 标题行：房间 + 金额 + 时间
        title = QLabel(
            f"<b>{room_id or '??'}</b>"
            f"　·　<span style='color:{_p('accent')};'>"
            f"{item_count} {i18n.t('shop_pending_items_unit')} / {cur}{total:.0f}</span>"
            f"　·　<span style='color:{_p('text_muted')};font-size:{_p('font.xs')};'>"
            f"{created_hm}　{cart_id}</span>"
        )
        title.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(title)

        # 商品摘要
        v.addWidget(QLabel(preview))

        # 付款信息
        if (pay or "CASH").upper() == "DEPOSIT":
            pay_txt = f"{i18n.t('shop_pay_deposit')} {cur}{total:.0f}"
        else:
            if cash_r > 0 and change > 0.005:
                pay_txt = f"{i18n.t('shop_pay_cash')} {cur}{cash_r:.0f} ｜ {i18n.t('shop_change')} <b>{cur}{change:.0f}</b>"
            elif cash_r > 0:
                pay_txt = f"{i18n.t('shop_pay_cash')} {cur}{cash_r:.0f}"
            else:
                pay_txt = f"{i18n.t('shop_pay_cash')} {cur}{total:.0f}"
        pay_lbl = QLabel(pay_txt)
        pay_lbl.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(pay_lbl)

        if delivery_status == "TAKING" and deliverer_id:
            taking = QLabel(f"🚚 {deliverer_id}")
            taking.setObjectName("FdSuccessLabel")
            v.addWidget(taking)

        # 操作按钮行
        btns = QHBoxLayout()
        btns.setSpacing(FD_SPACE_SM)

        btn_deliver = QPushButton(i18n.t("shop_btn_deliver"))
        fd_apply_action_btn(btn_deliver, primary=True)
        btn_deliver.clicked.connect(
            lambda _=False, cid=cart_id, rid=room_id, t=total, p=pay: self._fulfill_pending(cid, rid, t, p)
        )
        btns.addWidget(btn_deliver, 1)

        btn_open = QPushButton(i18n.t("shop_btn_load_cart"))
        fd_apply_card_action_btn(btn_open)
        btn_open.setToolTip(i18n.t("shop_btn_load_cart_tip"))
        btn_open.clicked.connect(
            lambda _=False, cid=cart_id, rid=room_id, ij=items_json: self._load_cloud_order(cid, rid, ij)
        )
        btns.addWidget(btn_open, 1)

        btn_cancel = QPushButton(i18n.t("shop_btn_cancel"))
        fd_apply_low_freq_btn(btn_cancel)
        btn_cancel.clicked.connect(
            lambda _=False, cid=cart_id, ij=items_json, rid=room_id: self._cancel_pending(cid, ij, rid)
        )
        btns.addWidget(btn_cancel)

        v.addLayout(btns)
        return card

    def _fulfill_pending(self, cart_id: str, room_id: str, total: float, pay: str) -> None:
        """前台直接确认订单已送达：写 SHOP 流水（押金还要补一笔 DEPOSIT_OUT），状态置 FULFILLED。

        [sub-a] 库存闭环说明：
          - Telegram 下单时 telegram_handlers.py 已调用 reserve_shop_stock 扣减库存；
          - 此处仅写营业流水 + 置状态，不重复扣库存（避免双扣）；
          - 整段在 db.transaction() 里保证流水与状态原子一致。
        """
        if not ask_confirm(
            self,
            i18n.t("shop_confirm_deliver"),
            f"{i18n.t('shop_confirm_deliver_msg').format(cart_id=cart_id, room_id=room_id)}\n"
            f"{i18n.t('currency_symbol')}{float(total or 0):.2f}",
        ):
            return
        try:
            from permission_system import PermissionManager
            _u = PermissionManager.current_user()
            op_id = str(_u.get("id") or _u.get("username") or "frontdesk") if _u else "frontdesk"
        except Exception:
            op_id = "frontdesk"
        cur = i18n.t("currency_symbol")
        # [sub-a] 事务保证流水与状态原子一致；库存已在下单时扣减，此处不重复
        try:
            with db.transaction() as conn:
                db.append_ledger_conn(
                    conn, "SHOP", float(total or 0), "CASH", 1, room_id,
                    f"TG 客房点单 {cart_id}",
                )
                if (pay or "CASH").upper() == "DEPOSIT":
                    db.append_ledger_conn(
                        conn, "DEPOSIT_OUT", -float(total or 0), "CASH", 1, room_id,
                        f"客房点单抵押金 {cur}{float(total or 0):.0f}",
                        is_deposit=1,
                    )
                conn.execute(
                    "UPDATE pending_carts SET status='FULFILLED', delivery_status='DONE', "
                    "deliverer_id=COALESCE(NULLIF(deliverer_id,''),?) WHERE cart_id=?",
                    (op_id, cart_id),
                )
        except Exception as e:
            play_warn()
            show_warning(self, i18n.t("dlg_tip"), f"{i18n.t('shop_deliver_failed')}: {e}")
            return
        play_notify()
        self.refresh()

    def _cancel_pending(self, cart_id: str, items_json: str, room_id: str) -> None:
        """作废订单：回退库存 + 状态置 CANCELLED。不写营业流水。

        [sub-a] 库存闭环：调用 shop_inventory.restore_stock_for_refund 集中回补库存，
        取代原直接调 db.adjust_shop_stock(sku, +qty) 的分散写法。
        """
        if not ask_confirm(
            self,
            i18n.t("shop_cancel_order"),
            i18n.t("shop_cancel_confirm_msg").format(cart_id=cart_id, room_id=room_id),
        ):
            return
        try:
            items = json.loads(items_json or "[]")
        except json.JSONDecodeError:
            items = []
        # [sub-a] 集中调用回补函数
        try:
            from shop_inventory import restore_stock_for_refund
            for it in items:
                restore_stock_for_refund(str(it.get("sku") or ""), int(it.get("qty") or 0))
        except Exception:
            # 回退到原 adjust_shop_stock 路径，避免引入新失败点
            for it in items:
                try:
                    db.adjust_shop_stock(str(it.get("sku") or ""), int(it.get("qty") or 0))
                except Exception:
                    pass
        try:
            db.execute(
                "UPDATE pending_carts SET status='CANCELLED', delivery_status='CANCELLED' WHERE cart_id=?",
                (cart_id,),
            )
        except Exception as e:
            play_warn()
            show_warning(self, i18n.t("dlg_tip"), f"{i18n.t('shop_cancel_failed')}: {e}")
            return
        play_notify()
        self.refresh()

    # ── 商品目录 ──────────────────────────────────────────────

    def _reload_catalog(self) -> None:
        """重新加载商品目录。

        [sub-i] 图标包改造：
          • SQL 新增 category 列，用于分类筛选与分类图标展示
          • emoji 解析走 icon_pack.get_icon()：DB emoji 空时回退到 manifest 的 sku_emoji_map
          • 库存 ≤ LOW_STOCK_WARN_THRESHOLD 时商品名前加 ⚠️（红色），便于前台一眼识别
          • 分类筛选下拉同步刷新（cmb_cat_filter）
        """
        self._product_rows = db.execute(
            """
            SELECT sku, COALESCE(emoji,''), name, COALESCE(price,0), COALESCE(stock,0),
                   TRIM(COALESCE(category,''))
            FROM shop_items
            WHERE COALESCE(listed,0)=1
            ORDER BY COALESCE(category,''), COALESCE(sort_order,9999), name
            """
        ).fetchall()
        # [sub-i] 同步分类筛选下拉（保留当前选中）
        self._refresh_cat_filter()
        # 应用当前筛选
        selected_cat = self.cmb_cat_filter.currentData() if hasattr(self, "cmb_cat_filter") else ""
        selected_cat = (selected_cat or "").strip()
        filtered = []
        for row in self._product_rows:
            # row: (sku, emoji, name, price, stock, category)
            if selected_cat and (row[5] or "").strip() != selected_cat:
                continue
            filtered.append(row)

        self.tbl_products.setRowCount(0)
        if not filtered:
            self.tbl_products.setRowCount(1)
            empty = QTableWidgetItem(i18n.t("shop_empty_catalog"))
            empty.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tbl_products.setItem(0, 0, empty)
            self.tbl_products.setSpan(0, 0, 1, 4)
            return
        cur = i18n.t("currency_symbol")
        for sku, emoji, name, price, stock, cat in filtered:
            r = self.tbl_products.rowCount()
            self.tbl_products.insertRow(r)
            # [sub-i] emoji 走 icon_pack 统一解析（DB 空时回退 manifest sku_emoji_map / category）
            if not (emoji or "").strip():
                emoji = icon_pack.get_emoji(str(sku), category=cat or "")
            stock_int = int(stock or 0)
            # [sub-i] 低库存预警：商品名前加 ⚠️
            warn_prefix = ""
            if stock_int <= 0:
                warn_prefix = "⚠️ "
            elif stock_int <= LOW_STOCK_WARN_THRESHOLD:
                warn_prefix = "⚠ "
            disp = f"{warn_prefix}{emoji} {name}".strip() if emoji else f"{warn_prefix}{name}".strip()
            name_it = QTableWidgetItem(disp)
            # [sub-i] PNG 优先：load_shop_icon 已走 items/{sku}.png 路径
            icon = load_shop_icon(str(sku), size=32)
            if icon is not None:
                name_it.setIcon(icon)
            # 低库存时商品名也变红
            if stock_int <= LOW_STOCK_WARN_THRESHOLD:
                name_it.setForeground(Qt.GlobalColor.red)
            self.tbl_products.setItem(r, 0, name_it)
            self.tbl_products.setItem(r, 1, QTableWidgetItem(f"{cur}{float(price or 0):.2f}"))
            stock_it = QTableWidgetItem(str(stock_int))
            if stock_int <= 0:
                stock_it.setForeground(Qt.GlobalColor.red)
            else:
                stock_it.setForeground(Qt.GlobalColor.gray)
            self.tbl_products.setItem(r, 2, stock_it)
            self.tbl_products.setItem(r, 3, QTableWidgetItem(str(sku)))
            self.tbl_products.setRowHeight(r, 44)

    def _refresh_cat_filter(self) -> None:
        """[sub-i] 刷新分类筛选下拉项（保留当前选中，避免重置）。

        分类来源：DB 中已 listed=1 的商品实际出现的 category 集合，
        用 manifest 的 category_label 显示中文标签。
        """
        if not hasattr(self, "cmb_cat_filter"):
            return
        # 收集 DB 中出现的分类（保持排序稳定）
        seen_cats: list[str] = []
        seen_set: set[str] = set()
        for row in self._product_rows:
            cid = (row[5] or "").strip() if len(row) > 5 else ""
            if cid and cid not in seen_set:
                seen_set.add(cid)
                seen_cats.append(cid)
        # 当前选中
        cur_data = self.cmb_cat_filter.currentData()
        # 重建下拉
        self.cmb_cat_filter.blockSignals(True)
        self.cmb_cat_filter.clear()
        all_label = i18n.t("shop_cat_all") if i18n.t("shop_cat_all") != "shop_cat_all" else "全部"
        self.cmb_cat_filter.addItem(all_label, "")
        for cid in seen_cats:
            label = icon_pack.category_label(cid, lang="cn") or cid
            self.cmb_cat_filter.addItem(f"{label} ({cid})", cid)
        # 恢复选中
        if cur_data:
            idx = self.cmb_cat_filter.findData(cur_data)
            if idx >= 0:
                self.cmb_cat_filter.setCurrentIndex(idx)
        self.cmb_cat_filter.blockSignals(False)
        # 更新分类图标
        self._update_cat_icon()

    def _update_cat_icon(self) -> None:
        """[sub-i] 根据当前选中的分类，更新 lbl_cat_icon 的 QPixmap。"""
        if not hasattr(self, "lbl_cat_icon"):
            return
        cid = (self.cmb_cat_filter.currentData() or "").strip()
        if not cid:
            self.lbl_cat_icon.clear()
            self.lbl_cat_icon.setToolTip("")
            return
        # 优先 PNG（categories/{cid}.png），无则用 emoji 文本兜底
        cat_icon_path = icon_pack.get_category_icon(cid)
        if cat_icon_path is not None:
            from PySide6.QtGui import QPixmap
            pix = QPixmap(str(cat_icon_path))
            if not pix.isNull():
                pix = pix.scaled(
                    24, 24,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.lbl_cat_icon.setPixmap(pix)
                self.lbl_cat_icon.setToolTip(f"{icon_pack.category_label(cid, lang='cn')} ({cid})")
                return
        # 兜底：用分类 emoji 文本
        emoji = icon_pack.get_emoji("", category=cid)
        self.lbl_cat_icon.setText(emoji)
        self.lbl_cat_icon.setToolTip(f"{icon_pack.category_label(cid, lang='cn')} ({cid})")

    def _on_cat_filter_changed(self) -> None:
        """[sub-i] 分类筛选变化时重新渲染商品表（不重新查 DB，仅过滤已加载行）。"""
        # _reload_catalog 已实现过滤逻辑，但为避免重复查 DB，这里只重渲染表
        if not self._product_rows:
            return
        selected_cat = (self.cmb_cat_filter.currentData() or "").strip()
        self.tbl_products.setRowCount(0)
        cur = i18n.t("currency_symbol")
        for sku, emoji, name, price, stock, cat in self._product_rows:
            if selected_cat and (cat or "").strip() != selected_cat:
                continue
            r = self.tbl_products.rowCount()
            self.tbl_products.insertRow(r)
            if not (emoji or "").strip():
                emoji = icon_pack.get_emoji(str(sku), category=cat or "")
            stock_int = int(stock or 0)
            warn_prefix = ""
            if stock_int <= 0:
                warn_prefix = "⚠️ "
            elif stock_int <= LOW_STOCK_WARN_THRESHOLD:
                warn_prefix = "⚠ "
            disp = f"{warn_prefix}{emoji} {name}".strip() if emoji else f"{warn_prefix}{name}".strip()
            name_it = QTableWidgetItem(disp)
            icon = load_shop_icon(str(sku), size=32)
            if icon is not None:
                name_it.setIcon(icon)
            if stock_int <= LOW_STOCK_WARN_THRESHOLD:
                name_it.setForeground(Qt.GlobalColor.red)
            self.tbl_products.setItem(r, 0, name_it)
            self.tbl_products.setItem(r, 1, QTableWidgetItem(f"{cur}{float(price or 0):.2f}"))
            stock_it = QTableWidgetItem(str(stock_int))
            if stock_int <= 0:
                stock_it.setForeground(Qt.GlobalColor.red)
            else:
                stock_it.setForeground(Qt.GlobalColor.gray)
            self.tbl_products.setItem(r, 2, stock_it)
            self.tbl_products.setItem(r, 3, QTableWidgetItem(str(sku)))
            self.tbl_products.setRowHeight(r, 44)
        self._update_cat_icon()

    def _selected_product(self) -> Optional[tuple]:
        """返回当前选中行对应的 _product_rows 元组。

        [sub-i] 修复：分类筛选后表格行号与 _product_rows 索引不一致问题。
        改为从隐藏列 3（SKU）取 SKU，再到 _product_rows 里精确匹配。
        """
        row = self.tbl_products.currentRow()
        if row < 0:
            return None
        sku_item = self.tbl_products.item(row, 3)
        if sku_item is None:
            return None
        sku = str(sku_item.text()).strip()
        for prod in self._product_rows:
            if str(prod[0]).strip() == sku:
                return prod
        return None

    def _add_selected_to_cart(self) -> None:
        prod = self._selected_product()
        if not prod:
            play_warn()
            show_warning(self, i18n.t("dlg_tip"), i18n.t("shop_pick_product_first"))
            return
        # [sub-i] 元组现在含 6 列（多了 category），用切片兼容
        sku, _em, name, price, stock = prod[:5]
        qty = self.spn_add_qty.value()
        if int(stock or 0) < qty:
            play_warn()
            show_warning(
                self,
                i18n.t("dlg_tip"),
                i18n.t("shop_stock_insufficient").format(name=name, stock=int(stock or 0)),
            )
            return
        self._cart.merge_line(sku, name, float(price or 0), qty)
        self._render_cart()

    # ── 云端订单 ──────────────────────────────────────────────

    def _load_cloud_order(self, cart_id: str, room_id: str, items_json: str) -> None:
        idx = self.cmb_room.findData(str(room_id))
        if idx >= 0:
            self.cmb_room.setCurrentIndex(idx)
        self._cart.room_id = str(room_id).strip()
        try:
            items = json.loads(items_json or "[]")
        except json.JSONDecodeError:
            items = []
        if not items:
            row = db.execute(
                "SELECT total_amount FROM pending_carts WHERE cart_id=?", (cart_id,)
            ).fetchone()
            if row:
                self._cart.merge_line("CLOUD", i18n.t("shop_cloud_bundle"), float(row[0] or 0), 1)
        else:
            for it in items:
                sku = str(it.get("sku") or "ITEM")
                name = str(it.get("name") or sku)
                price = float(it.get("price") or 0)
                qty = max(1, int(it.get("qty") or 1))
                self._cart.merge_line(sku, name, price, qty)
        if cart_id and cart_id not in self._cart.pending_ids:
            self._cart.pending_ids.append(cart_id)
        self._render_cart()
        show_info(self, i18n.t("dlg_tip"), i18n.t("shop_cloud_loaded"))

    def _on_cloud_signal(self, d: dict) -> None:
        """新订单到达 / 订单状态变化 → 仅刷新待处理列表。

        历史 bug：旧实现会把客人订单自动塞进前台正在用的购物车，
        导致前台手头的销售被覆盖。这里只刷新待处理区域，
        前台点「载入购物车」才会主动载入，避免误操作。
        """
        self.refresh()

    # ── 购物车渲染 ────────────────────────────────────────────

    def _render_cart(self) -> None:
        self.tbl_cart.setRowCount(0)
        cur = i18n.t("currency_symbol")
        for i, ln in enumerate(self._cart.lines):
            self.tbl_cart.insertRow(i)
            self.tbl_cart.setItem(i, 0, QTableWidgetItem(ln.name))
            self.tbl_cart.setItem(i, 1, QTableWidgetItem(f"{cur}{ln.unit_price:.2f}"))
            self.tbl_cart.setItem(i, 2, QTableWidgetItem(str(ln.qty)))
            self.tbl_cart.setItem(i, 3, QTableWidgetItem(f"{cur}{ln.subtotal:.2f}"))
            btn_rm = QPushButton("×")
            fd_apply_low_freq_btn(btn_rm)
            btn_rm.clicked.connect(lambda _=False, idx=i: self._remove_line(idx))
            self.tbl_cart.setCellWidget(i, 4, btn_rm)
        total = self._cart.total()
        self.lbl_total.setText(
            i18n.t("shop_cart_total").format(cur=cur, total=f"{total:.2f}")
        )
        self.btn_checkout.setEnabled(bool(self._cart.lines))

    def _remove_line(self, index: int) -> None:
        if 0 <= index < len(self._cart.lines):
            self._cart.lines.pop(index)
            self._render_cart()

    def _clear_cart(self) -> None:
        if not self._cart.lines:
            return
        if ask_confirm(self, i18n.t("shop_cart_clear"), i18n.t("shop_cart_clear_confirm")):
            self._cart.clear()
            self._render_cart()

    # ── 结账 ──────────────────────────────────────────────────

    def _checkout(self) -> None:
        rid = (self.cmb_room.currentData() or self.cmb_room.currentText() or "").strip()
        if not rid:
            play_warn()
            show_warning(self, i18n.t("dlg_tip"), i18n.t("shop_room_required"))
            return
        if not self._cart.lines:
            play_warn()
            show_warning(self, i18n.t("dlg_tip"), i18n.t("shop_cart_empty"))
            return
        for ln in self._cart.lines:
            row = db.execute(
                "SELECT COALESCE(stock,0) FROM shop_items WHERE sku=?", (ln.sku,)
            ).fetchone()
            if not row:
                continue
            stock = int(row[0] or 0)
            if stock < ln.qty:
                play_warn()
                show_warning(
                    self,
                    i18n.t("dlg_tip"),
                    i18n.t("shop_stock_insufficient").format(name=ln.name, stock=stock),
                )
                return
        # C0-gamma：账实差异锁定的 SKU 不允许销售（先解释 / 调拨）
        try:
            from inventory_audit_engine import is_sku_locked
            from inventory_baseline import make_item_id, CATEGORY_SHOP
            locked_names = [
                ln.name for ln in self._cart.lines
                if is_sku_locked(make_item_id(CATEGORY_SHOP, ln.sku))
            ]
        except Exception:
            locked_names = []
        if locked_names:
            play_warn()
            show_warning(
                self,
                i18n.t("dlg_tip"),
                i18n.t("shop_locked_sku_warning") + "、".join(locked_names),
            )
            return
        cur = i18n.t("currency_symbol")
        total = self._cart.total()
        pay = self.cmb_pay.currentData() or "CASH"
        pay_label = self.cmb_pay.currentText()
        summary = "\n".join(
            f"· {ln.name} ×{ln.qty} = {cur}{ln.subtotal:.2f}" for ln in self._cart.lines
        )
        if not ask_confirm(
            self,
            i18n.t("shop_checkout"),
            i18n.t("shop_checkout_confirm").format(
                room=rid, pay=pay_label, cur=cur, total=f"{total:.2f}", detail=summary
            ),
        ):
            return
        if pay == "DEPOSIT":
            bal = get_room_deposit_balance(rid)
            if bal + 0.009 < total:
                play_warn()
                show_warning(
                    self,
                    i18n.t("dlg_tip"),
                    i18n.t("shop_deposit_insufficient").format(
                        cur=cur, bal=f"{bal:.2f}", need=f"{total:.2f}"
                    ),
                )
                return
        note_tail = i18n.t("ledger_note_shop_cart")
        try:
            from permission_system import PermissionManager
            _u = PermissionManager.current_user()
            _op_id = str(_u.get("id") or _u.get("username") or "frontdesk") if _u else "frontdesk"
        except Exception:
            _op_id = "frontdesk"

        # [sub-a] 库存闭环：扣库存 + 写流水 + 押金冲抵 全部包在 db.transaction() 里
        # 任一 SKU 库存不足（reserve_shop_stock 返回 False）→ 抛 RuntimeError → 事务回滚
        # 避免出现"已扣库存但未写流水"或"已写流水但库存不足"的不一致状态
        try:
            with db.transaction() as conn:
                # 1. 原子扣库存：reserve_shop_stock 失败的行收集起来一次性回滚
                insufficient = []
                for ln in self._cart.lines:
                    if not db.reserve_shop_stock(ln.sku, ln.qty):
                        insufficient.append(f"{ln.name}×{ln.qty}（库存不足）")
                if insufficient:
                    raise RuntimeError("库存扣减失败: " + "、".join(insufficient))

                # 2. 写营业流水
                for ln in self._cart.lines:
                    line_note = f"{note_tail} · {ln.name}×{ln.qty}"
                    db.append_ledger_conn(
                        conn, "SHOP", ln.subtotal, "CASH", 1, rid, line_note,
                    )
                    # C0-beta：销售流水入哈希链（库存审计）
                    if db.execute("SELECT 1 FROM shop_items WHERE sku=?", (ln.sku,)).fetchone():
                        try:
                            from inventory_baseline import record_shop_movement, MOVE_SALE
                            unit_price = float(ln.subtotal or 0) / max(1, int(ln.qty or 1))
                            record_shop_movement(
                                db,
                                sku=ln.sku,
                                move_type=MOVE_SALE,
                                qty_change=-int(ln.qty or 0),
                                unit_cost=unit_price,
                                related_room=rid,
                                operator_id=_op_id,
                                note=f"前台购物车 {ln.name}",
                            )
                        except Exception as _e:
                            logger.warning("[INV-CHAIN] 销售流水入链失败 sku=%s: %s", ln.sku, _e)
                # 3. 押金冲抵
                if pay == "DEPOSIT":
                    db.append_ledger_conn(
                        conn, "DEPOSIT_OUT", -total, "CASH", 1, rid,
                        i18n.t("ledger_note_shop_deposit_offset") + f" {cur}{total:.2f}",
                        is_deposit=1,
                    )
                # 4. 关闭云端订单状态
                for pid in self._cart.pending_ids:
                    try:
                        conn.execute(
                            "UPDATE pending_carts SET status='FULFILLED' WHERE cart_id=?",
                            (pid,),
                        )
                    except Exception:
                        pass
        except Exception as e:
            play_warn()
            show_warning(self, i18n.t("dlg_tip"), f"结账失败（已回滚）: {e}")
            return

        sale_item_count = len(self._cart.lines)
        self._cart.clear()
        self._render_cart()
        self.refresh()
        bus.show_success_overlay.emit(i18n.t("msg_success"))
        try:
            from telemetry import report_event
            report_event("SHOP_SALE", {"room_id": rid, "amount": total, "items": sale_item_count})
        except Exception:
            pass
        self._on_room_changed()
        # 结账成功后自动触发发卡
        self._auto_card_issuance(rid)

    def _auto_card_issuance(self, room_id: str) -> None:
        """结账成功后自动弹出制卡对话框（如该房间为在住状态）。"""
        try:
            row = db.execute(
                "SELECT status FROM rooms WHERE room_id=?", (room_id,)
            ).fetchone()
            if row and str(row[0]).upper() == "INHOUSE":
                bus.request_card_issuance.emit(room_id)
        except Exception:
            pass
