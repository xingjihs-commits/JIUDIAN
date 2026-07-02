"""收银底栏 — 紧凑交班块（与 Hub ShiftTab 共用业务逻辑）。"""
from __future__ import annotations

import datetime
import json as _json
import logging
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from database import db
from design_tokens import _p
from event_bus import bus
from frontdesk_ui import (
    FD_MARGIN,
    FD_SECTION_BAR_H,
    FD_SPACE,
    FD_SPACE_SM,
    fd_section_bar,
)
from ui_surface import fd_apply_info_banner, fd_apply_shift_dock
from i18n import i18n
from sound_helper import play_success
from ui_helpers import (
    build_dialog_header,
    show_error,
    show_info,
    style_dialog,
)

logger = logging.getLogger(__name__)


class ShiftDockWidget(QFrame):
    """底栏右侧交班区：当班预期 + 支出登记 + 备注 + 确认交班。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FdShiftDock")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(FD_SPACE_SM)

        self._section_bar = fd_section_bar(i18n.t("shift_title"))
        lay.addWidget(self._section_bar)
        from ui_surface import fd_apply_section_bar_embedded
        fd_apply_section_bar_embedded(self._section_bar, bg_key="bg_card")

        self.lbl_expected = QLabel(i18n.t("shift_expected_loading"))
        self.lbl_expected.setObjectName("FdInfoBanner")
        self.lbl_expected.setWordWrap(True)
        self.lbl_expected.setFixedHeight(36)
        lay.addWidget(self.lbl_expected)
        fd_apply_info_banner(self.lbl_expected)

        self.txt = QPlainTextEdit()
        self.txt.setObjectName("FdShiftNotes")
        self.txt.setPlaceholderText(i18n.t("shift_notes_placeholder"))
        self.txt.setMinimumHeight(36)  # v7 统一 36px
        self.txt.setTabChangesFocus(True)
        lay.addWidget(self.txt, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(FD_SPACE_SM)
        self.btn_payout = QPushButton(i18n.t("expense_register_btn"))
        self.btn_payout.setObjectName("FdGhostBtn")
        self.btn_payout.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_payout.clicked.connect(self._do_payout)
        btn_row.addWidget(self.btn_payout)

        self.btn_shift = QPushButton("✅ " + i18n.t("shift_btn_confirm"))
        self.btn_shift.setObjectName("SolidPrimaryBtn")
        self.btn_shift.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shift.clicked.connect(self._do_shift)
        btn_row.addWidget(self.btn_shift)
        lay.addLayout(btn_row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_expected)
        self._timer.start(10000)
        self._refresh_expected()
        fd_apply_shift_dock(self)
        self._apply_shift_buttons()

    def _refresh_theme_styles(self) -> None:
        """换主题后重刷交班区 palette 实底。"""
        from ui_surface import fd_apply_section_bar_embedded
        fd_apply_shift_dock(self)
        if hasattr(self, "_section_bar"):
            fd_apply_section_bar_embedded(self._section_bar, bg_key="bg_card")
        fd_apply_info_banner(self.lbl_expected)
        self._apply_shift_buttons()

    def _apply_shift_buttons(self) -> None:
        """交班区按钮 — 勿被 dock 父级 QSS 盖成透明。"""
        from ui_surface import fd_apply_ghost_btn, fd_apply_solid_primary_btn
        fd_apply_solid_primary_btn(self.btn_shift, min_height=36)
        fd_apply_ghost_btn(self.btn_payout, min_height=36)

    def apply_ui_scale(self, scale: float) -> None:
        from tools.cashier_canvas import (
            px, CHECKIN_SECTION_BAR_H, CHECKIN_SHIFT_INFO_H, CHECKIN_SHIFT_NOTE_H,
        )
        self.lbl_expected.setFixedHeight(px(CHECKIN_SHIFT_INFO_H, scale))
        self.txt.setFixedHeight(px(CHECKIN_SHIFT_NOTE_H, scale))
        bar_h = px(CHECKIN_SECTION_BAR_H, scale)
        for bar in self.findChildren(QFrame):
            if bar.objectName() == "FdSectionBar":
                bar.setFixedHeight(bar_h)

    def _refresh_expected(self) -> None:
        try:
            expected = db.get_shift_expected()
            cur = i18n.t("currency_symbol")
            self.lbl_expected.setText(
                i18n.t("shift_expected_line").format(cur, f"{expected:.2f}", i18n.t("shift_cash_must_match"))
            )
        except Exception:
            pass

    def _do_payout(self) -> None:
        d = QDialog(self)
        d.setWindowTitle(i18n.t("shift_payout"))
        style_dialog(d, size="compact")
        outer = QVBoxLayout(d)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)
        outer.addWidget(
            build_dialog_header(
                i18n.t("payout_register_header_title"),
                i18n.t("payout_register_header_sub"),
            )
        )
        form_box = QWidget()
        f = QFormLayout(form_box)
        cmb_type = QComboBox()
        types = [
            ("payout_boss", "WITHDRAW"),
            ("payout_purchase", "PURCHASE"),
            ("payout_salary", "SALARY"),
            ("payout_misc", "MISC"),
        ]
        for key, code in types:
            cmb_type.addItem(i18n.t(key), code)
        txt_amt = QLineEdit()
        txt_note = QLineEdit()
        f.addRow(i18n.t("payout_type") + ":", cmb_type)
        f.addRow(i18n.t("table_amount") + ":", txt_amt)
        f.addRow(i18n.t("table_note") + ":", txt_note)
        btn_save = QPushButton(i18n.t("payout_confirm_register"))
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_save.clicked.connect(d.accept)
        f.addRow(btn_save)
        outer.addWidget(form_box)
        if not d.exec():
            return
        try:
            amt = float(txt_amt.text())
            code = cmb_type.currentData() or "MISC"
            t_label = cmb_type.currentText()
            note_tail = txt_note.text().strip()
            full_note = f"{code}:{note_tail}" if note_tail else code
            db.append_ledger("PAYOUT", -amt, "CASH", 1, note=full_note)
            bus.show_success_overlay.emit(i18n.t("msg_success"))
            from telegram_shadow import telegram_thread
            if telegram_thread.isRunning():
                msg = (
                    f"💸 [{i18n.t('shift_payout')}] {t_label}\n"
                    f"{i18n.t('table_amount')}: {i18n.t('currency_symbol')}{amt}\n"
                    f"{i18n.t('table_note')}: {note_tail}"
                )
                telegram_thread.send_alert_sync(msg)
            self._refresh_expected()
        except Exception as exc:
            show_error(self, i18n.t("dlg_tip"), str(exc))

    def _do_shift(self) -> None:
        d1 = QDialog(self)
        d1.setWindowTitle(i18n.t("shift_step1_win_title"))
        style_dialog(d1, size="compact")
        l1 = QVBoxLayout(d1)
        l1.setContentsMargins(16, 16, 16, 16)
        l1.setSpacing(12)
        l1.addWidget(
            build_dialog_header(
                i18n.t("shift_step1_header_title"),
                i18n.t("shift_step1_header_sub"),
            )
        )
        f1 = QFormLayout()
        expected = db.get_shift_expected()
        cur = i18n.t("currency_symbol")
        lbl_exp = QLabel(f"{cur}{expected:.2f}")
        lbl_exp.setStyleSheet(
            f"font-size:16px;font-weight: 600;color:{_p('primary')};"
        )
        f1.addRow(i18n.t("shift_book_should_cash"), lbl_exp)
        spn_cash = QDoubleSpinBox()
        spn_cash.setRange(0, 999999)
        spn_cash.setDecimals(2)
        spn_cash.setSingleStep(10)
        f1.addRow(i18n.t("shift_drawer_actual_cash"), spn_cash)
        btn1 = QPushButton(i18n.t("shift_btn_next"))
        btn1.setObjectName("SolidPrimaryBtn")
        btn1.clicked.connect(d1.accept)
        f1.addRow(btn1)
        l1.addLayout(f1)
        if not d1.exec():
            return
        actual_cash = spn_cash.value()

        d2 = QDialog(self)
        d2.setWindowTitle(i18n.t("shift_step2_win_title"))
        style_dialog(d2, size="compact")
        l2 = QVBoxLayout(d2)
        l2.setContentsMargins(16, 16, 16, 16)
        l2.setSpacing(12)
        l2.addWidget(
            build_dialog_header(
                i18n.t("shift_step2_header_title"),
                i18n.t("shift_step2_header_sub"),
            )
        )
        f2 = QFormLayout()
        shop_items = db.execute(
            "SELECT sku, name, COALESCE(stock,0) FROM shop_items ORDER BY name"
        ).fetchall()
        shop_actuals = {}
        if shop_items:
            for sku, name, sys_stock in shop_items:
                spn = QSpinBox()
                spn.setRange(0, 9999)
                spn.setValue(int(sys_stock))
                f2.addRow(i18n.t("shift_shop_row_label").format(name, sys_stock), spn)
                shop_actuals[sku] = (spn, int(sys_stock))
        else:
            f2.addRow(QLabel(i18n.t("shift_shop_no_items")))
        btn2 = QPushButton(i18n.t("shift_btn_next"))
        btn2.setObjectName("SolidPrimaryBtn")
        btn2.clicked.connect(d2.accept)
        f2.addRow(btn2)
        l2.addLayout(f2)
        if not d2.exec():
            return

        shift_shop_map = {sku: spn.value() for sku, (spn, _ss) in shop_actuals.items()}
        try:
            from permission_system import PermissionManager
            _u = PermissionManager.current_user()
            _op = _u.get("username", "SHIFT") if _u else "SHIFT"
        except Exception:
            _op = "SHIFT"
        try:
            db.apply_opening_stocktake(shift_shop_map, _op, "SHIFT_STOCKTAKE")
        except Exception:
            pass

        d3 = QDialog(self)
        d3.setWindowTitle(i18n.t("shift_step3_win_title"))
        style_dialog(d3, size="small")
        l3 = QVBoxLayout(d3)
        l3.setContentsMargins(16, 16, 16, 16)
        l3.setSpacing(12)
        l3.addWidget(
            build_dialog_header(
                i18n.t("shift_step3_header_title"),
                i18n.t("shift_step3_header_sub"),
            )
        )
        f3 = QFormLayout()
        inhouse_count = db.execute(
            "SELECT COUNT(*) FROM rooms WHERE status='INHOUSE'"
        ).fetchone()[0]
        lbl_inhouse = QLabel(i18n.t("shift_system_inhouse_display").format(inhouse_count))
        lbl_inhouse.setStyleSheet(
            f"font-size:14px;font-weight: 600;color:{_p('amount_positive')};"
        )
        f3.addRow(i18n.t("shift_system_inhouse_field"), lbl_inhouse)
        spn_inhouse = QSpinBox()
        spn_inhouse.setRange(0, 9999)
        spn_inhouse.setValue(inhouse_count)
        f3.addRow(i18n.t("shift_actual_inhouse_field"), spn_inhouse)
        txt_notes = QPlainTextEdit()
        txt_notes.setPlaceholderText(i18n.t("shift_notes_ph"))
        txt_notes.setMinimumHeight(60)
        txt_notes.setMaximumHeight(160)
        f3.addRow(i18n.t("shift_notes_field"), txt_notes)
        btn3 = QPushButton(i18n.t("shift_submit_btn"))
        btn3.setObjectName("SolidPrimaryBtn")
        btn3.clicked.connect(d3.accept)
        f3.addRow(btn3)
        l3.addLayout(f3)
        if not d3.exec():
            return
        actual_inhouse = spn_inhouse.value()
        notes = txt_notes.toPlainText().strip()

        cash_diff = actual_cash - expected
        inhouse_diff = actual_inhouse - inhouse_count
        shop_diff_lines = []
        for sku, (spn, sys_stock) in shop_actuals.items():
            actual_qty = spn.value()
            diff = actual_qty - sys_stock
            if diff != 0:
                name_row = db.execute(
                    "SELECT name FROM shop_items WHERE sku=?", (sku,)
                ).fetchone()
                name = name_row[0] if name_row else sku
                shop_diff_lines.append(
                    i18n.t("shift_shop_diff_fmt").format(name, sys_stock, actual_qty, diff)
                )

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cash_status = (
            i18n.t("shift_status_match")
            if abs(cash_diff) < 0.01
            else i18n.t("shift_status_cash_diff").format(cur, cash_diff)
        )
        inhouse_status = (
            i18n.t("shift_status_match")
            if inhouse_diff == 0
            else i18n.t("shift_status_room_diff").format(inhouse_diff)
        )
        shop_status = (
            i18n.t("shift_status_shop_ok")
            if not shop_diff_lines
            else i18n.t("shift_status_shop_diff").format("\n".join(shop_diff_lines))
        )
        summary = "\n".join(
            [
                i18n.t("shift_report_doc_title").format(now_str),
                "=" * 30,
                i18n.t("shift_line_cash").format(
                    sym=cur, book=expected, drawer=actual_cash, status=cash_status
                ),
                i18n.t("shift_line_inhouse").format(
                    sys_cnt=inhouse_count, act_cnt=actual_inhouse, status=inhouse_status
                ),
                i18n.t("shift_line_shop").format(status=shop_status),
                i18n.t("shift_line_notes").format(notes=notes or i18n.t("shift_note_none")),
            ]
        )

        if abs(cash_diff) >= 0.01:
            diff_note = i18n.t("shift_ledger_cash_diff").format(
                f"{cur}{expected:.2f}", f"{cur}{actual_cash:.2f}", f"{cur}{cash_diff:+.2f}"
            )
            db.append_ledger("SHIFT_DIFF", cash_diff, "CASH", 1, note=diff_note)

        meta = _json.dumps(
            {
                "expected_cash": expected,
                "actual_cash": actual_cash,
                "cash_diff": cash_diff,
                "inhouse_system": inhouse_count,
                "inhouse_actual": actual_inhouse,
                "shop_diffs": shop_diff_lines,
                "notes": notes,
            },
            ensure_ascii=False,
        )
        db.execute(
            "INSERT INTO audit_events (event_id, event_type, actor_id, reason, metadata_json) "
            "VALUES (?,?,?,?,?)",
            (f"SH{int(time.time())}", "SHIFT", "1", notes, meta),
        )
        db.append_ledger(
            "SHIFT_END",
            0,
            "CASH",
            1,
            note=i18n.t("shift_close_ledger_note").format(
                now_str, inhouse_count, actual_inhouse
            ),
        )
        show_info(self, i18n.t("shift_complete_msg_title"), summary)
        try:
            from telegram_shadow import telegram_thread
            if telegram_thread.isRunning():
                telegram_thread.send_alert_sync(summary)
        except Exception:
            pass
        self.txt.clear()
        self._refresh_expected()
        play_success()
