# qr_code_service.py — 客房服务二维码模块（完整重写）
# 每间房生成专属二维码（含 room_id + token）
# 客人扫码 → 打开机器人菜单
# 支持：叫早服务/加床/点餐/投诉
# 前台实时收到通知
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QFont, QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from database import db
from design_tokens import _p
from ui_helpers import style_dialog, make_dialog_scroll_area, build_dialog_header, show_warning, show_info, ask_confirm
from frontdesk_ui import fd_apply_action_btn
from event_bus import bus
import logging
logger = logging.getLogger(__name__)

# ─── 活码：房间贴纸固定，机器人不固定 ───────────────────────────────────────────
class LiveQrNotReadyError(Exception):
    """未配置云端或活码同步失败时抛出（房间码禁止生成直连 t.me）"""


# ─── 常量 ────────────────────────────────────────────────────────────────────
SERVICE_TYPES = {
    "wakeup":    ("⏰", "叫早服务", "请设置叫早时间"),
    "extra_bed": ("🛏️", "加床服务", "需要加床/加被"),
    "room_svc":  ("🍽️", "客房点餐", "需要送餐服务"),
    "complaint": ("📢", "意见投诉", "有问题需要反映"),
    "clean":     ("🧹", "请求保洁", "需要打扫房间"),
    "checkout":  ("🚪", "申请退房", "准备办理退房"),
    "other":     ("💬", "其他需求", "其他服务需求"),
}


# ─── 数据库初始化 ─────────────────────────────────────────────────────────────
def _ensure_qr_tables():
    """确保二维码相关表存在"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS room_qr_tokens (
            room_id   TEXT PRIMARY KEY,
            token     TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS guest_service_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id     TEXT NOT NULL,
            service_type TEXT NOT NULL,
            message     TEXT,
            status      TEXT DEFAULT 'PENDING',
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            handled_at  TEXT,
            handler_id  TEXT
        )
    """)


# ─── Token 管理 ───────────────────────────────────────────────────────────────
class QRTokenService:
    """房间二维码令牌管理"""

    @staticmethod
    def get_or_create_token(room_id: str) -> str:
        """获取或创建房间专属 token"""
        _ensure_qr_tables()
        row = db.execute(
            "SELECT token FROM room_qr_tokens WHERE room_id=?", (room_id,)
        ).fetchone()
        if row:
            return row[0]
        token = secrets.token_urlsafe(16)
        db.execute(
            "INSERT OR REPLACE INTO room_qr_tokens(room_id, token) VALUES(?,?)",
            (room_id, token)
        )
        QRTokenService._maybe_sync_live(room_id, token)
        return token

    @staticmethod
    def refresh_token(room_id: str) -> str:
        """刷新房间 token（退房后调用，防止旧二维码被滥用）"""
        _ensure_qr_tables()
        token = secrets.token_urlsafe(16)
        db.execute(
            "INSERT OR REPLACE INTO room_qr_tokens(room_id, token, created_at) "
            "VALUES(?, ?, datetime('now','localtime'))",
            (room_id, token)
        )
        QRTokenService._maybe_sync_live(room_id, token)
        return token

    @staticmethod
    def _maybe_sync_live(room_id: str, token: str) -> None:
        try:
            from live_qr_client import is_live_qr_enabled, sync_single_room
            if is_live_qr_enabled():
                sync_single_room(room_id, token)
        except Exception as exc:
            logger.warning("[QR] live sync skip: %s", exc)

    @staticmethod
    def verify_token(room_id: str, token: str) -> bool:
        """验证 token 是否有效"""
        _ensure_qr_tables()
        row = db.execute(
            "SELECT token FROM room_qr_tokens WHERE room_id=?", (room_id,)
        ).fetchone()
        return row is not None and row[0] == token

    @staticmethod
    def build_qr_url(room_id: str) -> str:
        """
        房间贴纸内容：固定活码 https://{云端}/r/{8位码}
        不含机器人名；客人扫码时云端按「当前酒店绑定的机器人」跳转。
        """
        from live_qr_client import is_live_qr_enabled, get_live_url_for_room, sync_single_room

        if not is_live_qr_enabled():
            raise LiveQrNotReadyError(
                "房间二维码须使用活码（贴纸固定、机器人可变）。\n"
                "请先在设置中配置云端地址，并在厂家后台完成酒店注册。"
            )
        token = QRTokenService.get_or_create_token(room_id)
        live = get_live_url_for_room(room_id) or sync_single_room(room_id, token)
        if not live:
            raise LiveQrNotReadyError(
                "活码同步失败。请确认酒店已在云端注册，或在厂家面板执行「同步全部房间活码」。"
            )
        return live


