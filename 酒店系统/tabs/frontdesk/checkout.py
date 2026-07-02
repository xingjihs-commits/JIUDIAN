"""退房流程 — 退房/一键退房/延迟退房/检查单/保洁通知/换房"""

import datetime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QRadioButton, QComboBox, QLineEdit,
)
from database import db
from design_tokens import _p
from i18n import i18n
from ui_helpers import show_warning, show_info, ask_confirm, style_dialog, select_from_list
from event_bus import bus
from sound_helper import play_success, play_fail, play_warn
from ._shared import _status_placeholders
from lock_legacy_bridge import LEGACY_ACTIVE_CARD_STATUSES, CARD_STATUS_ERASED

logger = __import__("logging").getLogger(__name__)


def get_default_room_status() -> str:
    try:
        flag = str(db.get_config("lock_takeover_flag_checkout") or "").strip()
    except Exception:
        flag = ""
    if flag == "0":
        return "READY"
    if flag == "1":
        return "DIRTY"
    return "DIRTY"


class CheckoutMixin:
    """退房流程"""

    def _checkout(self):
        from permission_system import PermissionManager
        if not PermissionManager.has_permission("checkout"):
            from ui_helpers import show_warning
            show_warning(self, i18n.t("perm_denied"), i18n.t("perm_no_checkout"))
            return
        rid = self.current_room
        if not rid:
            show_warning(self, i18n.t("btn_checkout"), "请先在房态选中一间在住房间。")
            return
        st = db.execute("SELECT status FROM rooms WHERE room_id=?", (rid,)).fetchone()
        if not st or st[0] != "INHOUSE":
            show_warning(self, i18n.t("btn_checkout"), "当前房间不是在住状态，无法退房。")
            return

        card_row = self._active_card_for_current_room()
        active_card_id = card_row[0] if card_row else ""

        overtime_charge = 0.0
        overtime_hours = 0.0
        guest_row = db.execute(
            "SELECT name, checkin_time, checkout_time FROM guests WHERE room_id=? AND status='INHOUSE' ORDER BY id DESC LIMIT 1",
            (rid,),
        ).fetchone()
        guest_name_ov = ""
        if guest_row:
            guest_name_ov, checkin_time, expected_checkout = guest_row
            if expected_checkout:
                try:
                    checkin_dt = datetime.datetime.strptime(str(checkin_time)[:19], "%Y-%m-%d %H:%M:%S")
                    expected_dt = datetime.datetime.strptime(str(expected_checkout)[:19], "%Y-%m-%d %H:%M:%S")
                    now_dt = datetime.datetime.now()
                    if now_dt > expected_dt:
                        overtime_hours = (now_dt - expected_dt).total_seconds() / 3600.0
                        rt_row = db.execute("SELECT room_type FROM rooms WHERE room_id=?", (rid,)).fetchone()
                        rt = rt_row[0] if rt_row else ""
                        if rt:
                            day_rate = db.get_rate_for_room_type(rt, "standard")
                            if overtime_hours > 6:
                                overtime_charge = day_rate
                            elif overtime_hours > 2:
                                overtime_charge = day_rate
                            elif overtime_hours > 0:
                                overtime_charge = day_rate * 0.5
                except Exception:
                    pass

        dlg = QDialog(self)
        dlg.setWindowTitle(f"退房 · {rid}")
        try:
            style_dialog(dlg, "small")
        except Exception:
            dlg.setMinimumWidth(440)
        dlg.setModal(True)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        lay.addWidget(QLabel(f"<b>退房房间：{rid}</b>"))

        if overtime_hours > 0 and overtime_charge > 0.01:
            cur = i18n.t("currency_symbol")
            ov_lbl = QLabel(f"⏰ 已超时 {overtime_hours:.1f} 小时，将加收 {cur}{overtime_charge:.2f}")
            ov_lbl.setObjectName("H4Title")
            ov_lbl.setStyleSheet(f"color:{_p('danger')}; font-weight: 600;")
            lay.addWidget(ov_lbl)

        mode_group = QGroupBox("退房方式")
        mode_lay = QVBoxLayout(mode_group)
        self._rb_checkout_bycard = QRadioButton("有卡退房 — 前台放卡 → 擦除卡数据 → 退房完成")
        self._rb_checkout_nocard = QRadioButton("无卡退房 — 客人卡丢失 → 制作退房卡给保洁刷锁")
        if active_card_id:
            self._rb_checkout_bycard.setChecked(True)
        else:
            self._rb_checkout_bycard.setEnabled(False)
            self._rb_checkout_nocard.setChecked(True)
        mode_lay.addWidget(self._rb_checkout_bycard)
        mode_lay.addWidget(self._rb_checkout_nocard)
        lay.addWidget(mode_group)

        status_group = QGroupBox("退房后房态")
        status_lay = QVBoxLayout(status_group)
        self._rb_status_vc = QRadioButton("空净房 — 可直接售卖")
        self._rb_status_vd = QRadioButton("脏房 — 需保洁打扫")
        default_rs = get_default_room_status()
        if default_rs == "READY":
            self._rb_status_vc.setChecked(True)
        else:
            self._rb_status_vd.setChecked(True)
        status_lay.addWidget(self._rb_status_vc)
        status_lay.addWidget(self._rb_status_vd)
        lay.addWidget(status_group)

        checklist_group = QGroupBox("退房检查单")
        checklist_group.setCheckable(True)
        checklist_group.setChecked(False)
        cl_lay = QVBoxLayout(checklist_group)
        checklist_items = {
            "bedding": "床品", "tv": "电视", "ac": "空调",
            "water_heater": "热水器", "toilet": "马桶", "other": "其他",
        }
        self._checkout_checklist = {}
        for key, label in checklist_items.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            cmb = QComboBox()
            cmb.addItem("✅ 正常", "ok")
            cmb.addItem("❌ 损坏", "damaged")
            cmb.setCurrentIndex(0)
            row.addWidget(cmb, 1)
            cl_lay.addLayout(row)
            self._checkout_checklist[key] = cmb
        lay.addWidget(checklist_group)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok = QPushButton("确认退房")
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_ok.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(btn_ok)
        lay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        mode = "bycard" if self._rb_checkout_bycard.isChecked() else "nocard"
        target_room_status = "READY" if self._rb_status_vc.isChecked() else "DIRTY"

        cur_sym = i18n.t("currency_symbol")
        damaged_items = []
        checklist_active = checklist_group.isChecked()
        if checklist_active:
            damage_labels = {
                "bedding": "床品损坏", "tv": "电视损坏", "ac": "空调损坏",
                "water_heater": "热水器损坏", "toilet": "马桶损坏", "other": "其他损坏",
            }
            for key, cmb in self._checkout_checklist.items():
                if cmb.currentData() == "damaged":
                    label = damage_labels[key]
                    damaged_items.append(label)
                    db.execute(
                        "INSERT INTO folio_items (room_id, sku, qty, unit_price, total, created_at, note) VALUES (?,?,1,?,?,datetime('now','localtime'),?)",
                        (rid, label, 0.0, 0.0, f"退房检查单：{label}"),
                    )

        from transactions.checkout import CheckoutTransaction

        if mode == "bycard" and active_card_id:
            result = CheckoutTransaction(rid, self._current_operator_id()).execute_bycard(
                card_id=active_card_id, target_room_status=target_room_status,
            )
        else:
            result = CheckoutTransaction(rid, self._current_operator_id()).execute_nocard(
                target_room_status=target_room_status,
            )

        if not result.ok:
            bus.room_status_changed.emit(rid, "")
            play_fail()
            show_warning(self, "退房未完成", result.error)
            return

        if overtime_charge > 0.01:
            db.append_ledger(
                "ROOM_IN_OVERTIME", overtime_charge, "CASH", 1, rid,
                f"延迟退房加收（{overtime_hours:.1f}小时）", pay_method="CASH_USD", is_deposit=0,
            )

        play_success()
        bus.room_status_changed.emit(rid, result.room_status)

        try:
            from telegram_shadow import telegram_thread
            if telegram_thread.isRunning():
                telegram_thread.notify_checkout(rid, result.guest_name or "")
        except Exception:
            pass
        self._notify_housekeeping(rid)

        try:
            from telemetry import report_event
            report_event("CHECKOUT", {
                "room_id": rid,
                "guest": result.guest_name or "",
                "mode": mode,
            })
        except Exception:
            pass

        msg_parts = [f"\u2705 {rid} 退房成功"]
        if mode == "bycard":
            msg_parts.append(f"客人卡已擦除（{active_card_id}）")
        elif result.checkout_card_hex:
            msg_parts.append(f"退房卡已写入：{result.checkout_card_hex[:16]}...")
        if result.deposit_refund > 0:
            msg_parts.append(f"押金已退还：{cur_sym}{result.deposit_refund:.2f}")
        if overtime_charge > 0.01:
            msg_parts.append(f"延迟退房加收：{cur_sym}{overtime_charge:.2f}")
        if damaged_items:
            msg_parts.append(f"损坏登记：{', '.join(damaged_items)}（已记入账单）")
        # [sub-d Task1] 退房成功后展示账单号（若有），便于前台追溯/打印
        if getattr(result, "bill_no", ""):
            msg_parts.append(f"账单号：{result.bill_no}")
        if result.next_action:
            msg_parts.append(f"\n{result.next_action}")

        # [sub-d Task1] 退房成功弹窗增加"查看账单"按钮
        # 自定义 QDialog 替代 show_info：含"查看账单"和"关闭"两个按钮
        if getattr(result, "bill_no", ""):
            self._show_checkout_success_with_bill("\n".join(msg_parts), result.bill_no)
        else:
            show_info(self, "退房成功", "\n".join(msg_parts))
        self._reset()

    def _show_checkout_success_with_bill(self, message: str, bill_no: str) -> None:
        """[sub-d Task1] 退房成功弹窗 — 含"查看账单"按钮。

        点击"查看账单"打开 BillDetailDialog 展示该账单头 + 明细 + 打印入口；
        点击"关闭"或关闭窗口即结束。不阻断后续 self._reset()。
        """
        try:
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QHBoxLayout
            from ui_helpers import style_dialog, build_dialog_header
            from .bill_detail_dialog import BillDetailDialog
        except Exception:
            # 任何导入失败都回退到普通 show_info，不阻断退房流程
            show_info(self, "退房成功", message)
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("退房成功")
        style_dialog(dlg, size="small")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(14)
        lay.addWidget(build_dialog_header("✅  退房成功", message))
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_view = QPushButton("查看账单")
        btn_view.setObjectName("FdCardActionBtn")
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("SolidPrimaryBtn")
        btn_view.clicked.connect(dlg.accept)
        btn_close.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_view)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)
        view_bill = (dlg.exec() == QDialog.DialogCode.Accepted)
        if view_bill:
            try:
                BillDetailDialog(self, bill_no=bill_no).exec()
            except Exception as e:
                # 账单查看失败仅告警，不影响主流程
                logger.warning("[checkout] 打开账单详情失败: %s", e)
                show_warning(self, "账单查看", f"账单详情打开失败：{e}")

    def _quick_checkout(self):
        rid = self.current_room
        if not rid:
            show_warning(self, "一键退房", "请先在房态选中一间在住房间。")
            return
        st = db.execute("SELECT status FROM rooms WHERE room_id=?", (rid,)).fetchone()
        if not st or st[0] != "INHOUSE":
            show_warning(self, "一键退房", "当前房间不是在住状态，无法退房。")
            return

        if not ask_confirm(self, "一键退房确认",
                           f"确认将 {rid} 快速退房？\n将自动：结算费用 → 退还剩余押金 → 注销所有客人卡 → 标记脏房 → 生成保洁任务。"):
            return

        try:
            with db.transaction() as conn:
                guest_row = conn.execute(
                    "SELECT id, name FROM guests WHERE room_id=? AND status='INHOUSE' ORDER BY id DESC LIMIT 1",
                    (rid,),
                ).fetchone()
                if not guest_row:
                    show_warning(self, "一键退房", "未找到当前在住客人。")
                    return
                guest_id, guest_name = guest_row

                dep_net = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE room_id=? AND is_deposit=1 AND tx_type IN ('DEPOSIT_IN','DEPOSIT_OUT')",
                    (rid,),
                ).fetchone()[0]
                from money_utils import to_money
                dep_net = float(to_money(dep_net or 0))

                charge_net = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE room_id=? AND is_deposit=0 AND tx_type IN ('ROOM_IN','SHOP','TIP','OTHER')",
                    (rid,),
                ).fetchone()[0]
                charge_net = float(to_money(charge_net or 0))

                refund = dep_net - charge_net
                if dep_net > 0:
                    db.append_ledger_conn(
                        conn, "DEPOSIT_OUT", -dep_net, "CASH", 1, rid,
                        "退房押金退还", is_deposit=1,
                    )

                active_cards = conn.execute(
                    f"SELECT card_id FROM card_records WHERE room_id=? AND status IN ({_status_placeholders(LEGACY_ACTIVE_CARD_STATUSES)})",
                    (rid, *LEGACY_ACTIVE_CARD_STATUSES),
                ).fetchall()
                for (cid,) in active_cards:
                    conn.execute(
                        "UPDATE card_records SET status=? WHERE card_id=?",
                        (CARD_STATUS_ERASED, cid),
                    )

                conn.execute("UPDATE rooms SET status='DIRTY' WHERE room_id=?", (rid,))
                conn.execute("UPDATE guests SET status='OUT', checkout_time=CURRENT_TIMESTAMP WHERE id=?", (guest_id,))
                db.log_action(self._current_operator_id(), "CHECKOUT",
                              f"room={rid} guest={guest_name} mode=quick")

            db.create_housekeeping_task(rid, "CHECKOUT_CLEAN", source="checkout", note="退房后保洁（一键退房）")
            try:
                from telegram_shadow import telegram_thread
                if telegram_thread.isRunning():
                    telegram_thread.notify_checkout(rid, guest_name or "")
            except Exception:
                pass

            bus.room_status_changed.emit(rid, "DIRTY")
            msg = f"✅ {rid} 一键退房完成\n"
            if dep_net > 0:
                msg += f"押金退还：¥{dep_net:.2f}\n"
            if refund > 0:
                msg += f"应退客人：¥{refund:.2f}"
            play_success()
            show_info(self, "一键退房", msg)
            self._reset()
        except Exception as e:
            play_fail()
            show_warning(self, "一键退房失败", str(e))

    def _notify_housekeeping(self, rid):
        try:
            from telegram_shadow import telegram_thread
            if not telegram_thread.isRunning():
                return
            task_id = db.create_housekeeping_task(rid, "CHECKOUT_CLEAN", source="checkout", note="退房后保洁")
            msg = i18n.t("checkout_notify_hk").format(rid, rid)
            from telegram_bot_config import get_work_bot_token
            token = get_work_bot_token()
            if not token:
                return
            import requests as _req
            sent = set()
            targets = [
                db.get_config("housekeeping_group_id"),
                db.get_config("housekeeping_chat_id"),
            ]
            for cid in [str(x).strip() for x in targets if str(x or "").strip()]:
                if cid in sent:
                    continue
                sent.add(cid)
                btns = [[
                    {"text": "接单", "callback_data": f"hk_accept:{task_id}:GROUP"},
                    {"text": i18n.t("hk_btn_done_room").format(rid), "callback_data": f"hk_done:{task_id}:GROUP"},
                ]]
                payload = {
                    "chat_id": cid,
                    "text": f"{msg}\n🧾 任务：<code>{task_id}</code>",
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": btns},
                }
                try:
                    _req.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=5)
                except Exception:
                    pass
            if not sent:
                telegram_thread.send_alert_sync(f"{msg}\n⚠️ 未配置保洁群，已临时通知老板/前台。")
        except Exception as e:
            logger.warning("HK NOTIFY: %s", e)

    def _change_room(self):
        old_room = self.current_room
        if not old_room:
            return
        rows = db.execute(
            "SELECT room_id, room_type, COALESCE(lock_no,'') FROM rooms WHERE status='READY' AND room_id<>? ORDER BY room_id",
            (old_room,),
        ).fetchall()
        options = [f"{r[0]} | {r[1] or ''} | 锁号 {r[2] or '未绑定'}" for r in rows if (r[2] or "").strip()]
        if not options:
            show_warning(self, "一键换房", "没有可换入的空净房，或空房尚未绑定锁号。")
            return
        picked, ok = select_from_list(self, "一键换房", f"当前房间：{old_room}\n请选择新房间。", options)
        if not ok or not picked:
            return
        new_room = picked.split("|", 1)[0].strip()
        if not ask_confirm(self, "确认换房", f"把 {old_room} 的在住客人换到 {new_room}？\n原房间将标记为脏房。"):
            return
        from transactions.room_change import RoomChangeTransaction
        result = RoomChangeTransaction(old_room, new_room, self._current_operator_id()).execute()
        if not result.ok:
            bus.room_status_changed.emit(old_room, "")
            bus.room_status_changed.emit(new_room, "")
            show_warning(self, "换房失败", result.error)
            return
        bus.room_status_changed.emit(old_room, "DIRTY")
        bus.room_status_changed.emit(new_room, "INHOUSE")
        row = db.execute("SELECT room_type FROM rooms WHERE room_id=?", (new_room,)).fetchone()
        self.update_room(new_room, row[0] if row else "", None)
        show_info(self, "换房完成", f"已从 {old_room} 换到 {new_room}。请按需要重新写新房卡。")
