"""客人列表 Tab — 在住客人档案与快速检索。

v2 升级（2026-06-24）：
  • 顶部 KPI 条：在住 / 今日入住 / 今日退房
  • 搜索框：支持房号 / 姓名 / 手机号模糊匹配
  • 空状态：无在住客人时显示引导提示
  • 错误态：DB 异常时显示错误 banner，不再静默吞错
"""
from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QSizePolicy,
)

from database import db
from i18n import i18n
from design_tokens import _p
from frontdesk_ui import fd_section_bar, fd_apply_low_freq_btn, FD_MARGIN, FD_SPACE_MD
from ui_surface import fd_apply_table_palette, fd_refresh_surfaces
from event_bus import bus

logger = logging.getLogger(__name__)


class GuestListTab(QWidget):
    """在住客人列表 + KPI 概览 + 搜索过滤。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("GuestListTab")
        self._search_text = ""

        l = QVBoxLayout(self)
        l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)
        l.setSpacing(FD_SPACE_MD)

        # ── 工具栏：刷新按钮 ──
        btn_rf = QPushButton(i18n.t("btn_refresh"))
        fd_apply_low_freq_btn(btn_rf)
        btn_rf.clicked.connect(self._rf)

        l.addWidget(fd_section_bar(i18n.t("tab_roster"), action_widgets=[btn_rf]))

        # ── KPI 条：在住 / 今日入住 / 今日退房 ──
        self._kpi_row = self._build_kpi_row()
        l.addWidget(self._kpi_row)

        # ── 搜索框 ──
        search_row = QHBoxLayout()
        search_row.setSpacing(FD_SPACE_MD)
        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("CardSearchInput")
        self.txt_search.setPlaceholderText(
            i18n.t("guest_search_placeholder", default="搜索房号 / 姓名 / 手机号")
        )
        self.txt_search.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.txt_search, 1)
        l.addLayout(search_row)

        # ── 错误提示 banner（默认隐藏）──
        self.lbl_error = QLabel("")
        self.lbl_error.setObjectName("FdAlertBanner")
        self.lbl_error.setStyleSheet(
            f"color:{_p('danger')}; background:{_p('surface_alt')}; "
            f"border:1px solid {_p('danger')}; border-radius:6px; padding:8px 12px;"
        )
        self.lbl_error.setVisible(False)
        l.addWidget(self.lbl_error)

        # ── SolidCard 包裹的客人表格 ──
        content_box = QFrame()
        content_box.setObjectName("SolidCard")
        content_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cl = QVBoxLayout(content_box)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.setSpacing(FD_SPACE_MD)

        self.tbl = QTableWidget(0, 6)
        cols = ["table_room", "table_guest", "table_id", "label_phone", "table_time", "table_status"]
        self.tbl.setHorizontalHeaderLabels([i18n.t(c) for c in cols])
        hdr = self.tbl.horizontalHeader()
        hdr.setMinimumSectionSize(70)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.tbl.setAlternatingRowColors(False)
        self.tbl.setSortingEnabled(True)
        cl.addWidget(self.tbl)
        fd_apply_table_palette(self.tbl)

        # ── 空状态提示（默认隐藏，_rf 时按数据量切换）──
        self.lbl_empty = QLabel(
            i18n.t("guest_empty_title", default="暂无在住客人")
        )
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setStyleSheet(
            f"color:{_p('text_muted')}; font-size:14px; padding:32px;"
        )
        self.lbl_empty.setVisible(False)
        cl.addWidget(self.lbl_empty)

        l.addWidget(content_box)
        self._rf()
        bus.theme_changed.connect(lambda _: fd_refresh_surfaces(self))

    def _build_kpi_row(self) -> QFrame:
        """构建 KPI 条：在住 / 今日入住 / 今日退房。"""
        row = QFrame()
        row.setObjectName("KpiStrip")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(FD_SPACE_MD)

        self._kpi_inhouse = self._make_kpi_cell(
            i18n.t("guest_kpi_inhouse", default="在住客人"), "0"
        )
        self._kpi_today_in = self._make_kpi_cell(
            i18n.t("guest_kpi_today_in", default="今日入住"), "0"
        )
        self._kpi_today_out = self._make_kpi_cell(
            i18n.t("guest_kpi_today_out", default="今日退房"), "0"
        )
        lay.addWidget(self._kpi_inhouse)
        lay.addWidget(self._kpi_today_in)
        lay.addWidget(self._kpi_today_out)
        lay.addStretch()
        return row

    def _make_kpi_cell(self, label: str, value: str) -> QFrame:
        cell = QFrame()
        cell.setObjectName("FinanceStatCell")
        cell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        cl = QVBoxLayout(cell)
        cl.setContentsMargins(12, 8, 12, 8)
        cl.setSpacing(2)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{_p('text_muted')}; font-size:11px;")
        val = QLabel(value)
        val.setObjectName("KpiValue")
        val.setStyleSheet(
            f"color:{_p('text')}; font-size:22px; font-weight:700;"
        )
        cl.addWidget(lbl)
        cl.addWidget(val)
        return cell

    def _on_search_changed(self, text: str):
        self._search_text = text.strip().lower()
        self._apply_filter()

    def _apply_filter(self):
        """按搜索词过滤表格行。"""
        text = self._search_text
        for row in range(self.tbl.rowCount()):
            if not text:
                self.tbl.setRowHidden(row, False)
                continue
            visible = False
            for col in range(self.tbl.columnCount()):
                item = self.tbl.item(row, col)
                if item and text in item.text().lower():
                    visible = True
                    break
            self.tbl.setRowHidden(row, not visible)

    def _rf(self):
        """刷新客人列表 + KPI。"""
        self.tbl.setRowCount(0)
        self.lbl_error.setVisible(False)

        try:
            data = db.execute(
                "SELECT room_id, name, id_card, phone, checkin_time, status "
                "FROM guests ORDER BY id DESC LIMIT 100"
            ).fetchall()
        except Exception as exc:
            logger.warning("客人列表加载失败: %s", exc)
            self.lbl_error.setText(
                i18n.t("guest_load_fail", default="客人列表加载失败：{err}").format(err=exc)
            )
            self.lbl_error.setVisible(True)
            self._update_kpi(0, 0, 0)
            return

        for i, r in enumerate(data):
            self.tbl.insertRow(i)
            for j, v in enumerate(r):
                self.tbl.setItem(i, j, QTableWidgetItem(str(v)))

        # 空状态切换
        has_data = len(data) > 0
        self.lbl_empty.setVisible(not has_data)
        self.tbl.setVisible(has_data)

        # 更新 KPI
        self._refresh_kpi()
        self._apply_filter()

    def _refresh_kpi(self):
        """统计在住 / 今日入住 / 今日退房。"""
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            inhouse = db.execute(
                "SELECT COUNT(*) FROM guests WHERE status='INHOUSE'"
            ).fetchone()[0]
        except Exception:
            inhouse = 0
        try:
            today_in = db.execute(
                "SELECT COUNT(*) FROM guests WHERE date(checkin_time)=?", (today,)
            ).fetchone()[0]
        except Exception:
            today_in = 0
        try:
            today_out = db.execute(
                "SELECT COUNT(*) FROM guests WHERE status='CHECKOUT' "
                "AND date(checkout_time)=?", (today,)
            ).fetchone()[0]
        except Exception:
            today_out = 0
        self._update_kpi(inhouse, today_in, today_out)

    def _update_kpi(self, inhouse: int, today_in: int, today_out: int):
        """更新 KPI 单元格数值。"""
        for cell, value in (
            (self._kpi_inhouse, inhouse),
            (self._kpi_today_in, today_in),
            (self._kpi_today_out, today_out),
        ):
            val_label = cell.findChild(QLabel, "KpiValue")
            if val_label:
                val_label.setText(str(value))
