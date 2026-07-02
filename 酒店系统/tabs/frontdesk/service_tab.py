"""服务/送物页面 — 前台客房服务请求管理。

功能：
  - 服务请求列表（房号/类型/状态/时间/备注）
  - 新增服务请求（送水/加床/毛巾/维修/其他）
  - 标记已完成/取消
  - 按状态筛选（待处理/处理中/已完成/已取消）
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QComboBox, QDialog, QFormLayout, QTextEdit, QFrame,
    QAbstractItemView, QMessageBox,
)

from database import db
from event_bus import bus
from i18n import i18n
from design_tokens import _p
from ui_helpers import show_info, show_warning, ask_confirm, style_dialog, build_dialog_header
from frontdesk_ui import (
    fd_section_bar, fd_apply_action_btn, fd_apply_card_action_btn,
    fd_apply_low_freq_btn, FD_MARGIN, FD_SPACE_SM, FD_SPACE_MD,
)
from ui_surface import fd_apply_table_palette, fd_refresh_surfaces, fd_sync_table_height
from tabs._shared import current_operator_id

SERVICE_TYPES = [
    ("water", "送水"),
    ("extra_bed", "加床"),
    ("towel", "毛巾/浴巾"),
    ("repair", "维修"),
    ("clean", "清洁"),
    ("other", "其他"),
]
_SERVICE_TYPE_MAP = dict(SERVICE_TYPES)

SERVICE_STATUSES = [
    ("PENDING", "待处理"),
    ("IN_PROGRESS", "处理中"),
    ("DONE", "已完成"),
    ("CANCELLED", "已取消"),
]


class ServiceRequestPanel(QWidget):
    """客房服务请求管理面板。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("ServiceRequestPanel")
        self._status_filter = "PENDING"
        self._build_ui()
        QTimer.singleShot(0, self.refresh)
        bus.theme_changed.connect(lambda _: fd_refresh_surfaces(self))

    def _build_ui(self):
        l = QVBoxLayout(self)
        l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)
        l.setSpacing(FD_SPACE_MD)

        # ── 标题栏 + 操作 ──
        btn_new = QPushButton(i18n.t("service_btn_new", default="+ 新建请求"))
        fd_apply_action_btn(btn_new, primary=True)
        btn_new.clicked.connect(self._new_request)

        btn_rf = QPushButton(i18n.t("btn_refresh", default="刷新"))
        fd_apply_low_freq_btn(btn_rf)
        btn_rf.clicked.connect(self.refresh)

        l.addWidget(fd_section_bar(
            i18n.t("service_title", default="客房服务"),
            action_widgets=[btn_new, btn_rf],
        ))

        # ── 状态筛选芯片 ──
        chip_row = QHBoxLayout()
        chip_row.setSpacing(FD_SPACE_SM)
        self._chips: dict[str, QPushButton] = {}
        for code, label in SERVICE_STATUSES:
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setChecked(code == self._status_filter)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _, c=code: self._on_filter(c))
            self._chips[code] = chip
            chip_row.addWidget(chip)
        chip_row.addStretch()
        l.addLayout(chip_row)

        # ── 表格 ──
        self.tbl = QTableWidget(0, 6)
        self.tbl.setObjectName("ServiceRequestTable")
        self.tbl.setHorizontalHeaderLabels([
            i18n.t("col_room", default="房号"),
            i18n.t("service_col_type", default="类型"),
            i18n.t("service_col_status", default="状态"),
            i18n.t("service_col_time", default="时间"),
            i18n.t("service_col_staff", default="处理人"),
            i18n.t("col_actions", default="操作"),
        ])
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setAlternatingRowColors(False)
        fd_apply_table_palette(self.tbl)
        fd_sync_table_height(self.tbl)
        l.addWidget(self.tbl, 1)

        # ── 空状态 ──
        self._empty_hint = QLabel(
            i18n.t("service_empty_hint", default="暂无服务请求，点击「+ 新建请求」发起")
        )
        self._empty_hint.setObjectName("TableEmptyHint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l.addWidget(self._empty_hint)

    def refresh(self):
        rows = db.execute("""
            SELECT id, room_id, service_type, status, created_at, handler, notes
            FROM service_requests
            WHERE (? = 'ALL' OR status = ?)
            ORDER BY CASE status WHEN 'PENDING' THEN 0 WHEN 'IN_PROGRESS' THEN 1 ELSE 2 END,
                     created_at DESC
        """, (self._status_filter, self._status_filter)).fetchall()

        self.tbl.setRowCount(len(rows))
        has_data = len(rows) > 0
        self.tbl.setVisible(has_data)
        self._empty_hint.setVisible(not has_data)

        for i, row in enumerate(rows):
            sid, room_id, svc_type, status, created_at, handler, notes = row
            self.tbl.setItem(i, 0, QTableWidgetItem(str(room_id or "-")))
            self.tbl.setItem(i, 1, QTableWidgetItem(
                _SERVICE_TYPE_MAP.get(svc_type, svc_type or "-")
            ))
            status_text = dict(SERVICE_STATUSES).get(status, status or "-")
            self.tbl.setItem(i, 2, QTableWidgetItem(status_text))
            self.tbl.setItem(
                i, 3,
                QTableWidgetItem(str(created_at)[:19] if created_at else "-")
            )
            self.tbl.setItem(i, 4, QTableWidgetItem(str(handler or "-")))

            # 操作按钮 — 仅对 PENDING/IN_PROGRESS 显示
            btn_wrap = QWidget()
            btn_lay = QHBoxLayout(btn_wrap)
            btn_lay.setContentsMargins(2, 2, 2, 2)
            btn_lay.setSpacing(4)

            if status in ("PENDING", "IN_PROGRESS"):
                btn_done = QPushButton(
                    i18n.t("btn_done", default="完成")
                )
                fd_apply_card_action_btn(btn_done)
                btn_done.setFixedHeight(28)
                btn_done.clicked.connect(lambda _, sid=sid: self._mark_done(sid))
                btn_lay.addWidget(btn_done)

                btn_cancel = QPushButton(
                    i18n.t("btn_cancel", default="取消")
                )
                fd_apply_low_freq_btn(btn_cancel)
                btn_cancel.setFixedHeight(28)
                btn_cancel.clicked.connect(lambda _, sid=sid: self._cancel(sid))
                btn_lay.addWidget(btn_cancel)

            self.tbl.setCellWidget(i, 5, btn_wrap)

    def _on_filter(self, code: str):
        self._status_filter = code
        for c, chip in self._chips.items():
            chip.setChecked(c == code)
        self.refresh()

    def _new_request(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("service_new_title", default="新建服务请求"))
        style_dialog(dlg, size="compact")
        build_dialog_header(dlg, i18n.t("service_new_title", default="新建服务请求"))

        lay = QFormLayout(dlg)
        cmb_room = QComboBox()
        rooms = db.execute(
            "SELECT room_id FROM rooms ORDER BY room_id"
        ).fetchall()
        for (rid,) in rooms:
            cmb_room.addItem(rid)
        lay.addRow(i18n.t("table_room", default="房间"), cmb_room)

        cmb_type = QComboBox()
        for code, label in SERVICE_TYPES:
            cmb_type.addItem(label, code)
        lay.addRow(i18n.t("service_col_type", default="类型"), cmb_type)

        txt_notes = QTextEdit()
        txt_notes.setMaximumHeight(100)
        txt_notes.setPlaceholderText(
            i18n.t("service_notes_ph", default="备注说明…")
        )
        lay.addRow(i18n.t("col_notes", default="备注"), txt_notes)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton(i18n.t("btn_confirm", default="确认"))
        fd_apply_action_btn(btn_ok, primary=True)
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel = QPushButton(i18n.t("btn_cancel", default="取消"))
        fd_apply_low_freq_btn(btn_cancel)
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        lay.addRow(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        room_id = cmb_room.currentText()
        svc_type = cmb_type.currentData()
        notes = txt_notes.toPlainText().strip()
        operator = current_operator_id()

        try:
            db.execute(
                """INSERT INTO service_requests
                   (room_id, service_type, status, notes, created_by, created_at)
                   VALUES (?, ?, 'PENDING', ?, ?, ?)""",
                (room_id, svc_type, notes, operator, datetime.now().isoformat()),
            )
            db.commit()
            show_info(self, i18n.t("dlg_tip", default="提示"),
                      i18n.t("service_created", default="服务请求已创建"))
            self.refresh()
        except Exception as e:
            db.rollback()
            show_warning(self, i18n.t("dlg_error", default="错误"),
                         i18n.t("service_create_failed", default="创建失败") + f": {e}")

    def _mark_done(self, sid: int):
        if not ask_confirm(self,
                           i18n.t("dlg_confirm", default="确认"),
                           i18n.t("service_done_confirm", default="标记为已完成？")):
            return
        try:
            db.execute(
                "UPDATE service_requests SET status='DONE', handler=?, done_at=? WHERE id=?",
                (current_operator_id(), datetime.now().isoformat(), sid),
            )
            db.commit()
            self.refresh()
        except Exception as e:
            db.rollback()
            show_warning(self, "错误", f"操作失败: {e}")

    def _cancel(self, sid: int):
        if not ask_confirm(self,
                           i18n.t("dlg_confirm", default="确认"),
                           i18n.t("service_cancel_confirm", default="取消此服务请求？")):
            return
        try:
            db.execute(
                "UPDATE service_requests SET status='CANCELLED' WHERE id=?",
                (sid,),
            )
            db.commit()
            self.refresh()
        except Exception as e:
            db.rollback()
            show_warning(self, "错误", f"操作失败: {e}")
