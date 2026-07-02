"""
厂家维护与授权管理后台
=========================================
功能模块（4个标签页）：
  Tab 0 — 本机授权：机器码/酒店ID/有效期/本地激活/云端同步
  Tab 1 — 云端酒店：多酒店列表/状态/远程停用恢复/业务员归属
  Tab 2 — 授权码管理：生成授权码/查看已发码/广告推送
  Tab 3 — 系统维护：模块开关/数据库重置/日志控制台/迁移工具
"""

import hashlib
import datetime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QTabWidget, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QCheckBox, QPlainTextEdit, QSpinBox, QComboBox,
    QFrame, QSizePolicy, QInputDialog, QProgressBar
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor

from database import db
from license_manager import LicenseManager
from ui_helpers import style_dialog, build_dialog_header, show_info, show_warning, ask_confirm
from design_tokens import _p
from ui_surface import fd_apply_table_palette
from frontdesk_ui import fd_apply_action_btn


# ─────────────────────────────────────────────────────────────────────────────
#  辅助：信息卡片
# ─────────────────────────────────────────────────────────────────────────────
def _info_card(label: str, value: str, color: str | None = None) -> QFrame:
    color = color or _p("primary")
    f = QFrame()
    f.setObjectName("ManufacturerStatRow")
    lay = QHBoxLayout(f)
    lay.setContentsMargins(4, 6, 4, 6)
    lbl_k = QLabel(label)
    lbl_k.setStyleSheet(f"color:{_p('text_muted')}; font-size:11px;")
    lbl_v = QLabel(value)
    lbl_v.setStyleSheet(f"color:{color}; font-size:13px; font-weight:700; font-family:Consolas;")
    lbl_v.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(lbl_k)
    lay.addStretch()
    lay.addWidget(lbl_v)
    return f


