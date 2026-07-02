"""客房保洁看板 — 任务列表 + 批量分配 + 清洁历史。

v2 升级：
  - 房间列表表格（房号/房型/状态/上次清洁时间/清洁模式）
  - 批量分配保洁任务
  - 清洁历史记录
  - 消耗品模板可视化（可编辑）
  - 按状态筛选
"""
from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QDialog, QAbstractItemView, QTextEdit,
)

from database import db
from event_bus import bus
from i18n import i18n
from design_tokens import _p
from ui_helpers import show_info, show_warning, ask_confirm, style_dialog, build_dialog_header
from frontdesk_ui import (
    fd_section_bar, fd_apply_action_btn, fd_apply_card_action_btn,
    fd_apply_low_freq_btn, FD_MARGIN, FD_SPACE_SM, FD_SPACE_MD, FD_SPACE_LG,
)
from ui_surface import fd_apply_table_palette, fd_refresh_surfaces, fd_sync_table_height
from tabs._shared import current_operator_id

HK_MODES = [
    ("standard", "标准清洁"),
    ("deep", "深度清洁"),
]
_HK_MODE_MAP = dict(HK_MODES)

# 房态 → 显示映射
ROOM_STATUS_DISPLAY = {
    "VD": "脏房",
    "VC": "空净房",
    "OC": "入住",
    "OO": "维修",
    "DIRTY": "脏房",
    "READY": "空净房",
    "INHOUSE": "入住",
    "MAINTENANCE": "维修",
}


