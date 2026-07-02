from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QHBoxLayout,
    QScrollArea,
    QMenu,
    QFrame,
    QPushButton,
    QDialog,
    QComboBox,
    QTextEdit,
    QSizePolicy,
    QLineEdit,
)
from PySide6.QtCore import Qt, Signal, QTimer, QMimeData
from PySide6.QtGui import QFont, QColor, QDrag, QPixmap

from database import db
from i18n import i18n
from permission_system import PermissionManager
from ui_helpers import show_warning, show_info, ask_confirm, fix_fusion_combo_popup
from sound_helper import play_success, play_fail, play_warn, play_notify
from design_tokens import active_room_status_theme, _p
from frontdesk_ui import (
    fd_section_bar,
    fd_apply_low_freq_btn,
    fd_apply_card_action_btn,
    fd_apply_action_btn,
    FD_MARGIN,
    FD_SPACE_SM,
    FD_SPACE_MD,
    FD_SPACE_LG,
)
from ui_surface import fd_apply_scroll_area, fd_connect_theme_refresh, fd_refresh_surfaces, fd_apply_empty_state
import logging
logger = logging.getLogger(__name__)

# 与顶栏 FilterChip 传入值一致；非法值会导致「全部不匹配」从而房卡全部被隐藏
_VALID_MATRIX_FILTERS = frozenset({"ALL", "READY", "INHOUSE", "DIRTY", "OVERTIME", "RESERVED"})


def _current_actor_id() -> str:
    u = PermissionManager.current_user()
    if u:
        return str(u.get("username") or u.get("id") or "unknown")
    return PermissionManager.current_role() or "guest"

_STATUS_KEYS = {
    "READY": "room_ready", "INHOUSE": "room_inhouse", "DIRTY": "room_dirty",
    "OVERTIME": "room_overtime", "MAINTENANCE": "room_maintenance",
    "RESERVED": "room_reserved",
}
_STATUS_EMOJI = {
    "READY": "", "INHOUSE": "", "DIRTY": "", "OVERTIME": "", "MAINTENANCE": "",
    "RESERVED": "",
}


def _build_status_config() -> dict:
    palette = active_room_status_theme()
    out = {}
    for code in _STATUS_KEYS:
        theme = palette.get(code, palette.get("MAINTENANCE", palette.get("READY")))
        out[code] = {"key": _STATUS_KEYS[code], "emoji": _STATUS_EMOJI[code], **theme}
    return out


def current_status_config() -> dict:
    return _build_status_config()


def _fallback_letter(room_type: str) -> str:
    """房型 emoji 渲染失败时的字母兜底；取 type_id 首字符大写。"""
    t = (room_type or "").strip().upper()
    if not t:
        return "·"
    for ch in t:
        if ch.isalnum():
            return ch
    return t[0]


def _floor_display(floor_val: str) -> str:
    """楼层显示：None/空 → i18n「未分层」。"""
    if not floor_val or floor_val.strip().upper() in ("NONE", "NULL", ""):
        return i18n.t("matrix_floor_none")
    return floor_val