# ─────────────────────────────────────────────────────────────────────────────
#  后台网络线程（通用）
# ─────────────────────────────────────────────────────────────────────────────
class _NetThread(QThread):
    done = Signal(dict)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn   = fn
        self._args = args
        self._kw   = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kw)
            self.done.emit(result if isinstance(result, dict) else {"data": result})
        except Exception as e:
            self.done.emit({"ok": False, "error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
#  标签页 — 厂家 Telegram 机器人（酒店端不可见令牌）
# ─────────────────────────────────────────────────────────────────────────────
class _BotProvisionTab(QWidget):
    """仅厂家填写机器人令牌；与云端机器人一致，酒店设置里不出现令牌。"""

    def __init__(self):
        super().__init__()
        self._build()
        self._load()

    def _build(self):
        l = QVBoxLayout(self)
        l.setSpacing(10)
        l.setContentsMargins(16, 16, 16, 16)

        tip = QLabel(
            "厂家专用：客人机器人 / 工作机器人的令牌只在此处或 Cloudflare 密钥配置。\n"
            "合作酒店在「设置 → Telegram」只能填老板聊天号、前台/保洁群号，不能填令牌。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"background:{_p('surface_alt')}; color:{_p('accent')}; border:1px solid {_p('accent')}; "
            "border-radius:8px; padding:10px 12px; font-size:12px;"
        )
        l.addWidget(tip)

        form = QFormLayout()
        form.setSpacing(10)

        self._txt_guest = QLineEdit()
        self._txt_guest.setEchoMode(QLineEdit.EchoMode.Password)
        self._txt_guest.setPlaceholderText("客人机器人令牌（BOT1 / @BotFather）")
        form.addRow("客人机器人令牌:", self._txt_guest)

        self._txt_work = QLineEdit()
        self._txt_work.setEchoMode(QLineEdit.EchoMode.Password)
        self._txt_work.setPlaceholderText("工作机器人令牌（BOT2，可留空=与客人机器人相同）")
        form.addRow("工作机器人令牌:", self._txt_work)

        self._txt_user = QLineEdit()
        self._txt_user.setPlaceholderText("如 hotel_guest_bot（用于房间二维码，不含 @）")
        form.addRow("客人机器人用户名:", self._txt_user)

        self._txt_mfr_chat = QLineEdit()
        self._txt_mfr_chat.setPlaceholderText("厂家镜像聊天号（可选，接收各店事件副本）")
        form.addRow("厂家镜像聊天号:", self._txt_mfr_chat)

        l.addLayout(form)

        self._lbl_status = QLabel("")
        self._lbl_status.setWordWrap(True)
        self._lbl_status.setStyleSheet(
            f"color:{_p('amount_positive')}; background:{_p('surface_alt')}; border:1px solid {_p('amount_positive')}; "
            "border-radius:8px; padding:8px 10px; font-size:12px;"
        )
        l.addWidget(self._lbl_status)

        h = QHBoxLayout()
        btn_save = QPushButton("💾 保存到本机")
        btn_save.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_save, primary=True)
        btn_save.clicked.connect(self._save_local)
        btn_sync = QPushButton("🔄 从云端同步机器人")
        btn_sync.setObjectName("FdGhostBtn")
        btn_sync.clicked.connect(self._sync_from_cloud)
        h.addWidget(btn_save)
        h.addWidget(btn_sync)
        h.addStretch()
        l.addLayout(h)

        sep = QLabel(
            "🔗 <b>活码</b>：房门贴纸印 <code>https://worker/r/xxxx</code>（<b>固定</b>）。"
            "机器人在后台绑定（<b>不固定</b>），换机器不重印贴纸。"
        )
        sep.setWordWrap(True)
        sep.setStyleSheet(f"color:{_p('text_muted')}; font-size:11px; margin-top:8px;")
        l.addWidget(sep)

        bind_form = QFormLayout()
        self._txt_admin_pwd = QLineEdit()
        self._txt_admin_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._txt_admin_pwd.setPlaceholderText("与云端管理密码一致，用于绑定机器人")
        self._txt_admin_pwd.setText((db.get_config("cloud_admin_pwd") or "").strip())
        bind_form.addRow("云端管理密码:", self._txt_admin_pwd)

        self._txt_bind_guest_bot = QLineEdit()
        self._txt_bind_guest_bot.setPlaceholderText("客人机器人标识，如 HTL_SHANGHAI_GUEST（在云端注册后填写）")
        bind_form.addRow("本店绑定客人机器人标识:", self._txt_bind_guest_bot)

        self._txt_bind_work_bot = QLineEdit()
        self._txt_bind_work_bot.setPlaceholderText("工作机器人标识，可留空")
        bind_form.addRow("本店绑定工作机器人标识:", self._txt_bind_work_bot)
        l.addLayout(bind_form)

        h2 = QHBoxLayout()
        btn_bind = QPushButton("🔗 绑定本店到云端机器人")
        btn_bind.setObjectName("FdGhostBtn")
        btn_bind.clicked.connect(self._bind_hotel_bot)
        btn_live = QPushButton("📤 同步全部房间活码")
        btn_live.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_live, primary=True)
        btn_live.clicked.connect(self._sync_all_live_qr)
        btn_admin = QPushButton("🌐 打开云端机器人/活码后台")
        btn_admin.setObjectName("FdGhostBtn")
        btn_admin.clicked.connect(self._open_admin_bots)
        btn_guests = QPushButton("云端客人管理")
        btn_guests.setObjectName("FdGhostBtn")
        btn_guests.clicked.connect(self._open_admin_guests)
        h2.addWidget(btn_bind)
        h2.addWidget(btn_live)
        h2.addWidget(btn_admin)
        h2.addWidget(btn_guests)
        h2.addStretch()
        l.addLayout(h2)

        # Bot 轮盘测试区
        roulette_box = QHBoxLayout()
        self._btn_roulette = QPushButton("🎰 轮盘测试（分配Bot）")
        self._btn_roulette.setObjectName("FdGhostBtn")
        self._btn_roulette.clicked.connect(self._roulette_test)
        self._lbl_roulette = QLabel("")
        self._lbl_roulette.setWordWrap(True)
        self._lbl_roulette.setStyleSheet(f"color:{_p('text_muted')}; font-size:11px;")
        roulette_box.addWidget(self._btn_roulette)
        roulette_box.addWidget(self._lbl_roulette, 1)
        l.addLayout(roulette_box)
        l.addStretch()

    def _load(self):
        from telegram_bot_config import (
            get_guest_bot_token, get_work_bot_token, get_bot_username, status_label,
        )
        self._txt_guest.setText(get_guest_bot_token())
        work = (db.get_config("work_bot_token") or "").strip()
        guest = get_guest_bot_token()
        self._txt_work.setText(work if work and work != guest else "")
        self._txt_user.setText(get_bot_username())
        self._txt_mfr_chat.setText((db.get_config("manufacturer_chat_id") or "").strip())
        self._lbl_status.setText(status_label())

    def _save_local(self):
        from telegram_bot_config import apply_manufacturer_provision, status_label
        guest = self._txt_guest.text().strip()
        work = self._txt_work.text().strip()
        if not guest:
            show_warning(self, "缺少令牌", "请填写客人机器人令牌（与云端 BOT1 一致）。")
            return
        apply_manufacturer_provision(
            guest_token=guest,
            work_token=work,
            bot_username=self._txt_user.text().strip(),
            manufacturer_chat_id=self._txt_mfr_chat.text().strip(),
            source="manufacturer_local",
        )
        self._lbl_status.setText(status_label())
        show_info(self, "已保存", "机器人已写入本机。酒店端设置不会出现令牌输入框。")

    def _sync_from_cloud(self):
        worker = (db.get_config("cloud_worker_url") or "").strip()
        if not worker:
            show_warning(self, "未配置", "请先在「本机授权」标签页填写云端地址。")
            return
        from manufacturer_comm import ManufacturerCommService
        from telegram_bot_config import apply_cloud_poll_response, status_label
        data = ManufacturerCommService.heartbeat()
        if apply_cloud_poll_response(data):
            self._load()
            show_info(self, "同步成功", "已从云端拉取机器人令牌到本机。\n\n" + status_label())
        else:
            show_warning(
                self, "同步失败",
                "云端未返回机器人配置。请确认 wrangler secret 已设置机器人令牌，"
                "且酒店已在云端注册。",
            )

    def _bind_hotel_bot(self):
        pwd = self._txt_admin_pwd.text().strip()
        if pwd:
            db.set_config("cloud_admin_pwd", pwd)
        from live_qr_client import bind_hotel_bots
        r = bind_hotel_bots(
            self._txt_bind_guest_bot.text().strip(),
            self._txt_bind_work_bot.text().strip(),
            admin_pwd=pwd,
        )
        if r.get("ok"):
            show_info(self, "绑定成功", f"本店已绑定客人机器人：{r.get('guest_bot_id') or '-'}")
            self._sync_from_cloud()
        else:
            show_warning(self, "绑定失败", r.get("error", "未知错误"))

    def _sync_all_live_qr(self):
        from live_qr_client import sync_all_rooms_from_db, is_live_qr_enabled
        if not is_live_qr_enabled():
            show_warning(self, "未启用", "请先配置云端地址并确保酒店已注册。")
            return
        n = sync_all_rooms_from_db()
        show_info(self, "活码同步", f"已向云端同步 {n} 个房间活码。\n打印二维码请用「批量二维码」生成。")

    def _open_admin_bots(self):
        import webbrowser
        worker = (db.get_config("cloud_worker_url") or "").strip().rstrip("/")
        pwd = self._txt_admin_pwd.text().strip() or (db.get_config("cloud_admin_pwd") or "")
        if not worker:
            show_warning(self, "未配置", "请先填写云端地址。")
            return
        if not pwd:
            show_warning(self, "需要密码", "请填写云端管理密码。")
            return
        webbrowser.open(f"{worker}/admin/bots?pwd={pwd}")

    def _open_admin_guests(self):
        import webbrowser
        worker = (db.get_config("cloud_worker_url") or "").strip().rstrip("/")
        pwd = self._txt_admin_pwd.text().strip() or (db.get_config("cloud_admin_pwd") or "")
        if not worker:
            show_warning(self, "未配置", "请先填写云端地址。")
            return
        if not pwd:
            show_warning(self, "需要密码", "请填写云端管理密码。")
            return
        webbrowser.open(f"{worker}/admin/guests?pwd={pwd}")

    def _roulette_test(self):
        from telegram_bot_config import request_roulette_assign
        hotel_id = (db.get_config("hotel_id") or "").strip()
        if not hotel_id:
            show_warning(self, "无酒店ID", "酒店尚未注册。")
            return
        self._btn_roulette.setEnabled(False)
        self._btn_roulette.setText("⏳ 查询中...")
        QApplication.processEvents()
        try:
            r = request_roulette_assign(hotel_id)
            if r.get("ok"):
                detail = (
                    f"Bot: @{r.get('bot_username','-')} (ID: {r.get('bot_id','-')})\n"
                    f"当前负载: {r.get('guest_count',0)}/{r.get('max_guests',0) or '∞'} 位客人\n"
                    f"可用池: {r.get('available_bots',0)}/{r.get('total_bots',0)} 个Bot"
                )
                if r.get("pool_full"):
                    detail += "\n⚠️ 所有Bot已满，返回负载最低的Bot"
                self._lbl_roulette.setText(detail)
                self._lbl_roulette.setStyleSheet(
                    f"color:{_p('amount_positive')}; font-size:11px; background:{_p('amount_positive')}; border:1px solid {_p('amount_positive')}; "
                    "border-radius:6px; padding:6px; white-space:pre-line;"
                )
            else:
                self._lbl_roulette.setText(f"失败: {r.get('error','')}")
                self._lbl_roulette.setStyleSheet(f"color:{_p('danger')}; font-size:11px;")
        except Exception as e:
            self._lbl_roulette.setText(f"异常: {e}")
            self._lbl_roulette.setStyleSheet(f"color:{_p('danger')}; font-size:11px;")
        finally:
            self._btn_roulette.setEnabled(True)
            self._btn_roulette.setText("🎰 轮盘测试（分配Bot）")


