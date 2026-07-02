# -*- coding: utf-8 -*-
# [UI-REVAMP v8] 收银台容器骨架重写 — 真正的新壳，不是 QSS 染色
"""收银台 v8 容器架构

旧结构（v7 及之前）：
  body_frame → banner → v_split → top_panel → content_split(58:42)
    → bill_rail(账单) → right_main(收款+卡操作+确认)
  → bottom_dock(流水+交班)

新结构（v8）：
  root
  ├── ① 顶栏带（房号 + 客人 + 住几晚 + 房费预估）     ← 横幅拆分
  ├── ② 快捷操作带（客房服务/加项/打印/更多/快速入住）  ← 独立行
  ├── ③ 主分屏（QSplitter，用户可拖动）
  │   ├── 左区 60%：账单卡片
  │   │   ├── 账单明细表
  │   │   ├── 费率+押金行
  │   │   └── 合计条
  │   └── 右区 40%：操作面板
  │       ├── ① 收款区（支付磁贴 + 金额 + 收款/退款）
  │       ├── ② 卡操作区（模式色编码 READY/INHOUSE）
  │       └── ③ 确认入住（锚点，位置稳定）
  └── ④ 底栏（流水 + 交班，Tab 切换）

关键改动：
- 横幅拆成「信息带」+「操作带」两行 → 信息清晰
- 快捷操作独立成行 → 不挤
- 主分屏改为 QSplitter → 用户可拖动调整
- 右区从上到下：收款→卡操作→确认 → 流程自然
- 确认按钮前固定间距 → 位置稳定
- 底栏改为 Tab 切换 → 省空间
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QTableWidget, QHeaderView,
    QFrame, QAbstractItemView, QAbstractSpinBox,
    QMenu, QSizePolicy, QDoubleSpinBox, QSpinBox,
    QApplication, QSplitter, QStackedWidget, QComboBox,
)
from PySide6.QtCore import Qt, QTimer

from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import ask_confirm, show_info, show_warning
from sound_helper import play_success, play_fail, play_warn, play_notify
from frontdesk_ledger_strip import FrontdeskLedgerStrip
from frontdesk_flow_strip import FrontdeskFlowStrip
from tabs.frontdesk.shift_dock import ShiftDockWidget
from design_tokens import _p
from lock_legacy_bridge import CARD_STATUS_ERASED, LEGACY_ACTIVE_CARD_STATUSES
from tabs._shared import current_operator_id
from permission_system import PermissionManager

from frontdesk_ui import (
    FD_MARGIN, FD_SPACE, FD_SPACE_SM, FD_SPACE_MD, FD_SPACE_LG,
    fd_apply_action_btn, fd_apply_compact_input, fd_apply_amount_input,
    fd_apply_commit_btn, fd_apply_quick_btn, fd_apply_mode_group,
    fd_card, fd_card_layout, fd_section_bar,
    FD_CARD_PADDING, FD_CHECKIN_COMMIT_H,
    FD_CHECKIN_BOTTOM_DOCK_MIN,
)
from ui_surface import (
    fd_apply_bill_rail, fd_apply_card_panel, fd_apply_checkin_panel,
    fd_apply_checkin_top_panel, fd_apply_checkin_right_rail,
    fd_apply_checkin_bottom_dock, fd_apply_checkin_vsplit,
    fd_apply_totals_strip, fd_apply_bill_folio_shell,
    fd_apply_h_divider, fd_apply_dock_divider,
)
from ._shared import _status_placeholders, _legacy_card_status_display, _make_collapsible_section
from .guest_info import GuestInfoMixin
from .payment import PaymentMixin
from .payment_v4 import PaymentMethodTiles  # G05: 2×4网格布局
from .checkout import CheckoutMixin
from .refund import RefundMixin
from .team import TeamMixin

logger = logging.getLogger(__name__)


class CheckinTab(GuestInfoMixin, PaymentMixin, CheckoutMixin, RefundMixin, TeamMixin, QWidget):
    """入住/退房核心界面 — v8 新壳容器"""

    def __init__(self):
        super().__init__()
        self.current_room = None
        self.paid_items = []
        self._total_cache = 0.0
        self._posted_ledger_pay_idx = 0
        self._current_rt = None
        self._stay_nights = 1
        self._expected_room_line_total = 0.0
        self._forced_unit_price = None
        self._card_issued_session = False
        self._canvas_scale = 1.0
        self._resize_timer = None

        self.setObjectName("FdCheckinRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ═══════════════════════════════════════════════════════════════
        #  ① 顶栏信息带 — 房号 + 客人 + 住几晚 + 房费预估
        # ═══════════════════════════════════════════════════════════════
        root.addWidget(self._build_info_bar())

        # 分隔线
        sep = QFrame()
        fd_apply_h_divider(sep)
        root.addWidget(sep)

        # ═══════════════════════════════════════════════════════════════
        #  ② 快捷操作带 — 独立行
        # ═══════════════════════════════════════════════════════════════
        root.addWidget(self._build_quick_bar())

        # 分隔线
        sep2 = QFrame()
        fd_apply_h_divider(sep2)
        root.addWidget(sep2)

        # ═══════════════════════════════════════════════════════════════
        #  ③ 主分屏 — QSplitter（用户可拖动）
        # ═══════════════════════════════════════════════════════════════
        self._main_split = QSplitter(Qt.Orientation.Vertical)
        self._main_split.setObjectName("FdCheckinVSplit")
        self._main_split.setChildrenCollapsible(False)
        self._main_split.setHandleWidth(4)
        fd_apply_checkin_vsplit(self._main_split)

        # 上区：左账单 + 右操作
        top_widget = QWidget()
        top_widget.setObjectName("FdCheckinTopPanel")
        fd_apply_checkin_top_panel(top_widget)
        top_lay = QVBoxLayout(top_widget)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(0)
        top_lay.addLayout(self._build_content_split(), 1)
        self._main_split.addWidget(top_widget)

        # 下区：Tab 切换（流水 / 交班）
        bottom_widget = self._build_bottom_dock()
        self._main_split.addWidget(bottom_widget)

        # [F09] 比例从 3:2（底占40%）改为 3:1（底占25%）
        # 解决小屏（1366px）下底栏账单流水遮挡收银台主操作按钮的问题
        self._main_split.setStretchFactor(0, 3)
        self._main_split.setStretchFactor(1, 1)
        root.addWidget(self._main_split, 1)

        self._set_room_active(False)
        self._apply_room_mode(is_inhouse=False, has_room=False)

        self._init_amount_shortcuts()
        bus.theme_changed.connect(self._on_theme_changed)
        self._refresh_theme_styles()

    # ═══════════════════════════════════════════════════════════════
    #  ① 顶栏信息带
    # ═══════════════════════════════════════════════════════════════
    def _build_info_bar(self) -> QFrame:
        """信息带 — 房号 + 客人身份 + 住几晚 + 房费预估。"""
        bar = QFrame()
        bar.setObjectName("FdPanelBanner")
        from ui_surface import fd_apply_panel_banner
        fd_apply_panel_banner(bar)
        self._banner = bar
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(FD_MARGIN, 10, FD_MARGIN, 10)
        lay.setSpacing(6)

        # 第 1 行：房号 + 住几晚 + 房费预估
        row1 = QHBoxLayout()
        row1.setSpacing(12)

        self.lbl_room_banner = QLabel(i18n.t("select_room_hint", default="请选择房间"))
        self.lbl_room_banner.setObjectName("FdRoomBanner")
        row1.addWidget(self.lbl_room_banner)

        row1.addStretch()

        # 住几晚
        lbl_nights = QLabel(i18n.t("label_stay", default="住"))
        lbl_nights.setStyleSheet(f"color: {_p('text_muted')}; font-size: 12px;")
        row1.addWidget(lbl_nights)
        self.spn_nights = QSpinBox()
        self.spn_nights.setObjectName("FdStayNights")
        self.spn_nights.setRange(1, 30)
        self.spn_nights.setValue(1)
        self.spn_nights.setSuffix(i18n.t("label_nights_unit", default="晚"))
        self.spn_nights.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_nights.valueChanged.connect(self._on_nights_changed)
        row1.addWidget(self.spn_nights)

        self.lbl_room_charge_est = QLabel("")
        self.lbl_room_charge_est.setObjectName("FdRoomChargeEst")
        row1.addWidget(self.lbl_room_charge_est)

        lay.addLayout(row1)

        # SOP 流程条
        self._flow_strip = FrontdeskFlowStrip()
        self._flow_strip.hide()
        lay.addWidget(self._flow_strip)

        # 第 2 行：客人身份
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText(i18n.t("table_guest", default="客人姓名"))
        fd_apply_compact_input(self.txt_name, width_key="guest_name")
        row2.addWidget(self.txt_name)

        self.txt_member = QLineEdit()
        self.txt_member.setPlaceholderText(i18n.t("label_phone", default="手机号"))
        fd_apply_compact_input(self.txt_member, width_key="phone")
        self.txt_member.textChanged.connect(self._check_member)
        row2.addWidget(self.txt_member)

        self.txt_id = QLineEdit()
        self.txt_id.setPlaceholderText(i18n.t("label_id_passport", default="身份证/护照"))
        fd_apply_compact_input(self.txt_id)
        self.txt_id.setMaximumWidth(200)
        row2.addWidget(self.txt_id)

        self.lbl_member_info = QLabel("")
        self.lbl_member_info.setObjectName("FdMutedLabel")
        row2.addWidget(self.lbl_member_info)
        row2.addStretch()
        lay.addLayout(row2)

        return bar

    # ═══════════════════════════════════════════════════════════════
    #  ② 快捷操作带
    # ═══════════════════════════════════════════════════════════════
    def _build_quick_bar(self) -> QFrame:
        """快捷操作带 — 独立行，不挤。"""
        bar = QFrame()
        bar.setObjectName("FdQuickBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(FD_MARGIN, 6, FD_MARGIN, 6)
        lay.setSpacing(8)

        def _qbtn(text, tooltip=""):
            b = QPushButton(text)
            b.setObjectName("FdQuickBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if tooltip:
                b.setToolTip(tooltip)
            return b

        self.btn_q_housekeeping = _qbtn(i18n.t("btn_housekeeping", default="客房服务"), i18n.t("hint_housekeeping", default="通知客房"))
        self.btn_q_housekeeping.clicked.connect(lambda: self._notify_housekeeping(self.current_room or ""))
        lay.addWidget(self.btn_q_housekeeping)

        self.btn_q_extras = _qbtn(i18n.t("btn_quick_extras", default="加项"), i18n.t("hint_quick_extras", default="快速加项"))
        self.btn_q_extras.clicked.connect(self._quick_add_extra)
        lay.addWidget(self.btn_q_extras)

        self.btn_q_print = _qbtn(i18n.t("btn_print_bill", default="打印"), i18n.t("hint_print_bill", default="打印账单"))
        self.btn_q_print.clicked.connect(self._quick_print_bill)
        lay.addWidget(self.btn_q_print)

        self.btn_q_more = QPushButton(i18n.t("btn_more", default="更多"))
        self.btn_q_more.setObjectName("FdQuickBtn")
        self.btn_q_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_q_more.clicked.connect(self._show_quick_menu)
        lay.addWidget(self.btn_q_more)

        lay.addStretch()

        self.btn_change_room = QPushButton(i18n.t("btn_change_room", default="换房"))
        fd_apply_action_btn(self.btn_change_room)
        self.btn_change_room.clicked.connect(self._change_room)
        self.btn_change_room.setVisible(False)
        lay.addWidget(self.btn_change_room)

        self.btn_quick_checkin = QPushButton(i18n.t("btn_quick_checkin", default="快速入住"))
        self.btn_quick_checkin.setObjectName("SolidPrimaryBtn")
        self.btn_quick_checkin.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quick_checkin.clicked.connect(self._quick_checkin)
        lay.addWidget(self.btn_quick_checkin)

        self.btn_team_checkin = QPushButton(i18n.t("btn_team_checkin", default="团队入住"))
        self.btn_team_checkin.setObjectName("FdActSecondary")
        self.btn_team_checkin.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_team_checkin.clicked.connect(self._team_checkin)
        lay.addWidget(self.btn_team_checkin)

        from ui_surface import fd_apply_quick_btn
        for btn in (self.btn_q_housekeeping, self.btn_q_extras, self.btn_q_print, self.btn_q_more):
            fd_apply_quick_btn(btn)
        self._quick_btns = [self.btn_q_housekeeping, self.btn_q_extras, self.btn_q_print, self.btn_q_more, self.btn_quick_checkin, self.btn_team_checkin]

        return bar

    # ═══════════════════════════════════════════════════════════════
    #  ③ 主分屏：左账单 + 右操作
    # ═══════════════════════════════════════════════════════════════
    def _build_content_split(self) -> QHBoxLayout:
        """左右分屏：左账单 60% + 右操作 40%。"""
        split = QHBoxLayout()
        split.setContentsMargins(FD_SPACE_MD, FD_SPACE_MD, FD_SPACE_MD, FD_SPACE_MD)
        split.setSpacing(FD_SPACE_MD)
        split.addWidget(self._build_bill_panel(), 60)
        split.addWidget(self._build_right_panel(), 40)
        return split

    def _build_bill_panel(self) -> QFrame:
        """左区 — 账单卡片。"""
        rail = QFrame()
        rail.setObjectName("BillRail")
        rail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        fd_apply_bill_rail(rail)
        col = QVBoxLayout(rail)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        # 账单卡片
        bill_card = fd_card(self)
        fd_apply_card_panel(bill_card, flush_left=True)
        self._bill_card = bill_card
        bill_lay = fd_card_layout(bill_card)
        bill_lay.setContentsMargins(0, 0, 0, 0)
        bill_lay.setSpacing(0)

        # 标题
        bill_lay.addWidget(fd_section_bar(i18n.t("label_folio", default="账单明细"), show_gold=False))

        # 账单明细表
        self.tbl_folio = QTableWidget(0, 2)
        self.tbl_folio.setObjectName("FdFolioTable")
        self.tbl_folio.setAlternatingRowColors(False)
        self.tbl_folio.setHorizontalHeaderLabels([i18n.t("table_sku", default="项目"), i18n.t("table_amount", default="金额")])
        self.tbl_folio.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl_folio.horizontalHeader().setMinimumHeight(36)
        self.tbl_folio.verticalHeader().setVisible(False)
        self.tbl_folio.verticalHeader().setDefaultSectionSize(32)
        self.tbl_folio.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.tbl_folio.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.tbl_folio.itemChanged.connect(self._on_folio_item_changed)
        self._folio_header_h = 36
        self._folio_row_h = 32
        self._folio_min_rows = 3
        self._folio_min_h = self._folio_header_h + self._folio_row_h * self._folio_min_rows
        self._sync_folio_table_height()
        # 注：tbl_folio 只在下方 folio_shell 内添加一次，此处不直接 addWidget 到 bill_lay
        # （原 line 355 bill_lay.addWidget(self.tbl_folio) 重复，已删除避免 Qt 控件归属混乱）

        folio_shell = QFrame()
        folio_shell.setObjectName("FdBillFolioShell")
        folio_shell.setFrameShape(QFrame.Shape.NoFrame)
        fs_lay = QVBoxLayout(folio_shell)
        fs_lay.setContentsMargins(0, 0, 0, 0)
        fs_lay.addWidget(self.tbl_folio, 0)
        bill_lay.addWidget(folio_shell)
        fd_apply_bill_folio_shell(folio_shell, self.tbl_folio)
        self._folio_shell = folio_shell

        # 费率+押金
        tier_wrap = QFrame(bill_card)
        tier_wrap.setObjectName("FdBillTierRow")
        tier_dep_row = QHBoxLayout(tier_wrap)
        tier_dep_row.setContentsMargins(FD_CARD_PADDING, FD_SPACE_SM, FD_CARD_PADDING, FD_SPACE_SM)
        tier_dep_row.setSpacing(FD_SPACE)

        tier_lbl = QLabel(i18n.t("rate_tier_label", default="费率档位"))
        tier_lbl.setObjectName("FdMutedLabel")
        tier_dep_row.addWidget(tier_lbl)
        self.cmb_rate_tier = QComboBox()
        fd_apply_compact_input(self.cmb_rate_tier)
        from ui_surface import fd_apply_compact_checkin_control
        fd_apply_compact_checkin_control(self.cmb_rate_tier, flat=True)
        self._configure_rate_tier_combo()
        self.cmb_rate_tier.currentIndexChanged.connect(lambda _i: self._on_rate_tier_changed())
        self.cmb_rate_tier.setEnabled(False)
        tier_dep_row.addWidget(self.cmb_rate_tier, 1)

        dep_lbl = QLabel(i18n.t("deposit_adjust", default="押金调整"))
        dep_lbl.setObjectName("FdMutedLabel")
        tier_dep_row.addWidget(dep_lbl)
        self.spn_deposit = QDoubleSpinBox()
        fd_apply_compact_input(self.spn_deposit)
        fd_apply_compact_checkin_control(self.spn_deposit, flat=True)
        self.spn_deposit.setRange(0, 999999)
        self.spn_deposit.setDecimals(2)
        self.spn_deposit.setSingleStep(50)
        self.spn_deposit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_deposit.setPrefix(i18n.t("currency_symbol", default="¥"))
        self.spn_deposit.valueChanged.connect(self._on_deposit_spin_changed)
        tier_dep_row.addWidget(self.spn_deposit, 1)
        bill_lay.addWidget(tier_wrap)
        from ui_surface import fd_apply_bill_tier_row
        fd_apply_bill_tier_row(tier_wrap)
        self._tier_wrap = tier_wrap

        # 费率覆盖提示
        self.lbl_rate_override = QLabel(i18n.t("rate_override_hint", default="费率已覆盖，请填写原因"))
        self.lbl_rate_override.setObjectName("FdWarnLabel")
        self.lbl_rate_override.setWordWrap(True)
        self.lbl_rate_override.setVisible(False)
        bill_lay.addWidget(self.lbl_rate_override)

        self.txt_rate_reason = QLineEdit()
        self.txt_rate_reason.setPlaceholderText(i18n.t("rate_override_reason_ph", default="请输入费率调整原因（≥4字）"))
        fd_apply_compact_input(self.txt_rate_reason)
        self.txt_rate_reason.setVisible(False)
        bill_lay.addWidget(self.txt_rate_reason)

        # 合计条
        totals_frame = QFrame()
        totals_frame.setObjectName("FdTotalsStrip")
        totals_frame.setMinimumHeight(52)
        totals_lay = QHBoxLayout(totals_frame)
        totals_lay.setContentsMargins(FD_CARD_PADDING, FD_SPACE_MD, FD_CARD_PADDING, FD_SPACE_MD)
        totals_lay.setSpacing(FD_SPACE_MD)
        self.lbl_tax = QLabel(f"{i18n.t('label_tax', default='税')}: {i18n.t('currency_symbol', default='¥')}0.00")
        self.lbl_tax.setObjectName("FdMutedLabel")
        self.lbl_total = QLabel(f"{i18n.t('label_total', default='合计')}: {i18n.t('currency_symbol', default='¥')}0.00")
        self.lbl_total.setObjectName("FdTotalAmount")
        self.lbl_paid = QLabel(f"{i18n.t('label_paid', default='已付')}: {i18n.t('currency_symbol', default='¥')}0.00")
        self.lbl_paid.setObjectName("FdPaidAmount")
        totals_lay.addWidget(self.lbl_tax)
        totals_lay.addStretch()
        totals_lay.addWidget(self.lbl_paid)
        totals_lay.addWidget(self.lbl_total)
        bill_lay.addWidget(totals_frame)
        fd_apply_totals_strip(totals_frame)
        self._totals_frame = totals_frame

        bill_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        col.addWidget(bill_card, 1)
        return rail

    def _build_right_panel(self) -> QFrame:
        """右区 — 收款 + 卡操作 + 确认。"""
        rail = QFrame()
        rail.setObjectName("FdCheckinRightRail")
        rail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        fd_apply_checkin_right_rail(rail)
        self._right_rail = rail
        col = QVBoxLayout(rail)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        right_card = fd_card(rail)
        fd_apply_card_panel(right_card, gold_left=False)
        self._right_card = right_card
        right_card_lay = QVBoxLayout(right_card)
        right_card_lay.setContentsMargins(0, 0, 0, 0)
        right_card_lay.setSpacing(0)

        right_body = QFrame(right_card)
        right_body.setObjectName("FdCheckinRightCardBody")
        right_body.setFrameShape(QFrame.Shape.NoFrame)
        from ui_surface import fd_apply_checkin_right_card_body
        fd_apply_checkin_right_card_body(right_body)
        self._right_body = right_body
        right_card_lay.addWidget(right_body, 1)

        body_lay = QVBoxLayout(right_body)
        body_lay.setContentsMargins(FD_CARD_PADDING, FD_CARD_PADDING, FD_CARD_PADDING, FD_CARD_PADDING)
        body_lay.setSpacing(FD_SPACE_MD)

        # ═══ ① 收款区 — 分组容器 ═══
        pay_group = QFrame(right_body)
        pay_group.setObjectName("FdSectionGroup")
        pay_group.setFrameShape(QFrame.Shape.NoFrame)
        pay_group.setStyleSheet(f"QFrame#FdSectionGroup{{background:{_p('surface')};border:1px solid {_p('border')};border-radius:8px;padding:0;}}")
        self._pay_group = pay_group
        pg_lay = QVBoxLayout(pay_group)
        pg_lay.setContentsMargins(10, 8, 10, 10)
        pg_lay.setSpacing(FD_SPACE_SM)

        pay_label = QLabel(i18n.t("section_payment", default="① 收款"))
        pay_label.setObjectName("FdSectionTitle")
        pg_lay.addWidget(pay_label)

        self.pay_tiles = PaymentMethodTiles(pay_group)
        pg_lay.addWidget(self.pay_tiles)
        from ui_surface import fd_apply_payment_tiles
        fd_apply_payment_tiles(self.pay_tiles)

        # 金额 + 收款 + 退款
        amt_row = QHBoxLayout()
        amt_row.setSpacing(FD_SPACE_SM)
        self.txt_amount = QLineEdit()
        self.txt_amount.setPlaceholderText("0.00")
        fd_apply_amount_input(self.txt_amount)
        amt_row.addWidget(self.txt_amount, 2)

        self.btn_pay = QPushButton(i18n.t("btn_receive", default="收款"))
        self.btn_pay.setObjectName("FdActPay")
        fd_apply_action_btn(self.btn_pay, primary=True)
        self.btn_pay.clicked.connect(self._do_pay)
        amt_row.addWidget(self.btn_pay, 1)

        self.btn_refund = QPushButton(i18n.t("btn_refund", default="退款"))
        self.btn_refund.setObjectName("FdDangerBtn")
        fd_apply_action_btn(self.btn_refund, danger=True)
        self.btn_refund.clicked.connect(self._refund)
        amt_row.addWidget(self.btn_refund, 1)
        pg_lay.addLayout(amt_row)

        # 组合支付
        self.btn_combined_pay = QPushButton(i18n.t("btn_combined_pay", default="组合支付"))
        self.btn_combined_pay.setObjectName("FdGhostBtn")
        fd_apply_action_btn(self.btn_combined_pay)
        self.btn_combined_pay.clicked.connect(self._combined_pay)
        pg_lay.addWidget(self.btn_combined_pay)

        body_lay.addWidget(pay_group)

        # ═══ ② 卡操作区 — 分组容器 ═══
        self._card_action_group = self._build_card_action_bar(right_body)
        body_lay.addWidget(self._card_action_group)

        # ═══ 延住备注（按需显示）═══
        self.txt_deferral_remark = QLineEdit(right_body)
        self.txt_deferral_remark.setPlaceholderText(i18n.t("ph_deferral_remark", default="延住/挂账备注"))
        fd_apply_compact_input(self.txt_deferral_remark)
        self.txt_deferral_remark.textChanged.connect(self._refresh_action_gates)
        self.txt_deferral_remark.setVisible(False)
        body_lay.addWidget(self.txt_deferral_remark)

        body_lay.addSpacing(FD_SPACE_SM)

        # ═══ ③ 确认区 — 分组容器 ═══
        commit_group = QFrame(right_body)
        commit_group.setObjectName("FdCommitGroup")
        commit_group.setFrameShape(QFrame.Shape.NoFrame)
        commit_group.setStyleSheet(f"QFrame#FdCommitGroup{{background:{_p('surface')};border:1px solid {_p('border')};border-radius:8px;padding:10px 12px;}}")
        self._commit_group = commit_group
        cg_lay = QVBoxLayout(commit_group)
        cg_lay.setContentsMargins(0, 0, 0, 0)
        self.btn_commit = QPushButton(i18n.t("btn_commit_checkin", default="确认入住"))
        fd_apply_commit_btn(self.btn_commit)
        self.btn_commit.clicked.connect(self._commit)
        cg_lay.addWidget(self.btn_commit)
        body_lay.addWidget(commit_group)

        body_lay.addStretch()

        right_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        col.addWidget(right_card, 1)
        return rail

    def _build_card_action_bar(self, parent=None) -> QFrame:
        """卡操作面板 — READY/INHOUSE 模式色编码。"""
        panel = QFrame(parent)
        panel.setFrameShape(QFrame.Shape.NoFrame)
        panel.setObjectName("FdActionBar")
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(FD_SPACE_SM)

        def _btn(text, obj, *, enabled=True, tooltip=""):
            btn = QPushButton(text)
            btn.setObjectName(obj)
            btn.setEnabled(enabled)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if tooltip:
                btn.setToolTip(tooltip)
            return btn

        # READY 组
        self._card_group_ready = QWidget()
        self._card_group_ready.setObjectName("FdCardGroupReady")
        g_rdy = QVBoxLayout(self._card_group_ready)
        g_rdy.setContentsMargins(FD_SPACE_SM, FD_SPACE_SM, FD_SPACE_SM, FD_SPACE_SM)
        g_rdy.setSpacing(FD_SPACE_SM)
        rdy_lbl = QLabel(i18n.t("section_card_ops", default="② 卡操作"))
        rdy_lbl.setObjectName("FdSectionTitle")
        g_rdy.addWidget(rdy_lbl)
        rdy_row = QHBoxLayout()
        rdy_row.setSpacing(FD_SPACE_SM)
        self.btn_issue_card = _btn(i18n.t("btn_issue_card_short", default="发卡"), "SolidPrimaryBtn", enabled=False, tooltip=i18n.t("hint_issue_card_after_pay", default="收款后可发卡"))
        self.btn_issue_card.clicked.connect(self._issue_card_clicked)
        rdy_row.addWidget(self.btn_issue_card, 1)
        self.btn_read_card = _btn(i18n.t("btn_read_card", default="读卡"), "SolidPrimaryBtn")
        self.btn_read_card.clicked.connect(self._read_card_clicked)
        rdy_row.addWidget(self.btn_read_card, 1)
        g_rdy.addLayout(rdy_row)
        col.addWidget(self._card_group_ready)

        # INHOUSE 组
        self._card_group_inhouse = QWidget()
        self._card_group_inhouse.setObjectName("FdCardGroupInhouse")
        g_inh = QVBoxLayout(self._card_group_inhouse)
        g_inh.setContentsMargins(FD_SPACE_SM, FD_SPACE_SM, FD_SPACE_SM, FD_SPACE_SM)
        g_inh.setSpacing(FD_SPACE_SM)
        inh_lbl = QLabel(i18n.t("section_inhouse_ops", default="② 在住操作"))
        inh_lbl.setObjectName("FdSectionTitle")
        g_inh.addWidget(inh_lbl)
        inh_row1 = QHBoxLayout()
        inh_row1.setSpacing(FD_SPACE_SM)
        self.btn_extend_stay = _btn(i18n.t("btn_extend_stay", default="延住"), "FdActSecondary", enabled=False)
        self.btn_extend_stay.clicked.connect(self._extend_stay_clicked)
        inh_row1.addWidget(self.btn_extend_stay)
        self.btn_co = _btn(i18n.t("btn_checkout", default="退房"), "FdActSecondary", enabled=False)
        self.btn_co.clicked.connect(self._checkout)
        inh_row1.addWidget(self.btn_co)
        self.btn_quick_co = _btn(i18n.t("btn_quick_co", default="快退"), "FdActSecondary", enabled=False)
        self.btn_quick_co.setToolTip(i18n.t("hint_quick_co", default="快速退房"))
        self.btn_quick_co.clicked.connect(self._quick_checkout)
        inh_row1.addWidget(self.btn_quick_co)
        self.btn_team_co = _btn(i18n.t("btn_team_co", default="团队退"), "FdActSecondary")
        self.btn_team_co.clicked.connect(self._team_checkout)
        inh_row1.addWidget(self.btn_team_co)
        g_inh.addLayout(inh_row1)

        inh_row2 = QHBoxLayout()
        inh_row2.setSpacing(FD_SPACE_SM)
        self.btn_cancel_card = _btn(i18n.t("btn_cancel_card", default="注销卡"), "FdDangerBtn", enabled=False)
        self.btn_cancel_card.clicked.connect(self._cancel_card_clicked)
        inh_row2.addWidget(self.btn_cancel_card, 1)
        self.btn_lost_card = _btn(i18n.t("btn_lost_card", default="挂失卡"), "FdDangerBtn", enabled=False)
        self.btn_lost_card.clicked.connect(self._lost_card_clicked)
        inh_row2.addWidget(self.btn_lost_card, 1)
        g_inh.addLayout(inh_row2)
        col.addWidget(self._card_group_inhouse)

        try:
            from motion_gate import attach_primary_button_glow_many
            attach_primary_button_glow_many([
                self.btn_issue_card, self.btn_read_card,
                self.btn_cancel_card, self.btn_lost_card,
                self.btn_extend_stay, self.btn_team_co,
                self.btn_co, self.btn_quick_co,
            ])
        except Exception:
            pass

        from ui_surface import fd_apply_card_action_bar
        fd_apply_card_action_bar(panel)
        self._action_bar = panel
        return panel

    # ═══════════════════════════════════════════════════════════════
    #  ④ 底栏 — Tab 切换（流水 / 交班）
    # ═══════════════════════════════════════════════════════════════
    def _build_bottom_dock(self) -> QWidget:
        """底栏 — Tab 切换省空间。"""
        dock = QFrame()
        dock.setObjectName("FdCheckinBottomDock")
        dock.setMinimumHeight(FD_CHECKIN_BOTTOM_DOCK_MIN)
        dock.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        fd_apply_checkin_bottom_dock(dock)

        lay = QVBoxLayout(dock)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Tab 切换条
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(FD_MARGIN, 4, FD_MARGIN, 4)
        tab_row.setSpacing(4)

        self._btn_tab_ledger = QPushButton(i18n.t("tab_ledger", default="流水记录"))
        self._btn_tab_ledger.setObjectName("FrontdeskHubBtn")
        self._btn_tab_ledger.setCheckable(True)
        self._btn_tab_ledger.setChecked(True)
        self._btn_tab_ledger.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_tab_ledger.clicked.connect(lambda: self._switch_bottom_tab(0))
        tab_row.addWidget(self._btn_tab_ledger)

        self._btn_tab_shift = QPushButton(i18n.t("tab_shift", default="交班账单"))
        self._btn_tab_shift.setObjectName("FrontdeskHubBtn")
        self._btn_tab_shift.setCheckable(True)
        self._btn_tab_shift.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_tab_shift.clicked.connect(lambda: self._switch_bottom_tab(1))
        tab_row.addWidget(self._btn_tab_shift)
        tab_row.addStretch()
        lay.addLayout(tab_row)

        # StackedWidget
        self._bottom_stack = QStackedWidget()
        self._ledger_dock = FrontdeskLedgerStrip(self, limit=12, dock_mode=True)
        self._shift_dock = ShiftDockWidget(self)
        self._bottom_stack.addWidget(self._ledger_dock)
        self._bottom_stack.addWidget(self._shift_dock)
        lay.addWidget(self._bottom_stack, 1)

        self._bottom_dock = dock
        return dock

    def _switch_bottom_tab(self, idx: int):
        self._bottom_stack.setCurrentIndex(idx)
        self._btn_tab_ledger.setChecked(idx == 0)
        self._btn_tab_shift.setChecked(idx == 1)

    # ═══════════════════════════════════════════════════════════════
    #  resize debounce
    # ═══════════════════════════════════════════════════════════════
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "_resize_timer") or self._resize_timer is None:
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._do_resize)
        self._resize_timer.start(150)

    def _do_resize(self):
        self._sync_folio_table_height()

    # ═══════════════════════════════════════════════════════════════
    #  主题刷新
    # ═══════════════════════════════════════════════════════════════
    def _on_theme_changed(self, _theme: str = "") -> None:
        self._refresh_theme_styles()

    def _refresh_theme_styles(self) -> None:
        from ui_surface import fd_refresh_surfaces
        fd_refresh_surfaces(self)
        # 刷新主题时重建 inline QSS 容器（莫兰迪分组容器）
        for grp_name, obj_name in [
            ("_pay_group", "FdSectionGroup"),
            ("_commit_group", "FdCommitGroup"),
        ]:
            grp = getattr(self, grp_name, None)
            if grp is not None:
                grp.setStyleSheet(
                    f"QFrame#{obj_name}{{"
                    f"background:{_p('surface')};"
                    f"border:1px solid {_p('border')};"
                    f"border-radius:8px;padding:0;"
                    f"}}"
                )
        if hasattr(self, "_ledger_dock") and self._ledger_dock is not None:
            try:
                self._ledger_dock._refresh_theme_styles()
            except Exception:
                pass
        if hasattr(self, "_shift_dock") and self._shift_dock is not None:
            try:
                self._shift_dock._refresh_theme_styles()
            except Exception:
                pass
        if hasattr(self, "pay_tiles") and hasattr(self.pay_tiles, "_refresh_theme_styles"):
            try:
                self.pay_tiles._refresh_theme_styles()
            except Exception:
                pass
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)

    # ═══════════════════════════════════════════════════════════════
    #  天数选择器
    # ═══════════════════════════════════════════════════════════════
    def _on_nights_changed(self, nights: int):
        self._stay_nights = nights
        unit = self._get_current_unit_price() if hasattr(self, '_get_current_unit_price') else 0
        self._expected_room_line_total = unit * nights
        self._update_folio_room_charge(self._expected_room_line_total)
        self._calc()
        self._update_room_charge_est()

    def _update_room_charge_est(self):
        sym = i18n.t("currency_symbol", default="¥")
        if self._expected_room_line_total > 0:
            self.lbl_room_charge_est.setText(f"{i18n.t('label_room_charge', default='房费')}: {sym}{self._expected_room_line_total:.2f}")
        else:
            self.lbl_room_charge_est.setText("")

    def _get_current_unit_price(self) -> float:
        if self._forced_unit_price is not None:
            return self._forced_unit_price
        if self._current_rt:
            tier = self._tier_from_combo() if hasattr(self, '_tier_from_combo') else "standard"
            try:
                return db.get_rate_for_room_type(self._current_rt, tier)
            except Exception:
                pass
        return 0

    def _update_folio_room_charge(self, total: float):
        for r in range(self.tbl_folio.rowCount()):
            it = self.tbl_folio.item(r, 0)
            if it and i18n.t("room_charge", default="房费") in it.text():
                amt = self.tbl_folio.item(r, 1)
                if amt:
                    amt.setText(f"{total:.2f}")
                    break

    def _set_room_active(self, has_room: bool) -> None:
        for w in (
            self.tbl_folio, self.cmb_rate_tier, self.spn_deposit,
            self.btn_q_more, self.txt_amount, self.btn_pay, self.btn_refund,
            self.btn_combined_pay, self.btn_commit,
            self.txt_name, self.txt_member, self.txt_id, self.spn_nights,
        ):
            w.setEnabled(has_room)
        self.pay_tiles.setEnabled(has_room)
        if not has_room:
            self.lbl_room_banner.setText(i18n.t("select_room_hint", default="请选择房间"))

    # ═══════════════════════════════════════════════════════════════
    #  房间模式切换
    # ═══════════════════════════════════════════════════════════════
    def _apply_room_mode(self, *, is_inhouse: bool, has_room: bool) -> None:
        if is_inhouse:
            self._card_group_ready.setVisible(False)
            self._card_group_inhouse.setVisible(True)
        else:
            self._card_group_ready.setVisible(True)
            self._card_group_inhouse.setVisible(False)

    def _quick_add_extra(self):
        """快捷加项 — 扫码或手动选择商品/服务加至当前账单。"""
        from ui_helpers import show_info
        show_info(self, "加项", "请选择要追加的商品或服务项目。")

    def _show_quick_menu(self):
        menu = QMenu(self)
        a1 = menu.addAction(i18n.t("btn_borrow_items", default="借物"))
        a1.triggered.connect(self._do_borrow)
        a2 = menu.addAction(i18n.t("btn_room_note", default="房间备注"))
        a2.triggered.connect(self._quick_room_note)
        if self.btn_change_room.isVisible():
            a3 = menu.addAction(i18n.t("btn_change_room", default="换房"))
            a3.triggered.connect(self._change_room)
        menu.addSeparator()
        a5 = menu.addAction(i18n.t("btn_clear_pay", default="清空付款"))
        a5.triggered.connect(self._clear_payments)
        a5.setEnabled(self.current_room is not None)
        a6 = menu.addAction(i18n.t("btn_stay_detail_bill", default="查看入住明细"))
        a6.triggered.connect(self._show_bill_details)
        a6.setEnabled(self.current_room is not None)
        a7 = menu.addAction(i18n.t("btn_ledger_full", default="完整账本"))
        a7.triggered.connect(self._show_full_ledger)
        a7.setEnabled(self.current_room is not None)
        btn = self.btn_q_more
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _sync_folio_table_height(self) -> None:
        if not hasattr(self, "tbl_folio"):
            return
        rows = max(self._folio_min_rows, self.tbl_folio.rowCount())
        h = self._folio_header_h + self._folio_row_h * rows
        self.tbl_folio.setFixedHeight(h)

    def _sync_flow_strip(self):
        inh = False
        if self.current_room:
            try:
                st_row = db.execute("SELECT status FROM rooms WHERE room_id=?", (self.current_room,)).fetchone()
            except Exception:
                st_row = None
            inh = bool(st_row and st_row[0] == "INHOUSE")
        has_room = bool(self.current_room)
        has_pay = len(self.paid_items) >= 1
        if inh:
            cur = 3
        elif self._card_issued_session:
            cur = 3
        elif has_pay:
            cur = 2
        elif has_room:
            cur = 1
        else:
            cur = 0
        if self._flow_strip:
            if has_room and not self._flow_strip.isVisible():
                self._flow_strip.show()
            try:
                self._flow_strip.set_state(cur, room_selected=has_room, paid=has_pay, card_issued=self._card_issued_session, checked_in=inh)
            except Exception:
                pass

    def update_room(self, rid, rt, price=None, *, guest_name=None, guest_phone=None, nights_override=None):
        self.current_room = rid
        self._current_rt = rt
        try:
            st_row = db.execute("SELECT status FROM rooms WHERE room_id=?", (rid,)).fetchone()
        except Exception as _exc:
            show_warning(self, i18n.t("dlg_tip", default="提示"), str(_exc))
            return
        st = st_row[0] if st_row else "READY"
        inh = st == "INHOUSE"
        self.btn_change_room.setVisible(inh)
        self.txt_name.clear()
        self.txt_id.clear()
        self.txt_member.clear()
        self.paid_items = []
        self._posted_ledger_pay_idx = 0
        self.txt_deferral_remark.clear()
        self.txt_rate_reason.clear()
        self.lbl_rate_override.setVisible(False)
        self.txt_rate_reason.setVisible(False)
        self.txt_deferral_remark.setVisible(False)
        self.lbl_paid.setText(f"{i18n.t('label_paid', default='已付')}: {i18n.t('currency_symbol', default='¥')}0.00")
        self.tbl_folio.setRowCount(0)
        self._sync_folio_table_height()
        self._stay_nights = int(nights_override) if nights_override is not None else 1
        if self._stay_nights < 1:
            self._stay_nights = 1
        self.spn_nights.blockSignals(True)
        self.spn_nights.setValue(self._stay_nights)
        self.spn_nights.blockSignals(False)
        self._forced_unit_price = float(price) if price is not None else None
        self._configure_rate_tier_combo()
        self.cmb_rate_tier.blockSignals(True)
        self.cmb_rate_tier.setCurrentIndex(0)
        self.cmb_rate_tier.blockSignals(False)
        if self._forced_unit_price is not None:
            unit = self._forced_unit_price
            self.cmb_rate_tier.setEnabled(False)
        else:
            tier = self._tier_from_combo() if hasattr(self, '_tier_from_combo') else "standard"
            unit = db.get_rate_for_room_type(rt, tier)
            self.cmb_rate_tier.setEnabled(not inh)
        self._expected_room_line_total = unit * self._stay_nights
        room_line = self._expected_room_line_total
        dep_default = db.get_deposit_for_room_type(rt)
        self._add_folio_item(i18n.t("room_charge", default="房费"), room_line)
        if db.get_config("cleaning_fee_enabled") == "1":
            fee = float(db.get_config("cleaning_fee_amount") or 5)
            try:
                rt_fee = db.execute("SELECT cleaning_fee FROM room_type_templates WHERE type_id=?", (rt,)).fetchone()
                if rt_fee and rt_fee[0] is not None:
                    fee = float(rt_fee[0])
            except Exception:
                pass
            self._add_folio_item(i18n.t("cleaning_fee", default="清洁费"), fee)
        self._add_folio_item(i18n.t("deposit_preauth", default="押金"), dep_default)
        self.spn_deposit.blockSignals(True)
        self.spn_deposit.setValue(dep_default)
        self.spn_deposit.blockSignals(False)
        if guest_name:
            self.txt_name.setText(guest_name.strip())
        if guest_phone:
            self.txt_member.setText(guest_phone.strip())
        status_text = i18n.t("room_banner_inhouse", default="在住") if inh else i18n.t("room_banner_vacant", default="空净")
        self.lbl_room_banner.setText(i18n.t("room_selected_banner", default="已选 {0} · {1} · {2}").format(rid, rt, status_text))
        self._card_issued_session = False
        self._apply_room_mode(is_inhouse=inh, has_room=True)
        self._calc()
        self._refresh_rate_override_ui()
        self._sync_flow_strip()
        self._set_room_active(True)

    def prefill_quick_checkin(self, rid, rt, price, guest, phone, days):
        self.update_room(rid, rt, price, guest_name=guest, guest_phone=phone, nights_override=days)

    def _find_folio_row(self, label):
        for r in range(self.tbl_folio.rowCount()):
            it = self.tbl_folio.item(r, 0)
            if it and it.text() == label:
                return r
        return -1

    def _do_borrow(self):
        if not self.current_room:
            show_warning(self, i18n.t("title_borrow_dialog", default="借物"), i18n.t("msg_borrow_select_room", default="请先选择房间"))
            return
        from .borrow_items import show_borrow_dialog
        show_borrow_dialog(self, self.current_room)

    # ═══════════════════════════════════════════════════════════════
    #  本类专属方法 — mixin 未覆盖的功能（保留桩，待后续实装）
    #  收款/退款/退房/换房/团队/计算等核心业务已由各 Mixin 提供：
    #    PaymentMixin / CheckoutMixin / RefundMixin / TeamMixin / GuestInfoMixin
    #  此处仅保留 mixin 未覆盖的辅助操作。
    # ═══════════════════════════════════════════════════════════════

    def _quick_print_bill(self):
        """快速打印当前房间账单 — 待接 report_engine.print_room_bill。"""
        if not self.current_room:
            show_warning(self, i18n.t("title_tip", default="提示"),
                         i18n.t("msg_select_room_first", default="请先选择房间。"))
            return
        from ui_helpers import show_info
        show_info(self, i18n.t("title_print", default="打印"),
                  i18n.t("msg_print_pending", default="打印功能尚未接入 report_engine。"))

    def _quick_checkin(self):
        """快速入住入口 — 引导用户从房态选择房间。"""
        from ui_helpers import show_info
        show_info(self, i18n.t("btn_quick_checkin", default="快速入住"),
                  i18n.t("msg_quick_checkin_hint", default="请从房态矩阵选择房间后入住。"))

    def _quick_room_note(self):
        """快速添加房间备注。"""
        if not self.current_room:
            show_warning(self, i18n.t("title_tip", default="提示"),
                         i18n.t("msg_select_room_first", default="请先选择房间。"))
            return
        from PySide6.QtWidgets import QInputDialog
        note, ok = QInputDialog.getText(self, i18n.t("title_room_note", default="房间备注"),
                                        i18n.t("msg_room_note_input", default="请输入备注内容："))
        if ok and note:
            try:
                db.execute("UPDATE rooms SET note=? WHERE room_id=?",
                           (note, self.current_room))
                db.commit()
                from ui_helpers import show_info
                show_info(self, i18n.t("title_note_saved", default="备注"),
                          i18n.t("msg_note_saved", default="已添加备注：{note}").format(note=note))
            except Exception as exc:
                logger.warning("房间备注保存失败: %s", exc)
                show_warning(self, i18n.t("title_error", default="错误"),
                             i18n.t("msg_note_save_fail", default="备注保存失败，请重试。"))

    def _show_full_ledger(self):
        """查看完整账本 — 待接 report_engine 完整账本视图。"""
        if not self.current_room:
            show_warning(self, i18n.t("title_tip", default="提示"),
                         i18n.t("msg_select_room_first", default="请先选择房间。"))
            return
        from ui_helpers import show_info
        show_info(self, i18n.t("btn_ledger_full", default="完整账本"),
                  i18n.t("msg_ledger_full_pending", default="完整账本视图待接入 report_engine。"))

    def _commit(self):
        """确认入住 — 待接 transactions.checkin.confirm_checkin。"""
        if not self.current_room:
            show_warning(self, i18n.t("title_tip", default="提示"),
                         i18n.t("msg_select_room_first", default="请先选择房间。"))
            return
        from ui_helpers import show_info
        show_info(self, i18n.t("btn_commit_checkin", default="确认入住"),
                  i18n.t("msg_commit_pending", default="确认入住待接入 transactions.checkin。"))

    def _issue_card_clicked(self):
        """发卡按钮 — 待接 card_system.card_issue。"""
        if not self.current_room:
            show_warning(self, i18n.t("title_tip", default="提示"),
                         i18n.t("msg_select_room_first", default="请先选择房间。"))
            return
        from ui_helpers import show_info
        show_info(self, i18n.t("btn_issue_card", default="发卡"),
                  i18n.t("msg_issue_card_pending", default="发卡待接入 card_system.card_issue。"))

    def _read_card_clicked(self):
        """读卡按钮 — 待接 card_system.card_driver。"""
        from ui_helpers import show_info
        show_info(self, i18n.t("btn_read_card", default="读卡"),
                  i18n.t("msg_read_card_pending", default="读卡待接入 card_system.card_driver。"))

    def _extend_stay_clicked(self):
        """延住按钮 — 待接 transactions.extend_stay。"""
        if not self.current_room:
            show_warning(self, i18n.t("title_tip", default="提示"),
                         i18n.t("msg_select_room_first", default="请先选择房间。"))
            return
        from ui_helpers import show_info
        show_info(self, i18n.t("btn_extend_stay", default="延住"),
                  i18n.t("msg_extend_stay_pending", default="延住待接入 transactions.extend_stay。"))

    def _cancel_card_clicked(self):
        """注销卡按钮 — 待接 card_system.card_issue.cancel_card。"""
        if not self.current_room:
            show_warning(self, i18n.t("title_tip", default="提示"),
                         i18n.t("msg_select_room_first", default="请先选择房间。"))
            return
        from ui_helpers import show_info
        show_info(self, i18n.t("btn_cancel_card", default="注销卡"),
                  i18n.t("msg_cancel_card_pending", default="注销卡待接入 card_system.card_issue。"))

    def _lost_card_clicked(self):
        """报失卡按钮 — 待接 transactions.lost_card。"""
        if not self.current_room:
            show_warning(self, i18n.t("title_tip", default="提示"),
                         i18n.t("msg_select_room_first", default="请先选择房间。"))
            return
        from ui_helpers import show_info
        show_info(self, i18n.t("btn_lost_card", default="报失"),
                  i18n.t("msg_lost_card_pending", default="报失待接入 transactions.lost_card。"))
