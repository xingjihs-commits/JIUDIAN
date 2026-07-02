"""账单详情对话框 + 历史账单查询入口（sub-d Task 1）

封装 BillDetailDialog：
- 退房成功后由 CheckoutMixin 弹出，传入 bill_no 直接展示该账单
- 财务页"历史账单查询"按钮也可弹出，不传 bill_no 时显示搜索面板
  （按房号 / 日期过滤 bill_headers），双击行加载对应账单详情

数据源：
- bill_headers（sub-a 新建）— 账单头：bill_no / issue_at / total_amount / currency / exchange_rate / operator_id / note
- folio_items（sub-a 加 bill_id 列）— 明细行：sku / qty / unit_price / total / note
- guests — 关联客人姓名（bill_headers.guest_id）

打印：优先 report_engine 若有 invoice 模板；当前 report_engine 无 invoice 入口，
回退用 QTextDocument + QPrintDialog 打印简单 HTML（A4 纸，含表头 + 明细 + 合计）。
"""
from __future__ import annotations

import datetime
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QDateEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame,
)
from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter

from database import db
from design_tokens import _p
from frontdesk_ui import FD_MARGIN, FD_SPACE_SM
from i18n import i18n
from ui_helpers import style_dialog, build_dialog_header, show_warning

logger = logging.getLogger(__name__)


def _money(v) -> str:
    """金额格式化：2 位小数。"""
    try:
        from money_utils import quantize_money
        return f"{float(quantize_money(v or 0)):.2f}"
    except Exception:
        try:
            return f"{float(v or 0):.2f}"
        except Exception:
            return "0.00"


