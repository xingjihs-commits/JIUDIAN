"""
======================================================
ShadowGuard — 时间轴视图 (v2.0)
完整重写：支持拖拽调整、预订管理、冲突检测、颜色状态区分

功能：
  - 未来14天房态甘特图
  - 颜色区分：在住/预订/空闲/维修
  - 点击房间格子 → 快速入住/预订
  - 拖拽调整退房时间（右边界拖拽）
  - 房间冲突检测（重叠预订高亮警告）
  - 预订管理（新增/修改/取消预订）
======================================================
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, date
from typing import Optional

from PySide6.QtCore import (
    Qt, QDate, QRect, QPoint, QSize, QTimer,
    Signal as QtSignal, QThread
)
from PySide6.QtGui import (
    QColor, QBrush, QPainter, QPen, QFont,
    QFontMetrics, QCursor, QMouseEvent, QPaintEvent
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QDialog, QFormLayout, QLineEdit, QDateTimeEdit,
    QComboBox, QFrame, QSizePolicy, QToolTip,
    QApplication
)
from PySide6.QtCore import QDateTime

from database import db
from design_tokens import _p, active_room_status_theme
from permission_system import PermissionManager
from ui_helpers import show_info, show_warning, show_error, ask_confirm
from frontdesk_ui import fd_apply_action_btn


def _current_actor_id() -> str:
    u = PermissionManager.current_user()
    if u:
        return str(u.get("username") or u.get("id") or "unknown")
    return PermissionManager.current_role() or "guest"

# ─────────────────────────────────────────────
#  颜色常量（跟随当前主题）
# ─────────────────────────────────────────────
def _rs_qcolor(semantic_key: str, *, fallback: str = "#7B8C9E") -> QColor:
    entry = active_room_status_theme().get(semantic_key, {})
    return QColor(entry.get("border") or entry.get("color") or fallback)


def _build_timeline_colors():
    from design_tokens import _p
    bg = _p("bg")
    surface = _p("surface")
    surface_alt = _p("bg")
    primary = _p("primary")
    danger = _p("danger")
    warn = _p("warn")
    text_muted = _p("text_muted")
    rs = active_room_status_theme()
    ready_bg = (rs.get("READY") or {}).get("soft", surface_alt)
    return {
        "COLOR_INHOUSE": _rs_qcolor("INHOUSE"),
        "COLOR_RESERVED": _rs_qcolor("INHOUSE", fallback="#7B8C9E"),
        "COLOR_DIRTY": _rs_qcolor("DIRTY"),
        "COLOR_READY": QColor(ready_bg),
        "COLOR_MAINTENANCE": _rs_qcolor("MAINTENANCE"),
        "COLOR_OVERTIME": _rs_qcolor("OVERTIME"),
        "COLOR_TODAY": QColor(warn),
        "COLOR_WEEKEND": QColor(text_muted),
        "COLOR_TODAY_LINE": QColor(danger),
        "COLOR_GRID": QColor(surface_alt),
        "COLOR_HEADER_BG": QColor(primary),
        "COLOR_HEADER_FG": QColor(surface),
        "COLOR_ROW_ODD": QColor(surface),
        "COLOR_ROW_EVEN": QColor(bg),
        "COLOR_HOVER": QColor(surface_alt),
        "COLOR_CONFLICT": QColor(danger),
    }


_TIMELINE_COLORS = _build_timeline_colors()

COLOR_INHOUSE     = _TIMELINE_COLORS["COLOR_INHOUSE"]
COLOR_RESERVED    = _TIMELINE_COLORS["COLOR_RESERVED"]
COLOR_DIRTY       = _TIMELINE_COLORS["COLOR_DIRTY"]
COLOR_READY       = _TIMELINE_COLORS["COLOR_READY"]
COLOR_MAINTENANCE = _TIMELINE_COLORS["COLOR_MAINTENANCE"]
COLOR_OVERTIME   = _TIMELINE_COLORS["COLOR_OVERTIME"]
COLOR_TODAY_LINE  = _TIMELINE_COLORS["COLOR_TODAY_LINE"]
COLOR_GRID        = _TIMELINE_COLORS["COLOR_GRID"]
COLOR_HEADER_BG   = _TIMELINE_COLORS["COLOR_HEADER_BG"]
COLOR_HEADER_FG   = _TIMELINE_COLORS["COLOR_HEADER_FG"]
COLOR_ROW_ODD     = _TIMELINE_COLORS["COLOR_ROW_ODD"]
COLOR_ROW_EVEN    = _TIMELINE_COLORS["COLOR_ROW_EVEN"]
COLOR_HOVER       = _TIMELINE_COLORS["COLOR_HOVER"]
COLOR_CONFLICT    = _TIMELINE_COLORS["COLOR_CONFLICT"]

# ─────────────────────────────────────────────
#  预订数据结构
# ─────────────────────────────────────────────

class Reservation:
    """预订/在住记录（用于时间轴渲染）"""
    __slots__ = ("res_id", "room_id", "guest_name", "checkin", "checkout",
                 "status", "phone", "note", "is_conflict")

    def __init__(self, res_id, room_id, guest_name, checkin, checkout,
                 status="RESERVED", phone="", note=""):
        self.res_id = res_id
        self.room_id = room_id
        self.guest_name = guest_name
        self.checkin: datetime = checkin
        self.checkout: datetime = checkout
        self.status = status   # INHOUSE / RESERVED / DIRTY / MAINTENANCE
        self.phone = phone
        self.note = note
        self.is_conflict = False


# ─────────────────────────────────────────────
#  预订对话框
# ─────────────────────────────────────────────

class ReservationDialog(QDialog):
    """新增/编辑预订对话框"""

    def __init__(self, parent=None, room_id: str = "",
                 checkin_dt: Optional[datetime] = None,
                 checkout_dt: Optional[datetime] = None,
                 reservation: Optional[Reservation] = None):
        super().__init__(parent)
        self.reservation = reservation
        is_edit = reservation is not None

        self.setWindowTitle("编辑预订" if is_edit else "新增预订")
        from ui_helpers import style_dialog
        style_dialog(self, size="medium")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        from ui_helpers import build_dialog_header
        layout.addWidget(build_dialog_header(
            "编辑预订" if is_edit else "新增预订",
            "登记或修改预订信息，预订不占用实时房态。"
        ))

        form = QFormLayout()
        form.setSpacing(10)

        self.room_edit = QLineEdit(
            reservation.room_id if is_edit else room_id
        )
        self.room_edit.setPlaceholderText("如：101")
        form.addRow("房间号：", self.room_edit)

        self.guest_edit = QLineEdit(
            reservation.guest_name if is_edit else ""
        )
        self.guest_edit.setPlaceholderText("住客姓名")
        form.addRow("住客姓名：", self.guest_edit)

        self.phone_edit = QLineEdit(
            reservation.phone if is_edit else ""
        )
        self.phone_edit.setPlaceholderText("联系电话（选填）")
        form.addRow("联系电话：", self.phone_edit)

        # 入住时间
        self.checkin_edit = QDateTimeEdit()
        self.checkin_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.checkin_edit.setCalendarPopup(True)
        if is_edit:
            self.checkin_edit.setDateTime(
                QDateTime(reservation.checkin.date(),
                          reservation.checkin.time() if hasattr(reservation.checkin, 'time') else
                          QDateTime.currentDateTime().time())
            )
        elif checkin_dt:
            self.checkin_edit.setDateTime(QDateTime(
                checkin_dt.date(),
                checkin_dt.time() if hasattr(checkin_dt, 'time') else
                QDateTime.currentDateTime().time()
            ))
        else:
            self.checkin_edit.setDateTime(QDateTime.currentDateTime())
        form.addRow("入住时间：", self.checkin_edit)

        # 退房时间
        self.checkout_edit = QDateTimeEdit()
        self.checkout_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.checkout_edit.setCalendarPopup(True)
        if is_edit:
            self.checkout_edit.setDateTime(
                QDateTime(reservation.checkout.date(),
                          reservation.checkout.time() if hasattr(reservation.checkout, 'time') else
                          QDateTime.currentDateTime().time())
            )
        elif checkout_dt:
            self.checkout_edit.setDateTime(QDateTime(
                checkout_dt.date(),
                checkout_dt.time() if hasattr(checkout_dt, 'time') else
                QDateTime.currentDateTime().time()
            ))
        else:
            self.checkout_edit.setDateTime(QDateTime.currentDateTime().addDays(1))
        form.addRow("退房时间：", self.checkout_edit)

        self.note_edit = QLineEdit(
            reservation.note if is_edit else ""
        )
        self.note_edit.setPlaceholderText("备注（选填）")
        form.addRow("备注：", self.note_edit)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(self.reject)

        if is_edit:
            btn_del = QPushButton("删除预订")
            btn_del.setObjectName("FdDangerBtn")
            fd_apply_action_btn(btn_del, danger=True)
            btn_del.clicked.connect(self._delete)
            btn_row.addWidget(btn_del)

        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("保存")
        btn_save.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_save, primary=True)
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

        self._deleted = False

    def _save(self):
        room_id = self.room_edit.text().strip()
        guest_name = self.guest_edit.text().strip()
        if not room_id or not guest_name:
            show_warning(self, "请填写房间号和住客姓名")
            return
        checkin_dt = self.checkin_edit.dateTime().toPython()
        checkout_dt = self.checkout_edit.dateTime().toPython()
        if checkout_dt <= checkin_dt:
            show_warning(self, "退房时间必须晚于入住时间")
            return
        self.result_data = {
            "room_id": room_id,
            "guest_name": guest_name,
            "phone": self.phone_edit.text().strip(),
            "checkin": checkin_dt,
            "checkout": checkout_dt,
            "note": self.note_edit.text().strip(),
        }
        self.accept()

    def _delete(self):
        if ask_confirm(
            self, "确认删除", "确定要删除这条预订记录吗？",
        ):
            self._deleted = True
            self.accept()


# ─────────────────────────────────────────────
#  甘特图画布（核心渲染组件）
# ─────────────────────────────────────────────

class GanttCanvas(QWidget):
    """
    时间轴甘特图画布
    - 左侧：房间列表（固定宽度）
    - 右侧：时间格子（可横向滚动）
    - 支持点击空格子 → 新增预订
    - 支持点击已有条目 → 编辑/删除
    - 支持拖拽右边界 → 调整退房时间
    """

    reservation_clicked = QtSignal(object)   # Reservation
    empty_cell_clicked  = QtSignal(str, object)  # room_id, date

    # 布局常量
    ROW_H    = 36    # 行高
    ROOM_W   = 80    # 房间列宽
    DAY_W    = 60    # 每天列宽
    HEADER_H = 40    # 表头高度
    DAYS     = 14    # 显示天数

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._rooms: list[str] = []
        self._reservations: list[Reservation] = []
        self._start_date: date = date.today()
        self._hover_cell: Optional[tuple] = None   # (row, day_idx)
        self._drag_res: Optional[Reservation] = None
        self._drag_start_x: int = 0
        self._drag_orig_checkout: Optional[datetime] = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, rooms: list[str], reservations: list[Reservation],
                 start_date: date):
        self._rooms = rooms
        self._reservations = reservations
        self._start_date = start_date
        self._detect_conflicts()
        total_w = self.ROOM_W + self.DAYS * self.DAY_W
        total_h = self.HEADER_H + len(rooms) * self.ROW_H
        self.setMinimumSize(total_w, max(total_h, 300))
        self.update()

    def _detect_conflicts(self):
        """检测同一房间的时间重叠"""
        for r in self._reservations:
            r.is_conflict = False
        for i, r1 in enumerate(self._reservations):
            for r2 in self._reservations[i+1:]:
                if r1.room_id != r2.room_id:
                    continue
                if r1.checkin < r2.checkout and r2.checkin < r1.checkout:
                    r1.is_conflict = True
                    r2.is_conflict = True

    # ── 坐标转换 ──────────────────────────────
    def _day_to_x(self, d: date) -> int:
        delta = (d - self._start_date).days
        return self.ROOM_W + delta * self.DAY_W

    def _x_to_day(self, x: int) -> Optional[date]:
        if x < self.ROOM_W:
            return None
        day_idx = (x - self.ROOM_W) // self.DAY_W
        if 0 <= day_idx < self.DAYS:
            return self._start_date + timedelta(days=day_idx)
        return None

    def _row_to_y(self, row: int) -> int:
        return self.HEADER_H + row * self.ROW_H

    def _y_to_row(self, y: int) -> int:
        return (y - self.HEADER_H) // self.ROW_H

    # ── 绘制 ──────────────────────────────────
    def paintEvent(self, event: QPaintEvent):
        palette = _build_timeline_colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._draw_background(p, palette)
        self._draw_header(p, palette)
        self._draw_grid(p, palette)
        self._draw_today_line(p, palette)
        self._draw_reservations(p, palette)
        self._draw_room_labels(p)
        p.end()

    def _draw_background(self, p: QPainter, palette: dict):
        for i, _ in enumerate(self._rooms):
            y = self._row_to_y(i)
            color = palette["COLOR_ROW_ODD"] if i % 2 == 0 else palette["COLOR_ROW_EVEN"]
            p.fillRect(0, y, self.width(), self.ROW_H, color)

        # 悬停高亮
        if self._hover_cell:
            row, day_idx = self._hover_cell
            if 0 <= row < len(self._rooms):
                x = self.ROOM_W + day_idx * self.DAY_W
                y = self._row_to_y(row)
                p.fillRect(x, y, self.DAY_W, self.ROW_H, palette["COLOR_HOVER"])

    def _draw_header(self, p: QPainter, palette: dict):
        # 表头背景
        p.fillRect(0, 0, self.width(), self.HEADER_H, palette["COLOR_HEADER_BG"])

        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        p.setFont(font)
        p.setPen(palette["COLOR_HEADER_FG"])

        # 房间列标题
        p.drawText(QRect(0, 0, self.ROOM_W, self.HEADER_H),
                   Qt.AlignCenter, "房间")

        # 日期列标题
        today = date.today()
        for i in range(self.DAYS):
            d = self._start_date + timedelta(days=i)
            x = self.ROOM_W + i * self.DAY_W
            is_today = (d == today)
            is_weekend = d.weekday() >= 5

            if is_today:
                p.fillRect(x, 0, self.DAY_W, self.HEADER_H, _TIMELINE_COLORS["COLOR_TODAY"])
            elif is_weekend:
                p.fillRect(x, 0, self.DAY_W, self.HEADER_H, _TIMELINE_COLORS["COLOR_WEEKEND"])

            day_names = ["一", "二", "三", "四", "五", "六", "日"]
            label = f"{d.month}/{d.day}\n周{day_names[d.weekday()]}"
            p.drawText(QRect(x, 0, self.DAY_W, self.HEADER_H),
                       Qt.AlignCenter, label)

    def _draw_grid(self, p: QPainter, palette: dict):
        p.setPen(QPen(palette["COLOR_GRID"], 1))
        total_h = self.HEADER_H + len(self._rooms) * self.ROW_H

        # 竖线（日期分隔）
        for i in range(self.DAYS + 1):
            x = self.ROOM_W + i * self.DAY_W
            p.drawLine(x, 0, x, total_h)

        # 横线（房间分隔）
        for i in range(len(self._rooms) + 1):
            y = self.HEADER_H + i * self.ROW_H
            p.drawLine(0, y, self.width(), y)

        # 房间列右边界
        p.setPen(QPen(QColor(_p("text_muted")), 2))
        p.drawLine(self.ROOM_W, 0, self.ROOM_W, total_h)

    def _draw_today_line(self, p: QPainter, palette: dict):
        today = date.today()
        if self._start_date <= today < self._start_date + timedelta(days=self.DAYS):
            x = self._day_to_x(today)
            # 当前时间精确位置
            now = datetime.now()
            hour_frac = (now.hour * 60 + now.minute) / (24 * 60)
            x_now = x + int(self.DAY_W * hour_frac)
            total_h = self.HEADER_H + len(self._rooms) * self.ROW_H
            p.setPen(QPen(palette["COLOR_TODAY_LINE"], 2, Qt.DashLine))
            p.drawLine(x_now, self.HEADER_H, x_now, total_h)

    def _draw_reservations(self, p: QPainter, palette: dict):
        font = QFont()
        font.setPointSize(9)
        p.setFont(font)

        end_date = self._start_date + timedelta(days=self.DAYS)

        for res in self._reservations:
            # 裁剪到可见范围
            vis_start = max(res.checkin.date(), self._start_date)
            vis_end   = min(res.checkout.date(), end_date)
            if vis_start >= vis_end:
                continue

            row = self._rooms.index(res.room_id) if res.room_id in self._rooms else -1
            if row < 0:
                continue

            x1 = self._day_to_x(vis_start)
            x2 = self._day_to_x(vis_end)
            y  = self._row_to_y(row) + 3
            h  = self.ROW_H - 6
            w  = x2 - x1

            # 颜色
            if res.is_conflict:
                color = palette["COLOR_CONFLICT"]
            elif res.status == "INHOUSE":
                color = palette["COLOR_INHOUSE"]
            elif res.status == "RESERVED":
                color = palette["COLOR_RESERVED"]
            elif res.status == "DIRTY":
                color = palette["COLOR_DIRTY"]
            elif res.status == "MAINTENANCE":
                color = palette["COLOR_MAINTENANCE"]
            elif res.status == "OVERTIME":
                color = palette["COLOR_OVERTIME"]
            else:
                color = palette["COLOR_RESERVED"]

            # 绘制圆角矩形
            p.setBrush(QBrush(color))
            p.setPen(QPen(color.darker(120), 1))
            p.drawRoundedRect(x1 + 1, y, w - 2, h, 4, 4)

            # 文字
            p.setPen(QColor(_p("surface")))
            text_rect = QRect(x1 + 4, y, w - 8, h)
            fm = QFontMetrics(font)
            text = res.guest_name
            if fm.horizontalAdvance(text) > w - 8:
                text = fm.elidedText(text, Qt.ElideRight, w - 8)
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)

    def _draw_room_labels(self, p: QPainter):
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        p.setFont(font)

        for i, room_id in enumerate(self._rooms):
            y = self._row_to_y(i)
            p.setPen(QColor(_p("primary")))
            p.drawText(QRect(0, y, self.ROOM_W, self.ROW_H),
                       Qt.AlignCenter, room_id)

    # ── 鼠标事件 ──────────────────────────────
    def mouseMoveEvent(self, event: QMouseEvent):
        x, y = event.x(), event.y()
        if y < self.HEADER_H:
            self._hover_cell = None
            self.setCursor(Qt.ArrowCursor)
            self.update()
            return

        row = self._y_to_row(y)
        day_idx = (x - self.ROOM_W) // self.DAY_W if x >= self.ROOM_W else -1

        if 0 <= row < len(self._rooms) and 0 <= day_idx < self.DAYS:
            self._hover_cell = (row, day_idx)
            # 检查是否在预订条目右边界（拖拽区域）
            res = self._find_reservation_at(x, y)
            if res:
                res_end_x = self._day_to_x(res.checkout.date())
                if abs(x - res_end_x) <= 6:
                    self.setCursor(Qt.SizeHorCursor)
                else:
                    self.setCursor(Qt.PointingHandCursor)
            else:
                self.setCursor(Qt.CrossCursor)
        else:
            self._hover_cell = None
            self.setCursor(Qt.ArrowCursor)

        # 拖拽中
        if self._drag_res:
            d = self._x_to_day(x)
            if d and d > self._drag_res.checkin.date():
                self._drag_res.checkout = datetime.combine(d, self._drag_res.checkout.time()
                                                           if hasattr(self._drag_res.checkout, 'time')
                                                           else datetime.min.time())
                self._detect_conflicts()
                self.update()

        self.update()

        # Tooltip
        res = self._find_reservation_at(x, y)
        if res:
            tip = (f"{res.room_id}  {res.guest_name}\n"
                   f"入住：{res.checkin.strftime('%m/%d %H:%M') if hasattr(res.checkin, 'strftime') else res.checkin}\n"
                   f"退房：{res.checkout.strftime('%m/%d %H:%M') if hasattr(res.checkout, 'strftime') else res.checkout}\n"
                   f"状态：{res.status}")
            if res.is_conflict:
                tip += "\n⚠️ 时间冲突！"
            QToolTip.showText(event.globalPos(), tip, self)
        else:
            QToolTip.hideText()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton:
            return
        x, y = event.x(), event.y()
        if y < self.HEADER_H:
            return

        res = self._find_reservation_at(x, y)
        if res:
            # 检查是否点击右边界（拖拽）
            res_end_x = self._day_to_x(res.checkout.date())
            if abs(x - res_end_x) <= 6:
                self._drag_res = res
                self._drag_start_x = x
                self._drag_orig_checkout = res.checkout
            else:
                self.reservation_clicked.emit(res)
        else:
            # 点击空格子 → 新增预订
            d = self._x_to_day(x)
            row = self._y_to_row(y)
            if d and 0 <= row < len(self._rooms):
                self.empty_cell_clicked.emit(self._rooms[row], d)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._drag_res:
            # 拖拽结束，保存新退房时间
            self._save_drag_result(self._drag_res)
            self._drag_res = None
            self._drag_orig_checkout = None
            self.update()

    def _find_reservation_at(self, x: int, y: int) -> Optional[Reservation]:
        if y < self.HEADER_H:
            return None
        row = self._y_to_row(y)
        if row < 0 or row >= len(self._rooms):
            return None
        room_id = self._rooms[row]
        d = self._x_to_day(x)
        if not d:
            return None
        dt = datetime.combine(d, datetime.min.time())
        for res in self._reservations:
            if res.room_id != room_id:
                continue
            if res.checkin.date() <= d < res.checkout.date():
                return res
        return None

    def _save_drag_result(self, res: Reservation):
        """保存拖拽后的退房时间到数据库"""
        try:
            db.execute(
                "UPDATE guests SET checkout_time=? WHERE room_id=? AND status='INHOUSE'",
                (res.checkout.strftime("%Y-%m-%d %H:%M:%S"), res.room_id)
            )
            bus.show_success_overlay.emit(
                f"已更新 {res.room_id} 退房时间：{res.checkout.strftime('%m/%d %H:%M')}"
            )
        except Exception as e:
            pass  # 静默失败（预订记录可能在其他表）


# ─────────────────────────────────────────────
#  时间轴视图主组件
# ─────────────────────────────────────────────

class TimelineView(QWidget):
    """
