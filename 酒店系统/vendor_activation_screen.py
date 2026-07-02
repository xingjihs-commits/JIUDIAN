"""
vendor_activation_screen.py — C0-alpha 厂家激活硬锁

设计意图（来自 商业重构执行清单.md / C0-alpha）：
- 未激活时安装包是"砖"，全屏锁死。
- 未激活状态只允许显示：
  - 激活码输入
  - 机器码 / 酒店 ID
  - 联系厂家方式
  - 必要的系统信息（版本、时间、网络状态）
- 激活通过 = 写入 license_activated_at 并放行；不通过则不能进入系统。
- 厂家入口隐藏：标题标志连击 7 次后弹出厂家口令输入框，校验通过等价于本机激活。

兼容性：
- 老用户已被 production_defaults._grandfather_legacy_activation() 标记为 grandfathered，
  is_activation_required() 会返回 False，本闸门不会拦下。
- 仅"裸装包首次启动 + 当前未激活"会被弹出。

返回：
- exec_() == Accepted 表示已激活，可继续启动流程
- exec_() == Rejected 表示用户拒绝激活（关窗 / 退出），调用方应直接 sys.exit。
"""
from __future__ import annotations

import datetime
import hashlib
import platform
import socket

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QCursor, QFont, QPixmap, QGuiApplication, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QProgressBar, QFrame, QApplication, QInputDialog, QWidget,
    QGraphicsDropShadowEffect, QSizePolicy,
)

from ui_helpers import show_warning, show_info, ask_confirm

from database import db
from design_tokens import _p
from license_manager import LicenseManager


# ─────────────────────────────────────────────────────────────────────────────
#  云端激活后台线程
# ─────────────────────────────────────────────────────────────────────────────
class _ActivateThread(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, code: str):
        super().__init__()
        self._code = code

    def run(self) -> None:
        try:
            ok, msg = LicenseManager.activate_with_code(self._code)
        except Exception as exc:
            ok, msg = False, f"激活异常：{exc}"
        self.finished_signal.emit(ok, msg)