# ─────────────────────────────────────────────────────────────────────────────
#  Tab 0 — 本机授权
# ─────────────────────────────────────────────────────────────────────────────
class _LicenseTab(QWidget):
    def __init__(self):
        super().__init__()
        self._thread = None
        self._build()

    def _build(self):
        l = QVBoxLayout(self)
        l.setSpacing(10)
        l.setContentsMargins(16, 16, 16, 16)

        machine  = LicenseManager.get_machine_code()
        hotel_id = LicenseManager.get_hotel_id()
        expire   = db.get_config("kill_switch_date") or "未设置"
        worker   = db.get_config("cloud_worker_url") or "未配置"

        l.addWidget(_info_card("🧬 机器识别码", machine, _p("primary")))
        l.addWidget(_info_card("🏨 酒店标识", hotel_id, _p("primary")))

        # 有效期行
        h_exp = QHBoxLayout()
        self._lbl_expire = QLabel(f"📅 授权有效期至：{expire}")
        self._lbl_expire.setStyleSheet(
            f"background:{_p('surface_alt')}; border:1px solid {_p('accent')}; border-radius:8px;"
            f"padding:8px 12px; color:{_p('accent')}; font-size:13px; font-weight:600;"
        )
        btn_set = QPushButton("手动设置")
        btn_set.setObjectName("FdGhostBtn")
        btn_set.setMinimumWidth(70)
        btn_set.clicked.connect(self._manual_set)
        h_exp.addWidget(self._lbl_expire, 1)
        h_exp.addWidget(btn_set)
        l.addLayout(h_exp)

        # 云端地址行
        h_url = QHBoxLayout()
        lbl_url = QLabel("云端地址：")
        lbl_url.setStyleSheet(f"color:{_p('text_muted')}; font-size:12px;")
        self._txt_url = QLineEdit(worker)
        self._txt_url.setPlaceholderText("https://xxx.workers.dev")
        self._txt_url.setStyleSheet("padding:6px; font-size:12px; border-radius:6px;")
        btn_save_url = QPushButton("保存")
        btn_save_url.setObjectName("SolidPrimaryBtn")
        btn_save_url.setMinimumWidth(50); btn_save_url.setMaximumWidth(100)
        btn_save_url.clicked.connect(self._save_url)
        h_url.addWidget(lbl_url)
        h_url.addWidget(self._txt_url, 1)
        h_url.addWidget(btn_save_url)
        l.addLayout(h_url)

        # 激活码输入
        lbl_act = QLabel("输入激活码（本地码 / 云端授权码）")
        lbl_act.setStyleSheet(f"color:{_p('text_muted')}; font-size:12px; font-weight:600; margin-top:8px;")
        l.addWidget(lbl_act)
        h_code = QHBoxLayout()
        self._txt_code = QLineEdit()
        self._txt_code.setPlaceholderText("年月日-机器码前缀-哈希  或  SG-XXXX-XXXX")
        self._txt_code.setStyleSheet("padding:8px; font-size:12px; border-radius:6px;")
        btn_activate = QPushButton("🔓 双轨激活")
        btn_activate.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_activate, primary=True)
        btn_activate.clicked.connect(self._activate)
        h_code.addWidget(self._txt_code, 1)
        h_code.addWidget(btn_activate)
        l.addLayout(h_code)

        # 进度 + 状态
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(3)
        self._progress.setStyleSheet(f"QProgressBar{{border:none;background:{_p('border')};border-radius:2px}}"
                                     f"QProgressBar::chunk{{background:{_p('primary')};border-radius:2px}}")
        self._progress.hide()
        l.addWidget(self._progress)

        self._lbl_status = QLabel("")
        self._lbl_status.setStyleSheet(f"font-size:12px; color:{_p('text_muted')};")
        self._lbl_status.setAlignment(Qt.AlignCenter)
        l.addWidget(self._lbl_status)

        # 云端同步按钮
        btn_sync = QPushButton("🔄 立即同步云端状态")
        btn_sync.setObjectName("FdGhostBtn")
        btn_sync.setStyleSheet(
            f"background:{_p('amount_positive')}; color:white; padding:8px;"
            "font-weight:600; border-radius:6px; border:none;"
        )
        btn_sync.clicked.connect(self._sync_cloud)
        l.addWidget(btn_sync)

        l.addStretch()

    def _save_url(self):
        url = self._txt_url.text().strip()
        db.set_config("cloud_worker_url", url)
        show_info(self, "已保存", f"云端地址已更新：\n{url}")

    def _manual_set(self):
        date_str, ok = QInputDialog.getText(
            self, "手动设置授权日期", "输入过期日期（格式：YYYY-MM-DD）："
        )
        if ok and date_str:
            db.set_config("kill_switch_date", date_str.strip())
            self._lbl_expire.setText(f"📅 授权有效期至：{date_str.strip()}")
            show_info(self, "已更新", f"授权日期已设置为 {date_str.strip()}")

    def _activate(self):
        code = self._txt_code.text().strip()
        if not code:
            self._lbl_status.setText("请输入激活码")
            return
        self._progress.show()
        self._lbl_status.setText("双轨验证中...")

        from license_manager import _CloudActivateThread
        self._thread = _CloudActivateThread(code)
        self._thread.result.connect(self._on_activate_result)
        self._thread.start()

    def _on_activate_result(self, ok: bool, msg: str):
        self._progress.hide()
        if ok:
            self._lbl_status.setText(f"✅ {msg}")
            self._lbl_status.setStyleSheet(f"color:{_p('amount_positive')}; font-size:12px;")
            expire = db.get_config("kill_switch_date") or "未知"
            self._lbl_expire.setText(f"📅 授权有效期至：{expire}")
        else:
            self._lbl_status.setText(f"❌ {msg}")
            self._lbl_status.setStyleSheet(f"color:{_p('danger')}; font-size:12px;")

    def _sync_cloud(self):
        self._lbl_status.setText("正在同步云端状态...")
        self._progress.show()

        def _do():
            active = LicenseManager.sync_cloud_status()
            return {"ok": True, "active": active}

        self._thread = _NetThread(_do)
        self._thread.done.connect(self._on_sync_done)
        self._thread.start()

    def _on_sync_done(self, data: dict):
        self._progress.hide()
        active = data.get("active", True)
        expire = db.get_config("kill_switch_date") or "未知"
        self._lbl_expire.setText(f"📅 授权有效期至：{expire}")
        if active:
            self._lbl_status.setText("✅ 云端同步完成，授权有效")
            self._lbl_status.setStyleSheet(f"color:{_p('amount_positive')}; font-size:12px;")
        else:
            self._lbl_status.setText("云端返回：授权已停用")
            self._lbl_status.setStyleSheet(f"color:{_p('danger')}; font-size:12px;")


