"""
制卡对话框 — IssueCardDialog
支持制卡/补卡/批量制卡/贵宾延时退房
"""
from __future__ import annotations

from datetime import datetime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFormLayout, QLineEdit, QSpinBox, QCheckBox, QProgressBar,
    QDateTimeEdit,
)
from PySide6.QtCore import QDateTime, QTime
from database import db
from design_tokens import _p
from ui_helpers import show_info, show_warning, show_error, style_dialog, build_dialog_header
from ._shared import CARD_BRANDS
from .card_driver import get_driver
from .card_service import CardService
from power_controller_config import resolve_power_config


class IssueCardDialog(QDialog):
    """制卡 / 补卡对话框"""

    def __init__(self, parent=None, room_id: str = "", guest_name: str = "",
                 old_card_id: str = "", mode: str = "issue"):
        super().__init__(parent)
        self.mode = mode
        self.old_card_id = old_card_id
        self.result_card_id = ""

        title = "🔑 制卡" if mode == "issue" else "🔄 补卡"
        self.setWindowTitle(title)
        style_dialog(self, size="medium")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(build_dialog_header(title, "填写信息后点击「开始制卡」"))

        form = QFormLayout()
        form.setSpacing(10)

        self.room_edit = QLineEdit(room_id)
        self.room_edit.setPlaceholderText("如：101")
        form.addRow("房间号：", self.room_edit)

        self.guest_edit = QLineEdit(guest_name)
        self.guest_edit.setPlaceholderText("住客姓名")
        form.addRow("住客姓名：", self.guest_edit)

        self.card_count_spin = QSpinBox()
        self.card_count_spin.setRange(1, 99)
        self.card_count_spin.setValue(1)
        self.card_count_spin.setToolTip("一次性为同一房间制作多张卡，每张卡卡号自动递增。")
        if mode == "reissue":
            self.card_count_spin.setVisible(False)
        form.addRow("发卡数量：", self.card_count_spin)

        self.expire_edit = QDateTimeEdit()
        self.expire_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.expire_edit.setCalendarPopup(True)
        checkout_hour = db.get_config("default_card_checkout_hour") or db.get_config("lock_takeover_checkout_time") or "12:00"
        try:
            hh, mm = checkout_hour.split(":")
            hh_i, mm_i = max(0, min(23, int(hh))), max(0, min(59, int(mm)))
        except (ValueError, AttributeError):
            hh_i, mm_i = 12, 0
        default_expire = QDateTime.currentDateTime().addDays(1)
        default_expire.setTime(QTime(hh_i, mm_i))
        self.expire_edit.setDateTime(default_expire)

        self.chk_vip = QCheckBox("会员延时退房（贵宾延时）")
        self.chk_vip.setToolTip("启用后有效期将延长至贵宾退房时间")
        vip_co = db.get_config("lock_takeover_vip_checkout_time") or ""
        self.chk_vip.setVisible(bool(vip_co.strip()))
        self.chk_vip.toggled.connect(lambda checked: self._on_vip_toggle(checked, vip_co))
        form.addRow("", self.chk_vip)
        form.addRow("有效期至：", self.expire_edit)

        if mode == "reissue":
            old_lbl = QLabel(f"原卡号：{old_card_id}")
            old_lbl.setObjectName("Small")
            old_lbl.setStyleSheet(f"color:{_p('text_muted')};")
            form.addRow("", old_lbl)
        layout.addLayout(form)

        self.status_lbl = QLabel("⚪ 读卡器未连接")
        self.status_lbl.setObjectName("Small")
        self.status_lbl.setStyleSheet(f"color:{_p('text_muted')}; padding:4px;")
        layout.addWidget(self.status_lbl)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setFixedHeight(6)
        layout.addWidget(self.progress)

        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("FdGhostBtn")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_issue = QPushButton("🔑 开始制卡" if mode == "issue" else "🔄 开始补卡")
        self.btn_issue.setObjectName("SolidPrimaryBtn")
        self.btn_issue.clicked.connect(self._do_issue)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_issue)
        layout.addLayout(btn_row)

        self._check_driver()

    def _check_driver(self):
        driver = get_driver()
        if driver.is_connected():
            brand = CARD_BRANDS.get(driver.brand, {}).get("name", "未知")
            mode_str = "（模拟模式）" if driver._simulate else ""
            self.status_lbl.setText(f"🟢 读卡器已就绪：{brand}{mode_str}")
            self.status_lbl.setStyleSheet(f"color:{_p('amount_positive')}; padding:4px;")
        else:
            ok, msg = driver.connect()
            if ok:
                self.status_lbl.setText(f"🟢 {msg}")
                self.status_lbl.setStyleSheet(f"color:{_p('amount_positive')}; padding:4px;")
            else:
                self.status_lbl.setText(f"🔴 {msg}")
                self.status_lbl.setStyleSheet(f"color:{_p('danger')}; padding:4px;")

    def _on_vip_toggle(self, checked: bool, vip_co: str) -> None:
        if not checked or not vip_co:
            return
        try:
            hh, mm = vip_co.strip().split(":")
            hh_i, mm_i = max(0, min(23, int(hh))), max(0, min(59, int(mm)))
            new_dt = self.expire_edit.dateTime()
            new_dt.setTime(QTime(hh_i, mm_i))
            self.expire_edit.setDateTime(new_dt)
        except Exception:
            pass

    def _do_issue(self):
        from services.trace_context import trace
        room_id = self.room_edit.text().strip()
        guest_name = self.guest_edit.text().strip()
        if not room_id:
            show_warning(self, "请输入房间号")
            return
        if not guest_name:
            show_warning(self, "请输入住客姓名")
            return

        with trace("card_issue", room_id=room_id, guest=guest_name):

        expire_dt = self.expire_edit.dateTime().toPython()
        card_count = self.card_count_spin.value()

        self.btn_issue.setEnabled(False)
        self.progress.setVisible(True)

        last_ok = False
        last_result = ""
        last_card_id = ""
        for i in range(card_count):
            if self.mode == "issue":
                ok, result = CardService.issue_card(room_id, guest_name, expire_dt, card_no=i + 1)
            else:
                ok, result = CardService.reissue_card(self.old_card_id, room_id, guest_name, expire_dt)
            if not ok:
                if card_count > 1:
                    self.btn_issue.setEnabled(True)
                    show_error(self, "发卡中断",
                               f"已发出 {i} 张，在发第 {i + 1} 张时失败：{result}")
                    return
                last_ok, last_result = False, result
                break
            last_ok, last_result = True, result
            last_card_id = result
            if card_count > 1 and i < card_count - 1:
                show_info(self, "请换卡",
                          f"第 {i + 1} 张卡已写好（{result}），\n请放下一张空白卡后点击确定。")

        self.progress.setVisible(False)
        self.btn_issue.setEnabled(True)

        if last_ok:
            self.result_card_id = last_card_id
            power_line = ""
            pc = resolve_power_config()
            if pc.get("enabled"):
                power_line = (
                    f"\n\n\uD83D\uDD0C 取电：已按「{pc.get('lock_brand_name', '门锁')}」同业惯例写入"
                    f"（第 {pc['sector']} 扇区）。插卡应能取电；"
                    f"若不能，请到设置→取电页查看是否已接上旧门锁/U盘密钥。"
                )
            count_msg = f"\n共制卡 {card_count} 张，末卡号：{last_card_id}" if card_count > 1 else ""
            show_info(self, "制卡成功",
                      f"\u2705 制卡成功！{count_msg}\n\n"
                      f"房间：{room_id}\n住客：{guest_name}\n"
                      f"卡号：{last_card_id}\n"
                      f"有效期至：{expire_dt.strftime('%Y-%m-%d %H:%M')}"
                      f"{power_line}\n\n请将门卡交给住客。")
            self.accept()
        else:
            show_error(self, "制卡失败", f"\u274C {last_result}")
