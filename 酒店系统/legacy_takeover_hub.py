"""
legacy_takeover_hub.py — 客户老系统「一键整合」总控台（省心向导版）
================================================================
目标：把旧系统 **软硬件与数据** 接到本系统，**利他、少给客户添麻烦** ——
路径写清、状态可见、工具一键可达；子能力仍由成熟模块实现。

能力矩阵：
  1. 一键接管 SQLite 旧库目录或文件 → one_click_migration
  2. 老库四步向导（Access/DBF/复杂库）→ legacy_migration 向导
  3. USB 门锁密钥与品牌 → usb_lock_migrate_dialog
  4. 发卡串口嗅探 → card_sniffer
  5. 数据与迁移中心（CSV/备份/密钥）→ data_import_service

入口：顶栏旧系统对接、设置内按钮、快捷键
"""
from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QTextEdit,
    QWidget,
    QGridLayout,
    QScrollArea,
    QSizePolicy,
)

from database import db
from i18n import i18n
from ui_helpers import style_dialog, build_dialog_header, show_warning, show_info, show_error, ask_confirm
from design_tokens import _p
from legacy_migration import find_cardlock_mdb_paths
from legacy_flow_guide import cardlock_flow, hub_cardlock_flow, sync_hub_from_cardlock
from legacy_migration_guide import GuideAction, hub_path_session
from migration_guide_panel import MigrationGuidePanel


def _count_table(table: str) -> int:
    """统计指定表的行数。

    [sub-e] SQL 注入加固：table 必须在 database._ALLOWED_TABLES 白名单中，
    否则 raise ValueError；FROM 后表名用 [table] 方括号包裹（SQLite 标识符语法）。
    调用方（_checklist_text 等）目前传入 rooms / guests / card_records 等硬编码值，
    白名单主要防万一调用方被改成接收用户输入。
    """
    try:
        # [sub-e] 防御性 import：避免模块加载顺序问题
        from database import _ALLOWED_TABLES, _validate_identifier
        safe_table = _validate_identifier(table, _ALLOWED_TABLES)
        row = db.execute(f"SELECT COUNT(*) FROM {safe_table}").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _checklist_text() -> str:
    """给客户看的接好了没有清单，纯文本便于复制给售后。"""
    lines: list[str] = []
    try:
        rooms = _count_table("rooms")
        guests = _count_table("guests")
        cards = _count_table("card_records")
        lines.append(f"  [{'✓' if rooms else '○'}] 本机房间条数: {rooms}")
        lines.append(f"  [{'✓' if guests else '○'}] 本机客人条数: {guests}")
        lines.append(f"  [{'✓' if cards else '○'}] 发卡历史 card_records: {cards}")

        last_ok = db.get_config("takeover_last_ok_at") or ""
        lines.append(f"  [{'✓' if last_ok else '○'}] 最近一次一键接管成功时间: {last_ok or '尚未成功过'}")

        src = db.get_config("takeover_last_source") or ""
        if src:
            lines.append(f"      源路径: {src}")

        brand = db.get_config("lock_brand_name") or db.get_config("lock_brand") or ""
        mig = db.get_config("lock_migrated_at") or ""
        lines.append(f"  [{'✓' if mig else '○'}] USB 门锁迁移: {mig or '未记录'}  品牌: {brand or '—'}")

        lk_raw = db.get_config("legacy_lock_keys") or "{}"
        lk = json.loads(lk_raw) if lk_raw else {}
        nkeys = len(lk) if isinstance(lk, dict) else 0
        lines.append(f"  [{'✓' if nkeys else '○'}] 卡密/门锁线索 legacy_lock_keys 条目: {nkeys}")

        csys = db.get_config("card_system") or db.get_config("card_brand") or "—"
        lines.append(f"  [{'✓' if csys != '—' else '○'}] 发卡读卡策略: {csys}")
    except Exception as e:
        lines.append(f"  (!) 读取清单时出错: {e}")
    return "\n".join(lines)