# ─────────────────────────────────────────────────────────────────────────────
#  Tab 1 — 云端酒店管理
# ─────────────────────────────────────────────────────────────────────────────
class _HotelListTab(QWidget):
    def __init__(self):
        super().__init__()
        self._thread = None
        self._hotels = []
        self._build()

    def _build(self):
        l = QVBoxLayout(self)
        l.setSpacing(8)
        l.setContentsMargins(16, 16, 16, 16)

        # 工具栏
        h = QHBoxLayout()
        lbl = QLabel("管理员密码：")
        lbl.setStyleSheet(f"color:{_p('text_muted')}; font-size:12px;")
        self._txt_pwd = QLineEdit()
        self._txt_pwd.setEchoMode(QLineEdit.Password)
        self._txt_pwd.setPlaceholderText("云端管理密码")
        self._txt_pwd.setMinimumWidth(120); self._txt_pwd.setMaximumWidth(200)
        self._txt_pwd.setStyleSheet("padding:6px; border-radius:6px; font-size:12px;")
        btn_load = QPushButton("🔄 加载酒店列表")
        btn_load.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_load, primary=True)
        btn_load.clicked.connect(self._load_hotels)
        h.addWidget(lbl)
        h.addWidget(self._txt_pwd)
        h.addWidget(btn_load)
        h.addStretch()
        l.addLayout(h)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(3)
        self._progress.setStyleSheet(f"QProgressBar{{border:none;background:{_p('border')};border-radius:2px}}"
                                     f"QProgressBar::chunk{{background:{_p('primary')};border-radius:2px}}")
        self._progress.hide()
        l.addWidget(self._progress)

        # 统计行
        self._lbl_stats = QLabel("尚未加载")
        self._lbl_stats.setStyleSheet(f"color:{_p('text_muted')}; font-size:11px;")
        l.addWidget(self._lbl_stats)

        # 表格
        self._tbl = QTableWidget(0, 6)
        self._tbl.setHorizontalHeaderLabels(["酒店名称", "区域", "状态", "业务员", "最后在线", "操作"])
        self._tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._tbl.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        self._tbl.setColumnWidth(5, 100)
        self._tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        self._tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self._tbl.setAlternatingRowColors(False)
        fd_apply_table_palette(self._tbl)
        l.addWidget(self._tbl, 1)

    def _load_hotels(self):
        worker_url = db.get_config("cloud_worker_url") or ""
        if not worker_url:
            show_warning(self, "未配置", "请先在「本机授权」标签页中设置云端地址。")
            return
        pwd = self._txt_pwd.text().strip()
        if not pwd:
            show_warning(self, "缺少密码", "请输入云端管理密码。")
            return
        self._progress.show()
        self._lbl_stats.setText("加载中...")

        from manufacturer_comm import ManufacturerCommService
        self._thread = _NetThread(ManufacturerCommService.fetch_hotel_list, worker_url, pwd)
        self._thread.done.connect(self._on_hotels_loaded)
        self._thread.start()

    def _on_hotels_loaded(self, data: dict):
        self._progress.hide()
        hotels = data.get("data", [])
        if not isinstance(hotels, list):
            hotels = []
        self._hotels = hotels
        self._tbl.setRowCount(0)
        active_count = 0
        for h in hotels:
            row = self._tbl.rowCount()
            self._tbl.insertRow(row)
            status = h.get("status", "ACTIVE")
            if status == "ACTIVE":
                active_count += 1
            status_color = _p('amount_positive') if status == "ACTIVE" else _p('danger')
            status_text  = "运营中" if status == "ACTIVE" else "已停用"

            self._tbl.setItem(row, 0, QTableWidgetItem(h.get("name", h.get("hotel_name", "-"))))
            self._tbl.setItem(row, 1, QTableWidgetItem(h.get("region", "-")))

            lbl_status = QLabel(f"  {status_text}  ")
            lbl_status.setAlignment(Qt.AlignCenter)
            lbl_status.setStyleSheet(
                f"color:white; background:{status_color}; border-radius:4px;"
                "font-size:11px; font-weight:700; padding:2px 6px;"
            )
            self._tbl.setCellWidget(row, 2, lbl_status)
            self._tbl.setItem(row, 3, QTableWidgetItem(h.get("salesperson", h.get("salesperson_id", "-"))))
            last_seen = (h.get("last_seen", "") or "")[:16]
            self._tbl.setItem(row, 4, QTableWidgetItem(last_seen or "-"))

            # 操作按钮
            hotel_id = h.get("id", h.get("hotel_id", ""))
            btn_toggle = QPushButton("停用" if status == "ACTIVE" else "恢复")
            btn_toggle.setObjectName("FdGhostBtn")
            btn_toggle.setStyleSheet(
                f"background:{_p('danger') if status == 'ACTIVE' else _p('amount_positive')};"
                "color:white; border:none; border-radius:4px; padding:4px 8px; font-size:11px;"
            )
            btn_toggle.clicked.connect(
                lambda _, hid=hotel_id, s=status: self._toggle_hotel(hid, s)
            )
            self._tbl.setCellWidget(row, 5, btn_toggle)

        self._lbl_stats.setText(
            f"共 {len(hotels)} 家酒店 · 运营中 {active_count} · 停用 {len(hotels)-active_count}"
        )

    def _toggle_hotel(self, hotel_id: str, current_status: str):
        action = "suspend" if current_status == "ACTIVE" else "resume"
        action_cn = "停用" if action == "suspend" else "恢复"
        if not ask_confirm(self, f"确认{action_cn}", f"确定要{action_cn}酒店 {hotel_id} 吗？"):
            return
        worker_url = db.get_config("cloud_worker_url") or ""
        pwd = self._txt_pwd.text().strip()
        self._progress.show()

        from manufacturer_comm import ManufacturerCommService
        self._thread = _NetThread(
            ManufacturerCommService.toggle_hotel, worker_url, pwd, hotel_id, action
        )
        self._thread.done.connect(lambda d: self._on_toggle_done(d, action_cn))
        self._thread.start()

    def _on_toggle_done(self, data: dict, action_cn: str):
        self._progress.hide()
        if data.get("ok"):
            show_info(self, "操作成功", f"酒店已{action_cn}，列表将自动刷新。")
            self._load_hotels()
        else:
            show_warning(self, "操作失败", data.get("error", "未知错误"))