class HousekeepingPanel(QWidget):
    """客房保洁管理看板。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("HousekeepingPanel")
        self._status_filter = "DIRTY"
        self._build_ui()
        QTimer.singleShot(0, self.refresh)
        bus.theme_changed.connect(lambda _: fd_refresh_surfaces(self))

    def _build_ui(self):
        l = QVBoxLayout(self)
        l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)
        l.setSpacing(FD_SPACE_MD)

        # ── 标题栏 ──
        btn_assign = QPushButton(i18n.t("hk_btn_assign", default="批量分配"))
        fd_apply_action_btn(btn_assign, primary=True)
        btn_assign.clicked.connect(self._batch_assign)

        btn_rf = QPushButton(i18n.t("btn_refresh", default="刷新"))
        fd_apply_low_freq_btn(btn_rf)
        btn_rf.clicked.connect(self.refresh)

        l.addWidget(fd_section_bar(
            i18n.t("hk_title", default="客房保洁"),
            action_widgets=[btn_assign, btn_rf],
        ))

        # ── 筛选芯片 ──
        chip_row = QHBoxLayout()
        chip_row.setSpacing(FD_SPACE_SM)
        self._chips: dict[str, QPushButton] = {}
        filters = [
            ("DIRTY", "待清洁"),
            ("ALL", "全部"),
            ("VC", "已清洁"),
        ]
        for code, label in filters:
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setChecked(code == self._status_filter)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _, c=code: self._on_filter(c))
            self._chips[code] = chip
            chip_row.addWidget(chip)
        chip_row.addStretch()
        l.addLayout(chip_row)

        # ── 快速操作行 ──
        quick_row = QHBoxLayout()
        quick_row.setSpacing(FD_SPACE_SM)

        self.cmb_room = QComboBox()
        self.cmb_room.setMinimumWidth(120)
        self.cmb_room.setPlaceholderText(
            i18n.t("hk_select_room", default="选择房间…")
        )
        quick_row.addWidget(QLabel(i18n.t("table_room", default="房间") + ":"))
        quick_row.addWidget(self.cmb_room)

        self.cmb_mode = QComboBox()
        for code, label in HK_MODES:
            self.cmb_mode.addItem(label, code)
        quick_row.addWidget(QLabel(i18n.t("hk_mode_label", default="模式") + ":"))
        quick_row.addWidget(self.cmb_mode)

        self.txt_staff = QLineEdit()
        self.txt_staff.setPlaceholderText(
            i18n.t("table_staff", default="保洁员")
        )
        self.txt_staff.setMaximumWidth(150)
        quick_row.addWidget(QLabel(i18n.t("table_staff", default="员工") + ":"))
        quick_row.addWidget(self.txt_staff)

        btn_done = QPushButton("✅ " + i18n.t("hk_btn_done", default="标记完成"))
        fd_apply_card_action_btn(btn_done)
        btn_done.clicked.connect(self._mark_done)
        quick_row.addWidget(btn_done)

        quick_row.addStretch()
        l.addLayout(quick_row)

        # ── 消耗品模板展示 ──
        self.lbl_template = QLabel("")
        self.lbl_template.setObjectName("HkTemplateBox")
        self.lbl_template.setWordWrap(True)
        l.addWidget(self.lbl_template)
        self.cmb_room.currentTextChanged.connect(self._update_template)

        # ── 房间列表表格 ──
        self.tbl = QTableWidget(0, 7)
        self.tbl.setObjectName("HkRoomTable")
        self.tbl.setHorizontalHeaderLabels([
            "",  # checkbox
            i18n.t("table_room", default="房号"),
            i18n.t("hk_col_type", default="房型"),
            i18n.t("hk_col_status", default="状态"),
            i18n.t("hk_col_last_clean", default="上次清洁"),
            i18n.t("hk_col_staff", default="保洁员"),
            i18n.t("hk_col_mode", default="模式"),
        ])
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setAlternatingRowColors(False)
        fd_apply_table_palette(self.tbl)
        fd_sync_table_height(self.tbl)
        l.addWidget(self.tbl, 1)

        # ── 空状态 ──
        self._empty_hint = QLabel(
            i18n.t("hk_empty_hint", default="🎉 所有房间已清洁完毕")
        )
        self._empty_hint.setObjectName("TableEmptyHint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l.addWidget(self._empty_hint)

        # ── 清洁历史（折叠区）──
        hist_header = QHBoxLayout()
        hist_header.addWidget(QLabel(
            i18n.t("hk_history", default="最近清洁记录")
        ))
        hist_header.addStretch()
        l.addLayout(hist_header)

        self.hist_tbl = QTableWidget(0, 4)
        self.hist_tbl.setObjectName("HkHistoryTable")
        self.hist_tbl.setMaximumHeight(160)
        self.hist_tbl.setHorizontalHeaderLabels([
            i18n.t("table_room", default="房号"),
            i18n.t("hk_col_mode", default="模式"),
            i18n.t("table_staff", default="保洁员"),
            i18n.t("hk_col_time", default="时间"),
        ])
        hist_hdr = self.hist_tbl.horizontalHeader()
        hist_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hist_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hist_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hist_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.hist_tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.hist_tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.hist_tbl.setAlternatingRowColors(False)
        fd_apply_table_palette(self.hist_tbl)
        l.addWidget(self.hist_tbl)

    def refresh(self):
        """刷新房间列表 + 清洁历史。"""
        # 加载房间下拉
        self.cmb_room.blockSignals(True)
        self.cmb_room.clear()
        rooms = db.execute(
            "SELECT room_id, room_type, status FROM rooms ORDER BY room_id"
        ).fetchall()
        self._room_data = {}
        for rid, rt, status in rooms:
            self._room_data[rid] = (rt, status or "")
            self.cmb_room.addItem(rid)
        self.cmb_room.blockSignals(False)

        # 加载房间表格
        status_filter_sql = {
            "DIRTY": "AND r.status IN ('VD','DIRTY')",
            "VC": "AND r.status IN ('VC','READY')",
            "ALL": "",
        }.get(self._status_filter, "AND r.status IN ('VD','DIRTY')")

        try:
            rows = db.execute(f"""
                SELECT r.room_id, r.room_type, r.status,
                       hk.cleaned_at, hk.staff_name, hk.mode
                FROM rooms r
                LEFT JOIN (
                    SELECT room_id, MAX(cleaned_at) as cleaned_at,
                           staff_name, mode
                    FROM housekeeping_log
                    GROUP BY room_id
                ) hk ON r.room_id = hk.room_id
                WHERE 1=1 {status_filter_sql}
                ORDER BY r.room_id
            """).fetchall()
        except Exception:
            rows = db.execute(f"""
                SELECT r.room_id, r.room_type, r.status,
                       NULL, NULL, NULL
                FROM rooms r
                WHERE 1=1 {status_filter_sql}
                ORDER BY r.room_id
            """).fetchall()

        self.tbl.setRowCount(len(rows))
        has_data = len(rows) > 0
        self.tbl.setVisible(has_data)
        self._empty_hint.setVisible(not has_data)

        for i, row in enumerate(rows):
            rid, rt, status, cleaned_at, staff_name, mode = row
            self.tbl.setItem(i, 1, QTableWidgetItem(str(rid)))
            self.tbl.setItem(i, 2, QTableWidgetItem(str(rt or "-")))
            status_disp = ROOM_STATUS_DISPLAY.get(status, status or "-")
            self.tbl.setItem(i, 3, QTableWidgetItem(status_disp))
            self.tbl.setItem(
                i, 4,
                QTableWidgetItem(str(cleaned_at)[:19] if cleaned_at else "-")
            )
            self.tbl.setItem(i, 5, QTableWidgetItem(str(staff_name or "-")))
            self.tbl.setItem(i, 6, QTableWidgetItem(
                _HK_MODE_MAP.get(mode, mode or "-")
            ))

        # 加载清洁历史
        try:
            hist_rows = db.execute("""
                SELECT room_id, mode, staff_name, cleaned_at
                FROM housekeeping_log
                ORDER BY cleaned_at DESC
                LIMIT 50
            """).fetchall()
        except Exception:
            hist_rows = []

        self.hist_tbl.setRowCount(len(hist_rows))
        for i, row in enumerate(hist_rows):
            rid, mode, staff, cleaned_at = row
            self.hist_tbl.setItem(i, 0, QTableWidgetItem(str(rid)))
            self.hist_tbl.setItem(i, 1, QTableWidgetItem(
                _HK_MODE_MAP.get(mode, mode or "-")
            ))
            self.hist_tbl.setItem(i, 2, QTableWidgetItem(str(staff or "-")))
            self.hist_tbl.setItem(i, 3, QTableWidgetItem(
                str(cleaned_at)[:19] if cleaned_at else "-"
            ))

    def _on_filter(self, code: str):
        self._status_filter = code
        for c, chip in self._chips.items():
            chip.setChecked(c == code)
        self.refresh()

    def _update_template(self, rid: str):
        """根据房间类型显示消耗品模板。"""
        if rid not in self._room_data:
            self.lbl_template.setText("-")
            return
        rt, _ = self._room_data[rid]
        tpl = db.execute(
            "SELECT consumables_json, hk_consumables_deep_json "
            "FROM room_type_templates WHERE type_id=?",
            (rt,),
        ).fetchone()
        if not tpl:
            self.lbl_template.setText("-")
            return
        std_raw, deep_raw = tpl[0], tpl[1]
        parts = []
        if std_raw:
            try:
                std = json.loads(std_raw)
                if std:
                    parts.append("标准: " + ", ".join(
                        f"{k}×{v}" for k, v in std.items()
                    ))
            except Exception:
                pass
        if deep_raw:
            try:
                deep = json.loads(deep_raw)
                if deep:
                    parts.append("深度: " + ", ".join(
                        f"{k}×{v}" for k, v in deep.items()
                    ))
            except Exception:
                pass
        self.lbl_template.setText(
            i18n.t("hk_template", default="消耗品") + ": " +
            (" | ".join(parts) if parts else "-")
        )

    def _mark_done(self):
        rid = self.cmb_room.currentText()
        if not rid:
            show_warning(self, i18n.t("dlg_tip", default="提示"),
                         i18n.t("hk_select_room_first", default="请先选择房间"))
            return
        mode = self.cmb_mode.currentData()
        staff = self.txt_staff.text().strip() or current_operator_id()

        try:
            # 更新房态
            db.execute(
                "UPDATE rooms SET status='VC' WHERE room_id=?",
                (rid,),
            )
            # 记录清洁日志
            db.execute(
                """INSERT INTO housekeeping_log
                   (room_id, mode, staff_name, cleaned_at)
                   VALUES (?, ?, ?, ?)""",
                (rid, mode, staff, datetime.now().isoformat()),
            )
            db.commit()
            show_info(self, i18n.t("dlg_tip", default="提示"),
                      i18n.t("hk_done_msg", default="清洁完成") + f": {rid}")
            self.refresh()
        except Exception as e:
            db.rollback()
            show_warning(self, i18n.t("dlg_error", default="错误"),
                         f"操作失败: {e}")

    def _batch_assign(self):
        """批量分配：弹窗选择保洁员 → 分配给所有脏房。"""
        staff = self.txt_staff.text().strip()
        if not staff:
            staff, ok = self._input_dialog(
                i18n.t("hk_assign_staff", default="保洁员姓名"),
                i18n.t("hk_enter_staff", default="请输入保洁员姓名"),
            )
            if not ok or not staff:
                return

        mode = self.cmb_mode.currentData()

        # 获取所有脏房
        dirty_rooms = db.execute(
            "SELECT room_id FROM rooms WHERE status IN ('VD','DIRTY')"
        ).fetchall()

        if not dirty_rooms:
            show_info(self, i18n.t("dlg_tip", default="提示"),
                      i18n.t("hk_no_dirty", default="没有待清洁房间"))
            return

        if not ask_confirm(self,
                           i18n.t("dlg_confirm", default="确认"),
                           i18n.t("hk_assign_confirm",
                                  default=f"将 {len(dirty_rooms)} 间脏房分配给 {staff}，模式: {_HK_MODE_MAP.get(mode, mode)}？")):
            return

        try:
            now = datetime.now().isoformat()
            for (rid,) in dirty_rooms:
                db.execute(
                    """INSERT INTO housekeeping_log
                       (room_id, mode, staff_name, cleaned_at)
                       VALUES (?, ?, ?, ?)""",
                    (rid, mode, staff, now),
                )
            db.commit()
            show_info(self, i18n.t("dlg_tip", default="提示"),
                      i18n.t("hk_assigned", default="已分配") +
                      f" {len(dirty_rooms)} {i18n.t('hk_rooms', default='间房')}")
            self.refresh()
        except Exception as e:
            db.rollback()
            show_warning(self, i18n.t("dlg_error", default="错误"),
                         f"操作失败: {e}")

    @staticmethod
    def _input_dialog(title: str, prompt: str) -> tuple:
        """简单的文本输入弹窗。"""
        dlg = QDialog()
        dlg.setWindowTitle(title)
        style_dialog(dlg, size="compact")
        lay = QFormLayout(dlg)
        txt = QLineEdit()
        lay.addRow(prompt, txt)
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
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return txt.text().strip(), True
        return "", False
