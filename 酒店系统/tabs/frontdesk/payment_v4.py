"""payment_v4 — 2x4 全显支付方式 + PaymentMixin 复用。

[sub-j] 像素级布局升级：
  • 支付方式网格 2x4，按钮固定高度 48px，间距 4px
  • 集成快捷金额按钮行（5/10/20/50/100/全款/清零），等分 7 列
  • 实时找零显示（大字号红色），监听 host.txt_amount 变化
  • 近期交易流水（最近 5 笔 ledger），max-height 100px 可点击回看
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QGridLayout, QPushButton, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QFrame, QListWidget, QListWidgetItem, QSizePolicy,
)

from database import db
from ui.layout.frontdesk import FD_BTN_H_CRITICAL
from i18n import i18n
from sound_helper import play_notify
from design_tokens import _p

from ._shared import PAYMENT_METHODS
from .payment import PaymentMixin as _PaymentMixinBase


# 柬埔寨市场 2x4 顺序（QUICK_REFERENCE）
_V4_PAYMENT_ORDER = [
    "CASH_USD", "ABA", "CASH_KHR", "USDT",
    "WECHAT", "ALIPAY", "BANK_CARD", "CREDIT",
]

# [sub-j] 快捷金额按钮：$5/$10/$20/$50/$100/全款/清零 — 7 个等分
_QUICK_AMOUNTS = [5, 10, 20, 50, 100]


class PaymentMethodTiles(QWidget):
    """v4: 8 种支付方式 2x4 网格 + 快捷金额行 + 找零显示 + 近期交易流水。

    [sub-j] 像素级布局：
      - 支付方式按钮：48px 高，min-width 80px，间距 4px
      - 快捷金额按钮：32px 高，等分 7 列
      - 找零显示：右对齐，14px 红色字
      - 近期交易：max-height 100px，点击回看
    """

    # [sub-j] 像素级常量
    PAY_TILE_H = 48          # 支付方式按钮高度
    PAY_TILE_MIN_W = 80      # 支付方式按钮最小宽度
    PAY_GRID_SPACING = 4     # 网格间距
    QUICK_BTN_H = 32         # 快捷金额按钮高度
    QUICK_SPACING = 4        # 快捷金额间距
    CHANGE_FONT_PX = 14      # 找零字号
    TX_LIST_MAX_H = 100      # 交易流水最大高度

    def __init__(self, parent=None, *, compact: bool = False):
        super().__init__(parent)
        self.setObjectName("PaymentMethodTilesV4")
        self._buttons: dict[str, QPushButton] = {}
        self._current = _V4_PAYMENT_ORDER[0]
        # PAYMENT_METHODS 是 list[dict]，转为 {id: dict} 字典
        self._by_code = {m["id"]: m for m in PAYMENT_METHODS}

        # [sub-j] 根布局：间距 8 → 4，紧凑
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # ① 支付方式网格 2x4 — 48px 高，间距 4px
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(self.PAY_GRID_SPACING)

        for idx, code in enumerate(_V4_PAYMENT_ORDER):
            meta = self._by_code.get(code)
            if not meta:
                continue
            icon = meta.get("icon", "")
            label_key = meta.get("label", code)
            btn = QPushButton(f"{icon} {i18n.t(label_key)}")
            btn.setObjectName("PayMethodTile")
            btn.setCheckable(True)
            # [sub-j] 固定高度 48px，最小宽度 80px
            btn.setFixedHeight(self.PAY_TILE_H)
            btn.setMinimumWidth(self.PAY_TILE_MIN_W)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"{code} — {meta.get('label', code)}")
            btn.clicked.connect(lambda checked=False, c=code: self.setCurrentData(c))
            self._buttons[code] = btn
            grid.addWidget(btn, idx // 4, idx % 4)

        root.addLayout(grid)

        # ② [sub-j] 快捷金额按钮行：5/10/20/50/100/全款/清零，等分 7 列
        root.addWidget(self._build_quick_amount_row())

        # ③ [sub-j] 实时找零显示（大字号红色，右对齐）
        self._change_label = QLabel(self._format_change(0.0))
        self._change_label.setObjectName("FdChangeDisplay")
        self._change_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self._change_label)

        # ④ [sub-j] 近期交易流水（最近 5 笔 ledger，max-height 100px）
        root.addWidget(self._build_recent_transactions())

        self.setCurrentData(self._current)

        self._refresh_theme_styles()

        # [sub-j] 延迟连接 host 的 txt_amount 信号（host 在 CheckinTab 中稍后创建）
        QTimer.singleShot(0, self._connect_host_signals)

    # ── 主题刷新 ─────────────────────────────────────────────────────────
    def _refresh_theme_styles(self) -> None:
        """切换主题后重刷全部 inline style。"""
        # 找零显示
        if hasattr(self, "_change_label") and self._change_label is not None:
            self._change_label.setStyleSheet(
                f"QLabel#FdChangeDisplay {{"
                f" color: {_p('danger', '#C47E7A')};"
                f" font-size: {self.CHANGE_FONT_PX}px; font-weight: 700;"
                f" background: transparent; padding: 2px 0;"
                f"}}"
            )
        # 快捷金额按钮行 — 找 FdQuickAmountRow 下的按钮
        row = self.findChild(QWidget, "FdQuickAmountRow")
        if row is not None:
            accent = _p("accent", "#7BA7C9")
            text_c = _p("text", "#2A3038")
            border_c = _p("border", "#E2E8ED")
            bg_c = _p("bg", "#F4F6F8")
            for btn in row.findChildren(QPushButton):
                btn.setStyleSheet(
                    f"QPushButton#FdQuickAmt {{"
                    f" background: {bg_c}; color: {text_c};"
                    f" border: 1px solid {border_c}; border-radius: 4px;"
                    f" font-size: 12px; font-weight: 600; padding: 0 4px;"
                    f"}}"
                    f"QPushButton#FdQuickAmt:hover {{ border-color: {accent}; color: {accent}; }}"
                    f"QPushButton#FdQuickAmt:pressed {{ background: {accent}; color: white; }}"
                )
        # 近期交易流水
        if hasattr(self, "_tx_list") and self._tx_list is not None:
            self._tx_list.setStyleSheet(
                f"QListWidget#FdRecentTxList {{"
                f" background: {_p('surface_alt', '#F0F3F5')};"
                f" border: 1px solid {_p('border', '#E2E8ED')};"
                f" border-radius: 4px;"
                f" font-size: 11px; color: {_p('text_muted', '#6B7280')};"
                f"}}"
                f"QListWidget#FdRecentTxList::item {{ padding: 2px 6px; border-bottom: 1px solid {_p('border', '#E2E8ED')}; }}"
                f"QListWidget#FdRecentTxList::item:hover {{ background: {_p('primary_10pct', 'rgba(91,143,185,0.1)')}; }}"
            )
            # 近期交易标题
            wrap = self._tx_list.parentWidget()
            if wrap is not None:
                for child in wrap.findChildren(QLabel):
                    child.setStyleSheet(
                        f"color: {_p('text_muted', '#6B7280')}; font-size: 11px; font-weight: 600;"
                        f" letter-spacing: 1px; background: transparent;"
                    )

    # ── 快捷金额行 ─────────────────────────────────────────────────────────
    def _build_quick_amount_row(self) -> QWidget:
        """[sub-j] 快捷金额按钮行：5/10/20/50/100/全款/清零，7 列等分。"""
        wrap = QWidget()
        wrap.setObjectName("FdQuickAmountRow")
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self.QUICK_SPACING)

        # 数字金额按钮 $5/$10/$20/$50/$100
        for amt in _QUICK_AMOUNTS:
            btn = QPushButton(f"${amt}")
            btn.setObjectName("FdQuickAmt")
            btn.setFixedHeight(self.QUICK_BTN_H)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(i18n.t("pay_quick_amt_tip", default=f"快捷填入 ${amt}"))
            btn.clicked.connect(lambda checked=False, a=amt: self._host_fill_amount(float(a)))
            lay.addWidget(btn, 1)

        # 全款按钮（填入剩余待付）
        btn_full = QPushButton(i18n.t("pay_quick_full", default="全款"))
        btn_full.setObjectName("FdQuickAmt")
        btn_full.setFixedHeight(self.QUICK_BTN_H)
        btn_full.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_full.setToolTip(i18n.t("pay_quick_full_tip", default="填入剩余待付金额"))
        btn_full.clicked.connect(self._host_fill_remaining)
        lay.addWidget(btn_full, 1)

        # 清零按钮（清空金额输入）
        btn_clear = QPushButton(i18n.t("pay_quick_clear", default="清零"))
        btn_clear.setObjectName("FdQuickAmt")
        btn_clear.setFixedHeight(self.QUICK_BTN_H)
        btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear.setToolTip(i18n.t("pay_quick_clear_tip", default="清空金额输入框"))
        btn_clear.clicked.connect(self._host_clear_amount)
        lay.addWidget(btn_clear, 1)

        return wrap

    # ── 近期交易流水 ───────────────────────────────────────────────────────
    def _build_recent_transactions(self) -> QWidget:
        """[sub-j] 近期交易流水列表：最近 5 笔 ledger，max-height 100px。"""
        wrap = QFrame()
        wrap.setObjectName("FdRecentTxWrap")
        wrap.setMaximumHeight(self.TX_LIST_MAX_H)
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(2)

        title = QLabel(i18n.t("pay_recent_tx_title", default="近期交易"))
        lay.addWidget(title)

        self._tx_list = QListWidget()
        self._tx_list.setObjectName("FdRecentTxList")
        self._tx_list.setMaximumHeight(72)
        self._tx_list.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._tx_list)
        return wrap

    def refresh_recent_transactions(self):
        """[sub-j] 刷新近期交易列表 — 查询 ledger 表最近 5 笔。"""
        self._tx_list.clear()
        try:
            rows = db.execute(
                "SELECT ledger_type, amount, room_id, created_at FROM ledger "
                "ORDER BY id DESC LIMIT 5"
            ).fetchall()
        except Exception:
            rows = []
        for r in rows:
            ltype, amt, rid, ts = r
            ts_short = str(ts or "")[:16] if ts else ""
            text = f"[{ts_short}] {ltype} · {amt:.2f} · {rid or '-'}"
            item = QListWidgetItem(text)
            self._tx_list.addItem(item)

    # ── Host (CheckinTab) 信号连接 ─────────────────────────────────────────
    def _find_host(self):
        """[sub-j] 向上查找 CheckinTab/PaymentMixin 实例（具备 txt_amount + _fill_amount）。"""
        p = self.parent()
        while p is not None:
            if hasattr(p, "txt_amount") and hasattr(p, "_fill_amount"):
                return p
            p = p.parent()
        return None

    def _connect_host_signals(self):
        """[sub-j] 延迟连接 host.txt_amount.textChanged → 找零实时刷新。"""
        host = self._find_host()
        if not host or not hasattr(host, "txt_amount"):
            # 重试一次（CheckinTab 在 pay_tiles 之后才创建 txt_amount）
            QTimer.singleShot(200, self._connect_host_signals)
            return
        try:
            host.txt_amount.textChanged.connect(self._update_change_display)
        except Exception:
            pass
        # 首次刷新交易流水
        self.refresh_recent_transactions()

    # ── 快捷金额回调（通过 host 调用 PaymentMixin 方法）─────────────────────
    def _host_fill_amount(self, amount: float):
        host = self._find_host()
        if host and hasattr(host, "_fill_amount"):
            host._fill_amount(amount)

    def _host_fill_remaining(self):
        host = self._find_host()
        if host and hasattr(host, "_fill_remaining"):
            host._fill_remaining()

    def _host_clear_amount(self):
        host = self._find_host()
        if host and hasattr(host, "txt_amount"):
            host.txt_amount.clear()

    # ── 找零实时显示 ───────────────────────────────────────────────────────
    def _format_change(self, change: float) -> str:
        cur = i18n.t("currency_symbol", default="$")
        return i18n.t("pay_change_label", default="找零：{cur}{amt:.2f}").format(cur=cur, amt=change)

    def _update_change_display(self):
        """[sub-j] 实时计算并显示找零：max(0, 已输入 - 待付余额)。"""
        host = self._find_host()
        if not host:
            return
        try:
            paid_now = float(host.txt_amount.text() or "0")
        except Exception:
            paid_now = 0.0
        try:
            total = float(getattr(host, "_total_cache", 0.0) or 0.0)
        except Exception:
            total = 0.0
        try:
            already_paid = sum(a for a, _ in (host.paid_items or []))
        except Exception:
            already_paid = 0.0
        remaining = max(0.0, total - already_paid)
        change = max(0.0, paid_now - remaining)
        self._change_label.setText(self._format_change(change))

    # ── 原 v4 接口保留 ─────────────────────────────────────────────────────
    def currentData(self) -> str:
        return self._current

    def setCurrentData(self, code: str) -> None:
        self._current = code
        for k, btn in self._buttons.items():
            sel = k == code
            btn.setChecked(sel)
            btn.setProperty("selected", sel)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        try:
            play_notify("click")
        except Exception:
            pass
        # 切换支付方式时也刷新找零
        self._update_change_display()

    def apply_ui_scale(self, scale: float) -> None:
        from tools.cashier_canvas import px, CHECKIN_PAY_TILE_H
        tile_h = px(CHECKIN_PAY_TILE_H, scale)
        for btn in self._buttons.values():
            btn.setFixedHeight(max(tile_h, self.PAY_TILE_H))


class PaymentMixin(_PaymentMixinBase):
    """v4 收款 — 入账时写入 ledger 关联字段 + 库存事件。"""

    def _ledger_link_kwargs(self, pay_idx: int) -> dict:
        rid = self.current_room or ""
        return {
            "checkin_id": str(getattr(self, "_current_guest_id", "") or ""),
            "reference_no": f"CHK_{rid}_{pay_idx}" if rid else "",
            "order_id": getattr(self, "_current_order_id", None),
        }

    def _folio_shop_items(self) -> list[dict]:
        dep_tag = self._deposit_line_label()
        out: list[dict] = []
        for i in range(self.tbl_folio.rowCount()):
            cell = self.tbl_folio.item(i, 0)
            if cell is None:
                continue
            name = (cell.text() or "").strip()
            if not name or name == dep_tag:
                continue
            row = db.execute(
                "SELECT sku FROM shop_items WHERE name=? LIMIT 1", (name,),
            ).fetchone()
            if row and row[0]:
                out.append({"product_id": str(row[0]), "sku": str(row[0]), "quantity": 1})
        return out

    def _post_new_payments_ledger(self, start_idx: int, guest_name: str, extra_note: str, conn=None):
        rid = self.current_room
        if not rid or start_idx >= len(self.paid_items):
            return
        folio_items = []
        from money_utils import to_money
        for i in range(self.tbl_folio.rowCount()):
            item_name = self.tbl_folio.item(i, 0).text()
            item_price = float(to_money(self.tbl_folio.item(i, 1).text()))
            folio_items.append((item_name, item_price))
        dep_tag = self._deposit_line_label()
        deposit_total = sum(p for n, p in folio_items if n == dep_tag)
        non_deposit_total = sum(p for n, p in folio_items if n != dep_tag)
        total_folio = deposit_total + non_deposit_total
        tail = (" " + extra_note.strip()) if extra_note and extra_note.strip() else ""
        shop_items = self._folio_shop_items()
        for j in range(start_idx, len(self.paid_items)):
            amt, method = self.paid_items[j]
            if amt <= 0:
                continue
            link = self._ledger_link_kwargs(j)
            credit_note = ""
            if method == "CREDIT":
                credit_note = i18n.t("pay_credit_note")
            tx_id = None
            if total_folio > 0 and deposit_total > 0:
                dep_portion = round(amt * deposit_total / total_folio, 2)
                room_portion = round(amt - dep_portion, 2)
                if dep_portion > 0:
                    if conn is not None:
                        tx_id = db.append_ledger_conn(
                            conn, "DEPOSIT_IN", dep_portion, "CASH", 1, rid,
                            i18n.t("ledger_note_deposit_in").format(guest_name) + tail + credit_note,
                            pay_method=method, is_deposit=1, **link,
                        )
                    else:
                        tx_id = db.append_ledger(
                            "DEPOSIT_IN", dep_portion, "CASH", 1, rid,
                            i18n.t("ledger_note_deposit_in").format(guest_name) + tail + credit_note,
                            pay_method=method, is_deposit=1, **link,
                        )
                if room_portion > 0:
                    if conn is not None:
                        tx_id = db.append_ledger_conn(
                            conn, "ROOM_IN", room_portion, "CASH", 1, rid,
                            i18n.t("ledger_note_room_in").format(guest_name) + tail + credit_note,
                            pay_method=method, **link,
                        )
                    else:
                        tx_id = db.append_ledger(
                            "ROOM_IN", room_portion, "CASH", 1, rid,
                            i18n.t("ledger_note_room_in").format(guest_name) + tail + credit_note,
                            pay_method=method, **link,
                        )
            else:
                if conn is not None:
                    tx_id = db.append_ledger_conn(
                        conn, "ROOM_IN", amt, "CASH", 1, rid,
                        i18n.t("ledger_note_room_in").format(guest_name) + tail + credit_note,
                        pay_method=method, **link,
                    )
                else:
                    tx_id = db.append_ledger(
                        "ROOM_IN", amt, "CASH", 1, rid,
                        i18n.t("ledger_note_room_in").format(guest_name) + tail + credit_note,
                        pay_method=method, **link,
                    )
            if shop_items:
                try:
                    from services.payment_complete import complete_payment
                    complete_payment({
                        "room_id": rid,
                        "checkin_id": link.get("checkin_id"),
                        "reference_no": link.get("reference_no"),
                        "order_id": link.get("order_id"),
                        "tx_id": tx_id or "",
                        "items": shop_items,
                        "amount": amt,
                        "method": method,
                        "pay_method": method,
                        "operator_id": str(getattr(self, "_current_operator_id", lambda: "")()),
                    })
                except Exception:
                    pass
        # [sub-j] 入账后刷新近期交易流水
        try:
            tiles = getattr(self, "pay_tiles", None)
            if tiles and hasattr(tiles, "refresh_recent_transactions"):
                tiles.refresh_recent_transactions()
        except Exception:
            pass