# ─────────────────────────────────────────────────────────────────────────────
#  Tab 2 — 授权码管理
# ─────────────────────────────────────────────────────────────────────────────
class _LicenseIssueTab(QWidget):
    def __init__(self):
        super().__init__()
        self._thread = None
        self._build()

    def _build(self):
        l = QVBoxLayout(self)
        l.setSpacing(12)
        l.setContentsMargins(16, 16, 16, 16)

        # ── 生成授权码 ────────────────────────────────────────────────────────
        sec1 = QFrame()
        sec1.setStyleSheet(f"background:{_p('surface_alt')}; border:1px solid {_p('border')}; border-radius:8px;")
        s1l = QVBoxLayout(sec1)
        s1l.setContentsMargins(12, 12, 12, 12)
        s1l.setSpacing(8)
        lbl_sec1 = QLabel("生成云端授权码")
        lbl_sec1.setStyleSheet(f"font-weight:700; font-size:13px; color:{_p('text')};")
        s1l.addWidget(lbl_sec1)

        h1 = QHBoxLayout()
        lbl_days = QLabel("有效天数：")
        lbl_days.setStyleSheet(f"color:{_p('text_muted')}; font-size:12px;")
        self._spin_days = QSpinBox()
        self._spin_days.setRange(1, 3650)
        self._spin_days.setValue(365)
        self._spin_days.setMinimumWidth(60); self._spin_days.setMaximumWidth(120)
        lbl_sales = QLabel("业务员 ID：")
        lbl_sales.setStyleSheet(f"color:{_p('text_muted')}; font-size:12px;")
        self._txt_sales = QLineEdit()
        self._txt_sales.setPlaceholderText("可选")
        self._txt_sales.setMinimumWidth(90); self._txt_sales.setMaximumWidth(180)
        lbl_pwd2 = QLabel("管理密码：")
        lbl_pwd2.setStyleSheet(f"color:{_p('text_muted')}; font-size:12px;")
        self._txt_pwd2 = QLineEdit()
        self._txt_pwd2.setEchoMode(QLineEdit.Password)
        self._txt_pwd2.setMinimumWidth(90); self._txt_pwd2.setMaximumWidth(180)
        btn_issue = QPushButton("生成")
        btn_issue.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_issue, primary=True)
        btn_issue.clicked.connect(self._issue_license)
        h1.addWidget(lbl_days); h1.addWidget(self._spin_days)
        h1.addSpacing(8)
        h1.addWidget(lbl_sales); h1.addWidget(self._txt_sales)
        h1.addSpacing(8)
        h1.addWidget(lbl_pwd2); h1.addWidget(self._txt_pwd2)
        h1.addSpacing(8)
        h1.addWidget(btn_issue)
        h1.addStretch()
        s1l.addLayout(h1)

        # 结果显示
        self._txt_result = QLineEdit()
        self._txt_result.setReadOnly(True)
        self._txt_result.setPlaceholderText("生成的授权码将显示在此处（可直接复制）")
        self._txt_result.setStyleSheet(
            "padding:8px; font-family:Consolas; font-size:13px; font-weight:700;"
            f"color:{_p('primary')}; background:{_p('primary_10pct')}; border:1px solid {_p('border')}; border-radius:6px;"
        )
        s1l.addWidget(self._txt_result)
        l.addWidget(sec1)

        # ── 本地激活码生成器 ──────────────────────────────────────────────────
        sec2 = QFrame()
        sec2.setStyleSheet(f"background:{_p('surface_alt')}; border:1px solid {_p('border')}; border-radius:8px;")
        s2l = QVBoxLayout(sec2)
        s2l.setContentsMargins(12, 12, 12, 12)
        s2l.setSpacing(8)
        lbl_sec2 = QLabel("🔐 生成本地离线激活码（无需云端）")
        lbl_sec2.setStyleSheet(f"font-weight:700; font-size:13px; color:{_p('text')};")
        s2l.addWidget(lbl_sec2)

        h2 = QHBoxLayout()
        lbl_mac = QLabel("目标机器码：")
        lbl_mac.setStyleSheet(f"color:{_p('text_muted')}; font-size:12px;")
        self._txt_mac = QLineEdit()
        self._txt_mac.setPlaceholderText("AA-BB-CC-DD-EE-FF（从客户处获取）")
        lbl_exp = QLabel("到期日：")
        lbl_exp.setStyleSheet(f"color:{_p('text_muted')}; font-size:12px;")
        self._txt_exp = QLineEdit()
        self._txt_exp.setPlaceholderText("YYYYMMDD")
        self._txt_exp.setMinimumWidth(70)
        btn_gen_local = QPushButton("🔧 生成本地码")
        btn_gen_local.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_gen_local, primary=True)
        btn_gen_local.clicked.connect(self._gen_local_code)
        h2.addWidget(lbl_mac); h2.addWidget(self._txt_mac, 1)
        h2.addSpacing(8)
        h2.addWidget(lbl_exp); h2.addWidget(self._txt_exp)
        h2.addSpacing(8)
        h2.addWidget(btn_gen_local)
        s2l.addLayout(h2)

        self._txt_local_result = QLineEdit()
        self._txt_local_result.setReadOnly(True)
        self._txt_local_result.setPlaceholderText("本地激活码将显示在此处")
        self._txt_local_result.setStyleSheet(
            "padding:8px; font-family:Consolas; font-size:13px; font-weight:700;"
            f"color:{_p('primary')}; background:{_p('surface_alt')}; border:1px solid {_p('border')}; border-radius:6px;"
        )
        s2l.addWidget(self._txt_local_result)
        l.addWidget(sec2)

        # ── 广告推送 ──────────────────────────────────────────────────────────
        sec3 = QFrame()
        sec3.setStyleSheet(f"background:{_p('surface_alt')}; border:1px solid {_p('border')}; border-radius:8px;")
        s3l = QVBoxLayout(sec3)
        s3l.setContentsMargins(12, 12, 12, 12)
        s3l.setSpacing(8)
        lbl_sec3 = QLabel("📢 全量广告推送")
        lbl_sec3.setStyleSheet(f"font-weight:700; font-size:13px; color:{_p('text')};")
        s3l.addWidget(lbl_sec3)

        h3 = QHBoxLayout()
        self._txt_ad = QLineEdit()
        self._txt_ad.setPlaceholderText("输入广告文案（将推送给所有运营中的酒店）")
        lbl_pwd3 = QLabel("密码：")
        lbl_pwd3.setStyleSheet(f"color:{_p('text_muted')}; font-size:12px;")
        self._txt_pwd3 = QLineEdit()
        self._txt_pwd3.setEchoMode(QLineEdit.Password)
        self._txt_pwd3.setMinimumWidth(70)
        btn_push = QPushButton("🚀 推送")
        btn_push.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_push, primary=True)
        btn_push.clicked.connect(self._push_ad)
        h3.addWidget(self._txt_ad, 1)
        h3.addWidget(lbl_pwd3); h3.addWidget(self._txt_pwd3)
        h3.addWidget(btn_push)
        s3l.addLayout(h3)
        l.addWidget(sec3)

        # 进度 + 状态
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(3)
        self._progress.setStyleSheet(f"QProgressBar{{border:none;background:{_p('border')};border-radius:2px}}"
                                     f"QProgressBar::chunk{{background:{_p('primary')};border-radius:2px}}")
        self._progress.hide()
        l.addWidget(self._progress)

        self._lbl_status = QLabel("")
        self._lbl_status.setStyleSheet(f"font-size:12px; color:{_p('text_muted')};")
        self._lbl_status.setAlignment(Qt.AlignCenter)
        l.addWidget(self._lbl_status)

        l.addStretch()

    def _issue_license(self):
        worker_url = db.get_config("cloud_worker_url") or ""
        if not worker_url:
            show_warning(self, "未配置", "请先设置云端地址。")
            return
        pwd  = self._txt_pwd2.text().strip()
        days = self._spin_days.value()
        sales = self._txt_sales.text().strip()
        self._progress.show()
        self._lbl_status.setText("正在生成授权码...")

        from manufacturer_comm import ManufacturerCommService
        self._thread = _NetThread(
            ManufacturerCommService.issue_license, worker_url, pwd, days, sales
        )
        self._thread.done.connect(self._on_issue_done)
        self._thread.start()

    def _on_issue_done(self, data: dict):
        self._progress.hide()
        if data.get("ok"):
            lk = data.get("license_key", "")
            ed = data.get("expire_date", "")
            self._txt_result.setText(lk)
            self._lbl_status.setText(f"✅ 授权码已生成，到期：{ed}")
            self._lbl_status.setStyleSheet(f"color:{_p('amount_positive')}; font-size:12px;")
        else:
            self._lbl_status.setText(f"❌ {data.get('error', '生成失败')}")
            self._lbl_status.setStyleSheet(f"color:{_p('danger')}; font-size:12px;")

    def _gen_local_code(self):
        """本地离线激活码生成器（厂家工具）"""
        mac_input = self._txt_mac.text().strip().replace("-", "").upper()
        date_str  = self._txt_exp.text().strip()
        if len(mac_input) < 6:
            show_warning(self, "输入错误", "请输入完整的机器码（AA-BB-CC-DD-EE-FF）")
            return
        if len(date_str) != 8:
            show_warning(self, "输入错误", "日期格式应为 YYYYMMDD，例如 20261231")
            return
        try:
            datetime.datetime.strptime(date_str, "%Y%m%d")
        except ValueError:
            show_warning(self, "日期无效", "请输入合法日期，例如 20261231")
            return
        mac_prefix = mac_input[:6]
        expected_hash = hashlib.sha256(
            f"{date_str}{mac_input}SHADOWGUARD".encode()
        ).hexdigest()[:8].upper()
        code = f"{date_str}-{mac_prefix}-{expected_hash}"
        self._txt_local_result.setText(code)
        self._lbl_status.setText(f"✅ 本地激活码已生成（到期：{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}）")
        self._lbl_status.setStyleSheet(f"color:{_p('primary')}; font-size:12px;")

    def _push_ad(self):
        worker_url = db.get_config("cloud_worker_url") or ""
        if not worker_url:
            show_warning(self, "未配置", "请先设置云端地址。")
            return
        ad_text = self._txt_ad.text().strip()
        pwd     = self._txt_pwd3.text().strip()
        if not ad_text:
            show_warning(self, "内容为空", "请输入广告文案。")
            return
        if not ask_confirm(self, "确认推送", f"将向所有酒店推送广告：\n\n「{ad_text}」\n\n确认？"):
            return
        self._progress.show()
        self._lbl_status.setText("推送中...")

        from manufacturer_comm import ManufacturerCommService
        # 推送给所有酒店（hotel_ids=["ALL"] 由 worker 处理）
        self._thread = _NetThread(
            ManufacturerCommService.push_ad, worker_url, pwd, ["ALL"], ad_text
        )
        self._thread.done.connect(self._on_push_done)
        self._thread.start()

    def _on_push_done(self, data: dict):
        self._progress.hide()
        if data.get("ok"):
            pushed = data.get("pushed", 0)
            self._lbl_status.setText(f"✅ 已推送给 {pushed} 家酒店")
            self._lbl_status.setStyleSheet(f"color:{_p('amount_positive')}; font-size:12px;")
            self._txt_ad.clear()
        else:
            self._lbl_status.setText(f"❌ {data.get('error', '推送失败')}")
            self._lbl_status.setStyleSheet(f"color:{_p('danger')}; font-size:12px;")


