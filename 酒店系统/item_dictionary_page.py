"""物品字典独立页 — 期初盘点 / 账实差异 SKU 来源"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import db
from inventory_baseline import CATEGORY_CONSUMABLE, CATEGORY_SHOP, ensure_shop_item_registered
from ui_helpers import ask_confirm, show_info, show_warning
from ui_helpers import style_dialog, build_dialog_header
from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QPushButton


class ItemDictionaryPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("ItemDictionaryPage")
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(12, 12, 12, 12)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("物品字典", styleSheet="font-size:18px;font-weight:bold;"))
        hdr.addStretch()
        btn_add = QPushButton("新增字典项")
        btn_add.setObjectName("SolidPrimaryBtn")
        btn_add.clicked.connect(self._add_row)
        btn_edit = QPushButton("编辑")
        btn_edit.setObjectName("FdGhostBtn")
        btn_edit.clicked.connect(self._edit_row)
        btn_toggle = QPushButton("切换纳入监控")
        btn_toggle.setObjectName("FdGhostBtn")
        btn_toggle.clicked.connect(self._toggle_monitor)
        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("FdGhostBtn")
        btn_refresh.clicked.connect(self.refresh)
        hdr.addWidget(btn_add)
        hdr.addWidget(btn_edit)
        hdr.addWidget(btn_toggle)
        hdr.addWidget(btn_refresh)
        lay.addLayout(hdr)

        hint = QLabel("这里的字典项会用于期初盘点、账实差异，以及超市/客房消耗监控。")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        content_box = QFrame()
        content_box.setObjectName("ContentBox")
        cb_lay = QVBoxLayout(content_box)
        cb_lay.setContentsMargins(10, 10, 10, 10)

        self.tbl = QTableWidget(0, 9)
        self.tbl.setObjectName("ItemDictionaryTable")
        self.tbl.setHorizontalHeaderLabels(
            ["字典ID", "分类", "显示名称", "来源SKU", "单位", "成本价", "销售价", "补货阈值", "纳入监控"]
        )
        self.tbl.setAlternatingRowColors(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        cb_lay.addWidget(self.tbl, 1)
        lay.addWidget(content_box, 1)

        from ui_surface import fd_apply_content_box, fd_apply_table_palette, fd_connect_theme_refresh
        fd_apply_content_box(content_box)
        fd_apply_table_palette(self.tbl)
        fd_connect_theme_refresh(self)
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        rows = db.execute(
            "SELECT item_id, category, name, source_sku, unit, cost_price, sale_price, reorder_threshold, in_monitoring "
            "FROM inventory_items ORDER BY category, name"
        ).fetchall()
        self.tbl.setRowCount(0)
        for row in rows:
            idx = self.tbl.rowCount()
            self.tbl.insertRow(idx)
            self.tbl.setItem(idx, 0, QTableWidgetItem(str(row[0] or "")))
            self.tbl.setItem(idx, 1, QTableWidgetItem("超市" if row[1] == CATEGORY_SHOP else "客房消耗品"))
            self.tbl.setItem(idx, 2, QTableWidgetItem(str(row[2] or "")))
            self.tbl.setItem(idx, 3, QTableWidgetItem(str(row[3] or "")))
            self.tbl.setItem(idx, 4, QTableWidgetItem(str(row[4] or "件")))
            self.tbl.setItem(idx, 5, QTableWidgetItem(f"{float(row[5] or 0):.2f}"))
            self.tbl.setItem(idx, 6, QTableWidgetItem(f"{float(row[6] or 0):.2f}"))
            self.tbl.setItem(idx, 7, QTableWidgetItem(str(int(row[7] or 0))))
            monitored = QTableWidgetItem("是" if int(row[8] or 0) else "否")
            monitored.setForeground(Qt.GlobalColor.darkGreen if int(row[8] or 0) else Qt.GlobalColor.gray)
            self.tbl.setItem(idx, 8, monitored)

    def _selected_item_id(self) -> str:
        row = self.tbl.currentRow()
        if row < 0:
            return ""
        it = self.tbl.item(row, 0)
        return it.text().strip() if it else ""

    def _row_values(self, preset: dict | None = None) -> dict:
        data = preset or {}
        dlg = QDialog(self)
        dlg.setWindowTitle("物品字典")
        style_dialog(dlg, size="small")
        lay = QVBoxLayout(dlg)
        lay.addWidget(build_dialog_header("物品字典", "新增或修改期初盘点、账实差异使用的 SKU。"))
        form = QFormLayout()
        cmb_cat = QComboBox()
        cmb_cat.addItem("超市商品", CATEGORY_SHOP)
        cmb_cat.addItem("客房消耗品", CATEGORY_CONSUMABLE)
        if data.get("category"):
            for i in range(cmb_cat.count()):
                if cmb_cat.itemData(i) == data["category"]:
                    cmb_cat.setCurrentIndex(i)
                    break
        txt_id = QLineEdit(data.get("item_id", ""))
        txt_name = QLineEdit(data.get("name", ""))
        txt_sku = QLineEdit(data.get("source_sku", ""))
        txt_unit = QLineEdit(data.get("unit", "件"))
        txt_cost = QLineEdit(str(data.get("cost_price", "0")))
        txt_sale = QLineEdit(str(data.get("sale_price", "0")))
        txt_threshold = QLineEdit(str(data.get("reorder_threshold", "0")))
        chk_monitor = QCheckBox("纳入账实差异监控")
        chk_monitor.setChecked(bool(data.get("in_monitoring", True)))
        form.addRow("分类", cmb_cat)
        form.addRow("字典ID", txt_id)
        form.addRow("名称", txt_name)
        form.addRow("来源SKU", txt_sku)
        form.addRow("单位", txt_unit)
        form.addRow("成本价", txt_cost)
        form.addRow("销售价", txt_sale)
        form.addRow("补货阈值", txt_threshold)
        form.addRow("", chk_monitor)
        lay.addLayout(form)
        btns = QHBoxLayout()
        ok = QPushButton("保存")
        ok.setObjectName("SolidPrimaryBtn")
        cancel = QPushButton("取消")
        cancel.setObjectName("FdGhostBtn")
        ok.clicked.connect(dlg.accept)
        cancel.clicked.connect(dlg.reject)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)
        if dlg.exec() != QDialog.Accepted:
            return {}
        category = cmb_cat.currentData()
        item_id = txt_id.text().strip()
        if not item_id:
            prefix = "shop:" if category == CATEGORY_SHOP else "cons:"
            sku = txt_sku.text().strip()
            item_id = prefix + (sku or txt_name.text().strip())
        if not item_id or not txt_name.text().strip():
            show_warning(self, "保存失败", "名称不能为空。")
            return {}
        return {
            "item_id": item_id,
            "category": category,
            "name": txt_name.text().strip(),
            "source_sku": txt_sku.text().strip(),
            "unit": txt_unit.text().strip() or "件",
            "cost_price": float(txt_cost.text().strip() or 0),
            "sale_price": float(txt_sale.text().strip() or 0),
            "reorder_threshold": int(float(txt_threshold.text().strip() or 0)),
            "in_monitoring": 1 if chk_monitor.isChecked() else 0,
        }

    def _save_values(self, values: dict) -> None:
        db.execute(
            "INSERT INTO inventory_items "
            "(item_id,category,source_sku,name,unit,cost_price,sale_price,reorder_threshold,in_monitoring,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,datetime('now')) "
            "ON CONFLICT(item_id) DO UPDATE SET "
            "category=excluded.category, source_sku=excluded.source_sku, name=excluded.name, unit=excluded.unit, "
            "cost_price=excluded.cost_price, sale_price=excluded.sale_price, "
            "reorder_threshold=excluded.reorder_threshold, in_monitoring=excluded.in_monitoring, updated_at=datetime('now')",
            (
                values["item_id"],
                values["category"],
                values["source_sku"],
                values["name"],
                values["unit"],
                values["cost_price"],
                values["sale_price"],
                values["reorder_threshold"],
                values["in_monitoring"],
            ),
        )
        if values["category"] == CATEGORY_SHOP and values["source_sku"]:
            ensure_shop_item_registered(db, values["source_sku"])
            db.execute(
                "INSERT INTO shop_items (sku,name,price,stock,cost_price) VALUES (?,?,?,COALESCE((SELECT stock FROM shop_items WHERE sku=?),0),?) "
                "ON CONFLICT(sku) DO UPDATE SET name=excluded.name, price=excluded.price, cost_price=excluded.cost_price",
                (
                    values["source_sku"],
                    values["name"],
                    values["sale_price"],
                    values["source_sku"],
                    values["cost_price"],
                ),
            )

    def _add_row(self):
        values = self._row_values()
        if not values:
            return
        self._save_values(values)
        show_info(self, "已保存", f"{values['name']} 已加入物品字典。")
        self.refresh()

    def _edit_row(self):
        item_id = self._selected_item_id()
        if not item_id:
            show_warning(self, "请选择", "请先选中一行字典项。")
            return
        row = db.execute(
            "SELECT item_id, category, name, source_sku, unit, cost_price, sale_price, reorder_threshold, in_monitoring "
            "FROM inventory_items WHERE item_id=?",
            (item_id,),
        ).fetchone()
        if not row:
            return
        values = self._row_values(
            preset={
                "item_id": row[0],
                "category": row[1],
                "name": row[2],
                "source_sku": row[3],
                "unit": row[4],
                "cost_price": row[5],
                "sale_price": row[6],
                "reorder_threshold": row[7],
                "in_monitoring": int(row[8] or 0),
            }
        )
        if not values:
            return
        self._save_values(values)
        show_info(self, "已保存", f"{values['name']} 已更新。")
        self.refresh()

    def _toggle_monitor(self):
        item_id = self._selected_item_id()
        if not item_id:
            show_warning(self, "请选择", "请先选中一行字典项。")
            return
        row = db.execute("SELECT in_monitoring FROM inventory_items WHERE item_id=?", (item_id,)).fetchone()
        newv = 0 if int(row[0] or 0) else 1
        db.execute("UPDATE inventory_items SET in_monitoring=? WHERE item_id=?", (newv, item_id))
        show_info(self, "已更新", "纳入监控状态已切换。")
        self.refresh()
