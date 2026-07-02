"""
cloud_config_dialog.py — 云端工作器配置弹窗

从 main_window_impl._vendor_cloud() 提取为独立类。
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QCheckBox,
    QSlider, QPushButton, QLabel,
)
from PySide6.QtCore import Qt

from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import style_dialog, show_warning


class CloudConfigDialog(QDialog):
    """云端工作器配置对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("settings_cloud_title"))
        style_dialog(self, 580, 620)
        ly = QVBoxLayout(self)
        ly.setContentsMargins(24, 24, 24, 24)
        ly.setSpacing(12)

        txt_worker_url = QLineEdit()
        txt_worker_url.setPlaceholderText(i18n.t("settings_ph_worker"))
        fl = QFormLayout()
        fl.setSpacing(14)
        fl.addRow(i18n.t("settings_cloud_worker_label"), txt_worker_url)

        chk_cloud_enabled = QCheckBox(i18n.t("settings_chk_cloud"))
        fl.addRow("", chk_cloud_enabled)

        txt_region = QLineEdit()
        txt_region.setPlaceholderText(i18n.t("settings_ph_region"))
        fl.addRow(i18n.t("settings_label_region"), txt_region)

        txt_salesperson = QLineEdit()
        txt_salesperson.setPlaceholderText(i18n.t("settings_ph_sales"))
        fl.addRow(i18n.t("settings_label_sales"), txt_salesperson)

        sld_poll = QSlider(Qt.Orientation.Horizontal)
        sld_poll.setRange(1, 60)
        sld_poll.setValue(3)
        sld_poll.setTickPosition(QSlider.TickPosition.TicksBelow)
        sld_poll.setTickInterval(5)
        lbl_poll = QLabel(i18n.t("settings_sec_unit").format(v=3))
        sld_poll.valueChanged.connect(
            lambda v: lbl_poll.setText(i18n.t("settings_sec_unit").format(v=v))
        )
        fl.addRow(i18n.t("settings_poll_interval"), sld_poll)
        fl.addRow(i18n.t("settings_label_current"), lbl_poll)

        sld_max_fail = QSlider(Qt.Orientation.Horizontal)
        sld_max_fail.setRange(3, 100)
        sld_max_fail.setValue(10)
        sld_max_fail.setTickPosition(QSlider.TickPosition.TicksBelow)
        sld_max_fail.setTickInterval(10)
        lbl_fail = QLabel(i18n.t("settings_times_unit").format(v=10))
        sld_max_fail.valueChanged.connect(
            lambda v: lbl_fail.setText(i18n.t("settings_times_unit").format(v=v))
        )
        fl.addRow(i18n.t("settings_fail_threshold"), sld_max_fail)
        fl.addRow(i18n.t("settings_label_current"), lbl_fail)

        sld_degraded = QSlider(Qt.Orientation.Horizontal)
        sld_degraded.setRange(10, 300)
        sld_degraded.setValue(30)
        sld_degraded.setTickPosition(QSlider.TickPosition.TicksBelow)
        sld_degraded.setTickInterval(30)
        lbl_degraded = QLabel(i18n.t("settings_sec_unit").format(v=30))
        sld_degraded.valueChanged.connect(
            lambda v: lbl_degraded.setText(i18n.t("settings_sec_unit").format(v=v))
        )
        fl.addRow(i18n.t("settings_degraded_interval"), sld_degraded)
        fl.addRow(i18n.t("settings_label_current"), lbl_degraded)

        ly.addLayout(fl)
        ly.addStretch()

        btn_save = QPushButton(i18n.t("settings_btn_save"))
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_save.setMinimumHeight(36)

        def _save_cloud():
            db.set_config("cloud_worker_url", txt_worker_url.text().strip())
            db.set_config("cloud_enabled", "1" if chk_cloud_enabled.isChecked() else "0")
            db.set_config("region", txt_region.text().strip())
            db.set_config("salesperson_id", txt_salesperson.text().strip())
            db.set_config("cloud_poll_interval", str(sld_poll.value()))
            db.set_config("cloud_max_consecutive_fail", str(sld_max_fail.value()))
            db.set_config("cloud_degraded_interval", str(sld_degraded.value()))
            bus.toast_requested.emit("☁ 云配置已保存")
            self.accept()

        btn_save.clicked.connect(_save_cloud)
        ly.addWidget(btn_save)

        # 预填当前值
        txt_worker_url.setText(db.get_config("cloud_worker_url") or "")
        chk_cloud_enabled.setChecked(
            (db.get_config("cloud_enabled") or "0") == "1"
        )
        txt_region.setText(db.get_config("region") or "")
        txt_salesperson.setText(db.get_config("salesperson_id") or "")
        try:
            sld_poll.setValue(int(db.get_config("cloud_poll_interval") or "3"))
        except ValueError:
            sld_poll.setValue(3)
        try:
            sld_max_fail.setValue(int(db.get_config("cloud_max_consecutive_fail") or "10"))
        except ValueError:
            sld_max_fail.setValue(10)
        try:
            sld_degraded.setValue(int(db.get_config("cloud_degraded_interval") or "30"))
        except ValueError:
            sld_degraded.setValue(30)