# ─────────────────────────────────────────────────────────────────────────────
#  全屏激活闸门
# ─────────────────────────────────────────────────────────────────────────────
class VendorActivationScreen(QDialog):

    LOGO_TAP_TARGET = 7  # Logo 连击次数，达成后弹厂家口令入口

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._activate_thread: _ActivateThread | None = None
        self._logo_tap_count = 0
        self._logo_tap_timer = QTimer(self)
        self._logo_tap_timer.setSingleShot(True)
        self._logo_tap_timer.timeout.connect(self._reset_logo_taps)

        self.setWindowTitle("Solid · 等待厂家激活")
        self.setWindowFlags(
            Qt.Dialog
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setModal(True)
        self._build_ui()
        self._refresh_system_status()
        # 每秒更新时间，避免单独定时器浪费 DNS 查询
        self._time_timer = QTimer(self)
        self._time_timer.timeout.connect(self._update_time)
        self._time_timer.start(1_000)
        # 云端连通性每 5 分钟检测一次即可（激活失败后会在点击验证时重试）
        self._network_timer = QTimer(self)
        self._network_timer.timeout.connect(self._check_network)
        self._network_timer.start(300_000)

    # ── 显示：覆盖整个主屏幕 ────────────────────────────────────────────────────
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        screen = QGuiApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            self.setGeometry(geom)
        self.raise_()
        self.activateWindow()

    # ── 拦截 Esc / Alt+F4：等同退出请求 ───────────────────────────────────────
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self._on_exit_clicked()
            return
        super().keyPressEvent(event)

    def reject(self) -> None:
        # 关闭按钮 / Esc 都走退出确认
        self._on_exit_clicked()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # [sub-j] 居中卡片布局：登录表单放在一张大卡片里（max-width 460px），
        # 水平垂直居中。卡片样式：圆角 16px + 阴影 + 主题色边框 1px。
        self.setStyleSheet(f"""
            QDialog {{ background: {_p('bg')}; }}
            QLabel {{ color: {_p('text')}; }}
            QLabel#MutedLabel {{ color: {_p('text_muted')}; }}
            QLabel#ActivationBrandTitle {{
                color: {_p('text')}; font-size: 24px; font-weight: 800;
                letter-spacing: 1px;
            }}
            QLabel#ActivationBrandSubtitle {{
                color: {_p('text_muted')}; font-size: 13px;
                letter-spacing: 2px;
            }}
            QLabel#ActivationSectionTitle {{
                color: {_p('primary')}; font-size: 13px; font-weight: 700;
                letter-spacing: 1.5px;
            }}
            QLineEdit {{
                background: {_p('card')}; color: {_p('text')};
                border: 1px solid {_p('border')}; border-radius: 8px;
                padding: 10px 14px; font-size: 15px;
                selection-background-color: {_p('primary')};
            }}
            QLineEdit:focus {{ border: 2px solid {_p('primary')}; }}
            QPushButton#BtnVerify {{
                background: {_p('primary')}; color: {_p('card')};
                border: none; border-radius: 8px;
                font-size: 15px; font-weight: 700;
            }}
            QPushButton#BtnVerify:hover {{ background: {_p('primary_hover')}; }}
            QPushButton#BtnVerify:disabled {{ background: {_p('text_dim')}; }}
            QPushButton#BtnExit {{
                background: transparent; color: {_p('text_muted')};
                border: 1px solid {_p('border')}; border-radius: 6px;
                padding: 6px 14px; font-size: 12px;
            }}
            QPushButton#BtnExit:hover {{ color: {_p('text')}; }}
            QFrame#ActivationCard {{
                background: {_p('card')};
                border: 1px solid {_p('primary')};
                border-radius: 16px;
            }}
            QFrame#ActivationSeparator {{
                background: {_p('border')}; max-height: 1px; min-height: 1px;
                border: none;
            }}
            QLabel#ActivationFooter {{
                color: {_p('text_dim')}; font-size: 11px;
            }}
        """)

        # ── 根布局：上下左右 stretch 让卡片居中 ──
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(12)

        # 顶部 stretch（垂直居中）
        root.addStretch(1)

        # 中间水平居中行
        center_row = QHBoxLayout()
        center_row.setSpacing(0)
        center_row.addStretch(1)

        # 主卡片（max-width 460px）
        card = QFrame()
        card.setObjectName("ActivationCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setMaximumWidth(460)
        card.setMinimumWidth(360)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        # 阴影（Qt QSS 不支持 box-shadow，用 QGraphicsDropShadowEffect 落地）
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 60))
        card.setGraphicsEffect(shadow)

        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(32, 28, 32, 24)
        card_lay.setSpacing(12)

        # ── 卡片顶部：Logo + 品牌名 + 副标题 ──
        from brand_assets import make_brand_mark_label

        # Logo 64x64（也是隐藏厂家入口的连击目标）
        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(0, 0, 0, 0)
        logo_row.addStretch()
        self.lbl_logo = make_brand_mark_label(64, object_name="ActivationBrandMark")
        self.lbl_logo.setCursor(QCursor(Qt.PointingHandCursor))
        self.lbl_logo.mousePressEvent = self._on_logo_tapped  # type: ignore[assignment]
        logo_row.addWidget(self.lbl_logo)
        logo_row.addStretch()
        card_lay.addLayout(logo_row)

        # 品牌名 "Solid PMS"
        self.lbl_brand_title = QLabel("Solid PMS")
        self.lbl_brand_title.setObjectName("ActivationBrandTitle")
        self.lbl_brand_title.setAlignment(Qt.AlignCenter)
        card_lay.addWidget(self.lbl_brand_title)

        # 副标题
        self.lbl_brand_subtitle = QLabel("构筑稳固底座 · 驱动卓越运营")
        self.lbl_brand_subtitle.setObjectName("ActivationBrandSubtitle")
        self.lbl_brand_subtitle.setAlignment(Qt.AlignCenter)
        card_lay.addWidget(self.lbl_brand_subtitle)

        # 分隔线
        card_lay.addWidget(self._make_separator())

        # ── 终端识别信息 ──
        info_title = QLabel("终端识别信息")
        info_title.setObjectName("ActivationSectionTitle")
        card_lay.addWidget(info_title)

        info_section = QVBoxLayout()
        info_section.setSpacing(6)
        self.lbl_machine = self._info_row(info_section, "机器识别码", LicenseManager.get_machine_code(), copy=True)
        self.lbl_hotel_id = self._info_row(info_section, "酒店 ID", LicenseManager.get_hotel_id(), copy=True)
        self.lbl_version = self._info_row(info_section, "系统版本", _read_app_version())
        self.lbl_time = self._info_row(info_section, "当前时间", "")
        self.lbl_network = self._info_row(info_section, "云端状态", "检测中…")
        card_lay.addLayout(info_section)

        card_lay.addWidget(self._make_separator())

        # ── 激活码输入 + 验证按钮 ──
        act_title = QLabel("输入厂家激活码")
        act_title.setObjectName("ActivationSectionTitle")
        card_lay.addWidget(act_title)

        tip = QLabel(
            "将上方「机器识别码」和「酒店 ID」发送给厂家。"
            "厂家审核通过后会回发一段激活码，将它完整粘贴到下方输入框。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"font-size: 11px; color: {_p('text_muted')};")
        card_lay.addWidget(tip)

        self.txt_code = QLineEdit()
        self.txt_code.setPlaceholderText("YYYYMMDD-XXXXXX-XXXXXXXX")
        self.txt_code.setFixedHeight(44)  # [sub-j] 输入框高度 44px
        self.txt_code.returnPressed.connect(self._on_verify_clicked)
        card_lay.addWidget(self.txt_code)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            f"QProgressBar {{ border: none; background: {_p('bg')}; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {_p('primary')}; border-radius: 2px; }}"
        )
        self.progress.hide()
        card_lay.addWidget(self.progress)

        self.lbl_status = QLabel("")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(f"font-size: 12px; color: {_p('text_muted')};")
        card_lay.addWidget(self.lbl_status)

        # 主按钮：固定高度 48px，全宽，主题色填充
        self.btn_verify = QPushButton("验证并激活")
        self.btn_verify.setObjectName("BtnVerify")
        self.btn_verify.setFixedHeight(48)  # [sub-j] 主按钮 48px
        self.btn_verify.setCursor(Qt.PointingHandCursor)
        self.btn_verify.clicked.connect(self._on_verify_clicked)
        card_lay.addWidget(self.btn_verify)

        card_lay.addWidget(self._make_separator())

        # ── 联系厂家 ──
        contact_title = QLabel("联系厂家")
        contact_title.setObjectName("ActivationSectionTitle")
        card_lay.addWidget(contact_title)

        info = LicenseManager.contact_info()
        contact_section = QVBoxLayout()
        contact_section.setSpacing(4)
        if info["telegram"]:
            self._info_row(contact_section, "Telegram", info["telegram"], copy=True)
        if info["email"]:
            self._info_row(contact_section, "Email", info["email"], copy=True)
        if info["phone"]:
            self._info_row(contact_section, "电话", info["phone"], copy=True)
        card_lay.addLayout(contact_section)

        note = QLabel(info["note"])
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 11px; color: {_p('text_muted')};")
        card_lay.addWidget(note)

        warn = QLabel(
            "⚠️ 未激活时本系统不会启动任何业务模块。\n"
            "请勿尝试绕过激活——所有数据将无法生成可信审计证据。"
        )
        warn.setWordWrap(True)
        warn.setAlignment(Qt.AlignCenter)
        warn.setStyleSheet(
            f"font-size: 11px; color: {_p('badge_warn')}; "
            f"border: 1px solid {_p('border')}; border-radius: 6px; "
            f"background: {_p('bg')}; padding: 8px;"
        )
        card_lay.addWidget(warn)

        center_row.addWidget(card)
        center_row.addStretch(1)
        root.addLayout(center_row)

        # 底部 stretch（垂直居中）
        root.addStretch(1)

        # ── 底部：版本号 + 厂家工程师入口 + 退出 ──
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(12)
        # 左：版本号 + "厂家工程师登录" 小字
        footer_text = f"{_read_app_version()}  ·  厂家工程师登录"
        lbl_footer = QLabel(footer_text)
        lbl_footer.setObjectName("ActivationFooter")
        footer.addWidget(lbl_footer)
        footer.addStretch()
        # 右：厂家入口 + 退出程序
        btn_vendor = QPushButton("厂家入口")
        btn_vendor.setObjectName("BtnExit")
        btn_vendor.setCursor(Qt.PointingHandCursor)
        btn_vendor.clicked.connect(self._on_vendor_bypass_clicked)
        footer.addWidget(btn_vendor)
        btn_exit = QPushButton("退出程序")
        btn_exit.setObjectName("BtnExit")
        btn_exit.setCursor(Qt.PointingHandCursor)
        btn_exit.clicked.connect(self._on_exit_clicked)
        footer.addWidget(btn_exit)
        root.addLayout(footer)

    def _make_separator(self) -> QFrame:
        """卡片内分隔线 — 1px 横线，主题 border 色。"""
        sep = QFrame()
        sep.setObjectName("ActivationSeparator")
        sep.setFrameShape(QFrame.Shape.NoFrame)
        return sep

    def _info_row(self, layout: QVBoxLayout, label: str, value: str, *, copy: bool = False) -> QLabel:
        """信息行：label + value + 可选复制按钮，返回 value QLabel。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(f"{label}：")
        lbl.setStyleSheet(f"color: {_p('text_muted')}; font-size: 12px; min-width: 88px;")
        row.addWidget(lbl)

        val = QLabel(value or "—")
        val.setStyleSheet(f"color: {_p('text')}; font-size: 12px; font-weight: 600;")
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(val, 1)

        if copy and value:
            btn = QPushButton("复制")
            btn.setObjectName("FdGhostBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background: {_p('bg')}; color: {_p('text_muted')}; border: 1px solid {_p('border')};"
                f" border-radius: 4px; padding: 2px 10px; font-size: 11px;}}"
                f"QPushButton:hover {{ color: {_p('primary')}; border-color: {_p('accent')}; }}"
            )
            btn.clicked.connect(lambda _, v=value: QApplication.clipboard().setText(v))
            row.addWidget(btn)

        layout.addLayout(row)
        return val

    # ── 状态刷新 ──────────────────────────────────────────────────────────────
    def _refresh_system_status(self) -> None:
        """全量刷新（首次显示时调用）。"""
        self._update_time()
        self._check_network()

    def _update_time(self) -> None:
        """仅更新时间标签（每秒触发，开销极低）。"""
        self.lbl_time.setText(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def _check_network(self) -> None:
        """检测云端连通性（每 5 分钟一次，避免高频 DNS 查询）。"""
        worker_url = (db.get_config("cloud_worker_url") or "").strip()
        if not worker_url:
            self.lbl_network.setText("⚪ 未配置")
            self.lbl_network.setStyleSheet(f"color: {_p('text_muted')}; font-size: 13px; font-weight: 600;")
            return
        try:
            host = worker_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
            socket.gethostbyname(host)
            self.lbl_network.setText("✓ 在线")
            self.lbl_network.setStyleSheet(f"color: {_p('amount_positive')}; font-size: 13px; font-weight: 600;")
        except Exception:
            self.lbl_network.setText("✗ 离线（仍可走本地激活码）")
            self.lbl_network.setStyleSheet(f"color: {_p('amount_negative')}; font-size: 13px; font-weight: 600;")

    # ── 激活流程 ──────────────────────────────────────────────────────────────
    def _on_verify_clicked(self) -> None:
        code = self.txt_code.text().strip()
        if not code:
            self._set_status("请粘贴或输入厂家发的激活码", error=True)
            return
        if self._activate_thread and self._activate_thread.isRunning():
            return
        self.btn_verify.setEnabled(False)
        self.txt_code.setEnabled(False)
        self.progress.show()
        self._set_status("正在验证（本地校验 + 云端注册）…", error=False)

        self._activate_thread = _ActivateThread(code)
        self._activate_thread.finished_signal.connect(self._on_verify_done)
        self._activate_thread.start()

    def _on_verify_done(self, ok: bool, msg: str) -> None:
        self.progress.hide()
        if ok:
            self._set_status(f"✅ {msg}\n激活成功，系统正在进入期初盘点向导…", error=False, ok=True)
            QTimer.singleShot(900, self.accept)
            return
        self.btn_verify.setEnabled(True)
        self.txt_code.setEnabled(True)
        self._set_status(f"❌ {msg}", error=True)

    def _set_status(self, text: str, *, error: bool = False, ok: bool = False) -> None:
        if ok:
            color = _p('amount_positive')
        elif error:
            color = _p('amount_negative')
        else:
            color = _p('text_muted')
        self.lbl_status.setStyleSheet(f"font-size: 13px; color: {color};")
        self.lbl_status.setText(text)

    # ── 隐藏厂家入口（标志连击 7 次） ────────────────────────────────────────
    def _on_logo_tapped(self, _event) -> None:
        self._logo_tap_count += 1
        self._logo_tap_timer.start(2_000)
        if self._logo_tap_count >= self.LOGO_TAP_TARGET:
            self._reset_logo_taps()
            self._prompt_vendor_bypass()

    def _reset_logo_taps(self) -> None:
        self._logo_tap_count = 0

    def _prompt_vendor_bypass(self) -> None:
        """厂家入口：输入厂家密码 → 等价于通过激活。
        首次部署时直接输入内置厂家密码 196776 即可绕过激活。"""
        from permission_system import VENDOR_PASSWORD
        stored = (db.get_config("vendor_password_hash") or "").strip()
        pwd, ok = QInputDialog.getText(
            self, "厂家入口", "输入厂家密码：", QLineEdit.Password
        )
        if not ok:
            return
        if stored:
            h = hashlib.sha256(pwd.strip().encode()).hexdigest()
            if h != stored:
                show_warning(self, "口令错误", "厂家口令不正确。")
                return
        elif pwd.strip() != VENDOR_PASSWORD:
            show_warning(self, "密码错误", "厂家密码不正确。")
            return
        LicenseManager.persist_activation_metadata(
            source="vendor_bypass",
            kill_date="2099-12-31",
            status="VENDOR",
        )
        show_info(self, "厂家入口", "本机已以厂家身份激活，授权永久有效。")
        self.accept()

    def _on_vendor_bypass_clicked(self) -> None:
        """「厂家入口」按钮点击 → 直接弹密码验证。"""
        self._prompt_vendor_bypass()

    # ── 退出 ─────────────────────────────────────────────────────────────────
    def _on_exit_clicked(self) -> None:
        if ask_confirm(
            self, "退出程序",
            "确认退出？\n未激活前系统将无法使用任何业务功能。",
        ):
            QDialog.reject(self)


# ─────────────────────────────────────────────────────────────────────────────
#  辅助
# ─────────────────────────────────────────────────────────────────────────────
def _read_app_version() -> str:
    try:
        from brand_config_v4 import APP_VERSION  # type: ignore[attr-defined]
        return f"Solid v{APP_VERSION}"
    except Exception:
        pass
    try:
        from brand_config_v4 import APP_NAME_FULL  # type: ignore[attr-defined]
        return str(APP_NAME_FULL)
    except Exception:
        return "Solid"


def show_activation_screen_if_needed(parent: QWidget | None = None) -> bool:
    """便捷入口：如需激活则弹出全屏闸门，返回 True 表示放行。"""
    if not LicenseManager.is_activation_required():
        return True
    dlg = VendorActivationScreen(parent)
    return dlg.exec() == QDialog.DialogCode.Accepted
