"""借物追踪 — 充电宝/雨伞/转换插头，挂房间，退房检查，未还不退押金。"""
import logging

from PySide6.QtWidgets import QDialog, QVBoxLayout, QComboBox, QSpinBox, QPushButton, QHBoxLayout, QLabel
from PySide6.QtCore import Qt

from database import db
from i18n import i18n
from ui_helpers import show_warning, show_info, style_dialog, build_dialog_header

logger = logging.getLogger(__name__)

BORROW_ITEM_TYPES = [
    ("powerbank", "borrow_pb"),
    ("umbrella", "borrow_umb"),
    ("adapter", "borrow_adpt"),
    ("other", "borrow_other"),
]


def show_borrow_dialog(parent, room_id: str) -> bool:
    dlg = QDialog(parent)
    dlg.setWindowTitle(i18n.t("title_borrow_dialog"))
    style_dialog(dlg, size="compact")
    lv = QVBoxLayout(dlg)
    lv.setContentsMargins(16, 16, 16, 16)
    lv.setSpacing(12)
    lv.addWidget(build_dialog_header(i18n.t("title_borrow_dialog"), i18n.t("borrow_dialog_sub", default="房间: {}").format(room_id)))

    cmb_type = QComboBox()
    for code, label_key in BORROW_ITEM_TYPES:
        cmb_type.addItem(i18n.t(label_key), code)
    spn_qty = QSpinBox()
    spn_qty.setRange(1, 99)
    spn_qty.setValue(1)

    lv.addWidget(QLabel(i18n.t("label_category")))
    lv.addWidget(cmb_type)
    lv.addWidget(QLabel(i18n.t("label_quantity")))
    lv.addWidget(spn_qty)

    btn_row = QHBoxLayout()
    btn_cancel = QPushButton(i18n.t("btn_cancel"))
    btn_cancel.setObjectName("FdGhostBtn")
    btn_cancel.clicked.connect(dlg.reject)
    btn_ok = QPushButton(i18n.t("btn_borrow_confirm"))
    btn_ok.setObjectName("SolidPrimaryBtn")
    btn_ok.clicked.connect(dlg.accept)
    btn_row.addWidget(btn_cancel)
    btn_row.addStretch()
    btn_row.addWidget(btn_ok)
    lv.addLayout(btn_row)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False

    item_type = cmb_type.currentData()
    qty = spn_qty.value()
    item_label_cfg = {c: i18n.t(l) for c, l in BORROW_ITEM_TYPES}
    item_label = item_label_cfg.get(item_type, item_type)
    try:
        db.execute(
            "INSERT INTO borrowed_items (room_id, item_type, qty, note) VALUES (?,?,?,?)",
            (room_id, item_type, qty, item_label),
        )
        show_info(parent, i18n.t("title_borrow_dialog"),
                  i18n.t("msg_borrow_recorded").format(item_label=item_label, qty=qty))
        return True
    except Exception as e:
        logger.error("borrow write failed: %s", e)
        show_warning(parent, i18n.t("title_borrow_dialog"),
                     i18n.t("msg_borrow_write_fail").format(e=e))
        return False


def get_borrowed_unreturned(room_id: str) -> list:
    try:
        rows = db.execute(
            "SELECT item_type, MAX(qty,0)-MAX(qty_returned,0) AS outstanding, note "
            "FROM borrowed_items WHERE room_id=? GROUP BY item_type "
            "HAVING outstanding > 0",
            (room_id,),
        ).fetchall()
        return [(r[0], int(r[1]), r[2] or r[0]) for r in rows]
    except Exception:
        return []


def check_borrowed_on_checkout(room_id: str) -> list:
    return get_borrowed_unreturned(room_id)


def return_items_dialog(parent, room_id: str) -> bool:
    items = get_borrowed_unreturned(room_id)
    if not items:
        show_info(parent, i18n.t("title_return"), i18n.t("msg_no_borrowed_items"))
        return False

    dlg = QDialog(parent)
    dlg.setWindowTitle(i18n.t("title_return_dialog"))
    style_dialog(dlg, size="compact")
    lv = QVBoxLayout(dlg)
    lv.setContentsMargins(16, 16, 16, 16)
    lv.setSpacing(12)

    header = build_dialog_header(i18n.t("title_return_dialog"), f"房间: {room_id}")
    lv.addWidget(header)

    cmb_type = QComboBox()
    for _, item_type, qty_out, note in items:
        cmb_type.addItem(f"{note} (未还 {qty_out} 件)", item_type)
    spn_qty = QSpinBox()
    spn_qty.setRange(1, 99)
    spn_qty.setValue(1)

    lv.addWidget(QLabel(i18n.t("label_select_item")))
    lv.addWidget(cmb_type)
    lv.addWidget(QLabel(i18n.t("label_return_quantity")))
    lv.addWidget(spn_qty)

    btn_row = QHBoxLayout()
    btn_cancel = QPushButton(i18n.t("btn_cancel"))
    btn_cancel.setObjectName("FdGhostBtn")
    btn_cancel.clicked.connect(dlg.reject)
    btn_ok = QPushButton(i18n.t("btn_return_confirm"))
    btn_ok.setObjectName("SolidPrimaryBtn")
    btn_ok.clicked.connect(dlg.accept)
    btn_row.addWidget(btn_cancel)
    btn_row.addStretch()
    btn_row.addWidget(btn_ok)
    lv.addLayout(btn_row)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False

    item_type = cmb_type.currentData()
    qty = spn_qty.value()
    try:
        db.execute(
            "UPDATE borrowed_items SET qty_returned = qty_returned + ?, "
            "returned_at = datetime('now','localtime') "
            "WHERE room_id=? AND item_type=? AND qty_returned < qty AND id = ("
            "  SELECT id FROM borrowed_items WHERE room_id=? AND item_type=? "
            "  AND qty_returned < qty ORDER BY id LIMIT 1"
            ")",
            (qty, room_id, item_type, room_id, item_type),
        )
        show_info(parent, i18n.t("title_return"), i18n.t("msg_return_recorded"))
        return True
    except Exception as e:
        logger.error("return write failed: %s", e)
        show_warning(parent, i18n.t("title_return"),
                     i18n.t("msg_borrow_write_fail").format(e=e))
        return False
