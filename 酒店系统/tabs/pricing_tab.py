from __future__ import annotations

import logging
import json
import datetime
from money_utils import to_money, fmt_money
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QFormLayout, QDialog, QDoubleSpinBox, QSpinBox,
    QCheckBox, QPlainTextEdit, QDateEdit, QTabWidget,
    QAbstractItemView, QFrame,
)
from PySide6.QtCore import Qt, QDate, QDateTime
from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import (
    show_info, show_warning, show_error, style_dialog,
    build_dialog_header, ask_confirm, apply_money_table_item,
)
from design_tokens import _p
from frontdesk_ui import (
    fd_section_bar, FD_MARGIN, FD_SPACE_SM, fd_apply_low_freq_btn,
    fd_apply_card_action_btn, fd_apply_action_btn,
)
from ui_surface import fd_apply_content_box, fd_apply_table_palette, fd_refresh_surfaces, fd_sync_table_height

logger = logging.getLogger(__name__)


class PricingTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("PricingTab")
        l = QVBoxLayout(self); l.setSpacing(FD_SPACE_SM)
        l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)
        cur = i18n.t("currency_symbol")

        # ── 金线横栏：标题 + 操作按钮 ──
        btn_rf = QPushButton(i18n.t("btn_reload"))
        fd_apply_low_freq_btn(btn_rf)
        btn_rf.clicked.connect(self.refresh)

        l.addWidget(fd_section_bar(
            i18n.t("pricing_title"),
            action_widgets=[btn_rf],
        ))

        # ── 子标签页 ──
        sub = QTabWidget()
        sub.setObjectName("PricingSubTabs")
        sub.setDocumentMode(True)

        # ── 子标签页1：房型基础价格 ──
        w1 = QFrame(); w1.setObjectName("ContentBox"); v1 = QVBoxLayout(w1); v1.setSpacing(FD_SPACE_SM)
        v1.setContentsMargins(10, 10, 10, 10)

        btn_add_rt = QPushButton(i18n.t("pricing_btn_add_rt"))
        fd_apply_action_btn(btn_add_rt, primary=True)
        btn_add_rt.clicked.connect(self._add_room_type)

        btn_edit_rt = QPushButton(i18n.t("pricing_btn_edit_rt"))
        fd_apply_card_action_btn(btn_edit_rt)
        btn_edit_rt.clicked.connect(self._edit_room_type)

        v1.addWidget(fd_section_bar(
            i18n.t("pricing_tab_base"),
            action_widgets=[btn_add_rt, btn_edit_rt],
        ))

        self.tbl_rt = QTableWidget(0, 9)
        self.tbl_rt.setObjectName("PricingRoomTypeTable")
        self.tbl_rt.setHorizontalHeaderLabels([
            i18n.t("pricing_col_rt_id"),
            i18n.t("pricing_col_rt_name"),
            i18n.t("pricing_col_day").format(cur),
            i18n.t("pricing_col_hour").format(cur),
            i18n.t("pricing_col_dep").format(cur),
            i18n.t("pricing_col_walk").format(cur),
            i18n.t("pricing_col_contract").format(cur),
            i18n.t("pricing_col_member").format(cur),
            i18n.t("pricing_col_disc"),
        ])
        rt_hdr = self.tbl_rt.horizontalHeader()
        rt_hdr.setMinimumSectionSize(70)
        rt_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        rt_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in range(2, 8):
            rt_hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        rt_hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_rt.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tbl_rt.setAlternatingRowColors(False)
        self.tbl_rt.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_rt.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        v1.addWidget(self.tbl_rt)
        sub.addTab(w1, i18n.t("pricing_tab_base"))

        # ── 子标签页2：节假日加价 ──
        w2 = QFrame(); w2.setObjectName("ContentBox"); v2 = QVBoxLayout(w2); v2.setSpacing(FD_SPACE_SM)
        v2.setContentsMargins(10, 10, 10, 10)

        btn_add_hp = QPushButton(i18n.t("pricing_btn_add_holiday"))
        fd_apply_action_btn(btn_add_hp, primary=True)
        btn_add_hp.clicked.connect(self._add_holiday)

        btn_del_hp = QPushButton(i18n.t("pricing_btn_del"))
        fd_apply_low_freq_btn(btn_del_hp)
        btn_del_hp.clicked.connect(self._del_holiday)

        v2.addWidget(fd_section_bar(
            i18n.t("pricing_tab_holiday"),
            action_widgets=[btn_add_hp, btn_del_hp],
        ))

        self.tbl_hp = QTableWidget(0, 5)
        self.tbl_hp.setObjectName("PricingHolidayTable")
        self.tbl_hp.setHorizontalHeaderLabels([i18n.t(f"pricing_hp_col_{i}") for i in range(5)])
        hp_hdr = self.tbl_hp.horizontalHeader()
        hp_hdr.setMinimumSectionSize(70)
        for c in (1, 2, 3, 4):
            hp_hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hp_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl_hp.setAlternatingRowColors(False)
        self.tbl_hp.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_hp.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        v2.addWidget(self.tbl_hp)
        sub.addTab(w2, i18n.t("pricing_tab_holiday"))

        # ── 子标签页3：协议价 ──
        w3 = QFrame(); w3.setObjectName("ContentBox"); v3 = QVBoxLayout(w3); v3.setSpacing(FD_SPACE_SM)
        v3.setContentsMargins(10, 10, 10, 10)

        btn_add_gr = QPushButton(i18n.t("pricing_btn_add_group"))
        fd_apply_action_btn(btn_add_gr, primary=True)
        btn_add_gr.clicked.connect(self._add_group_rate)

        btn_del_gr = QPushButton(i18n.t("pricing_btn_del"))
        fd_apply_low_freq_btn(btn_del_gr)
        btn_del_gr.clicked.connect(self._del_group_rate)

        v3.addWidget(fd_section_bar(
            i18n.t("pricing_tab_group"),
            action_widgets=[btn_add_gr, btn_del_gr],
        ))

        self.tbl_gr = QTableWidget(0, 5)
        self.tbl_gr.setObjectName("PricingGroupRateTable")
        self.tbl_gr.setHorizontalHeaderLabels([i18n.t(f"pricing_gr_col_{i}").format(cur) if i == 2 else i18n.t(f"pricing_gr_col_{i}") for i in range(5)])
        gr_hdr = self.tbl_gr.horizontalHeader()
        gr_hdr.setMinimumSectionSize(70)
        for c in (1, 3, 4):
            gr_hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        gr_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        gr_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl_gr.setAlternatingRowColors(False)
        self.tbl_gr.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_gr.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        v3.addWidget(self.tbl_gr)
        sub.addTab(w3, i18n.t("pricing_tab_group"))

        for frame in (w1, w2, w3):
            fd_apply_content_box(frame)
        for tbl in (self.tbl_rt, self.tbl_hp, self.tbl_gr):
            fd_apply_table_palette(tbl)

        l.addWidget(sub, stretch=1)
        self._sub = sub
        self._sub.currentChanged.connect(self._on_sub_change)
        self.refresh()
        bus.theme_changed.connect(lambda _: fd_refresh_surfaces(self))

    def _on_sub_change(self, idx):
        self.refresh()

    def refresh(self):
        self._load_room_types()
        self._load_holidays()
        self._load_group_rates()
        fd_sync_table_height(self.tbl_rt, min_rows=3, max_rows=14)
        fd_sync_table_height(self.tbl_hp, min_rows=3, max_rows=12)
        fd_sync_table_height(self.tbl_gr, min_rows=3, max_rows=12)

    def _load_room_types(self):
        self.tbl_rt.setRowCount(0)
        try:
            rows = db.execute(
                "SELECT type_id, type_name, base_price, hourly_price, default_deposit, "
                "price_walk_in, price_contract, price_member "
                "FROM room_type_templates ORDER BY type_name"
            ).fetchall()
        except Exception:
            rows = []
        cur = i18n.t("currency_symbol")
        # 读取会员折扣（从 pricing_rules 表）
        disc_map = {}
        try:
            pr = db.execute(
                "SELECT room_type, discount_silver, discount_gold, discount_diamond FROM pricing_rules"
            ).fetchall()
            for rt, ds, dg, dd in pr:
                disc_map[rt] = i18n.t("pricing_disc_fmt").format(int(ds * 100), int(dg * 100), int(dd * 100))
        except Exception:
            pass
        for row in rows:
            tid, tname, base, hourly = row[0], row[1], row[2], row[3]
            dep_col = row[4] if len(row) > 4 else None
            pw, pc, pm = (row[5], row[6], row[7]) if len(row) > 7 else (None, None, None)
            r = self.tbl_rt.rowCount(); self.tbl_rt.insertRow(r)
            self.tbl_rt.setItem(r, 0, QTableWidgetItem(str(tid)))
            self.tbl_rt.setItem(r, 1, QTableWidgetItem(str(tname or "")))

            for col, val in (
                (2, fmt_money(to_money(base), cur)),
                (3, fmt_money(to_money(hourly), cur)),
            ):
                it = apply_money_table_item(QTableWidgetItem(val))
                self.tbl_rt.setItem(r, col, it)
            eff_dep = db.get_deposit_for_room_type(str(tid))
            dep_it = apply_money_table_item(
                QTableWidgetItem(f"{int(eff_dep or 0)}" + (" *" if dep_col is None else ""))
            )
            self.tbl_rt.setItem(r, 4, dep_it)
            for j, v in enumerate((pw, pc, pm)):
                cell = "—" if v is None else fmt_money(to_money(v), cur)
                it = QTableWidgetItem(cell)
                if v is not None:
                    apply_money_table_item(it)
                self.tbl_rt.setItem(r, 5 + j, it)
            self.tbl_rt.setItem(r, 8, QTableWidgetItem(disc_map.get(tname, i18n.t("pricing_disc_default"))))

    def _load_holidays(self):
        self.tbl_hp.setRowCount(0)
        try:
            rows = db.execute(
                "SELECT id, label, date_start, date_end, price_multiplier, room_type FROM holiday_pricing ORDER BY date_start"
            ).fetchall()
        except Exception:
            rows = []
        for hid, label, ds, de, mult, rt in rows:
            r = self.tbl_hp.rowCount(); self.tbl_hp.insertRow(r)
            self.tbl_hp.setItem(r, 0, QTableWidgetItem(str(label or "")))
            self.tbl_hp.setItem(r, 1, QTableWidgetItem(str(ds or "")))
            self.tbl_hp.setItem(r, 2, QTableWidgetItem(str(de or "")))
            self.tbl_hp.setItem(r, 3, QTableWidgetItem(f"×{mult:.1f}"))
            self.tbl_hp.setItem(r, 4, QTableWidgetItem(str(rt or "*")))
            self.tbl_hp.item(r, 0).setData(Qt.ItemDataRole.UserRole, hid)

    def _load_group_rates(self):
        self.tbl_gr.setRowCount(0)
        cur = i18n.t("currency_symbol")
        try:
            rows = db.execute(
                "SELECT id, group_name, room_type, negotiated_price, min_rooms, contact FROM group_rates ORDER BY group_name"
            ).fetchall()
        except Exception:
            rows = []
        for gid, gname, rt, price, minr, contact in rows:
            r = self.tbl_gr.rowCount(); self.tbl_gr.insertRow(r)
            self.tbl_gr.setItem(r, 0, QTableWidgetItem(str(gname or "")))
            self.tbl_gr.setItem(r, 1, QTableWidgetItem(str(rt or "*")))
            self.tbl_gr.setItem(r, 2, QTableWidgetItem(fmt_money(to_money(price), cur)))
            self.tbl_gr.setItem(r, 3, QTableWidgetItem(str(minr or 1)))
            self.tbl_gr.setItem(r, 4, QTableWidgetItem(str(contact or "")))
            self.tbl_gr.item(r, 0).setData(Qt.ItemDataRole.UserRole, gid)

    def _add_room_type(self):
        cur = i18n.t("currency_symbol")
        d = QDialog(self); d.setWindowTitle(i18n.t("pricing_dialog_rt_add_title")); style_dialog(d, size="small")
        l = QVBoxLayout(d); l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN); l.setSpacing(FD_SPACE_MD)
        l.addWidget(build_dialog_header(i18n.t("pricing_dialog_rt_add_title"), i18n.t("pricing_dialog_rt_add_sub")))

        # ── 基础定价 ──
        sec1 = QLabel(i18n.t("pricing_section_basic"))
        sec1.setObjectName("FdSectionTitle")
        l.addWidget(sec1)
        f1 = QFormLayout()
        f1.setSpacing(FD_SPACE_SM)
        txt_id = QLineEdit(); txt_id.setPlaceholderText(i18n.t("pricing_ph_rt_id"))
        txt_name = QLineEdit(); txt_name.setPlaceholderText(i18n.t("pricing_ph_rt_name"))
        spn_base = QDoubleSpinBox(); spn_base.setRange(0, 99999); spn_base.setValue(200); spn_base.setPrefix(cur)
        spn_hourly = QDoubleSpinBox(); spn_hourly.setRange(0, 9999); spn_hourly.setValue(50); spn_hourly.setPrefix(cur)
        spn_dep = QDoubleSpinBox(); spn_dep.setRange(0, 999999); spn_dep.setDecimals(0)
        spn_dep.setValue(db.get_config_float("default_deposit", 50)); spn_dep.setPrefix(cur)
        f1.addRow(i18n.t("pricing_field_rt_id"), txt_id)
        f1.addRow(i18n.t("pricing_field_rt_name"), txt_name)
        f1.addRow(i18n.t("pricing_field_day_rate"), spn_base)
        f1.addRow(i18n.t("pricing_field_hour_rate"), spn_hourly)
        f1.addRow(i18n.t("pricing_field_dep"), spn_dep)
        l.addLayout(f1)

        # ── 派生定价 ──
        sec2 = QLabel(i18n.t("pricing_section_derived"))
        sec2.setObjectName("FdSectionTitle")
        l.addWidget(sec2)
        f2 = QFormLayout()
        f2.setSpacing(FD_SPACE_SM)
        spn_silver = QDoubleSpinBox(); spn_silver.setRange(0.5, 1.0); spn_silver.setValue(0.95); spn_silver.setSingleStep(0.05)
        spn_gold = QDoubleSpinBox(); spn_gold.setRange(0.5, 1.0); spn_gold.setValue(0.90); spn_gold.setSingleStep(0.05)
        spn_diamond = QDoubleSpinBox(); spn_diamond.setRange(0.5, 1.0); spn_diamond.setValue(0.80); spn_diamond.setSingleStep(0.05)
        spn_walkin = QDoubleSpinBox(); spn_walkin.setRange(0, 99999); spn_walkin.setPrefix(cur)
        spn_contract = QDoubleSpinBox(); spn_contract.setRange(0, 99999); spn_contract.setPrefix(cur)
        spn_member = QDoubleSpinBox(); spn_member.setRange(0, 99999); spn_member.setPrefix(cur)
        f2.addRow(i18n.t("pricing_field_walkin"), spn_walkin)
        f2.addRow(i18n.t("pricing_field_contract"), spn_contract)
        f2.addRow(i18n.t("pricing_field_member"), spn_member)
        f2.addRow(i18n.t("pricing_field_disc_silver"), spn_silver)
        f2.addRow(i18n.t("pricing_field_disc_gold"), spn_gold)
        f2.addRow(i18n.t("pricing_field_disc_diamond"), spn_diamond)
        l.addLayout(f2)

        btn_ok = QPushButton(i18n.t("staff_btn_save")); btn_ok.setObjectName("SolidPrimaryBtn"); btn_ok.clicked.connect(d.accept)
        l.addSpacing(FD_SPACE_SM)
        l.addWidget(btn_ok, alignment=Qt.AlignmentFlag.AlignRight)
        if d.exec():
            tid = txt_id.text().strip(); tname = txt_name.text().strip()
            if not tid or not tname:
                show_warning(self, i18n.t("staff_err_incomplete"), i18n.t("pricing_err_rt_required")); return
            try:
                db.execute(
                    "INSERT OR REPLACE INTO room_type_templates "
                    "(type_id, type_name, base_price, hourly_price, default_deposit, price_walk_in, price_contract, price_member) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (tid, tname, spn_base.value(), spn_hourly.value(), spn_dep.value(), spn_walkin.value() or None, spn_contract.value() or None, spn_member.value() or None),
                )
                db.execute(
                    "INSERT OR REPLACE INTO pricing_rules (room_type, base_price, hourly_price, discount_silver, discount_gold, discount_diamond) VALUES (?,?,?,?,?,?)",
                    (tname, spn_base.value(), spn_hourly.value(), spn_silver.value(), spn_gold.value(), spn_diamond.value())
                )
                self.refresh()
                bus.show_success_overlay.emit(i18n.t("pricing_saved_rt").format(tname))
            except Exception as e:
                show_warning(self, i18n.t("finance_register_failed"), str(e))

    def _edit_room_type(self):
        row = self.tbl_rt.currentRow()
        if row < 0:
            show_warning(self, i18n.t("staff_err_incomplete"), i18n.t("pricing_select_rt_first")); return
        tid = self.tbl_rt.item(row, 0).text()
        tname = self.tbl_rt.item(row, 1).text()
        cur = i18n.t("currency_symbol")
        base_str = self.tbl_rt.item(row, 2).text().replace(cur, "").replace("¥", "").strip()
        hourly_str = self.tbl_rt.item(row, 3).text().replace(cur, "").replace("¥", "").strip()
        try:
            base = to_money(base_str); hourly = to_money(hourly_str)
        except Exception:
            base = 200; hourly = 50

        d = QDialog(self); d.setWindowTitle(i18n.t("pricing_edit_rt_title").format(tname)); style_dialog(d, size="medium")
        l = QVBoxLayout(d); l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN); l.setSpacing(FD_SPACE_MD)
        l.addWidget(build_dialog_header(i18n.t("pricing_edit_rt_title").format(tname), i18n.t("pricing_edit_rt_sub")))

        # ── 基础定价 ──
        sec1 = QLabel(i18n.t("pricing_section_basic"))
        sec1.setObjectName("FdSectionTitle")
        l.addWidget(sec1)
        f1 = QFormLayout()
        f1.setSpacing(FD_SPACE_SM)
        spn_base = QDoubleSpinBox(); spn_base.setRange(0, 99999); spn_base.setValue(base); spn_base.setPrefix(cur)
        spn_hourly = QDoubleSpinBox(); spn_hourly.setRange(0, 9999); spn_hourly.setValue(hourly); spn_hourly.setPrefix(cur)
        dep_val = db.get_deposit_for_room_type(tid)
        spn_dep = QDoubleSpinBox(); spn_dep.setRange(0, 999999); spn_dep.setDecimals(0)
        spn_dep.setValue(dep_val); spn_dep.setPrefix(cur)
        f1.addRow(i18n.t("pricing_field_day_rate"), spn_base)
        f1.addRow(i18n.t("pricing_field_hour_rate"), spn_hourly)
        f1.addRow(i18n.t("pricing_field_dep"), spn_dep)
        l.addLayout(f1)

        # ── 派生定价 ──
        sec2 = QLabel(i18n.t("pricing_section_derived"))
        sec2.setObjectName("FdSectionTitle")
        l.addWidget(sec2)
        f2 = QFormLayout()
        f2.setSpacing(FD_SPACE_SM)
        # 读取现有折扣
        try:
            pr = db.execute("SELECT discount_silver, discount_gold, discount_diamond FROM pricing_rules WHERE room_type=?", (tname,)).fetchone()
            ds, dg, dd = (pr[0], pr[1], pr[2]) if pr else (0.95, 0.90, 0.80)
        except Exception as e:
            show_warning(self, i18n.t("dlg_error"), str(e))
            return
        spn_silver = QDoubleSpinBox(); spn_silver.setRange(0.5, 1.0); spn_silver.setValue(ds); spn_silver.setSingleStep(0.05)
        spn_gold = QDoubleSpinBox(); spn_gold.setRange(0.5, 1.0); spn_gold.setValue(dg); spn_gold.setSingleStep(0.05)
        spn_diamond = QDoubleSpinBox(); spn_diamond.setRange(0.5, 1.0); spn_diamond.setValue(dd); spn_diamond.setSingleStep(0.05)
        try:
            pr_row = db.execute(
                "SELECT price_walk_in, price_contract, price_member FROM room_type_templates WHERE type_id=?",
                (tid,),
            ).fetchone()
            pw, pc, pm = (pr_row[0], pr_row[1], pr_row[2]) if pr_row else (None, None, None)
        except Exception as e:
            show_warning(self, i18n.t("dlg_error"), str(e))
            return
        chk_walk = QCheckBox(i18n.t("pricing_chk_use_walk"))
        chk_walk.setChecked(pw is not None)
        spn_walk = QDoubleSpinBox(); spn_walk.setRange(0, 99999); spn_walk.setPrefix(cur)
        spn_walk.setValue(float(to_money(pw)) if pw is not None else float(base))
        chk_contract = QCheckBox(i18n.t("pricing_chk_use_contract"))
        chk_contract.setChecked(pc is not None)
        spn_contract = QDoubleSpinBox(); spn_contract.setRange(0, 99999); spn_contract.setPrefix(cur)
        spn_contract.setValue(float(to_money(pc)) if pc is not None else float(base))
        chk_member = QCheckBox(i18n.t("pricing_chk_use_member"))
        chk_member.setChecked(pm is not None)
        spn_member = QDoubleSpinBox(); spn_member.setRange(0, 99999); spn_member.setPrefix(cur)
        spn_member.setValue(float(to_money(pm)) if pm is not None else float(base))
        f2.addRow(chk_walk, spn_walk)
        f2.addRow(chk_contract, spn_contract)
        f2.addRow(chk_member, spn_member)
        f2.addRow(i18n.t("pricing_field_disc_silver"), spn_silver)
        f2.addRow(i18n.t("pricing_field_disc_gold"), spn_gold)
        f2.addRow(i18n.t("pricing_field_disc_diamond"), spn_diamond)
        l.addLayout(f2)

        # ── 客房配置 ──
        sec3 = QLabel(i18n.t("pricing_section_config"))
        sec3.setObjectName("FdSectionTitle")
        l.addWidget(sec3)
        f3 = QFormLayout()
        f3.setSpacing(FD_SPACE_SM)
        try:
            tpl_json = db.execute(
                "SELECT consumables_json, hk_consumables_deep_json FROM room_type_templates WHERE type_id=?",
                (tid,),
            ).fetchone()
            raw_c, raw_d = (tpl_json[0], tpl_json[1]) if tpl_json else ("{}", "")
        except Exception as e:
            show_warning(self, i18n.t("dlg_error"), str(e))
            return
        txt_cons = QPlainTextEdit()
        txt_cons.setPlainText(raw_c or "{}")
        txt_cons.setMaximumHeight(72)
        txt_deep = QPlainTextEdit()
        txt_deep.setPlainText(raw_d or "")
        txt_deep.setMaximumHeight(72)
        txt_deep.setPlaceholderText(i18n.t("settings_ph_consumables_deep"))
        f3.addRow(i18n.t("settings_label_consumables"), txt_cons)
        f3.addRow(i18n.t("settings_label_consumables_deep"), txt_deep)
        l.addLayout(f3)

        btn_ok = QPushButton(i18n.t("pricing_btn_save_edit")); btn_ok.setObjectName("SolidPrimaryBtn"); btn_ok.clicked.connect(d.accept)
        l.addSpacing(FD_SPACE_SM)
        l.addWidget(btn_ok, alignment=Qt.AlignmentFlag.AlignRight)
        if d.exec():
            try:
                std_s = txt_cons.toPlainText().strip() or "{}"
                deep_s = txt_deep.toPlainText().strip()
                try:
                    sc = json.loads(std_s)
                    if not isinstance(sc, dict):
                        raise ValueError("consumables must be object")
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    show_warning(self, i18n.t("finance_register_failed"), str(e))
                    return
                if deep_s:
                    try:
                        sd = json.loads(deep_s)
                        if not isinstance(sd, dict):
                            raise ValueError("deep template must be object")
                    except (json.JSONDecodeError, TypeError, ValueError) as e:
                        show_warning(self, i18n.t("finance_register_failed"), str(e))
                        return
                db.execute(
                    "UPDATE room_type_templates SET base_price=?, hourly_price=?, default_deposit=?, "
                    "price_walk_in=?, price_contract=?, price_member=?, consumables_json=?, hk_consumables_deep_json=? WHERE type_id=?",
                    (
                        spn_base.value(),
                        spn_hourly.value(),
                        spn_dep.value(),
                        spn_walk.value() if chk_walk.isChecked() else None,
                        spn_contract.value() if chk_contract.isChecked() else None,
                        spn_member.value() if chk_member.isChecked() else None,
                        std_s,
                        deep_s or None,
                        tid,
                    ),
                )
                db.execute(
                    "INSERT OR REPLACE INTO pricing_rules (room_type, base_price, hourly_price, discount_silver, discount_gold, discount_diamond) VALUES (?,?,?,?,?,?)",
                    (tname, spn_base.value(), spn_hourly.value(), spn_silver.value(), spn_gold.value(), spn_diamond.value())
                )
                self.refresh()
                bus.show_success_overlay.emit(i18n.t("pricing_saved_price").format(tname))
            except Exception as e:
                show_warning(self, i18n.t("finance_register_failed"), str(e))

    def _add_holiday(self):
        d = QDialog(self); d.setWindowTitle(i18n.t("pricing_holiday_win")); style_dialog(d, size="compact")
        l = QVBoxLayout(d); l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN); l.setSpacing(FD_SPACE_SM)
        l.addWidget(build_dialog_header(i18n.t("pricing_holiday_win"), i18n.t("pricing_holiday_sub")))
        f = QFormLayout()
        txt_label = QLineEdit(); txt_label.setPlaceholderText(i18n.t("pricing_holiday_ph"))
        de_start = QDateEdit(); de_start.setCalendarPopup(True); de_start.setDate(QDate.currentDate())
        de_end = QDateEdit(); de_end.setCalendarPopup(True); de_end.setDate(QDate.currentDate().addDays(7))
        spn_mult = QDoubleSpinBox(); spn_mult.setRange(1.0, 5.0); spn_mult.setValue(1.5); spn_mult.setSingleStep(0.1); spn_mult.setSuffix(i18n.t("pricing_suffix_mult"))
        cmb_rt = QComboBox(); cmb_rt.addItem(i18n.t("pricing_combo_all_rt"), "*")
        try:
            rts = db.execute("SELECT type_name FROM room_type_templates ORDER BY type_name").fetchall()
            for (rtn,) in rts: cmb_rt.addItem(rtn, rtn)
        except Exception:
            pass
        f.addRow(i18n.t("pricing_field_holiday_name"), txt_label)
        f.addRow(i18n.t("pricing_field_start"), de_start)
        f.addRow(i18n.t("pricing_field_end"), de_end)
        f.addRow(i18n.t("pricing_field_mult"), spn_mult)
        f.addRow(i18n.t("pricing_field_apply_rt"), cmb_rt)
        btn_ok = QPushButton(i18n.t("staff_btn_save")); btn_ok.setObjectName("SolidPrimaryBtn"); btn_ok.clicked.connect(d.accept)
        f.addRow(btn_ok)
        l.addLayout(f)
        if d.exec():
            label = txt_label.text().strip() or i18n.t("pricing_holiday_default_name")
            try:
                db.execute(
                    "INSERT INTO holiday_pricing (label, date_start, date_end, price_multiplier, room_type) VALUES (?,?,?,?,?)",
                    (label, de_start.date().toString("yyyy-MM-dd"), de_end.date().toString("yyyy-MM-dd"),
                     spn_mult.value(), cmb_rt.currentData())
                )
                self.refresh()
                bus.show_success_overlay.emit(i18n.t("pricing_holiday_added").format(label))
            except Exception as e:
                show_warning(self, i18n.t("finance_register_failed"), str(e))

    def _del_holiday(self):
        row = self.tbl_hp.currentRow()
        if row < 0:
            show_warning(self, i18n.t("staff_err_incomplete"), i18n.t("pricing_select_holiday")); return
        hid = self.tbl_hp.item(row, 0).data(Qt.ItemDataRole.UserRole)
        label = self.tbl_hp.item(row, 0).text()
        if ask_confirm(self, i18n.t("pricing_confirm_del"), i18n.t("pricing_confirm_del_holiday").format(label)):
            try:
                db.execute("DELETE FROM holiday_pricing WHERE id=?", (hid,))
                self.refresh()
            except Exception as e:
                show_warning(self, i18n.t("dlg_error"), str(e))

    def _add_group_rate(self):
        cur = i18n.t("currency_symbol")
        d = QDialog(self); d.setWindowTitle(i18n.t("pricing_group_win")); style_dialog(d, size="compact")
        l = QVBoxLayout(d); l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN); l.setSpacing(FD_SPACE_SM)
        l.addWidget(build_dialog_header(i18n.t("pricing_group_sub"), i18n.t("pricing_group_sub2")))
        f = QFormLayout()
        txt_name = QLineEdit(); txt_name.setPlaceholderText(i18n.t("pricing_ph_group_name"))
        cmb_rt = QComboBox(); cmb_rt.addItem(i18n.t("pricing_combo_all_rt"), "*")
        try:
            rts = db.execute("SELECT type_name FROM room_type_templates ORDER BY type_name").fetchall()
            for (rtn,) in rts: cmb_rt.addItem(rtn, rtn)
        except Exception:
            pass
        spn_price = QDoubleSpinBox(); spn_price.setRange(0, 99999); spn_price.setValue(180); spn_price.setPrefix(cur)
        spn_min = QSpinBox(); spn_min.setRange(1, 999); spn_min.setValue(1); spn_min.setSuffix(i18n.t("pricing_suffix_min_rooms"))
        txt_contact = QLineEdit(); txt_contact.setPlaceholderText(i18n.t("pricing_ph_contact"))
        f.addRow(i18n.t("pricing_field_group_name"), txt_name)
        f.addRow(i18n.t("pricing_field_apply_rt"), cmb_rt)
        f.addRow(i18n.t("pricing_field_neg_price"), spn_price)
        f.addRow(i18n.t("pricing_field_min_rooms"), spn_min)
        f.addRow(i18n.t("pricing_field_contact"), txt_contact)
        btn_ok = QPushButton(i18n.t("staff_btn_save")); btn_ok.setObjectName("SolidPrimaryBtn"); btn_ok.clicked.connect(d.accept)
        f.addRow(btn_ok)
        l.addLayout(f)
        if d.exec():
            gname = txt_name.text().strip()
            if not gname:
                show_warning(self, i18n.t("staff_err_incomplete"), i18n.t("pricing_err_group_name")); return
            try:
                db.execute(
                    "INSERT INTO group_rates (group_name, room_type, negotiated_price, min_rooms, contact) VALUES (?,?,?,?,?)",
                    (gname, cmb_rt.currentData(), spn_price.value(), spn_min.value(), txt_contact.text().strip())
                )
                self.refresh()
                bus.show_success_overlay.emit(i18n.t("pricing_group_added").format(gname))
            except Exception as e:
                show_warning(self, i18n.t("finance_register_failed"), str(e))

    def _del_group_rate(self):
        row = self.tbl_gr.currentRow()
        if row < 0:
            show_warning(self, i18n.t("staff_err_incomplete"), i18n.t("pricing_select_group")); return
        gid = self.tbl_gr.item(row, 0).data(Qt.ItemDataRole.UserRole)
        gname = self.tbl_gr.item(row, 0).text()
        if ask_confirm(self, i18n.t("pricing_confirm_del"), i18n.t("pricing_confirm_del_group").format(gname)):
            try:
                db.execute("DELETE FROM group_rates WHERE id=?", (gid,))
                self.refresh()
            except Exception as e:
                show_warning(self, i18n.t("dlg_error"), str(e))