def _status_snapshot() -> str:
    """详细状态块（与清单互补）。"""
    lines: list[str] = []
    lines.append("── 配置快照 ──")
    lines.extend(_checklist_text().split("\n"))
    lines.append("")
    lines.append("── 说明 ──")
    lines.append("• 智能门锁换系统：请用「前台门锁对接」，按窗内 ①→⑤ 操作。")
    lines.append("• 其它旧软件：一般不用点下面灰色按钮；需要时找售后。")
    lines.append("• 单独补密钥：可用 U 盘迁移或发卡时抓密钥。")
    return "\n".join(lines)


class LegacyTakeoverHubDialog(QDialog):
    """向导式总控，弱化形同虚设的入口感。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("takeover_hub_window_title"))
        style_dialog(self, size="large")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        root = QVBoxLayout(inner)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(
            build_dialog_header(
                i18n.t("takeover_hub_h1"),
                i18n.t("takeover_hub_sub"),
            )
        )

        phil = QLabel(i18n.t("takeover_hub_philosophy"))
        phil.setWordWrap(True)
        phil.setStyleSheet(
            f"color:{_p('text')}; background:{_p('surface_alt')}; border:1px solid {_p('amount_positive')}; "
            "border-radius:10px; padding:12px 14px; font-size:13px;"
        )
        root.addWidget(phil)

        hint = QLabel(i18n.t("takeover_hub_order_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{_p('text_muted')}; background:{_p('surface_alt')}; border:1px solid {_p('border')}; "
            "border-radius:10px; padding:10px 12px; font-size:12px;"
        )
        root.addWidget(hint)

        self.guide_panel = MigrationGuidePanel(on_action=self._on_hub_guide_action)
        root.addWidget(self.guide_panel)

        self.lbl_cardlock_guard = QLabel()
        self.lbl_cardlock_guard.setWordWrap(True)
        self.lbl_cardlock_guard.setStyleSheet(
            f"color:{_p('accent')}; background:{_p('surface_alt')}; border:2px solid {_p('accent')}; "
            "border-radius:10px; padding:10px 12px; font-size:12px; font-weight:600;"
        )
        root.addWidget(self.lbl_cardlock_guard)

        ck = QGroupBox(i18n.t("takeover_hub_checklist_title"))
        ck_l = QVBoxLayout(ck)
        self.txt_check = QTextEdit()
        self.txt_check.setReadOnly(True)
        self.txt_check.setMinimumHeight(120)
        self.txt_check.setMaximumHeight(200)
        self.txt_check.setStyleSheet("font-family: Consolas, 'Cascadia Mono', monospace; font-size:12px;")
        ck_l.addWidget(self.txt_check)
        root.addWidget(ck)

        grp = QGroupBox(i18n.t("takeover_hub_actions"))
        gl = QGridLayout(grp)
        gl.setSpacing(10)

        def big_btn(text: str, slot, col: int, row: int, colspan: int = 1, obj_name: str = "") -> None:
            b = QPushButton(text)
            b.setObjectName("FdGhostBtn")
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(48)
            if obj_name:
                b.setObjectName(obj_name)
            b.clicked.connect(slot)
            gl.addWidget(b, row, col, 1, colspan)

        big_btn(i18n.t("takeover_hub_btn_frontdesk"), self._open_frontdesk, 0, 0, 2, "SolidPrimaryBtn")
        big_btn(i18n.t("takeover_hub_btn_one_click"), self._open_one_click, 0, 1, 1, "SolidPrimaryBtn")
        big_btn(i18n.t("takeover_hub_btn_legacy_wizard"), self._open_legacy_wizard, 1, 1, 1, "FdGhostBtn")
        big_btn(i18n.t("takeover_hub_btn_usb_lock"), self._open_usb_lock, 0, 2, 1, "FdGhostBtn")
        big_btn(i18n.t("takeover_hub_btn_sniffer"), self._open_sniffer, 1, 2, 1, "FdGhostBtn")
        big_btn(i18n.t("takeover_hub_btn_data_center"), self._open_data_import, 0, 3, 2, "FdGhostBtn")
        big_btn(i18n.t("takeover_hub_btn_redist"), self._open_redist_installers, 0, 4, 2, "FdGhostBtn")
        big_btn(
            i18n.t("takeover_hub_btn_takeover_report"),
            self._open_takeover_report,
            0, 5, 2, "FdGhostBtn",
        )
        big_btn(
            i18n.t("takeover_hub_btn_brand_learn"),
            self._open_brand_learn,
            1, 5, 2, "FdGhostBtn",
        )
        root.addWidget(grp)

        st = QGroupBox(i18n.t("takeover_hub_status"))
        sl = QVBoxLayout(st)
        self.txt_status = QTextEdit()
        self.txt_status.setReadOnly(True)
        self.txt_status.setMinimumHeight(100)
        self.txt_status.setPlaceholderText(i18n.t("takeover_hub_status_ph"))
        sl.addWidget(self.txt_status, 1)
        row = QHBoxLayout()
        rb = QPushButton(i18n.t("takeover_hub_refresh"))
        rb.setObjectName("FdGhostBtn")
        rb.clicked.connect(self._refresh)
        row.addWidget(rb)
        row.addStretch()
        sl.addLayout(row)
        root.addWidget(st, 1)

        peace = QLabel(i18n.t("takeover_hub_peace_line"))
        peace.setWordWrap(True)
        peace.setStyleSheet(f"color:{_p('text_muted')}; font-size:11px;")
        root.addWidget(peace)

        row2 = QHBoxLayout()
        row2.addStretch()
        close = QPushButton(i18n.t("takeover_hub_close"))
        close.setObjectName("FdGhostBtn")
        close.clicked.connect(self.accept)
        row2.addWidget(close)
        root.addLayout(row2)

        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self._refresh()

    def _on_hub_guide_action(self, action: str) -> None:
        if action == GuideAction.OPEN_FRONTDESK:
            self._open_frontdesk()
        elif action == GuideAction.START_ONECLICK:
            self._open_one_click()

    def _refresh(self) -> None:
        self.txt_check.setPlainText(_checklist_text())
        self.txt_status.setPlainText(_status_snapshot())
        self._update_cardlock_guard()
        cf = cardlock_flow()
        self.guide_panel.bind_session(
            hub_path_session(
                is_cardlock=self._is_cardlock_hotel(),
                frontdesk_opened=cf.is_done("preflight") or hub_cardlock_flow().is_done("open_frontdesk"),
            )
        )

    def _is_cardlock_hotel(self) -> bool:
        if db.get_config("legacy_takeover_kind") == "cardlock_frontdesk":
            return True
        if db.get_config("cardlock_mdb_path"):
            return True
        return bool(find_cardlock_mdb_paths())

    def _update_cardlock_guard(self) -> None:
        if not self._is_cardlock_hotel():
            self.lbl_cardlock_guard.setText("")
            self.lbl_cardlock_guard.setVisible(False)
            return
        self.lbl_cardlock_guard.setVisible(True)
        cf = cardlock_flow()
        self.lbl_cardlock_guard.setText(
            "【重要】本店是智能门锁换系统 — 请只点上面绿色大按钮前台门锁对接，"
            "在弹出窗口里按 ①到⑤ 顺序点（跳步会提醒）。\n"
            "请不要在本页先点下面其它按钮。\n"
            "您当前进度：" + cf.status_line()
        )

    def _block_if_cardlock_wrong_entry(self, action: str) -> bool:
        """若应先走前台向导却点了其它入口，返回 True 表示已拦截。"""
        if not self._is_cardlock_hotel():
            return False
        cf = cardlock_flow()
        if action == "frontdesk":
            return False
        if not cf.is_done("verify"):
            cur = cf.current_step()
            show_warning(
                self,
                "请按顺序操作",
                f"您正在使用智能门锁换系统流程。\n\n"
                f"请勿在此直接点「{action}」。\n\n"
                f"👉 请先打开【前台门锁对接】，完成：\n"
                f"   {cur.title}\n\n"
                f"完整顺序：① 预检 → ② 导入 → ③ USB → ④ 嗅探（可选）→ ⑤ 验收",
            )
            return True
        return False

    def _open_frontdesk(self) -> None:
        try:
            from cardlock_frontdesk import open_cardlock_frontdesk

            hub_cardlock_flow().mark_done("open_frontdesk")
            open_cardlock_frontdesk(self)
            sync_hub_from_cardlock()
            self._refresh()
        except Exception as e:
            show_warning(self, i18n.t("takeover_hub_err_title"), str(e))

    def _open_one_click(self) -> None:
        if self._block_if_cardlock_wrong_entry("一键接管"):
            return
        try:
            from one_click_migration import open_one_click_migration

            open_one_click_migration(self)
            self._refresh()
        except Exception as e:
            show_warning(self, i18n.t("takeover_hub_err_title"), str(e))

    def _open_legacy_wizard(self) -> None:
        if self._block_if_cardlock_wrong_entry("其它旧软件 · 分步导入"):
            return
        try:
            from legacy_migration import open_legacy_migration_wizard

            open_legacy_migration_wizard(self)
            self._refresh()
        except Exception as e:
            show_warning(self, i18n.t("takeover_hub_err_title"), str(e))

    def _open_usb_lock(self) -> None:
        if self._block_if_cardlock_wrong_entry("USB 门锁迁移"):
            return
        try:
            from usb_lock_migrate_dialog import UsbLockMigrateDialog

            UsbLockMigrateDialog(self).exec()
            self._refresh()
        except Exception as e:
            show_warning(self, i18n.t("takeover_hub_err_title"), str(e))

    def _open_sniffer(self) -> None:
        if self._block_if_cardlock_wrong_entry("发卡串口嗅探"):
            return
        try:
            from card_sniffer import open_card_sniffer

            open_card_sniffer(self)
            self._refresh()
        except Exception as e:
            show_warning(self, i18n.t("takeover_hub_err_title"), str(e))

    def _open_data_import(self) -> None:
        try:
            from data_import_service import DataImportDialog

            DataImportDialog(self).exec()
            self._refresh()
        except Exception as e:
            show_warning(self, i18n.t("takeover_hub_err_title"), str(e))

    def _open_redist_installers(self) -> None:
        try:
            from usb_driver_helper import open_driver_install_dialog

            open_driver_install_dialog(self)
        except Exception as e:
            show_warning(self, i18n.t("takeover_hub_err_title"), str(e))

    def _open_takeover_report(self) -> None:
        try:
            from takeover_report import open_takeover_report_dialog

            open_takeover_report_dialog(self)
        except Exception as e:
            show_warning(self, i18n.t("takeover_hub_err_title"), str(e))

    def _open_brand_learn(self) -> None:
        show_info(
            self,
            i18n.t("takeover_hub_btn_brand_learn"),
            i18n.t(
                "takeover_hub_collector_hint",
                default=(
                    "新门锁品牌采集请使用独立工具 SolidCollector（U 盘即用）。\n\n"
                    "流程：读卡分析 → 导出 .solidhandover 握手包 → "
                    "厂家控制台 → 门锁品牌 → 导入握手包。\n\n"
                    "PMS 不再内置品牌学习对话框。"
                ),
            ),
        )


def open_legacy_takeover_hub(parent: Optional[QWidget] = None) -> None:
    from vendor_gate import require_vendor_or_block

    if not require_vendor_or_block(parent):
        return
    LegacyTakeoverHubDialog(parent).exec()
