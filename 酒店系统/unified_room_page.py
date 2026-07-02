"""统一房间与房型管理页 — 将房型管理和房间管理集成在一个视图中。

布局说明（从上到下）：
  ① 房型概览条（水平滚动卡片）— 新增/编辑/删除房型，点击过滤房间
  ② 左侧：楼栋列表 | 右侧：搜索+房间表格
  ③ 批量操作折叠面板

所有编辑通过弹窗完成（双击房间行或点击操作按钮）。
"""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import db
from design_tokens import _p, active_room_status_theme, FONT_SM, FONT_MD, RADIUS_MD, RADIUS_LG, SHADOW_SM, SHADOW_MD
from event_bus import bus
from ui_surface import fd_apply_data_table_shell, fd_refresh_surfaces, fd_apply_table_palette
from lock_legacy_bridge import ROOM_STATUS_LEGACY, display_lock_no, lock_no_from_parts, normalize_lock_no_hex
from ui_helpers import ask_confirm, build_dialog_header, show_info, show_warning, style_dialog


# ═══════════════════════════════════════════════════════════════════
# 房态颜色映射（跟随主题）
# ═══════════════════════════════════════════════════════════════════
# 房态 → 语义键（颜色跟随 active_room_status_theme）
_STATUS_SEMANTIC: dict[str, tuple[str, str]] = {
    "VC": ("空净房", "READY"),
    "VD": ("脏房", "DIRTY"),
    "OC": ("入住", "INHOUSE"),
    "OC_WalkIn": ("散客房", "INHOUSE"),
    "OC_Team": ("团体房", "INHOUSE"),
    "OO": ("维修房", "MAINTENANCE"),
    "OH": ("钟点房", "OVERTIME"),
    "OT": ("预订房", "INHOUSE"),
    "TO": ("催租房", "OVERTIME"),
    "READY": ("空净房", "READY"),
    "INHOUSE": ("入住", "INHOUSE"),
    "DIRTY": ("脏房", "DIRTY"),
    "OVERTIME": ("超时", "OVERTIME"),
    "MAINTENANCE": ("维修", "MAINTENANCE"),
}


def _status_color(semantic_key: str) -> str:
    pal = active_room_status_theme()
    entry = pal.get(semantic_key) or pal.get("READY", {})
    return entry.get("border") or entry.get("color") or _p("primary")


def _status_display(status: str) -> tuple[str, str]:
    """返回 (显示文本, 颜色 hex) — 跟随当前 UI 主题房态色板。"""
    s = (status or "").strip()
    if s in _STATUS_SEMANTIC:
        label, key = _STATUS_SEMANTIC[s]
        return label, _status_color(key)
    for k, (label, key) in _STATUS_SEMANTIC.items():
        if k and (s.startswith(k) or s == label):
            return label, _status_color(key)
    return (s or "—", _p("text_dim"))


def _room_types_data() -> list[dict]:
    """返回所有房型数据（含使用该房型的房间计数）。"""
    try:
        rows = db.execute(
            "SELECT t.type_id, t.type_name, COALESCE(t.base_price,0), "
            "COALESCE(t.hourly_price,0), COALESCE(t.default_deposit,0), "
            "COALESCE(t.icon,''), "
            "(SELECT COUNT(*) FROM rooms r WHERE r.room_type=t.type_name OR r.room_type=t.type_id) AS room_count "
            "FROM room_type_templates t ORDER BY t.type_id"
        ).fetchall()
        return [
            {
                "type_id": r[0],
                "type_name": r[1],
                "base_price": float(r[2] or 0),
                "hourly_price": float(r[3] or 0),
                "default_deposit": float(r[4] or 0),
                "icon": r[5] or "",
                "room_count": r[6] or 0,
            }
            for r in rows
        ]
    except Exception:
        return []


def _prop_definitions() -> list[dict[str, Any]]:
    try:
        rows = db.execute(
            "SELECT key, label, field_type, options, sort_order FROM room_prop_definitions "
            "WHERE enabled=1 ORDER BY sort_order, key"
        ).fetchall()
        return [
            {"key": r[0], "label": r[1], "field_type": r[2],
             "options": json.loads(r[3]) if r[3] else [], "sort_order": r[4]}
            for r in rows
        ]
    except Exception:
        return []


def _item(value: Any) -> QTableWidgetItem:
    return QTableWidgetItem("" if value is None else str(value))


def _range_values(raw: str) -> list[int]:
    s = (raw or "").strip().replace("，", ",")
    vals: list[int] = []
    for part in s.split(","):
        p = part.strip()
        if not p:
            continue
        if "-" in p:
            a, _, b = p.partition("-")
            start, end = int(a), int(b)
            step = 1 if end >= start else -1
            vals.extend(range(start, end + step, step))
        else:
            vals.append(int(p))
    return vals


