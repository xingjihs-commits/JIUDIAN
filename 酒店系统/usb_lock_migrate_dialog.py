"""
usb_lock_migrate_dialog.py — 门锁系统迁移向导
=============================================
在设置 → 门卡系统标签页里调用：
    from usb_lock_migrate_dialog import UsbLockMigrateDialog
    dlg = UsbLockMigrateDialog(parent=self)
    dlg.exec()
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QGroupBox, QTextEdit, QSizePolicy
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QColor
from pathlib import Path
from usb_lock_scanner import usb_lock_scanner
from ui_helpers import style_dialog, build_dialog_header, show_info, show_warning, show_error, ask_confirm
from ui_surface import fd_apply_table_palette
from i18n import i18n
from design_tokens import _p
from legacy_migration_guide import GuideAction, usb_migrate_session
from migration_guide_panel import MigrationGuidePanel
from lock_adapters.middleware import BrandConfigExtractor


# ─────────────────────────────────────────────────────────────────────────────
#  后台扫描线程（防止UI卡死）
# ─────────────────────────────────────────────────────────────────────────────
class ScanWorker(QThread):
    finished = Signal(list)   # 扫描完成，传回结果列表
    error    = Signal(str)    # 扫描出错

    def __init__(self, skip_usb: bool = False, parent=None):
        super().__init__(parent)
        self.skip_usb = skip_usb

    def run(self):
        try:
            results = usb_lock_scanner.scan_and_detect(skip_usb=self.skip_usb)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


# ─────────────────────────────────────────────────────────────────────────────
#  主对话框
# ─────────────────────────────────────────────────────────────────────────────
class UsbLockMigrateDialog(QDialog):
    """门锁系统自动识别与无感迁移对话框（三层识别）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("usb_lock_migrate.window_title"))
        self.setModal(True)
        style_dialog(self, size="medium")
        self._scan_results = []
        self._usb_guide = usb_migrate_session()
        self._init_ui()
        self._load_current_config()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(build_dialog_header(i18n.t("usb_lock_migrate.header_title")))

        self.guide_panel = MigrationGuidePanel(on_action=self._on_guide_action)
        self.guide_panel.bind_session(self._usb_guide)
        layout.addWidget(self.guide_panel)

        # ── 当前配置状态 ──
        self._status_group = QGroupBox(i18n.t("usb_lock_migrate.current_config"))
        sg_layout = QVBoxLayout(self._status_group)
        self._status_label = QLabel(i18n.t("usb_lock_migrate.not_configured"))
        self._status_label.setStyleSheet(f"color: {_p('text_muted')};")
        sg_layout.addWidget(self._status_label)
        layout.addWidget(self._status_group)

        # ── 扫描按钮 + 进度条 ──
        btn_row = QHBoxLayout()
        self._scan_btn = QPushButton(i18n.t("usb_lock_migrate.scan_btn"))
        self._scan_btn.setObjectName("SolidPrimaryBtn")
        self._scan_btn.setMinimumHeight(36)
        self._scan_btn.setMaximumHeight(36)
        self._scan_btn.setStyleSheet(
            f"QPushButton{{background:{_p('primary')};color:{_p('surface')};border-radius:6px;font-weight:700;font-size:13px;}}"
            f"QPushButton:hover{{background:{_p('primary_hover')};}}"
            f"QPushButton:disabled{{background:{_p('surface_alt')};color:{_p('text_muted')};}}"
        )
        self._scan_btn.clicked.connect(self._start_scan)
        btn_row.addWidget(self._scan_btn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setFixedHeight(8)
        btn_row.addWidget(self._progress)
        layout.addLayout(btn_row)

        # ── 底部说明 ──
        hint = QLabel(i18n.t("usb_lock_migrate.auto_detect_hint"))
        hint.setStyleSheet(f"color: {_p('text_dim')}; font-size: 11px;")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        # ── 扫描结果表格 ──
        result_label = QLabel(i18n.t("usb_lock_migrate.result_label"))
        result_label.setFont(QFont("", 11, QFont.Bold))
        layout.addWidget(result_label)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            i18n.t("usb_lock_migrate.col_brand"),
            i18n.t("usb_lock_migrate.col_method"),
            i18n.t("usb_lock_migrate.col_source"),
            i18n.t("usb_lock_migrate.col_detail"),
            i18n.t("usb_lock_migrate.col_card"),
            i18n.t("usb_lock_migrate.col_action"),
        ])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setMinimumHeight(96)
        self._table.setAlternatingRowColors(False)
        fd_apply_table_palette(self._table)
        layout.addWidget(self._table, 1)

        # ── 已知品牌库 ──
        brands_group = QGroupBox(i18n.t("usb_lock_migrate.known_brands").format(n=len(usb_lock_scanner.list_all_brands())))
        bg_layout = QVBoxLayout(brands_group)
        brands_text = QTextEdit()
        brands_text.setReadOnly(True)
        brands_text.setMinimumHeight(36)
        brands_text.setMaximumHeight(120)
        brands_text.setStyleSheet(f"font-size: 11px; color: {_p('text_dim')};")
        lines = []
        for b in usb_lock_scanner.list_all_brands():
            regions = "/".join(b.get("region", []))
            lines.append(f"• {b['name']} ({b['name_en']})  [{regions}]  — {b['notes']}")
        brands_text.setPlainText("\n".join(lines))
        bg_layout.addWidget(brands_text)
        layout.addWidget(brands_group)

        # ── 底部按钮 ──
        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton(i18n.t("usb_lock_migrate.btn_close"))
        close_btn.setObjectName("FdGhostBtn")
        close_btn.setMinimumWidth(60)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def _on_guide_action(self, action: str) -> None:
        if action == GuideAction.START_USB_SCAN:
            self._start_scan()

    def _load_current_config(self):
        """加载并显示当前已迁移的门锁配置"""
        cfg = usb_lock_scanner.get_migrated_config()
        if cfg.get("brand_name"):
            migrated_at = cfg.get("migrated_at", "")[:16].replace("T", " ")
            method = cfg.get("detect_method", "")
            method_label = {
                "usb_filename": i18n.t("usb_lock_migrate.method_usb"),
                "fingerprint":  i18n.t("usb_lock_migrate.method_fingerprint"),
                "learn":        i18n.t("usb_lock_migrate.method_learn"),
            }.get(method, method)
            learned = cfg.get("learned_dlsCoID", "")
            extra = f"  |  dlsCoID: {learned}" if learned else ""
            self._status_label.setText(
                i18n.t("usb_lock_migrate.config_status").format(
                    brand=cfg['brand_name'], method=method_label,
                    baud=cfg['baud_rate'], extra=extra,
                    migrated_at=migrated_at, source=cfg.get('source_drive', '-')
                )
            )
            self._status_label.setStyleSheet(f"color: {_p('amount_positive')}; font-weight: 600;")
        else:
            self._status_label.setText(i18n.t("usb_lock_migrate.config_status_empty"))
            self._status_label.setStyleSheet(f"color: {_p('accent')};")

    def _start_scan(self):
        """启动后台扫描线程"""
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText(i18n.t("usb_lock_migrate.scanning"))
        self._progress.setVisible(True)
        self._table.setRowCount(0)

        self._worker = ScanWorker(skip_usb=True, parent=self)
        self._worker.finished.connect(self._on_scan_done)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_scan_done(self, results: list):
        """扫描完成回调"""
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText(i18n.t("usb_lock_migrate.rescan_btn"))
        self._progress.setVisible(False)
        self._scan_results = results

        if not results:
            show_info(
                self, i18n.t("usb_lock_migrate.no_lock_title"),
                i18n.t("usb_lock_migrate.no_lock_body"),
            )
            return

        # 填充结果表格
        self._table.setRowCount(len(results))
        for row, r in enumerate(results):
            method = r.get("detect_method", "usb_filename")
            method_label = {
                "usb_filename": i18n.t("usb_lock_migrate.method_file"),
                "fingerprint":  i18n.t("usb_lock_migrate.method_fingerprint_icon"),
                "learn":        i18n.t("usb_lock_migrate.method_learn_icon"),
            }.get(method, method)
            brand_id = r.get("brand_id", "")

            # 通过中间层检查是否有发卡支持
            ext = BrandConfigExtractor.extract(brand_id)
            has_profile = ext.get("profile") is not None
            card_support = i18n.t("usb_lock_migrate.card_ok") if has_profile else i18n.t("usb_lock_migrate.card_backup_only")
            card_color = QColor(_p('amount_positive')) if has_profile else QColor(_p('accent'))

            # 品牌名
            brand_item = QTableWidgetItem(f"{r['brand_name']} ({r.get('brand_en','')})")
            brand_item.setForeground(QColor(_p('primary')))
            self._table.setItem(row, 0, brand_item)

            # 方式
            method_item = QTableWidgetItem(method_label)
            if method == "learn":
                method_item.setForeground(QColor(_p('accent')))  # 金色
            elif method == "fingerprint":
                method_item.setForeground(QColor(_p('amount_positive')))  # 绿色
            self._table.setItem(row, 1, method_item)

            # 来源
            source = r.get("drive", "")
            if method == "learn":
                source = i18n.t("usb_lock_migrate.source_learn")
            elif method == "fingerprint":
                fp = r.get("fingerprint", {})
                score = fp.get("total_score", 0)
                source = i18n.t("usb_lock_migrate.source_fingerprint").format(source=source, score=score)
            self._table.setItem(row, 2, QTableWidgetItem(source))

            # 详情
            detail_lines = []
            found = r.get("found_files", [])
            if found:
                detail_lines.append(i18n.t("usb_lock_migrate.detail_files").format(files="; ".join(Path(f).name for f in found[:3])))
            if method == "fingerprint":
                fp = r.get("fingerprint", {})
                dll = fp.get("dll", {})
                ini = fp.get("ini", {})
                if dll.get("matched"):
                    detail_lines.append(i18n.t("usb_lock_migrate.detail_dll").format(matched=' '.join(dll['matched'][:4])))
                if ini.get("matched"):
                    detail_lines.append(i18n.t("usb_lock_migrate.detail_ini").format(matched=' '.join(ini['matched'][:4])))
            if method == "learn":
                dls = r.get("learned_dlsCoID", "")
                detail_lines.append(i18n.t("usb_lock_migrate.detail_learn").format(dls=dls))
            detail_item = QTableWidgetItem("\n".join(detail_lines))
            self._table.setItem(row, 3, detail_item)

            # 发卡能力
            card_support_item = QTableWidgetItem(card_support)
            card_support_item.setForeground(card_color)
            self._table.setItem(row, 4, card_support_item)

            # 迁移按钮
            migrate_btn = QPushButton(i18n.t("usb_lock_migrate.migrate_btn"))
            migrate_btn.setObjectName("SolidPrimaryBtn")
            migrate_btn.setStyleSheet(
                f"QPushButton{{background:{_p('amount_positive')};color:{_p('surface')};border-radius:4px;font-weight:700;padding:4px 10px;}}"
                f"QPushButton:hover{{background:{_p('primary_hover')};}}"
            )
            migrate_btn.clicked.connect(lambda checked, idx=row: self._do_migrate(idx))
            self._table.setCellWidget(row, 5, migrate_btn)

        self._table.resizeRowsToContents()

    def _on_scan_error(self, error_msg: str):
        """扫描出错回调"""
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText(i18n.t("usb_lock_migrate.rescan_btn"))
        self._progress.setVisible(False)
        show_error(self, i18n.t("usb_lock_migrate.scan_fail_title"), i18n.t("usb_lock_migrate.scan_fail_body").format(msg=error_msg))

    def _do_migrate(self, row_idx: int):
        """执行迁移"""
        if row_idx >= len(self._scan_results):
            return
        result = self._scan_results[row_idx]
        brand_name = result.get("brand_name", i18n.t("usb_lock_migrate.unknown_brand"))
        method = result.get("detect_method", "")

        extra_note = ""
        if method == "learn":
            dls = result.get("learned_dlsCoID", "")
            extra_note = i18n.t("usb_lock_migrate.note_learn").format(dls=dls)
        elif method == "fingerprint":
            fp = result.get("fingerprint", {})
            extra_note = i18n.t("usb_lock_migrate.note_fingerprint").format(score=fp.get('total_score', '?'))

        if not ask_confirm(
            self, i18n.t("usb_lock_migrate.confirm_title"),
            i18n.t("usb_lock_migrate.confirm_body").format(brand=brand_name, extra=extra_note),
        ):
            return

        migrate_result = usb_lock_scanner.migrate(result)

        if migrate_result["ok"]:
            show_info(
                self, i18n.t("usb_lock_migrate.migrate_ok_title"),
                migrate_result["message"] + i18n.t("usb_lock_migrate.migrate_ok_body").format(keys=', '.join(migrate_result['migrated_keys']) or i18n.t("usb_lock_migrate.no_backup"))
            )
            self._load_current_config()
            self.accept()  # 返回接受信号
        else:
            show_error(self, i18n.t("usb_lock_migrate.migrate_fail_title"), migrate_result["message"])


def open_usb_migrate_dialog(parent=None) -> None:
    UsbLockMigrateDialog(parent).exec()
