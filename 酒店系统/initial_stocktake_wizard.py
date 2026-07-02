"""
initial_stocktake_wizard.py — 测试版期初盘点向导

设计意图（来自 商业重构执行清单.md / C0测试版）：
- 厂家激活后第一步不是统计校准，而是强制期初盘点；以前没有证据的错账不追。
- 4 步向导：
    1. 超市商品盘点（每个商品编号：当前实物 / 进货单价 / 销售价 / 补货阈值；可跳过）
    2. 客房消耗品盘点（毛巾、洗发水、矿泉水等：实物 / 单位 / 成本价 / 阈值；老板新建）
    3. 房型标准配备（每个房型每次入住/退房应补哪些消耗品 + 数量）
    4. 老板确认（二次密码 + 生成快照 + 落地哈希 + 尝试云端备份）
- 未完成 → 主界面阻塞；完成后写初始盘点完成标记，配合厂家门控放行。
- 跳过的商品不进入差异监控，但记录保留，下次报表底部显示"未纳入监控清单"。

返回：
- 确定：盘点完成，可进入主界面
- 取消：老板临时关闭，下次启动会再弹（C0测试版完成后由主程序强制阻塞）
"""
from __future__ import annotations

import datetime as _dt
import uuid
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from database import db
from design_tokens import _p
from ui_helpers import show_info
from ui_surface import fd_apply_table_palette
from inventory_baseline import (
    CATEGORY_CONSUMABLE, CATEGORY_SHOP,
    build_baseline_snapshot, make_item_id,
    record_opening_quantities, set_room_type_standard,
    sync_shop_items_to_inventory, upload_snapshot_to_cloud, upsert_item,
)
import logging
logger = logging.getLogger(__name__)

from consumable_standards import (
    default_consumable_seed_rows,
    standard_qty_for,
)


