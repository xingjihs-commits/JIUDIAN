"""开门历史对话框 — CardOpenHistoryDialog"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView,
)
from database import db
from i18n import i18n
from ui_helpers import style_dialog
from ui_surface import fd_apply_table_palette


class CardOpenHistoryDialog(QDialog):
    """某张卡的开门次数与房间明细。"""

    def __init__(self, parent, card_id: str, card_label: str = ""):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("card_open_history_title").format(card=card_id))
        style_dialog(self, size="large")
        lay = QVBoxLayout(self)
        stats = db.get_card_open_stats(card_id)
        summary = i18n.t("card_open_history_summary").format(
            total=stats["total"],
            rooms=stats["room_count"],
            last=stats["last_at"] or "—",
            label=card_label or card_id,
        )
        lay.addWidget(QLabel(summary))
        lay.addWidget(QLabel(i18n.t("card_open_by_room"), styleSheet="font-weight:600; margin-top:6px;"))
        tbl_room = QTableWidget()
        tbl_room.setColumnCount(3)
        tbl_room.setHorizontalHeaderLabels(
            [i18n.t("table_room"), i18n.t("card_open_count_col"), i18n.t("card_open_last_col")]
        )
        tbl_room.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl_room.setRowCount(len(stats["by_room"]))
        for i, row in enumerate(stats["by_room"]):
            tbl_room.setItem(i, 0, QTableWidgetItem(row["room_id"]))
            tbl_room.setItem(i, 1, QTableWidgetItem(str(row["count"])))
            tbl_room.setItem(i, 2, QTableWidgetItem(row["last_at"] or "—"))
        tbl_room.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl_room.setMaximumHeight(160)
        tbl_room.setAlternatingRowColors(False)
        fd_apply_table_palette(tbl_room)
        lay.addWidget(tbl_room)
        lay.addWidget(QLabel(i18n.t("card_open_event_list"), styleSheet="font-weight:600; margin-top:8px;"))
        events = db.list_card_open_events(card_id, 500)
        tbl = QTableWidget()
        tbl.setColumnCount(5)
        tbl.setHorizontalHeaderLabels(
            [i18n.t("table_time"), i18n.t("table_room"), i18n.t("card_open_source"),
             i18n.t("table_operator"), i18n.t("table_note")]
        )
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl.setRowCount(len(events))
        src_map = {
            "read_swipe": i18n.t("card_src_read_swipe"),
            "master_open": i18n.t("card_src_master_open"),
            "lock_open": i18n.t("card_src_lock_open"),
            "door_open": i18n.t("card_src_door_open"),
        }
        for i, ev in enumerate(events):
            created, rid, source, op, note = ev
            tbl.setItem(i, 0, QTableWidgetItem(str(created or "")))
            tbl.setItem(i, 1, QTableWidgetItem(str(rid or "")))
            tbl.setItem(i, 2, QTableWidgetItem(src_map.get(str(source), str(source))))
            tbl.setItem(i, 3, QTableWidgetItem(str(op or "")))
            tbl.setItem(i, 4, QTableWidgetItem(str(note or "")))
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setAlternatingRowColors(False)
        fd_apply_table_palette(tbl)
        lay.addWidget(tbl)
        btn_close = QPushButton(i18n.t("btn_bill_close"))
        btn_close.setObjectName("FdGhostBtn")
        btn_close.clicked.connect(self.accept)
        lay.addWidget(btn_close)
