"""
======================================================
ShadowGuard — 房价计算规则引擎 (v1.0)
口号: 每一家酒店的定价逻辑都可以不同

功能:
  - 基础价 × 会员折扣 × 节假日系数 × 连住优惠
  - 钟点房独立计费（按小时阶梯）
  - 节假日/旺季价格上浮日历
  - 团队/协议价管理
  - 自定义计算公式（简单表达式解析）
  - 退房超时自动计费

配置存储在 pricing_rules 表中
======================================================
"""
from __future__ import annotations

import json
import re
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QWidget, QGroupBox, QFormLayout, QSpinBox,
    QDoubleSpinBox, QDateEdit,
)
from PySide6.QtCore import QDate

from ui_helpers import style_dialog, build_dialog_header, show_info, show_warning
from ui_surface import fd_apply_table_palette
from design_tokens import _p
from database import db
import logging
logger = logging.getLogger(__name__)


# ================================================================
# 数据库初始化
# ================================================================
def init_pricing_tables():
    """初始化定价相关表"""
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS pricing_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_type TEXT NOT NULL,
                base_price REAL NOT NULL DEFAULT 100,
                hourly_price REAL DEFAULT 50,
                hourly_first_hours INTEGER DEFAULT 3,
                hourly_extend_price REAL DEFAULT 20,
                overtime_rate REAL DEFAULT 0.5,
                late_checkout_hour INTEGER DEFAULT 14,
                early_checkin_hour INTEGER DEFAULT 10,
                discount_silver REAL DEFAULT 0.95,
                discount_gold REAL DEFAULT 0.90,
                discount_diamond REAL DEFAULT 0.80,
                currency TEXT DEFAULT 'RMB',
                is_active INTEGER DEFAULT 1,
                UNIQUE(room_type)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS holiday_pricing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_start TEXT NOT NULL,
                date_end TEXT NOT NULL,
                label TEXT,
                price_multiplier REAL DEFAULT 1.5,
                room_type TEXT DEFAULT '*',
                is_active INTEGER DEFAULT 1
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS group_rates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT NOT NULL,
                room_type TEXT,
                negotiated_price REAL NOT NULL,
                min_rooms INTEGER DEFAULT 1,
                contact TEXT,
                note TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
    except Exception as e:
        logger.warning("定价表初始化失败: %s", e)


