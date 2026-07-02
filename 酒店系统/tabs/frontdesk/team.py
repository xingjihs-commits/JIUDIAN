"""团队入住/退房"""

import datetime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QAbstractItemView, QGroupBox, QRadioButton,
    QFormLayout, QLineEdit, QDateTimeEdit,
)
from PySide6.QtCore import QDateTime
from database import db
from i18n import i18n
from ui_helpers import show_warning, show_info, ask_confirm, style_dialog, build_dialog_header
from event_bus import bus
from frontdesk_ui import fd_apply_compact_input
from .checkout import get_default_room_status


class TeamMixin:
    """团队入住/批量制卡/团体退房"""

    def _team_checkin(self):
        ready_rooms = db.execute(
            "SELECT room_id, room_type, base_price "
            "FROM rooms JOIN room_type_templates ON rooms.room_type = room_type_templates.type_id "
            "WHERE rooms.status='READY' ORDER BY rooms.room_id"
        ).fetchall()
        if not ready_rooms:
            show_warning(self, i18n.t("team_checkin"), i18n.t("team_no_ready"))
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("team_checkin"))
        style_dialog(dlg, size="large")
        lv = QVBoxLayout(dlg)
        lv.setContentsMargins(16, 16, 16, 16)
        lv.setSpacing(12)
        header = build_dialog_header(i18n.t("team_checkin"), i18n.t("team_checkin_sub"))
        lv.addWidget(header)

        room_list = QListWidget()
        room_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        cur = i18n.t("currency_symbol")
        for r in ready_rooms:
            room_list.addItem(f"{r[0]}  |  {r[1]}  |  {cur}{r[2]:.0f}/" + i18n.t("team_night"))
        lv.addWidget(room_list)

        f = QFormLayout()
        txt_guest = QLineEdit()
        txt_guest.setPlaceholderText(i18n.t("team_leader_ph"))
        fd_apply_compact_input(txt_guest)
        f.addRow(i18n.t("team_name") + "：", txt_guest)

        dt_in = QDateTimeEdit(QDateTime.currentDateTime())
        dt_in.setCalendarPopup(True)
        dt_in.setDisplayFormat("yyyy-MM-dd HH:mm")
        f.addRow(i18n.t("team_checkin_time") + "：", dt_in)

        dt_out = QDateTimeEdit(QDateTime.currentDateTime().addDays(1))
        dt_out.setCalendarPopup(True)
        dt_out.setDisplayFormat("yyyy-MM-dd HH:mm")
        dt_out.setDateTime(QDateTime.fromString(
            (datetime.datetime.now() + datetime.timedelta(days=1)).replace(hour=12, minute=0).strftime("%Y-%m-%d %H:%M"),
            "yyyy-MM-dd HH:mm"
        ))
        f.addRow(i18n.t("team_checkout_time") + "：", dt_out)

        cb_batch_card = QGroupBox()
        cb_batch_card.setCheckable(True)
        cb_batch_card.setChecked(False)
        cb_batch_card.setTitle(i18n.t("team_batch_card"))
        lv.addWidget(cb_batch_card)

        lv.addLayout(f)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton(i18n.t("btn_cancel"))
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok = QPushButton(i18n.t("team_btn_checkin"))
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_ok.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(btn_ok)
        lv.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = room_list.selectedItems()
        if not selected:
            show_warning(self, i18n.t("team_checkin"), i18n.t("team_select_at_least"))
            return

        guest_name = txt_guest.text().strip() or i18n.t("team_guest_default")
        cin = dt_in.dateTime().toPython().strftime("%Y-%m-%d %H:%M:%S")
        cout = dt_out.dateTime().toPython().strftime("%Y-%m-%d %H:%M:%S")
        do_card = cb_batch_card.isChecked()

        picked_rooms = []
        for it in selected:
            parts = it.text().split("|")
            rid = parts[0].strip()
            rt = parts[1].strip() if len(parts) > 1 else ""
            picked_rooms.append((rid, rt))

        success = 0
        failed = []
        card_fail = []
        for rid, rt in picked_rooms:
            try:
                from transactions.checkin import CheckinTransaction

                def _noop_ledger(conn):
                    pass

                CheckinTransaction(
                    room_id=rid,
                    guest_name=guest_name,
                    id_card=i18n.t("team_guest_default"),
                    phone="",
                    ledger_callback=_noop_ledger,
                ).commit()

                db.execute(
                    "UPDATE guests SET checkout_time=? WHERE room_id=? AND status='INHOUSE' ORDER BY id DESC LIMIT 1",
                    (cout, rid),
                )
                db.execute("UPDATE rooms SET status='INHOUSE' WHERE room_id=?", (rid,))
                success += 1
            except Exception as e:
                failed.append(f"{rid}: {e}")

        if do_card and success > 0:
            for rid, _ in picked_rooms[:success]:
                try:
                    from card_ritual_dialog import CardRitualDialog
                    card_dlg = CardRitualDialog(self, room_id=rid, guest_name=guest_name, mode="issue")
                    if card_dlg.exec() != QDialog.Accepted:
                        card_fail.append(rid)
                except Exception:
                    card_fail.append(rid)

        msg = i18n.t("team_result_ok", "团队入住完成：{} 间成功").format(success)
        if failed:
            msg += "\n" + i18n.t("team_result_fail", "失败 {} 间：").format(len(failed)) + "\n" + "\n".join(failed)
        if card_fail:
            msg += "\n" + i18n.t("team_result_card_fail", "制卡失败 {} 间：").format(len(card_fail)) + ", ".join(card_fail)
        show_info(self, i18n.t("team_checkin"), msg)

        for rid, _ in picked_rooms:
            bus.room_status_changed.emit(rid, "INHOUSE")

    def _team_checkout(self):
        inhouse = db.execute(
            "SELECT room_id, room_type FROM rooms WHERE status='INHOUSE' ORDER BY room_id"
        ).fetchall()
        if not inhouse:
            show_warning(self, i18n.t("team_checkout"), i18n.t("team_no_inhouse"))
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("team_checkout"))
        try:
            style_dialog(dlg, "medium")
        except Exception:
            dlg.setMinimumWidth(500)
            dlg.setMinimumHeight(400)
        dlg.setModal(True)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        lay.addWidget(QLabel(i18n.t("team_checkout_pick")))

        room_list = QListWidget()
        room_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for r in inhouse:
            room_list.addItem(f"{r[0]}  ({r[1] or ''})")
        lay.addWidget(room_list)

        status_group = QGroupBox(i18n.t("team_checkout_status"))
        slay = QHBoxLayout(status_group)
        rb_vc = QRadioButton(i18n.t("team_status_ready"))
        rb_vd = QRadioButton(i18n.t("team_status_dirty"))
        default_rs = get_default_room_status()
        if default_rs == "READY":
            rb_vc.setChecked(True)
        else:
            rb_vd.setChecked(True)
        slay.addWidget(rb_vc)
        slay.addWidget(rb_vd)
        lay.addWidget(status_group)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton(i18n.t("btn_cancel"))
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok = QPushButton(i18n.t("team_btn_checkout_fmt", "确认退房（{} 间可选）").format(len(inhouse)))
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_ok.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(btn_ok)
        lay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected_items = room_list.selectedItems()
        if not selected_items:
            show_warning(self, i18n.t("team_checkout"), i18n.t("team_select_at_least"))
            return

        picked_rooms = [it.text().split("  ")[0].strip() for it in selected_items]
        target_status = "READY" if rb_vc.isChecked() else "DIRTY"

        status_label = i18n.t("team_status_ready") if target_status == "READY" else i18n.t("team_status_dirty")
        if not ask_confirm(
            self, i18n.t("team_checkout_confirm"),
            i18n.t("team_checkout_confirm_msg", "确认将 {} 间房全部退房？\n房态将设为 {}\n\n").format(len(picked_rooms), status_label)
            + "\n".join(f"  \u2022 {r}" for r in picked_rooms),
        ):
            return

        from transactions.checkout import TeamCheckoutTransaction
        results = TeamCheckoutTransaction(picked_rooms, self._current_operator_id()).execute(
            target_room_status=target_status,
        )

        ok_count = sum(1 for r in results if r.ok)
        fail_count = sum(1 for r in results if not r.ok)
        for r in results:
            if r.ok:
                bus.room_status_changed.emit(r.guest_name or r.ok, r.room_status)
            else:
                bus.room_status_changed.emit(r.guest_name or "", "")

        msg = i18n.t("team_checkout_result", "团体退房完成：{} 间成功").format(ok_count)
        if fail_count:
            msg += i18n.t("team_result_fail", "，{} 间失败").format(fail_count)
            for r in results:
                if not r.ok:
                    msg += f"\n  \u274c {r.error}"
        show_info(self, i18n.t("team_checkout"), msg)
        self._reset()