时间轴视图主组件（嵌入主窗口左侧）
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        from event_bus import bus
        bus.theme_changed.connect(lambda _: self.refresh_theme())
        # 每分钟刷新（更新今日线位置）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(60_000)
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 6, 8, 6)
        toolbar.setSpacing(8)

        title_lbl = QLabel("房态时间轴")
        self._title_lbl = title_lbl
        title_lbl.setStyleSheet(f"font-weight:bold; font-size:16px; color:{_p('primary')};")
        toolbar.addWidget(title_lbl)
        toolbar.addStretch()

        # 日期导航
        btn_prev = QPushButton("◀ 前7天")
        btn_prev.setObjectName("FdGhostBtn")
        btn_prev.setStyleSheet("padding:4px 10px; font-size:12px;")
        btn_prev.clicked.connect(lambda: self._shift_days(-7))

        btn_today = QPushButton("今天")
        self._btn_today = btn_today
        btn_today.setObjectName("FdGhostBtn")
        btn_today.setStyleSheet(
            f"background:{_p('primary')}; color:white; padding:4px 10px; "
            "font-size:12px; border-radius:4px;"
        )
        btn_today.clicked.connect(self._go_today)

        btn_next = QPushButton("后7天 ▶")
        btn_next.setObjectName("FdGhostBtn")
        btn_next.setStyleSheet("padding:4px 10px; font-size:12px;")
        btn_next.clicked.connect(lambda: self._shift_days(7))

        toolbar.addWidget(btn_prev)
        toolbar.addWidget(btn_today)
        toolbar.addWidget(btn_next)

        # 图例
        self._legend_dots: list[tuple[QLabel, str]] = []
        legend_items = [
            ("在住", "COLOR_INHOUSE"),
            ("预订", "COLOR_RESERVED"),
            ("待清洁", "COLOR_DIRTY"),
            ("维修", "COLOR_MAINTENANCE"),
            ("冲突", "COLOR_CONFLICT"),
        ]
        palette = _build_timeline_colors()
        for label, color_key in legend_items:
            dot = QLabel("●")
            dot.setStyleSheet(
                f"color: {palette[color_key].name()}; font-size:14px;"
            )
            lbl = QLabel(label)
            lbl.setObjectName("Small")
            toolbar.addWidget(dot)
            toolbar.addWidget(lbl)
            self._legend_dots.append((dot, color_key))

        btn_add = QPushButton("新增预订")
        self._btn_add = btn_add
        btn_add.setObjectName("SolidPrimaryBtn")
        btn_add.setStyleSheet(
            f"background:{_p('amount_positive')}; color:white; font-weight:bold; "
            "padding:4px 12px; border-radius:4px; font-size:12px;"
        )
        btn_add.clicked.connect(self._add_reservation)
        toolbar.addWidget(btn_add)

        toolbar_widget = QWidget()
        self._toolbar_widget = toolbar_widget
        toolbar_widget.setLayout(toolbar)
        toolbar_widget.setStyleSheet(f"background:{_p('surface_alt')}; border-bottom:1px solid {_p('border')};")
        layout.addWidget(toolbar_widget)

        # ── 冲突警告条 ──
        self.conflict_bar = QLabel("")
        self.conflict_bar.setStyleSheet(
            f"background:{_p('surface_alt')}; color:{_p('danger')}; font-weight:bold; "
            "padding:6px 12px; font-size:12px;"
        )
        self.conflict_bar.setVisible(False)
        layout.addWidget(self.conflict_bar)

        # ── 甘特图（带滚动）──
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setFrameShape(QFrame.NoFrame)

        self.canvas = GanttCanvas()
        self.canvas.reservation_clicked.connect(self._on_res_clicked)
        self.canvas.empty_cell_clicked.connect(self._on_empty_clicked)
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll, stretch=1)

        self._start_date = date.today()

    def refresh_theme(self) -> None:
        """换主题后重刷工具栏 inline 色与甘特图画布。"""
        self._title_lbl.setStyleSheet(
            f"font-weight:bold; font-size:16px; color:{_p('primary')};"
        )
        self._btn_today.setStyleSheet(
            f"background:{_p('primary')}; color:white; padding:4px 10px; "
            "font-size:12px; border-radius:4px;"
        )
        self._btn_add.setStyleSheet(
            f"background:{_p('amount_positive')}; color:white; font-weight:bold; "
            "padding:4px 12px; border-radius:4px; font-size:12px;"
        )
        self._toolbar_widget.setStyleSheet(
            f"background:{_p('surface_alt')}; border-bottom:1px solid {_p('border')};"
        )
        self.conflict_bar.setStyleSheet(
            f"background:{_p('surface_alt')}; color:{_p('danger')}; font-weight:bold; "
            "padding:6px 12px; font-size:12px;"
        )
        palette = _build_timeline_colors()
        for dot, color_key in self._legend_dots:
            dot.setStyleSheet(f"color: {palette[color_key].name()}; font-size:14px;")
        self.canvas.update()

    def _shift_days(self, delta: int):
        self._start_date += timedelta(days=delta)
        self.refresh()

    def _go_today(self):
        self._start_date = date.today()
        self.refresh()

    def refresh(self):
        """从数据库加载数据并刷新甘特图"""
        try:
            # 加载房间列表
            rooms_raw = db.execute(
                "SELECT room_id FROM rooms ORDER BY room_id"
            ).fetchall()
            rooms = [r[0] for r in rooms_raw]

            # 加载在住客人
            reservations = []
            end_date = self._start_date + timedelta(days=GanttCanvas.DAYS)

            guests = db.execute(
                """SELECT room_id, name, checkin_time, checkout_time, status, phone
                   FROM guests
                   WHERE status IN ('INHOUSE', 'RESERVED')
                   AND checkout_time >= ?
                   AND checkin_time <= ?""",
                (self._start_date.strftime("%Y-%m-%d"),
                 end_date.strftime("%Y-%m-%d"))
            ).fetchall()

            for g in guests:
                room_id, name, checkin_str, checkout_str, status, phone = g
                try:
                    checkin_dt = datetime.strptime(checkin_str[:16], "%Y-%m-%d %H:%M")
                    checkout_dt = datetime.strptime(checkout_str[:16], "%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    continue
                res = Reservation(
                    res_id=f"{room_id}_{checkin_str}",
                    room_id=room_id,
                    guest_name=name or "未知",
                    checkin=checkin_dt,
                    checkout=checkout_dt,
                    status=status,
                    phone=phone or ""
                )
                reservations.append(res)

            # 加载待清洁房间
            dirty_rooms = db.execute(
                "SELECT room_id FROM rooms WHERE status='DIRTY'"
            ).fetchall()
            for (room_id,) in dirty_rooms:
                now = datetime.now()
                res = Reservation(
                    res_id=f"dirty_{room_id}",
                    room_id=room_id,
                    guest_name="待清洁",
                    checkin=now,
                    checkout=now + timedelta(hours=2),
                    status="DIRTY"
                )
                reservations.append(res)

            self.canvas.set_data(rooms, reservations, self._start_date)

            # 检查冲突
            conflicts = [r for r in reservations if r.is_conflict]
            if conflicts:
                rooms_str = "、".join(set(r.room_id for r in conflicts))
                self.conflict_bar.setText(
                    f"⚠️ 检测到时间冲突！房间：{rooms_str}  请及时处理。"
                )
                self.conflict_bar.setVisible(True)
            else:
                self.conflict_bar.setVisible(False)

        except Exception as e:
            pass  # 静默失败，避免影响主界面

    def _on_res_clicked(self, res: Reservation):
        """点击已有预订 → 编辑对话框"""
        dlg = ReservationDialog(self, reservation=res)
        if dlg.exec() == QDialog.Accepted:
            if dlg._deleted:
                self._delete_reservation(res)
            else:
                self._update_reservation(res, dlg.result_data)
            self.refresh()

    def _on_empty_clicked(self, room_id: str, d):
        """点击空格子 → 新增预订"""
        checkin_dt = datetime.combine(d, datetime.strptime("14:00", "%H:%M").time())
        checkout_dt = checkin_dt + timedelta(days=1)
        dlg = ReservationDialog(
            self, room_id=room_id,
            checkin_dt=checkin_dt, checkout_dt=checkout_dt
        )
        if dlg.exec() == QDialog.Accepted:
            self._create_reservation(dlg.result_data)
            self.refresh()

    def _add_reservation(self):
        """工具栏新增预订按钮"""
        dlg = ReservationDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self._create_reservation(dlg.result_data)
            self.refresh()

    def _create_reservation(self, data: dict):
        """写入预订到数据库"""
        try:
            db.execute(
                """INSERT INTO guests (room_id, name, phone, checkin_time, checkout_time, status)
                   VALUES (?, ?, ?, ?, ?, 'RESERVED')""",
                (data["room_id"], data["guest_name"], data.get("phone", ""),
                 data["checkin"].strftime("%Y-%m-%d %H:%M:%S"),
                 data["checkout"].strftime("%Y-%m-%d %H:%M:%S"))
            )
            bus.show_success_overlay.emit(
                f"预订成功：{data['room_id']} {data['guest_name']}"
            )
            db.log_action(_current_actor_id(), "RESERVATION_CREATE",
                          f"房间{data['room_id']} 客人{data['guest_name']}")
        except Exception as e:
            show_error(self, "保存失败", str(e))

    def _update_reservation(self, res: Reservation, data: dict):
        """更新预订"""
        try:
            db.execute(
                """UPDATE guests SET name=?, phone=?, checkin_time=?, checkout_time=?
                   WHERE room_id=? AND checkin_time=?""",
                (data["guest_name"], data.get("phone", ""),
                 data["checkin"].strftime("%Y-%m-%d %H:%M:%S"),
                 data["checkout"].strftime("%Y-%m-%d %H:%M:%S"),
                 res.room_id,
                 res.checkin.strftime("%Y-%m-%d %H:%M:%S"))
            )
            bus.show_success_overlay.emit(f"预订已更新：{data['room_id']}")
        except Exception as e:
            show_error(self, "更新失败", str(e))

    def _delete_reservation(self, res: Reservation):
        """删除预订"""
        try:
            db.execute(
                """DELETE FROM guests WHERE room_id=? AND checkin_time=? AND status='RESERVED'""",
                (res.room_id, res.checkin.strftime("%Y-%m-%d %H:%M:%S"))
            )
            bus.show_success_overlay.emit(f"预订已删除：{res.room_id}")
        except Exception as e:
            show_error(self, "删除失败", str(e))
