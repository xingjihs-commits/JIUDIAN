"""
energy_entry_page.py — C0-delta 电表抄录与周期能耗对账页
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
)
from PySide6.QtGui import QColor

import energy_audit_engine as engine
from design_tokens import _p
from frontdesk_ui import FD_CONTENT_BOX_MARGINS
from i18n import i18n
from ui_helpers import show_info, show_warning


class EnergyEntryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("EnergyEntryPage")  # v7 视觉标记
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel(i18n.t("energy_title"))
        title.setObjectName("EnergyPageTitle")
        root.addWidget(title)

        row = QHBoxLayout()
        self.cmb_meter = QComboBox()
        self.txt_reading = QLineEdit()
        self.txt_reading.setPlaceholderText("当前电表读数 千瓦时")
        self.txt_staff = QLineEdit()
        self.txt_staff.setPlaceholderText("电工/录入人")
        self.txt_note = QLineEdit()
        self.txt_note.setPlaceholderText("备注")
        btn_submit = QPushButton("录入电表")
        btn_submit.setObjectName("SolidPrimaryBtn")
        btn_submit.clicked.connect(self._submit_reading)
        row.addWidget(QLabel("电表："))
        row.addWidget(self.cmb_meter)
        row.addWidget(self.txt_reading)
        row.addWidget(self.txt_staff)
        row.addWidget(self.txt_note)
        row.addWidget(btn_submit)
        root.addLayout(row)

        action = QHBoxLayout()
        btn_start = QPushButton("开始本期对账")
        btn_start.setObjectName("SolidPrimaryBtn")
        btn_start.clicked.connect(self._start_period)
        btn_finish = QPushButton("结束并生成差异")
        btn_finish.setObjectName("SolidPrimaryBtn")
        btn_finish.clicked.connect(self._finish_period)
        action.addWidget(btn_start)
        action.addWidget(btn_finish)
        action.addStretch()
        self.lbl_status = QLabel("")
        action.addWidget(self.lbl_status)
        root.addLayout(action)

        # ── 表格容器 ──
        tbl_box1 = QFrame()
        tbl_box1.setObjectName("ContentBox")
        t1 = QVBoxLayout(tbl_box1)
        t1.setContentsMargins(*FD_CONTENT_BOX_MARGINS)
        self.tbl_readings = QTableWidget(0, 6)
        self.tbl_readings.setHorizontalHeaderLabels(["时间", "电表", "读数", "录入人", "来源", "备注"])
        self.tbl_readings.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_readings.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_readings.setAlternatingRowColors(False)
        t1.addWidget(self.tbl_readings)
        root.addWidget(tbl_box1)

        tbl_box2 = QFrame()
        tbl_box2.setObjectName("ContentBox")
        t2 = QVBoxLayout(tbl_box2)
        t2.setContentsMargins(*FD_CONTENT_BOX_MARGINS)
        self.tbl_periods = QTableWidget(0, 6)
        self.tbl_periods.setHorizontalHeaderLabels(["开始", "结束", "实际", "理论", "差异率", "状态"])
        self.tbl_periods.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_periods.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_periods.setAlternatingRowColors(False)
        t2.addWidget(self.tbl_periods)
        root.addWidget(tbl_box2)

        from ui_surface import fd_apply_content_box, fd_apply_table_palette
        fd_apply_content_box(tbl_box1)
        fd_apply_content_box(tbl_box2)
        fd_apply_table_palette(self.tbl_readings)
        fd_apply_table_palette(self.tbl_periods)

        from ui_surface import fd_connect_theme_refresh
        fd_connect_theme_refresh(self)

    def refresh(self) -> None:
        meters = engine.list_meters()
        cur = self.cmb_meter.currentData()
        self.cmb_meter.clear()
        for m in meters:
            self.cmb_meter.addItem(f"{m['label']} ({m['meter_id']})", m["meter_id"])
        if cur:
            idx = self.cmb_meter.findData(cur)
            if idx >= 0:
                self.cmb_meter.setCurrentIndex(idx)

        self.tbl_readings.setRowCount(0)
        for r, row in enumerate(engine.recent_readings(40)):
            self.tbl_readings.insertRow(r)
            vals = [
                row["created_at"][:16],
                row["label"],
                f"{row['reading_kwh']:.2f}",
                row["recorded_by"],
                row["source"],
                row["note"],
            ]
            for c, val in enumerate(vals):
                self.tbl_readings.setItem(r, c, QTableWidgetItem(str(val)))

        self.tbl_periods.setRowCount(0)
        for r, row in enumerate(engine.list_periods(20)):
            self.tbl_periods.insertRow(r)
            vals = [
                row["started_at"][:16],
                row["finished_at"][:16],
                f"{row['actual_kwh']:.2f}",
                f"{row['theoretical_kwh']:.2f}",
                f"{row['diff_rate'] * 100:.1f}%",
                "异常" if row["is_anomaly"] else row["status"],
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                if c == 5 and row["is_anomaly"]:
                    item.setForeground(QColor(_p('danger')))
                    item.setTextAlignment(Qt.AlignCenter)
                self.tbl_periods.setItem(r, c, item)

        open_pid = engine.open_period_id()
        self.lbl_status.setText(f"本期进行中：{open_pid[:8]}" if open_pid else "当前没有进行中的能耗周期")

    def _submit_reading(self) -> None:
        try:
            reading = float(self.txt_reading.text().strip())
        except ValueError:
            show_warning(self, "能耗对账", "电表读数必须是数字。")
            return
        staff = self.txt_staff.text().strip()
        if not staff:
            show_warning(self, "能耗对账", "请填写录入人。")
            return
        engine.record_meter_reading(
            self.cmb_meter.currentData() or engine.DEFAULT_METER_ID,
            reading,
            staff,
            note=self.txt_note.text().strip(),
        )
        self.txt_reading.clear()
        self.txt_note.clear()
        self.refresh()

    def _start_period(self) -> None:
        pid = engine.start_energy_period(operator_id=self.txt_staff.text().strip() or "SYSTEM", note="UI 触发")
        show_info(self, "能耗对账", f"本期能耗周期已开始：{pid[:8]}")
        self.refresh()

    def _finish_period(self) -> None:
        pid = engine.open_period_id()
        if not pid:
            show_warning(self, "能耗对账", "当前没有进行中的能耗周期。")
            return
        result = engine.finalize_energy_period(pid, operator_id=self.txt_staff.text().strip() or "SYSTEM")
        show_info(
            self,
            "能耗对账",
            f"已生成差异：实际 {result['actual_kwh']:.2f} / 理论 {result['theoretical_kwh']:.2f} 千瓦时",
        )
        self.refresh()

