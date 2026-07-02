from __future__ import annotations

import logging
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QComboBox,
    QLineEdit,
    QSpinBox,
    QFormLayout,
    QDialog,
    QAbstractItemView,
    QSplitter,
)
from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import show_info, show_warning, style_dialog, build_dialog_header
from design_tokens import _p
from frontdesk_ui import (
    fd_section_bar,
    fd_apply_card_action_btn,
    fd_apply_low_freq_btn,
    fd_apply_action_btn,
    fd_apply_compact_input,
    FD_MARGIN,
    FD_SPACE_SM,
    FD_SPACE_MD,
    FD_SPACE_LG,
)
from tabs._shared import current_operator_id
from ui_surface import fd_apply_table_palette, fd_refresh_surfaces, fd_sync_table_height, fd_apply_workspace_splitter, fd_apply_page_tab_root

logger = logging.getLogger(__name__)


# 库存管理标签页
# ═══════════════════════════════════════════════════════════
class InventoryTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("InventoryTab")
        self._inv_filter = "ALL"
        l = QVBoxLayout(self)
        l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)
        l.setSpacing(FD_SPACE_MD)

        intro = QLabel(i18n.t("inventory_tab_intro"))
        intro.setWordWrap(True)
        intro.setObjectName("FdMutedLabel")
        l.addWidget(intro)

        # ── 搜索筛选行（顶部）───────────────────────────────
        fl = QHBoxLayout()
        fl.setSpacing(FD_SPACE_SM)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText(i18n.t("inventory_search_ph") if i18n.t("inventory_search_ph") != "inventory_search_ph" else "搜索SKU / 商品名称…")
        self.txt_search.setObjectName("FdCompactInput")
        self.txt_search.textChanged.connect(self._filter_inventory)
        fl.addWidget(self.txt_search, 1)
        self._filter_chips: dict[str, QPushButton] = {}
        for fk, label in (("ALL", "全部"), ("LOW", "低库存"), ("LINEN", "布草类")):
            chip = QPushButton(label)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setChecked(fk == "ALL")
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _=False, k=fk: self._on_inv_filter(k))
            self._filter_chips[fk] = chip
            fl.addWidget(chip)
        l.addLayout(fl)

        # ── 操作按钮浮动工具栏（主操作 + [更多]下拉菜单）──
        action_bar = QHBoxLayout()
        action_bar.setSpacing(FD_SPACE_SM)

        # 主操作：采购（Primary，始终可见）
        btn_purchase = QPushButton(i18n.t("inventory_btn_purchase"))
        fd_apply_action_btn(btn_purchase, primary=True)
        btn_purchase.setCursor(Qt.PointingHandCursor)
        btn_purchase.clicked.connect(self._do_purchase)
        action_bar.addWidget(btn_purchase)

        # 次操作：库存管理（Card，始终可见）
        btn_manage = QPushButton(i18n.t("shop_btn_manage"))
        fd_apply_card_action_btn(btn_manage)
        btn_manage.setCursor(Qt.PointingHandCursor)
        btn_manage.clicked.connect(self._manage_products)
        action_bar.addWidget(btn_manage)

        # 低频操作 → [更多] 下拉菜单
        btn_more = QPushButton(i18n.t("btn_more", default="更多 ▾"))
        fd_apply_card_action_btn(btn_more)
        btn_more.setCursor(Qt.PointingHandCursor)
        more_menu = QMenu(btn_more)
        for label, handler in (
            (i18n.t("btn_initial_stocktake"), self._open_initial_stocktake),
            (i18n.t("btn_discrepancy_audit"), self._open_inventory_diff),
            (i18n.t("inventory_linen_issue"), lambda: self._do_linen_move("LINEN_ISSUE")),
            (i18n.t("inventory_linen_return"), lambda: self._do_linen_move("LINEN_RETURN")),
        ):
            action = more_menu.addAction(label)
            action.triggered.connect(handler)
        btn_more.setMenu(more_menu)
        action_bar.addWidget(btn_more)

        action_bar.addStretch()
        btn_rf = QPushButton(i18n.t("btn_reload"))
        fd_apply_low_freq_btn(btn_rf)
        btn_rf.clicked.connect(self.refresh)
        action_bar.addWidget(btn_rf)

        l.addLayout(action_bar)

        # ── 低库存预警横幅 ──────────────────────────────────
        self.alert_banner = QFrame()
        self.alert_banner.setObjectName("FdAlertBanner")
        self.alert_banner.setVisible(False)
        banner_lay = QHBoxLayout(self.alert_banner)
        banner_lay.setContentsMargins(FD_SPACE_MD, FD_SPACE_SM, FD_SPACE_MD, FD_SPACE_SM)
        self.lbl_warn = QLabel()
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("background: transparent;")
        banner_lay.addWidget(self.lbl_warn)
        l.addWidget(self.alert_banner)

        # ── 上下分栏：库存总表 + 变动记录 ───────────────────
        v_split = QSplitter(Qt.Orientation.Vertical)
        v_split.setObjectName("WorkspaceSplit")
        v_split.setChildrenCollapsible(False)
        v_split.setHandleWidth(6)

        # 上：库存总表
        # [sub-j] SolidCard 卡片包裹：圆角 10px + 1px panel_border + surface 实底
        # 替换原 ContentBox（gold_thread 左线 + radius 0），整体更精致符合"精品"定位
        top_panel = QFrame()
        top_panel.setObjectName("SolidCard")
        top_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        top_lay = QVBoxLayout(top_panel)
        top_lay.setContentsMargins(0, FD_SPACE_MD, FD_SPACE_LG, FD_SPACE_MD)
        top_lay.setSpacing(FD_SPACE_SM)

        top_lay.addWidget(fd_section_bar(i18n.t("inventory_title_short")))

        self.tbl = QTableWidget(0, 9)
        self.tbl.setObjectName("InventoryTable")
        self.tbl.setHorizontalHeaderLabels([
            i18n.t("inventory_col_sku"),
            i18n.t("inventory_col_name"),
            i18n.t("shop_col_cost"),
            i18n.t("shop_col_spec"),
            i18n.t("shop_col_sale"),
            i18n.t("inventory_col_stock"),
            i18n.t("inventory_col_in"),
            i18n.t("inventory_col_out"),
            i18n.t("inventory_col_status"),
        ])
        inv_hdr = self.tbl.horizontalHeader()
        inv_hdr.setMinimumSectionSize(70)
        for c in (0, 3, 5, 6, 7, 8):
            inv_hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        for c in (1, 2, 4):
            inv_hdr.setSectionResizeMode(c, QHeaderView.Stretch)
        self.tbl.setAlternatingRowColors(False)
        self.tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        top_lay.addWidget(self.tbl)
        v_split.addWidget(top_panel)

        # 下：最近变动记录
        # [sub-j] SolidCard 卡片包裹：圆角 10px + 1px panel_border + surface 实底
        bottom_panel = QFrame()
        bottom_panel.setObjectName("SolidCard")
        bottom_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bottom_lay = QVBoxLayout(bottom_panel)
        bottom_lay.setContentsMargins(0, FD_SPACE_MD, FD_SPACE_LG, FD_SPACE_MD)
        bottom_lay.setSpacing(FD_SPACE_SM)
        bottom_lay.addWidget(fd_section_bar(i18n.t("inventory_recent_moves")))
        self.tbl_log = QTableWidget(0, 5)
        self.tbl_log.setObjectName("InventoryLogTable")
        self.tbl_log.setHorizontalHeaderLabels([
            i18n.t("inventory_log_col_time"),
            i18n.t("inventory_log_col_room"),
            i18n.t("inventory_log_col_action"),
            i18n.t("inventory_log_col_sku_key"),
            i18n.t("inventory_log_col_qty"),
        ])
        log_hdr = self.tbl_log.horizontalHeader()
        log_hdr.setMinimumSectionSize(70)
        for c in (0, 1, 3, 4):
            log_hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        log_hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_log.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_log.setAlternatingRowColors(False)
        bottom_lay.addWidget(self.tbl_log)
        v_split.addWidget(bottom_panel)
        v_split.setStretchFactor(0, 3)
        v_split.setStretchFactor(1, 2)

        l.addWidget(v_split, 1)
        fd_apply_workspace_splitter(v_split)
        fd_apply_table_palette(self.tbl)
        fd_apply_table_palette(self.tbl_log)
        fd_apply_page_tab_root(self)
        self.refresh()
        from ui_surface import fd_connect_theme_refresh
        fd_connect_theme_refresh(self)

    def refresh(self):
        self.tbl.setRowCount(0)
        low_stock = []
        cur = i18n.t("currency_symbol")
        try:
            items = db.execute(
                """
                SELECT sku, COALESCE(emoji,''), name,
                       COALESCE(cost_price,0), COALESCE(price,0),
                       COALESCE(pack_label,'箱'), COALESCE(units_per_pack,1), COALESCE(stock,0)
                FROM shop_items ORDER BY name
                """
            ).fetchall()
        except Exception:
            items = []
        # 获取入库/出库汇总
        try:
            audit_map = {}
            rows = db.execute(
                "SELECT item_sku, "
                "SUM(CASE WHEN qty_change>0 THEN qty_change ELSE 0 END) AS total_in, "
                "SUM(CASE WHEN qty_change<0 THEN -qty_change ELSE 0 END) AS total_out "
                "FROM inventory_audit GROUP BY item_sku"
            ).fetchall()
            for sku, tin, tout in rows:
                audit_map[sku] = (int(tin or 0), int(tout or 0))
        except Exception:
            audit_map = {}

        for sku, emoji, name, cost, price, pack_label, upp, stock in items:
            tin, tout = audit_map.get(sku, (0, 0))
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            pack_label = (pack_label or i18n.t("shop_pack_default")).strip()
            upp_i = max(1, int(upp or 1))
            self.tbl.setItem(r, 0, QTableWidgetItem(sku))
            self.tbl.setItem(r, 1, QTableWidgetItem(f"{emoji} {name}".strip()))
            cost_item = QTableWidgetItem(f"{cur}{float(cost or 0):.2f}")
            cost_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.tbl.setItem(r, 2, cost_item)
            self.tbl.setItem(
                r, 3,
                QTableWidgetItem(i18n.t("shop_spec_short").format(pack=pack_label, upp=upp_i)),
            )
            sale_item = QTableWidgetItem(f"{cur}{float(price or 0):.2f}")
            sale_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.tbl.setItem(r, 4, sale_item)
            stock_item = QTableWidgetItem(str(stock))
            stock_item.setTextAlignment(Qt.AlignCenter)
            if stock <= 5:
                stock_item.setForeground(QColor(_p("amount_negative")))
                low_stock.append(i18n.t("inventory_low_piece_fmt").format(name, stock))
            self.tbl.setItem(r, 5, stock_item)
            self.tbl.setItem(r, 6, QTableWidgetItem(str(tin)))
            self.tbl.setItem(r, 7, QTableWidgetItem(str(tout)))
            status = i18n.t("inventory_status_low") if stock <= 5 else (i18n.t("inventory_status_ok") if stock > 20 else i18n.t("inventory_status_mid"))
            status_item = QTableWidgetItem(status)
            if stock <= 5:
                status_item.setForeground(QColor(_p("amount_negative")))
            elif stock > 20:
                status_item.setForeground(QColor(_p("amount_positive")))
            else:
                status_item.setForeground(QColor(_p("accent")))
            self.tbl.setItem(r, 8, status_item)

        if low_stock:
            self.lbl_warn.setText(
                i18n.t("inventory_low_warn_fmt").format(
                    "、".join(low_stock[:3]) +
                    (f" 等 {len(low_stock)} 项" if len(low_stock) > 3 else "")
                )
            )
            self.alert_banner.setVisible(True)
        else:
            self.alert_banner.setVisible(False)

        # 刷新变动记录
        self.tbl_log.setRowCount(0)
        try:
            logs = db.execute(
                "SELECT created_at, room_id, action_type, item_sku, qty_change "
                "FROM inventory_audit ORDER BY id DESC LIMIT 30"
            ).fetchall()
            for i, (ts, rid, act, sku, qty) in enumerate(logs):
                self.tbl_log.insertRow(i)
                self.tbl_log.setItem(i, 0, QTableWidgetItem(str(ts or "")[:16]))
                self.tbl_log.setItem(i, 1, QTableWidgetItem(str(rid or "")))
                if act == "SHOP_PURCHASE":
                    act_disp = i18n.t("ledger_tx_shop_purchase")
                elif act == "PURCHASE_IN":
                    act_disp = i18n.t("inventory_action_purchase_legacy")
                else:
                    act_disp = str(act or "")
                self.tbl_log.setItem(i, 2, QTableWidgetItem(act_disp))
                self.tbl_log.setItem(i, 3, QTableWidgetItem(str(sku or "")))
                qty_item = QTableWidgetItem(f"+{qty}" if qty > 0 else str(qty))
                qty_item.setForeground(QColor(_p("amount_positive") if qty > 0 else _p("amount_negative")))
                self.tbl_log.setItem(i, 4, qty_item)
        except Exception:
            pass
        fd_sync_table_height(self.tbl, min_rows=4, max_rows=18)
        fd_sync_table_height(self.tbl_log, min_rows=2, max_rows=10)

    def _selected_sku(self) -> str | None:
        row = self.tbl.currentRow()
        if row < 0:
            return None
        it = self.tbl.item(row, 0)
        return it.text().strip() if it else None

    def _do_purchase(self):
        from shop_inventory import open_shop_purchase_dialog

        if open_shop_purchase_dialog(self, sku=self._selected_sku()):
            self.refresh()

    def _open_initial_stocktake(self):
        """C0-beta：弹期初盘点向导。"""
        try:
            from initial_stocktake_wizard import open_initial_stocktake_wizard
            if open_initial_stocktake_wizard(self.window()):
                self.refresh()
        except Exception as exc:
            show_warning(self, "期初盘点", f"无法打开向导：{exc}")

    def _open_inventory_diff(self):
        """C0-gamma：弹账实差异审计页。"""
        try:
            from inventory_diff_page import open_inventory_diff_page
            open_inventory_diff_page(self.window())
            self.refresh()
        except Exception as exc:
            show_warning(self, "账实差异", f"无法打开差异审计：{exc}")

    def _manage_products(self):
        parent = self.window()
        try:
            from tabs.system_console_tab import SystemConsoleTab as SettingsDialog

            sd = SettingsDialog(parent)
            sd._manage_shop()
            self.refresh()
        except Exception as e:
            show_warning(self, i18n.t("dlg_tip"), str(e))

    def _do_linen_move(self, action: str):
        """布草发出/回收：库存审计独立操作，与商店物品库存联动。"""
        from PySide6.QtWidgets import QDialog, QFormLayout, QSpinBox

        d = QDialog(self)
        d.setWindowTitle(i18n.t("inventory_linen_dialog_title"))
        style_dialog(d, size="compact")
        lv = QVBoxLayout(d)
        lv.setContentsMargins(16, 16, 16, 16)
        lv.setSpacing(FD_SPACE_MD)
        lv.addWidget(
            build_dialog_header(
                i18n.t("inventory_linen_dialog_title"),
                i18n.t("inventory_linen_issue") if action == "LINEN_ISSUE" else i18n.t("inventory_linen_return"),
            )
        )
        f = QFormLayout()
        cmb_sku = QComboBox()
        try:
            items = db.execute("SELECT sku, name FROM shop_items ORDER BY name").fetchall()
            for sku, name in items:
                cmb_sku.addItem(f"{name} ({sku})", sku)
        except Exception:
            pass
        spn_qty = QSpinBox()
        spn_qty.setRange(1, 9999)
        spn_qty.setValue(1)
        txt_note = QLineEdit()
        txt_note.setPlaceholderText(i18n.t("inventory_linen_note_ph"))
        f.addRow(i18n.t("inventory_field_product"), cmb_sku)
        f.addRow(i18n.t("inventory_field_qty"), spn_qty)
        f.addRow(i18n.t("inventory_field_note"), txt_note)
        btn_ok = QPushButton(i18n.t("inventory_confirm_in"))
        fd_apply_action_btn(btn_ok, primary=True)
        btn_ok.clicked.connect(d.accept)
        f.addRow(btn_ok)
        lv.addLayout(f)
        if not d.exec():
            return
        sku = cmb_sku.currentData()
        qty = spn_qty.value()
        note = txt_note.text().strip() or action
        if not sku:
            return
        op_id = current_operator_id()
        try:
            if action == "LINEN_ISSUE":
                db.log_inventory_change("__LINEN__", action, sku, -qty, op_id, note)
                db.adjust_shop_stock(sku, -qty)
            else:
                db.log_inventory_change("__LINEN__", action, sku, qty, op_id, note)
                db.adjust_shop_stock(sku, qty)
        except Exception as e:
            show_warning(self, i18n.t("dlg_error"), str(e))
            return
        # C0-beta：布草发出/回收入哈希链
        try:
            from inventory_baseline import (
                record_shop_movement, MOVE_ROOM_CONSUME, MOVE_ADJUST,
            )
            if action == "LINEN_ISSUE":
                record_shop_movement(
                    db, sku=sku, move_type=MOVE_ROOM_CONSUME,
                    qty_change=-int(qty), related_room="__LINEN__",
                    operator_id=op_id, note=f"布草发出 {note}",
                )
            else:
                record_shop_movement(
                    db, sku=sku, move_type=MOVE_ADJUST,
                    qty_change=int(qty), related_room="__LINEN__",
                    operator_id=op_id, note=f"布草回收 {note}",
                )
        except Exception as _e:
            logger.warning("布草流水入链失败 sku=%s: %s", sku, _e)
        self.refresh()
        bus.show_success_overlay.emit(
            i18n.t("inventory_linen_success_overlay").format(sku, f"-{qty}" if action == "LINEN_ISSUE" else f"+{qty}")
        )

    def _filter_inventory(self):
        self.refresh()

    def _on_inv_filter(self, fk: str):
        self._inv_filter = fk
        for k, chip in self._filter_chips.items():
            chip.setChecked(k == fk)
        self.refresh()
