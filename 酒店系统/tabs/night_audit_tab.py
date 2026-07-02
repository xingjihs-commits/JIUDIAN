from __future__ import annotations

import logging
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTextEdit,
    QSplitter,
    QFrame,
)
from PySide6.QtCore import Qt
from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import show_info, show_warning, ask_confirm
from sound_helper import play_warn, play_fail, play_notify
from design_tokens import _p
from frontdesk_ui import (
    fd_section_bar,
    fd_apply_action_btn,
    fd_apply_low_freq_btn,
    FD_MARGIN,
    FD_SPACE_SM,
    FD_SPACE_MD,
)
from ui_surface import fd_apply_content_box, fd_apply_table_palette, fd_refresh_surfaces, fd_sync_table_height, fd_apply_info_banner, fd_apply_workspace_splitter, fd_apply_content_text_edit

logger = logging.getLogger(__name__)


class NightAuditTab(QWidget):
    """夜审管理标签页：手动触发夜审、查看历史报告"""

    def __init__(self):
        super().__init__()
        self.setObjectName("NightAuditTab")
        self._build_ui()
        self.refresh()
        bus.theme_changed.connect(lambda _: fd_refresh_surfaces(self))

    def _build_ui(self):
        l = QVBoxLayout(self)
        l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)
        l.setSpacing(FD_SPACE_MD)

        # 标题行：金线横栏 + 上次状态
        self.status_lbl = QLabel(i18n.t("na_last_unknown"))
        self.status_lbl.setObjectName("NightAuditStatus")
        l.addWidget(
            fd_section_bar(
                i18n.t("na_title"),
                action_widgets=[self.status_lbl],
            )
        )

        # 说明
        desc = QLabel(i18n.t("na_desc"))
        desc.setWordWrap(True)
        desc.setObjectName("FdInfoBanner")
        fd_apply_info_banner(desc)
        l.addWidget(desc)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(FD_SPACE_SM)

        btn_manual = QPushButton(i18n.t("na_btn_run"))
        fd_apply_action_btn(btn_manual, primary=True)
        btn_manual.clicked.connect(self._manual_audit)
        toolbar.addWidget(btn_manual)

        btn_overtime = QPushButton(i18n.t("na_btn_ot"))
        fd_apply_low_freq_btn(btn_overtime)
        btn_overtime.clicked.connect(self._check_overtime)
        toolbar.addWidget(btn_overtime)

        btn_refresh = QPushButton(i18n.t("na_btn_rf"))
        fd_apply_low_freq_btn(btn_refresh)
        btn_refresh.clicked.connect(self.refresh)
        toolbar.addWidget(btn_refresh)

        toolbar.addStretch()
        l.addLayout(toolbar)

        # LEFT-RIGHT QSplitter：左=报告列表，右=报告内容
        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("WorkspaceSplit")

        # 左侧：历史报告列表
        left_widget = QFrame()
        left_widget.setObjectName("ContentBox")
        fd_apply_content_box(left_widget)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(FD_SPACE_SM)
        left_layout.addWidget(fd_section_bar(i18n.t("na_list_title")))

        self.report_list = QTableWidget()
        self.report_list.setObjectName("NightAuditReportList")
        self.report_list.setColumnCount(2)
        self.report_list.setHorizontalHeaderLabels(
            [i18n.t("na_col_date"), i18n.t("na_col_file")]
        )
        nalist_hdr = self.report_list.horizontalHeader()
        nalist_hdr.setMinimumSectionSize(70)
        nalist_hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        nalist_hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        self.report_list.setEditTriggers(QTableWidget.NoEditTriggers)
        self.report_list.setSelectionBehavior(QTableWidget.SelectRows)
        self.report_list.setAlternatingRowColors(False)
        self.report_list.verticalHeader().setVisible(False)
        self.report_list.clicked.connect(self._load_report)
        left_layout.addWidget(self.report_list)
        fd_apply_table_palette(self.report_list)
        splitter.addWidget(left_widget)

        # 右侧：报告内容
        right_widget = QFrame()
        right_widget.setObjectName("ContentBox")
        fd_apply_content_box(right_widget)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(FD_SPACE_SM)
        right_layout.addWidget(fd_section_bar(i18n.t("na_content_title")))

        self.report_view = QTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setObjectName("NightAuditReport")
        self.report_view.setPlaceholderText(i18n.t("na_content_ph"))
        fd_apply_content_text_edit(self.report_view)
        right_layout.addWidget(self.report_view)
        splitter.addWidget(right_widget)

        splitter.setSizes([300, 600])
        fd_apply_workspace_splitter(splitter)
        l.addWidget(splitter, 1)

        # 底部：超时房间快速概览
        self.overtime_bar = QLabel()
        self.overtime_bar.setObjectName("FdWarnLabel")
        self.overtime_bar.hide()
        l.addWidget(self.overtime_bar)

        fd_refresh_surfaces(self)

    def refresh(self):
        """刷新报告列表和超时房间状态"""
        from audit_engine import AuditEngine

        self.report_list.setRowCount(0)
        reports = AuditEngine.list_audit_reports()
        for path in reports:
            fname = __import__("os").path.basename(path)
            # 从文件名提取日期
            date_part = fname.replace("night_audit_", "").replace(".txt", "")
            r = self.report_list.rowCount()
            self.report_list.insertRow(r)
            self.report_list.setItem(r, 0, QTableWidgetItem(date_part))
            self.report_list.setItem(r, 1, QTableWidgetItem(fname))
            self.report_list.item(r, 0).setData(Qt.UserRole, path)

        # 更新上次夜审时间
        try:
            last = db.execute(
                "SELECT created_at FROM audit_events WHERE event_type='NIGHT_AUDIT' "
                "ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if last:
                self.status_lbl.setText(i18n.t("na_last_fmt").format(last[0][:16]))
            else:
                self.status_lbl.setText(i18n.t("na_last_unknown"))
        except Exception:
            pass

        # 检查超时房间
        try:
            overtime_rooms = db.execute(
                "SELECT room_id FROM rooms WHERE status='OVERTIME'"
            ).fetchall()
            if overtime_rooms:
                ids = ", ".join(r[0] for r in overtime_rooms)
                self.overtime_bar.setText(
                    i18n.t("na_ot_bar").format(len(overtime_rooms), ids)
                )
                self.overtime_bar.show()
            else:
                self.overtime_bar.hide()
        except Exception:
            self.overtime_bar.hide()
        fd_sync_table_height(self.report_list, min_rows=2, max_rows=14)

    def _load_report(self):
        """加载选中的报告内容"""
        row = self.report_list.currentRow()
        if row < 0:
            return
        item = self.report_list.item(row, 0)
        if not item:
            return
        path = item.data(Qt.UserRole)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.report_view.setPlainText(content)
        except Exception as e:
            self.report_view.setPlainText(i18n.t("na_read_fail").format(e))

    def _manual_audit(self):
        """手动触发夜审"""
        play_warn()
        if not ask_confirm(
            self,
            i18n.t("na_confirm_title"),
            i18n.t("na_confirm_body"),
        ):
            return

        try:
            # 获取全局 audit_engine 实例
            from app_main import audit_engine

            report_text = audit_engine.manual_night_audit()
            self.report_view.setPlainText(report_text)
            self.refresh()
            bus.show_success_overlay.emit(i18n.t("na_done_overlay"))
        except ImportError:
            # 如果无法导入，直接创建临时实例执行
            try:
                from audit_engine import AuditEngine

                ae = AuditEngine.__new__(AuditEngine)
                ae._last_night_audit_date = ""
                ae._last_overtime_check = 0.0
                report_text = ae.manual_night_audit()
                self.report_view.setPlainText(report_text)
                self.refresh()
                bus.show_success_overlay.emit(i18n.t("na_done_overlay"))
            except Exception as e:
                play_fail()
                show_warning(
                    self, i18n.t("na_fail_title"), i18n.t("na_fail_body").format(e)
                )
        except Exception as e:
            play_fail()
            show_warning(
                self, i18n.t("na_fail_title"), i18n.t("na_fail_body").format(e)
            )

    def _check_overtime(self):
        """手动检查超时房间"""
        try:
            now_str = __import__("datetime").datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            overdue = db.execute(
                """
                SELECT g.room_id, g.name, g.checkout_time
                FROM guests g
                JOIN rooms r ON r.room_id = g.room_id
                WHERE g.status = 'INHOUSE'
                  AND g.checkout_time IS NOT NULL
                  AND g.checkout_time != ''
                  AND g.checkout_time < ?
                  AND r.status = 'INHOUSE'
            """,
                (now_str,),
            ).fetchall()

            if overdue:
                lines = [i18n.t("na_ot_found_hdr").format(len(overdue))]
                for room_id, guest_name, checkout_time in overdue:
                    lines.append(
                        i18n.t("na_ot_line").format(
                            room_id, guest_name, checkout_time[:16]
                        )
                    )
                    db.execute(
                        "UPDATE rooms SET status='OVERTIME' WHERE room_id=? AND status='INHOUSE'",
                        (room_id,),
                    )
                    bus.room_status_changed.emit(room_id, "OVERTIME")
                play_warn()
                show_warning(
                    self, i18n.t("na_ot_title"), "\n".join(lines)
                )
            else:
                play_notify()
                show_info(
                    self, i18n.t("na_ot_none_title"), i18n.t("na_ot_none_body")
                )

            self.refresh()
        except Exception as e:
            play_fail()
            show_warning(self, i18n.t("na_check_fail"), str(e))