# ─── 服务请求管理 ─────────────────────────────────────────────────────────────
class GuestServiceManager:
    """客人服务请求管理"""

    @staticmethod
    def create_request(room_id: str, service_type: str, message: str = "") -> int:
        """创建服务请求，返回请求ID"""
        _ensure_qr_tables()
        db.execute(
            "INSERT INTO guest_service_requests(room_id, service_type, message) VALUES(?,?,?)",
            (room_id, service_type, message)
        )
        row = db.execute("SELECT last_insert_rowid()").fetchone()
        req_id = row[0] if row else 0

        # 发送通知前台
        svc = SERVICE_TYPES.get(service_type, ("💬", service_type, ""))
        emoji, svc_name, _ = svc
        msg = (
            f"{emoji} [客房服务请求]\n"
            f"房间: {room_id}\n"
            f"服务: {svc_name}\n"
            f"备注: {message or '无'}\n"
            f"时间: {datetime.now().strftime('%H:%M')}"
        )
        GuestServiceManager._send_tg(msg)

        # 触发事件总线（前台界面实时提示）
        bus.show_warning.emit(f"🔔 {room_id} 客人请求: {svc_name}", "")

        return req_id

    @staticmethod
    def get_pending_requests() -> list:
        """获取所有待处理请求"""
        _ensure_qr_tables()
        return db.execute(
            "SELECT id, room_id, service_type, message, status, created_at "
            "FROM guest_service_requests WHERE status='PENDING' ORDER BY id DESC"
        ).fetchall()

    @staticmethod
    def get_all_requests(limit: int = 100) -> list:
        """获取所有请求"""
        _ensure_qr_tables()
        return db.execute(
            "SELECT id, room_id, service_type, message, status, created_at "
            "FROM guest_service_requests ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()

    @staticmethod
    def mark_handled(req_id: int, handler_id: str = "frontdesk"):
        """标记请求已处理"""
        db.execute(
            "UPDATE guest_service_requests SET status='DONE', "
            "handled_at=datetime('now','localtime'), handler_id=? WHERE id=?",
            (handler_id, req_id)
        )

    @staticmethod
    def _send_tg(msg: str):
        try:
            from telegram_shadow import telegram_thread
            if telegram_thread and telegram_thread.isRunning():
                telegram_thread.send_alert_sync(msg)
        except Exception as e:
            logger.warning("[QR] Telegram 发送失败: %s", e)


# ─── Telegram Bot 命令处理（注册到 telegram_shadow） ─────────────────────────
def handle_room_start(payload: str, chat_id: str) -> str:
    """
    处理 /start room_{room_id}_{token} 命令
    返回欢迎消息文本（由 telegram_shadow 发送）
    """
    try:
        parts = payload.split("_", 2)
        if len(parts) < 3 or parts[0] != "room":
            return ""
        room_id = parts[1]
        token = parts[2]

        if not QRTokenService.verify_token(room_id, token):
            return f"❌ 二维码已失效，请联系前台重新获取。"

        # 查询当前住客
        guest = db.execute(
            "SELECT name FROM guests WHERE room_id=? AND status='INHOUSE'",
            (room_id,)
        ).fetchone()
        guest_name = guest[0] if guest else "尊贵的客人"

        hotel_name = db.get_config("hotel_name") or "酒店"

        return (
            f"🏨 欢迎入住 {hotel_name}！\n"
            f"您好，{guest_name}！\n"
            f"房间号: {room_id}\n\n"
            f"请选择您需要的服务：\n"
            f"⏰ /wakeup - 叫早服务\n"
            f"/extrabed - 加床服务\n"
            f"/roomsvc - 客房点餐\n"
            f"/clean - 请求保洁\n"
            f"/checkout - 申请退房\n"
            f"📢 /complaint - 意见投诉\n"
            f"💬 /other - 其他需求\n\n"
            f"如需紧急帮助，请直接拨打前台电话。"
        )
    except Exception as e:
        logger.warning("[QR] handle_room_start 失败: %s", e)
        return ""


def handle_room_service_command(command: str, room_id: str, message: str = "") -> str:
    """处理客房服务命令，返回确认消息"""
    cmd_map = {
        "/wakeup": "wakeup",
        "/extrabed": "extra_bed",
        "/roomsvc": "room_svc",
        "/clean": "clean",
        "/checkout": "checkout",
        "/complaint": "complaint",
        "/other": "other",
    }
    service_type = cmd_map.get(command, "other")
    req_id = GuestServiceManager.create_request(room_id, service_type, message)
    svc = SERVICE_TYPES.get(service_type, ("💬", "服务", ""))
    return (
        f"{svc[0]} 您的{svc[1]}请求已收到！\n"
        f"请求编号: #{req_id}\n"
        f"前台将尽快为您处理，请稍候。"
    )


# ─── 二维码生成工具 ───────────────────────────────────────────────────────────
def generate_qr_pixmap(url: str, size: int = 200) -> Optional[QPixmap]:
    """生成二维码图像，失败返回空"""
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        tmp = tempfile.mktemp(suffix=".png")
        img.save(tmp)
        pix = QPixmap(tmp).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        os.unlink(tmp)
        return pix
    except ImportError:
        logger.warning("[QR] qrcode 库未安装，请运行: pip install qrcode[pil]")
        return None
    except Exception as e:
        logger.warning("[QR] 生成失败: %s", e)
        return None


def save_qr_to_file(room_id: str, save_path: str) -> bool:
    """将房间二维码保存为 PNG 文件（固定活码链接）"""
    try:
        url = QRTokenService.build_qr_url(room_id)
    except LiveQrNotReadyError:
        return False
    pix = generate_qr_pixmap(url, 400)
    if pix:
        return pix.save(save_path, "PNG")
    return False


# ─── UI：单房间二维码对话框 ───────────────────────────────────────────────────
class QRCodePanel(QDialog):
    """单房间二维码生成对话框（兼容旧版调用方式）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("房间二维码 / 立牌")
        style_dialog(self, size="medium")
        self._current_url = ""
        self._standee_hint = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(build_dialog_header("🔲 房间专属二维码", "【固定】房门贴纸印活码链接，一次印刷长期使用。\n【不固定】机器人由厂家在云端绑定，换机器人不用重印贴纸。"))

        from room_standee_renderer import build_standee_hint_widget
        self._standee_hint = build_standee_hint_widget(self)
        layout.addWidget(self._standee_hint)

        # 输入行
        input_row = QHBoxLayout()
        self.te = QLineEdit(placeholderText="请输入房间号")
        self.te.setStyleSheet(f"border:1px solid {_p('border')}; border-radius:8px; padding:6px 10px;")
        input_row.addWidget(self.te)
        btn_gen = QPushButton("生成")
        btn_gen.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_gen, primary=True)
        btn_gen.clicked.connect(self._gen)
        input_row.addWidget(btn_gen)
        layout.addLayout(input_row)

        # 二维码显示区
        self.lb = QLabel("等待生成")
        self.lb.setMinimumSize(220, 220)
        self.lb.setAlignment(Qt.AlignCenter)
        self.lb.setStyleSheet(
            f"background:{_p('surface')}; border:2px dashed {_p('border')}; "
            f"border-radius:14px; color:{_p('text_muted')}; font-size:13px;"
        )
        layout.addWidget(self.lb, alignment=Qt.AlignCenter)

        # URL 显示
        self.url_lbl = QLabel()
        self.url_lbl.setWordWrap(True)
        self.url_lbl.setStyleSheet(f"color:{_p('text_dim')}; font-size:10px;")
        self.url_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.url_lbl)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾 保存图片")
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_save.setStyleSheet(
            f"background:{_p('amount_positive')}; color:white; border-radius:8px; padding:6px 14px; font-weight:700;"
        )
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)

        btn_refresh = QPushButton("🔄 刷新令牌")
        btn_refresh.setObjectName("FdGhostBtn")
        btn_refresh.setStyleSheet(
            f"background:{_p('accent')}; color:white; border-radius:8px; padding:6px 14px; font-weight:700;"
        )
        btn_refresh.clicked.connect(self._refresh_token)
        btn_row.addWidget(btn_refresh)

        btn_standee = QPushButton("🪧 导出 A6 立牌")
        btn_standee.setObjectName("FdGhostBtn")
        btn_standee.setStyleSheet(
            f"background:{_p('primary')}; color:white; border-radius:8px; padding:6px 14px; font-weight:700;"
        )
        btn_standee.clicked.connect(self._export_standee)
        btn_row.addWidget(btn_standee)

        btn_tpl = QPushButton("📁 立牌模板文件夹")
        btn_tpl.setObjectName("FdGhostBtn")
        btn_tpl.setStyleSheet(
            f"background:{_p('surface_alt')}; color:{_p('text_muted')}; border-radius:8px; padding:6px 14px;"
        )
        btn_tpl.clicked.connect(self._open_standee_folder)
        btn_row.addWidget(btn_tpl)

        btn_close = QPushButton("关闭")
        btn_close.setObjectName("FdGhostBtn")
        btn_close.setStyleSheet(
            f"background:{_p('surface_alt')}; color:{_p('text_muted')}; border-radius:8px; padding:6px 14px;"
        )
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _gen(self):
        rid = self.te.text().strip()
        if not rid:
            return
        try:
            url = QRTokenService.build_qr_url(rid)
        except LiveQrNotReadyError as e:
            self._current_url = ""
            self.url_lbl.setText("")
            self.lb.setText(f"{e}")
            return
        self._current_url = url
        self.url_lbl.setText(url + "\n（活码固定 · 机器人由厂家后台指定）")
        pix = generate_qr_pixmap(url, 220)
        if pix:
            self.lb.setPixmap(pix)
        else:
            self.lb.setText("二维码库未安装\npip install qrcode[pil]")

    def _save(self):
        rid = self.te.text().strip()
        if not rid or not self._current_url:
            show_warning(self, "提示", "请先生成二维码")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存二维码", f"room_{rid}_qr.png", "PNG 图片 (*.png)"
        )
        if path:
            ok = save_qr_to_file(rid, path)
            if ok:
                show_info(self, "成功", f"二维码已保存到:\n{path}")
            else:
                show_warning(self, "失败", "保存失败，请检查 qrcode 库是否安装")

    def _refresh_token(self):
        rid = self.te.text().strip()
        if not rid:
            return
        QRTokenService.refresh_token(rid)
        self._gen()
        show_info(
            self, "成功",
            f"房间 {rid} 安全令牌已刷新（旧客人链接失效）。\n"
            f"房门贴纸上的活码链接不变，无需重印。",
        )

    def _export_standee(self):
        rid = self.te.text().strip()
        if not rid or not self._current_url:
            show_warning(self, "提示", "请先生成活码二维码")
            return
        from room_standee_renderer import save_standee_png, standee_assets_dir, open_standee_folder
        default_name = f"room_{rid}_standee_A6.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 A6 立牌", default_name, "PNG 图片 (*.png)"
        )
        if path and save_standee_png(rid, self._current_url, path):
            show_info(
                self, "成功",
                f"A6 立牌已保存：\n{path}\n\n规格：148×105mm 横版 @300DPI\n可直接送打印店或热敏打印机。",
            )

    def _open_standee_folder(self):
        from room_standee_renderer import open_standee_folder
        open_standee_folder()


# ─── UI：批量二维码打印对话框 ─────────────────────────────────────────────────
class BatchQRDialog(QDialog):
    """批量生成所有房间二维码"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量生成房间二维码")
        style_dialog(self, size="large")
        self._standee_hint = None
        self._build_ui()
        self._load_rooms()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(build_dialog_header("🔲 批量房间二维码", "每房一个固定活码，贴房门即可。机器人在厂家云端更换，无需重印。"))

        from room_standee_renderer import build_standee_hint_widget
        self._standee_hint = build_standee_hint_widget(self)
        layout.addWidget(self._standee_hint)

        # 工具栏
        toolbar = QHBoxLayout()
        btn_gen_all = QPushButton("生成全部")
        btn_gen_all.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_gen_all, primary=True)
        btn_gen_all.clicked.connect(self._gen_all)
        toolbar.addWidget(btn_gen_all)

        btn_save_all = QPushButton("💾 批量保存")
        btn_save_all.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_save_all, primary=True)
        btn_save_all.clicked.connect(self._save_all)
        toolbar.addWidget(btn_save_all)

        btn_refresh_all = QPushButton("🔄 刷新全部令牌")
        btn_refresh_all.setObjectName("FdGhostBtn")
        fd_apply_action_btn(btn_refresh_all)
        btn_refresh_all.clicked.connect(self._refresh_all)
        toolbar.addWidget(btn_refresh_all)

        btn_standee_all = QPushButton("🪧 导出全部 A6")
        btn_standee_all.setObjectName("FdGhostBtn")
        fd_apply_action_btn(btn_standee_all)
        btn_standee_all.clicked.connect(self._export_all_standees)
        toolbar.addWidget(btn_standee_all)

        btn_a4_all = QPushButton("🖨️ 导出 A4 拼版")
        btn_a4_all.setObjectName("FdGhostBtn")
        fd_apply_action_btn(btn_a4_all)
        btn_a4_all.clicked.connect(self._export_all_a4)
        toolbar.addWidget(btn_a4_all)

        btn_tpl = QPushButton("📁 立牌模板")
        btn_tpl.setObjectName("FdGhostBtn")
        btn_tpl.clicked.connect(self._open_standee_folder)
        toolbar.addWidget(btn_tpl)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 滚动区域显示二维码网格
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(16)
        scroll.setWidget(self.grid_widget)
        layout.addWidget(scroll)

        btn_close = QPushButton("关闭")
        btn_close.setObjectName("FdGhostBtn")
        btn_close.setStyleSheet(
            f"background:{_p('surface_alt')}; color:{_p('text_muted')}; border-radius:8px; padding:6px 14px;"
        )
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignRight)

    def _load_rooms(self):
        self._rooms = db.execute(
            "SELECT room_id, floor, room_type FROM rooms ORDER BY floor, room_id"
        ).fetchall()

    def _gen_all(self):
        """生成所有房间二维码并显示"""
        try:
            from live_qr_client import is_live_qr_enabled, sync_all_rooms_from_db
            if not is_live_qr_enabled():
                show_warning(
                    self, "需要云端活码",
                    "批量打印须先配置云端地址。\n房间贴纸为固定活码，机器人由厂家后台绑定。",
                )
                return
            sync_all_rooms_from_db()
        except Exception as e:
            show_warning(self, "同步失败", str(e))
            return
        # 清空网格
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        cols = 4
        for idx, (room_id, floor, room_type) in enumerate(self._rooms):
            card = self._make_qr_card(room_id, floor, room_type)
            self.grid_layout.addWidget(card, idx // cols, idx % cols)

    def _make_qr_card(self, room_id: str, floor, room_type: str) -> QFrame:
        """创建单个房间二维码卡片"""
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background:{_p('surface_alt')}; border:1px solid {_p('border')}; border-radius:12px; }}"
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(10, 10, 10, 10)
        fl.setSpacing(6)

        # 房间号标题
        room_lbl = QLabel(f"{room_id}")
        room_lbl.setStyleSheet(f"font-size:14px; font-weight:700; color:{_p('text')};")
        room_lbl.setAlignment(Qt.AlignCenter)
        fl.addWidget(room_lbl)

        try:
            url = QRTokenService.build_qr_url(room_id)
        except LiveQrNotReadyError:
            url = ""
        pix = generate_qr_pixmap(url, 120) if url else None
        qr_lbl = QLabel()
        qr_lbl.setFixedSize(120, 120)
        qr_lbl.setAlignment(Qt.AlignCenter)
        if pix:
            qr_lbl.setPixmap(pix)
        else:
            qr_lbl.setText("未安装\nqrcode")
            qr_lbl.setStyleSheet(f"color:{_p('text_dim')}; font-size:10px;")
        fl.addWidget(qr_lbl, alignment=Qt.AlignCenter)

        # 房型信息
        type_lbl = QLabel(f"{floor}层 · {room_type}")
        type_lbl.setStyleSheet(f"color:{_p('text_muted')}; font-size:10px;")
        type_lbl.setAlignment(Qt.AlignCenter)
        fl.addWidget(type_lbl)

        return frame

    def _save_all(self):
        """批量保存所有二维码到文件夹"""
        folder = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
        if not folder:
            return
        count = 0
        for room_id, _, _ in self._rooms:
            path = os.path.join(folder, f"room_{room_id}_qr.png")
            if save_qr_to_file(room_id, path):
                count += 1
        show_info(self, "完成", f"已保存 {count} 个二维码到:\n{folder}")

    def _refresh_all(self):
        """刷新所有房间令牌"""
        if not ask_confirm(
            self, "确认",
            "将刷新各房安全令牌（已退房客人无法再扫旧会话）。\n"
            "房门活码链接 /r/xxxx 不变，无需重印贴纸。确认继续？",
        ):
            return
        for room_id, _, _ in self._rooms:
            QRTokenService.refresh_token(room_id)
        show_info(self, "完成", "所有房间令牌已刷新")

    def _export_all_standees(self):
        from room_standee_renderer import export_all_a6, standee_assets_dir, open_standee_folder
        folder = QFileDialog.getExistingDirectory(self, "选择 A6 立牌保存文件夹")
        if not folder:
            return
        try:
            from live_qr_client import is_live_qr_enabled, sync_all_rooms_from_db
            if is_live_qr_enabled():
                sync_all_rooms_from_db()
        except Exception:
            pass
        n_ok, n_total = export_all_a6(folder, self._rooms)
        show_info(
            self, "导出完成",
            f"已导出 {n_ok}/{n_total} 张 A6 立牌到：\n{folder}\n\n"
            f"规格：148×105mm 横版 @300DPI\n"
            f"文件名：room_房号_standee.png\n"
            f"（活码固定，换机器人不用重印立牌）",
        )

    def _export_all_a4(self):
        from room_standee_renderer import export_all_a4, standee_assets_dir
        folder = QFileDialog.getExistingDirectory(self, "选择 A4 拼版保存文件夹")
        if not folder:
            return
        try:
            from live_qr_client import is_live_qr_enabled, sync_all_rooms_from_db
            if is_live_qr_enabled():
                sync_all_rooms_from_db()
        except Exception:
            pass
        n_sheets, n_total = export_all_a4(folder, self._rooms)
        show_info(
            self, "导出完成",
            f"已导出 {n_sheets} 张 A4 拼版（共 {n_total} 个房间）到：\n{folder}\n\n"
            f"规格：A4 竖版 @300DPI，每张 2 个 A6 立牌\n"
            f"带裁切线，办公室打印机直接打出来裁开即可。",
        )

    def _open_standee_folder(self):
        from room_standee_renderer import open_standee_folder
        open_standee_folder()


