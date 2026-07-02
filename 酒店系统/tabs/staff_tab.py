"""
Solid PMS — 员工管理标签页
==========================
员工列表 + 添加/编辑 + 权限分配
使用 fd_section_bar() + _p() token + 三级按键体系
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QLineEdit, QComboBox, QHeaderView, QFrame,
    QSizePolicy, QAbstractItemView,
)
from PySide6.QtCore import Qt

from design_tokens import _p, SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, FONT_SM, FONT_MD, FONT_LG, RADIUS_SM, RADIUS_MD, BTN_HEIGHT_SM, BTN_HEIGHT_MD, BTN_HEIGHT_LG
from frontdesk_ui import fd_section_bar, fd_apply_card_action_btn, fd_apply_compact_input, fd_apply_low_freq_btn
from ui_surface import fd_apply_data_table_shell, fd_refresh_surfaces, fd_sync_table_height
from i18n import i18n
from event_bus import bus

logger = logging.getLogger(__name__)


class StaffTab(QWidget):
    """员工管理标签页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StaffTab")
        self._setup_ui()
        bus.theme_changed.connect(lambda _: fd_refresh_surfaces(self))
        bus.user_logged_in.connect(lambda *_: self.refresh())

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        root.setSpacing(SPACE_MD)

        # ── [sub-j] SolidCard 卡片包裹：圆角 10px + 1px panel_border + surface 实底
        # 替换原 ContentBox（gold_thread 左线 + radius 0），整体更精致符合"精品"定位
        content = QFrame()
        content.setObjectName("SolidCard")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.setSpacing(SPACE_MD)

        # ── 搜索行（独立于 section bar，避免 32px 高度挤压）──
        search_row = QHBoxLayout()
        search_row.setSpacing(SPACE_SM)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText(i18n.t("staff_search_ph", default="搜索员工姓名/角色..."))
        self.txt_search.setFixedHeight(BTN_HEIGHT_MD)
        self.txt_search.setMinimumWidth(200)
        fd_apply_compact_input(self.txt_search)
        self.txt_search.textChanged.connect(self._on_search)
        search_row.addWidget(self.txt_search, 1)

        self.btn_add = QPushButton(i18n.t("btn_add_staff", default="+ 添加员工"))
        fd_apply_card_action_btn(self.btn_add)
        self.btn_add.clicked.connect(self._on_add)
        search_row.addWidget(self.btn_add)

        # ── 员工列表标题（section bar 只放标题，不塞搜索控件）──
        section = fd_section_bar(
            i18n.t("staff_list_title", default="员工列表"),
        )
        cl.addWidget(section)

        cl.addLayout(search_row)

        # ── 员工表格 ──
        self.table = QTableWidget(0, 6)
        self.table.setObjectName("StaffTable")
        self.table.setHorizontalHeaderLabels([
            i18n.t("col_name", default="姓名"),
            i18n.t("col_role", default="角色"),
            i18n.t("col_phone", default="手机号"),
            i18n.t("col_status", default="状态"),
            i18n.t("col_last_login", default="最后登录"),
            i18n.t("col_actions", default="操作"),
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)

        table_shell = QFrame()
        table_shell.setObjectName("DataTableShell")
        table_shell.setFrameShape(QFrame.Shape.NoFrame)
        ts_lay = QVBoxLayout(table_shell)
        ts_lay.setContentsMargins(0, 0, 0, 0)
        ts_lay.addWidget(self.table)
        cl.addWidget(table_shell)
        fd_apply_data_table_shell(table_shell, self.table)

        # ── 区段：角色权限概览 ──
        perm_section = fd_section_bar(
            i18n.t("staff_role_perms", default="角色权限概览"),
        )
        cl.addWidget(perm_section)

        self.role_summary = QLabel(i18n.t("staff_role_summary_placeholder", default="加载中..."))
        self.role_summary.setStyleSheet(f"""
            color: {_p("text_muted")};
            font-size: {FONT_SM};
            padding: {SPACE_SM}px;
        """)
        cl.addWidget(self.role_summary)

        root.addWidget(content, 1)

    def _on_search(self, text: str):
        """搜索过滤员工列表。"""
        for row in range(self.table.rowCount()):
            visible = False
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item and text.lower() in item.text().lower():
                    visible = True
                    break
            self.table.setRowHidden(row, not visible)

    def _on_add(self):
        """添加新员工"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox, QLabel
        from database import db
        from permission_system import PermissionManager
        from ui_helpers import style_dialog, build_dialog_header, show_info, show_warning
        from i18n import i18n

        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("staff_add_title", default="添加员工"))
        dlg.setMinimumWidth(360)
        style_dialog(dlg)
        lay = QVBoxLayout(dlg)
        lay.addWidget(build_dialog_header(i18n.t("staff_add_title", default="添加员工"),
                                           i18n.t("staff_add_desc", default="填写新员工信息")))

        form = QFormLayout()
        txt_username = QLineEdit()
        txt_username.setPlaceholderText(i18n.t("staff_ph_username", default="登录账号"))
        form.addRow(i18n.t("staff_username", default="账号") + ":", txt_username)

        txt_password = QLineEdit()
        txt_password.setPlaceholderText(i18n.t("staff_ph_password", default="初始密码"))
        txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(i18n.t("staff_password", default="密码") + ":", txt_password)

        txt_name = QLineEdit()
        txt_name.setPlaceholderText(i18n.t("staff_ph_name", default="员工姓名"))
        form.addRow(i18n.t("staff_name", default="姓名") + ":", txt_name)

        txt_phone = QLineEdit()
        txt_phone.setPlaceholderText(i18n.t("staff_ph_phone", default="手机号"))
        form.addRow(i18n.t("staff_phone", default="手机") + ":", txt_phone)

        cmb_role = QComboBox()
        roles = ["frontdesk", "manager", "admin", "housekeeping", "vendor"]
        for r in roles:
            cmb_role.addItem(PermissionManager.role_display_name(r) if hasattr(PermissionManager, 'role_display_name') else r, r)
        form.addRow(i18n.t("staff_role", default="角色") + ":", cmb_role)

        lay.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        username = txt_username.text().strip()
        password = txt_password.text().strip()
        name = txt_name.text().strip()
        phone = txt_phone.text().strip()
        role = cmb_role.currentData()

        if not username or not password:
            show_warning(self, i18n.t("dlg_tip", default="提示"), i18n.t("staff_add_need_cred", default="账号和密码不能为空"))
            return

        try:
            db.execute(
                "INSERT INTO users (username, password, display_name, phone, role, status) VALUES (?, ?, ?, ?, ?, 'active')",
                (username, password, name or username, phone, role),
            )
            show_info(self, i18n.t("dlg_tip", default="提示"), i18n.t("staff_add_ok", default="员工添加成功"))
            self.refresh()
        except Exception as e:
            show_warning(self, i18n.t("dlg_tip", default="提示"), str(e))

    def _on_edit_staff(self, username: str):
        """编辑员工信息"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox
        from database import db
        from permission_system import PermissionManager
        from ui_helpers import style_dialog, build_dialog_header, show_info, show_warning
        from i18n import i18n

        try:
            row = db.execute("SELECT username, display_name, phone, role, status FROM users WHERE username=?", (username,)).fetchone()
        except Exception as e:
            show_warning(self, i18n.t("dlg_tip"), str(e))
            return
        if not row:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("staff_edit_title", default="编辑员工"))
        dlg.setMinimumWidth(360)
        style_dialog(dlg)
        lay = QVBoxLayout(dlg)
        lay.addWidget(build_dialog_header(i18n.t("staff_edit_title", default="编辑员工"),
                                           f"{i18n.t('staff_username', default='账号')}: {row[0]}"))

        form = QFormLayout()
        txt_name = QLineEdit(row[1] or "")
        form.addRow(i18n.t("staff_name", default="姓名") + ":", txt_name)

        txt_phone = QLineEdit(row[2] or "")
        form.addRow(i18n.t("staff_phone", default="手机") + ":", txt_phone)

        cmb_role = QComboBox()
        roles = ["frontdesk", "manager", "admin", "housekeeping", "vendor"]
        for r in roles:
            cmb_role.addItem(PermissionManager.role_display_name(r) if hasattr(PermissionManager, 'role_display_name') else r, r)
        idx = cmb_role.findData(row[3])
        if idx >= 0:
            cmb_role.setCurrentIndex(idx)
        form.addRow(i18n.t("staff_role", default="角色") + ":", cmb_role)

        cmb_status = QComboBox()
        cmb_status.addItem(i18n.t("status_active", default="启用"), "active")
        cmb_status.addItem(i18n.t("status_disabled", default="禁用"), "disabled")
        sidx = cmb_status.findData(row[4])
        if sidx >= 0:
            cmb_status.setCurrentIndex(sidx)
        form.addRow(i18n.t("staff_status", default="状态") + ":", cmb_status)

        lay.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            db.execute(
                "UPDATE users SET display_name=?, phone=?, role=?, status=? WHERE username=?",
                (txt_name.text().strip() or row[0], txt_phone.text().strip(), cmb_role.currentData(), cmb_status.currentData(), username),
            )
            show_info(self, i18n.t("dlg_tip"), i18n.t("staff_edit_ok", default="员工信息已更新"))
            self.refresh()
        except Exception as e:
            show_warning(self, i18n.t("dlg_tip"), str(e))

    def _on_perm_staff(self, username: str):
        """管理员工权限"""
        from ui_helpers import show_info
        from i18n import i18n
        show_info(self, i18n.t("dlg_tip", default="提示"),
                  i18n.t("staff_perm_hint", default="请在 系统设置 → 角色权限 中管理员工权限"))

    def refresh(self):
        """刷新员工列表 + 角色权限概览。"""
        try:
            from database import db
            rows = db.execute(
                "SELECT username, role, phone, status, last_login FROM users ORDER BY role, username"
            ).fetchall()
            self.table.setRowCount(len(rows))
            for r, row_data in enumerate(rows):
                for c, val in enumerate(row_data):
                    item = QTableWidgetItem(str(val or ""))
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                    self.table.setItem(r, c, item)
                # 操作列
                op_widget = QWidget()
                op_lay = QHBoxLayout(op_widget)
                op_lay.setContentsMargins(SPACE_XS, 0, SPACE_XS, 0)
                op_lay.setSpacing(SPACE_XS)

                btn_edit = QPushButton(i18n.t("btn_edit", default="编辑"))
                fd_apply_low_freq_btn(btn_edit)
                btn_edit.clicked.connect(lambda checked, u=row_data[0]: self._on_edit_staff(u))
                op_lay.addWidget(btn_edit)

                btn_perm = QPushButton(i18n.t("btn_perms", default="权限"))
                fd_apply_low_freq_btn(btn_perm)
                btn_perm.clicked.connect(lambda checked, u=row_data[0]: self._on_perm_staff(u))
                op_lay.addWidget(btn_perm)

                op_lay.addStretch()
                self.table.setCellWidget(r, 5, op_widget)
            fd_sync_table_height(self.table, min_rows=3, max_rows=14)
        except Exception as exc:
            logger.warning("员工列表刷新失败: %s", exc)
            self.role_summary.setText(
                i18n.t("staff_role_summary_load_fail", default="员工列表加载失败：{err}").format(err=exc)
            )
            return

        # 刷新角色权限概览
        self._refresh_role_summary()

    def _refresh_role_summary(self):
        """汇总当前各角色人数与权限数，更新 role_summary 标签。"""
        try:
            from database import db
            from permission_system import get_role_permissions, PermissionManager
            role_count: dict[str, int] = {}
            try:
                rows = db.execute("SELECT role FROM users").fetchall()
                for (r,) in rows:
                    role_count[r or "unknown"] = role_count.get(r or "unknown", 0) + 1
            except Exception:
                pass

            current_role = PermissionManager.current_role()
            current_perms = get_role_permissions(current_role)
            parts = [
                i18n.t("staff_role_summary_current",
                       default="当前角色：{role}（{n} 项权限）").format(
                    role=current_role, n=len(current_perms)),
            ]
            if role_count:
                role_dist = "、".join(f"{k}×{v}" for k, v in sorted(role_count.items()))
                parts.append(i18n.t("staff_role_summary_dist",
                                    default="员工分布：{dist}").format(dist=role_dist))
            self.role_summary.setText("　|　".join(parts))
        except Exception as exc:
            logger.warning("角色权限概览刷新失败: %s", exc)
            self.role_summary.setText(
                i18n.t("staff_role_summary_unavailable", default="角色权限概览暂不可用")
            )
