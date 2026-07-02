"""
system_console_tab.py — 系统控制台
===================================
内嵌工作区标签页，替代设置对话框弹窗模式。
左侧：分类导航树（带搜索）+ 右侧：QStackedWidget 内容区
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QFormLayout, QComboBox, QGroupBox, QCheckBox,
    QSpinBox, QDoubleSpinBox, QTimeEdit, QScrollArea, QFrame,
    QTreeWidget, QTreeWidgetItem, QStackedWidget,
)
from PySide6.QtCore import QTime

from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import show_info, show_warning
from design_tokens import _p
from frontdesk_ui import FD_MARGIN, FD_SPACE_MD
from ui_surface import fd_apply_scroll_area, fd_apply_settings_page, fd_apply_page_tab_root, fd_refresh_surfaces


def _group(title=""):
    g = QGroupBox(title)
    from ui_surface import fd_apply_settings_groupbox
    fd_apply_settings_groupbox(g)
    return g


class SystemConsoleTab(QWidget):

    def __init__(self):
        super().__init__()
        self.setObjectName("SystemConsolePage")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)
        lay.setSpacing(FD_SPACE_MD)

        # 搜索
        self._search = QLineEdit()
        self._search.setPlaceholderText(i18n.t("search_settings_ph"))
        self._search.setObjectName("ConsoleSearchInput")
        self._search.setMinimumHeight(36)
        self._search.textChanged.connect(self._on_search)
        lay.addWidget(self._search)

        body = QHBoxLayout()
        body.setSpacing(0)
        self._nav = QTreeWidget()
        self._nav.setObjectName("SettingsNavTree")
        self._nav.setHeaderHidden(True)
        self._nav.setMinimumWidth(180)
        self._nav.setMaximumWidth(280)
        self._nav.setIndentation(14)
        self._nav.setAnimated(True)
        self._nav.setRootIsDecorated(True)
        self._stack = QStackedWidget()
        self._stack.setObjectName("ConsoleSettingsStack")
        from ui_surface import fd_apply_panel_container
        fd_apply_panel_container(self._nav, fallback_name="SettingsNavTree")
        fd_apply_panel_container(self._stack, fallback_name="ConsoleSettingsStack")
        body.addWidget(self._nav)
        body.addWidget(self._stack, 1)
        lay.addLayout(body, 1)

        btn = QPushButton(i18n.t("btn_save_all_settings"))
        btn.setObjectName("SolidPrimaryBtn")
        btn.setMinimumHeight(36)
        btn.clicked.connect(self._save)
        br = QHBoxLayout()
        br.addStretch()
        br.addWidget(btn)
        lay.addLayout(br)

        self._all_leaves = []
        self._build_all()
        self._load()
        self._select_first()

        from ui_surface import fd_connect_theme_refresh
        fd_connect_theme_refresh(self)
        fd_apply_page_tab_root(self)
        fd_refresh_surfaces(self)

    def _refresh_theme_styles(self) -> None:
        """换主题 — 样式由 workspace_dock.fd_refresh_surfaces + base.qss 负责，勿 inline 覆盖。"""
        pass

    def _add_page(self, w: QWidget) -> int:
        fd_apply_settings_page(w)
        s = QScrollArea()
        s.setWidgetResizable(True)
        s.setFrameShape(QScrollArea.Shape.NoFrame)
        s.setWidget(w)
        fd_apply_scroll_area(s, bg_key="surface")
        idx = self._stack.count()
        self._stack.addWidget(s)
        return idx

    def _add_grp(self, text: str) -> QTreeWidgetItem:
        n = QTreeWidgetItem(self._nav)
        n.setText(0, text)
        n.setFlags(Qt.ItemFlag.ItemIsEnabled)
        f = n.font(0)
        f.setBold(True)
        n.setFont(0, f)
        n.setExpanded(True)
        return n

    def _add_leaf(self, text: str, idx: int, parent: QTreeWidgetItem):
        n = QTreeWidgetItem(parent)
        n.setText(0, text)
        n.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        n.setData(0, Qt.ItemDataRole.UserRole, idx)
        self._all_leaves.append(n)
        return n

    def _on_nav(self, item: QTreeWidgetItem, _c):
        v = item.data(0, Qt.ItemDataRole.UserRole)
        if v is not None:
            self._stack.setCurrentIndex(int(v))

    def _select_first(self):
        for i in range(self._nav.topLevelItemCount()):
            g = self._nav.topLevelItem(i)
            if g.isHidden():
                continue
            for j in range(g.childCount()):
                c = g.child(j)
                if c.isHidden():
                    continue
                self._nav.setCurrentItem(c)
                self._on_nav(c, 0)
                return

    def _on_search(self, txt):
        q = (txt or "").strip().lower()
        for i in range(self._nav.topLevelItemCount()):
            g = self._nav.topLevelItem(i)
            vis = 0
            for j in range(g.childCount()):
                c = g.child(j)
                if not q:
                    c.setHidden(False)
                    vis += 1
                else:
                    m = q in c.text(0).lower()
                    c.setHidden(not m)
                    if m:
                        vis += 1
            g.setHidden(vis == 0)

    # ══════════════════════════════════════════════
    def _build_all(self):
        # 外观
        i0 = self._build_appearance()
        # 酒店信息
        i1 = self._build_hotel()
        # Telegram
        i2 = self._build_telegram()
        # 房间与价格
        i3 = self._build_room_price()
        # 人员
        i4 = self._build_permissions()
        # 运营
        i5 = self._build_ops()
        # 前台布局
        i7 = self._build_fd_layers()

        self._nav.itemClicked.connect(self._on_nav)

        g1 = self._add_grp(i18n.t("nav_group_appearance"))
        self._add_leaf(i18n.t("leaf_theme_lang"), i0, g1)
        g2 = self._add_grp(i18n.t("nav_group_hotel"))
        self._add_leaf(i18n.t("leaf_hotel_info"), i1, g2)
        self._add_leaf(i18n.t("leaf_telegram_bot"), i2, g2)
        g3 = self._add_grp(i18n.t("nav_group_room_price"))
        self._add_leaf(i18n.t("leaf_room_type_price"), i3, g3)
        g4 = self._add_grp(i18n.t("nav_group_staff"))
        self._add_leaf(i18n.t("leaf_staff_roles"), i4, g4)
        g5 = self._add_grp(i18n.t("nav_group_ops"))
        self._add_leaf(i18n.t("leaf_shop_stock"), i5, g5)
        self._add_leaf(i18n.t("leaf_fd_layout"), i7, g5)

    # ── 外观 ──
    def _build_appearance(self):
        w = QWidget()
        r = QVBoxLayout(w)
        r.setContentsMargins(24, 24, 24, 24)
        r.setSpacing(16)
        g = _group(i18n.t("settings_appearance"))
        fl = QFormLayout(g)
        fl.setSpacing(14)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.cmb_theme = QComboBox()
        self.cmb_theme.setObjectName("ConsoleThemeCombo")
        for t_key, t_val in [("theme_mist", "mist"),
                               ("theme_shade", "shade"),
                               ("theme_glow", "glow"),
                               ("theme_ink", "ink")]:
            self.cmb_theme.addItem(i18n.t(t_key), t_val)
        fl.addRow(i18n.t("label_theme"), self.cmb_theme)
        self.cmb_lang = QComboBox()
        self.cmb_lang.setObjectName("ConsoleLangCombo")
        for c, l in i18n.available_languages().items():
            self.cmb_lang.addItem(l, c)
        fl.addRow(i18n.t("label_language"), self.cmb_lang)
        r.addWidget(g)
        return self._add_page(w)

    # ── 酒店信息 ──
    def _build_hotel(self):
        w = QWidget()
        r = QVBoxLayout(w)
        r.setContentsMargins(24, 24, 24, 24)
        r.setSpacing(16)

        g1 = _group(i18n.t("settings_grp_hotel"))
        f1 = QFormLayout(g1)
        f1.setSpacing(14)
        self.txt_name = QLineEdit()
        f1.addRow(i18n.t("label_hotel_name"), self.txt_name)
        self.txt_tax = QLineEdit()
        f1.addRow(i18n.t("label_tax_rate"), self.txt_tax)
        self.spn_dep = QDoubleSpinBox()
        self.spn_dep.setRange(0, 999999)
        self.spn_dep.setDecimals(0)
        self.spn_dep.setSuffix(f"  {i18n.t('currency_symbol')}")
        f1.addRow(i18n.t("label_default_deposit"), self.spn_dep)
        r.addWidget(g1)

        g2 = _group(i18n.t("settings_grp_op_defaults"))
        f2 = QFormLayout(g2)
        f2.setSpacing(14)
        self.tim_co = QTimeEdit()
        self.tim_co.setDisplayFormat("HH:mm")
        self.tim_co.setTime(QTime(12, 0))
        f2.addRow(i18n.t("settings_label_default_checkout_hour"), self.tim_co)
        self.spn_stay = QSpinBox()
        self.spn_stay.setRange(1, 365)
        self.spn_stay.setSuffix(i18n.t("settings_unit_days"))
        f2.addRow(i18n.t("settings_label_default_stay_days"), self.spn_stay)
        self.spn_card_d = QDoubleSpinBox()
        self.spn_card_d.setRange(0, 9999)
        self.spn_card_d.setDecimals(0)
        self.spn_card_d.setSuffix(f"  {i18n.t('currency_symbol')}")
        f2.addRow(i18n.t("settings_label_default_card_deposit"), self.spn_card_d)
        self.spn_hourly = QSpinBox()
        self.spn_hourly.setRange(1, 24)
        self.spn_hourly.setSuffix(i18n.t("settings_unit_hours"))
        f2.addRow(i18n.t("settings_label_default_hourly_hours"), self.spn_hourly)
        self.spn_card_cost = QDoubleSpinBox()
        self.spn_card_cost.setRange(0, 999)
        self.spn_card_cost.setDecimals(0)
        self.spn_card_cost.setSuffix(f"  {i18n.t('currency_symbol')}")
        f2.addRow(i18n.t("settings_label_card_cost_per_blank"), self.spn_card_cost)
        self.chk_allow_expire = QCheckBox(i18n.t("settings_label_allow_frontdesk_modify_expire"))
        f2.addRow("", self.chk_allow_expire)
        r.addWidget(g2)
        return self._add_page(w)

    # ── Telegram ──
    def _build_telegram(self):
        w = QWidget()
        s = QScrollArea()
        s.setWidgetResizable(True)
        s.setFrameShape(QFrame.Shape.NoFrame)
        from ui_surface import fd_apply_scroll_area
        fd_apply_scroll_area(s)
        inner = QWidget()
        fl = QFormLayout(inner)
        fl.setContentsMargins(24, 24, 24, 24)
        fl.setSpacing(14)

        from telegram_bot_config import status_label
        self.lbl_tg = QLabel(status_label())
        self.lbl_tg.setObjectName("TgStatusLabel")
        self.lbl_tg.setWordWrap(True)
        g1 = _group(i18n.t("vendor_group_system_status"))
        c1 = QFormLayout(g1)
        c1.addRow(i18n.t("settings_tg_bot_status_label"), self.lbl_tg)
        self.txt_tg = QLineEdit()
        c1.addRow(i18n.t("settings_tg_chat_id_label"), self.txt_tg)
        bt = QPushButton(i18n.t("settings_tg_test_send"))
        bt.setObjectName("SolidPrimaryBtn")
        self._btn_tg_test = bt
        bt.clicked.connect(self._test_tg)
        c1.addRow("", bt)
        fl.addRow(g1)

        g2 = _group(i18n.t("settings_tg_notify_switches"))
        nly = QVBoxLayout(g2)
        nly.setSpacing(8)
        self.chk_ci = QCheckBox(i18n.t("settings_tg_chk_ci"))
        self.chk_co = QCheckBox(i18n.t("settings_tg_chk_co"))
        self.chk_sh = QCheckBox(i18n.t("settings_tg_chk_shift"))
        self.chk_ri = QCheckBox(i18n.t("settings_tg_chk_risk"))
        self.chk_hk = QCheckBox(i18n.t("settings_tg_chk_hk"))
        self.chk_payout = QCheckBox(i18n.t("settings_tg_chk_payout"))
        self.chk_rate_over = QCheckBox(i18n.t("settings_tg_chk_rate_override"))
        for c in [self.chk_ci, self.chk_co, self.chk_sh, self.chk_ri, self.chk_hk,
                   self.chk_payout, self.chk_rate_over]:
            nly.addWidget(c)
        fl.addRow(g2)

        g3 = _group(i18n.t("settings_tg_route_group"))
        rt = QFormLayout(g3)
        rt.setSpacing(10)
        self.cmb_route = QComboBox()
        self.cmb_route.addItem(i18n.t("settings_tg_route_personal"), "prefer_dm")
        self.cmb_route.addItem(i18n.t("settings_tg_route_group_first"), "prefer_group")
        self.cmb_route.addItem(i18n.t("settings_tg_route_both"), "both")
        rt.addRow(i18n.t("settings_tg_label_route"), self.cmb_route)
        self.txt_hk_group = QLineEdit()
        self.txt_hk_group.setPlaceholderText(i18n.t("settings_tg_ph_hk_group"))
        rt.addRow(i18n.t("settings_tg_label_housekeeping"), self.txt_hk_group)
        self.txt_fd_group = QLineEdit()
        self.txt_fd_group.setPlaceholderText(i18n.t("settings_tg_ph_fd_group"))
        rt.addRow(i18n.t("settings_tg_label_frontdesk"), self.txt_fd_group)
        fl.addRow(g3)

        g4 = _group(i18n.t("group_daily_report"))
        rpt = QFormLayout(g4)
        rpt.setSpacing(10)
        self.chk_daily_rpt = QCheckBox(i18n.t("chk_enable_daily_report"))
        rpt.addRow("", self.chk_daily_rpt)
        self.spn_rpt_hour = QSpinBox()
        self.spn_rpt_hour.setRange(0, 23)
        self.spn_rpt_hour.setValue(23)
        self.spn_rpt_hour.setSuffix(i18n.t("suffix_hour_send"))
        rpt.addRow(i18n.t("settings_label_send_time"), self.spn_rpt_hour)
        fl.addRow(g4)

        s.setWidget(inner)
        ow = QVBoxLayout(w)
        ow.setContentsMargins(0, 0, 0, 0)
        ow.addWidget(s)
        return self._add_page(w)

    def _test_tg(self):
        try:
            from telegram_notify import send_telegram
            send_telegram(i18n.t("settings_tg_test_msg"))
            show_info(self, i18n.t("settings_tg_bot_status_label"), i18n.t("settings_tg_sent"))
        except Exception as e:
            show_warning(self, i18n.t("settings_tg_bot_status_label"), str(e))

    # ── 房间与价格 ──
    def _build_room_price(self):
        w = QWidget()
        r = QVBoxLayout(w)
        r.setContentsMargins(24, 24, 24, 24)
        r.setSpacing(16)
        g = _group(i18n.t("nav_group_room_price"))
        gv = QVBoxLayout(g)
        gv.setSpacing(12)
        gv.addWidget(QLabel(i18n.t("settings_room_price_desc")))
        b1 = QPushButton(i18n.t("settings_btn_manage_types_prices"))
        b1.setObjectName("SolidPrimaryBtn")
        b1.setMinimumHeight(36)
        b1.clicked.connect(self._open_types)
        gv.addWidget(b1)
        b2 = QPushButton(i18n.t("settings_btn_manage_rooms"))
        b2.setObjectName("SolidPrimaryBtn")
        b2.setMinimumHeight(36)
        b2.clicked.connect(self._open_rooms)
        gv.addWidget(b2)
        gv.addStretch(1)
        r.addWidget(g)
        return self._add_page(w)

    def _open_types(self):
        from unified_room_page import UnifiedRoomPage
        from ui_helpers import style_dialog
        d = UnifiedRoomPage()
        d.setWindowTitle(i18n.t("settings_btn_manage_types_prices"))
        style_dialog(d, size="large")
        d.setWindowModality(Qt.WindowModality.NonModal)
        d.show()

    def _open_rooms(self):
        from unified_room_page import UnifiedRoomPage
        from ui_helpers import style_dialog
        d = UnifiedRoomPage()
        d.setWindowTitle(i18n.t("settings_btn_manage_types_prices"))
        style_dialog(d, size="large")
        d.setWindowModality(Qt.WindowModality.NonModal)
        d.show()

    # ── 人员 ──
    def _build_permissions(self):
        from tabs.staff_tab import StaffTab
        w = QWidget()
        r = QVBoxLayout(w)
        r.setContentsMargins(20, 20, 20, 20)
        g = _group(i18n.t("leaf_staff_roles"))
        gv = QHBoxLayout(g)
        gv.setContentsMargins(8, 8, 8, 8)
        self._stf = StaffTab()
        gv.addWidget(self._stf)
        r.addWidget(g, 1)
        return self._add_page(w)

    # ── 运营工具 ──
    def _build_ops(self):
        w = QWidget()
        r = QVBoxLayout(w)
        r.setContentsMargins(24, 24, 24, 24)
        r.setSpacing(16)
        g = _group(i18n.t("nav_group_ops"))
        gv = QVBoxLayout(g)
        gv.setSpacing(12)
        b1 = QPushButton(i18n.t("settings_btn_shop_mgmt"))
        b1.setObjectName("SolidPrimaryBtn")
        b1.setMinimumHeight(36)
        b1.clicked.connect(self._open_shop)
        gv.addWidget(b1)
        b2 = QPushButton(i18n.t("settings_btn_initial_stock"))
        b2.setObjectName("SolidPrimaryBtn")
        b2.setMinimumHeight(36)
        b2.clicked.connect(self._open_stock)
        gv.addWidget(b2)
        gv.addStretch(1)
        r.addWidget(g)
        return self._add_page(w)

    def _open_shop(self):
        from ui_helpers import style_dialog
        d = QWidget()
        d.setWindowTitle(i18n.t("settings_btn_shop_mgmt"))
        from shop_inventory import ShopInventoryTab
        t = QVBoxLayout(d)
        t.addWidget(ShopInventoryTab())
        style_dialog(d)
        d.setWindowModality(Qt.WindowModality.NonModal)
        d.show()

    def _open_stock(self):
        from ui_helpers import show_warning
        show_warning(self, i18n.t("dlg_inventory"), i18n.t("settings_msg_stock_wizard_dev"))


    # ── 前台布局 ──
    def _build_fd_layers(self):
        from frontdesk_layers import get_frontdesk_layers, layer_is_on
        w = QWidget()
        r = QVBoxLayout(w)
        r.setContentsMargins(24, 24, 24, 24)
        r.setSpacing(16)
        g = _group(i18n.t("settings_fd_layout_grp"))
        gv = QVBoxLayout(g)
        gv.setSpacing(8)
        ly = get_frontdesk_layers(db)
        self._fd_chk = {}
        for sec, sk, ls in [(i18n.t("settings_fd_workspace"), "frontdesk_hub", ["checkin", "roster", "shop", "service", "shift"])]:
            gv.addWidget(QLabel(sec))
            for lf in ls:
                on = True
                try:
                    on = layer_is_on(ly, sk, lf)
                except Exception:
                    pass
                chk = QCheckBox(i18n.t(f"fd_layer_{lf}"))
                chk.setChecked(on)
                self._fd_chk[(sk, lf)] = chk
                gv.addWidget(chk)
        r.addWidget(g)
        return self._add_page(w)

    # ══════════════════════════════════════════════
    def _load(self):
        th = db.get_config("theme") or "mist"
        from theme_palette import resolve_theme_name
        th = resolve_theme_name(th)
        for i in range(self.cmb_theme.count()):
            if self.cmb_theme.itemData(i) == th:
                self.cmb_theme.setCurrentIndex(i)
                break
        lg = db.get_config("language") or "zh"
        for i in range(self.cmb_lang.count()):
            if self.cmb_lang.itemData(i) == lg:
                self.cmb_lang.setCurrentIndex(i)
                break
        self.txt_name.setText(db.get_config("hotel_name") or "")
        self.txt_tax.setText(db.get_config("tax_rate") or "")
        self.spn_dep.setValue(db.get_config_float("default_deposit", 50.0))
        try:
            hh, mm = (db.get_config("default_card_checkout_hour") or "12:00").split(":")
            self.tim_co.setTime(QTime(int(hh), int(mm)))
        except Exception:
            self.tim_co.setTime(QTime(12, 0))
        self.spn_stay.setValue(int(db.get_config("default_stay_days") or "1"))
        self.spn_card_d.setValue(db.get_config_float("default_card_deposit", 50.0))
        self.spn_hourly.setValue(int(db.get_config("default_hourly_hours") or "4"))
        self.spn_card_cost.setValue(db.get_config_float("card_cost_per_blank", 3.0))
        self.chk_allow_expire.setChecked(
            (db.get_config("allow_frontdesk_modify_expire") or "1") == "1")
        self.txt_tg.setText(db.get_config("tg_chat_id") or "")
        for k, c in [("tg_notify_checkin", self.chk_ci), ("tg_notify_checkout", self.chk_co),
                      ("tg_notify_shift", self.chk_sh), ("tg_notify_risk", self.chk_ri),
                      ("tg_notify_hk", self.chk_hk)]:
            c.setChecked((db.get_config(k) or "1") == "1")
        self.chk_payout.setChecked((db.get_config("notify_payout") or "1") == "1")
        self.chk_rate_over.setChecked((db.get_config("notify_rate_override") or "0") == "1")
        mode = (db.get_config("tg_staff_route_default") or "prefer_dm").strip()
        idx = max(0, self.cmb_route.findData(mode))
        self.cmb_route.setCurrentIndex(idx)
        self.txt_hk_group.setText(
            (db.get_config("housekeeping_group_id") or db.get_config("housekeeping_chat_id") or "").strip())
        self.txt_fd_group.setText(
            (db.get_config("front_desk_group_id") or db.get_config("front_desk_chat_id") or "").strip())
        self.chk_daily_rpt.setChecked((db.get_config("daily_report_enabled") or "1") == "1")
        try:
            self.spn_rpt_hour.setValue(int(db.get_config("daily_report_hour") or "23"))
        except (ValueError, TypeError):
            self.spn_rpt_hour.setValue(23)

    def _save(self):
        try:
            th = self.cmb_theme.currentData()
            if th:
                db.set_config("theme", th)
            lg = self.cmb_lang.currentData()
            if lg:
                db.set_config("language", lg)
                i18n.switch(lg)
            db.set_config("hotel_name", self.txt_name.text().strip())
            db.set_config("tax_rate", self.txt_tax.text().strip())
            db.set_config("default_deposit", str(self.spn_dep.value()))
            db.set_config("default_stay_days", str(self.spn_stay.value()))
            db.set_config("default_card_deposit", str(self.spn_card_d.value()))
            db.set_config("default_card_checkout_hour", self.tim_co.time().toString("HH:mm"))
            db.set_config("default_hourly_hours", str(self.spn_hourly.value()))
            db.set_config("card_cost_per_blank", str(self.spn_card_cost.value()))
            db.set_config("allow_frontdesk_modify_expire",
                          "1" if self.chk_allow_expire.isChecked() else "0")
            db.set_config("tg_chat_id", self.txt_tg.text().strip())
            db.set_config("tg_notify_checkin", "1" if self.chk_ci.isChecked() else "0")
            db.set_config("tg_notify_checkout", "1" if self.chk_co.isChecked() else "0")
            db.set_config("tg_notify_shift", "1" if self.chk_sh.isChecked() else "0")
            db.set_config("tg_notify_risk", "1" if self.chk_ri.isChecked() else "0")
            db.set_config("tg_notify_hk", "1" if self.chk_hk.isChecked() else "0")
            db.set_config("notify_payout", "1" if self.chk_payout.isChecked() else "0")
            db.set_config("notify_rate_override", "1" if self.chk_rate_over.isChecked() else "0")
            db.set_config("tg_staff_route_default", self.cmb_route.currentData() or "prefer_dm")
            db.set_config("housekeeping_group_id", self.txt_hk_group.text().strip())
            db.set_config("front_desk_group_id", self.txt_fd_group.text().strip())
            db.set_config("daily_report_enabled", "1" if self.chk_daily_rpt.isChecked() else "0")
            db.set_config("daily_report_hour", str(self.spn_rpt_hour.value()))
            try:
                from frontdesk_layers import DEFAULT_FD_LAYERS, layers_to_json
                from copy import deepcopy
                out = deepcopy(DEFAULT_FD_LAYERS)
                for (sk, lf), c in self._fd_chk.items():
                    if sk in out and lf in out[sk]:
                        out[sk][lf] = c.isChecked()
                db.set_config("frontdesk_display_json", layers_to_json(out))
            except Exception:
                pass
            db.log_action("system", "SETTINGS_SAVE", "console")
            bus.theme_changed.emit(th)
            show_info(self, i18n.t("dlg_save_success"), i18n.t("msg_all_settings_saved"))
        except Exception as e:
            show_warning(self, i18n.t("dlg_error"), str(e))