# ================================================================
# 房价计算引擎
# ================================================================
class PricingEngine:
    """房价计算核心引擎"""

    @staticmethod
    def get_room_price(room_type: str, checkin_date: date = None,
                       checkout_date: date = None, member_level: str = None,
                       nights: int = None) -> Dict[str, Any]:
        """
        计算一个房间的最终价格。
        返回: {base, discount, holiday_factor, total_per_night, total, breakdown}
        """
        rule = db.execute(
            "SELECT * FROM pricing_rules WHERE room_type=? AND is_active=1",
            (room_type,)
        ).fetchone()

        if not rule:
            return {
                "base": 100, "discount": 0, "holiday_factor": 1.0,
                "total_per_night": 100, "total": 100,
                "breakdown": "未配置价格规则，使用默认价100元"
            }

        base_price = float(rule[2])

        # 会员折扣
        discount_map = {
            "SILVER": float(rule[8] or 0.95),
            "GOLD": float(rule[9] or 0.90),
            "DIAMOND": float(rule[10] or 0.80),
        }
        discount_rate = discount_map.get(member_level, 1.0) if member_level else 1.0

        # 节假日系数
        holiday_factor = 1.0
        holiday_label = ""
        if checkin_date:
            date_str = checkin_date.strftime("%Y-%m-%d") if isinstance(checkin_date, date) else str(checkin_date)
            holidays = db.execute(
                "SELECT label, price_multiplier FROM holiday_pricing "
                "WHERE date_start<=? AND date_end>=? AND (room_type=? OR room_type='*') AND is_active=1",
                (date_str, date_str, room_type)
            ).fetchall()
            if holidays:
                holiday_factor = max(h[1] for h in holidays)
                holiday_label = holidays[0][0]

        # 连住优惠（住3晚以上9折，7晚以上85折）
        n = nights or 1
        long_stay_discount = 1.0
        if n >= 7:
            long_stay_discount = 0.85
        elif n >= 3:
            long_stay_discount = 0.90

        # 计算
        price_per_night = base_price * discount_rate * holiday_factor * long_stay_discount
        total_price = price_per_night * n

        breakdown_parts = [f"基础价 {base_price:.0f}"]
        if discount_rate < 1.0:
            breakdown_parts.append(f"会员折 {discount_rate:.0%}")
        if holiday_factor > 1.0:
            breakdown_parts.append(f"节假日×{holiday_factor} ({holiday_label})")
        if long_stay_discount < 1.0:
            breakdown_parts.append(f"连住{n}晚折 {long_stay_discount:.0%}")

        return {
            "base": base_price,
            "discount_rate": discount_rate,
            "holiday_factor": holiday_factor,
            "long_stay_discount": long_stay_discount,
            "total_per_night": round(price_per_night, 2),
            "total": round(total_price, 2),
            "breakdown": " × ".join(breakdown_parts) + f" = {price_per_night:.0f}/晚",
            "nights": n,
        }

    @staticmethod
    def get_hourly_price(room_type: str, hours: float) -> Dict[str, Any]:
        """计算钟点房价格"""
        rule = db.execute(
            "SELECT * FROM pricing_rules WHERE room_type=? AND is_active=1",
            (room_type,)
        ).fetchone()

        if not rule:
            return {"total": hours * 50, "breakdown": "未配置钟点价"}

        first_hours = int(rule[5] or 3)
        first_price = float(rule[4] or 50)
        extend_price = float(rule[6] or 20)

        if hours <= first_hours:
            total = first_price
            breakdown = f"钟点{first_hours}小时内 {first_price:.0f}元"
        else:
            extra = hours - first_hours
            total = first_price + extra * extend_price
            breakdown = f"前{first_hours}h {first_price:.0f} + 超时{extra:.0f}h×{extend_price:.0f}"

        return {"total": round(total, 2), "breakdown": breakdown}

    @staticmethod
    def get_overtime_charge(room_type: str, overtime_hours: float) -> float:
        """计算超时房费"""
        rule = db.execute(
            "SELECT base_price, overtime_rate FROM pricing_rules WHERE room_type=? AND is_active=1",
            (room_type,)
        ).fetchone()
        if not rule:
            return overtime_hours * 20
        base = float(rule[0])
        rate = float(rule[1] or 0.5)
        return round(base * rate * overtime_hours, 2)

    @staticmethod
    def get_group_price(group_name: str, room_type: str) -> Optional[float]:
        """获取协议价"""
        r = db.execute(
            "SELECT negotiated_price FROM group_rates WHERE group_name=? AND room_type=? AND is_active=1",
            (group_name, room_type)
        ).fetchone()
        return float(r[0]) if r else None

    @staticmethod
    def get_day_rate(room_type: str, target_date: date = None) -> float:
        """获取指定日期的单日价格（包含节假日系数）"""
        rule = db.execute(
            "SELECT base_price FROM pricing_rules WHERE room_type=? AND is_active=1",
            (room_type,)
        ).fetchone()
        base = float(rule[0]) if rule else 100

        if target_date:
            ds = target_date.strftime("%Y-%m-%d")
            h = db.execute(
                "SELECT price_multiplier FROM holiday_pricing "
                "WHERE date_start<=? AND date_end>=? AND (room_type=? OR room_type='*') AND is_active=1",
                (ds, ds, room_type)
            ).fetchone()
            if h:
                base *= float(h[0])

        return round(base, 2)

    @staticmethod
    def get_dynamic_rate(room_type: str, base_rate: float) -> dict:
        """动态房价：基于当前出租率自动调整。
        出租率 < 30% → 85折（吸引客流）
        出租率 30-60% → 95折
        出租率 60-85% → 原价
        出租率 > 85% → 1.2倍（旺季溢价）
        返回: {"adjusted_rate": float, "multiplier": float, "occupancy": float, "description": str}
        """
        total = db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
        inhouse = db.execute("SELECT COUNT(*) FROM rooms WHERE status='INHOUSE'").fetchone()[0]
        occ = (inhouse / total * 100) if total > 0 else 0

        if occ < 30:
            multiplier = 0.85
            desc = f"低出租率 {occ:.0f}% → 85折引流"
        elif occ < 60:
            multiplier = 0.95
            desc = f"出租率 {occ:.0f}% → 95折"
        elif occ < 85:
            multiplier = 1.0
            desc = f"正常出租率 {occ:.0f}% → 原价"
        else:
            multiplier = 1.2
            desc = f"高出租率 {occ:.0f}% → 1.2倍旺季价"

        return {
            "adjusted_rate": round(base_rate * multiplier, 2),
            "multiplier": multiplier,
            "occupancy": round(occ, 1),
            "description": desc,
        }

    @staticmethod
    def calculate_long_stay_discount(nights: int, base_rate: float) -> dict:
        """连住折扣计算。
        3-6晚: 9折
        7-13晚: 85折
        14-29晚: 8折
        30+晚: 75折（长包房）
        返回: {"discount_rate": float, "adjusted_rate": float, "description": str}
        """
        n = max(1, int(nights))
        if n >= 30:
            rate = 0.75
            desc = f"长包房 {n} 晚 → 75折"
        elif n >= 14:
            rate = 0.80
            desc = f"半月 {n} 晚 → 8折"
        elif n >= 7:
            rate = 0.85
            desc = f"周租 {n} 晚 → 85折"
        elif n >= 3:
            rate = 0.90
            desc = f"连住 {n} 晚 → 9折"
        else:
            rate = 1.0
            desc = f"单晚入住，无连住折扣"

        return {
            "discount_rate": rate,
            "adjusted_rate": round(base_rate * rate, 2),
            "description": desc,
        }

    @staticmethod
    def calculate_early_bird_discount(days_ahead: int, rate: float) -> dict:
        """早鸟优惠：提前预订天数越多折扣越大。
        提前 7-13 天: 95折
        提前 14-29 天: 9折
        提前 30+ 天: 85折
        返回: {"discount_rate": float, "adjusted_rate": float, "description": str}
        """
        d = max(0, int(days_ahead))
        if d >= 30:
            disc = 0.85
            desc = f"提前 {d} 天预订 → 85折早鸟价"
        elif d >= 14:
            disc = 0.90
            desc = f"提前 {d} 天预订 → 9折早鸟价"
        elif d >= 7:
            disc = 0.95
            desc = f"提前 {d} 天预订 → 95折早鸟价"
        else:
            disc = 1.0
            desc = f"提前 {d} 天预订，无早鸟折扣"

        return {
            "discount_rate": disc,
            "adjusted_rate": round(rate * disc, 2),
            "description": desc,
        }

    @staticmethod
    def save_rule(room_type: str, base_price: float, hourly_price: float = 50,
                  hourly_first: int = 3, hourly_extend: float = 20,
                  overtime_rate: float = 0.5, late_hour: int = 14,
                  early_hour: int = 10, disc_silver: float = 0.95,
                  disc_gold: float = 0.90, disc_diamond: float = 0.80):
        """保存/更新价格规则"""
        db.execute(
            "INSERT OR REPLACE INTO pricing_rules "
            "(room_type, base_price, hourly_price, hourly_first_hours, hourly_extend_price, "
            "overtime_rate, late_checkout_hour, early_checkin_hour, "
            "discount_silver, discount_gold, discount_diamond) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (room_type, base_price, hourly_price, hourly_first, hourly_extend,
             overtime_rate, late_hour, early_hour,
             disc_silver, disc_gold, disc_diamond)
        )

    @staticmethod
    def get_all_rules() -> List[Dict]:
        """获取所有价格规则"""
        rows = db.execute("SELECT * FROM pricing_rules WHERE is_active=1").fetchall()
        return [
            {
                "room_type": r[1], "base_price": r[2], "hourly_price": r[3],
                "hourly_first_hours": r[4], "hourly_extend_price": r[5],
                "overtime_rate": r[6], "late_checkout_hour": r[7],
                "early_checkin_hour": r[8],
                "discount_silver": r[9], "discount_gold": r[10], "discount_diamond": r[11],
            }
            for r in rows
        ]