class RoomCard(QFrame):
    """房卡：放大版 + 信息三层 + 右上角房型 emoji（字母兜底）+ 左侧色条。"""

    CARD_W = 200   # 初始默认值，构造时被 _init_card_size 重写
    CARD_H = 144

    @staticmethod
    def _init_card_size(screen_w: int = 0, viewport_w: int | None = None) -> tuple:
        from design_tokens import pick_card_size
        return pick_card_size(screen_w or 1440, viewport_w)

    clicked = Signal(str, str)
    selection_changed = Signal(str, bool)   # room_id, selected

    def __init__(
        self,
        room_id,
        floor,
        room_type,
        status,
        guest_name="",
        guest_phone="",
        checkin_time="",
        note="",
        type_icon="",
        lock_no="",
        screen_w: int = 0,
        viewport_w: int | None = None,
    ):
        super().__init__()
        self.room_id = room_id
        self.floor = str(floor)
        self.room_type = room_type
        self.status = status
        self.guest_name = guest_name or ""
        self.guest_phone = guest_phone or ""
        self.checkin_time = checkin_time or ""
        self.note = note or ""
        self.type_icon = (type_icon or "").strip()
        self.lock_no = (lock_no or "").strip()
        self._hovered = False
        self._pressed = False
        self._selected = False          # 批量选择状态
        self._batch_mode = False        # 是否处于批量模式

        self.setObjectName("RoomCard")
        self.setCursor(Qt.PointingHandCursor)
        # 初始尺寸
        cw, ch = RoomCard._init_card_size(screen_w, viewport_w)
        self.CARD_W = cw
        self.CARD_H = ch
        self.setMinimumSize(max(100, cw - 24), max(84, ch - 16))
        self.setMaximumHeight(min(200, ch + 14))
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.setFixedSize(cw, ch)
        self.setProperty("status", status)
        self.setAcceptDrops(True)  # 接受拖拽落放（换房）

        # 整卡水平：左 4px 色条 + 右侧内容区
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # [sub-j] 色条加粗：5px → 6px，左侧状态色条更醒目
        self.lbl_strip = QLabel()
        self.lbl_strip.setObjectName("RoomCardStrip")
        self.lbl_strip.setFixedWidth(6)
        root.addWidget(self.lbl_strip)

        body = QWidget()
        body.setObjectName("RoomCardBody")
        layout = QVBoxLayout(body)
        # [sub-j] 上部留白减少：顶部 padding 4px → 2px（原 12px QSS 已经够，本地 4→2 进一步压紧）
        # 左右保持 8px，底部 2px 让退房日期贴底
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(2)  # 3 → 2，客人名紧贴房号
        root.addWidget(body, 1)

        # 第 1 层：房号 + 右上角房型 emoji/字母
        # [sub-j] 房号字号在 QSS 中由 22px → 26px 加大 20%（见 themes/base.qss v8.2 段）
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(4)  # 6 → 4 像素级紧凑
        self.lbl_id = QLabel(room_id)
        self.lbl_id.setObjectName("RoomCardId")
        hdr.addWidget(self.lbl_id)
        hdr.addStretch()

        self.lbl_type_icon = QLabel()
        self.lbl_type_icon.setObjectName("RoomCardTypeIcon")
        self.lbl_type_icon.setAlignment(Qt.AlignCenter)
        self.lbl_type_icon.setMinimumWidth(40)
        hdr.addWidget(self.lbl_type_icon)
        layout.addLayout(hdr)

        # 第 2 层：房态色块 + 客人名 + 入住时长
        self.lbl_guest = QLabel()
        self.lbl_guest.setObjectName("RoomCardGuest")
        self.lbl_guest.setWordWrap(False)
        layout.addWidget(self.lbl_guest)

        # 第 3 层：辅助提示（楼层 · 房型 / 备注）
        self.lbl_meta = QLabel()
        self.lbl_meta.setObjectName("RoomCardMeta")
        self.lbl_meta.setWordWrap(True)
        layout.addWidget(self.lbl_meta)

        layout.addStretch()

        # 状态徽章（小色块），底部一行
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)
        self.lbl_badge = QLabel()
        self.lbl_badge.setObjectName("RoomCardBadge")
        self.lbl_badge.setAlignment(Qt.AlignCenter)
        bottom.addWidget(self.lbl_badge)
        self.lbl_lock_warn = QLabel(i18n.t("lock_not_bound"))
        self.lbl_lock_warn.setObjectName("RoomCardLockWarn")
        self.lbl_lock_warn.setAlignment(Qt.AlignCenter)
        self.lbl_lock_warn.setToolTip(i18n.t("hint_lock_not_bound"))
        bottom.addWidget(self.lbl_lock_warn)
        bottom.addStretch()
        self.lbl_status = QLabel()
        self.lbl_status.setObjectName("RoomCardStatus")
        self.lbl_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bottom.addWidget(self.lbl_status, 1)
        layout.addLayout(bottom)

        # 文字标签会吃掉点击，导致房卡 mousePressEvent / clicked 不触发（收银无反应）
        for lbl in (
            self.lbl_strip,
            self.lbl_id,
            self.lbl_type_icon,
            self.lbl_badge,
            self.lbl_lock_warn,
            self.lbl_guest,
            self.lbl_meta,
            self.lbl_status,
        ):
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        body.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._refresh_texts()
        self._apply_card_style()
        self._refresh_tooltip()

    # ── 批量选择支持 ──────────────────────────────────────────────────────────
    def set_batch_mode(self, enabled: bool):
        """开启/关闭批量选择模式"""
        self._batch_mode = enabled
        if not enabled:
            self._selected = False
        self._apply_card_style()

    def set_selected(self, selected: bool):
        prev = self._selected
        self._selected = selected
        self._apply_card_style()
        # Lovable v3 · 节奏 #2：选中"盖章"动效，仅在新选中时触发
        if selected and not prev:
            from motion_gate import pulse_room_select
            pulse_room_select(self)

    def is_selected(self) -> bool:
        return self._selected

    def _masked_phone(self):
        digits = "".join(ch for ch in self.guest_phone if ch.isdigit())
        if len(digits) >= 4:
            return digits[-4:]
        return self.guest_phone.strip()

    def _format_duration(self):
        if not self.checkin_time:
            return ""
        try:
            dt = datetime.strptime(str(self.checkin_time)[:19], "%Y-%m-%d %H:%M:%S")
            delta = datetime.now() - dt
            hours = max(0, int(delta.total_seconds() // 3600))
            days = hours // 24
            if days > 0:
                return i18n.t("duration_days").format(days=days, hours=hours % 24)
            return i18n.t("duration_hours").format(hours=hours)
        except Exception:
            return ""

    def _build_hint(self):
        duration = self._format_duration()
        if self.status == "READY":
            return i18n.t("room_hint_ready")
        if self.status == "INHOUSE":
            return duration or i18n.t("room_hint_inhouse_alt")
        if self.status == "DIRTY":
            return i18n.t("room_hint_dirty")
        if self.status == "OVERTIME":
            return (duration or "") + i18n.t("room_hint_overtime")
        if self.status == "MAINTENANCE":
            return self.note or i18n.t("room_hint_maintenance_default")
        return ""

    def _refresh_texts(self):
        status_config = current_status_config()
        cfg = status_config.get(self.status, status_config["READY"])
        self.lbl_badge.setText(i18n.t(cfg["key"]))
        # ★ 房态文字标签
        _status_short = {
            "READY": i18n.t("status_ready_short"),
            "INHOUSE": i18n.t("status_inhouse_short"),
            "DIRTY": i18n.t("status_dirty_short"),
            "MAINTENANCE": i18n.t("status_maintenance_short"),
            "OCCUPIED": i18n.t("status_inhouse_short"),
            "RESERVED": i18n.t("status_reserved_short"),
        }.get(self.status, self.status)
        letter = _fallback_letter(self.room_type)
        if self.type_icon:
            self.lbl_type_icon.setText(f"{self.type_icon} {letter}")
        else:
            self.lbl_type_icon.setText(letter)
        self.lbl_type_icon.setToolTip(self.room_type or "")

        duration = self._format_duration() if self.status in ("INHOUSE", "OVERTIME") else ""
        if self.guest_name:
            second_line = self.guest_name
            if duration:
                second_line += f"  ·  {duration}"
        else:
            second_line = i18n.t("status_vacant") if self.status == "READY" else i18n.t("status_no_guest")
        self.lbl_guest.setText(second_line)

        meta = [f"{_floor_display(self.floor)}{i18n.t('table_floor')}"]
        if self.guest_phone:
            meta.append(f"尾号 {self._masked_phone()}")
        elif self.note and self.status != "MAINTENANCE":
            meta.append(self.note[:14])
        elif self.status == "MAINTENANCE" and self.note:
            meta.append(self.note[:14])
        self.lbl_meta.setText(" · ".join(meta))
        self.lbl_status.setText(f"{cfg['emoji']} {self._build_hint()}")
        self.lbl_lock_warn.setVisible(not bool(self.lock_no))

    def _apply_card_style(self):
        status_config = current_status_config()
        cfg = status_config.get(self.status, status_config["READY"])
        if self._selected:
            border = _p("primary")
            background = _p("bg_root")
        elif self._pressed:
            border = _p("primary")
            background = _p("bg_root")
        elif self._hovered:
            border = _p("accent")
            background = _p("bg_root")
        else:
            border = cfg["border"]
            background = _p("bg_root")

        select_outline = f"border-top: 3px solid {_p('primary')}; border-right: 3px solid {_p('primary')}; border-bottom: 3px solid {_p('primary')};" if self._selected else ""
        strip_color = cfg["border"]
        self.setStyleSheet(
            f"""
            QFrame#RoomCard {{
                background: {background};
                border-top: 2px solid {border};
                border-right: 2px solid {border};
                border-bottom: 2px solid {border};
                border-left: none;
                border-radius: 12px;
                {select_outline}
            }}
            /* [sub-j] 房号字号 26px 加大（不论 QSS 实际生效值 22 或 28，统一覆盖为 26px，
               保证视觉一致；字重 800 加重，房号是房卡最重要的信息，需第一眼可见） */
            QLabel#RoomCardId {{ color: {_p("text")}; font-size: 26px; font-weight: 800; }}
            QLabel#RoomCardGuest {{ color: {_p("text")}; font-size: 13px; font-weight: 600; }}
            QLabel#RoomCardMeta {{ color: {_p("text_muted")}; font-size: 11px; }}
            QLabel#RoomCardStatus {{ color: {cfg['color']}; font-size: 11px; font-weight: 700; }}
            QLabel#RoomCardLockWarn {{
                background: {_p("surface_alt")};
                color: {_p("danger")};
                border: 1px solid {_p("danger")};
            }}
            """
        )

        self.lbl_strip.setStyleSheet(
            f"background:{strip_color}; border-top-left-radius:12px; border-bottom-left-radius:12px;"
        )
        self.lbl_type_icon.setStyleSheet(
            f"background:{cfg['soft']}; color:{cfg['color']}; border-radius:6px;"
            "padding:3px 10px; font-size:14px; font-weight:800;"
        )
        self.lbl_badge.setStyleSheet(
            f"background:{cfg['soft']}; color:{cfg['color']}; border-radius:6px;"
            "padding:2px 10px; font-size:11px; font-weight:800;"
        )
        self._apply_shadow()

    def _apply_shadow(self):
        """房卡阴影 — 委托 motion_gate.attach_card_shadow，与 fd_card 面板统一。"""
        try:
            from motion_gate import attach_card_shadow
            attach_card_shadow(self, "sm")
        except Exception:
            pass

    def _start_breathing(self):
        """房卡选中态呼吸动效 — 委托 motion_gate.pulse_room_select。"""
        # pulse_room_select 已在 RoomMatrix._on_card_clicked 中调用，此处保留接口兼容
        pass

    def enterEvent(self, event):
        self._hovered = True
        self._apply_card_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_card_style()
        super().leaveEvent(event)

    def update_status(self, st):
        self.status = st
        self.setProperty("status", st)
        try:
            row = db.execute(
                """
                SELECT COALESCE(g.name,''), COALESCE(g.phone,''), COALESCE(g.checkin_time,''),
                       COALESCE(r.note,''), COALESCE(t.icon,''), COALESCE(r.lock_no,'')
                FROM rooms r
                LEFT JOIN guests g ON g.room_id=r.room_id AND g.status='INHOUSE'
                LEFT JOIN room_type_templates t ON t.type_id=r.room_type
                WHERE r.room_id=?
                """,
                (self.room_id,),
            ).fetchone()
        except Exception as _exc:
            logger.error("update_status db error: %s", _exc)
            return
        if row:
            self.guest_name, self.guest_phone, self.checkin_time, self.note, icon_now, lock_no_now = row
            self.type_icon = (icon_now or "").strip()
            self.lock_no = (lock_no_now or "").strip()
        else:
            self.guest_name = ""
            self.guest_phone = ""
            self.checkin_time = ""
            self.note = ""
            self.lock_no = ""
        self._refresh_texts()
        self._apply_card_style()
        self._refresh_tooltip()

    def _refresh_tooltip(self):
        """鼠标悬停时显示房间详情速览卡片。"""
        lines = [f"{self.room_id}  ·  {self.room_type}"]
        st_map = {"READY": "\U0001f7e2 " + i18n.t("room_hint_ready"), "INHOUSE": "\U0001f535 " + i18n.t("status_inhouse_short"), "DIRTY": "\U0001f7e4 " + i18n.t("status_dirty_short"), "OVERTIME": "\U0001f7e0 " + i18n.t("room_hint_overtime_short"), "MAINTENANCE": "\u26ab " + i18n.t("status_maintenance_short")}
        lines.append(st_map.get(self.status, self.status))
        if self.guest_name:
            lines.append(f"{self.guest_name}" + (f"  {self.guest_phone}" if self.guest_phone else ""))
        if self.checkin_time:
            ci = self.checkin_time[:16] if len(str(self.checkin_time)) > 16 else str(self.checkin_time)
            lines.append(f"入住 {ci}")
        if self.note:
            lines.append(f"{self.note[:40]}")
        self.setToolTip("\n".join(lines))

    def mousePressEvent(self, event):
        self._pressed = True
        self._apply_card_style()
        QTimer.singleShot(130, self._clear_pressed_feedback)
        if self._batch_mode:
            self._selected = not self._selected
            self._apply_card_style()
            self.selection_changed.emit(self.room_id, self._selected)
        elif event.button() == Qt.LeftButton:
            # 拖拽起点
            self._drag_start_pos = event.pos()
            db.log_action(_current_actor_id(), "SELECT_ROOM", self.room_id)
            self.clicked.emit(self.room_id, self.room_type)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._batch_mode and event.buttons() == Qt.LeftButton:
            if hasattr(self, '_drag_start_pos') and (event.pos() - self._drag_start_pos).manhattanLength() >= 20:
                drag = QDrag(self)
                mime = QMimeData()
                # 拖拽数据：room_id|status|guest_name
                mime.setData("application/x-solid-room", f"{self.room_id}|{self.status}|{self.guest_name}".encode("utf-8"))
                drag.setMimeData(mime)
                pixmap = self.grab()
                drag.setPixmap(pixmap.scaled(100, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                drag.setHotSpot(pixmap.rect().center() / 2)
                drag.exec_(Qt.MoveAction)
        super().mouseMoveEvent(event)

    def _clear_pressed_feedback(self):
        self._pressed = False
        self._apply_card_style()

    def dragEnterEvent(self, event):
        """接受拖入（用于换房）"""
        if event.mimeData().hasFormat("application/x-solid-room"):
            # 只接受拖到空房
            if self.status == "READY":
                event.acceptProposedAction()
                self.setProperty("dragOver", True)
                self._apply_card_style()
            else:
                event.ignore()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setProperty("dragOver", False)
        self._apply_card_style()

    def dropEvent(self, event):
        """落放：将其他房间的客人换到此空房"""
        self.setProperty("dragOver", False)
        self._apply_card_style()
        data = event.mimeData().data("application/x-solid-room").data().decode("utf-8")
        parts = data.split("|")
        if len(parts) >= 1:
            src_room_id = parts[0]
            if src_room_id != self.room_id:
                try:
                    src = db.execute(
                        "SELECT guest_name, guest_phone, guest_id_card FROM rooms WHERE room_id=?",
                        (src_room_id,),
                    ).fetchone()
                    dst = db.execute(
                        "SELECT guest_name, guest_phone, guest_id_card FROM rooms WHERE room_id=?",
                        (self.room_id,),
                    ).fetchone()

                    src_guest = src[0] or "" if src else ""
                    src_phone = src[1] or "" if src else ""
                    src_idcard = src[2] or "" if src else ""

                    dst_guest = dst[0] or "" if dst else ""
                    dst_phone = dst[1] or "" if dst else ""
                    dst_idcard = dst[2] or "" if dst else ""

                    db.execute(
                        "UPDATE rooms SET guest_name=?, guest_phone=?, guest_id_card=? WHERE room_id=?",
                        (dst_guest, dst_phone, dst_idcard, src_room_id),
                    )
                    db.execute(
                        "UPDATE rooms SET guest_name=?, guest_phone=?, guest_id_card=? WHERE room_id=?",
                        (src_guest, src_phone, src_idcard, self.room_id),
                    )

                    from event_bus import bus

                    bus.room_status_changed.emit(src_room_id, "")
                    bus.room_status_changed.emit(self.room_id, "")
                    play_success()
                except Exception as e:
                    play_fail()
                    show_warning(
                        self,
                        i18n.t("dlg_tip", default="提示"),
                        str(e),
                    )
                else:
                    show_info(
                        self,
                        i18n.t("dlg_tip", default="提示"),
                        i18n.t("room_swapped", default=f"已将 {src_room_id} 的客人换到 {self.room_id}"),
                    )
                event.acceptProposedAction()
        super().dropEvent(event)

    def contextMenuEvent(self, ev):
        """右键快捷菜单：入住 / 退房 / 保洁 / 锁号。"""
        from PySide6.QtGui import QAction
        menu = QMenu(self)

        act_ci = QAction(f"{i18n.t('btn_checkin')} · {self.room_id}", self)
        act_co = QAction(f"{i18n.t('btn_checkout')} · {self.room_id}", self)
        act_hk = QAction(f"{i18n.t('tab_housekeeping')} · {self.room_id}", self)
        act_lock = QAction(f"查看完整属性", self)

        if self.status not in ("INHOUSE",):
            act_co.setEnabled(False)
        if self.status == "INHOUSE":
            act_ci.setEnabled(False)

        act_ci.triggered.connect(lambda: self.clicked.emit(self.room_id, self.room_type))
        act_co.triggered.connect(self._emit_checkout)
        act_hk.triggered.connect(lambda: self.clicked.emit(self.room_id, self.room_type))
        act_lock.triggered.connect(self._on_edit_properties)

        menu.addAction(act_ci)
        menu.addAction(act_co)
        menu.addSeparator()
        menu.addAction(act_hk)
        menu.addAction(act_lock)

        menu.exec(ev.globalPos())

    def _update_lock_warn(self):
        """根据有无锁号显示/隐藏锁号警告。"""
        has_lock = bool(self.lock_no)
        self.lbl_lock_warn.setVisible(not has_lock)

    def _on_edit_properties(self):
        """跳转到房间管理页查看完整属性（前台不可在此改锁号）。"""
        try:
            from app_main import get_main_window
            mw = get_main_window()
            if mw and hasattr(mw, '_navigate_to'):
                mw._navigate_to("room_unified")
                QTimer.singleShot(300, lambda: self._focus_room_card_in_page())
            else:
                show_info(self, i18n.t("dlg_tip"), "请在「房间与房型管理」中查看或修改房间属性。")
        except Exception:
            show_info(self, i18n.t("dlg_tip"), "请在「房间与房型管理」中查看或修改房间属性。")

    def _focus_room_card_in_page(self):
        try:
            from app_main import get_main_window
            mw = get_main_window()
            if mw and hasattr(mw, 'workspace') and hasattr(mw.workspace, 'room_unified_tab'):
                mw.workspace.room_unified_tab.select_room(self.room_id)
        except Exception:
            pass

    def _emit_checkout(self):
        from event_bus import bus
        bus.toast_requested.emit(f"确认退房 · {self.room_id}？请在右侧操作区确认")


class RoomMatrix(QWidget):
    room_selected = Signal(str, str)  # room_id, room_type

    def __init__(self):
        super().__init__()
        self.setObjectName("RoomMatrixRoot")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.cards = {}
        self.sections = []
        self.current_filter = "ALL"
        self.current_search = ""
        self._current_floor_filter = ""
        self._current_type_filter = ""
        self._batch_mode = False
        self._selected_rooms: set = set()
        self._card_row_gap = 6
        self._external_chrome = False
        self._last_pack_vw = -1
        self._repack_timer = QTimer(self)
        self._repack_timer.setSingleShot(True)
        self._repack_timer.timeout.connect(self._load)
        self._skeleton_active = False
        self._initial_load_done = False
        self._on_batch_create = None
        self._on_setup_wizard = None

        ml = QVBoxLayout(self)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        # ── 批量操作工具栏 ──
        self._batch_toolbar = QWidget()
        self._batch_toolbar.hide()
        bt_layout = QHBoxLayout(self._batch_toolbar)
        bt_layout.setContentsMargins(FD_MARGIN, FD_SPACE_MD, FD_MARGIN, FD_SPACE_MD)
        bt_layout.setSpacing(FD_SPACE_MD)

        self._batch_count_lbl = QLabel(i18n.t("batch_count_label").format(count=0))
        self._batch_count_lbl.setObjectName("BatchCountLabel")
        bt_layout.addWidget(self._batch_count_lbl)
        bt_layout.addStretch()

        btn_select_all = QPushButton(i18n.t("btn_select_all"))
        fd_apply_low_freq_btn(btn_select_all)
        btn_select_all.clicked.connect(self._select_all)
        bt_layout.addWidget(btn_select_all)

        btn_deselect = QPushButton(i18n.t("btn_deselect_all"))
        fd_apply_low_freq_btn(btn_deselect)
        btn_deselect.clicked.connect(self._deselect_all)
        bt_layout.addWidget(btn_deselect)

        btn_batch_status = QPushButton(i18n.t("btn_batch_status"))
        fd_apply_card_action_btn(btn_batch_status)
        btn_batch_status.clicked.connect(self._batch_change_status)
        bt_layout.addWidget(btn_batch_status)

        btn_batch_hk = QPushButton(i18n.t("btn_batch_housekeeping"))
        fd_apply_card_action_btn(btn_batch_hk)
        btn_batch_hk.clicked.connect(self._batch_send_housekeeping)
        bt_layout.addWidget(btn_batch_hk)

        btn_exit_batch = QPushButton(i18n.t("btn_exit_batch"))
        fd_apply_low_freq_btn(btn_exit_batch)
        btn_exit_batch.clicked.connect(self.exit_batch_mode)
        bt_layout.addWidget(btn_exit_batch)

        ml.addWidget(self._batch_toolbar)

        # ── 智能筛选栏 ──
        self._filter_bar = QWidget()
        self._filter_bar.setObjectName("MatrixFilterBar")
        self._fb_lay = QHBoxLayout(self._filter_bar)
        self._fb_lay.setContentsMargins(FD_MARGIN, 0, FD_MARGIN, 0)
        self._fb_lay.setSpacing(FD_SPACE_SM)

        # 按状态筛选
        statuses = [
            ("ALL", "全部"),
            ("READY", "空净"),
            ("INHOUSE", "在住"),
            ("DIRTY", "脏房"),
            ("OVERTIME", "超时"),
            ("MAINTENANCE", "维修"),
            ("RESERVED", "预留"),
        ]
        self._filter_chips = {}
        for code, label in statuses:
            chip = QPushButton(label)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda checked, c=code: self._apply_filter(c))
            self._filter_chips[code] = chip
            self._fb_lay.addWidget(chip)
            if code == "ALL":
                chip.setChecked(True)

        self._fb_lay.addSpacing(8)

        # 综合筛选（楼层+房型合并）
        self._filter_combo = QComboBox()
        self._filter_combo.setObjectName("FilterFloorCombo")
        self._filter_combo.setMinimumWidth(120)
        fix_fusion_combo_popup(self._filter_combo)
        self._filter_combo.currentTextChanged.connect(self._apply_combined_filter)
        self._fb_lay.addWidget(QLabel(i18n.t("filter_label")))
        self._fb_lay.addWidget(self._filter_combo)

        self._fb_lay.addStretch()

        self._search_inline = QLineEdit()
        self._search_inline.setObjectName("MatrixSearchInline")
        self._search_inline.setPlaceholderText(i18n.t("search_room_ph"))
        self._search_inline.setMaximumWidth(220)
        self._search_inline.setMinimumHeight(28)
        self._search_inline.textChanged.connect(self._on_search_inline_changed)
        self._fb_lay.addWidget(self._search_inline)

        # 分隔
        self._filter_bar_sep = QLabel("|")
        self._filter_bar_sep.setObjectName("FilterBarSep")
        self._filter_bar_sep.setStyleSheet("font-size: 14px; padding: 0 4px;")
        self._fb_lay.addWidget(self._filter_bar_sep)

        # 批处理入口按钮
        self._btn_batch_mode = QPushButton(i18n.t("btn_batch_mode"))
        fd_apply_low_freq_btn(self._btn_batch_mode)
        self._btn_batch_mode.clicked.connect(self.enter_batch_mode)
        self._fb_lay.addWidget(self._btn_batch_mode)

        ml.addWidget(self._filter_bar)

        # ── 顶栏合计带 ──
        self._stats_strip = QWidget()
        self._stats_strip.setObjectName("MatrixStatsStrip")
        stats_layout = QHBoxLayout(self._stats_strip)
        stats_layout.setContentsMargins(12, 1, 12, 1)
        stats_layout.setSpacing(16)

        labels_def = [
            ("total", "stat_total"),
            ("ready", "stat_ready"),
            ("inhouse", "stat_inhouse"),
            ("dirty", "stat_dirty"),
            ("overtime", "stat_overtime"),
            ("maintenance", "stat_maintenance"),
        ]
        self._stat_labels = {}
        self._stat_label_keys = {}
        for key, label_key in labels_def:
            lbl = QLabel(f"{i18n.t(label_key)} —")
            lbl.setObjectName("MatrixStatCell")
            lbl.setProperty("statKey", key)
            stats_layout.addWidget(lbl)
            self._stat_labels[key] = lbl
            self._stat_label_keys[key] = label_key
        stats_layout.addStretch()

        ml.addWidget(self._stats_strip)

        # ── v7 房态图例条 ──
        self._legend_strip = QWidget()
        self._legend_strip.setObjectName("MatrixLegendStrip")
        legend_lay = QHBoxLayout(self._legend_strip)
        legend_lay.setContentsMargins(FD_MARGIN, 6, FD_MARGIN, 6)
        legend_lay.setSpacing(FD_SPACE_LG)

        legend_title = QLabel(i18n.t("legend_title", default="图例"))
        legend_title.setObjectName("LegendTitle")
        legend_lay.addWidget(legend_title)

        status_config = _build_status_config()
        legend_items = [
            ("READY", i18n.t("status_ready", default="空净")),
            ("INHOUSE", i18n.t("status_inhouse", default="在住")),
            ("DIRTY", i18n.t("status_dirty", default="脏房")),
            ("OVERTIME", i18n.t("status_overtime", default="超时")),
            ("MAINTENANCE", i18n.t("status_maintenance", default="维修")),
        ]
        for code, label in legend_items:
            cfg = status_config.get(code, {})
            color = cfg.get("color", _p("text_muted"))
            dot = QLabel("\u25cf")
            dot.setObjectName("LegendDot")
            dot.setStyleSheet(f"color: {color}; font-size: 14px; background: transparent;")
            legend_lay.addWidget(dot)
            txt = QLabel(label)
            txt.setObjectName("LegendLabel")
            legend_lay.addWidget(txt)
        legend_lay.addStretch()
        ml.addWidget(self._legend_strip)

        # 兼容旧布局键（搜索已并入筛选条）
        self._search_bar = self._filter_bar

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("MatrixScroll")

        self.sw = QWidget()
        self.sw.setObjectName("MatrixScrollContainer")
        self.sl = QVBoxLayout(self.sw)
        self.sl.setSpacing(FD_SPACE_SM)
        self.sl.setContentsMargins(FD_SPACE_SM, FD_SPACE_SM, FD_SPACE_SM, FD_SPACE_SM)

        self.scroll.setWidget(self.sw)
        self.scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        # 首帧布局未完成时 viewport 高度偶发为 0，给滚动区底线避免「有数据但整片空白」
        self.scroll.setMinimumHeight(160)
        fd_apply_scroll_area(self.scroll)
        ml.addWidget(self.scroll, stretch=1)
        self._load()

        fd_refresh_surfaces(self)
        fd_connect_theme_refresh(self)

        from event_bus import bus

        bus.room_status_changed.connect(self._on_status)

    def set_external_chrome(self, enabled: bool = True) -> None:
        """SmartHeader 已提供筛选/批处理/添加时，隐藏矩阵内重复控件。"""
        self._external_chrome = enabled
        for chip in self._filter_chips.values():
            chip.setVisible(not enabled)
        self._btn_batch_mode.setVisible(not enabled)
        self._filter_bar_sep.setVisible(not enabled)
        if enabled:
            self._fb_lay.setContentsMargins(FD_MARGIN, 2, FD_MARGIN, FD_SPACE_SM)
            self._search_inline.setMaximumWidth(200)
        else:
            self._fb_lay.setContentsMargins(FD_MARGIN, FD_SPACE_SM, FD_MARGIN, FD_SPACE_SM)
            self._search_inline.setMaximumWidth(220)

    def _columns_for_viewport(self) -> int:
        """按滚动区宽度计算每行房卡数，避免固定 5 列右侧大块留白。"""
        vw = int(self.scroll.viewport().width())
        if vw < 120:
            pw = int(self.width())
            if pw > 200:
                vw = max(vw, pw - 120)
            else:
                vw = max(520, vw)
        gap = self._card_row_gap
        slot = max(108, RoomCard.CARD_W + gap)
        margins = 12
        cols = int((max(0, vw - margins)) // slot)
        return max(2, min(cols, 18))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.cards:
            return
        vw = int(self.scroll.viewport().width())
        if vw < 120:
            return
        if self._last_pack_vw >= 0 and abs(vw - self._last_pack_vw) < 56:
            return
        self._last_pack_vw = vw
        self._repack_timer.start(120)

    def _fetch_rooms(self):
        try:
            return db.execute(
                """
                SELECT
                    r.room_id,
                    r.floor,
                    r.room_type,
                    UPPER(COALESCE(NULLIF(TRIM(r.status), ''), 'READY')),
                    COALESCE(r.note, ''),
                    COALESCE(g.name, ''),
                    COALESCE(g.phone, ''),
                    COALESCE(g.checkin_time, ''),
                    COALESCE(t.icon, ''),
                    COALESCE(r.lock_no, '')
                FROM rooms r
                LEFT JOIN guests g ON g.room_id = r.room_id AND g.status = 'INHOUSE'
                LEFT JOIN room_type_templates t ON t.type_id = r.room_type
                ORDER BY r.floor, r.room_id
                """
            ).fetchall()
        except Exception as _exc:
            logger.error("_fetch_rooms db error: %s", _exc)
            return []

    def _floor_summary_text(self, floor_rooms):
        total = len(floor_rooms)
        inhouse = sum(1 for room in floor_rooms if room[3] == "INHOUSE")
        dirty = sum(1 for room in floor_rooms if room[3] == "DIRTY")
        overtime = sum(1 for room in floor_rooms if room[3] == "OVERTIME")
        return f"{total}间 · 在住 {inhouse} · 待清 {dirty} · 超时 {overtime}"

    def _floor_display(self, floor_val: str) -> str:
        return _floor_display(floor_val)

    def set_empty_handlers(self, on_batch_create=None, on_setup_wizard=None):
        self._on_batch_create = on_batch_create
        self._on_setup_wizard = on_setup_wizard

    def _add_empty_state(self):
        box = QFrame()
        box.setObjectName("MatrixEmptyState")
        fd_apply_empty_state(box)
        lay = QVBoxLayout(box)
        lay.setSpacing(14)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.addStretch()
        t = QLabel(i18n.t("matrix_empty_title"))
        t.setObjectName("MatrixEmptyTitle")
        t.setAlignment(Qt.AlignCenter)
        t.setWordWrap(True)
        lay.addWidget(t)
        h = QLabel(i18n.t("matrix_empty_hint_short"))
        h.setObjectName("MatrixEmptyHint")
        h.setAlignment(Qt.AlignCenter)
        h.setWordWrap(True)
        lay.addWidget(h)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()
        if getattr(self, "_on_batch_create", None):
            btn_batch = QPushButton(i18n.t("matrix_empty_btn_batch"))
            btn_batch.setObjectName("SolidPrimaryBtn")
            btn_batch.setMinimumWidth(160)
            btn_batch.clicked.connect(self._on_batch_create)
            btn_row.addWidget(btn_batch)
        if getattr(self, "_on_setup_wizard", None):
            btn_wiz = QPushButton(i18n.t("matrix_empty_btn_wizard"))
            fd_apply_low_freq_btn(btn_wiz)
            btn_wiz.setMinimumWidth(160)
            btn_wiz.clicked.connect(self._on_setup_wizard)
            btn_row.addWidget(btn_wiz)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        lay.addStretch()
        self.sl.addWidget(box, 1)

    def _load(self):
        # 骨架屏加载中，_do_real_load 已排队，避免重复调度
        if self._skeleton_active:
            return

        while self.sl.count():
            item = self.sl.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()

        self.cards = {}
        self.sections = []

        # 仅首次加载显示骨架屏，后续刷新直接走 _do_real_load
        if not self._initial_load_done:
            self._skeleton_active = True
            from ui_helpers import SkeletonCard

            cols = self._columns_for_viewport() or 4
            est_rows = 6
            for row_idx in range(est_rows):
                row_w = QWidget()
                row_l = QHBoxLayout(row_w)
                row_l.setSpacing(self._card_row_gap)
                row_l.setContentsMargins(0, 0, 0, 0)
                for col_idx in range(cols):
                    cw, ch = RoomCard._init_card_size(int(self.scroll.viewport().width()), viewport_w=int(self.scroll.viewport().width()))
                    sk = SkeletonCard(max(cw, 120), max(ch, 90))
                    row_l.addWidget(sk, 1)
                self.sl.addWidget(row_w)
            QTimer.singleShot(100, self._do_real_load)
            return

        # 后续刷新（房态变化/缩放重排）直接走真实加载
        self._do_real_load()

    def _do_real_load(self):
        self._skeleton_active = False

        while self.sl.count():
            item = self.sl.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                # 递归隐藏子控件并停止动画，再标记删除
                for child in widget.findChildren(QWidget):
                    if hasattr(child, '_anim') and child._anim:
                        child._anim.stop()
                    child.hide()
                widget.hide()
                widget.deleteLater()

        self.cards = {}
        self.sections = []
        rooms = self._fetch_rooms()
        if not rooms:
            self._add_empty_state()
            self._initial_load_done = True
            self._refresh_stats_strip()
            return

        floors = {}
        for room in rooms:
            floors.setdefault(str(room[1]), []).append(room)

        if (
            rooms
            and getattr(self, "_on_batch_create", None)
            and not self._external_chrome
        ):
            add_bar = QWidget()
            add_lay = QHBoxLayout(add_bar)
            add_lay.setContentsMargins(0, 0, 0, 2)
            add_lay.addStretch()
            btn_add = QPushButton(i18n.t("btn_add_room"))
            btn_add.setObjectName("SolidPrimaryBtn")
            btn_add.clicked.connect(self._on_batch_create)
            add_lay.addWidget(btn_add)
            self.sl.addWidget(add_bar)

        for floor, floor_rooms in floors.items():
            header = fd_section_bar(f"{self._floor_display(floor)}{i18n.t('table_floor')}  ·  {self._floor_summary_text(floor_rooms)}")
            self.sl.addWidget(header)

            row_widgets = []
            section_cards = []
            cols = self._columns_for_viewport()
            for index, room in enumerate(floor_rooms):
                if index % cols == 0:
                    row_widget = QWidget()
                    row_widget.setSizePolicy(
                        QSizePolicy.Policy.Expanding,
                        QSizePolicy.Policy.Minimum,
                    )
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setSpacing(self._card_row_gap)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    self.sl.addWidget(row_widget)
                    row_widgets.append(row_widget)

                card = RoomCard(
                    *room[:4],
                    guest_name=room[5],
                    guest_phone=room[6],
                    checkin_time=room[7],
                    note=room[4],
                    type_icon=room[8] if len(room) > 8 else "",
                    lock_no=room[9] if len(room) > 9 else "",
                    screen_w=int(self.scroll.viewport().width()),
                    viewport_w=int(self.scroll.viewport().width()),
                )
                card.selection_changed.connect(self._on_card_selection_changed)
                card.clicked.connect(self._emit_room_selected)
                if self._batch_mode:
                    card.set_batch_mode(True)
                    if room[0] in self._selected_rooms:
                        card.set_selected(True)
                self.cards[room[0]] = card
                section_cards.append(card)
                row_layout = row_widgets[-1].layout()
                row_layout.addWidget(card, 0)
            # 末行不拉宽：空白留在右侧，卡片保持固定磁贴尺寸
            if row_widgets:
                row_widgets[-1].layout().addStretch(1)

            self.sections.append({"header": header, "rows": row_widgets, "cards": section_cards})

        self._apply_visibility()
        self._populate_filter_combos()
        self._render_visible()
        self.sw.updateGeometry()
        self.scroll.updateGeometry()
        self._last_pack_vw = int(self.scroll.viewport().width())
        self._initial_load_done = True
        self._refresh_stats_strip()

    def _refresh_stats_strip(self) -> None:
        """顶栏合计带：从数据库刷新各状态计数。"""
        counts = db.get_room_status_counts()
        ready = int(counts.get("READY", 0))
        inhouse = int(counts.get("INHOUSE", 0))
        dirty = int(counts.get("DIRTY", 0))
        overtime = int(counts.get("OVERTIME", 0))
        maintenance = int(counts.get("MAINTENANCE", 0))
        known = {"READY", "INHOUSE", "DIRTY", "OVERTIME", "MAINTENANCE"}
        total = sum(int(n) for st, n in counts.items())
        values = {
            "total": total,
            "ready": ready,
            "inhouse": inhouse,
            "dirty": dirty,
            "overtime": overtime,
            "maintenance": maintenance,
        }
        emoji_map = {
            "total": "\U0001f4ca", "ready": "\U0001f7e2", "inhouse": "\U0001f535",
            "dirty": "\U0001f7e0", "overtime": "\U0001f534", "maintenance": "\U0001f7e3",
        }
        for key, lbl in self._stat_labels.items():
            label_key = self._stat_label_keys.get(key, key)
            emoji = emoji_map.get(key, "")
            lbl.setText(f"{emoji}  {i18n.t(label_key)} {values.get(key, 0)}")

    def _emit_room_selected(self, room_id: str, room_type: str):
        self.room_selected.emit(room_id, room_type)

    def _on_status(self, rid, st):
        if rid not in self.cards:
            self._load()
            return
        self.cards[rid].update_status(st)
        self._apply_visibility()
        self._refresh_stats_strip()

    # ── 批量操作方法 ──────────────────────────────────────────────────────────
    def enter_batch_mode(self):
        """进入批量选择模式"""
        self._batch_mode = True
        self._selected_rooms.clear()
        for card in self.cards.values():
            card.set_batch_mode(True)
        self._batch_toolbar.show()
        self._batch_count_lbl.setText(i18n.t("batch_count_label").format(count=0))
        self._btn_batch_mode.setText(i18n.t("btn_exit_batch"))

    def exit_batch_mode(self):
        """退出批量选择模式"""
        self._batch_mode = False
        self._selected_rooms.clear()
        for card in self.cards.values():
            card.set_batch_mode(False)
        self._batch_toolbar.hide()
        self._btn_batch_mode.setText(i18n.t("btn_batch_mode"))

    def _on_card_selection_changed(self, room_id: str, selected: bool):
        if selected:
            self._selected_rooms.add(room_id)
        else:
            self._selected_rooms.discard(room_id)
        self._update_batch_count()

    def _update_batch_count(self):
        count = len(self._selected_rooms)
        self._batch_count_lbl.setText(i18n.t("batch_count_label").format(count=count))
        # 已进入批量模式时，根据选中数自动显隐操作条
        if self._batch_mode:
            self._batch_toolbar.setVisible(count > 0)

    def _select_all(self):
        for room_id, card in self.cards.items():
            if card.isVisible():
                card.set_selected(True)
                self._selected_rooms.add(room_id)
        self._update_batch_count()

    def _deselect_all(self):
        for card in self.cards.values():
            card.set_selected(False)
        self._selected_rooms.clear()
        self._update_batch_count()

    def _batch_change_status(self):
        """批量改变房间状态"""
        if not self._selected_rooms:
            play_warn()
            show_warning(self, i18n.t("dlg_tip"), i18n.t("msg_batch_no_rooms"))
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("title_batch_status"))
        dlg.setMinimumWidth(320)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel(i18n.t("label_selected_count").format(count=len(self._selected_rooms))))

        combo = QComboBox()
        combo.addItems([i18n.t("combo_status_ready"), i18n.t("combo_status_maint"), i18n.t("combo_status_dirty")])
        layout.addWidget(combo)

        note_edit = QTextEdit()
        note_edit.setPlaceholderText(i18n.t("ph_note_optional"))
        note_edit.setMaximumHeight(80)
        layout.addWidget(note_edit)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton(i18n.t("btn_confirm_exec"))
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_cancel = QPushButton(i18n.t("btn_cancel"))
        fd_apply_low_freq_btn(btn_cancel)
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.Accepted:
            return

        status_map = {0: "READY", 1: "MAINTENANCE", 2: "DIRTY"}
        new_status = status_map.get(combo.currentIndex(), "READY")
        note = note_edit.toPlainText().strip()
        try:
            rows = db.execute(
                "SELECT room_id, status FROM rooms WHERE room_id IN (%s)" % ",".join("?" for _ in self._selected_rooms),
                tuple(self._selected_rooms),
            ).fetchall()
        except Exception as _exc:
            logger.error("_batch_change_status select error: %s", _exc)
            show_warning(self, i18n.t("dlg_tip"), str(_exc))
            return
        inhouse = [rid for rid, st in rows if st == "INHOUSE"]
        if inhouse:
            play_warn()
            show_warning(
                self,
                i18n.t("title_cant_batch_inhouse"),
                i18n.t("msg_inhouse_rooms").format(rooms=", ".join(inhouse)),
            )
            return
        if new_status in ("READY", "MAINTENANCE"):
            play_warn()
            if not ask_confirm(
                self,
                i18n.t("title_high_risk"),
                i18n.t("msg_batch_status_confirm").format(count=len(self._selected_rooms), status=new_status),
            ):
                return

        count = 0
        for room_id in list(self._selected_rooms):
            try:
                cur = db.execute(
                    "UPDATE rooms SET status=?, note=? WHERE room_id=? AND status <> 'INHOUSE'",
                    (new_status, note or None, room_id)
                )
                if (cur.rowcount or 0) != 1:
                    continue
                from event_bus import bus
                bus.room_status_changed.emit(room_id, new_status)
                db.log_action(_current_actor_id(), "BATCH_STATUS", f"{room_id} → {new_status}")
                count += 1
            except Exception as e:
                logger.warning("[BatchOp] %s 状态更新失败: %s", room_id, e)

        if count > 0:
            play_success()
        else:
            play_warn()
        show_info(
            self, i18n.t("title_done"),
            i18n.t("batch_op_done").format(count=count, status=new_status)
        )
        self.exit_batch_mode()

    def _batch_send_housekeeping(self):
        """批量派发保洁任务"""
        if not self._selected_rooms:
            play_warn()
            show_warning(self, i18n.t("dlg_tip"), i18n.t("msg_batch_no_rooms"))
            return

        # 只对 DIRTY 或 READY 状态的房间派保洁
        target_rooms = [
            rid for rid in self._selected_rooms
            if rid in self.cards and self.cards[rid].status in ("DIRTY", "READY", "OVERTIME")
        ]

        if not target_rooms:
            play_warn()
            show_warning(
                self, i18n.t("dlg_tip"),
                i18n.t("msg_hk_no_dirty")
            )
            return

        # 获取员工列表
        try:
            staff_list = db.execute(
                "SELECT staff_id, name FROM staff_roster WHERE role IN ('保洁','housekeeping','cleaner') "
                "UNION SELECT staff_id, name FROM staff_roster LIMIT 10"
            ).fetchall()
        except Exception as _exc:
            logger.error("_batch_send_housekeeping staff list error: %s", _exc)
            staff_list = []

        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("title_batch_hk"))
        dlg.setMinimumWidth(320)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel(i18n.t("label_hk_room_list").format(count=len(target_rooms), rooms=", ".join(target_rooms))))

        combo = QComboBox()
        combo.addItem(i18n.t("combo_auto_assign"), "system")
        for sid, sname in staff_list:
            combo.addItem(f"{sname} ({sid})", sid)
        layout.addWidget(QLabel(i18n.t("label_assign_to")))
        layout.addWidget(combo)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton(i18n.t("btn_dispatch"))
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_cancel = QPushButton(i18n.t("btn_cancel"))
        fd_apply_low_freq_btn(btn_cancel)
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.Accepted:
            return

        cleaner_id = combo.currentData() or "system"
        count = 0
        for room_id in target_rooms:
            try:
                # 获取房型
                room_row = db.execute(
                    "SELECT room_type FROM rooms WHERE room_id=?", (room_id,)
                ).fetchone()
                room_type = room_row[0] if room_row else "standard"
                # 触发保洁完成信号（前台手动派发，标记为已派）
                db.log_action(_current_actor_id(), "BATCH_HK_DISPATCH",
                              f"{room_id} 派保洁给 {cleaner_id}")
                # 发送 Telegram 通知
                try:
                    from telegram_shadow import telegram_thread
                    if telegram_thread and telegram_thread.isRunning():
                        telegram_thread.send_alert_sync(
                            f"[批量保洁派发]\n房间: {room_id}\n指派: {cleaner_id}"
                        )
                except Exception:
                    pass
                count += 1
            except Exception as e:
                logger.warning("[BatchHK] %s 派保洁失败: %s", room_id, e)

        if count > 0:
            play_success()
        else:
            play_warn()
        show_info(self, i18n.t("title_done"), i18n.t("msg_hk_done").format(count=count))
        self.exit_batch_mode()

    def filter_rooms(self, ft):
        key = str(ft or "ALL").strip().upper()
        self.current_filter = key if key in _VALID_MATRIX_FILTERS else "ALL"
        self._apply_visibility()

    def search_rooms(self, txt):
        # 仅空白时视为无搜索，避免 "" 以外仍过滤掉全部卡片导致「有房态统计但矩阵空白」
        self.current_search = (txt or "").strip().upper()
        self._apply_visibility()

    def _apply_visibility(self):
        raw_ft = str(getattr(self, "current_filter", "ALL") or "ALL").strip().upper()
        ft = raw_ft if raw_ft in _VALID_MATRIX_FILTERS else "ALL"
        srch = str(getattr(self, "current_search", "") or "").strip().upper()
        intended_visible = set()
        for card in self.cards.values():
            card_st = str(card.status or "READY").upper()
            status_match = (ft == "ALL") or (card_st == ft)
            haystack = " ".join(
                [
                    str(card.room_id or "").upper(),
                    str(card.room_type or "").upper(),
                    str(card.floor or "").upper(),
                    str(card.guest_name or "").upper(),
                    str(card.guest_phone or "").upper(),
                    str(card.note or "").upper(),
                ]
            )
            search_match = (srch == "") or (srch in haystack)
            should_show = status_match and search_match
            card.setVisible(should_show)
            if should_show:
                intended_visible.add(card)

        for section in self.sections:
            any_visible = False
            for row in section["rows"]:
                visible_in_row = False
                for i in range(row.layout().count()):
                    item = row.layout().itemAt(i)
                    widget = item.widget() if item else None
                    if widget in intended_visible:
                        visible_in_row = True
                        any_visible = True
                row.setVisible(visible_in_row)
            section["header"].setVisible(any_visible)

    # ── 智能筛选栏方法 ────────────────────────────────────────────────────────

    def _apply_filter(self, code: str):
        """按状态筛选房间卡片。选中态用 gold_thread 底边指示线。"""
        self.current_filter = code
        for c, chip in self._filter_chips.items():
            chip.setChecked(c == code)
        self._render_visible()

    def _apply_combined_filter(self, text: str):
        """综合筛选器：选项格式"楼层:2"或"房型:标准间(std)"或"全部"。"""
        if text in ("", i18n.t("filter_all"), "所有"):
            self._current_floor_filter = ""
            self._current_type_filter = ""
        elif text.startswith("楼层:"):
            self._current_floor_filter = text[3:].strip()
            self._current_type_filter = ""
        elif text.startswith("房型:"):
            self._current_floor_filter = ""
            # 提取 type_id（括号中）
            import re
            m = re.search(r'\(([^)]+)\)', text)
            self._current_type_filter = m.group(1) if m else ""
        self._render_visible()

    def _on_search_inline_changed(self, text: str):
        self.current_search = text.strip()
        self._render_visible()

    def _render_visible(self):
        """根据当前筛选条件显示/隐藏卡片（status + floor + type + search）。"""
        for room_id, card in self.cards.items():
            status = card.status
            floor = card.floor
            rtype = card.room_type

            pass_filter = (self.current_filter == "ALL" or status == self.current_filter)
            pass_floor = (not self._current_floor_filter or str(floor) == self._current_floor_filter)
            pass_type = (not self._current_type_filter or str(rtype) == self._current_type_filter)
            pass_search = (not self.current_search or self.current_search.upper() in str(room_id).upper())

            card.setVisible(pass_filter and pass_floor and pass_type and pass_search)

    def _populate_filter_combos(self):
        """从房间数据填充综合筛选器（楼层 + 房型合并）。"""
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        self._filter_combo.addItem(i18n.t("filter_all"))

        try:
            floors = db.execute("SELECT DISTINCT floor FROM rooms ORDER BY floor").fetchall()
        except Exception as _exc:
            logger.error("_populate_filter_combos floors error: %s", _exc)
            floors = []
        for r in floors:
            if r[0]:
                self._filter_combo.addItem(f"楼层:{r[0]}")

        try:
            types = db.execute("SELECT DISTINCT type_id, type_name FROM room_type_templates ORDER BY type_name").fetchall()
        except Exception as _exc:
            logger.error("_populate_filter_combos types error: %s", _exc)
            types = []
        for t in types:
            self._filter_combo.addItem(f"房型:{t[1]}({t[0]})")

        self._filter_combo.blockSignals(False)

    # ── 矩阵缩放 (Ctrl+滚轮) ──────────────────────────────────────────────────
    def wheelEvent(self, event):
        """Ctrl+滚轮缩放房卡大小。"""
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                RoomCard.CARD_W = min(280, RoomCard.CARD_W + 20)
                RoomCard.CARD_H = min(200, RoomCard.CARD_H + 14)
            elif delta < 0:
                RoomCard.CARD_W = max(120, RoomCard.CARD_W - 20)
                RoomCard.CARD_H = max(90, RoomCard.CARD_H - 14)
            for card in self.cards.values():
                card.setFixedSize(RoomCard.CARD_W, RoomCard.CARD_H)
                if hasattr(card, '_apply_card_style'):
                    card._apply_card_style()
            self._repack_timer.start(80)
            return
        super().wheelEvent(event)

    def _on_theme_changed(self):
        """切换主题后重刷所有房卡 inline style。"""
        for card in self.cards.values():
            card._apply_card_style()
