"""
inventory_diff_page.py — C0-gamma 账实差异审计界面

功能：
- 顶部横幅：下次盘点截止 / 是否逾期 / 上次盘点时间
- 左半：差异行表格（账面 / 实物 / 差 / 差异率 / 是否锁定 / 解释）
- 右半：选中行的"经手时间线"（最近 50 条流水：谁、何时、什么动作、关联房间/订单）
- 底部：解释 / 解锁 / 全部一键调拨修正
- 顶部按钮：开始本期盘点（弹 _PeriodicStocktakeDialog） / 关闭并对账
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

import inventory_audit_engine as audit
import stocktake_scheduler as sched
from database import db
from inventory_baseline import CATEGORY_CONSUMABLE, CATEGORY_SHOP
from design_tokens import _p
from ui_helpers import show_warning, show_info, show_error, ask_confirm
import logging
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  顶部横幅
# ─────────────────────────────────────────────────────────────────────────────
class _StatusBanner(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InventoryDiffBanner")
        self.setWordWrap(True)
        self.setMinimumHeight(56)
        self.refresh()

    def refresh(self) -> None:
        s = sched.status_summary()
        next_due = s.get("next_due_at") or "—"
        last_done = s.get("last_stocktake_at") or s.get("baseline_done_at") or "—"
        d = s.get("days_until_due")
        if s.get("overdue"):
            tone = "overdue"
            txt = (
                f"周期盘点【已逾期】  距离截止：{abs(int(d or 0))} 天前  "
                f"截止时间：{next_due}  ·  请尽快点【开始本期盘点】"
            )
        elif s.get("in_reminder_window"):
            tone = "warn"
            txt = (
                f"即将到期：剩约 {int(d or 0)} 天  截止：{next_due}  "
                "建议安排员工尽快盘点"
            )
        else:
            tone = "ok"
            txt = (
                f"上次盘点：{last_done}   下次截止：{next_due}   剩约 {d if d is not None else '?'} 天"
            )
        self.setText(txt)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


# ─────────────────────────────────────────────────────────────────────────────
#  周期盘点弹窗：录入实物盘点数量
# ─────────────────────────────────────────────────────────────────────────────
class _PeriodicStocktakeDialog(QDialog):
    """让员工把每个 SKU 的实物数量录进来，提交后写入会话 + 算差异。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("本期盘点 · 实物数量录入")
        from ui_helpers import style_dialog
        style_dialog(self, size="xlarge")
        self._session_id = audit.has_open_periodic_session() or audit.start_periodic_session(
            operator_id=self._operator_name(), note="UI 触发"
        )

        items = self._monitored_items()
        self._items = items
        self._spins: dict[str, QSpinBox] = {}

        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(16, 16, 16, 16)
        wrap.setSpacing(10)

        head = QLabel(
            f"本次会话：<b>{self._session_id[:12]}…</b>  "
            f"共 {len(items)} 个纳入监控的 SKU。<br>"
            "按真实点数填实物，差异率 ≥ 5% 会自动锁定 SKU。空着不填 = 跳过本期。"
        )
        head.setStyleSheet(f"color:{_p('text_muted')};font-size:13px;")
        wrap.addWidget(head)

        table = QTableWidget(len(items), 5)
        table.setHorizontalHeaderLabels(["类别", "名称", "单位", "账面", "实物盘点"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setAlternatingRowColors(False)
        for r, it in enumerate(items):
            cat = "🛍️ 超市" if it["category"] == CATEGORY_SHOP else "🛏️ 客房消耗"
            self._set_locked(table, r, 0, cat)
            self._set_locked(table, r, 1, it["name"])
            self._set_locked(table, r, 2, it["unit"])
            self._set_locked(table, r, 3, str(audit.book_qty_of(it["item_id"])))
            spn = QSpinBox()
            spn.setRange(0, 999999)
            spn.setSpecialValueText("（跳过）")
            spn.setValue(0)
            table.setCellWidget(r, 4, spn)
            self._spins[it["item_id"]] = spn
        wrap.addWidget(table, 1)

        bar = QHBoxLayout()
        bar.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(self.reject)
        btn_submit = QPushButton("提交并算差异")
        btn_submit.setObjectName("SolidPrimaryBtn")
        btn_submit.clicked.connect(self._submit)
        bar.addWidget(btn_cancel); bar.addWidget(btn_submit)
        wrap.addLayout(bar)

    @staticmethod
    def _set_locked(table: QTableWidget, r: int, c: int, text: str) -> None:
        item = QTableWidgetItem(str(text))
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        table.setItem(r, c, item)

    @staticmethod
    def _monitored_items() -> list[dict]:
        rows = db.execute(
            """SELECT item_id, category, name, unit FROM inventory_items
               WHERE in_monitoring=1 ORDER BY category, name"""
        ).fetchall()
        return [{"item_id": r[0], "category": r[1], "name": r[2], "unit": r[3] or "件"}
                for r in rows]

    def _operator_name(self) -> str:
        try:
            from permission_system import PermissionManager
            u = PermissionManager.current_user()
            return (u or {}).get("username") or PermissionManager.current_role() or "guest"
        except Exception:
            return "guest"

    def _submit(self) -> None:
        counted: dict[str, int] = {}
        for it in self._items:
            spn = self._spins.get(it["item_id"])
            if spn is None:
                continue
            v = int(spn.value())
            if v == 0 and spn.specialValueText():
                # 用户选择跳过该 SKU 时，QSpinBox 显示「(跳过)」，不写入
                continue
            counted[it["item_id"]] = v
        if not counted:
            show_warning(self, "未填", "你没有录入任何 SKU 的实物数量。")
            return
        result = audit.commit_counted_quantities(
            self._session_id, counted, operator_id=self._operator_name()
        )
        show_info(
            self, "盘点录入完成",
            f"已录入 {result['lines_total']} 个 SKU，"
            f"其中 {result['lines_critical']} 个差异 ≥ 5%，已锁定。\n\n"
            "下一步：在差异列表里逐条写解释，然后【关闭并对账】。",
        )
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
#  差异行表格（主页主体）
# ─────────────────────────────────────────────────────────────────────────────
class _DiffTable(QTableWidget):
    COL = {
        "session": 0, "category": 1, "name": 2,
        "book": 3, "counted": 4, "diff": 5, "rate": 6,
        "locked": 7, "explanation": 8,
    }

    def __init__(self, parent=None):
        super().__init__(0, 9, parent)
        self.setHorizontalHeaderLabels([
            "会话", "类别", "名称", "账面", "实物",
            "差", "差异率", "锁定", "解释",
        ])
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(self.COL["explanation"], QHeaderView.Stretch)
        self.setAlternatingRowColors(False)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)

    def load(self, lines: list[dict]) -> None:
        self.setRowCount(len(lines))
        for r, line in enumerate(lines):
            sess = (line.get("session_id") or "")[:8]
            cat = "🛍️" if line.get("category") == CATEGORY_SHOP else "🛏️"
            sign = "+" if line["diff_qty"] >= 0 else ""
            row_cells = {
                "session": sess,
                "category": cat,
                "name": line["name"],
                "book": str(line["book_qty"]),
                "counted": str(line["counted_qty"]),
                "diff": f"{sign}{line['diff_qty']}",
                "rate": f"{line['diff_rate'] * 100:.1f}%",
                "locked": "🔒" if line.get("locked_at") and not line.get("resolved_at") else "",
                "explanation": line.get("explanation") or "",
            }
            for key, val in row_cells.items():
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, line["line_id"])
                if line.get("is_critical") and not line.get("resolved_at"):
                    item.setForeground(QColor(_p("danger")))
                    item.setBackground(QColor(_p("bg")))
                self.setItem(r, self.COL[key], item)
            # 把整行 line_id 存到 row userrole（行头）
            self.item(r, 0).setData(Qt.UserRole + 1, line["line_id"])

    def selected_line_id(self) -> Optional[int]:
        r = self.currentRow()
        if r < 0:
            return None
        item = self.item(r, 0)
        return int(item.data(Qt.UserRole + 1)) if item else None

    def selected_item_id(self) -> Optional[str]:
        r = self.currentRow()
        if r < 0:
            return None
        # 我们没在表里直接放 item_id；从 lines cache 取需要外部传入。
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  右侧：经手时间线
# ─────────────────────────────────────────────────────────────────────────────
class _Timeline(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InventoryTimeline")
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self._placeholder()

    def _placeholder(self) -> None:
        self.setPlainText("← 在左边表格选一行差异，这里会显示该 SKU 最近 50 条经手流水")

    def load_for_item(self, item_id: str, name: str) -> None:
        rows = audit.item_timeline(item_id, limit=50)
        if not rows:
            self.setPlainText(f"{name} (item={item_id}) 暂无流水（可能是第一次盘点）")
            return
        lines = [f"经手时间线：{name}  (item={item_id})", "─" * 60]
        for r in rows:
            sign = "+" if r["qty_change"] >= 0 else ""
            lines.append(
                f"{r['created_at']:<20} {r['move_type']:<22}"
                f" {sign}{r['qty_change']:>4}  操作={r['operator_id'] or '?'}"
                f"  房={r['related_room'] or '-'}  单={r['related_order'] or '-'}"
                f"  备注={r['note'] or ''}"
            )
        self.setPlainText("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
#  主页面
# ─────────────────────────────────────────────────────────────────────────────
class InventoryDiffPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InventoryDiffPage")
        self._lines_cache: list[dict] = []

        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(12, 12, 12, 12)
        wrap.setSpacing(10)

        self._banner = _StatusBanner(self)
        wrap.addWidget(self._banner)

        bar = QHBoxLayout()
        self._filter_combo = QComboBox()
        self._filter_combo.addItem("仅未解释（默认）", "unresolved")
        self._filter_combo.addItem("当前会话全部", "current_session")
        self._filter_combo.addItem("所有历史差异", "all")
        self._filter_combo.currentIndexChanged.connect(self.refresh)
        bar.addWidget(QLabel("筛选："))
        bar.addWidget(self._filter_combo)
        bar.addStretch()

        btn_start = QPushButton("开始本期盘点")
        btn_start.setObjectName("SolidPrimaryBtn")
        btn_start.clicked.connect(self._open_periodic_dialog)
        btn_finalize = QPushButton("✅ 关闭并对账")
        btn_finalize.setObjectName("SolidPrimaryBtn")
        btn_finalize.clicked.connect(self._finalize_session)
        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("FdGhostBtn")
        btn_refresh.clicked.connect(self.refresh)
        for b in (btn_start, btn_finalize, btn_refresh):
            bar.addWidget(b)
        wrap.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        left_box = QFrame()
        left_box.setObjectName("ContentBox")
        from ui_surface import fd_apply_content_box, fd_apply_table_palette
        fd_apply_content_box(left_box)
        left_lay = QVBoxLayout(left_box)
        left_lay.setContentsMargins(10, 10, 10, 10)
        left_lay.setSpacing(0)
        self._table = _DiffTable()
        fd_apply_table_palette(self._table)
        self._table.itemSelectionChanged.connect(self._on_row_changed)
        left_lay.addWidget(self._table)
        split.addWidget(left_box)

        right = QFrame()
        right.setObjectName("ContentBox")
        fd_apply_content_box(right)
        rl = QVBoxLayout(right); rl.setContentsMargins(10, 10, 10, 10); rl.setSpacing(8)
        self._timeline = _Timeline()
        rl.addWidget(self._timeline, 1)

        exp_row = QHBoxLayout()
        self._exp_input = QPlainTextEdit()
        self._exp_input.setPlaceholderText(
            "选中一行后写解释，例如：员工调拨到 305 房、客人投诉补偿、报损盘亏…"
        )
        self._exp_input.setMinimumHeight(50)
        self._exp_input.setMaximumHeight(100)
        rl.addWidget(self._exp_input)

        btn_save_exp = QPushButton("保存解释 + 标记已处理")
        btn_save_exp.setObjectName("SolidPrimaryBtn")
        btn_save_exp.clicked.connect(self._save_explanation)
        btn_unlock = QPushButton("🔓 解锁该 SKU（不写解释）")
        btn_unlock.setObjectName("FdGhostBtn")
        btn_unlock.clicked.connect(self._unlock_selected)
        exp_row.addWidget(btn_save_exp); exp_row.addWidget(btn_unlock); exp_row.addStretch()
        rl.addLayout(exp_row)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        wrap.addWidget(split, 1)
        self.refresh()

        from ui_surface import fd_connect_theme_refresh
        fd_connect_theme_refresh(self)

    # ── 数据加载 ─────────────────────────────────────────────────────────
    def refresh(self) -> None:
        self._banner.refresh()
        mode = self._filter_combo.currentData()
        if mode == "unresolved":
            self._lines_cache = audit.list_critical_lines(only_unresolved=True)
        elif mode == "current_session":
            sid = audit.has_open_periodic_session()
            self._lines_cache = audit.session_lines(sid) if sid else []
        else:
            self._lines_cache = audit.list_critical_lines(only_unresolved=False)
        self._table.load(self._lines_cache)

    def _on_row_changed(self) -> None:
        r = self._table.currentRow()
        if r < 0 or r >= len(self._lines_cache):
            self._timeline._placeholder()
            return
        line = self._lines_cache[r]
        self._timeline.load_for_item(line["item_id"], line["name"])
        self._exp_input.setPlainText(line.get("explanation") or "")

    # ── 操作 ─────────────────────────────────────────────────────────────
    def _open_periodic_dialog(self) -> None:
        dlg = _PeriodicStocktakeDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _finalize_session(self) -> None:
        sid = audit.has_open_periodic_session()
        if not sid:
            show_info(
                self, "没有进行中的盘点",
                "目前没有进行中的周期盘点会话。点【开始本期盘点】先建一个。"
            )
            return
        # 提示老板未处理的 critical 数量
        unresolved = sum(
            1 for ln in audit.session_lines(sid)
            if ln["is_critical"] and not ln["resolved_at"] and not ln["explanation"]
        )
        if unresolved > 0:
            if not ask_confirm(
                self, "还有未解释的差异",
                f"本会话还有 {unresolved} 个差异≥5% 的 SKU 没写解释。\n\n"
                "现在关闭会话：未解释的 SKU 会保持锁定，已解释 / 0 差异的 SKU 会被自动对账。\n\n"
                "确定关闭吗？",
            ):
                return
        result = audit.finalize_session(sid, operator_id=self._operator_name())

        # 推送老板通知（关闭即推一份完整报告）
        try:
            msg = audit.format_telegram_alert(sid)
            from telegram_shadow import telegram_thread
            if telegram_thread.isRunning():
                telegram_thread.send_alert_sync(msg)
        except Exception as exc:
            logger.warning("[inventory_diff_page] 推送报警失败: %s", exc)

        show_info(
            self, "盘点关闭",
            f"会话已关闭。\n\n• 状态：{result['status']}\n"
            f"• 已对账的 SKU：{result['reconciled_lines']}\n"
            f"• 仍锁定（未解释）的 SKU：{result['unresolved_critical']}\n\n"
            "已自动推送报警给老板。"
        )
        self.refresh()

    def _save_explanation(self) -> None:
        r = self._table.currentRow()
        if r < 0 or r >= len(self._lines_cache):
            return
        line = self._lines_cache[r]
        txt = self._exp_input.toPlainText().strip()
        if not txt:
            show_warning(self, "请写解释", "解释不能为空。")
            return
        audit.explain_line(line["line_id"], explanation=txt,
                           operator_id=self._operator_name(), mark_resolved=True)
        self.refresh()

    def _unlock_selected(self) -> None:
        r = self._table.currentRow()
        if r < 0 or r >= len(self._lines_cache):
            return
        line = self._lines_cache[r]
        if not ask_confirm(
            self, "确认解锁",
            f"确定要解锁【{line['name']}】吗？\n\n"
            "解锁后该 SKU 可正常发货 / 销售，但本次差异行仍会保留在历史里。",
        ):
            return
        audit.unlock_line(line["line_id"])
        self.refresh()

    def _operator_name(self) -> str:
        try:
            from permission_system import PermissionManager
            u = PermissionManager.current_user()
            return (u or {}).get("username") or "BOSS"
        except Exception:
            return "BOSS"


def open_inventory_diff_page(parent=None) -> None:
    """供菜单 / 命令面板调用：在独立窗口打开账实差异页。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle("账实差异审计")
    from ui_helpers import style_dialog
    style_dialog(dlg, size="xlarge")
    lay = QVBoxLayout(dlg); lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(InventoryDiffPage(dlg))
    dlg.exec()