# ─────────────────────────────────────────────────────────────────────────────
#  通用：可全屏的向导外壳
# ─────────────────────────────────────────────────────────────────────────────
class _StepHeader(QFrame):
    def __init__(self, step_no: int, total: int, title: str, subtitle: str = ""):
        super().__init__()
        self.setObjectName("StocktakeStepHeader")
        border_col = _p("border")
        self.setStyleSheet(
            f"#StocktakeStepHeader{{background:transparent;border-bottom:1px solid qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {border_col},stop:1 transparent);}}"
            "QLabel{color:inherit;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(4)
        bar = QHBoxLayout()
        tag = QLabel(f"第 {step_no} / {total} 步")
        tag.setStyleSheet(f"color:{_p('gold_thread')};font-weight:700;font-size:14px;")
        bar.addWidget(tag)
        bar.addStretch()
        when = QLabel(_dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
        when.setStyleSheet(f"color:{_p('text_dim')};font-size:12px;")
        bar.addWidget(when)
        lay.addLayout(bar)
        ttl = QLabel(title)
        ttl.setStyleSheet("font-size:22px;font-weight:800;")
        lay.addWidget(ttl)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"color:{_p('text_muted')};font-size:13px;")
            lay.addWidget(sub)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{_p('border')};max-height:1px;")
        lay.addWidget(sep)


# ─────────────────────────────────────────────────────────────────────────────
#  第 1 步：超市商品盘点
# ─────────────────────────────────────────────────────────────────────────────
class _ShopStockTable(QTableWidget):
    """每行：上架 / 商品编号 / 名称 / 进价 / 售价 / 实物 / 阈值 / 跳过监控"""

    COL_LISTED, COL_SKU, COL_NAME, COL_COST, COL_SALE, COL_QTY, COL_THRESH, COL_SKIP = range(8)

    def __init__(self, parent=None):
        super().__init__(0, 8, parent)
        self.setObjectName("ShopStockTable")
        self.setHorizontalHeaderLabels([
            "本店上架", "商品编号", "商品名称", "进货单价", "销售价",
            "当前实物", "补货阈值", "跳过监控",
        ])
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
        # 有数值输入框的列必须用交互模式，否则列宽设置会被自动调整
        self.horizontalHeader().setSectionResizeMode(self.COL_COST, QHeaderView.Interactive)
        self.horizontalHeader().setSectionResizeMode(self.COL_SALE, QHeaderView.Interactive)
        self.horizontalHeader().setSectionResizeMode(self.COL_QTY, QHeaderView.Interactive)
        self.horizontalHeader().setSectionResizeMode(self.COL_THRESH, QHeaderView.Interactive)
        self.setColumnWidth(self.COL_COST, 110)
        self.setColumnWidth(self.COL_SALE, 100)
        self.setColumnWidth(self.COL_QTY, 110)
        self.setColumnWidth(self.COL_THRESH, 110)
        self.setAlternatingRowColors(False)
        self.setEditTriggers(QTableWidget.AllEditTriggers)
        self._load()

    def _load(self) -> None:
        try:
            from shop_catalog import seed_shop_from_manifest
            seed_shop_from_manifest(db)
        except Exception as exc:
            logger.warning("[InitialStocktakeWizard] shop manifest 同步失败: %s", exc)

        rows = db.execute(
            """SELECT sku, name,
                      COALESCE(cost_price, 0), COALESCE(price, 0),
                      COALESCE(stock, 0), COALESCE(listed, 0)
               FROM shop_items
               ORDER BY COALESCE(category,''), COALESCE(sort_order,9999), name"""
        ).fetchall()
        self.setRowCount(len(rows))
        for r, (sku, name, cost, sale, stock, listed) in enumerate(rows):
            cb = QCheckBox("上架")
            cb.setChecked(bool(int(listed or 0)))
            self.setCellWidget(r, self.COL_LISTED, cb)
            self._set_text(r, self.COL_SKU, sku, editable=False)
            self._set_text(r, self.COL_NAME, name or sku, editable=False)
            self.setCellWidget(r, self.COL_COST, self._money_spin(float(cost or 0)))
            self.setCellWidget(r, self.COL_SALE, self._money_spin(float(sale or 0)))
            self.setCellWidget(r, self.COL_QTY, self._int_spin(int(stock or 0), maxv=999999))
            self.setCellWidget(r, self.COL_THRESH, self._int_spin(0, maxv=999999))
            skip_cb = QCheckBox("跳过")
            skip_cb.setToolTip("跳过的商品不进入账实差异监控，但会保留记录")
            skip_cb.setChecked(not bool(int(listed or 0)))
            self.setCellWidget(r, self.COL_SKIP, skip_cb)

    def _set_text(self, r: int, c: int, text: str, editable: bool = True) -> None:
        item = QTableWidgetItem(str(text or ""))
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.setItem(r, c, item)

    @staticmethod
    def _money_spin(value: float) -> QDoubleSpinBox:
        spn = QDoubleSpinBox()
        spn.setRange(0, 999999)
        spn.setDecimals(2)
        spn.setValue(value)
        spn.setMinimumHeight(34)
        return spn

    @staticmethod
    def _int_spin(value: int, *, maxv: int = 9999) -> QSpinBox:
        spn = QSpinBox()
        spn.setRange(0, maxv)
        spn.setValue(value)
        spn.setMinimumHeight(34)
        return spn

    def export_rows(self) -> list[dict]:
        out: list[dict] = []
        for r in range(self.rowCount()):
            sku = (self.item(r, self.COL_SKU).text() if self.item(r, self.COL_SKU) else "").strip()
            if not sku:
                continue
            name = (self.item(r, self.COL_NAME).text() if self.item(r, self.COL_NAME) else "").strip()
            cost = float(self.cellWidget(r, self.COL_COST).value())
            sale = float(self.cellWidget(r, self.COL_SALE).value())
            qty = int(self.cellWidget(r, self.COL_QTY).value())
            thresh = int(self.cellWidget(r, self.COL_THRESH).value())
            skip = self.cellWidget(r, self.COL_SKIP).isChecked()
            listed = self.cellWidget(r, self.COL_LISTED).isChecked()
            out.append({
                "sku": sku, "name": name,
                "cost": cost, "sale": sale,
                "qty": qty, "thresh": thresh,
                "skip": skip or not listed,
                "listed": listed,
            })
        return out


class _Step1Shop(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(_StepHeader(
            1, 4, "超市商品盘点",
            "把货架上所有超市商品当前的实物数量、进货单价、销售价、补货阈值录入。"
            "勾选『本店上架』表示前台/Telegram 可卖；未上架的保留在总库但不展示。",
        ))
        tip = QLabel(
            "提示：这一步是账实差异审计的『起点』——录入的数字就是『从此刻起的账面起点』，"
            "以前的错账不会追。请按真实点数填，不要凑数。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"color:{_p('primary')};background:{_p('primary_10pct')};"
            f"border:1px solid {_p('border')};border-radius:8px;padding:12px 16px;font-size:13px;"
        )
        lay.addWidget(tip)

        self.table = _ShopStockTable()
        fd_apply_table_palette(self.table)
        lay.addWidget(self.table, 1)

        if self.table.rowCount() == 0:
            empty = QLabel(
                "（超市商品库为空，本步无需录入。下一步直接录入客房消耗品即可。）"
            )
            empty.setStyleSheet(f"color:{_p('text_muted')};padding:24px;")
            empty.setAlignment(Qt.AlignCenter)
            lay.addWidget(empty)

    def collect(self) -> list[dict]:
        return self.table.export_rows()


# ─────────────────────────────────────────────────────────────────────────────
#  第 2 步：客房消耗品盘点
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_CONSUMABLES = [
    ("毛巾", "条"),
    ("浴巾", "条"),
    ("洗发水（小瓶）", "瓶"),
    ("沐浴露（小瓶）", "瓶"),
    ("香皂", "块"),
    ("牙刷", "套"),
    ("梳子", "把"),
    ("矿泉水", "瓶"),
    ("卷纸", "卷"),
    ("拖鞋", "双"),
]


class _ConsumableTable(QTableWidget):
    """每行：名称 / 单位 / 成本价 / 当前实物 / 补货阈值 / 跳过监控？"""

    COL_NAME, COL_UNIT, COL_COST, COL_QTY, COL_THRESH, COL_SKIP = range(6)

    def __init__(self, parent=None):
        super().__init__(0, 6, parent)
        self.setObjectName("ConsumableTable")
        self.setHorizontalHeaderLabels([
            "消耗品名称", "单位", "成本价",
            "当前实物", "补货阈值", "跳过监控",
        ])
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
        # 有数值输入框的列必须用交互模式，否则列宽设置会被自动调整
        self.horizontalHeader().setSectionResizeMode(self.COL_COST, QHeaderView.Interactive)
        self.horizontalHeader().setSectionResizeMode(self.COL_QTY, QHeaderView.Interactive)
        self.horizontalHeader().setSectionResizeMode(self.COL_THRESH, QHeaderView.Interactive)
        self.setColumnWidth(self.COL_COST, 100)
        self.setColumnWidth(self.COL_QTY, 100)
        self.setColumnWidth(self.COL_THRESH, 100)
        self.setAlternatingRowColors(False)
        self._load_existing_or_default()

    def _load_existing_or_default(self) -> None:
        # 若已经创建过消耗品，沿用；否则用默认列表占位
        existing = db.execute(
            """SELECT item_id, name, unit, cost_price, in_monitoring, reorder_threshold
               FROM inventory_items WHERE category=? ORDER BY name""",
            (CATEGORY_CONSUMABLE,),
        ).fetchall()
        if existing:
            for item_id, name, unit, cost, monitor, thresh in existing:
                book_qty = self._book_qty_for(item_id)
                self._append_row(name, unit, float(cost or 0), int(book_qty),
                                 int(thresh or 0), skip=not bool(monitor),
                                 item_id=item_id)
        else:
            seed_map = {n: (u, c, t) for n, u, c, t in default_consumable_seed_rows()}
            for name, unit in _DEFAULT_CONSUMABLES:
                _u, cost, thresh = seed_map.get(name, (unit, 0.0, 0))
                self._append_row(name, unit, float(cost), 0, int(thresh), skip=False, item_id="")

    @staticmethod
    def _book_qty_for(item_id: str) -> int:
        row = db.execute(
            "SELECT COALESCE(SUM(qty_change), 0) FROM inventory_movements WHERE item_id=?",
            (item_id,),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _append_row(self, name: str, unit: str, cost: float, qty: int,
                    thresh: int, *, skip: bool, item_id: str) -> None:
        r = self.rowCount()
        self.insertRow(r)
        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.UserRole, item_id)
        self.setItem(r, self.COL_NAME, name_item)
        self.setItem(r, self.COL_UNIT, QTableWidgetItem(unit))
        cost_spn = QDoubleSpinBox()
        cost_spn.setRange(0, 999999); cost_spn.setDecimals(2); cost_spn.setValue(cost)
        self.setCellWidget(r, self.COL_COST, cost_spn)
        qty_spn = QSpinBox(); qty_spn.setRange(0, 999999); qty_spn.setValue(qty)
        self.setCellWidget(r, self.COL_QTY, qty_spn)
        thresh_spn = QSpinBox(); thresh_spn.setRange(0, 999999); thresh_spn.setValue(thresh)
        self.setCellWidget(r, self.COL_THRESH, thresh_spn)
        cb = QCheckBox("跳过")
        cb.setChecked(skip)
        self.setCellWidget(r, self.COL_SKIP, cb)

    def add_blank_row(self) -> None:
        self._append_row("", "件", 0.0, 0, 0, skip=False, item_id="")
        self.setCurrentCell(self.rowCount() - 1, self.COL_NAME)
        self.editItem(self.item(self.rowCount() - 1, self.COL_NAME))

    def remove_current_row(self) -> None:
        r = self.currentRow()
        if r >= 0:
            self.removeRow(r)

    def export_rows(self) -> list[dict]:
        out: list[dict] = []
        for r in range(self.rowCount()):
            name_item = self.item(r, self.COL_NAME)
            name = (name_item.text() if name_item else "").strip()
            if not name:
                continue
            unit_item = self.item(r, self.COL_UNIT)
            unit = (unit_item.text() if unit_item else "件").strip() or "件"
            cost = float(self.cellWidget(r, self.COL_COST).value())
            qty = int(self.cellWidget(r, self.COL_QTY).value())
            thresh = int(self.cellWidget(r, self.COL_THRESH).value())
            skip = self.cellWidget(r, self.COL_SKIP).isChecked()
            existing_id = name_item.data(Qt.UserRole) or ""
            out.append({
                "item_id": existing_id, "name": name, "unit": unit,
                "cost": cost, "qty": qty, "thresh": thresh, "skip": skip,
            })
        return out


class _Step2Consumable(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(_StepHeader(
            2, 4, "客房消耗品盘点",
            "把每间客房会用到的消耗品（毛巾、洗发水、矿泉水…）当前的总实物量录入。"
            "已经预填了常见品类，你可以直接改名、改数，或者新增/删除。",
        ))

        tools = QHBoxLayout()
        btn_add = QPushButton("➕ 新增一行")
        btn_add.setObjectName("SolidPrimaryBtn")
        btn_add.clicked.connect(lambda: self.table.add_blank_row())
        btn_del = QPushButton("🗑️ 删除选中行")
        btn_del.setObjectName("FdDangerBtn")
        btn_del.clicked.connect(lambda: self.table.remove_current_row())
        tools.addWidget(btn_add); tools.addWidget(btn_del); tools.addStretch()
        lay.addLayout(tools)

        self.table = _ConsumableTable()
        fd_apply_table_palette(self.table)
        lay.addWidget(self.table, 1)

    def collect(self) -> list[dict]:
        return self.table.export_rows()


# ─────────────────────────────────────────────────────────────────────────────
#  第 3 步：房型标准配备
# ─────────────────────────────────────────────────────────────────────────────
class _RoomTypeStandardTable(QTableWidget):
    """行 = 房型 × 消耗品，单元格 = 每次入住补给数量（0 表示不补）。"""

    def __init__(self, room_types: list[tuple[str, str]],
                 consumables: list[dict], parent=None):
        super().__init__(len(consumables), 1 + len(room_types), parent)
        self.setObjectName("RoomTypeAllocTable")
        self._room_types = room_types
        self._consumables = consumables

        headers = ["消耗品"] + [f"{name}\n({tid})" for tid, name in room_types]
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.setAlternatingRowColors(False)
        # 给数值输入列设固定宽度 + 交互模式
        for c in range(1, 1 + len(room_types)):
            self.horizontalHeader().setSectionResizeMode(c, QHeaderView.Interactive)
            self.setColumnWidth(c, 80)

        for r, item in enumerate(consumables):
            self._set_label(r, 0, f"{item['name']} ({item['unit']})", item_id=item.get("item_id", ""))
            for c, (tid, _name) in enumerate(room_types, start=1):
                spn = QSpinBox()
                spn.setRange(0, 99)
                spn.setMinimumHeight(34)
                cname = item.get("name", "")
                preset = 0
                if item.get("item_id"):
                    row = db.execute(
                        """SELECT standard_qty FROM room_type_consumable_standards
                           WHERE type_id=? AND item_id=? AND trigger_event='CHECKIN'""",
                        (tid, item["item_id"]),
                    ).fetchone()
                    if row:
                        preset = int(row[0] or 0)
                else:
                    preset = standard_qty_for(tid, cname)
                spn.setValue(preset)
                self.setCellWidget(r, c, spn)

    def _set_label(self, r: int, c: int, text: str, *, item_id: str = "") -> None:
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        it.setData(Qt.UserRole, item_id)
        self.setItem(r, c, it)

    def export(self) -> list[dict]:
        out: list[dict] = []
        for r in range(self.rowCount()):
            label_item = self.item(r, 0)
            if not label_item:
                continue
            item_id = label_item.data(Qt.UserRole) or ""
            if not item_id:
                continue  # 还没在第 2 步落库的行（新建但未提交）
            for c, (tid, _name) in enumerate(self._room_types, start=1):
                qty = int(self.cellWidget(r, c).value())
                if qty > 0:
                    out.append({"type_id": tid, "item_id": item_id,
                                "standard_qty": qty, "trigger_event": "CHECKIN"})
        return out


class _Step3RoomTypeStandard(QWidget):
    def __init__(self):
        super().__init__()
        self._table: Optional[_RoomTypeStandardTable] = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)
        self._layout.addWidget(_StepHeader(
            3, 4, "房型标准配备",
            "每个房型『每次入住时应补什么、补几件』。这是账实差异引擎判断"
            "『客房消耗品被消耗多少才合理』的依据。",
        ))
        self._placeholder = QLabel(
            "请在第 2 步先把至少一个客房消耗品落库，再回到本步配置房型标准。"
        )
        self._placeholder.setStyleSheet(f"color:{_p('text_muted')};padding:24px;")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(self._placeholder)
        self._layout.addStretch(1)

    def refresh(self) -> None:
        """每次切到本步时调用：从库存物品表和房型模板表重建表格。"""
        room_types = db.execute(
            "SELECT type_id, type_name FROM room_type_templates ORDER BY type_name"
        ).fetchall()
        consumables = db.execute(
            """SELECT item_id, name, unit FROM inventory_items
               WHERE category=? ORDER BY name""",
            (CATEGORY_CONSUMABLE,),
        ).fetchall()
        if self._table is not None:
            self._layout.removeWidget(self._table)
            self._table.deleteLater()
            self._table = None
        self._placeholder.setVisible(not (room_types and consumables))
        if not room_types or not consumables:
            if not room_types:
                self._placeholder.setText(
                    "尚未配置房型。请先在『系统设置 → 房型管理』里建好房型，再回到本步。\n"
                    "（也可以先点『下一步』跳过，回头再补。）"
                )
            return
        consumables_list = [{"item_id": r[0], "name": r[1], "unit": r[2] or "件"} for r in consumables]
        self._table = _RoomTypeStandardTable(room_types=list(room_types),
                                             consumables=consumables_list)
        fd_apply_table_palette(self._table)
        self._layout.insertWidget(self._layout.count() - 1, self._table, 1)

    def collect(self) -> list[dict]:
        return self._table.export() if self._table else []


# ─────────────────────────────────────────────────────────────────────────────
#  第 4 步：老板确认
# ─────────────────────────────────────────────────────────────────────────────
class _Step4Confirm(QWidget):
    def __init__(self):
        super().__init__()
        self._summary = QLabel("（待生成摘要）")
        self._summary.setWordWrap(True)
        self._summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._summary.setStyleSheet(
            f"background:{_p('sidebar')};color:{_p('surface')};padding:14px 16px;border-radius:8px;"
            "font-family:'Consolas','Microsoft YaHei UI';font-size:13px;line-height:1.55;"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(_StepHeader(
            4, 4, "老板确认 + 生成期初快照",
            "下面是即将落地的期初基线。请仔细核对——一旦确认，"
            "系统会用哈希算法给当前所有数据签字，从此账实差异审计有了不可篡改的起点。",
        ))
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        wrap = QWidget(); wrap.setLayout(QVBoxLayout()); wrap.layout().addWidget(self._summary)
        sc.setWidget(wrap)
        lay.addWidget(sc, 1)

        warn = QLabel(
            "请仔细核对上方数据，确认无误后再点击生成。\n"
            "期初快照一旦生成即不可修改，后续账实差异均以此基线为参考。"
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color:{_p('danger')};background:{_p('surface_alt')};padding:10px 14px;border-radius:6px;")
        lay.addWidget(warn)

    def render_summary(self, shop_rows: list[dict], cons_rows: list[dict],
                       std_rows: list[dict]) -> None:
        lines: list[str] = []
        lines.append("【超市商品】")
        if shop_rows:
            for r in shop_rows:
                tag = "❌ 跳过监控" if r["skip"] else "✅ 纳入监控"
                lines.append(f"  · {r['sku']:<10} {r['name']:<14} 实物={r['qty']}件  进价¥{r['cost']:.2f}  售价¥{r['sale']:.2f}  阈值={r['thresh']}  {tag}")
        else:
            lines.append("  （无）")

        lines.append("")
        lines.append("【客房消耗品】")
        if cons_rows:
            for r in cons_rows:
                tag = "❌ 跳过监控" if r["skip"] else "✅ 纳入监控"
                lines.append(f"  · {r['name']:<16} 实物={r['qty']}{r['unit']}  成本¥{r['cost']:.2f}  阈值={r['thresh']}  {tag}")
        else:
            lines.append("  （无）")

        lines.append("")
        lines.append("【房型标准配备（每次入住补给）】")
        if std_rows:
            by_type: dict[str, list[str]] = {}
            for s in std_rows:
                by_type.setdefault(s["type_id"], []).append(f"item={s['item_id']} × {s['standard_qty']}")
            for tid, items in by_type.items():
                lines.append(f"  · 房型 {tid}: " + ", ".join(items))
        else:
            lines.append("  （未配置，账实差异引擎将按『所有消耗都算异常』看待，建议返回上一步补）")

        lines.append("")
        lines.append("一经确认，系统会：")
        lines.append("  1) 把上述数字写为库存变动表中的期初流水，链入哈希链；")
        lines.append("  2) 生成库存基线快照表中的期初快照（哈希签字）；")
        lines.append("  3) 标记初始盘点完成时间，放行主界面；")
        lines.append("  4) 后台尝试把快照摘要上传云端（失败也不影响本地放行）。")

        self._summary.setText("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
#  主向导
# ─────────────────────────────────────────────────────────────────────────────
class InitialStocktakeWizard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("期初盘点向导 · 厂家激活后必做")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        from ui_helpers import style_wizard, show_warning, show_info, show_error, ask_confirm
        style_wizard(self, size="xlarge")
        try:
            self.setFont(QFont("Microsoft YaHei UI", 11))
        except Exception:
            pass

        # 启动时把超市商品镜像到库存物品，避免老板感知不到自己有多少商品
        try:
            sync_shop_items_to_inventory(db)
        except Exception as exc:
            logger.warning("[InitialStocktakeWizard] 同步 shop_items 失败: %s", exc)

        self._step1 = _Step1Shop()
        self._step2 = _Step2Consumable()
        self._step3 = _Step3RoomTypeStandard()
        self._step4 = _Step4Confirm()
        self._step2_committed = False  # 第 2 步是否已经把数据落到库存物品表

        self._stack = QStackedWidget()
        for w in (self._step1, self._step2, self._step3, self._step4):
            self._stack.addWidget(w)

        nav = QHBoxLayout()
        self._btn_prev = QPushButton("← 上一步")
        self._btn_prev.setObjectName("FdGhostBtn")
        self._btn_prev.clicked.connect(self._go_prev)
        self._btn_next = QPushButton("下一步 →")
        self._btn_next.setObjectName("SolidPrimaryBtn")
        self._btn_next.clicked.connect(self._go_next)
        self._btn_finish = QPushButton("✅ 确认并生成期初快照")
        self._btn_finish.setObjectName("SolidPrimaryBtn")
        self._btn_finish.setStyleSheet(f"background:{_p('amount_positive')};color:{_p('surface')};font-weight:700;")
        self._btn_finish.clicked.connect(self._on_finish)
        self._btn_finish.setVisible(False)
        nav.addWidget(self._btn_prev)
        nav.addStretch()
        nav.addWidget(self._btn_next)
        nav.addWidget(self._btn_finish)

        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(16, 16, 16, 16)
        wrap.setSpacing(12)
        wrap.addWidget(self._stack, 1)
        wrap.addLayout(nav)

        self._update_nav()

    # ── 导航 ─────────────────────────────────────────────────────────────
    def _update_nav(self) -> None:
        idx = self._stack.currentIndex()
        self._btn_prev.setEnabled(idx > 0)
        is_last = idx == self._stack.count() - 1
        self._btn_next.setVisible(not is_last)
        self._btn_finish.setVisible(is_last)

    def _go_prev(self) -> None:
        i = self._stack.currentIndex()
        if i > 0:
            self._stack.setCurrentIndex(i - 1)
            self._update_nav()

    def _go_next(self) -> None:
        idx = self._stack.currentIndex()
        if idx == 1:
            # 第 2 步 → 第 3 步前先把消耗品落库（这样第 3 步的房型矩阵能拿到物品编号）
            if not self._commit_step2_to_db():
                return
        if idx == 2:
            # 进入第 4 步前生成摘要
            self._step4.render_summary(
                shop_rows=self._step1.collect(),
                cons_rows=self._step2.collect(),
                std_rows=self._step3.collect(),
            )
        self._stack.setCurrentIndex(idx + 1)
        if self._stack.currentIndex() == 2:
            self._step3.refresh()
        self._update_nav()

    # ── 第 2 步预提交：把消耗品落到库存物品表，让第 3 步能引用 ──
    def _commit_step2_to_db(self) -> bool:
        try:
            cons_rows = self._step2.collect()
        except Exception as exc:
            show_warning(self, "录入异常", f"客房消耗品录入有问题：{exc}")
            return False
        # 允许为空（老板可以选择只盘超市），但要提示一下
        if not cons_rows:
            if not ask_confirm(
                self, "未录入消耗品",
                "你尚未录入任何客房消耗品。\n这样第 3 步就没有标准可配，账实差异引擎"
                "会把所有客房消耗都视为『未授权』。\n\n确定继续吗？",
            ):
                return False
        for r in cons_rows:
            item_id = r.get("item_id") or make_item_id(CATEGORY_CONSUMABLE,
                                                      uuid.uuid4().hex[:8])
            upsert_item(
                db,
                item_id=item_id, category=CATEGORY_CONSUMABLE,
                name=r["name"], unit=r["unit"],
                cost_price=r["cost"], reorder_threshold=r["thresh"],
                in_monitoring=not r["skip"],
                skip_reason="老板在期初盘点向导勾选『跳过监控』" if r["skip"] else "",
            )
            r["item_id"] = item_id  # 回填给后续步骤
        self._step2_committed = True
        return True

    # ── 完成：写期初流水 → 房型标准 → 期初快照 → 云端 ──
    def _on_finish(self) -> None:
        shop_rows = self._step1.collect()
        cons_rows = self._step2.collect()
        std_rows = self._step3.collect()

        try:
            self._persist_shop(shop_rows)
            self._persist_consumables(cons_rows)
            self._persist_standards(std_rows)
            self._persist_openings(shop_rows, cons_rows)
            snap = build_baseline_snapshot(db, self._current_username(),
                                           note="期初盘点向导生成")
        except Exception as exc:
            show_error(
                self, "期初盘点失败",
                f"生成期初快照时出错：{exc}\n\n"
                "（已写入的部分会保留，下次启动可继续；请把这段错误转给厂家。）"
            )
            return

        cloud_ok = False
        try:
            cloud_ok = upload_snapshot_to_cloud(db, snap["snapshot_id"])
        except Exception as exc:
            logger.warning("[InitialStocktakeWizard] 云端备份失败: %s", exc)

        msg = (
            "期初盘点完成！\n\n"
            f"• 快照 ID：{snap['snapshot_id'][:12]}…\n"
            f"• 签字（前 16 位）：{snap['snapshot_hash'][:16]}\n"
            f"• 纳入监控：{snap['monitored_count']} 项\n"
            f"• 跳过监控：{snap['skipped_count']} 项\n"
            f"• 云端备份：{'已上传' if cloud_ok else '本地保留，未上传（不影响放行）'}\n\n"
            "从此刻起，所有库存变动会被记入哈希链；每 15 天的周期盘点"
            "会自动算账实差异，超过 5% 报警。"
        )
        show_info(self, "期初盘点完成", msg)
        self.accept()

    # ── 辅助 ─────────────────────────────────────────────────────────────
    def _current_username(self) -> str:
        try:
            from permission_system import VENDOR_USERNAME
            return VENDOR_USERNAME
        except Exception:
            return "admin"

    def _persist_shop(self, rows: list[dict]) -> None:
        for r in rows:
            sku = r["sku"]
            listed = 1 if r.get("listed") else 0
            db.execute(
                """UPDATE shop_items SET
                      cost_price=?, price=?, listed=?
                   WHERE sku=?""",
                (r["cost"], r["sale"], listed, sku),
            )
            if not listed:
                continue
            item_id = make_item_id(CATEGORY_SHOP, sku)
            upsert_item(
                db,
                item_id=item_id, category=CATEGORY_SHOP, source_sku=sku,
                name=r["name"] or sku, unit="件",
                cost_price=r["cost"], sale_price=r["sale"],
                reorder_threshold=r["thresh"],
                in_monitoring=not r["skip"],
                skip_reason="老板在期初盘点向导勾选『跳过监控』" if r["skip"] else "",
            )

    def _persist_consumables(self, rows: list[dict]) -> None:
        for r in rows:
            item_id = r.get("item_id") or make_item_id(CATEGORY_CONSUMABLE,
                                                      uuid.uuid4().hex[:8])
            upsert_item(
                db,
                item_id=item_id, category=CATEGORY_CONSUMABLE,
                name=r["name"], unit=r["unit"],
                cost_price=r["cost"], reorder_threshold=r["thresh"],
                in_monitoring=not r["skip"],
                skip_reason="老板在期初盘点向导勾选『跳过监控』" if r["skip"] else "",
            )
            r["item_id"] = item_id

    def _persist_standards(self, rows: list[dict]) -> None:
        # 全量重置当前会话中提到的房型 × 消耗品标准
        seen_pairs: set[tuple[str, str, str]] = set()
        for r in rows:
            key = (r["type_id"], r["item_id"], r["trigger_event"])
            seen_pairs.add(key)
            set_room_type_standard(
                db,
                type_id=r["type_id"], item_id=r["item_id"],
                standard_qty=r["standard_qty"], trigger_event=r["trigger_event"],
            )

    def _persist_openings(self, shop_rows: list[dict], cons_rows: list[dict]) -> None:
        item_to_qty: dict[str, int] = {}
        for r in shop_rows:
            item_to_qty[make_item_id(CATEGORY_SHOP, r["sku"])] = int(r["qty"])
        for r in cons_rows:
            iid = r.get("item_id")
            if iid:
                item_to_qty[iid] = int(r["qty"])
        record_opening_quantities(db, item_to_qty, operator_id=self._current_username())


# ─────────────────────────────────────────────────────────────────────────────
#  对外便捷入口
# ─────────────────────────────────────────────────────────────────────────────
def open_initial_stocktake_wizard(parent=None) -> bool:
    """供主程序/设置页调用：弹出期初盘点向导。返回是否完成。"""
    dlg = InitialStocktakeWizard(parent)
    return dlg.exec() == QDialog.DialogCode.Accepted
