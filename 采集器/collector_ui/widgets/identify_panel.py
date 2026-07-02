"""
identify_panel.py — 识别门锁系统面板

包含：安装目录输入 + 扫描按钮 + 身份摘要 + 桥接状态 + 厂家任务拉取。
从 CollectorWizard 中抽取。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QListWidget, QSizePolicy,
)

from ..constants import PALETTE


class IdentifyPanel(QGroupBox):
    """识别门锁系统面板。

    Signals:
        scan_requested(install_dir: str) — 用户点「开始扫描」
        browse_requested()               — 用户点「浏览」
        task_fetch_requested()           — 用户点「拉取厂家任务」
        task_selected(task: dict)        — 用户选中厂家任务
    """

    scan_requested   = Signal(str)
    browse_requested = Signal()
    task_fetch_requested = Signal()
    task_selected    = Signal(dict)

    def __init__(self, parent=None):
        super().__init__("① 识别门锁系统", parent)
        self.setObjectName("FdGhost")
        self._fetched_tasks: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        il = QVBoxLayout(self)
        il.setSpacing(8)

        # 说明
        self._desc = QLabel(
            "选择原厂门锁软件的安装目录，工具会自动识别配置、DLL 和前台程序。")
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet("color:%s; font-size:13px;" % PALETTE["text"])
        il.addWidget(self._desc)

        # 路径输入行
        path_row = QHBoxLayout()
        self._path_input = QLineEdit()
        self._path_input.setPlaceholderText("例如 D:\\智能门锁管理系统")
        path_row.addWidget(self._path_input, 1)
        il.addLayout(path_row)

        # 操作按钮行
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self._scan_btn = QPushButton("开始扫描")
        self._scan_btn.setObjectName("SolidPrimaryBtn")
        self._scan_btn.setMinimumHeight(48)
        self._scan_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._scan_btn.setStyleSheet(
            "QPushButton { background-color: %s; color: white; border: none;"
            " border-radius: 8px; font-size: 15px; font-weight: 700; padding: 10px 20px; }"
            "QPushButton:hover { background-color: %s; }"
            "QPushButton:disabled { background-color: %s; color: white; }" %
            (PALETTE["primary"], PALETTE["primary_hover"], PALETTE["primary_light"]))
        self._scan_btn.clicked.connect(
            lambda: self.scan_requested.emit(self._path_input.text().strip().strip('"')))
        action_row.addWidget(self._scan_btn, 3)
        browse_btn = QPushButton("浏览")
        browse_btn.setObjectName("FdGhostBtn")
        browse_btn.setMinimumWidth(96)
        browse_btn.setMinimumHeight(48)
        browse_btn.clicked.connect(self.browse_requested.emit)
        action_row.addWidget(browse_btn, 1)
        il.addLayout(action_row)

        # 身份摘要
        self._identity_summary = QLabel("")
        self._identity_summary.setWordWrap(True)
        self._identity_summary.setStyleSheet(
            "color:%s; font-size:12px; background:%s; "
            "border-radius:6px; padding:8px;" %
            (PALETTE["muted"], PALETTE["bg_alt"]))
        il.addWidget(self._identity_summary)

        # 桥接状态
        self._bridge_status = QLabel("")
        self._bridge_status.setWordWrap(True)
        self._bridge_status.setStyleSheet(
            "color:%s; font-size:12px; background:%s; "
            "border-radius:6px; padding:8px;" %
            (PALETTE["muted"], PALETTE["bg_alt"]))
        il.addWidget(self._bridge_status)

        # ── 厂家任务拉取 ──
        self._task_gb = QGroupBox("⬇ 厂家云端任务（可选 · 现场先看要采哪几家）")
        self._task_gb.setObjectName("FdGhost")
        tl = QVBoxLayout(self._task_gb)
        tl.setSpacing(8)

        task_row = QHBoxLayout()
        self._task_fetch_btn = QPushButton("拉取厂家任务")
        self._task_fetch_btn.setObjectName("FdGhostBtn")
        self._task_fetch_btn.setMinimumHeight(36)
        self._task_fetch_btn.clicked.connect(self.task_fetch_requested.emit)
        task_row.addWidget(self._task_fetch_btn)
        self._task_refresh_btn = QPushButton("刷新")
        self._task_refresh_btn.setObjectName("FdGhostBtn")
        self._task_refresh_btn.setMinimumHeight(36)
        self._task_refresh_btn.clicked.connect(self.task_fetch_requested.emit)
        task_row.addWidget(self._task_refresh_btn)
        task_row.addStretch()
        tl.addLayout(task_row)

        self._task_list = QListWidget()
        self._task_list.setMinimumHeight(80)
        self._task_list.setMaximumHeight(160)
        self._task_list.itemSelectionChanged.connect(self._on_task_selection)
        tl.addWidget(self._task_list)

        self._task_detail = QLabel("")
        self._task_detail.setWordWrap(True)
        self._task_detail.setStyleSheet(
            "color:%s; font-size:12px; background:%s; "
            "border-radius:6px; padding:8px;" %
            (PALETTE["muted"], PALETTE["bg_alt"]))
        tl.addWidget(self._task_detail)
        il.addWidget(self._task_gb)

    # ── 外部接口 ─────────────────────────────────────────

    def set_install_dir(self, path: str):
        self._path_input.setText(path)

    def set_scan_loading(self):
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("扫描中...")

    def set_scan_done(self):
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("重新扫描")

    def set_identity_summary(self, text: str, ok: bool):
        color = PALETTE["green"] if ok else PALETTE["danger"]
        bg    = PALETTE["green_bg"] if ok else PALETTE["danger_bg"]
        border= PALETTE["green_border"] if ok else PALETTE["danger_border"]
        self._identity_summary.setStyleSheet(
            f"color:{color}; font-size:12px; background:{bg}; "
            f"border:1px solid {border}; border-radius:6px; padding:10px;")
        self._identity_summary.setText(text)

    def set_bridge_status(self, text: str, level: str = "ok"):
        """level: ok / warn / error"""
        style_map = {
            "ok": (PALETTE["green"], PALETTE["green_bg"], PALETTE["green_border"]),
            "warn": (PALETTE["warn"], PALETTE["warn_bg"], PALETTE["warn_border"]),
            "error": (PALETTE["danger"], PALETTE["danger_bg"], PALETTE["danger_border"]),
        }
        color, bg, border = style_map.get(level, style_map["warn"])
        weight = "font-weight:600;" if level == "ok" else ""
        self._bridge_status.setStyleSheet(
            f"color:{color}; font-size:12px; background:{bg}; "
            f"border:1px solid {border}; border-radius:6px; padding:10px; {weight}")
        self._bridge_status.setText(text)

    def set_completed(self):
        self.setTitle("① 识别门锁系统 — 已完成")

    # ── 厂家任务 ─────────────────────────────────────────

    def set_tasks(self, tasks: list[dict]):
        self._fetched_tasks = tasks
        self._task_list.clear()
        self._task_fetch_btn.setEnabled(True)
        self._task_fetch_btn.setText("拉取厂家任务")
        if not tasks:
            self._task_detail.setText("（无待办任务，或云端未配置 / 不可达）")
            return
        for t in tasks:
            prio = t.get("priority", "normal")
            icon = {"high": "🔴", "normal": "🟡", "low": "⚪"}.get(prio, "🟡")
            hotel_name = t.get("hotel_name", "?")
            brand = t.get("brand_hint", "?")
            due = (t.get("due_at") or "")[:16].replace("T", " ")
            self._task_list.addItem(f"{icon} {hotel_name} · {brand} · 截止 {due}")
        self._task_list.setCurrentRow(0)

    def set_task_fetch_error(self, msg: str):
        self._task_fetch_btn.setEnabled(True)
        self._task_fetch_btn.setText("拉取厂家任务")
        self._task_detail.setText(f"⚠ 拉取失败: {msg}")

    def set_task_fetch_loading(self):
        self._task_fetch_btn.setEnabled(False)
        self._task_fetch_btn.setText("拉取中...")
        self._task_list.clear()
        self._task_detail.setText("正在拉取厂家任务列表...")

    def _on_task_selection(self):
        idx = self._task_list.currentRow()
        if idx < 0 or idx >= len(self._fetched_tasks):
            return
        task = self._fetched_tasks[idx]
        lines = []
        for key, label in [
            ("hotel_name", "酒店"), ("brand_hint", "门锁品牌提示"),
            ("mode_hint", "发卡方式提示"), ("dll_requirements", "DLL 要求"),
            ("contact_name", "现场联系人"), ("note", "备注"),
            ("task_id", "任务号"),
        ]:
            val = task.get(key)
            if val:
                if isinstance(val, list):
                    val = ", ".join(val)
                if key == "contact_name" and task.get("contact_phone"):
                    val += " " + task["contact_phone"]
                lines.append(f"{label}: {val}")
        self._task_detail.setText("\n".join(lines))
        self.task_selected.emit(task)

    @property
    def scan_btn(self) -> QPushButton:
        return self._scan_btn

    @property
    def path_input(self) -> QLineEdit:
        return self._path_input