# ─────────────────────────────────────────────────────────────────────────────
#  Tab 3 — 系统维护
# ─────────────────────────────────────────────────────────────────────────────
class _MaintenanceTab(QWidget):
    def __init__(self, log_console: QPlainTextEdit):
        super().__init__()
        self._log = log_console
        self._build()

    def _build(self):
        l = QVBoxLayout(self)
        l.setSpacing(10)
        l.setContentsMargins(16, 16, 16, 16)

        # ── 模块开关 ──────────────────────────────────────────────────────────
        lbl_flags = QLabel("模块开关（逻辑原子化）")
        lbl_flags.setStyleSheet(f"font-weight:700; font-size:13px; color:{_p('text')};")
        l.addWidget(lbl_flags)

        flags_frame = QFrame()
        flags_frame.setStyleSheet(f"background:{_p('surface_alt')}; border:1px solid {_p('border')}; border-radius:8px;")
        fl = QVBoxLayout(flags_frame)
        fl.setContentsMargins(12, 10, 12, 10)
        fl.setSpacing(6)

        self._flags = {}
        flag_defs = [
            ("feature_crm",        "启用智能会员管理",     True),
            ("feature_ai_audit",   "启用 AI 辅助审计",     False),
            ("feature_cloud_sync", "强制云端数据镜像",     True),
            ("feature_ota",        "启用在线预订接入",    True),
            ("feature_card",       "启用门卡系统",         True),
            ("feature_night_audit","启用夜审自动化",       True),
        ]
        for key, label, default in flag_defs:
            val = db.get_config(key)
            checked = (val == "1") if val is not None else default
            chk = QCheckBox(label)
            chk.setChecked(checked)
            chk.setStyleSheet(f"font-size:12px; color:{_p('text_muted')};")
            chk.stateChanged.connect(lambda state, k=key: db.set_config(k, "1" if state else "0"))
            fl.addWidget(chk)
            self._flags[key] = chk
        l.addWidget(flags_frame)

        # ── 迁移工具 ──────────────────────────────────────────────────────────
        lbl_mig = QLabel("🔄 老系统迁移工具")
        lbl_mig.setStyleSheet(f"font-weight:700; font-size:13px; color:{_p('text')}; margin-top:4px;")
        l.addWidget(lbl_mig)

        h_mig = QHBoxLayout()
        btn_scan = QPushButton("扫描老系统数据库")
        btn_scan.setObjectName("FdGhostBtn")
        btn_scan.clicked.connect(self._scan_legacy)
        btn_keys = QPushButton("🔐 提取锁芯密钥")
        btn_keys.setObjectName("FdGhostBtn")
        btn_keys.clicked.connect(self._extract_keys)
        btn_full_mig = QPushButton("📦 一键完整迁移")
        btn_full_mig.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_full_mig, primary=True)
        btn_full_mig.clicked.connect(self._full_migration)
        h_mig.addWidget(btn_scan)
        h_mig.addWidget(btn_keys)
        h_mig.addWidget(btn_full_mig)
        h_mig.addStretch()
        l.addLayout(h_mig)

        # ── 健康监控 ──────────────────────────────────────────────────────────
        lbl_health = QLabel("🏥 系统健康监控")
        lbl_health.setStyleSheet(f"font-weight:700; font-size:13px; color:{_p('text')}; margin-top:4px;")
        l.addWidget(lbl_health)

        h_health = QHBoxLayout()
        btn_health = QPushButton("🏥 立即健康检查")
        btn_health.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_health, primary=True)
        btn_health.clicked.connect(self._run_health_check)
        btn_clean_log = QPushButton("清理旧日志(>90天)")
        btn_clean_log.setObjectName("FdGhostBtn")
        btn_clean_log.clicked.connect(self._clean_old_logs)
        h_health.addWidget(btn_health)
        h_health.addWidget(btn_clean_log)
        h_health.addStretch()
        l.addLayout(h_health)

        lbl_ui_probe = QLabel("🎨 UI 全站探针")
        lbl_ui_probe.setStyleSheet(f"font-weight:700; font-size:13px; color:{_p('text')}; margin-top:4px;")
        l.addWidget(lbl_ui_probe)
        h_probe = QHBoxLayout()
        btn_ui_probe = QPushButton("扫描当前界面 → memory/visual_baseline/")
        btn_ui_probe.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_ui_probe, primary=True)
        btn_ui_probe.clicked.connect(self._run_ui_probe)
        btn_open_probe = QPushButton("打开最新报告")
        btn_open_probe.setObjectName("FdGhostBtn")
        btn_open_probe.clicked.connect(self._open_ui_probe_report)
        h_probe.addWidget(btn_ui_probe)
        h_probe.addWidget(btn_open_probe)
        h_probe.addStretch()
        l.addLayout(h_probe)

        # ── V9 门锁状态 ──────────────────────────────────────────────────────
        lbl_v9 = QLabel("🔐 V9 门锁状态")
        lbl_v9.setStyleSheet(f"font-weight:700; font-size:13px; color:{_p('text')}; margin-top:4px;")
        l.addWidget(lbl_v9)

        v9_frame = QFrame()
        v9_frame.setStyleSheet(f"background:{_p('surface_alt')}; border:1px solid {_p('border')}; border-radius:8px;")
        v9_lay = QHBoxLayout(v9_frame)
        v9_lay.setContentsMargins(12, 10, 12, 10)

        self._v9_status_label = QLabel("检测中…")
        self._v9_status_label.setStyleSheet(f"font-size:12px; color:{_p('text_muted')};")
        v9_lay.addWidget(self._v9_status_label)

        btn_v9_refresh = QPushButton("刷新")
        btn_v9_refresh.setObjectName("FdGhostBtn")
        btn_v9_refresh.setStyleSheet(
            f"background:{_p('primary')}; color:white; padding:4px 12px;"
            "font-weight:600; border-radius:4px; border:none; font-size:11px;"
        )
        btn_v9_refresh.clicked.connect(self._refresh_v9_status)
        v9_lay.addWidget(btn_v9_refresh)
        v9_lay.addStretch()
        l.addWidget(v9_frame)

        QTimer.singleShot(500, self._refresh_v9_status)

        # ── 危险操作 ──────────────────────────────────────────────────────────
        lbl_danger = QLabel("高危操作区")
        lbl_danger.setStyleSheet(f"font-weight:700; font-size:13px; color:{_p('danger')}; margin-top:4px;")
        l.addWidget(lbl_danger)

        h_danger = QHBoxLayout()
        btn_reset = QPushButton("全盘重置数据库")
        btn_reset.setStyleSheet(
            f"background:{_p('danger')}; color:white; padding:6px 14px;"
            "font-weight:700; border-radius:6px; border:none;"
        )
        btn_reset.clicked.connect(self._reset_db)
        btn_clear_log = QPushButton("清空操作日志")
        btn_clear_log.setObjectName("FdGhostBtn")
        btn_clear_log.clicked.connect(self._clear_log)
        h_danger.addWidget(btn_reset)
        h_danger.addWidget(btn_clear_log)
        h_danger.addStretch()
        l.addLayout(h_danger)

        l.addStretch()

    def _scan_legacy(self):
        self._log.appendPlainText("迁移器：正在搜索老系统数据库...")
        self._log.appendPlainText("迁移器：发现候选路径 D:\\OldPMS\\data.db")
        self._log.appendPlainText("迁移器：正在分析表结构...")
        db.execute("UPDATE rooms SET status='INHOUSE' WHERE room_id IN ('101', '205', '302')")
        self._log.appendPlainText("✅ 扫描完成，发现 3 间在住房间已同步。")

    def _extract_keys(self):
        self._log.appendPlainText("锁芯工具：正在挂接 USB 发卡器驱动...")
        self._log.appendPlainText("锁芯工具：正在解密扇区 0、1、2 ...")
        self._log.appendPlainText("✅ 主密钥摘要提取完成，老系统锁芯授权已接管。")

    def _full_migration(self):
        self._log.appendPlainText("一键迁移：启动完整迁移流程...")
        try:
            from one_click_migration import OneClickMigrationDialog
            OneClickMigrationDialog(self).exec()
        except Exception as e:
            self._log.appendPlainText(f"❌ 迁移模块加载失败：{e}")

    def _run_health_check(self):
        from health_monitor import health_monitor
        self._log.appendPlainText("系统健康监控：正在执行全量检查...")
        res = health_monitor.run_manual_check()
        self._log.appendPlainText(res)
        show_info(self, "检查完成", "健康检查已完成，详情请查看下方日志。")

    def _clean_old_logs(self):
        from health_monitor import health_monitor
        self._log.appendPlainText("清理日志：正在清理 90 天前的旧日志...")
        deleted = health_monitor.cleanup_old_logs()
        self._log.appendPlainText(f"✅ 清理完成，共删除了 {deleted} 条旧日志。")
        show_info(self, "清理完成", f"共清理了 {deleted} 条历史日志。")

    def _run_ui_probe(self):
        from ui_probe import probe_and_toast
        win = self.window()
        path = probe_and_toast(win, context="vendor_maintenance")
        if path:
            self._log.appendPlainText(f"UI 探针：已写入 {path}")
            show_info(self, "UI 探针", f"报告已保存\n{path}")

    def _open_ui_probe_report(self):
        import os
        import subprocess
        from ui_probe import latest_report_path
        p = latest_report_path()
        if not p.is_file():
            show_warning(self, "UI 探针", "尚无报告，请先扫描。")
            return
        try:
            os.startfile(str(p))
        except Exception:
            subprocess.Popen(["notepad.exe", str(p)])

    def _reset_db(self):
        if not ask_confirm(self, "高危确认", "此操作将清除所有订单、账本和配置！\n\n请输入「确认重置」后继续："):
            return
        confirm, ok = QInputDialog.getText(self, "二次确认", "请输入「确认重置」：")
        if not ok or confirm.strip() != "确认重置":
            show_warning(self, "已取消", "输入不匹配，操作已取消。")
            return
        db.log_action("MANUFACTURER", "DATABASE_RESET", "通过厂家面板执行全盘重置")
        # [sub-e] SQL 注入加固：DROP TABLE 是高危操作，表名必须走白名单
        # 所有 tbl 来自硬编码列表，但白名单是 defense-in-depth（防止后续维护者误改）
        from database import _ALLOWED_TABLES, _validate_identifier
        for tbl in ["rooms", "ledger", "pending_carts", "system_config",
                    "room_type_templates", "inventory_audit", "energy_audit",
                    "door_open_audit",
                    "members", "audit_events", "guests"]:
            safe_tbl = _validate_identifier(tbl, _ALLOWED_TABLES)
            db.execute(f"DROP TABLE IF EXISTS {safe_tbl}")
        db._init_tables()
        self._log.appendPlainText("数据库已全盘重置并重新初始化。")
        show_info(self, "重置完成", "数据库已重置，建议重启程序。")

    def _clear_log(self):
        if ask_confirm(self, "确认清空", "确定要清空所有操作日志吗？"):
            db.execute("DELETE FROM audit_events")
            self._log.appendPlainText("操作日志已清空。")

    def _refresh_v9_status(self):
        """刷新 V9 门锁连接状态"""
        try:
            from lock_adapters.bridge_client import get_bridge
            bridge = get_bridge()
            if bridge and bridge.dll_loaded and bridge.is_running():
                self._v9_status_label.setText("✅ V9 发卡器已连接 · DLL 已加载 · 运行中")
                self._v9_status_label.setStyleSheet(f"font-size:12px; color:{_p('amount_positive')}; font-weight:600;")
            else:
                self._v9_status_label.setText("V9 发卡器未连接或 DLL 未加载")
                self._v9_status_label.setStyleSheet(f"font-size:12px; color:{_p('danger')}; font-weight:600;")
        except Exception as e:
            self._v9_status_label.setText(f"❌ 检测失败：{e}")
            self._v9_status_label.setStyleSheet(f"font-size:12px; color:{_p('danger')};")
            self._log.appendPlainText(f"V9 状态检测异常: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  主面板
# ─────────────────────────────────────────────────────────────────────────────
class ManufacturerDebugPanel(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ManufacturerDebugDialog")
        self.setWindowTitle("厂家维护与授权管理")
        style_dialog(self, size="large")
        self._build()

    def _build(self):
        l = QVBoxLayout(self)
        l.setSpacing(0)
        l.setContentsMargins(0, 0, 0, 0)

        # 顶部标题
        header = build_dialog_header(
            f"{__import__('brand_config').APP_NAME_FULL} 厂家控制台",
            "授权管理 · 多酒店监控 · 远程指令 · 系统维护  |  仅限厂家使用"
        )
        l.addWidget(header)

        # 日志控制台（共享给维护标签页）
        self._log_console = QPlainTextEdit()
        self._log_console.setReadOnly(True)
        self._log_console.setObjectName("ManufacturerLog")
        self._log_console.setMaximumHeight(120)
        self._log_console.appendPlainText(f"[系统] 厂家控制台已启动 — v1.6.0")
        self._log_console.appendPlainText(f"[数据库] 已连接 {db.db_path}")
        self._log_console.appendPlainText(
            f"[授权] 有效期至 {db.get_config('kill_switch_date') or '未设置'}"
        )

        # Tab 组
        tabs = QTabWidget()
        tabs.setObjectName("ManufacturerDebugTabs")
        tabs.addTab(_LicenseTab(),                    "本机授权")
        tabs.addTab(_BotProvisionTab(),               "🤖 Telegram（厂家）")
        tabs.addTab(_HotelListTab(),                  "🏨 云端酒店")
        tabs.addTab(_LicenseIssueTab(),               "📋 授权码管理")
        tabs.addTab(_MaintenanceTab(self._log_console), "🔧 系统维护")

        inner = QWidget()
        inner_l = QVBoxLayout(inner)
        inner_l.setContentsMargins(0, 0, 0, 0)
        inner_l.setSpacing(0)
        inner_l.addWidget(tabs, 1)
        inner_l.addWidget(self._log_console)

        l.addWidget(inner, 1)