# ═══════════════════════════════════════════════════════════════════
# 房型卡片组件
# ═══════════════════════════════════════════════════════════════════
class TypeCard(QFrame):
    """单个房型概览卡片 — 显示图标、名称、价格、房间数。"""

    def __init__(self, data: dict, selected: bool = False) -> None:
        super().__init__()
        self.type_name = data["type_name"]
        self.type_id = data["type_id"]
        self._selected = selected
        self.clicked = None

        self.setObjectName("TypeCard")
        self.setFixedSize(150, 100)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", "true" if selected else "false")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)

        icon_row = QHBoxLayout()
        icon_row.setSpacing(8)
        icon_label = QLabel(data.get("icon", "") or "")
        icon_label.setObjectName("TypeCardIcon")
        icon_label.setFixedSize(28, 28)
        icon_row.addWidget(icon_label)

        name_label = QLabel(data["type_name"])
        name_label.setObjectName("TypeCardName")
        name_label.setWordWrap(False)
        icon_row.addWidget(name_label, 1)
        lay.addLayout(icon_row)

        price_label = QLabel(f"¥{data['base_price']:.0f}")
        price_label.setObjectName("TypeCardPrice")
        lay.addWidget(price_label)

        count_label = QLabel(f"{data['room_count']} 间")
        count_label.setObjectName("TypeCardCount")
        lay.addWidget(count_label)
        lay.addStretch()

    def set_selected(self, sel: bool) -> None:
        self._selected = sel
        self.setProperty("selected", "true" if sel else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.clicked:
            self.clicked(self.type_name)
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════════
# 房间类型图标预设（复用 room_type_admin_page 中的定义）
# ═══════════════════════════════════════════════════════════════════
ROOM_TYPE_ICON_PRESETS = [
    ("自定义 / 不显示", ""),
    ("大床房 (K)", "🛏"),
    ("双床房 (T)", "🛌"),
    ("标准间 (S)", "🏨"),
    ("豪华间 (D)", "⭐"),
    ("套房 (X)", "🛋"),
    ("家庭房 (F)", "🏠"),
    ("商务房 (B)", "💼"),
    ("钟点房 (H)", "⏱"),
    ("情侣房 (C)", "🌹"),
    ("总统套 (P)", "👑"),
    ("无障碍房 (A)", "♿"),
    ("禁烟房 (N)", "🚭"),
]


# ═══════════════════════════════════════════════════════════════════
# 统一页面
# ═══════════════════════════════════════════════════════════════════
class UnifiedRoomPage(QWidget):
    """统一房间与房型管理。"""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("UnifiedRoomPage")
        # ── 状态 ──
        self._buildings: list[tuple] = []
        self._rooms: list[tuple] = []
        self._current_bld_no: int | None = None  # None = 所有楼栋
        self._filter_type: str | None = None  # 按房型过滤（None = 全部）
        self._type_cards: list[TypeCard] = []
        self._pending_room_id: str | None = None

        self._build_ui()
        QTimer.singleShot(0, self.refresh)
        bus.theme_changed.connect(lambda _: fd_refresh_surfaces(self))

    # ════════════════════════════════════════════════════════════════
    # UI 构建
    # ════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)

        # ── ① 顶部标题 ──
        root.addWidget(build_dialog_header("房间与房型管理",
                                            "统一管理楼栋、房间、房型模板、价格覆盖和扩展属性。"))

        # ── ② 房型概览条 ──
        root.addWidget(self._build_type_strip())

        # ── ③ 主区域：左树 | 右表（ContentBox 包裹）──
        main_frame = QFrame()
        main_frame.setObjectName("ContentBox")
        from ui_surface import fd_apply_content_box
        fd_apply_content_box(main_frame)
        main_lay = QVBoxLayout(main_frame)
        main_lay.setContentsMargins(8, 8, 8, 8)
        main_lay.setSpacing(8)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_nav_panel())
        split.addWidget(self._build_room_panel())
        split.setSizes([220, 580])
        main_lay.addWidget(split, 1)
        root.addWidget(main_frame, 1)

        # ── ④ 批量操作 → 改用按钮触发弹窗 ──

    def _build_type_strip(self) -> QFrame:
        """房型概览条：水平滚动卡片 + 操作按钮。"""
        container = QFrame()
        container.setFrameShape(QFrame.Shape.NoFrame)
        container.setObjectName("TypeStripContainer")

        lay = QVBoxLayout(container)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)

        title_row = QHBoxLayout()
        title_lbl = QLabel("房型概览")
        title_lbl.setObjectName("TypeStripTitle")
        title_row.addWidget(title_lbl)
        title_row.addStretch()

        self._type_filter_info = QLabel("")
        self._type_filter_info.setObjectName("FdMutedLabel")
        self._type_filter_info.setVisible(False)
        title_row.addWidget(self._type_filter_info)

        self.btn_clear_type_filter = QPushButton("✕ 清除过滤")
        self.btn_clear_type_filter.setObjectName("FdGhostBtn")
        self.btn_clear_type_filter.setMaximumHeight(30)
        self.btn_clear_type_filter.setVisible(False)
        self.btn_clear_type_filter.clicked.connect(self._clear_type_filter)
        title_row.addWidget(self.btn_clear_type_filter)

        btn_new_type = QPushButton("＋ 新增房型")
        btn_new_type.setObjectName("SolidPrimaryBtn")
        btn_new_type.setMaximumHeight(36)
        btn_new_type.clicked.connect(self._add_type_dialog)
        title_row.addWidget(btn_new_type)
        lay.addLayout(title_row)

        scroll = QScrollArea()
        scroll.setObjectName("TypeCardScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(108)
        scroll.setMaximumHeight(200)

        self._type_card_container = QFrame()
        self._type_card_container.setObjectName("TypeCardRow")
        self._type_card_container.setFrameShape(QFrame.Shape.NoFrame)
        self._card_lay = QHBoxLayout(self._type_card_container)
        self._card_lay.setContentsMargins(8, 8, 8, 8)
        self._card_lay.setSpacing(8)
        self._card_lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        scroll.setWidget(self._type_card_container)
        lay.addWidget(scroll)
        self._type_scroll = scroll
        return container

    def _rebuild_type_cards(self) -> None:
        """重新渲染房型概览卡片。"""
        while self._card_lay.count():
            item = self._card_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._type_cards = []
        types = _room_types_data()

        for td in types:
            card = TypeCard(td, selected=(td["type_name"] == self._filter_type))
            card.clicked = lambda name, c=card: self._on_type_card_clicked(name)
            card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda _, td=td: self._edit_type_dialog(td["type_id"])
            )
            self._card_lay.addWidget(card)
            self._type_cards.append(card)

        if not types:
            tip = QLabel("暂无房型定义，请点击「＋ 新增房型」添加。")
            tip.setObjectName("EmptyStateTip")
            self._card_lay.addWidget(tip)

        self._type_card_container.adjustSize()
        if hasattr(self, "_type_scroll"):
            self._type_scroll.updateGeometry()

    def _on_type_card_clicked(self, type_name: str) -> None:
        if self._filter_type == type_name:
            self._clear_type_filter()
            return
        self._filter_type = type_name
        self._current_bld_no = None
        self._update_type_card_selection()
        self._type_filter_info.setText(f"当前过滤：{type_name}")
        self._type_filter_info.setVisible(True)
        self.btn_clear_type_filter.setVisible(True)
        self._populate_nav_tree()
        self._load_rooms(self.txt_search.text().strip())

    def _clear_type_filter(self) -> None:
        self._filter_type = None
        self._update_type_card_selection()
        self._type_filter_info.setVisible(False)
        self.btn_clear_type_filter.setVisible(False)
        self._load_rooms(self.txt_search.text().strip())

    def _update_type_card_selection(self) -> None:
        for card in self._type_cards:
            card.set_selected(card.type_name == self._filter_type)

    def _build_nav_panel(self) -> QFrame:
        """左侧导航树：楼栋 + 房型 + 总览。"""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.NoFrame)
        panel.setObjectName("NavPanelContainer")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        self.nav_tree = QTreeWidget()
        self.nav_tree.setHeaderHidden(True)
        self.nav_tree.setObjectName("RoomNavTree")
        self.nav_tree.setIndentation(16)
        self.nav_tree.setAnimated(True)
        self.nav_tree.setMinimumWidth(180)
        self.nav_tree.itemClicked.connect(self._on_nav_item_clicked)
        lay.addWidget(self.nav_tree, 1)

        # 底部操作按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_add_bld = QPushButton("＋ 楼栋")
        btn_add_bld.setObjectName("FdCardActionBtn")
        btn_add_bld.setMaximumHeight(36)
        btn_add_bld.clicked.connect(self._add_building)
        btn_row.addWidget(btn_add_bld)

        btn_add_type = QPushButton("＋ 房型")
        btn_add_type.setObjectName("FdCardActionBtn")
        btn_add_type.setMaximumHeight(36)
        btn_add_type.clicked.connect(self._add_type_dialog)
        btn_row.addWidget(btn_add_type)

        btn_row.addStretch()
        lay.addLayout(btn_row)
        return panel

    def _on_nav_item_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        """点击导航树节点 → 过滤房间列表。"""
        item_type = item.data(0, Qt.ItemDataRole.UserRole) or ""
        item_key = item.data(0, Qt.ItemDataRole.UserRole + 1) or ""

        if item_type == "overview":
            self._current_bld_no = None
            self._filter_type = None
            self._type_filter_info.setVisible(False)
            self.btn_clear_type_filter.setVisible(False)
            self._update_type_card_selection()
        elif item_type == "building":
            self._current_bld_no = int(item_key) if item_key else None
            self._filter_type = None
            self._type_filter_info.setVisible(False)
            self.btn_clear_type_filter.setVisible(False)
            self._update_type_card_selection()
        elif item_type == "roomtype":
            self._current_bld_no = None
            self._filter_type = item_key or None
            self._update_type_card_selection()
            if self._filter_type:
                self._type_filter_info.setText(f"当前过滤：{self._filter_type}")
                self._type_filter_info.setVisible(True)
                self.btn_clear_type_filter.setVisible(True)
            else:
                self._type_filter_info.setVisible(False)
                self.btn_clear_type_filter.setVisible(False)
        self._load_rooms(self.txt_search.text().strip())

    def _populate_nav_tree(self) -> None:
        """填充导航树：总览 → 按楼栋 → 按房型。"""
        self.nav_tree.clear()

        # 总览
        root_item = QTreeWidgetItem(self.nav_tree)
        root_item.setText(0, "所有房间视图")
        root_item.setData(0, Qt.ItemDataRole.UserRole, "overview")
        root_item.setData(0, Qt.ItemDataRole.UserRole + 1, "")
        root_item.setExpanded(True)

        # 楼栋分组
        bld_item = QTreeWidgetItem(root_item)
        bld_item.setText(0, "按楼栋")
        bld_item.setData(0, Qt.ItemDataRole.UserRole, "group")
        bld_item.setExpanded(True)

        for building in self._buildings:
            _, bld_no, name, room_count = building
            child = QTreeWidgetItem(bld_item)
            child.setText(0, f"{name}  ({room_count}间)")
            child.setData(0, Qt.ItemDataRole.UserRole, "building")
            child.setData(0, Qt.ItemDataRole.UserRole + 1, bld_no)

        # 房型分组
        rt_item = QTreeWidgetItem(root_item)
        rt_item.setText(0, "按房型")
        rt_item.setData(0, Qt.ItemDataRole.UserRole, "group")
        rt_item.setExpanded(True)

        types = _room_types_data()
        for td in types:
            child = QTreeWidgetItem(rt_item)
            icon = td.get("icon", "") or ""
            display = f"{icon} {td['type_name']} ({td['room_count']}间)" if icon else f"{td['type_name']} ({td['room_count']}间)"
            child.setText(0, display)
            child.setData(0, Qt.ItemDataRole.UserRole, "roomtype")
            child.setData(0, Qt.ItemDataRole.UserRole + 1, td["type_name"])

        if not types:
            tip = QTreeWidgetItem(rt_item)
            tip.setText(0, "暂无房型定义")

    def _build_room_panel(self) -> QFrame:
        """房间列表面板（含搜索和操作按钮）。"""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.NoFrame)
        panel.setObjectName("RoomPanelContainer")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # 搜索行
        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("搜索房号…")
        self.txt_search.setObjectName("RoomSearchInput")
        self.txt_search.textChanged.connect(self._filter_rooms)
        search_row.addWidget(self.txt_search, 1)

        self.lbl_room_count = QLabel("0 间")
        self.lbl_room_count.setObjectName("RoomCountLabel")
        search_row.addWidget(self.lbl_room_count)

        lay.addLayout(search_row)

        # 房间表格
        self.tbl_rooms = QTableWidget(0, 8)
        self.tbl_rooms.setHorizontalHeaderLabels(
            ["楼栋", "楼层", "房号", "房型", "状态", "价格", "备注", "锁号"]
        )
        self.tbl_rooms.setAlternatingRowColors(False)
        self.tbl_rooms.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_rooms.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_rooms.setObjectName("RoomTable")
        self.tbl_rooms.verticalHeader().setDefaultSectionSize(32)
        self.tbl_rooms.itemDoubleClicked.connect(self._on_room_double_clicked)
        # 列宽
        header = self.tbl_rooms.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 楼栋
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 楼层
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 房号
        header.setSectionResizeMode(3, QHeaderView.Stretch)           # 房型
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 状态
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 价格
        header.setSectionResizeMode(6, QHeaderView.Stretch)           # 备注
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 锁号

        table_shell = QFrame()
        table_shell.setObjectName("DataTableShell")
        table_shell.setFrameShape(QFrame.Shape.NoFrame)
        table_shell.setMinimumHeight(200)
        table_shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ts_lay = QVBoxLayout(table_shell)
        ts_lay.setContentsMargins(0, 0, 0, 0)
        ts_lay.addWidget(self.tbl_rooms, 1)
        lay.addWidget(table_shell, 1)
        from ui_surface import fd_apply_data_table_shell
        fd_apply_data_table_shell(table_shell, self.tbl_rooms)

        # 操作按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_add = QPushButton("+ 批量添加")
        btn_add.setObjectName("SolidPrimaryBtn")
        btn_add.setMaximumHeight(36)
        btn_add.clicked.connect(self._add_rooms)
        btn_row.addWidget(btn_add)

        btn_import = QPushButton("从老系统导入")
        btn_import.setObjectName("FdActSecondary")
        btn_import.setMaximumHeight(36)
        btn_import.clicked.connect(self._import_rooms)
        btn_row.addWidget(btn_import)

        btn_edit = QPushButton("编辑选中")
        btn_edit.setObjectName("FdCardActionBtn")
        btn_edit.setMaximumHeight(36)
        btn_edit.clicked.connect(self._edit_selected_room)
        btn_row.addWidget(btn_edit)

        btn_del = QPushButton("删除选中")
        btn_del.setObjectName("FdDangerBtn")
        btn_del.setMaximumHeight(36)
        btn_del.clicked.connect(self._delete_room)
        btn_row.addWidget(btn_del)

        btn_row.addStretch()

        # 批量操作 → 弹窗模式
        self.btn_batch = QPushButton("☰ 批量操作")
        self.btn_batch.setObjectName("FdActSecondary")
        self.btn_batch.setMaximumHeight(36)
        self.btn_batch.clicked.connect(self._batch_dialog)
        btn_row.addWidget(self.btn_batch)
        btn_row.addStretch()

        # 右键菜单 → 跳转到指定房间
        self._pending_room_id = None

        lay.addLayout(btn_row)
        return panel

    # ════════════════════════════════════════════════════════════════
    # 房型卡片管理
    # ════════════════════════════════════════════════════════════════
    # 房型卡片管理
    # ════════════════════════════════════════════════════════════════

    # ════════════════════════════════════════════════════════════════
    # 房型 CRUD 对话框
    # ════════════════════════════════════════════════════════════════
    # 房型 CRUD 对话框
    # ════════════════════════════════════════════════════════════════

    def _add_type_dialog(self) -> None:
        self._edit_type_dialog(None)

    def _edit_type_dialog(self, type_id: str | None) -> None:
        """新增/编辑房型弹窗。"""
        is_new = type_id is None
        dlg = QDialog(self)
        dlg.setWindowTitle("新增房型" if is_new else "编辑房型")
        style_dialog(dlg, size="medium")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(build_dialog_header(
            "房型资料",
            "维护房型基础价、押金、入住标配和显示图标。"
        ))

        # 表单区
        form_container = QWidget()
        form = QFormLayout(form_container)
        form.setContentsMargins(20, 16, 20, 16)
        form.setSpacing(12)

        # 预填值
        preset = {}
        if not is_new:
            row = db.execute(
                "SELECT type_id, type_name, base_price, hourly_price, default_deposit, "
                "consumables_json, COALESCE(icon,'') FROM room_type_templates WHERE type_id=?",
                (type_id,),
            ).fetchone()
            if row:
                preset = {
                    "type_id": row[0],
                    "type_name": row[1],
                    "base_price": row[2],
                    "hourly_price": row[3],
                    "default_deposit": row[4],
                    "consumables_json": row[5] or "{}",
                    "icon": row[6] or "",
                }

        txt_id = QLineEdit(preset.get("type_id", ""))
        txt_name = QLineEdit(preset.get("type_name", ""))
        txt_base = QLineEdit(str(preset.get("base_price", "0")))
        txt_hourly = QLineEdit(str(preset.get("hourly_price", "0")))
        txt_dep = QLineEdit(str(preset.get("default_deposit", "0")))
        txt_json = QLineEdit(preset.get("consumables_json", "{}"))

        # 图标选择
        icon_row = QHBoxLayout()
        cmb_icon = QComboBox()
        cmb_icon.setEditable(False)
        current_icon = (preset.get("icon") or "").strip()
        for label, value in ROOM_TYPE_ICON_PRESETS:
            cmb_icon.addItem(label, value)
        match_idx = next(
            (i for i, (_, v) in enumerate(ROOM_TYPE_ICON_PRESETS) if v == current_icon), -1
        )
        if match_idx >= 0:
            cmb_icon.setCurrentIndex(match_idx)
        txt_icon = QLineEdit(current_icon)
        txt_icon.setPlaceholderText("可直接输入 emoji（留空走字母兜底）")
        txt_icon.setMaximumWidth(160)

        def _on_preset_icon_change(idx: int):
            val = cmb_icon.itemData(idx)
            txt_icon.setText(val if val else "")

        cmb_icon.currentIndexChanged.connect(_on_preset_icon_change)
        icon_row.addWidget(cmb_icon, 1)
        icon_row.addWidget(txt_icon)

        form.addRow("房型ID", txt_id)
        form.addRow("房型名称", txt_name)
        form.addRow("显示图标", icon_row)
        form.addRow("标准价", txt_base)
        form.addRow("钟点价", txt_hourly)
        form.addRow("默认押金", txt_dep)
        json_row = QHBoxLayout()
        json_row.addWidget(txt_json, 1)
        btn_pick_consumables = QPushButton("从库存消耗品选择")
        btn_pick_consumables.setObjectName("FdGhostBtn")
        btn_pick_consumables.clicked.connect(lambda: self._pick_consumables_json(txt_json))
        json_row.addWidget(btn_pick_consumables)
        form.addRow("入住标配", json_row)

        # 价格同步选项（编辑时显示）
        if not is_new and type_id:
            room_count = db.execute(
                "SELECT COUNT(*) FROM rooms WHERE room_type=(SELECT type_name FROM room_type_templates WHERE type_id=?)",
                (type_id,),
            ).fetchone()[0]
            if room_count > 0:
                sync_cb = QCheckBox(f"同步新价格到 {room_count} 间使用此房型的房间（跳过已设覆盖价的房间）")
                sync_cb.setChecked(True)
                form.addRow("", sync_cb)

                type_delete_warn = QLabel(f"⚠ 删除此房型不会自动修改 {room_count} 间房的房型字段")
                type_delete_warn.setStyleSheet(f"color:{_p('text_muted')}; font-size:11px;")
                form.addRow("", type_delete_warn)

        lay.addWidget(form_container, 1)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(20, 0, 20, 16)
        btn_ok = QPushButton("保存")
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("FdGhostBtn")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        lay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # ── 保存逻辑 ──
        try:
            base_price = float(txt_base.text().strip() or 0)
            hourly_price = float(txt_hourly.text().strip() or 0)
            deposit = float(txt_dep.text().strip() or 0)
        except ValueError:
            show_warning(self, "输入错误", "价格和押金必须是数字。")
            return

        try:
            json.loads(txt_json.text().strip() or "{}")
        except json.JSONDecodeError:
            show_warning(self, "保存失败", "入住标配 JSON 格式不正确。")
            return

        new_type_id = txt_id.text().strip().upper()
        new_type_name = txt_name.text().strip()
        if not new_type_id or not new_type_name:
            show_warning(self, "输入错误", "房型ID和名称不能为空。")
            return

        icon = txt_icon.text().strip()
        consumables_json = txt_json.text().strip() or "{}"

        if is_new:
            db.execute(
                "INSERT OR REPLACE INTO room_type_templates "
                "(type_id, type_name, base_price, hourly_price, default_deposit, consumables_json, icon) "
                "VALUES (?,?,?,?,?,?,?)",
                (new_type_id, new_type_name, base_price, hourly_price, deposit, consumables_json, icon),
            )
        else:
            db.execute(
                "UPDATE room_type_templates SET type_name=?, base_price=?, hourly_price=?, "
                "default_deposit=?, consumables_json=?, icon=? WHERE type_id=?",
                (new_type_name, base_price, hourly_price, deposit, consumables_json, icon, type_id),
            )
            # 同步价格到房间
            if not is_new and 'sync_cb' in locals() and hasattr(sync_cb, 'isChecked') and sync_cb.isChecked():
                db.execute(
                    "UPDATE rooms SET rate_override=NULL WHERE "
                    "room_type=(SELECT type_name FROM room_type_templates WHERE type_id=?) "
                    "AND rate_override IS NULL",
                    (type_id,),
                )
                show_info(self, "价格同步", f"已同步标准价到使用此房型的房间。")

        # 同步消耗品标准
        self._sync_consumable_standards(new_type_id, consumables_json)

        show_info(self, "已保存", f"房型 {new_type_name} 已{'新增' if is_new else '更新'}。")
        self.refresh()

    def _pick_consumables_json(self, target: QLineEdit) -> None:
        """从库存消耗品选择入住标配（复用原始逻辑）。"""
        try:
            rows = db.execute(
                "SELECT item_id, name, COALESCE(unit,'件') FROM inventory_items "
                "WHERE category='consumable' ORDER BY name"
            ).fetchall()
        except Exception:
            rows = []
        if not rows:
            show_warning(self, "库存消耗品", "请先在库存物品里建立 category='consumable' 的消耗品。")
            return
        current = {}
        try:
            current = json.loads(target.text().strip() or "{}")
        except Exception:
            current = {}
        d = QDialog(self)
        d.setWindowTitle("入住标配消耗品")
        style_dialog(d, size="medium")
        lay = QVBoxLayout(d)
        lay.setSpacing(12)
        lay.addWidget(build_dialog_header("入住标配消耗品", "从库存消耗品字典选择名称和数量。"))
        tbl = QTableWidget(len(rows), 3)
        tbl.setHorizontalHeaderLabels(["启用", "物品", "数量"])
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.verticalHeader().setDefaultSectionSize(34)
        fd_apply_table_palette(tbl)
        for i, (item_id, name, unit) in enumerate(rows):
            chk = QTableWidgetItem("")
            chk.setFlags(chk.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if str(item_id) in current else Qt.CheckState.Unchecked)
            chk.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            tbl.setItem(i, 0, chk)
            name_item = QTableWidgetItem(f"{name} ({unit})")
            name_item.setData(Qt.ItemDataRole.UserRole, str(item_id))
            tbl.setItem(i, 1, name_item)
            spn = QSpinBox()
            spn.setRange(0, 999)
            spn.setValue(int(current.get(str(item_id), 1) or 1))
            spn.setStyleSheet(
                "QSpinBox { font-size: 14px; font-weight: 600; padding: 2px 4px;"
                " min-height: 26px; }"
            )
            tbl.setCellWidget(i, 2, spn)
            tbl.setRowHeight(i, 34)
        lay.addWidget(tbl, 1)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        ok = QPushButton("确定")
        ok.setObjectName("SolidPrimaryBtn")
        cancel = QPushButton("取消")
        cancel.setObjectName("FdGhostBtn")
        ok.clicked.connect(d.accept)
        cancel.clicked.connect(d.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)
        if d.exec() != QDialog.Accepted:
            return
        data = {}
        for i in range(tbl.rowCount()):
            if tbl.item(i, 0).checkState() == Qt.CheckState.Checked:
                item_id = tbl.item(i, 1).data(Qt.ItemDataRole.UserRole)
                qty = tbl.cellWidget(i, 2).value()
                if item_id and qty > 0:
                    data[str(item_id)] = qty
        target.setText(json.dumps(data, ensure_ascii=False))

    def _sync_consumable_standards(self, type_id: str, consumables_json: str) -> None:
        try:
            data = json.loads(consumables_json or "{}")
        except Exception:
            data = {}
        db.execute("DELETE FROM room_type_consumable_standards WHERE type_id=?", (type_id,))
        for item_id, qty in data.items():
            try:
                q = int(qty)
            except Exception:
                continue
            if q <= 0:
                continue
            db.execute(
                "INSERT OR REPLACE INTO room_type_consumable_standards (type_id, item_id, standard_qty, trigger_event) "
                "VALUES (?, ?, ?, 'CHECKIN')",
                (type_id, str(item_id), q),
            )

    def _delete_type(self, type_id: str) -> None:
        """删除房型，检查关联房间。"""
        if not ask_confirm(self, "确认删除", f"确定删除此房型？"):
            return
        row = db.execute(
            "SELECT type_name FROM room_type_templates WHERE type_id=?", (type_id,)
        ).fetchone()
        if not row:
            return
        type_name = row[0]
        count = db.execute(
            "SELECT COUNT(*) FROM rooms WHERE room_type=?", (type_name,)
        ).fetchone()[0]
        if count > 0:
            if not ask_confirm(self, "房型被引用",
                               f"有 {count} 间房使用「{type_name}」，删除后这些房间的房型字段不会自动变更。\n\n是否继续删除？"):
                return
        db.execute("DELETE FROM room_type_templates WHERE type_id=?", (type_id,))
        db.execute("DELETE FROM room_type_consumable_standards WHERE type_id=?", (type_id,))
        show_info(self, "已删除", f"房型 {type_name} 已删除。")
        self.refresh()

    # ════════════════════════════════════════════════════════════════
    # 数据加载 — 楼栋
    # ════════════════════════════════════════════════════════════════

    def refresh(self) -> None:
        """刷新所有数据：楼栋、导航树、房间列表。"""
        try:
            db.execute(
                "INSERT OR IGNORE INTO buildings (building_id, bld_no, name, sort_order) VALUES ('1', 1, '01', 1)"
            )
        except Exception:
            pass
        self._load_buildings()
        self._populate_nav_tree()
        self._rebuild_type_cards()
        self._load_rooms(self.txt_search.text().strip())

    def _load_buildings(self) -> None:
        self._buildings = db.execute(
            "SELECT b.building_id, b.bld_no, b.name, COUNT(r.room_id) "
            "FROM buildings b LEFT JOIN rooms r ON COALESCE(r.bld_no, 1)=b.bld_no "
            "GROUP BY b.building_id, b.bld_no, b.name ORDER BY b.sort_order, b.bld_no"
        ).fetchall()

    # ════════════════════════════════════════════════════════════════
    # 数据加载 — 房间
    # ════════════════════════════════════════════════════════════════

    def _load_rooms(self, filter_text: str) -> None:
        params = []
        where_clauses: list[str] = []

        if self._current_bld_no is not None:
            where_clauses.append("COALESCE(r.bld_no,1)=?")
            params.append(self._current_bld_no)

        if filter_text:
            where_clauses.append("r.room_id LIKE ?")
            params.append(f"%{filter_text}%")

        if self._filter_type:
            where_clauses.append("r.room_type=?")
            params.append(self._filter_type)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        self._rooms = db.execute(
            f"SELECT r.room_id, COALESCE(r.room_type,''), COALESCE(r.lock_no,''), "
            f"COALESCE(r.flr_no,0), COALESCE(r.status,''), COALESCE(r.note,''), "
            f"COALESCE(r.max_cards,4), COALESCE(r.dai,0), COALESCE(r.rate_override,NULL), "
            f"COALESCE(r.rom_id,0), COALESCE(r.deposit,0), COALESCE(r.extra_props,'{{}}') "
            f"FROM rooms r WHERE {where_sql} "
            f"ORDER BY r.flr_no, r.rom_id, r.room_id",
            tuple(params),
        ).fetchall()

        self.tbl_rooms.setRowCount(0)

        # 收集所有引用到的房型名称，获取它们的图标
        type_names = {r[1] for r in self._rooms if r[1]}
        type_icons = {}
        if type_names:
            try:
                rows = db.execute(
                    "SELECT type_name, COALESCE(icon,'') FROM room_type_templates WHERE type_name IN ({})".format(
                        ",".join("?" for _ in type_names)
                    ),
                    tuple(type_names),
                ).fetchall()
                type_icons = {r[0]: r[1] for r in rows}
            except Exception:
                pass

        for r in self._rooms:
            idx = self.tbl_rooms.rowCount()
            self.tbl_rooms.insertRow(idx)

            # 楼栋
            self.tbl_rooms.setItem(idx, 0, _item(str(self._current_bld_no)))

            # 楼层
            flr = int(r[3] or 0)
            self.tbl_rooms.setItem(idx, 1, _item(f"{flr}F" if flr else ""))

            # 房号
            room_id = str(r[0] or "")
            item_rid = QTableWidgetItem(room_id)
            item_rid.setFont(QFont("", 12, QFont.Weight.Bold))
            self.tbl_rooms.setItem(idx, 2, item_rid)

            # 房型（图标+名称）
            rt_name = str(r[1] or "")
            icon = type_icons.get(rt_name, "")
            rt_display = f"{icon} {rt_name}" if icon else rt_name
            self.tbl_rooms.setItem(idx, 3, _item(rt_display))

            # 状态（带颜色圆点）
            status_label, status_color = _status_display(str(r[4] or ""))
            status_item = QTableWidgetItem(f"● {status_label}")
            status_item.setForeground(QColor(status_color))
            self.tbl_rooms.setItem(idx, 4, status_item)

            # 价格（显示实际生效价格）
            rate = r[8]
            # 从标准价查询
            inherited_price = 0.0
            if rt_name:
                price_row = db.execute(
                    "SELECT base_price FROM room_type_templates WHERE type_name=? LIMIT 1",
                    (rt_name,),
                ).fetchone()
                if price_row:
                    inherited_price = float(price_row[0] or 0)
            actual_price = rate if rate is not None else inherited_price
            price_text = f"¥{actual_price:.0f}" if actual_price else "—"
            price_item = QTableWidgetItem(price_text)
            if rate is not None:
                price_item.setToolTip(f"覆盖价（标准价 ¥{inherited_price:.0f}）")
                price_item.setForeground(QColor(_p("primary")))
            self.tbl_rooms.setItem(idx, 5, price_item)

            # 备注
            self.tbl_rooms.setItem(idx, 6, _item(str(r[5] or "")[:20]))

            # 锁号（最后一列）
            self.tbl_rooms.setItem(idx, 7, _item(display_lock_no(str(r[2] or "")) or r[2] or ""))

        self.lbl_room_count.setText(f"{self.tbl_rooms.rowCount()} 间")

    def _filter_rooms(self, text: str) -> None:
        self._load_rooms(text.strip())

    # ════════════════════════════════════════════════════════════════
    # 房间编辑弹窗
    # ════════════════════════════════════════════════════════════════

    def select_room(self, room_id: str) -> None:
        """外部调用（如从房态右键跳转）直接跳转到指定房间。"""
        for r in range(self.tbl_rooms.rowCount()):
            it = self.tbl_rooms.item(r, 2)
            if it and it.text().strip() == room_id:
                self.tbl_rooms.selectRow(r)
                self.tbl_rooms.scrollToItem(it)
                self._edit_room_dialog(room_id)
                return
        # 没找到，尝试搜索
        self.txt_search.setText(room_id)
        QTimer.singleShot(200, lambda: self.select_room(room_id))

    def _on_room_double_clicked(self, row: int, _col: int) -> None:
        item = self.tbl_rooms.item(row, 2)
        if item:
            self._edit_room_dialog(item.text().strip())

    def _edit_selected_room(self) -> None:
        row = self.tbl_rooms.currentRow()
        if row < 0:
            show_warning(self, "未选择", "请先在房间列表选中一个房间。")
            return
        item = self.tbl_rooms.item(row, 2)
        if item:
            self._edit_room_dialog(item.text().strip())

    def _edit_room_dialog(self, room_id: str) -> None:
        """弹窗编辑单个房间（复用原始逻辑但整合在当前页面）。"""
        row = db.execute(
            "SELECT room_id, COALESCE(flr_no,0), COALESCE(room_type,''), COALESCE(status,''), "
            "COALESCE(lock_no,''), COALESCE(note,''), COALESCE(max_cards,4), COALESCE(dai,0), "
            "COALESCE(rate_override,NULL), COALESCE(rom_id,0), COALESCE(deposit,0), "
            "COALESCE(extra_props,'{}') "
            "FROM rooms WHERE room_id=?",
            (room_id,),
        ).fetchone()
        if not row:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("编辑房间")
        style_dialog(dlg, size="large")
        root_lay = QVBoxLayout(dlg)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        root_lay.addWidget(build_dialog_header("编辑房间", f"房间 {room_id}"))

        tabs = QTabWidget()
        tabs.setContentsMargins(16, 12, 16, 12)

        # ── Tab 1: 基础信息 ──
        tab1 = QWidget()
        lay1 = QFormLayout(tab1)
        lay1.setSpacing(12)

        edit_room_id = QLineEdit()
        edit_room_id.setPlaceholderText("门牌号码")
        edit_room_id.setText(str(row[0] or ""))
        lay1.addRow("房号：", edit_room_id)

        edit_flr = QLineEdit()
        edit_flr.setPlaceholderText("楼层编号")
        edit_flr.setText(str(row[1] or 0))
        lay1.addRow("楼层：", edit_flr)

        cmb_type = QComboBox()
        types = _room_types_data()
        for td in types:
            icon = td.get("icon", "")
            display = f"{icon} {td['type_name']}" if icon else td['type_name']
            cmb_type.addItem(display, td["type_name"])
        if not types:
            cmb_type.addItems(["标准间", "豪华间", "套房"])
        idx = cmb_type.findData(str(row[2] or ""))
        if idx >= 0:
            cmb_type.setCurrentIndex(idx)
        lay1.addRow("房型：", cmb_type)

        edit_lock = QLineEdit()
        edit_lock.setPlaceholderText("8 位 hex，例如 80010301")
        edit_lock.setMaxLength(8)
        edit_lock.setText(display_lock_no(row[4]) or row[4] or "")
        lay1.addRow("锁号：", edit_lock)

        edit_dai = QLineEdit()
        edit_dai.setText(str(row[7] or 0))
        lay1.addRow("Dai：", edit_dai)

        edit_max_cards = QLineEdit()
        edit_max_cards.setText(str(row[6] or 4))
        lay1.addRow("最大发卡：", edit_max_cards)

        cmb_status = QComboBox()
        for key, label in ROOM_STATUS_LEGACY.items():
            cmb_status.addItem(label, key)
        status_idx = cmb_status.findData(str(row[3] or ""))
        if status_idx >= 0:
            cmb_status.setCurrentIndex(status_idx)
        lay1.addRow("状态：", cmb_status)

        edit_note = QLineEdit()
        edit_note.setText(str(row[5] or ""))
        lay1.addRow("备注：", edit_note)

        tabs.addTab(tab1, "基础信息")

        # ── Tab 2: 价格覆盖 ──
        tab2 = QWidget()
        lay2 = QFormLayout(tab2)
        lay2.setSpacing(12)

        rt_name = str(row[2] or "")
        prices = db.execute(
            "SELECT base_price, default_deposit FROM room_type_templates WHERE type_name=? LIMIT 1",
            (rt_name,),
        ).fetchone()
        inherited_price = prices[0] if prices else 0
        inherited_dep = prices[1] if prices else 0

        lbl_inherited_price = QLabel(f"¥{inherited_price:.2f}")
        lbl_inherited_price.setStyleSheet(f"color:{_p('text_muted')};")
        lay2.addRow("房型标准价：", lbl_inherited_price)

        edit_rate_override = QLineEdit()
        edit_rate_override.setPlaceholderText("留空 = 继承房型价格")
        rate = row[8]
        edit_rate_override.setText(f"{rate:.2f}" if rate is not None else "")
        lay2.addRow("覆盖价格（元）：", edit_rate_override)

        lbl_inherited_deposit = QLabel(f"¥{inherited_dep:.2f}")
        lbl_inherited_deposit.setStyleSheet(f"color:{_p('text_muted')};")
        lay2.addRow("房型默认押金：", lbl_inherited_deposit)

        edit_deposit = QLineEdit()
        edit_deposit.setPlaceholderText("留空 = 继承房型押金")
        edit_deposit.setText(str(row[10] or ""))
        lay2.addRow("覆盖押金（元）：", edit_deposit)

        tabs.addTab(tab2, "价格覆盖")

        # ── Tab 3: 扩展属性 ──
        tab3 = QWidget()
        lay3 = QVBoxLayout(tab3)
        lay3.setSpacing(8)

        prop_defs = _prop_definitions()
        prop_form_widget = QWidget()
        prop_form_lay = QFormLayout(prop_form_widget)
        prop_form_lay.setSpacing(10)
        prop_widgets: dict[str, QWidget] = {}

        for pd in prop_defs:
            key = pd["key"]
            label = pd["label"]
            ftype = pd["field_type"]
            options = pd.get("options", [])

            if ftype == "checkbox":
                w = QCheckBox(label)
                prop_form_lay.addRow("", w)
            elif ftype == "select":
                w = QComboBox()
                for opt in options:
                    w.addItem(opt)
                w.setEditable(True)
                prop_form_lay.addRow(f"{label}：", w)
            elif ftype == "number":
                w = QLineEdit()
                w.setPlaceholderText("数字")
                prop_form_lay.addRow(f"{label}：", w)
            else:
                w = QLineEdit()
                w.setPlaceholderText(label)
                prop_form_lay.addRow(f"{label}：", w)
            prop_widgets[key] = w

        try:
            extra = json.loads(row[11] or "{}")
        except Exception:
            extra = {}
        for key, w in prop_widgets.items():
            val = extra.get(key, "")
            if isinstance(w, QCheckBox):
                w.setChecked(str(val).lower() in ("1", "true", "yes"))
            elif isinstance(w, QComboBox):
                idx = w.findText(str(val))
                if idx >= 0:
                    w.setCurrentIndex(idx)
                elif val:
                    w.setEditText(str(val))
            else:
                w.setText(str(val) if val else "")

        scroll = QScrollArea()
        scroll.setWidget(prop_form_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        lay3.addWidget(scroll, 1)

        tabs.addTab(tab3, "扩展属性")

        root_lay.addWidget(tabs, 1)

        # ── 底部按钮 ──
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 0, 16, 16)
        btn_save = QPushButton("保存")
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("SecondaryBtn")
        btn_save.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)
        root_lay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # ── 保存逻辑 ──
        extra_save: dict[str, Any] = {}
        for key, w in prop_widgets.items():
            if isinstance(w, QCheckBox):
                extra_save[key] = "1" if w.isChecked() else "0"
            elif isinstance(w, QComboBox):
                extra_save[key] = w.currentText()
            else:
                extra_save[key] = w.text().strip()

        # 获取实际房型名称（comboBox 的 userData 存的是纯名称）
        actual_type_name = cmb_type.currentData() or cmb_type.currentText()
        # 如果 userData 为空，从显示文字提取
        if not cmb_type.currentData():
            display_text = cmb_type.currentText()
            # 去掉可能的前缀 emoji
            parts = display_text.split(" ", 1)
            actual_type_name = parts[-1] if len(parts) > 1 else display_text

        try:
            flr = int(edit_flr.text() or 0)
            dai = int(float(edit_dai.text() or 0))
            max_cards = int(float(edit_max_cards.text() or 4))
            rate_override = float(edit_rate_override.text()) if edit_rate_override.text().strip() else None
            deposit = float(edit_deposit.text()) if edit_deposit.text().strip() else None
        except ValueError:
            show_warning(dlg, "输入错误", "楼层、Dai、发卡数量、价格必须是数字。")
            return

        lock_no = normalize_lock_no_hex(edit_lock.text()) or ""
        new_room_id = edit_room_id.text().strip()
        if not new_room_id:
            show_warning(dlg, "输入错误", "房号不能为空。")
            return

        db.execute(
            "UPDATE rooms SET room_id=?, floor=?, room_type=?, status=?, lock_no=?, note=?, "
            "flr_no=?, max_cards=?, dai=?, rate_override=?, deposit=?, extra_props=? WHERE room_id=?",
            (
                new_room_id, str(flr), actual_type_name,
                cmb_status.currentData(), lock_no, edit_note.text().strip(),
                flr, max_cards, dai, rate_override, deposit,
                json.dumps(extra_save, ensure_ascii=False),
                room_id,
            ),
        )

        bus.room_status_changed.emit(new_room_id, cmb_status.currentData())
        show_info(self, "保存成功", f"房间 {new_room_id} 已更新。")
        self.refresh()

    # ════════════════════════════════════════════════════════════════
    # 楼栋管理
    # ════════════════════════════════════════════════════════════════

    def _building_dialog(self, preset: tuple | None = None) -> tuple[int, str] | None:
        d = QDialog(self)
        d.setWindowTitle("楼栋资料")
        style_dialog(d, size="compact")
        lay = QVBoxLayout(d)
        lay.addWidget(build_dialog_header("楼栋资料", "编辑楼栋编号与名称"))
        form = QFormLayout()
        spn = QSpinBox()
        spn.setRange(1, 255)
        spn.setValue(int(preset[1] if preset else self._current_bld_no))
        txt = QLineEdit(str(preset[2] if preset else f"{spn.value():02d}"))
        form.addRow("楼栋编号", spn)
        form.addRow("楼栋名称", txt)
        lay.addLayout(form)
        row = QHBoxLayout()
        ok = QPushButton("确定")
        ok.setObjectName("SolidPrimaryBtn")
        cancel = QPushButton("取消")
        cancel.setObjectName("FdGhostBtn")
        ok.clicked.connect(d.accept)
        cancel.clicked.connect(d.reject)
        row.addStretch()
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)
        if d.exec() != QDialog.DialogCode.Accepted:
            return None
        return spn.value(), txt.text().strip() or f"{spn.value():02d}"

    def _add_building(self) -> None:
        data = self._building_dialog()
        if not data:
            return
        bld_no, name = data
        db.execute(
            "INSERT OR REPLACE INTO buildings (building_id, bld_no, name, sort_order) VALUES (?,?,?,?)",
            (str(bld_no), bld_no, name, bld_no),
        )
        self._current_bld_no = bld_no
        self.refresh()

    def _edit_building(self) -> None:
        if not self._buildings:
            return
        # 弹窗选择要编辑的楼栋
        items = [f"{b[2]} (楼栋{b[1]}, {b[3]}间)" for b in self._buildings]
        from PySide6.QtWidgets import QInputDialog
        sel, ok = QInputDialog.getItem(self, "选择楼栋", "选择要编辑的楼栋：", items, 0, False)
        if not ok or not sel:
            return
        idx = items.index(sel)
        preset = self._buildings[idx]
        data = self._building_dialog(preset)
        if not data:
            return
        bld_no, name = data
        db.execute("UPDATE buildings SET bld_no=?, name=? WHERE building_id=?", (bld_no, name, preset[0]))
        db.execute("UPDATE rooms SET bld_no=?, building=? WHERE COALESCE(bld_no,1)=?", (bld_no, name, preset[1]))
        self._current_bld_no = bld_no
        self.refresh()

    def _delete_building(self) -> None:
        if self._current_bld_no is None:
            show_warning(self, "请选择", "请先在左侧导航树选择一个楼栋。")
            return
        db.execute("DELETE FROM rooms WHERE COALESCE(bld_no,1)=?", (self._current_bld_no,))
        db.execute("DELETE FROM buildings WHERE bld_no=?", (self._current_bld_no,))
        self._current_bld_no = 1
        bus.room_status_changed.emit("__batch__", "READY")
        self.refresh()

    # ════════════════════════════════════════════════════════════════
    # 房间批量操作
    # ════════════════════════════════════════════════════════════════

    def _add_rooms(self) -> None:
        d = QDialog(self)
        d.setWindowTitle("批量添加房间")
        style_dialog(d, size="medium")
        lay = QVBoxLayout(d)
        lay.addWidget(build_dialog_header("批量添加房间", "输入层号与房号范围"))
        form = QFormLayout()
        txt_flrs = QLineEdit("3")
        txt_rooms = QLineEdit("801-813")
        cmb_type = QComboBox()
        types = _room_types_data()
        for td in types:
            cmb_type.addItem(td["type_name"])
        if not types:
            cmb_type.addItems(["标准间", "豪华间", "套房"])
        txt_price = QLineEdit("")
        txt_skip = QLineEdit("")
        form.addRow("层号（或范围）", txt_flrs)
        form.addRow("房号（或范围）", txt_rooms)
        form.addRow("房间类型", cmb_type)
        form.addRow("覆盖价格（空=房型价）", txt_price)
        form.addRow("跳过房号（逗号分隔）", txt_skip)
        lay.addLayout(form)

        # 扩展属性预设
        prop_defs = _prop_definitions()
        if prop_defs:
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet(f"background:{_p('border')};max-height:1px;margin:4px 0;")
            lay.addWidget(sep)
            prop_lbl = QLabel("扩展属性预设（新建房间统一填入）")
            prop_lbl.setStyleSheet(f"font-weight:600; font-size:12px; color:{_p('primary')};")
            lay.addWidget(prop_lbl)
            props_wrap = QWidget()
            props_form = QFormLayout(props_wrap)
            props_form.setSpacing(6)
            self._batch_prop_widgets: dict[str, QWidget] = {}
            for pd in prop_defs:
                key = pd["key"]
                label = pd["label"]
                ftype = pd["field_type"]
                options = pd.get("options", [])
                if ftype == "checkbox":
                    w = QCheckBox(label)
                    props_form.addRow("", w)
                elif ftype == "select":
                    w = QComboBox()
                    for opt in options:
                        w.addItem(opt)
                    w.setEditable(True)
                    props_form.addRow(f"{label}：", w)
                elif ftype == "number":
                    w = QLineEdit()
                    w.setPlaceholderText("数字")
                    props_form.addRow(f"{label}：", w)
                else:
                    w = QLineEdit()
                    w.setPlaceholderText(label)
                    props_form.addRow(f"{label}：", w)
                self._batch_prop_widgets[key] = w
            scroll = QScrollArea()
            scroll.setWidget(props_wrap)
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(160)
            scroll.setFrameShape(QFrame.NoFrame)
            lay.addWidget(scroll)

        row = QHBoxLayout()
        ok = QPushButton("确定")
        ok.setObjectName("SolidPrimaryBtn")
        cancel = QPushButton("取消")
        cancel.setObjectName("FdGhostBtn")
        ok.clicked.connect(d.accept)
        cancel.clicked.connect(d.reject)
        row.addStretch()
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            flrs = _range_values(txt_flrs.text())
            rooms = _range_values(txt_rooms.text())
            skip = {str(v) for v in _range_values(txt_skip.text())} if txt_skip.text().strip() else set()
            rate = float(txt_price.text()) if txt_price.text().strip() else None
        except Exception:
            show_warning(self, "格式错误", "范围请使用 3 或 3-5 这种格式。")
            return

        batch_extra = {}
        if hasattr(self, "_batch_prop_widgets"):
            for key, w in self._batch_prop_widgets.items():
                if isinstance(w, QCheckBox):
                    batch_extra[key] = "1" if w.isChecked() else "0"
                elif isinstance(w, QComboBox):
                    val = w.currentText().strip()
                    if val:
                        batch_extra[key] = val
                else:
                    val = w.text().strip()
                    if val:
                        batch_extra[key] = val

        count = 0
        for flr in flrs:
            for idx, room_no in enumerate(rooms, start=1):
                rid = str(room_no)
                if rid in skip:
                    continue
                rom_id = idx
                lock_no = lock_no_from_parts(self._current_bld_no, flr, rom_id)
                try:
                    db.execute(
                        "INSERT INTO rooms (room_id, floor, room_type, status, building, lock_no, bld_no, flr_no, rom_id, max_cards, dai, rate_override, extra_props) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, str(flr), cmb_type.currentText(), "VC", str(self._current_bld_no), lock_no,
                         self._current_bld_no, flr, rom_id, 100, 0, rate, json.dumps(batch_extra)),
                    )
                    count += 1
                except Exception:
                    pass
        show_info(self, "添加房间", f"已添加 {count} 间房。")
        bus.room_status_changed.emit("__batch__", "READY")
        self.refresh()

    def _import_rooms(self) -> None:
        try:
            from batch_create_dialog import ImportRoomsFromMdbDialog
            dlg = ImportRoomsFromMdbDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.refresh()
        except Exception as exc:
            show_warning(self, "从老系统导入房间", str(exc))

    def _delete_room(self) -> None:
        row = self.tbl_rooms.currentRow()
        if row < 0:
            show_warning(self, "未选择", "请先在房间列表选中要删除的房间。")
            return
        item = self.tbl_rooms.item(row, 0)
        if not item:
            return
        rid = item.text().strip()
        if not ask_confirm(self, "删除房间", f"是否确定删除房间 {rid}？"):
            return
        db.execute("DELETE FROM rooms WHERE room_id=?", (rid,))
        bus.room_status_changed.emit(rid, "DELETED")
        self.refresh()

    # ════════════════════════════════════════════════════════════════
    # 批量操作
    # ════════════════════════════════════════════════════════════════

    def _batch_dialog(self) -> None:
        """弹窗模式的批量操作面板。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("批量操作")
        style_dialog(dlg, size="medium")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)
        lay.addWidget(build_dialog_header("批量操作", "对当前楼栋的所有房间批量改房型、设锁号、覆盖价格。"))

        form = QFormLayout()
        form.setSpacing(8)

        # 批量改房型
        batch_type_cmb = QComboBox()
        batch_type_cmb.setMinimumWidth(140)
        types = _room_types_data()
        for td in types:
            batch_type_cmb.addItem(td["type_name"])
        form.addRow("改房型：", batch_type_cmb)

        # 批量设锁号前缀
        batch_lock_prefix = QLineEdit()
        batch_lock_prefix.setPlaceholderText("例如 8001，自动+递增")
        form.addRow("锁号前缀：", batch_lock_prefix)

        # 批量覆盖价格
        batch_price = QLineEdit()
        batch_price.setPlaceholderText("留空不清除")
        form.addRow("覆盖价格：", batch_price)

        lay.addLayout(form)

        current_bld = self._current_bld_no or 1
        bld_label = QLabel(f"操作范围：楼栋 {current_bld}")
        bld_label.setObjectName("FdMutedLabel")
        lay.addWidget(bld_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_apply_all = QPushButton("全部执行")
        btn_apply_all.setObjectName("SolidPrimaryBtn")

        def _do_batch():
            rt = batch_type_cmb.currentText()
            if rt:
                count = db.execute(
                    "UPDATE rooms SET room_type=? WHERE COALESCE(bld_no,1)=?",
                    (rt, current_bld),
                ).rowcount
                show_info(dlg, "", f"已更新 {count} 间房为 {rt}。")

            prefix = batch_lock_prefix.text().strip()
            if prefix and len(prefix) >= 2:
                rooms = db.execute(
                    "SELECT room_id, rom_id, flr_no FROM rooms WHERE COALESCE(bld_no,1)=? ORDER BY flr_no, rom_id",
                    (current_bld,),
                ).fetchall()
                cnt = 0
                for r in rooms:
                    rom = int(r[1] or 0)
                    flr = int(r[2] or 0)
                    lock_no = f"{prefix}{flr:02d}{rom:02d}"
                    if len(lock_no) == 8:
                        db.execute("UPDATE rooms SET lock_no=? WHERE room_id=?", (lock_no, r[0]))
                        cnt += 1
                show_info(dlg, "", f"已更新 {cnt} 间房锁号。")

            price_text = batch_price.text().strip()
            if price_text:
                try:
                    p = float(price_text)
                    cnt = db.execute(
                        "UPDATE rooms SET rate_override=? WHERE COALESCE(bld_no,1)=?",
                        (p, current_bld),
                    ).rowcount
                    show_info(dlg, "", f"已更新 {cnt} 间房价格为 ¥{p:.2f}。")
                except ValueError:
                    show_warning(dlg, "输入错误", "价格必须是数字。")

            dlg.accept()
            bus.room_status_changed.emit("__batch__", "READY")
            self.refresh()

        btn_apply_all.clicked.connect(_do_batch)
        btn_row.addWidget(btn_apply_all)

        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_cancel)

        btn_row.addStretch()
        lay.addLayout(btn_row)

        dlg.exec()

    def _batch_set_type(self) -> None:
        rt = self.batch_type.currentText()
        if not rt:
            return
        count = db.execute(
            "UPDATE rooms SET room_type=? WHERE COALESCE(bld_no,1)=?",
            (rt, self._current_bld_no),
        ).rowcount
        show_info(self, "批量改房型", f"已更新 {count} 间房为 {rt}。")
        bus.room_status_changed.emit("__batch__", "READY")
        self.refresh()

    def _batch_set_lock(self) -> None:
        prefix = self.batch_lock_prefix.text().strip()
        if not prefix or len(prefix) < 2:
            show_warning(self, "输入错误", "锁号前缀至少 2 位。")
            return
        rooms = db.execute(
            "SELECT room_id, rom_id, flr_no FROM rooms WHERE COALESCE(bld_no,1)=? ORDER BY flr_no, rom_id",
            (self._current_bld_no,),
        ).fetchall()
        count = 0
        for r in rooms:
            rom = int(r[1] or 0)
            flr = int(r[2] or 0)
            lock_no = f"{prefix}{flr:02d}{rom:02d}"
            if len(lock_no) == 8:
                db.execute("UPDATE rooms SET lock_no=? WHERE room_id=?", (lock_no, r[0]))
                count += 1
        show_info(self, "批量设锁号", f"已更新 {count} 间房锁号。")
        self.refresh()

    def _batch_set_price(self) -> None:
        price_text = self.batch_price.text().strip()
        if not price_text:
            show_warning(self, "输入错误", "请输入价格。")
            return
        try:
            price = float(price_text)
        except ValueError:
            show_warning(self, "输入错误", "价格必须是数字。")
            return
        count = db.execute(
            "UPDATE rooms SET rate_override=? WHERE COALESCE(bld_no,1)=?",
            (price, self._current_bld_no),
        ).rowcount
        show_info(self, "批量改价格", f"已更新 {count} 间房价格为 ¥{price:.2f}。")
        self.refresh()


