"""
门卡管理标签 — CardSystemTab（嵌入 WorkspaceDock）
三栏布局：左操作栏 / 右内容区（统计卡片 + 搜索 + 增强表格 + 底部统计）
"""
from __future__ import annotations

from datetime import datetime, timedelta
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QDialog, QFrame, QSplitter, QComboBox, QMenu,
    QAbstractItemView, QInputDialog, QSizePolicy,
)
from database import db
from design_tokens import _p
from event_bus import bus
from i18n import i18n
from ui_helpers import show_info, show_warning, show_error, ask_confirm, style_dialog
from power_controller_config import power_config_summary, resolve_power_config
from ._shared import CARD_BRANDS, REGISTRY_CARD_KINDS, _registry_kind_display
from .card_driver import get_driver
from .card_service import CardService
from .card_open_history import CardOpenHistoryDialog
from .card_settings import CardReaderSettingsDialog
from ui_surface import fd_apply_data_table_shell, fd_refresh_surfaces, fd_apply_content_box


class CardSystemTab(QWidget):
    """门卡系统标签 — 三栏布局"""

    def _status_styles(self) -> dict:
        """运行时读主题色，避免类加载时锁死老钱绿。"""
        return {
            "ACTIVE":       ("🟢", _p("amount_positive"), _p("bg_root"), _p("amount_positive"), "有效"),
            "CANCELLED":    ("🔴", _p("danger"), _p("bg_root"), _p("danger"), "已注销"),
            "EXPIRED":      ("⚫", _p("text_muted"), _p("bg_root"), _p("text_muted"), "已过期"),
            "PENDING":      ("🔵", _p("primary"), _p("bg_root"), _p("primary_hover"), "待写入"),
            "LOST":         ("🟡", _p("accent"), _p("bg_root"), _p("accent"), "已挂失"),
            "LOST_PENDING": ("🟠", _p("accent"), _p("bg_root"), _p("accent"), "挂失中"),
        }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_records: list[dict] = []
        self._filter_status = "ALL"
        self._search_text = ""

        self._build_ui()
        self._install_shortcuts()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._expire_check)
        self._timer.start(30_000)

        QTimer.singleShot(0, self.refresh)
        bus.theme_changed.connect(lambda _: (fd_refresh_surfaces(self), self._refresh_inline_colors()))
        # 登录/角色变更后刷新「发管理卡」按钮可见性
        bus.user_logged_in.connect(lambda *_: self._update_mgmt_card_visibility())

    @staticmethod
    def _set_read_uid_state(lbl: QLabel, state: str = "idle") -> None:
        lbl.setObjectName("CardReadUid")
        lbl.setProperty("state", state)
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)

    def _refresh_inline_colors(self):
        """换主题后重刷门卡页动态区域（容器/按钮走 base.qss）。"""
        self._update_hw_status()
        for val_lbl in self._metric_values.values():
            val_lbl.style().unpolish(val_lbl)
            val_lbl.style().polish(val_lbl)
        if hasattr(self, "lbl_read_uid"):
            self.lbl_read_uid.style().unpolish(self.lbl_read_uid)
            self.lbl_read_uid.style().polish(self.lbl_read_uid)

    # ═══════════════════════════════════════════════════════════
    # UI 构建
    # ═══════════════════════════════════════════════════════════

    def _build_ui(self):
        self.setObjectName("CardSystemRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)
        root.addWidget(self._build_header())

        main_box = QFrame()
        main_box.setObjectName("ContentBox")
        fd_apply_content_box(main_box)
        main_lay = QVBoxLayout(main_box)
        main_lay.setContentsMargins(10, 10, 10, 10)
        main_lay.setSpacing(8)
        main_lay.addWidget(self._build_metrics_row())
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setObjectName("CardSystemSplit")
        action_panel = self._build_action_panel()
        content_panel = self._build_content_panel()
        action_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        content_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        split.addWidget(action_panel)
        split.addWidget(content_panel)
        split.setSizes([170, 720])
        main_lay.addWidget(split, 1)
        root.addWidget(main_box, 1)
        self.stat_lbl = QLabel("")
        self.stat_lbl.setObjectName("Small")
        root.addWidget(self.stat_lbl)

    def _build_header(self) -> QFrame:
        w = QFrame()
        w.setFrameShape(QFrame.Shape.NoFrame)
        w.setObjectName("CardSystemHeader")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 8, 12, 8)
        title_lbl = QLabel("门卡管理中心")
        title_lbl.setObjectName("H2Title")
        lay.addWidget(title_lbl)
        lay.addStretch()
        self.hw_lbl = QLabel("检测中...")
        self.hw_lbl.setObjectName("Small")
        lay.addWidget(self.hw_lbl)
        self.power_lbl = QLabel("")
        self.power_lbl.setObjectName("Tiny")
        lay.addWidget(self.power_lbl)
        btn_settings = QPushButton("设置")
        btn_settings.setObjectName("FdActSecondary")
        btn_settings.clicked.connect(self._open_settings)
        lay.addWidget(btn_settings)
        return w

    def _build_metrics_row(self) -> QFrame:
        w = QFrame()
        w.setFrameShape(QFrame.Shape.NoFrame)
        w.setObjectName("MetricsRow")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)

        self._metric_values: dict[str, QLabel] = {}
        self._metric_cards: list[QFrame] = []
        self._metric_defs = [
            ("today",     "🃏", "今日制卡", "0 张", "primary"),
            ("active",    "✅", "有效卡片", "0 张", "amount_positive"),
            ("expiring",  "⏰", "24小时将过期", "0 张", "accent"),
            ("cancelled", "🚫", "已注销/过期", "0 张", "text_muted"),
        ]
        for mid, icon, label, default, color_key in self._metric_defs:
            card = QFrame()
            card.setObjectName("MetricCard")
            card.setCursor(Qt.PointingHandCursor)
            card.setProperty("highlighted", False)
            card.setMinimumSize(140, 50)
            card.setMaximumSize(240, 80)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 6, 10, 6)
            cl.setSpacing(2)
            row = QHBoxLayout()
            row.setSpacing(6)
            icon_lbl = QLabel(icon)
            icon_lbl.setObjectName("H3Title")
            row.addWidget(icon_lbl)
            name_lbl = QLabel(label)
            name_lbl.setObjectName("Tiny")
            row.addWidget(name_lbl)
            row.addStretch()
            cl.addLayout(row)
            val_lbl = QLabel(default)
            val_lbl.setObjectName("MetricCardValue")
            val_lbl.setProperty("tone", color_key)
            cl.addWidget(val_lbl)
            self._metric_values[label] = val_lbl
            card.mousePressEvent = lambda e, lbl=label: self._on_metric_clicked(lbl)
            lay.addWidget(card)
            self._metric_cards.append(card)

        lay.addStretch()
        return w

    def _set_metric_highlight(self, active_label: str | None) -> None:
        for card, (_, _, label, _, _) in zip(self._metric_cards, self._metric_defs):
            card.setProperty("highlighted", label == active_label if active_label else False)
            card.style().unpolish(card)
            card.style().polish(card)

    def _on_metric_clicked(self, label: str):
        mapping = {
            "今日制卡": "", "有效卡片": "ACTIVE",
            "24小时将过期": "EXPIRING", "已注销/过期": "CANCELLED",
        }
        st = mapping.get(label, "")
        if st:
            self._filter_status = st
            self.txt_search.setPlaceholderText(f"当前筛选：{label}（搜卡号/房号/姓名）")
            self._set_metric_highlight(label)
        else:
            self._filter_status = "ALL"
            self.txt_search.setPlaceholderText("搜索卡号 / 房号 / 客人姓名")
            self._set_metric_highlight(None)
        self._apply_filters()

    def _build_action_panel(self) -> QFrame:
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.NoFrame)
        panel.setObjectName("ActionPanel")
        panel.setMinimumWidth(168)
        panel.setMaximumWidth(200)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(8)

        btn_issue = QPushButton("✦ 制卡")
        btn_issue.setObjectName("SolidPrimaryBtn")
        btn_issue.setStyleSheet("font-weight: 600;")
        btn_issue.clicked.connect(self._issue_card)
        lay.addWidget(btn_issue)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setObjectName("PanelHLine")
        lay.addWidget(sep1)

        btn_reissue = QPushButton("补卡")
        btn_reissue.setObjectName("FdCardActionBtn")
        btn_reissue.clicked.connect(self._reissue_card)
        lay.addWidget(btn_reissue)

        btn_cancel_card = QPushButton("注销卡")
        btn_cancel_card.setObjectName("CardActionBtnDanger")
        btn_cancel_card.clicked.connect(self._cancel_card)
        lay.addWidget(btn_cancel_card)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setObjectName("PanelHLine")
        lay.addWidget(sep2)

        # 管理卡发卡（仅厂家可见）
        self.btn_mgmt_card = QPushButton("发管理卡")
        self.btn_mgmt_card.setObjectName("SolidPrimaryBtn")
        self.btn_mgmt_card.clicked.connect(self._issue_management_card)
        self.btn_mgmt_card.setVisible(False)  # 默认隐藏，仅厂家角色显示
        lay.addWidget(self.btn_mgmt_card)

        for text, icon, cb in (
            ("验卡", "", self._read_card_audit),
            ("管理卡登记", "", self._register_registry),
            ("开门历史", "", self._show_open_history),
            ("手动开门", "", self._log_manual_room_open),
        ):
            btn = QPushButton(text)
            btn.setObjectName("FdCardActionBtn")
            btn.clicked.connect(cb)
            lay.addWidget(btn)

        lay.addStretch()
        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("FdActSecondary")
        btn_refresh.clicked.connect(self.refresh)
        lay.addWidget(btn_refresh)

        return panel

    def _build_content_panel(self) -> QFrame:
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.NoFrame)
        panel.setObjectName("CardTablePanel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("CardSearchInput")
        self.txt_search.setPlaceholderText("搜索卡号 / 房号 / 客人姓名")
        self.txt_search.textChanged.connect(self._on_search_changed)
        filter_row.addWidget(self.txt_search, 1)

        self._status_chips: list[QPushButton] = []
        for st, label in (
            ("ALL", "全部"), ("ACTIVE", "有效"), ("EXPIRING", "即将过期"),
            ("CANCELLED", "已注销"), ("EXPIRED", "已过期"),
        ):
            chip = QPushButton(label)
            chip.setObjectName("CardFilterChip")
            chip.setCheckable(True)
            chip.setChecked(st == "ALL")
            chip.clicked.connect(lambda checked, s=st: self._on_status_chip(s))
            filter_row.addWidget(chip)
            self._status_chips.append(chip)

        filter_row.addStretch()
        self.lbl_record_count = QLabel("")
        self.lbl_record_count.setObjectName("Small")
        filter_row.addWidget(self.lbl_record_count)
        lay.addLayout(filter_row)

        self.table = QTableWidget()
        self.table.setObjectName("CardRegistryTable")
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["卡号", "房间 / 住客", "类型", "制卡时间", "有效期", "状态", "操作"]
        )
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.verticalHeader().setVisible(False)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeToContents)

        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        table_shell = QFrame()
        table_shell.setObjectName("DataTableShell")
        table_shell.setFrameShape(QFrame.Shape.NoFrame)
        table_shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ts_lay = QVBoxLayout(table_shell)
        ts_lay.setContentsMargins(0, 0, 0, 0)
        ts_lay.addWidget(self.table, 1)
        lay.addWidget(table_shell, 1)
        fd_apply_data_table_shell(table_shell, self.table)
        return panel

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        item = self.table.item(row, 0)
        if not item:
            return
        card_id = item.text().strip()
        status_item = self.table.item(row, 5)
        status = status_item.text() if status_item else ""

        menu = QMenu(self)
        if "有效" in status or "ACTIVE" in status:
            menu.addAction("补卡", lambda: self._reissue_card())
            menu.addAction("⏰ 续期（延长 24 小时）", lambda: self._extend_card_expiry(card_id))
            menu.addSeparator()
        if "有效" in status or "待写入" in status or "挂失" in status:
            menu.addAction("注销卡", self._cancel_card)
        menu.addAction("开门历史", self._show_open_history)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _extend_card_expiry(self, card_id: str):
        row = db.execute(
            "SELECT expire_time FROM card_records WHERE card_id=?", (card_id,)
        ).fetchone()
        if not row:
            return
        try:
            old = datetime.strptime(str(row[0]), "%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            show_warning(self, "续期失败", "当前有效期格式异常")
            return
        new = old + timedelta(hours=24)
        db.execute(
            "UPDATE card_records SET expire_time=? WHERE card_id=?",
            (new.strftime("%Y-%m-%d %H:%M:%S"), card_id),
        )
        show_info(self, "续期成功", f"卡 {card_id} 有效期已延长至 {new.strftime('%m-%d %H:%M')}")
        self.refresh()

    def _install_shortcuts(self):
        from PySide6.QtGui import QShortcut, QKeySequence
        for seq, cb in (
            ("Ctrl+I", self._issue_card),
            ("Ctrl+R", self._reissue_card),
            ("Ctrl+D", self._cancel_card),
            ("Ctrl+F", lambda: self.txt_search.setFocus()),
            ("F5", self.refresh),
        ):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(cb)

    # ═══════════════════════════════════════════════════════════
    # 筛选与搜索
    # ═══════════════════════════════════════════════════════════

    def _on_search_changed(self, text: str):
        self._search_text = text.strip()
        QTimer.singleShot(200, self._apply_filters)

    def _on_status_chip(self, status: str):
        self._filter_status = status
        chip_map = {"ALL": "全部", "ACTIVE": "有效", "EXPIRING": "即将过期",
                     "CANCELLED": "已注销", "EXPIRED": "已过期"}
        for chip in self._status_chips:
            chip.setChecked(chip.text() == chip_map.get(status, ""))
        self._apply_filters()

    def _apply_filters(self):
        search = self._search_text.lower()
        status = self._filter_status
        filtered = []
        for rec in self._all_records:
            if status == "ACTIVE" and rec["status"] != "ACTIVE":
                continue
            if status == "CANCELLED" and rec["status"] not in ("CANCELLED", "ERASED"):
                continue
            if status == "EXPIRED" and rec["status"] != "EXPIRED":
                continue
            if status == "EXPIRING":
                if rec["status"] != "ACTIVE":
                    continue
                try:
                    et = datetime.strptime(str(rec["expire_time"] or ""), "%Y-%m-%d %H:%M:%S")
                    if (et - datetime.now()).total_seconds() > 86400:
                        continue
                except (ValueError, AttributeError):
                    continue
            if search:
                cid = (rec.get("card_id") or "").lower()
                rid = (rec.get("room_id") or "").lower()
                gname = (rec.get("guest_name") or "").lower()
                if search not in cid and search not in rid and search not in gname:
                    continue
            filtered.append(rec)
        self._render_table(filtered)

    # ═══════════════════════════════════════════════════════════
    # 表格渲染
    # ═══════════════════════════════════════════════════════════

    def _render_table(self, records: list[dict]):
        self.table.setRowCount(0)
        for rec in records:
            idx = self.table.rowCount()
            self.table.insertRow(idx)
            st = rec.get("status", "")
            dot, color, bg, fg, label = self._status_styles().get(
                st, ("⚪", _p("text_dim"), _p("bg_root"), _p("text"), st)
            )
            rk = rec.get("registry_kind", "guest")

            item_card_id = QTableWidgetItem(str(rec.get("card_id", "")))
            item_card_id.setFont(QFont("", -1, QFont.Weight.Bold))
            self.table.setItem(idx, 0, item_card_id)

            room_id = str(rec.get("room_id", "") or "")
            guest_name = str(rec.get("guest_name", "") or "")
            if room_id == "__REGISTRY__":
                room_display = "🏢 " + guest_name
            elif guest_name:
                room_display = f"{room_id} · {guest_name}"
            else:
                room_display = room_id
            self.table.setItem(idx, 1, QTableWidgetItem(room_display))

            self.table.setItem(idx, 2, QTableWidgetItem(_registry_kind_display(rk)))

            issue_time = str(rec.get("issue_time", "") or "")
            self.table.setItem(idx, 3, QTableWidgetItem(self._smart_time(issue_time)))

            expire_time = str(rec.get("expire_time", "") or "")
            display_expire = self._smart_time(expire_time)
            item_expire = QTableWidgetItem(display_expire)
            if st == "ACTIVE":
                try:
                    et = datetime.strptime(expire_time, "%Y-%m-%d %H:%M:%S")
                    if (et - datetime.now()).total_seconds() < 86400:
                        item_expire.setForeground(QColor(_p('danger')))
                        item_expire.setToolTip("⚠ 即将在 24 小时内过期")
                except (ValueError, AttributeError):
                    pass
            self.table.setItem(idx, 4, item_expire)

            status_item = QTableWidgetItem(f"{dot} {label}")
            status_item.setForeground(QColor(color))
            status_item.setFont(QFont("", -1, QFont.Weight.Bold))
            self.table.setItem(idx, 5, status_item)

            action_widget = self._build_row_actions(rec)
            self.table.setCellWidget(idx, 6, action_widget)

            for c in range(self.table.columnCount()):
                it = self.table.item(idx, c)
                if it:
                    it.setBackground(QColor(bg if st != "ACTIVE" else _p('surface')))

        self.lbl_record_count.setText(f"{self.table.rowCount()} 条")
        self._update_hw_status()

    def _smart_time(self, time_str: str) -> str:
        if not time_str:
            return "—"
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            return time_str[:16] if len(time_str) >= 16 else time_str
        now = datetime.now()
        if dt.date() == now.date():
            return dt.strftime("今天 %H:%M")
        yesterday = now - timedelta(days=1)
        if dt.date() == yesterday.date():
            return dt.strftime("昨天 %H:%M")
        return dt.strftime("%m-%d %H:%M")

    def _build_row_actions(self, rec: dict) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(2)
        st = rec.get("status", "")
        card_id = rec.get("card_id", "")

        if st == "ACTIVE":
            btn_reissue = QPushButton("🔄")
            btn_reissue.setObjectName("CardTableActionBtn")
            btn_reissue.setFixedSize(24, 24)
            btn_reissue.setToolTip("补卡")
            btn_reissue.clicked.connect(lambda: self._do_reissue_for(card_id))
            lay.addWidget(btn_reissue)

            btn_extend = QPushButton("⏰")
            btn_extend.setObjectName("CardTableActionBtn")
            btn_extend.setFixedSize(24, 24)
            btn_extend.setToolTip("续期 +24 小时")
            btn_extend.clicked.connect(lambda: self._extend_card_expiry(card_id))
            lay.addWidget(btn_extend)

        if st in ("ACTIVE", "PENDING", "LOST", "LOST_PENDING"):
            btn_cancel = QPushButton("🚫")
            btn_cancel.setObjectName("CardTableActionBtn")
            btn_cancel.setFixedSize(24, 24)
            btn_cancel.setToolTip("注销")
            btn_cancel.clicked.connect(lambda: self._do_cancel_for(card_id, rec))
            lay.addWidget(btn_cancel)

        btn_history = QPushButton("📊")
        btn_history.setObjectName("CardTableActionBtn")
        btn_history.setFixedSize(24, 24)
        btn_history.setToolTip("开门历史")
        btn_history.clicked.connect(lambda: self._show_history_for(card_id))
        lay.addWidget(btn_history)

        lay.addStretch()
        return w

    def _do_reissue_for(self, card_id: str):
        row_data = db.execute(
            "SELECT room_id, COALESCE(guest_name,'') FROM card_records WHERE card_id=?",
            (card_id,),
        ).fetchone()
        if not row_data:
            return
        room_id, guest_name = row_data[0], row_data[1]
        from card_ritual_dialog import CardRitualDialog
        dlg = CardRitualDialog(
            self, room_id=room_id, guest_name=guest_name,
            old_card_id=card_id, mode="reissue"
        )
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            bus.show_success_overlay.emit(f"补卡成功：{dlg.result_card_id}")

    def _do_cancel_for(self, card_id: str, rec: dict):
        if not ask_confirm(self, "确认注销",
            f"确定要注销卡号 {card_id} 吗？\n注销后该卡将无法开门。"):
            return
        ok, msg = CardService.cancel_card(card_id)
        if ok:
            self.refresh()
            bus.show_success_overlay.emit(f"卡 {card_id} 已注销")
        else:
            show_error(self, "注销失败", msg)

    def _show_history_for(self, card_id: str):
        CardOpenHistoryDialog(self, card_id).exec()

    # ═══════════════════════════════════════════════════════════
    # 统计更新
    # ═══════════════════════════════════════════════════════════

    def _update_metrics(self, records: list[dict]):
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        today_count = sum(
            1 for r in records
            if (r.get("issue_time") or "").startswith(today_str)
        )
        active_count = sum(1 for r in records if r["status"] == "ACTIVE")
        cancelled_count = sum(1 for r in records if r["status"] in ("CANCELLED", "ERASED", "EXPIRED"))
        expiring_count = 0
        for r in records:
            if r["status"] == "ACTIVE":
                try:
                    et = datetime.strptime(str(r.get("expire_time") or ""), "%Y-%m-%d %H:%M:%S")
                    if 0 < (et - now).total_seconds() < 86400:
                        expiring_count += 1
                except (ValueError, AttributeError):
                    pass
        self._metric_values["今日制卡"].setText(f"{today_count} 张")
        self._metric_values["有效卡片"].setText(f"{active_count} 张")
        self._metric_values["24小时将过期"].setText(f"{expiring_count} 张")
        self._metric_values["已注销/过期"].setText(f"{cancelled_count} 张")
        total = len(records)
        self.stat_lbl.setText(
            f"共 {total} 条记录 | 有效 {active_count} 张 | "
            f"今日制卡 {today_count} | 即将过期 {expiring_count}"
        )

    # ═══════════════════════════════════════════════════════════
    # 硬件状态
    # ═══════════════════════════════════════════════════════════

    def _update_hw_status(self):
        driver = get_driver()
        if not driver.is_connected():
            driver.connect()
        if driver.is_connected():
            brand_name = CARD_BRANDS.get(driver.brand, {}).get("name", "未知")
            sim = "（模拟）" if driver._simulate else ""
            self.hw_lbl.setText(f"{brand_name}{sim}")
            self.hw_lbl.setProperty("hwState", "ok")
        else:
            self.hw_lbl.setText("读卡器未连接")
            self.hw_lbl.setProperty("hwState", "error")
        self.hw_lbl.style().unpolish(self.hw_lbl)
        self.hw_lbl.style().polish(self.hw_lbl)
        pc = resolve_power_config()
        self.power_lbl.setText(power_config_summary(pc))

    # ═══════════════════════════════════════════════════════════
    # 操作方法
    # ═══════════════════════════════════════════════════════════

    def _pick_room_for_open(self, card_id: str, hint: str = "") -> str:
        rooms = db.execute(
            "SELECT room_id FROM rooms ORDER BY CAST(room_id AS INTEGER), room_id"
        ).fetchall()
        room_ids = [str(r[0]) for r in rooms if r and r[0]]
        default = hint if hint in room_ids else (room_ids[0] if room_ids else "")
        rid, ok = QInputDialog.getText(
            self, "选择房间", f"请输入 {card_id} 要开的房间号：", text=default,
        )
        if not ok:
            return ""
        return (rid or "").strip()

    def _read_card_audit(self):
        driver = get_driver()
        if not driver.is_connected():
            ok, msg = driver.connect()
            if not ok:
                show_warning(self, "验卡失败", msg)
                return
        ok, uid = driver.read_card_uid()
        if not ok:
            show_warning(self, "验卡失败", str(uid))
            return
        op = CardService._effective_operator("FRONTDESK")
        row = db.execute(
            """SELECT room_id, guest_name, status, COALESCE(registry_kind,'guest')
               FROM card_records WHERE card_id=? ORDER BY issue_time DESC LIMIT 1""",
            (uid,),
        ).fetchone()
        registry = False
        if row:
            rid, gname, st, rk = row[0], row[1], row[2], row[3]
            registry = (str(rk or "").lower() in REGISTRY_CARD_KINDS) or str(rid or "") == "__REGISTRY__"
            note = f"{gname or ''} {st or ''}".strip()
        else:
            rid, note, registry = "", "未注册", False
        source = "read_swipe"
        if registry or not db.is_trackable_room_id(str(rid or "")):
            picked = self._pick_room_for_open(uid)
            if not picked:
                return
            rid = picked
            source = "master_open"
            note = (note + " 总卡刷卡").strip()
        try:
            db.log_door_open_event(rid, uid, source, op, 1, note)
        except Exception as e:
            show_warning(self, "验卡失败", str(e))
            return
        show_info(self, "验卡成功",
                  f"卡号：{uid}\n房间：{rid or '-'}\n来源：{source}")
        self.refresh()
        self._update_hw_status()

    def _open_settings(self):
        dlg = CardReaderSettingsDialog(self)
        dlg.exec()
        self._update_hw_status()

    def _selected_card_id(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        it = self.table.item(row, 0)
        return (it.text() if it else "").strip()

    def _show_open_history(self):
        cid = self._selected_card_id()
        if not cid:
            show_warning(self, "提示", "请先在列表中选择一张卡")
            return
        CardOpenHistoryDialog(self, cid).exec()

    def _log_manual_room_open(self):
        cid = self._selected_card_id()
        if not cid:
            show_warning(self, "提示", "请先在列表中选择一张卡")
            return
        rid = self._pick_room_for_open(cid)
        if not rid or not db.is_trackable_room_id(rid):
            show_warning(self, "提示", "无效房间号")
            return
        op = CardService._effective_operator("FRONTDESK")
        db.log_door_open_event(rid, cid, "master_open", op, 1, "手动开门")
        show_info(self, "提示", f"已记录开门：{rid} · {cid}")
        self.refresh()

    def _register_registry(self):
        from PySide6.QtWidgets import QFrame as QF
        d = QDialog(self)
        d.setWindowTitle("登记管理卡")
        d.setMinimumWidth(480)
        d.setMinimumHeight(320)
        lay = QVBoxLayout(d)
        lay.setSpacing(16)
        lay.setContentsMargins(20, 20, 20, 20)

        title = QLabel("登记管理卡")
        title.setObjectName("H2Title")
        lay.addWidget(title)
        subtitle = QLabel("支持读卡器自动读取 UID（推荐）或手动输入卡号")
        subtitle.setObjectName("Small")
        subtitle.setWordWrap(True)
        lay.addWidget(subtitle)

        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        type_row.addWidget(QLabel("卡片类型："))
        cmb = QComboBox()
        cmb.setMinimumWidth(180)
        for kind in REGISTRY_CARD_KINDS:
            cmb.addItem(_registry_kind_display(kind), kind)
        type_row.addWidget(cmb)
        type_row.addStretch()
        lay.addLayout(type_row)

        sep = QF()
        sep.setFrameShape(QF.HLine)
        sep.setObjectName("PanelHLine")
        lay.addWidget(sep)

        read_label = QLabel("📡 读卡器获取")
        read_label.setObjectName("H4Title")
        lay.addWidget(read_label)
        read_hint = QLabel("将管理卡贴近读卡器，点击下方按钮自动读取 UID")
        read_hint.setObjectName("Small")
        read_hint.setWordWrap(True)
        lay.addWidget(read_hint)

        read_row = QHBoxLayout()
        read_row.setSpacing(8)
        self.lbl_read_uid = QLabel("— 未读取 —")
        self._set_read_uid_state(self.lbl_read_uid, "idle")
        read_row.addWidget(self.lbl_read_uid)
        btn_read = QPushButton("读取卡片")
        btn_read.setObjectName("SolidPrimaryBtn")
        btn_read.clicked.connect(self._on_registry_read_card)
        read_row.addWidget(btn_read)
        lay.addLayout(read_row)

        sep2 = QF()
        sep2.setFrameShape(QF.HLine)
        sep2.setObjectName("PanelHLine")
        lay.addWidget(sep2)

        manual_label = QLabel("手动输入（无读卡器时使用）")
        manual_label.setObjectName("H4Title")
        lay.addWidget(manual_label)

        from PySide6.QtWidgets import QFormLayout as QFL
        f = QFL()
        f.setSpacing(10)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        txt_id = QLineEdit()
        txt_id.setPlaceholderText("卡号 / UID（十六进制）")
        txt_lbl = QLineEdit()
        txt_lbl.setPlaceholderText("位置或持有人说明（可选）")
        f.addRow("卡号：", txt_id)
        f.addRow("备注：", txt_lbl)
        lay.addLayout(f)

        from PySide6.QtWidgets import QPushButton as QPB
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_ok = QPushButton("保存登记")
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("FdGhostBtn")
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        lay.addLayout(btn_row)

        btn_cancel.clicked.connect(d.reject)
        txt_id.textChanged.connect(lambda: self.lbl_read_uid.setText("— 未读取 —"))

        def _do():
            kind = cmb.currentData() or "master"
            read_val = self.lbl_read_uid.text().strip()
            if read_val and read_val != "— 未读取 —" and read_val != "读取失败":
                card_id = read_val
            else:
                card_id = txt_id.text().strip()
            if not card_id:
                show_warning(self, "登记失败", "请先读取卡片或手动输入卡号")
                return
            op = "FRONTDESK"
            try:
                from permission_system import PermissionManager
                u = PermissionManager.current_user()
                if u:
                    op = u.get("username") or op
            except Exception:
                pass
            ok, msg = CardService.register_registry_card(card_id, str(kind), txt_lbl.text(), op)
            if ok:
                d.accept()
                show_info(self, "已保存", f"已登记卡号 {msg}")
            else:
                show_warning(self, "登记失败", msg)

        btn_ok.clicked.connect(_do)
        if d.exec():
            self.refresh()

    def _on_registry_read_card(self):
        self.lbl_read_uid.setText("⏳ 正在读取...")
        self.lbl_read_uid.repaint()
        try:
            from lock_adapters.prousb_v9 import ProuSBV9Adapter
            adapter = ProuSBV9Adapter()
            ok, raw = adapter.read_card_payload()
            if ok and raw:
                uid_hex = raw.strip().upper()
                if uid_hex:
                    self.lbl_read_uid.setText(uid_hex)
                    self._set_read_uid_state(self.lbl_read_uid, "ok")
                else:
                    self.lbl_read_uid.setText("读取失败")
                    self._set_read_uid_state(self.lbl_read_uid, "error")
        except Exception as e:
            self.lbl_read_uid.setText("读取失败")
            self._set_read_uid_state(self.lbl_read_uid, "error")

    def _issue_card(self):
        room_id = ""
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row, 1):
            room_text = self.table.item(row, 1).text()
            if "·" in room_text:
                room_id = room_text.split("·")[0].strip()
            else:
                room_id = room_text
        from card_ritual_dialog import CardRitualDialog
        dlg = CardRitualDialog(self, room_id=room_id, guest_name="", mode="issue")
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            bus.show_success_overlay.emit(f"制卡成功：{dlg.result_card_id}")

    def _reissue_card(self):
        row = self.table.currentRow()
        if row < 0:
            show_warning(self, "请先在列表中选择要补卡的记录")
            return
        card_id = self.table.item(row, 0).text() if self.table.item(row, 0) else ""
        room_text = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
        room_id = room_text.split("·")[0].strip() if "·" in room_text else room_text
        guest_name = room_text.split("·")[1].strip() if "·" in room_text else ""

        from card_ritual_dialog import CardRitualDialog
        dlg = CardRitualDialog(
            self, room_id=room_id, guest_name=guest_name,
            old_card_id=card_id, mode="reissue"
        )
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            bus.show_success_overlay.emit(f"补卡成功：{dlg.result_card_id}")

    def _cancel_card(self):
        row = self.table.currentRow()
        if row < 0:
            show_warning(self, "请先在列表中选择要注销的门卡")
            return
        card_id = self.table.item(row, 0).text() if self.table.item(row, 0) else ""
        status_item = self.table.item(row, 5)
        status_text = status_item.text() if status_item else ""
        if "已注销" in status_text or "已过期" in status_text:
            show_warning(self, "该卡已注销或已过期，无需再次注销")
            return
        if not ask_confirm(self, "确认注销",
            f"确定要注销卡号 {card_id} 吗？\n注销后该卡将无法开门。"):
            return
        ok, msg = CardService.cancel_card(card_id)
        if ok:
            self.refresh()
            bus.show_success_overlay.emit(f"卡 {card_id} 已注销")
        else:
            show_error(self, "注销失败", msg)

    def _expire_check(self):
        CardService.expire_overdue_cards()
        self.refresh()

    def refresh(self):
        status_filter = self._filter_status if self._filter_status != "EXPIRING" else "ALL"
        self._all_records = CardService.get_all_cards(status_filter)
        self._update_metrics(self._all_records)
        self._apply_filters()
        self._update_mgmt_card_visibility()

    def _update_mgmt_card_visibility(self):
        """仅厂家/管理员角色显示「发管理卡」按钮。"""
        try:
            from permission_system import PermissionManager
            u = PermissionManager.current_user()
            role = (u.get("role") or "").lower() if u else ""
            visible = role in ("vendor", "factory", "admin", "superadmin", "厂家")
            self.btn_mgmt_card.setVisible(visible)
        except Exception:
            self.btn_mgmt_card.setVisible(False)

    def _issue_management_card(self):
        """发管理卡：总卡/楼栋卡/楼层卡/应急卡。"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QDateEdit, QSpinBox
        from PySide6.QtCore import QDate
        from ui_helpers import show_info, show_warning
        from database import db

        # 检查是否有可用的适配器
        try:
            from lock_adapters import get_adapter_for_brand
            # 加载已学习的品牌
            brand = db.get_config("lock_takeover_brand") or "CardLockAuto"
            install_dir = db.get_config("lock_takeover_install_dir") or ""
            from pathlib import Path
            adapter = get_adapter_for_brand(brand, Path(install_dir) if install_dir else None)
        except Exception:
            adapter = None

        if adapter is None:
            show_warning(self, "无适配器",
                "请先在「厂家控制台 → 门锁品牌」点「导入握手包」导入 .solidhandover。")
            return

        # 管理卡选择对话框
        dlg = QDialog(self)
        dlg.setWindowTitle("发管理卡")
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        layout.addWidget(QLabel("选择管理卡类型，点击「发卡」后将卡片放在发卡器上。"))

        warning_lbl = QLabel("⚠ 管理卡权限极高，请谨慎操作！")
        warning_lbl.setObjectName("DangerNote")
        layout.addWidget(warning_lbl)

        mgmt_card_types = ["总卡", "楼栋卡", "楼层卡", "应急卡"]
        combo = QComboBox()
        combo.addItems(mgmt_card_types)
        layout.addWidget(QLabel("管理卡类型:"))
        layout.addWidget(combo)

        bd = QDateEdit()
        bd.setCalendarPopup(True)
        bd.setDate(QDate.currentDate())
        bd.setDisplayFormat("yyyy-MM-dd")
        layout.addWidget(QLabel("起始日期:"))
        layout.addWidget(bd)

        ed = QDateEdit()
        ed.setCalendarPopup(True)
        ed.setDate(QDate.currentDate().addYears(1))
        ed.setDisplayFormat("yyyy-MM-dd")
        layout.addWidget(QLabel("截止日期:"))
        layout.addWidget(ed)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        def do_issue():
            dlg.accept()
            card_type_map = {"总卡": "master", "楼栋卡": "building",
                             "楼层卡": "floor", "应急卡": "emergency"}
            card_type = card_type_map.get(combo.currentText(), "master")
            b_str = bd.date().toString("yyMMdd")
            e_str = ed.date().toString("yyMMdd")

            try:
                if not adapter.is_open:
                    if hasattr(adapter, 'auto_configure'):
                        adapter.auto_configure()
                    ok = adapter.initialize()
                    if not ok:
                        show_warning(self, "初始化失败",
                            "无法连接发卡器，请检查设备。")
                        return

                if card_type == "master":
                    result = adapter.issue_master_card(b_date=b_str, e_date=e_str)
                elif card_type == "building":
                    result = adapter.issue_building_card(b_date=b_str, e_date=e_str)
                elif card_type == "floor":
                    result = adapter.issue_floor_card(b_date=b_str, e_date=e_str)
                elif card_type == "emergency":
                    result = adapter.issue_emergency_card(b_date=b_str, e_date=e_str)
                else:
                    show_warning(self, "未知类型", f"不支持的管理卡类型: {card_type}")
                    return

                if result and result.success:
                    show_info(self, "发卡成功",
                        f"已成功发出{combo.currentText()}\n卡片数据: {result.payload[:32]}...")
                else:
                    err = result.message if result else "未知错误"
                    show_warning(self, "发卡失败", str(err))
            except Exception as e:
                show_warning(self, "异常", f"发管理卡时出错:\n{e}")
            finally:
                try:
                    adapter.close()
                except Exception:
                    pass

        from PySide6.QtWidgets import QPushButton
        btn_ok = QPushButton("发卡")
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_ok.clicked.connect(do_issue)
        btn_row.addWidget(btn_ok)

        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        dlg.exec()