# ================================================================
# 节假日定价管理对话框
# ================================================================
class HolidayPricingDialog(QDialog):
    """节假日/旺季价格管理"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📅 节假日 & 旺季价格管理")
        style_dialog(self, size="large")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(build_dialog_header(
            "节假日定价日历",
            "设置特定日期段的价格上浮倍数。例如春节期间×2.0，暑假×1.3。"
        ))

        # 表格
        self.tbl = QTableWidget()
        self.tbl.setColumnCount(5)
        self.tbl.setHorizontalHeaderLabels(["开始日期", "结束日期", "标签", "倍数", "适用房型"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        fd_apply_table_palette(self.tbl)
        layout.addWidget(self.tbl)

        # 新增行
        grp_add = QGroupBox("新增节假日规则")
        f = QFormLayout(grp_add)

        self.dt_start = QDateEdit(QDate.currentDate())
        self.dt_start.setCalendarPopup(True)
        f.addRow("开始日期:", self.dt_start)

        self.dt_end = QDateEdit(QDate.currentDate().addDays(7))
        self.dt_end.setCalendarPopup(True)
        f.addRow("结束日期:", self.dt_end)

        self.txt_label = QLineEdit()
        self.txt_label.setPlaceholderText("如: 春节 / 暑期旺季 / 泼水节")
        f.addRow("标签:", self.txt_label)

        self.spin_mult = QDoubleSpinBox()
        self.spin_mult.setRange(1.0, 10.0)
        self.spin_mult.setValue(1.5)
        self.spin_mult.setSingleStep(0.1)
        f.addRow("价格倍数:", self.spin_mult)

        self.cmb_room = QComboBox()
        self.cmb_room.addItem("全部房型", "*")
        types = db.execute("SELECT type_id FROM room_type_templates").fetchall()
        for (t,) in types:
            self.cmb_room.addItem(t, t)
        f.addRow("适用房型:", self.cmb_room)

        btn_add = QPushButton("添加规则")
        btn_add.setObjectName("SolidPrimaryBtn")
        btn_add.clicked.connect(self._add)
        f.addRow(btn_add)

        layout.addWidget(grp_add)

        btn_row = QHBoxLayout()
        btn_del = QPushButton("删除选中")
        btn_del.setObjectName("FdDangerBtn")
        btn_del.clicked.connect(self._del)
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("FdGhostBtn")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self._load()

    def _load(self):
        rows = db.execute(
            "SELECT id, date_start, date_end, label, price_multiplier, room_type "
            "FROM holiday_pricing WHERE is_active=1 ORDER BY date_start"
        ).fetchall()
        self.tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.tbl.setItem(i, 0, QTableWidgetItem(str(r[1])))
            self.tbl.setItem(i, 1, QTableWidgetItem(str(r[2])))
            self.tbl.setItem(i, 2, QTableWidgetItem(str(r[3] or "")))
            self.tbl.setItem(i, 3, QTableWidgetItem(f"×{r[4]}"))
            self.tbl.setItem(i, 4, QTableWidgetItem(str(r[5])))
            # store id
            self.tbl.item(i, 0).setData(Qt.UserRole, r[0])

    def _add(self):
        label = self.txt_label.text().strip()
        if not label:
            show_warning(self, "必填", "请输入节假日标签名称。")
            return
        ds = self.dt_start.date().toString("yyyy-MM-dd")
        de = self.dt_end.date().toString("yyyy-MM-dd")
        mult = self.spin_mult.value()
        rt = self.cmb_room.currentData()
        db.execute(
            "INSERT INTO holiday_pricing (date_start, date_end, label, price_multiplier, room_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (ds, de, label, mult, rt)
        )
        self._load()

    def _del(self):
        row = self.tbl.currentRow()
        if row < 0:
            return
        item = self.tbl.item(row, 0)
        if item:
            hid = item.data(Qt.UserRole)
            db.execute("UPDATE holiday_pricing SET is_active=0 WHERE id=?", (hid,))
            self._load()


# ================================================================
# 团队协议价管理对话框
# ================================================================
class GroupRateDialog(QDialog):
    """团队/旅行社协议价管理"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🤝 团队 & 协议价管理")
        style_dialog(self, size="large")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(build_dialog_header(
            "团队协议价",
            "为旅行社、公司客户设置固定协议价。入住时选择协议价方案自动套用。"
        ))

        self.tbl = QTableWidget()
        self.tbl.setColumnCount(5)
        self.tbl.setHorizontalHeaderLabels(["团队名称", "房型", "协议价", "最低间数", "备注"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        fd_apply_table_palette(self.tbl)
        layout.addWidget(self.tbl)

        grp_add = QGroupBox("新增协议价")
        f = QFormLayout(grp_add)

        self.txt_group = QLineEdit()
        self.txt_group.setPlaceholderText("如: 携程旅行社")
        f.addRow("团队名称:", self.txt_group)

        self.cmb_room2 = QComboBox()
        types = db.execute("SELECT type_id FROM room_type_templates").fetchall()
        for (t,) in types:
            self.cmb_room2.addItem(t, t)
        f.addRow("房型:", self.cmb_room2)

        self.spin_price = QDoubleSpinBox()
        self.spin_price.setRange(1, 99999)
        self.spin_price.setValue(120)
        f.addRow("协议价:", self.spin_price)

        self.spin_min = QSpinBox()
        self.spin_min.setRange(1, 100)
        self.spin_min.setValue(1)
        f.addRow("最低间数:", self.spin_min)

        self.txt_note = QLineEdit()
        f.addRow("备注:", self.txt_note)

        btn_add = QPushButton("添加")
        btn_add.setObjectName("SolidPrimaryBtn")
        btn_add.clicked.connect(self._add)
        f.addRow(btn_add)
        layout.addWidget(grp_add)

        btn_row = QHBoxLayout()
        btn_del = QPushButton("删除")
        btn_del.setObjectName("FdDangerBtn")
        btn_del.clicked.connect(self._del)
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("FdGhostBtn")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self._load()

    def _load(self):
        rows = db.execute(
            "SELECT id, group_name, room_type, negotiated_price, min_rooms, note "
            "FROM group_rates WHERE is_active=1 ORDER BY group_name"
        ).fetchall()
        self.tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.tbl.setItem(i, 0, QTableWidgetItem(str(r[1])))
            self.tbl.setItem(i, 1, QTableWidgetItem(str(r[2])))
            self.tbl.setItem(i, 2, QTableWidgetItem(f"¥{r[3]:.0f}"))
            self.tbl.setItem(i, 3, QTableWidgetItem(str(r[4])))
            self.tbl.setItem(i, 4, QTableWidgetItem(str(r[5] or "")))
            self.tbl.item(i, 0).setData(Qt.UserRole, r[0])

    def _add(self):
        gname = self.txt_group.text().strip()
        if not gname:
            show_warning(self, "必填", "请输入团队名称。")
            return
        rt = self.cmb_room2.currentText()
        price = self.spin_price.value()
        mins = self.spin_min.value()
        note = self.txt_note.text().strip()
        db.execute(
            "INSERT INTO group_rates (group_name, room_type, negotiated_price, min_rooms, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (gname, rt, price, mins, note)
        )
        self._load()

    def _del(self):
        row = self.tbl.currentRow()
        if row < 0:
            return
        gid = self.tbl.item(row, 0).data(Qt.UserRole)
        db.execute("UPDATE group_rates SET is_active=0 WHERE id=?", (gid,))
        self._load()


# ================================================================
# 价格规则编辑对话框
# ================================================================
class PricingRulesDialog(QDialog):
    """房型价格规则编辑"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("房价规则配置")
        style_dialog(self, size="medium")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(build_dialog_header(
            "房价计算规则",
            "配置基础价、钟点价、超时费率、会员折扣和早/晚入住时间。"
        ))

        f = QFormLayout()

        self.cmb_type = QComboBox()
        types = db.execute("SELECT type_id FROM room_type_templates").fetchall()
        for (t,) in types:
            self.cmb_type.addItem(t)
        f.addRow("房型:", self.cmb_type)

        self.spin_base = QDoubleSpinBox()
        self.spin_base.setRange(1, 99999)
        self.spin_base.setValue(100)
        f.addRow("基础价 (元/晚):", self.spin_base)

        self.spin_hourly = QDoubleSpinBox()
        self.spin_hourly.setRange(1, 9999)
        self.spin_hourly.setValue(50)
        f.addRow("钟点价 (元):", self.spin_hourly)

        self.spin_first_h = QSpinBox()
        self.spin_first_h.setRange(1, 12)
        self.spin_first_h.setValue(3)
        f.addRow("钟点首段 (小时):", self.spin_first_h)

        self.spin_extend = QDoubleSpinBox()
        self.spin_extend.setRange(1, 9999)
        self.spin_extend.setValue(20)
        f.addRow("钟点续时 (元/时):", self.spin_extend)

        self.spin_overtime = QDoubleSpinBox()
        self.spin_overtime.setRange(0.1, 5.0)
        self.spin_overtime.setValue(0.5)
        self.spin_overtime.setSingleStep(0.1)
        f.addRow("超时费率 (基础价×):", self.spin_overtime)

        self.spin_late = QSpinBox()
        self.spin_late.setRange(8, 20)
        self.spin_late.setValue(14)
        f.addRow("最晚退房 (时):", self.spin_late)

        self.spin_early = QSpinBox()
        self.spin_early.setRange(6, 14)
        self.spin_early.setValue(10)
        f.addRow("最早入住 (时):", self.spin_early)

        # 会员折扣
        grp_member = QGroupBox("会员折扣")
        mf = QFormLayout(grp_member)
        self.spin_silver = QDoubleSpinBox()
        self.spin_silver.setRange(0.5, 1.0)
        self.spin_silver.setValue(0.95)
        self.spin_silver.setSingleStep(0.05)
        mf.addRow("银卡折扣:", self.spin_silver)
        self.spin_gold = QDoubleSpinBox()
        self.spin_gold.setRange(0.5, 1.0)
        self.spin_gold.setValue(0.90)
        self.spin_gold.setSingleStep(0.05)
        mf.addRow("金卡折扣:", self.spin_gold)
        self.spin_diamond = QDoubleSpinBox()
        self.spin_diamond.setRange(0.5, 1.0)
        self.spin_diamond.setValue(0.80)
        self.spin_diamond.setSingleStep(0.05)
        mf.addRow("钻石卡折扣:", self.spin_diamond)
        f.addRow(grp_member)

        layout.addLayout(f)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾 保存规则")
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_save.clicked.connect(self._save)
        btn_save.setStyleSheet(f"background:{_p('amount_positive')};color:white;font-weight:700;")
        btn_row.addWidget(btn_save)

        btn_row.addStretch()

        btn_holiday = QPushButton("📅 节假日定价")
        btn_holiday.setObjectName("FdGhostBtn")
        btn_holiday.clicked.connect(self._holidays)
        btn_row.addWidget(btn_holiday)

        btn_group = QPushButton("🤝 协议价管理")
        btn_group.setObjectName("FdGhostBtn")
        btn_group.clicked.connect(self._groups)
        btn_row.addWidget(btn_group)

        btn_close = QPushButton("关闭")
        btn_close.setObjectName("FdGhostBtn")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

        self.cmb_type.currentTextChanged.connect(self._load_rule)
        if self.cmb_type.count() > 0:
            self._load_rule(self.cmb_type.currentText())

    def _load_rule(self, rt):
        rule = db.execute(
            "SELECT * FROM pricing_rules WHERE room_type=? AND is_active=1", (rt,)
        ).fetchone()
        if rule:
            self.spin_base.setValue(float(rule[2]))
            self.spin_hourly.setValue(float(rule[3]))
            self.spin_first_h.setValue(int(rule[4]))
            self.spin_extend.setValue(float(rule[5]))
            self.spin_overtime.setValue(float(rule[6]))
            self.spin_late.setValue(int(rule[7]))
            self.spin_early.setValue(int(rule[8]))
            self.spin_silver.setValue(float(rule[9]))
            self.spin_gold.setValue(float(rule[10]))
            self.spin_diamond.setValue(float(rule[11]))

    def _save(self):
        rt = self.cmb_type.currentText()
        PricingEngine.save_rule(
            rt,
            self.spin_base.value(),
            self.spin_hourly.value(),
            self.spin_first_h.value(),
            self.spin_extend.value(),
            self.spin_overtime.value(),
            self.spin_late.value(),
            self.spin_early.value(),
            self.spin_silver.value(),
            self.spin_gold.value(),
            self.spin_diamond.value(),
        )
        show_info(self, "保存成功", f"房型 {rt} 的定价规则已更新。")

    def _holidays(self):
        HolidayPricingDialog(self).exec()

    def _groups(self):
        GroupRateDialog(self).exec()