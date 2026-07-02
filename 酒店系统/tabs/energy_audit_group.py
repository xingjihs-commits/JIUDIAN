"""能耗审计组 — 录入页 + 监控页 + 可视化图表。

v2 升级：
  - 监控页增加柱状图模拟（QProgressBar）+ 异常标记
  - 录入页保持原有功能
  - 数据自动同步
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QProgressBar,
    QScrollArea, QGridLayout, QSizePolicy,
)

from database import db
from event_bus import bus
from i18n import i18n
from design_tokens import _p
from ui_surface import fd_apply_table_palette, fd_refresh_surfaces

logger = logging.getLogger(__name__)


class EnergyAuditGroup(QWidget):
    """能耗管理组 — Tab 包装器，协调录入+监控。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("EnergyAuditGroup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        from energy_entry_page import EnergyEntryPage
        self._entry = EnergyEntryPage()
        tabs.addTab(self._entry, i18n.t("energy_tab_entry"))

        self._monitor = EnergyMonitorPanel()
        tabs.addTab(self._monitor, i18n.t("energy_tab_monitor"))

        tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(tabs)

        from ui_surface import fd_connect_theme_refresh
        fd_connect_theme_refresh(self)

    def _on_tab_changed(self, idx: int):
        if idx == 1:
            self._monitor.refresh()


class EnergyMonitorPanel(QWidget):
    """能耗监控面板 — 柱状图模拟 + 异常标记 + 历史对比。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("EnergyMonitorPanel")
        self._build_ui()
        QTimer.singleShot(0, self.refresh)
        bus.theme_changed.connect(lambda _: fd_refresh_surfaces(self))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel(i18n.t("energy_monitor_title", default="能耗监控"))
        title.setObjectName("EnergyPageTitle")
        root.addWidget(title)

        # ── 概览卡片行 ──
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self._card_labels = {}
        for key, label in (
            ("current", "本期用电"),
            ("avg_daily", "日均"),
            ("trend", "趋势"),
        ):
            card = QFrame()
            card.setObjectName("EnergyDashCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 10, 14, 10)
            cl.setSpacing(4)
            cl.addWidget(QLabel(label))
            val = QLabel("-")
            val.setObjectName("EnergyDashCell")
            val.setStyleSheet(
                f"font-size: 22px; font-weight: 800; color: {_p('primary')};"
                f" background: {_p('surface_alt')}; border-radius: 8px; padding: 8px;"
            )
            self._card_labels[key] = val
            cl.addWidget(val)
            cards.addWidget(card)
        cards.addStretch()
        root.addLayout(cards)

        # ── 柱状图模拟（最近7天）──
        chart_label = QLabel(
            i18n.t("energy_chart_title", default="近7天用电量 (kWh)")
        )
        root.addWidget(chart_label)

        self._chart_container = QFrame()
        self._chart_container.setObjectName("EnergyChartBox")
        self._chart_container.setStyleSheet(
            f"QFrame#EnergyChartBox {{"
            f" background: {_p('surface')};"
            f" border: 1px solid {_p('border')};"
            f" border-radius: 8px;"
            f" padding: 12px;"
            f"}}"
        )
        self._chart_layout = QVBoxLayout(self._chart_container)
        self._chart_layout.setSpacing(6)
        self._bar_widgets = []
        root.addWidget(self._chart_container, 1)

        # ── 异常告警列表 ──
        alert_header = QLabel(
            i18n.t("energy_alerts", default="⚠ 异常告警")
        )
        alert_header.setStyleSheet(
            f"font-weight: 700; color: {_p('danger')};"
        )
        root.addWidget(alert_header)

        self._alert_tbl = QTableWidget(0, 3)
        self._alert_tbl.setObjectName("EnergyAlertTable")
        self._alert_tbl.setMaximumHeight(120)
        self._alert_tbl.setHorizontalHeaderLabels([
            i18n.t("energy_col_date", default="日期"),
            i18n.t("energy_col_meter", default="电表"),
            i18n.t("energy_col_detail", default="异常说明"),
        ])
        alert_hdr = self._alert_tbl.horizontalHeader()
        alert_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        alert_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        alert_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._alert_tbl.setSelectionBehavior(
            QHeaderView.SelectionBehavior.SelectRows
        )
        self._alert_tbl.setEditTriggers(
            QHeaderView.EditTrigger.NoEditTriggers
        )
        self._alert_tbl.setAlternatingRowColors(False)
        fd_apply_table_palette(self._alert_tbl)
        root.addWidget(self._alert_tbl)

        root.addStretch()

    def refresh(self):
        """刷新监控数据。"""
        try:
            import energy_audit_engine as engine
        except Exception:
            self._card_labels["current"].setText("-")
            return

        # 概览卡片
        try:
            summary = engine.get_period_summary() or {}
        except Exception:
            summary = {}
        self._card_labels["current"].setText(
            f"{summary.get('total_kwh', 0):.1f} kWh"
        )
        self._card_labels["avg_daily"].setText(
            f"{summary.get('avg_daily', 0):.1f} kWh"
        )

        trend_val = summary.get("trend_pct")
        if trend_val is not None:
            arrow = "↑" if trend_val > 0 else "↓" if trend_val < 0 else "→"
            self._card_labels["trend"].setText(
                f"{arrow} {abs(trend_val):.1f}%"
            )
        else:
            self._card_labels["trend"].setText("-")

        # 柱状图
        self._build_chart()

        # 异常告警
        try:
            alerts = engine.get_alerts(limit=10) or []
        except Exception:
            alerts = []
        self._alert_tbl.setRowCount(len(alerts))
        for i, alert in enumerate(alerts):
            self._alert_tbl.setItem(
                i, 0, QTableWidgetItem(str(alert.get("date", "-")))
            )
            self._alert_tbl.setItem(
                i, 1, QTableWidgetItem(str(alert.get("meter", "-")))
            )
            self._alert_tbl.setItem(
                i, 2, QTableWidgetItem(str(alert.get("detail", "-")))
            )

    def _build_chart(self):
        """用 QProgressBar 模拟柱状图。"""
        # 清除旧柱子
        for w in self._bar_widgets:
            w.deleteLater()
        self._bar_widgets = []

        # 获取近7天数据
        try:
            import energy_audit_engine as engine
            daily = engine.get_daily_usage(days=7) or []
        except Exception:
            daily = []

        if not daily:
            no_data = QLabel(
                i18n.t("energy_no_data", default="暂无数据，请先录入电表读数")
            )
            no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_data.setStyleSheet(f"color: {_p('text_muted')}; padding: 20px;")
            self._chart_layout.addWidget(no_data)
            self._bar_widgets.append(no_data)
            return

        max_val = max(d.get("kwh", 0) for d in daily) if daily else 1
        primary = _p("primary")
        danger = _p("danger")
        accent = _p("accent")

        for day_data in daily:
            kwh = day_data.get("kwh", 0)
            date_str = str(day_data.get("date", ""))[-5:]  # MM-DD
            pct = int((kwh / max_val * 100)) if max_val > 0 else 0

            row = QHBoxLayout()
            row.setSpacing(8)

            date_lbl = QLabel(date_str)
            date_lbl.setFixedWidth(50)
            date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            date_lbl.setStyleSheet(f"color: {_p('text_muted')}; font-size: 11px; background: transparent;")
            row.addWidget(date_lbl)

            bar = QProgressBar()
            bar.setMinimum(0)
            bar.setMaximum(100)
            bar.setValue(pct)
            bar.setTextVisible(True)
            bar.setFormat(f"{kwh:.1f}")
            bar.setFixedHeight(28)
            bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            # 异常日红色，正常日主题色
            is_anomaly = day_data.get("anomaly", False)
            bar_color = danger if is_anomaly else primary
            bar.setStyleSheet(
                f"QProgressBar {{"
                f" background-color: {_p('surface_alt')};"
                f" border: none; border-radius: 4px;"
                f"}}"
                f"QProgressBar::chunk {{"
                f" background-color: {bar_color};"
                f" border-radius: 4px;"
                f"}}"
            )
            row.addWidget(bar, 1)
            self._chart_layout.addLayout(row)
            self._bar_widgets.extend([date_lbl, bar])
