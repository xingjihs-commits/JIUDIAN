from __future__ import annotations

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QFormLayout, QDialog, QRadioButton, QButtonGroup,
    QSpinBox, QDoubleSpinBox, QCheckBox, QPlainTextEdit, QFrame,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import show_info, show_warning, style_dialog, build_dialog_header
from design_tokens import _p
from frontdesk_ui import (
    fd_section_bar, FD_MARGIN, FD_SPACE_SM, fd_apply_low_freq_btn,
    fd_apply_card_action_btn, fd_apply_action_btn,
)
from tabs._shared import current_operator_id
from ui_surface import fd_apply_table_palette, fd_refresh_surfaces, fd_sync_table_height

logger = logging.getLogger(__name__)


class MemberTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("MemberTab")
        l = QVBoxLayout(self); l.setSpacing(FD_SPACE_SM)
        l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)

        # ── 金线横栏：标题 + 操作按钮 ──
        btn_rf = QPushButton(i18n.t("btn_reload"))
        fd_apply_low_freq_btn(btn_rf)
        btn_rf.clicked.connect(self.refresh)

        btn_add = QPushButton(i18n.t("member_btn_add"))
        fd_apply_action_btn(btn_add, primary=True)
        btn_add.clicked.connect(self._add_member)

        l.addWidget(fd_section_bar(
            i18n.t("member_title"),
            action_widgets=[btn_rf, btn_add],
        ))

        # ── 搜索+筛选栏 ──
        search_bar = QHBoxLayout()
        search_bar.setSpacing(FD_SPACE_SM)
        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("FdCompactInput")
        self.txt_search.setPlaceholderText(i18n.t("member_search_ph"))
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)
        self.txt_search.textChanged.connect(lambda: self._search_timer.start(300))
        search_bar.addWidget(self.txt_search, 1)

        btn_pts = QPushButton(i18n.t("member_btn_points"))
        fd_apply_card_action_btn(btn_pts)
        btn_pts.clicked.connect(self._adjust_points)
        search_bar.addWidget(btn_pts)

        btn_lvl = QPushButton(i18n.t("member_btn_level"))
        fd_apply_card_action_btn(btn_lvl)
        btn_lvl.clicked.connect(self._change_level)
        search_bar.addWidget(btn_lvl)

        btn_detail = QPushButton(i18n.t("btn_detail"))
        fd_apply_card_action_btn(btn_detail)
        btn_detail.clicked.connect(self._view_detail)
        search_bar.addWidget(btn_detail)

        btn_history = QPushButton(i18n.t("btn_consumption_history"))
        fd_apply_low_freq_btn(btn_history)
        btn_history.clicked.connect(self._view_consumption)
        search_bar.addWidget(btn_history)

        btn_policy = QPushButton(i18n.t("member_btn_policy"))
        fd_apply_low_freq_btn(btn_policy)
        btn_policy.clicked.connect(self._edit_policy)
        search_bar.addWidget(btn_policy)

        l.addLayout(search_bar)

        # ── [sub-j] SolidCard 卡片包裹：圆角 10px + 1px panel_border + surface 实底
        # 替换原 ContentBox（gold_thread 左线 + radius 0），整体更精致符合"精品"定位
        content_box = QFrame()
        content_box.setObjectName("SolidCard")
        content_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cb_lay = QVBoxLayout(content_box)
        cb_lay.setContentsMargins(10, 10, 10, 10)
        cb_lay.setSpacing(FD_SPACE_SM)

        # ── 会员全宽列表 ──
        self.tbl = QTableWidget(0, 8)
        self.tbl.setObjectName("MemberTable")
        self.tbl.setHorizontalHeaderLabels([
            i18n.t("member_col_id"),
            i18n.t("staff_name"),
            i18n.t("member_col_phone"),
            i18n.t("member_col_level"),
            i18n.t("member_col_points"),
            i18n.t("member_col_birthday"),
            i18n.t("member_col_prefs"),
            i18n.t("member_col_reg"),
        ])
        mb_hdr = self.tbl.horizontalHeader()
        mb_hdr.setMinimumSectionSize(70)
        for c in (0, 2, 3, 4, 5, 6, 7):
            mb_hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        mb_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl.setAlternatingRowColors(False)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        cb_lay.addWidget(self.tbl)

        self.lbl_empty = QLabel(i18n.t("member_empty_text"))
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setObjectName("MemberEmptyLabel")
        self.lbl_empty.hide()
        cb_lay.addWidget(self.lbl_empty)

        # ── 等级说明 ──
        legend_row = QHBoxLayout()
        legend_row.setSpacing(FD_SPACE_SM)
        legend = QLabel(i18n.t("member_level_hint"))
        legend.setObjectName("MemberLevelHint")
        legend_row.addWidget(legend)
        for object_name, text in (
            ("MemberLevelBronze", "BRONZE"),
            ("MemberLevelSilver", "SILVER"),
            ("MemberLevelGold", "GOLD"),
            ("MemberLevelDiamond", "DIAMOND"),
            ("MemberLevelEnterprise", "ENTERPRISE"),
        ):
            badge = QLabel(text)
            badge.setObjectName(object_name)
            legend_row.addWidget(badge)
        legend_row.addStretch()
        cb_lay.addLayout(legend_row)
        l.addWidget(content_box, 1)
        fd_apply_table_palette(self.tbl)

        self.refresh()
        bus.theme_changed.connect(lambda _: fd_refresh_surfaces(self))

    def refresh(self):
        self._load_members("")

    def _load_members(self, keyword=""):
        self.tbl.setRowCount(0)
        try:
            if keyword:
                rows = db.execute(
                    "SELECT id, name, phone, level, points, COALESCE(birthday,''), "
                    "COALESCE(preferences,''), COALESCE(remark,''), created_at FROM members "
                    "WHERE name LIKE ? OR phone LIKE ? ORDER BY id DESC LIMIT 200",
                    (f"%{keyword}%", f"%{keyword}%")
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id, name, phone, level, points, COALESCE(birthday,''), "
                    "COALESCE(preferences,''), COALESCE(remark,''), created_at FROM members ORDER BY id DESC LIMIT 200"
                ).fetchall()
        except Exception:
            rows = []

        LEVEL_COLORS = {
            "BRONZE": _p("member_bronze", "#8A6A4A"),
            "SILVER": _p("member_silver", "#8E8B95"),
            "GOLD": _p("member_gold", "#B89968"),
            "DIAMOND": _p("member_diamond", "#3D3450"),
            "ENTERPRISE": _p("member_enterprise", "#1E3A5F"),
        }
        LEVEL_EMOJI = {"BRONZE": "🥉", "SILVER": "🥈", "GOLD": "🥇", "DIAMOND": "💎", "ENTERPRISE": "🏢"}

        for mid, name, phone, level, points, birthday, prefs, remark, created_at in rows:
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            self.tbl.setItem(r, 0, QTableWidgetItem(str(mid)))
            self.tbl.setItem(r, 1, QTableWidgetItem(str(name or "")))
            self.tbl.setItem(r, 2, QTableWidgetItem(str(phone or "")))
            lvl_text = f"{LEVEL_EMOJI.get(level,'')}{level}"
            lvl_item = QTableWidgetItem(lvl_text)
            lvl_item.setForeground(QColor(LEVEL_COLORS.get(level, _p("text_muted"))))
            self.tbl.setItem(r, 3, lvl_item)
            pts_item = QTableWidgetItem(str(points or 0))
            pts_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tbl.setItem(r, 4, pts_item)
            self.tbl.setItem(r, 5, QTableWidgetItem(str(birthday or "")[:10]))
            prefs_display = str(prefs or "")[:20] + ("…" if len(str(prefs or "")) > 20 else "")
            self.tbl.setItem(r, 6, QTableWidgetItem(prefs_display))
            self.tbl.setItem(r, 7, QTableWidgetItem(str(created_at or "")[:10]))

        # 空状态切换
        self.lbl_empty.setVisible(self.tbl.rowCount() == 0)
        fd_sync_table_height(self.tbl, min_rows=3, max_rows=14)

    def _do_search(self):
        self._load_members(self.txt_search.text().strip())

    def _add_member(self):
        from PySide6.QtWidgets import QDialog, QFormLayout
        d = QDialog(self); d.setWindowTitle(i18n.t("member_dialog_add_title")); style_dialog(d, size="compact")
        l = QVBoxLayout(d); l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN); l.setSpacing(FD_SPACE_SM)
        l.addWidget(build_dialog_header(i18n.t("member_dialog_add_title"), i18n.t("member_dialog_add_sub")))
        f = QFormLayout()
        txt_name = QLineEdit(); txt_name.setPlaceholderText(i18n.t("member_ph_name"))
        txt_phone = QLineEdit(); txt_phone.setPlaceholderText(i18n.t("member_ph_phone_unique"))
        cmb_level = QComboBox(); cmb_level.addItems(["BRONZE", "SILVER", "GOLD", "DIAMOND", "ENTERPRISE"])
        spn_pts = __import__('PySide6.QtWidgets', fromlist=['QSpinBox']).QSpinBox()
        spn_pts.setRange(0, 999999); spn_pts.setValue(0)
        txt_birthday = QLineEdit(); txt_birthday.setPlaceholderText(i18n.t("member_ph_birthday"))
        txt_prefs = QLineEdit(); txt_prefs.setPlaceholderText(i18n.t("member_ph_prefs"))
        txt_remark = QLineEdit(); txt_remark.setPlaceholderText(i18n.t("member_ph_remark"))
        f.addRow(i18n.t("staff_name") + ":", txt_name)
        f.addRow(i18n.t("member_col_phone") + ":", txt_phone)
        f.addRow(i18n.t("member_field_initial_level"), cmb_level)
        f.addRow(i18n.t("member_field_initial_pts"), spn_pts)
        f.addRow(i18n.t("label_birthday"), txt_birthday)
        f.addRow(i18n.t("label_prefs"), txt_prefs)
        f.addRow(i18n.t("label_remark"), txt_remark)
        btn_ok = QPushButton(i18n.t("staff_btn_save")); btn_ok.setObjectName("SolidPrimaryBtn"); btn_ok.clicked.connect(d.accept)
        f.addRow(btn_ok)
        l.addLayout(f)
        if d.exec():
            name = txt_name.text().strip(); phone = txt_phone.text().strip()
            if not name or not phone:
                show_warning(self, i18n.t("staff_err_incomplete"), i18n.t("member_err_name_phone")); return
            try:
                db.execute(
                    "INSERT INTO members (name, phone, level, points, birthday, preferences, remark) VALUES (?,?,?,?,?,?,?)",
                    (name, phone, cmb_level.currentText(), spn_pts.value(),
                     txt_birthday.text().strip(), txt_prefs.text().strip(), txt_remark.text().strip())
                )
                self.refresh()
                bus.show_success_overlay.emit(i18n.t("member_added").format(name))
            except Exception as e:
                show_warning(self, i18n.t("finance_register_failed"), i18n.t("member_err_dup").format(e))

    def _adjust_points(self):
        row = self.tbl.currentRow()
        if row < 0:
            show_warning(self, i18n.t("staff_err_incomplete"), i18n.t("member_err_select")); return
        mid = self.tbl.item(row, 0).text()
        name = self.tbl.item(row, 1).text()
        cur_pts = int(self.tbl.item(row, 4).text() or 0)

        from PySide6.QtWidgets import QDialog, QFormLayout, QSpinBox, QRadioButton, QButtonGroup
        d = QDialog(self); d.setWindowTitle(i18n.t("member_pts_win").format(name)); style_dialog(d, size="compact")
        l = QVBoxLayout(d); l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN); l.setSpacing(FD_SPACE_SM)
        l.addWidget(build_dialog_header(i18n.t("member_pts_header"), i18n.t("member_pts_current").format(cur_pts)))
        f = QFormLayout()
        rb_add = QRadioButton(i18n.t("member_pts_add")); rb_add.setChecked(True)
        rb_sub = QRadioButton(i18n.t("member_pts_sub"))
        grp = QButtonGroup(d); grp.addButton(rb_add); grp.addButton(rb_sub)
        rh = QHBoxLayout(); rh.addWidget(rb_add); rh.addWidget(rb_sub)
        spn = QSpinBox(); spn.setRange(1, 99999); spn.setValue(100)
        txt_reason = QLineEdit(); txt_reason.setPlaceholderText(i18n.t("member_pts_reason_ph"))
        f.addRow(i18n.t("member_pts_op"), rh)
        f.addRow(i18n.t("member_pts_qty"), spn)
        f.addRow(i18n.t("member_pts_reason"), txt_reason)
        btn_ok = QPushButton(i18n.t("member_pts_confirm")); btn_ok.setObjectName("SolidPrimaryBtn"); btn_ok.clicked.connect(d.accept)
        f.addRow(btn_ok)
        l.addLayout(f)
        if d.exec():
            delta = spn.value() if rb_add.isChecked() else -spn.value()
            new_pts = max(0, cur_pts + delta)
            try:
                db.execute("UPDATE members SET points=? WHERE id=?", (new_pts, mid))
                db.log_action(current_operator_id(), "MEMBER_POINTS", f"{name} pts {delta:+d} -> {new_pts}")
                self.refresh()
                bus.show_success_overlay.emit(i18n.t("member_pts_updated").format(name, new_pts))
            except Exception as e:
                show_warning(self, i18n.t("finance_register_failed"), str(e))

    def _change_level(self):
        row = self.tbl.currentRow()
        if row < 0:
            show_warning(self, i18n.t("staff_err_incomplete"), i18n.t("member_err_select")); return
        mid = self.tbl.item(row, 0).text()
        name = self.tbl.item(row, 1).text()
        cur_lvl = self.tbl.item(row, 3).text()

        from PySide6.QtWidgets import QDialog, QFormLayout
        d = QDialog(self); d.setWindowTitle(i18n.t("member_lvl_win").format(name)); style_dialog(d, size="compact")
        l = QVBoxLayout(d); l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN); l.setSpacing(FD_SPACE_SM)
        l.addWidget(build_dialog_header(i18n.t("member_lvl_header"), i18n.t("member_lvl_current").format(cur_lvl.strip())))
        f = QFormLayout()
        cmb = QComboBox(); cmb.addItems(["BRONZE", "SILVER", "GOLD", "DIAMOND", "ENTERPRISE"])
        idx = {"BRONZE":0,"SILVER":1,"GOLD":2,"DIAMOND":3,"ENTERPRISE":4}.get(cur_lvl.strip(), 0)
        cmb.setCurrentIndex(idx)
        f.addRow(i18n.t("member_field_new_level"), cmb)
        btn_ok = QPushButton(i18n.t("member_pts_confirm")); btn_ok.setObjectName("SolidPrimaryBtn"); btn_ok.clicked.connect(d.accept)
        f.addRow(btn_ok)
        l.addLayout(f)
        if d.exec():
            new_lvl = cmb.currentText()
            try:
                db.execute("UPDATE members SET level=? WHERE id=?", (new_lvl, mid))
                db.log_action(current_operator_id(), "MEMBER_LEVEL", f"{name} tier {cur_lvl}->{new_lvl}")
                self.refresh()
                bus.show_success_overlay.emit(i18n.t("member_lvl_updated").format(name, new_lvl))
            except Exception as e:
                show_warning(self, i18n.t("finance_register_failed"), str(e))

    def _view_detail(self):
        """查看/编辑会员详情：生日折扣、偏好标签、备注。"""
        row = self.tbl.currentRow()
        if row < 0:
            show_warning(self, i18n.t("staff_err_incomplete"), i18n.t("member_err_select")); return
        mid = self.tbl.item(row, 0).text()
        name = self.tbl.item(row, 1).text()
        try:
            info = db.execute(
                "SELECT phone, level, points, COALESCE(birthday,''), COALESCE(preferences,''), COALESCE(remark,'') FROM members WHERE id=?",
                (mid,),
            ).fetchone()
        except Exception:
            return
        if not info:
            return
        phone, level, points, birthday, prefs, remark = info

        # 生日折扣检查
        bday_info = db.calculate_birthday_discount(mid)

        d = QDialog(self); d.setWindowTitle(i18n.t("member_detail_title").format(name=name)); style_dialog(d, size="medium")
        lv = QVBoxLayout(d); lv.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN); lv.setSpacing(FD_SPACE_SM)
        lv.addWidget(build_dialog_header(i18n.t("member_detail_header").format(name=name), i18n.t("member_detail_sub").format(level=level, points=points)))
        f = QFormLayout()
        txt_prefs = QLineEdit(); txt_prefs.setText(prefs)
        txt_remark = QLineEdit(); txt_remark.setText(remark)
        txt_birthday = QLineEdit(); txt_birthday.setText(birthday); txt_birthday.setPlaceholderText("YYYY-MM-DD")
        f.addRow(i18n.t("label_prefs") + ":", txt_prefs)
        f.addRow(i18n.t("label_remark") + ":", txt_remark)
        f.addRow(i18n.t("label_birthday") + ":", txt_birthday)
        lv.addLayout(f)

        # 生日折扣信息
        bday_label = QLabel()
        if bday_info["is_birthday"]:
            bday_label.setText(i18n.t("member_bday_today").format(name=bday_info['name']))
            bday_label.setObjectName("MemberBdayToday")
        else:
            bday_label.setText(i18n.t("member_bday_other").format(birthday=birthday or i18n.t("member_not_set")))
            bday_label.setObjectName("MemberBdayOther")
        lv.addWidget(bday_label)

        btn_ok = QPushButton(i18n.t("btn_save")); btn_ok.setObjectName("SolidPrimaryBtn"); btn_ok.clicked.connect(d.accept)
        lv.addWidget(btn_ok)
        if d.exec():
            try:
                db.execute(
                    "UPDATE members SET preferences=?, remark=?, birthday=? WHERE id=?",
                    (txt_prefs.text().strip(), txt_remark.text().strip(), txt_birthday.text().strip(), mid),
                )
                db.log_action(current_operator_id(), "MEMBER_UPDATE", f"{name} detail updated")
                self.refresh()
                bus.show_success_overlay.emit(i18n.t("member_updated").format(name=name))
            except Exception as e:
                show_warning(self, i18n.t("finance_register_failed"), str(e))

    def _view_consumption(self):
        """查看会员消费历史。"""
        row = self.tbl.currentRow()
        if row < 0:
            show_warning(self, i18n.t("staff_err_incomplete"), i18n.t("member_err_select")); return
        mid = self.tbl.item(row, 0).text()
        name = self.tbl.item(row, 1).text()
        try:
            rows = db.execute(
                "SELECT room_id, amount, points_earned, COALESCE(checkin_date,''), COALESCE(checkout_date,''), created_at "
                "FROM member_consumption WHERE member_id=? ORDER BY id DESC LIMIT 50",
                (mid,),
            ).fetchall()
        except Exception:
            rows = []

        d = QDialog(self); d.setWindowTitle(i18n.t("member_consumption_title").format(name=name)); style_dialog(d, size="large")
        lv = QVBoxLayout(d); lv.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN); lv.setSpacing(FD_SPACE_SM)
        lv.addWidget(build_dialog_header(i18n.t("member_consumption_header").format(name=name), i18n.t("member_consumption_count").format(count=len(rows))))
        tbl = QTableWidget(max(len(rows), 1), 5)
        tbl.setHorizontalHeaderLabels([i18n.t("member_consumption_col_room"), i18n.t("member_consumption_col_amount"), i18n.t("member_consumption_col_points"), i18n.t("member_consumption_col_checkin"), i18n.t("member_consumption_col_time")])
        for r, (rid, amt, pts, cin, cout, ts) in enumerate(rows):
            tbl.setItem(r, 0, QTableWidgetItem(str(rid or "")))
            tbl.setItem(r, 1, QTableWidgetItem(f"{i18n.t('currency_symbol')}{float(amt or 0):.0f}"))
            tbl.setItem(r, 2, QTableWidgetItem(str(pts or 0)))
            tbl.setItem(r, 3, QTableWidgetItem(f"{str(cin or '')[:10]}→{str(cout or '')[:10]}"))
            tbl.setItem(r, 4, QTableWidgetItem(str(ts or "")[:16]))
        if not rows:
            tbl.setItem(0, 0, QTableWidgetItem(i18n.t("member_consumption_empty")))
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        from ui_surface import fd_apply_table_palette
        fd_apply_table_palette(tbl)
        lv.addWidget(tbl)
        btn_close = QPushButton(i18n.t("btn_close"))
        fd_apply_low_freq_btn(btn_close)
        btn_close.clicked.connect(d.accept)
        lv.addWidget(btn_close)
        d.exec()

    def _edit_policy(self):
        """会员策略：客人机器人的 5 个配置的统一编辑入口。"""
        from PySide6.QtWidgets import QFormLayout, QSpinBox, QDoubleSpinBox, QCheckBox, QPlainTextEdit
        d = QDialog(self)
        d.setWindowTitle(i18n.t("member_policy_title"))
        style_dialog(d, size="medium")
        outer = QVBoxLayout(d)
        outer.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)
        outer.setSpacing(FD_SPACE_SM)
        outer.addWidget(build_dialog_header(i18n.t("member_policy_title"), i18n.t("member_policy_sub")))

        form_box = QWidget()
        f = QFormLayout(form_box)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        f.setSpacing(FD_SPACE_SM)

        txt_welcome = QPlainTextEdit()
        txt_welcome.setPlaceholderText(i18n.t("member_policy_welcome_ph"))
        txt_welcome.setPlainText(db.get_config("bot_welcome_text") or "")
        txt_welcome.setMinimumHeight(80)
        f.addRow(i18n.t("member_policy_welcome"), txt_welcome)

        spn_bonus = QSpinBox(); spn_bonus.setRange(0, 9999)
        try:
            spn_bonus.setValue(int(db.get_config("checkin_bonus_points") or "0"))
        except Exception:
            spn_bonus.setValue(0)
        spn_bonus.setSuffix(" pts")
        f.addRow(i18n.t("member_policy_bonus"), spn_bonus)

        spn_loyalty = QDoubleSpinBox(); spn_loyalty.setRange(0.0, 100.0); spn_loyalty.setDecimals(2); spn_loyalty.setSingleStep(0.1)
        try:
            spn_loyalty.setValue(float(db.get_config("loyalty_points_rate") or "1.0"))
        except Exception:
            spn_loyalty.setValue(1.0)
        spn_loyalty.setSuffix(" pts/¥")
        f.addRow(i18n.t("member_policy_loyalty"), spn_loyalty)

        spn_cart = QSpinBox(); spn_cart.setRange(1, 999)
        try:
            spn_cart.setValue(int(db.get_config("max_cart_items") or "20"))
        except Exception:
            spn_cart.setValue(20)
        f.addRow(i18n.t("member_policy_max_cart"), spn_cart)

        chk_auto = QCheckBox(i18n.t("member_policy_auto_confirm"))
        chk_auto.setChecked((db.get_config("auto_confirm_order") or "0") == "1")
        f.addRow("", chk_auto)

        outer.addWidget(form_box)
        outer.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton(i18n.t("btn_cancel"))
        fd_apply_low_freq_btn(btn_cancel)
        btn_cancel.clicked.connect(d.reject)
        btn_ok = QPushButton(i18n.t("btn_save"))
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_ok.clicked.connect(d.accept)
        btn_row.addWidget(btn_cancel); btn_row.addWidget(btn_ok)
        outer.addLayout(btn_row)

        if d.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            db.set_config("bot_welcome_text", txt_welcome.toPlainText().strip())
            db.set_config("checkin_bonus_points", str(spn_bonus.value()))
            db.set_config("loyalty_points_rate", f"{spn_loyalty.value():.2f}")
            db.set_config("max_cart_items", str(spn_cart.value()))
            db.set_config("auto_confirm_order", "1" if chk_auto.isChecked() else "0")
            db.log_action(current_operator_id(), "MEMBER_POLICY_UPDATE", "5 keys")
            bus.show_success_overlay.emit(i18n.t("member_policy_saved"))
        except Exception as exc:
            show_warning(self, i18n.t("member_policy_title"), str(exc))