# ─── UI：服务请求管理面板 ─────────────────────────────────────────────────────
class ServiceRequestPanel(QWidget):
    """前台服务请求管理面板（可嵌入工作区或独立使用）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ServiceRequestPanel")
        _ensure_qr_tables()
        self._build_ui()
        QTimer.singleShot(0, self.refresh)

        # 每30秒自动刷新
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(30000)

        # 监听事件总线
        bus.show_warning.connect(self._on_new_request)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 标题行
        title_row = QHBoxLayout()
        title = QLabel("🔔 客房服务请求")
        title.setObjectName("FdRoomBanner")
        title_row.addWidget(title)
        title_row.addStretch()

        self.pending_badge = QLabel("0 待处理")
        self.pending_badge.setObjectName("PendingBadge")
        self.pending_badge.setProperty("pending", False)
        title_row.addWidget(self.pending_badge)
        layout.addLayout(title_row)

        # 工具栏
        toolbar = QHBoxLayout()
        btn_refresh = QPushButton("🔄 刷新")
        btn_refresh.setObjectName("FdActSecondary")
        btn_refresh.clicked.connect(self.refresh)
        toolbar.addWidget(btn_refresh)

        btn_handle = QPushButton("✅ 标记已处理")
        btn_handle.setObjectName("FdActSecondary")
        btn_handle.clicked.connect(self._mark_handled)
        toolbar.addWidget(btn_handle)

        btn_qr = QPushButton("🔲 生成二维码")
        btn_qr.setObjectName("FdActSecondary")
        btn_qr.clicked.connect(self._open_qr_panel)
        toolbar.addWidget(btn_qr)

        btn_batch = QPushButton("📋 批量二维码")
        btn_batch.setObjectName("FdActSecondary")
        btn_batch.clicked.connect(self._open_batch_qr)
        toolbar.addWidget(btn_batch)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 请求列表（ContentBox 包裹）
        table_box = QFrame()
        table_box.setObjectName("ContentBox")
        from ui_surface import fd_apply_content_box, fd_apply_table_palette
        fd_apply_content_box(table_box)
        tb_lay = QVBoxLayout(table_box)
        tb_lay.setContentsMargins(10, 10, 10, 10)
        from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["ID", "房间", "服务类型", "备注", "状态", "时间"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 60)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 150)
        self.table.setColumnWidth(4, 70)
        self.table.setColumnWidth(5, 130)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        tb_lay.addWidget(self.table)
        fd_apply_table_palette(self.table)
        layout.addWidget(table_box)
        self._table_box = table_box

        from ui_surface import fd_connect_theme_refresh, fd_refresh_surfaces
        fd_refresh_surfaces(self)
        fd_connect_theme_refresh(self)

    def refresh(self) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
        from PySide6.QtGui import QColor

        _ensure_qr_tables()
        rows = GuestServiceManager.get_all_requests(50)
        self.table.setRowCount(len(rows))

        pending_count = 0
        for r, row in enumerate(rows):
            req_id, room_id, svc_type, msg, status, created = row
            svc = SERVICE_TYPES.get(svc_type, ("💬", svc_type, ""))
            svc_label = f"{svc[0]} {svc[1]}"
            status_label = "待处理" if status == "PENDING" else "已处理"
            if status == "PENDING":
                pending_count += 1

            vals = [str(req_id), room_id, svc_label, msg or "-", status_label, created[:16] if created else "-"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 4:
                    if status == "PENDING":
                        item.setForeground(QColor(_p('danger')))
                    else:
                        item.setForeground(QColor(_p('amount_positive')))
                self.table.setItem(r, c, item)

        self.pending_badge.setText(f"{pending_count} 待处理")
        self.pending_badge.setProperty("pending", pending_count > 0)
        self.pending_badge.style().unpolish(self.pending_badge)
        self.pending_badge.style().polish(self.pending_badge)

    def _refresh_theme_styles(self) -> None:
        from ui_surface import fd_apply_content_box, fd_apply_table_palette

        if hasattr(self, "_table_box"):
            fd_apply_content_box(self._table_box)
        fd_apply_table_palette(self.table)
        self.refresh()

    def _mark_handled(self):
        row = self.table.currentRow()
        if row < 0:
            show_warning(self, "提示", "请先选择一条请求")
            return
        item = self.table.item(row, 0)
        if not item:
            return
        req_id = int(item.text())
        GuestServiceManager.mark_handled(req_id)
        self.refresh()

    def _open_qr_panel(self):
        dlg = QRCodePanel(self)
        dlg.exec()

    def _open_batch_qr(self):
        dlg = BatchQRDialog(self)
        dlg.exec()

    def _on_new_request(self, msg: str, _: str):
        """收到新服务请求时刷新"""
        if "客人请求" in msg:
            self.refresh()