class BillDetailDialog(QDialog):
    """账单详情 / 历史账单查询。

    用法：
        # 退房成功后直接展示某张账单
        dlg = BillDetailDialog(parent, bill_no="BILL...")
        dlg.exec()

        # 历史查询模式
        dlg = BillDetailDialog(parent)
        dlg.exec()
    """

    def __init__(self, parent=None, *, bill_no: str = ""):
        super().__init__(parent)
        self.setWindowTitle("账单详情" if bill_no else "历史账单查询")
        style_dialog(self, size="large")
        self._current_bill_no = (bill_no or "").strip()

        root = QVBoxLayout(self)
        root.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)
        root.setSpacing(FD_SPACE_SM)

        root.addWidget(build_dialog_header(
            "账单详情" if bill_no else "历史账单查询",
            "查看账单头与明细行，支持打印" if bill_no else "按房号或日期筛选，双击行查看明细",
        ))

        # ── 搜索面板（仅历史查询模式显示）──
        self._search_panel = QFrame()
        self._search_panel.setObjectName("FdCard")
        sp_lay = QVBoxLayout(self._search_panel)
        sp_lay.setContentsMargins(12, 8, 12, 8)
        sp_lay.setSpacing(FD_SPACE_SM)
        sf = QFormLayout()
        sf.setSpacing(FD_SPACE_SM)
        self.txt_search_room = QLineEdit()
        self.txt_search_room.setPlaceholderText("留空查全部，如 101")
        self.txt_search_room.setMaximumWidth(160)
        # 默认查近 30 天
        end_d = datetime.date.today()
        start_d = end_d - datetime.timedelta(days=30)
        self.dt_start = QDateEdit()
        self.dt_start.setCalendarPopup(True)
        self.dt_start.setDisplayFormat("yyyy-MM-dd")
        self.dt_start.setDate(start_d)
        self.dt_start.setMaximumWidth(140)
        self.dt_end = QDateEdit()
        self.dt_end.setCalendarPopup(True)
        self.dt_end.setDisplayFormat("yyyy-MM-dd")
        self.dt_end.setDate(end_d)
        self.dt_end.setMaximumWidth(140)
        sf.addRow("房号：", self.txt_search_room)
        sf.addRow("开始日期：", self.dt_start)
        sf.addRow("结束日期：", self.dt_end)
        sp_lay.addLayout(sf)
        btn_row = QHBoxLayout()
        self.btn_search = QPushButton("🔍 查询")
        self.btn_search.setObjectName("SolidPrimaryBtn")
        self.btn_search.clicked.connect(self._run_search)
        btn_row.addWidget(self.btn_search)
        btn_row.addStretch()
        sp_lay.addLayout(btn_row)
        # 历史账单列表
        self.tbl_history = QTableWidget(0, 6)
        self.tbl_history.setObjectName("BillHistoryTable")
        self.tbl_history.setHorizontalHeaderLabels(
            ["账单号", "开单时间", "房号", "客人", "金额", "状态"]
        )
        hh = self.tbl_history.horizontalHeader()
        hh.setMinimumSectionSize(70)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_history.verticalHeader().setVisible(False)
        self.tbl_history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_history.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl_history.itemDoubleClicked.connect(self._on_history_double_click)
        sp_lay.addWidget(self.tbl_history)
        root.addWidget(self._search_panel)

        # ── 详情面板 ──
        self._detail_panel = QFrame()
        self._detail_panel.setObjectName("FdCard")
        dp_lay = QVBoxLayout(self._detail_panel)
        dp_lay.setContentsMargins(12, 10, 12, 10)
        dp_lay.setSpacing(FD_SPACE_SM)

        # 账单头信息
        self.lbl_bill_no = QLabel("账单号：-")
        self.lbl_issue_at = QLabel("开单时间：-")
        self.lbl_guest = QLabel("客人 / 房间：-")
        self.lbl_total = QLabel("合计：-")
        self.lbl_total.setObjectName("H4Title")
        for lbl in (self.lbl_bill_no, self.lbl_issue_at, self.lbl_guest, self.lbl_total):
            dp_lay.addWidget(lbl)

        # 明细表
        self.tbl_items = QTableWidget(0, 4)
        self.tbl_items.setObjectName("BillItemsTable")
        self.tbl_items.setHorizontalHeaderLabels(["项目", "数量", "单价", "小计"])
        ih = self.tbl_items.horizontalHeader()
        ih.setMinimumSectionSize(60)
        ih.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            ih.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_items.verticalHeader().setVisible(False)
        self.tbl_items.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_items.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        dp_lay.addWidget(self.tbl_items)

        # 操作按钮
        btn_row2 = QHBoxLayout()
        self.btn_print = QPushButton("🖨 打印")
        self.btn_print.setObjectName("FdCardActionBtn")
        self.btn_print.clicked.connect(self._on_print)
        btn_row2.addWidget(self.btn_print)
        btn_row2.addStretch()
        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("FdGhostBtn")
        self.btn_close.clicked.connect(self.accept)
        btn_row2.addWidget(self.btn_close)
        dp_lay.addLayout(btn_row2)
        root.addWidget(self._detail_panel, 1)

        # 显示模式：bill_no 有值 → 隐藏搜索面板；否则隐藏详情面板直到选中行
        if self._current_bill_no:
            self._search_panel.setVisible(False)
            self._load_bill(self._current_bill_no)
        else:
            self._detail_panel.setVisible(False)
            self._run_search()

    # ── 数据加载 ──────────────────────────────────────────────

    def _load_bill(self, bill_no: str) -> bool:
        """加载指定 bill_no 的账单头 + 明细行；成功返回 True。"""
        bill_no = (bill_no or "").strip()
        if not bill_no:
            return False
        try:
            row = db.execute(
                "SELECT bill_no, issue_at, total_amount, currency, exchange_rate, status, "
                "operator_id, note, guest_id "
                "FROM bill_headers WHERE bill_no=?",
                (bill_no,),
            ).fetchone()
        except Exception as e:
            show_warning(self, "账单查询失败", str(e))
            return False
        if not row:
            show_warning(self, "账单不存在", f"未找到账单号 {bill_no}")
            return False
        (bno, issue_at, total_amt, currency, ex_rate, status, op, note, guest_id) = row
        # 取客人 + 房间（最近一次入住）
        guest_name, room_id = "", ""
        if guest_id:
            try:
                g = db.execute(
                    "SELECT name, room_id FROM guests WHERE id=?", (guest_id,)
                ).fetchone()
                if g:
                    guest_name, room_id = g[0] or "", g[1] or ""
            except Exception:
                pass
        cur_sym = i18n.t("currency_symbol")
        self.lbl_bill_no.setText(f"账单号：<b>{bno}</b>")
        self.lbl_issue_at.setText(f"开单时间：{str(issue_at or '')[:19]}")
        self.lbl_guest.setText(
            f"客人 / 房间：{guest_name or '-'} / {room_id or '-'}　"
            f"币种：{currency or '-'}　汇率：{_money(ex_rate)}　状态：{status or '-'}"
        )
        self.lbl_total.setText(f"合计：{cur_sym}{_money(total_amt)}")

        # 明细行
        self.tbl_items.setRowCount(0)
        try:
            items = db.execute(
                "SELECT sku, qty, unit_price, total, note FROM folio_items "
                "WHERE bill_id=? ORDER BY id",
                (bill_no,),
            ).fetchall()
        except Exception as e:
            show_warning(self, "明细加载失败", str(e))
            items = []
        for i, (sku, qty, price, total, note) in enumerate(items):
            self.tbl_items.insertRow(i)
            name = str(note or sku or "")
            self.tbl_items.setItem(i, 0, QTableWidgetItem(name))
            q_item = QTableWidgetItem(str(qty or 1))
            q_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tbl_items.setItem(i, 1, q_item)
            p_item = QTableWidgetItem(f"{cur_sym}{_money(price)}")
            p_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tbl_items.setItem(i, 2, p_item)
            t_item = QTableWidgetItem(f"{cur_sym}{_money(total)}")
            t_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tbl_items.setItem(i, 3, t_item)
        self._current_bill_no = bno
        self._detail_panel.setVisible(True)
        return True

    def _run_search(self) -> None:
        """按房号 + 日期范围查 bill_headers。"""
        room = self.txt_search_room.text().strip()
        start = self.dt_start.date().toString("yyyy-MM-dd")
        end = self.dt_end.date().toString("yyyy-MM-dd")
        sql = (
            "SELECT bh.bill_no, bh.issue_at, "
            "COALESCE(g.room_id,'') AS room_id, "
            "COALESCE(g.name,'') AS guest_name, "
            "bh.total_amount, bh.status "
            "FROM bill_headers bh "
            "LEFT JOIN guests g ON g.id = bh.guest_id "
            "WHERE date(bh.issue_at) >= ? AND date(bh.issue_at) <= ? "
        )
        params: list = [start, end]
        if room:
            sql += " AND COALESCE(g.room_id,'') = ?"
            params.append(room)
        sql += " ORDER BY bh.issue_at DESC LIMIT 500"
        try:
            rows = db.execute(sql, tuple(params)).fetchall()
        except Exception as e:
            show_warning(self, "查询失败", str(e))
            return
        self.tbl_history.setRowCount(0)
        cur_sym = i18n.t("currency_symbol")
        for i, (bno, issue_at, rid, gname, total, status) in enumerate(rows):
            self.tbl_history.insertRow(i)
            self.tbl_history.setItem(i, 0, QTableWidgetItem(str(bno or "")))
            self.tbl_history.setItem(i, 1, QTableWidgetItem(str(issue_at or "")[:19]))
            self.tbl_history.setItem(i, 2, QTableWidgetItem(str(rid or "-")))
            self.tbl_history.setItem(i, 3, QTableWidgetItem(str(gname or "-")))
            amt_item = QTableWidgetItem(f"{cur_sym}{_money(total)}")
            amt_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tbl_history.setItem(i, 4, amt_item)
            self.tbl_history.setItem(i, 5, QTableWidgetItem(str(status or "-")))

    def _on_history_double_click(self, item) -> None:
        """双击历史行：加载该账单详情，并切换到详情面板。"""
        if item is None:
            return
        row = item.row()
        bno_item = self.tbl_history.item(row, 0)
        if not bno_item:
            return
        bno = bno_item.text().strip()
        if not bno:
            return
        if self._load_bill(bno):
            # 隐藏搜索面板，详情面板已 show
            self._search_panel.setVisible(False)

    # ── 打印 ─────────────────────────────────────────────────

    def _build_print_html(self) -> str:
        """生成简单 HTML 用于打印。"""
        cur_sym = i18n.t("currency_symbol")
        bill_no_txt = self.lbl_bill_no.text().replace("账单号：", "").replace("<b>", "").replace("</b>", "")
        issue_txt = self.lbl_issue_at.text().replace("开单时间：", "")
        guest_txt = self.lbl_guest.text().replace("客人 / 房间：", "")
        total_txt = self.lbl_total.text().replace("合计：", "")
        from design_tokens import _p
        rows_html = []
        for r in range(self.tbl_items.rowCount()):
            cells = []
            for c in range(self.tbl_items.columnCount()):
                it = self.tbl_items.item(r, c)
                cells.append(it.text() if it else "")
            rows_html.append(
                f"<tr><td>{cells[0]}</td><td style='text-align:right'>{cells[1]}</td>"
                f"<td style='text-align:right'>{cells[2]}</td>"
                f"<td style='text-align:right'>{cells[3]}</td></tr>"
            )
        rows_html_str = "\n".join(rows_html) or (
            f"<tr><td colspan='4' style='text-align:center;color:{_p('text_dim','#999')}'>（无明细）</td></tr>"
        )
        return f"""
        <html><head><meta charset='utf-8'><style>
          body {{ font-family: 'Microsoft YaHei', sans-serif; padding: 24px; }}
          h2 {{ margin: 0 0 8px 0; color: {_p('text','#1E3A5F')}; }}
          .meta {{ color: {_p('text_muted','#555')}; font-size: 13px; margin: 2px 0; }}
          table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }}
          th, td {{ border: 1px solid {_p('border','#ccc')}; padding: 6px 8px; }}
          th {{ background: {_p('bg','#f2f0ee')}; text-align: left; }}
          .total {{ margin-top: 14px; font-size: 16px; font-weight: bold; color: {_p('text','#1E3A5F')}; }}
        </style></head><body>
          <h2>账单 {bill_no_txt}</h2>
          <div class='meta'>{issue_txt}</div>
          <div class='meta'>{guest_txt}</div>
          <table>
            <thead><tr><th>项目</th><th style='text-align:right'>数量</th>
              <th style='text-align:right'>单价</th><th style='text-align:right'>小计</th></tr></thead>
            <tbody>{rows_html_str}</tbody>
          </table>
          <div class='total'>{total_txt}</div>
          <div class='meta' style='margin-top: 24px; color:{_p('text_dim','#999')};'>
            打印时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
          </div>
        </body></html>
        """

    def _on_print(self) -> None:
        """打印当前账单：优先 report_engine invoice 模板（暂无），回退 QTextDocument + QPrintDialog。"""
        if not self._current_bill_no:
            show_warning(self, "打印", "请先选择一张账单。")
            return
        # 优先调用 report_engine 的 invoice 模板（若未来新增）
        try:
            from report_engine import ReportExporter  # noqa: F401  # 暂无 invoice 入口
        except Exception:
            pass
        # 回退：QTextDocument + QPrintDialog
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageOrientation(QPrinter.Orientation.Portrait)
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("打印账单")
        if dlg.exec() != QPrintDialog.DialogCode.Accepted:
            return
        try:
            doc = QTextDocument()
            doc.setHtml(self._build_print_html())
            doc.print_(printer)
        except Exception as e:
            logger.exception("[bill_detail] 打印失败")
            show_warning(self, "打印失败", str(e))
