"""
读卡器设置对话框 — CardReaderSettingsDialog
读卡器品牌+串口 + 取电器（节电开关）写卡参数
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QFormLayout, QLineEdit, QSpinBox, QCheckBox,
    QGroupBox, QTabWidget, QWidget,
)
from database import db
from design_tokens import _p
from ui_helpers import show_info, show_warning, style_dialog
from power_controller_config import (
    import_keys_from_legacy_lock,
    list_data_formats,
    list_power_profiles,
    load_power_config,
    power_config_detail_text,
    power_config_summary,
    resolve_power_config,
    save_power_config,
)
from ._shared import CARD_BRANDS, _list_serial_ports
from .card_driver import CardReaderDriver, reset_driver, get_driver


class CardReaderSettingsDialog(QDialog):
    """读卡器 + 取电器（节电开关）写卡参数"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("读卡器设置")
        style_dialog(self, size="large")
        self.setModal(True)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── Tab1 读卡器 ──
        tab_reader = QWidget()
        rl = QVBoxLayout(tab_reader)
        form = QFormLayout()
        self.brand_combo = QComboBox()
        for key, info in CARD_BRANDS.items():
            self.brand_combo.addItem(info["name"], key)
        saved_brand = db.get_config("card_reader_brand") or "simulate"
        idx = self.brand_combo.findData(saved_brand)
        if idx >= 0:
            self.brand_combo.setCurrentIndex(idx)
        form.addRow("读卡器品牌", self.brand_combo)
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.addItems(_list_serial_ports())
        self.port_combo.setCurrentText(db.get_config("card_reader_port") or "COM1")
        form.addRow("串口端口", self.port_combo)
        rl.addLayout(form)
        self.test_lbl = QLabel("")
        self.test_lbl.setWordWrap(True)
        rl.addWidget(self.test_lbl)
        btn_test = QPushButton("测试连接")
        btn_test.setObjectName("FdGhostBtn")
        btn_test.clicked.connect(self._test)
        rl.addWidget(btn_test)
        rl.addStretch()
        tabs.addTab(tab_reader, "读卡器")

        # ── Tab2 取电器 ──
        tab_power = QWidget()
        pl = QVBoxLayout(tab_power)
        pl.addWidget(QLabel("取电器写卡配置：按门锁品牌自动推理，一般无需手动设置。"))
        pc = resolve_power_config()
        is_auto = pc.get("mode") != "manual"
        self.chk_manual_mode = QCheckBox("手动模式（自定义扇区/密钥）")
        self.chk_manual_mode.setChecked(not is_auto)
        self.chk_manual_mode.toggled.connect(self._on_power_mode_toggle)
        pl.addWidget(self.chk_manual_mode)

        self.lbl_resolved = QLabel(power_config_detail_text(pc))
        self.lbl_resolved.setWordWrap(True)
        self.lbl_resolved.setStyleSheet(
            f"background:{_p('surface_alt')}; color:{_p('amount_positive')}; padding:10px; border-radius:8px;"
        )
        pl.addWidget(self.lbl_resolved)

        self.advanced_box = QGroupBox("高级设置")
        adv_l = QVBoxLayout(self.advanced_box)
        pf = QFormLayout()
        self.power_profile = QComboBox()
        for p in list_power_profiles():
            self.power_profile.addItem(p.get("name", p.get("id")), p.get("id"))
        pidx = self.power_profile.findData(pc.get("profile_id"))
        if pidx >= 0:
            self.power_profile.setCurrentIndex(pidx)
        pf.addRow("厂商模板", self.power_profile)
        self.spn_sector = QSpinBox()
        self.spn_sector.setRange(0, 15)
        self.spn_sector.setValue(pc.get("sector", 1))
        pf.addRow("扇区", self.spn_sector)
        self.spn_block = QSpinBox()
        self.spn_block.setRange(0, 2)
        self.spn_block.setValue(pc.get("block", 0))
        pf.addRow("数据块", self.spn_block)
        self.txt_key_a = QLineEdit(pc.get("key_a", "FFFFFFFFFFFF"))
        pf.addRow("Key A", self.txt_key_a)
        self.txt_key_b = QLineEdit(pc.get("key_b", "FFFFFFFFFFFF"))
        pf.addRow("Key B", self.txt_key_b)
        self.cmb_format = QComboBox()
        for df in list_data_formats():
            self.cmb_format.addItem(df.get("name", df.get("id")), df.get("id"))
        fidx = self.cmb_format.findData(pc.get("data_format"))
        if fidx >= 0:
            self.cmb_format.setCurrentIndex(fidx)
        pf.addRow("数据格式", self.cmb_format)
        adv_l.addLayout(pf)
        btn_import = QPushButton("从旧门锁导入密钥")
        btn_import.setObjectName("FdGhostBtn")
        btn_import.clicked.connect(self._import_lock_keys)
        adv_l.addWidget(btn_import)
        pl.addWidget(self.advanced_box)
        self.advanced_box.setVisible(not is_auto)
        pl.addStretch()
        tabs.addTab(tab_power, "取电器")

        btn_row = QHBoxLayout()
        btn_save = QPushButton("保存设置")
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_save.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

        self._refresh_resolved_label()

    def _on_power_mode_toggle(self):
        auto = not self.chk_manual_mode.isChecked()
        self.advanced_box.setVisible(not auto)
        self._refresh_resolved_label()

    def _refresh_resolved_label(self):
        if not self.chk_manual_mode.isChecked():
            self.lbl_resolved.setText(power_config_detail_text(resolve_power_config()))
        else:
            self.lbl_resolved.setText("手动模式 — 请在下方设置扇区和密钥")

    def _import_lock_keys(self):
        keys = import_keys_from_legacy_lock(self.spn_sector.value())
        if keys:
            self.txt_key_a.setText(keys[0])
            self.txt_key_b.setText(keys[1])
            show_info(self, "提示", "已从旧门锁导入密钥")
            self._refresh_resolved_label()
        else:
            show_warning(self, "提示", "从旧门锁导入密钥失败")

    def _test(self):
        brand = self.brand_combo.currentData()
        port = self.port_combo.currentText().strip()
        driver = CardReaderDriver(brand, port)
        ok, msg = driver.connect()
        driver.disconnect()
        if ok:
            self.test_lbl.setText(f"🟢 {msg}")
            self.test_lbl.setStyleSheet(f"color:{_p('amount_positive')};")
        else:
            self.test_lbl.setText(f"🔴 {msg}")
            self.test_lbl.setStyleSheet(f"color:{_p('danger')};")

    def _save(self):
        brand = self.brand_combo.currentData()
        port = self.port_combo.currentText().strip()
        db.set_config("card_reader_brand", brand)
        db.set_config("card_reader_port", port)
        try:
            if not self.chk_manual_mode.isChecked():
                save_power_config({"enabled": True, "mode": "auto", "profile_id": "follow_lock"})
            else:
                save_power_config({
                    "enabled": True,
                    "mode": "manual",
                    "profile_id": self.power_profile.currentData() or "cn_fallback_s1",
                    "sector": self.spn_sector.value(),
                    "block": self.spn_block.value(),
                    "key_a": self.txt_key_a.text(),
                    "key_b": self.txt_key_b.text(),
                    "data_format": self.cmb_format.currentData() or "room_ascii8_ts4",
                    "notes": "",
                })
        except ValueError as e:
            show_warning(self, "提示", str(e))
            return
        reset_driver()
        show_info(self, "提示", "读卡器设置已保存")
        self.accept()
