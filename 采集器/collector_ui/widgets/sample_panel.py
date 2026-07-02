"""
sample_panel.py — 读卡采样面板

卡型选择 + 参数输入 + 采集进度看板 + 读卡/添加样本按钮 + OEM 录制。
从 CollectorWizard 中抽取。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QSpinBox,
    QDateEdit, QFormLayout, QFrame, QWidget, QCheckBox,
)

from PySide6.QtCore import QDate

from ..constants import PALETTE, CARD_TYPES, CARD_NAMES, CARD_KEY_MAP, CARD_KEY_TO_NAME, CARD_DESC_MAP, CARD_FIELDS


class SamplePanel(QGroupBox):
    """读卡采样面板。

    Signals:
        read_blank_requested()   — 读空白卡
        read_written_requested() — 读已写卡
        add_sample_requested()   — 添加样本
        card_type_changed(name: str) — 卡型切换
        launch_oem_requested()   — 启动原厂软件
        toggle_recording()       — 切换录制
        erase_requested()        — 擦卡
    """

    read_blank_requested   = Signal()
    read_written_requested = Signal()
    add_sample_requested   = Signal()
    card_type_changed      = Signal(str)
    launch_oem_requested   = Signal()
    toggle_recording       = Signal()
    erase_requested        = Signal()

    def __init__(self, parent=None):
        super().__init__("② 读卡采样", parent)
        self.setObjectName("FdGhost")
        self._build_ui()

    def _build_ui(self):
        sgl = QVBoxLayout(self)
        sgl.setSpacing(8)

        # ── 卡型选择 + 动态参数（2 列表单） ──
        params_grid = QHBoxLayout()
        params_grid.setSpacing(10)

        left_col = QFormLayout()
        left_col.setSpacing(4)
        ctl = QHBoxLayout()
        self._card_type_combo = QComboBox()
        self._card_type_combo.addItems(CARD_NAMES)
        self._card_type_combo.currentTextChanged.connect(self._on_card_type_changed)
        ctl.addWidget(self._card_type_combo, 1)
        left_col.addRow("卡型", ctl)

        self._room_i = QLineEdit()
        self._room_i.setPlaceholderText("如 101")
        left_col.addRow("房间号", self._room_i)

        self._bd_i = QDateEdit()
        self._bd_i.setCalendarPopup(True)
        self._bd_i.setDate(QDate.currentDate())
        self._bd_i.setDisplayFormat("yyyy-MM-dd")
        left_col.addRow("入住日期", self._bd_i)

        right_col = QFormLayout()
        right_col.setSpacing(4)
        self._building_no_i = QSpinBox()
        self._building_no_i.setRange(0, 255)
        self._building_no_i.setValue(1)
        right_col.addRow("楼栋号", self._building_no_i)

        self._floor_no_i = QSpinBox()
        self._floor_no_i.setRange(0, 255)
        self._floor_no_i.setValue(1)
        right_col.addRow("楼层号", self._floor_no_i)

        self._group_no_i = QSpinBox()
        self._group_no_i.setRange(0, 255)
        self._group_no_i.setValue(1)
        right_col.addRow("组号", self._group_no_i)

        self._ed_i = QDateEdit()
        self._ed_i.setCalendarPopup(True)
        self._ed_i.setDate(QDate.currentDate().addDays(1))
        self._ed_i.setDisplayFormat("yyyy-MM-dd")
        right_col.addRow("退房日期", self._ed_i)

        params_grid.addLayout(left_col)
        params_grid.addLayout(right_col)
        sgl.addLayout(params_grid)

        # [FIX] 保存表单布局引用，供后续字段显隐
        self._form_left = left_col
        self._form_right = right_col

        # ── 采集进度看板 ──
        self._progress_frame = QFrame()
        self._progress_frame.setStyleSheet(
            f"background:{PALETTE['bg_alt']}; border:1px solid {PALETTE['border_strong']}; "
            f"border-radius:8px; padding:4px;")
        pfl = QHBoxLayout(self._progress_frame)
        pfl.setSpacing(4)
        pfl.setContentsMargins(6, 2, 6, 2)
        pfl.addWidget(QLabel("进度:"), 0)

        self._card_progress_grid = QWidget()
        self._card_progress_grid.setStyleSheet("background:transparent;")
        cgl = QHBoxLayout(self._card_progress_grid)
        cgl.setSpacing(4)
        cgl.setContentsMargins(0, 0, 0, 0)
        self._card_progress_labels = {}
        for cname, ckey, _ in CARD_TYPES:
            lbl = QLabel(cname)
            lbl.setStyleSheet(
                f"color:{PALETTE['muted']}; font-size:10px; padding:1px 6px; "
                f"border:1px solid {PALETTE['border_strong']}; border-radius:10px; background:white;")
            cgl.addWidget(lbl)
            self._card_progress_labels[ckey] = lbl
        cgl.addStretch()
        pfl.addWidget(self._card_progress_grid)
        self._progress_hint = QLabel("1组客人卡即可")
        self._progress_hint.setStyleSheet(f"color:{PALETTE['muted']}; font-size:10px;")
        pfl.addWidget(self._progress_hint)
        pfl.addStretch()
        sgl.addWidget(self._progress_frame)

        # 操作描述
        self._st = QLabel("")
        self._st.setWordWrap(True)
        self._st.setStyleSheet(f"font-size:13px; font-weight:600; color:{PALETTE['text']};")
        sgl.addWidget(self._st)

        # ── 操作按钮行 1: 读卡 ──
        cr = QHBoxLayout()
        cr.setSpacing(8)
        self._rb_btn = QPushButton("读空白卡（采样本）")
        self._rb_btn.setObjectName("SolidPrimaryBtn")
        self._rb_btn.clicked.connect(self.read_blank_requested.emit)
        self._rb_btn.setEnabled(False)
        cr.addWidget(self._rb_btn)

        self._rw_btn = QPushButton("读已写卡（采样本）")
        self._rw_btn.setObjectName("SolidPrimaryBtn")
        self._rw_btn.clicked.connect(self.read_written_requested.emit)
        self._rw_btn.setEnabled(False)
        cr.addWidget(self._rw_btn)

        self._add_btn = QPushButton("添加样本")
        self._add_btn.setObjectName("FdGhostBtn")
        self._add_btn.clicked.connect(self.add_sample_requested.emit)
        self._add_btn.setVisible(False)
        cr.addWidget(self._add_btn)

        self._erase_btn = QPushButton("擦卡重试")
        self._erase_btn.setObjectName("FdGhostBtn")
        self._erase_btn.clicked.connect(self.erase_requested.emit)
        self._erase_btn.setVisible(False)
        cr.addWidget(self._erase_btn)
        cr.addStretch()
        sgl.addLayout(cr)

        # ── 操作按钮行 2: OEM 录制 ──
        ur = QHBoxLayout()
        ur.setSpacing(8)
        self._launch_btn = QPushButton("启动原厂发卡软件")
        self._launch_btn.setObjectName("SolidPrimaryBtn")
        self._launch_btn.clicked.connect(self.launch_oem_requested.emit)
        self._launch_btn.setEnabled(False)
        self._launch_btn.setVisible(False)
        ur.addWidget(self._launch_btn)

        self._record_btn = QPushButton("开始录制操作")
        self._record_btn.setObjectName("FdGhostBtn")
        self._record_btn.clicked.connect(self.toggle_recording.emit)
        self._record_btn.setEnabled(False)
        self._record_btn.setVisible(False)
        ur.addWidget(self._record_btn)

        self._recording_status = QLabel("")
        self._recording_status.setStyleSheet(f"color:{PALETTE['green']}; font-size:12px;")
        self._recording_status.setVisible(False)
        ur.addWidget(self._recording_status)
        ur.addStretch()
        sgl.addLayout(ur)

        # 监控状态条
        self._monitor_status = QLabel("")
        self._monitor_status.setStyleSheet(
            f"color:{PALETTE['green']}; font-size:12px; background:{PALETTE['bg_alt']}; "
            f"border-radius:4px; padding:4px 8px;")
        sgl.addWidget(self._monitor_status)

        # 提示
        self._tip = QLabel("")
        self._tip.setWordWrap(True)
        self._tip.setStyleSheet(
            f"color:#92400E; background:#FEF3C7; border:1px solid #F59E0B; "
            f"border-radius:8px; padding:10px 12px; font-size:13px;")
        sgl.addWidget(self._tip)

        # payload 显示
        self._pl = QLabel("")
        self._pl.setStyleSheet(
            f"color:{PALETTE['muted']}; font-size:11px; font-family:monospace;")
        sgl.addWidget(self._pl)

        # 样本计数
        self._count_label = QLabel("")
        self._count_label.setStyleSheet(f"color:{PALETTE['muted']}; font-size:12px;")
        sgl.addWidget(self._count_label)

    # ── 卡型切换 ─────────────────────────────────────────

    def _on_card_type_changed(self, text: str):
        """卡型切换：更新描述 + 显示/隐藏字段。"""
        key = CARD_KEY_MAP.get(text, "guest")
        field_set = CARD_FIELDS.get(text, set())

        desc = CARD_DESC_MAP.get(key, "")
        self._st.setText(desc)

        # 显隐字段
        has_room  = "room" in field_set
        has_date  = "b_date" in field_set
        has_bld   = "building_no" in field_set
        has_flr   = "floor_no" in field_set
        has_grp   = "group_no" in field_set

        for w, vis in [
            (self._room_i, has_room),
            (self._bd_i, has_date), (self._ed_i, has_date),
            (self._building_no_i, has_bld),
            (self._floor_no_i, has_flr),
            (self._group_no_i, has_grp),
        ]:
            # [FIX] 用正确的 FormLayout 引用
            form = self._form_left
            if w in (self._building_no_i, self._floor_no_i, self._group_no_i, self._ed_i):
                form = self._form_right
            lbl = form.labelForField(w)
            if lbl:
                lbl.setVisible(vis)
            w.setVisible(vis)

        self.card_type_changed.emit(text)

    # ── 进度看板 ─────────────────────────────────────────

    def update_progress_grid(self, collected_keys: set[str]):
        """刷新采集进度看板标签。"""
        for cname, ckey, _ in CARD_TYPES:
            lbl = self._card_progress_labels.get(ckey)
            if not lbl:
                continue
            if ckey in collected_keys:
                lbl.setStyleSheet(
                    f"color:{PALETTE['green']}; font-size:11px; padding:2px 8px; "
                    f"border:1px solid {PALETTE['green_border']}; border-radius:10px; "
                    f"background:{PALETTE['green_bg']}; font-weight:600;")
                lbl.setText(cname + " ✓")
            else:
                lbl.setStyleSheet(
                    f"color:{PALETTE['muted']}; font-size:11px; padding:2px 8px; "
                    f"border:1px solid {PALETTE['border_strong']}; border-radius:10px; background:white;")
                lbl.setText(cname)

    # ── 外部状态设置 ─────────────────────────────────────

    def set_tip(self, text: str, level: str = "info"):
        """设置提示文本。level: info / warn / error"""
        style_map = {
            "info": (f"color:#92400E; background:#FEF3C7; border:1px solid #F59E0B; "
                     f"border-radius:8px; padding:10px 12px; font-size:13px;"),
            "warn": (f"color:white; background:{PALETTE['warn']}; border-radius:8px; "
                     f"padding:10px; font-size:13px;"),
            "error": (f"color:white; background:{PALETTE['danger']}; border-radius:8px; "
                      f"padding:10px; font-size:13px;"),
        }
        self._tip.setStyleSheet(style_map.get(level, style_map["info"]))
        self._tip.setText(text)

    def set_monitor_status(self, text: str):
        self._monitor_status.setText(text)
        self._monitor_status.setStyleSheet(
            f"color:{PALETTE['green']}; font-size:12px; background:{PALETTE['green_bg']}; "
            f"border:1px solid {PALETTE['green_border']}; border-radius:4px; padding:6px 10px;")

    def reset_read_buttons(self):
        """重置读卡按钮到初始状态（添加样本后调用）。"""
        self._rb_btn.setEnabled(True)
        self._rb_btn.setText("读空白卡（采样本）")
        self._rw_btn.setEnabled(False)
        self._rw_btn.setText("读已写卡（采样本）")
        self._add_btn.setVisible(False)
        self._erase_btn.setVisible(False)
        self._launch_btn.setVisible(False)
        self._record_btn.setVisible(False)
        self._recording_status.setVisible(False)
        self._pl.setText("")

    # ── 属性 ─────────────────────────────────────────────

    @property
    def card_type_combo(self) -> QComboBox:
        return self._card_type_combo

    @property
    def rb_btn(self) -> QPushButton:
        return self._rb_btn

    @property
    def rw_btn(self) -> QPushButton:
        return self._rw_btn

    @property
    def add_btn(self) -> QPushButton:
        return self._add_btn

    @property
    def launch_btn(self) -> QPushButton:
        return self._launch_btn

    @property
    def record_btn(self) -> QPushButton:
        return self._record_btn

    @property
    def erase_btn(self) -> QPushButton:
        """擦卡按钮（由主窗口动态添加到高级面板，此处提供占位）。"""
        return getattr(self, "_erase_btn", None)

    @property
    def room_input(self) -> QLineEdit:
        return self._room_i

    @property
    def bd_input(self) -> QDateEdit:
        return self._bd_i

    @property
    def ed_input(self) -> QDateEdit:
        return self._ed_i

    @property
    def building_no_input(self) -> QSpinBox:
        return self._building_no_i

    @property
    def floor_no_input(self) -> QSpinBox:
        return self._floor_no_i

    @property
    def group_no_input(self) -> QSpinBox:
        return self._group_no_i

    @property
    def tip_label(self) -> QLabel:
        return self._tip

    @property
    def payload_label(self) -> QLabel:
        return self._pl

    @property
    def count_label(self) -> QLabel:
        return self._count_label
