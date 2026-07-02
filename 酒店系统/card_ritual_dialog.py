"""
╔═══════════════════════════════════════════════════════════════╗
║               CardRitualDialog — 制卡仪式                    ║
║                                                              ║
║  不再是机械的点击→滴滴制卡，而是一场有温度的仪式：           ║
║  1. 填写信息 → 点击制卡                                      ║
║  2. 温柔提示"请将空白卡放在发卡器上"                         ║
║  3. 卡片图标脉冲呼吸，等待卡片放置                           ║
║  4. 检测到卡 → 确认微动效                                    ║
║  5. 写入中 → 渐进进度                                        ║
║  6. 完成 → 温暖祝贺 / 失败 → 柔声重试                        ║
║                                                              ║
║  「每一个交互都是一次问候，每一张卡片都是一份温暖」          ║
║                                                              ║
║  FF  ·  RI  (deepseek-r1)                                    ║
║  2026-06-05                                                   ║
╚═══════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QLinearGradient, QPainterPath
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QDateTimeEdit, QSpinBox, QFormLayout, QCheckBox,
    QProgressBar, QFrame, QGraphicsOpacityEffect,
)

from database import db
from design_tokens import _p
from i18n import i18n
from ui_helpers import show_warning, show_info, style_dialog, build_dialog_header
from sound_helper import play_success, play_fail, play_warn, play_notify
from card_system import CardService, get_driver
from power_controller_config import resolve_power_config


# ────────────────────────────────────────────────────────────────
#  仪式阶段枚举
# ────────────────────────────────────────────────────────────────
RITUAL_PHASE_WAIT_CARD = "wait_card"      # 请放卡
RITUAL_PHASE_DETECTED = "detected"        # 已感应
RITUAL_PHASE_WRITING = "writing"          # 写入中
RITUAL_PHASE_SUCCESS = "success"          # 完成
RITUAL_PHASE_ERROR = "error"              # 出错了
RITUAL_PHASE_TIMEOUT = "timeout"          # 超时

RITUAL_TIMEOUT_MS = 45_000                # 45 秒等待放置
RITUAL_POLL_INTERVAL_MS = 400             # 每 400ms 检测一次


# ────────────────────────────────────────────────────────────────
#  仪式状态文案 — 通过 i18n 获取
# ────────────────────────────────────────────────────────────────
def _ritual_msg(phase: str) -> dict:
    """从 i18n 获取仪式阶段文案"""
    prefix = "cd_ritual_phase_" + phase
    return {
        "title": i18n.t(prefix + "_title"),
        "subtitle": i18n.t(prefix + "_subtitle"),
        "hint": i18n.t(prefix + "_hint"),
    }


# ────────────────────────────────────────────────────────────────
#  仪式核心区域 — 卡片图标 + 状态动画
# ────────────────────────────────────────────────────────────────

class CardRitualWidget(QWidget):
    """绘制的卡片图标，带徽标"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 80)
        self._glow_color = QColor(_p("accent"))
        self._glow_intensity = 0.0

    def set_glow(self, intensity: float, color_hex: str = ""):
        self._glow_intensity = max(0.0, min(1.0, intensity))
        if color_hex:
            self._glow_color = QColor(color_hex)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        card_w, card_h = 100, 64
        cx, cy = (w - card_w) / 2, (h - card_h) / 2

        # 辉光外圈
        glow_radius = 4 + int(self._glow_intensity * 12)
        if glow_radius > 4:
            glow_color = QColor(self._glow_color)
            glow_color.setAlpha(int(self._glow_intensity * 60))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow_color)
            path = QPainterPath()
            path.addRoundedRect(
                cx - glow_radius, cy - glow_radius,
                card_w + glow_radius * 2, card_h + glow_radius * 2,
                12, 12,
            )
            p.drawPath(path)

        # 卡片主体（圆角矩形）
        gradient = QLinearGradient(cx, cy, cx, cy + card_h)
        surface = _p("surface")
        surface_alt = _p("bg")
        gradient.setColorAt(0.0, QColor(surface))
        gradient.setColorAt(1.0, QColor(surface_alt))
        p.setBrush(QBrush(gradient))

        border_color = _p("border")
        p.setPen(QPen(QColor(border_color), 1.5))
        path = QPainterPath()
        path.addRoundedRect(cx, cy, card_w, card_h, 8, 8)
        p.drawPath(path)

        # 芯片触点（金色小方块）
        chip_x, chip_y = cx + 12, cy + 12
        chip_w, chip_h = 20, 16
        chip_gradient = QLinearGradient(chip_x, chip_y, chip_x, chip_y + chip_h)
        chip_gradient.setColorAt(0.0, QColor(_p("accent")))
        chip_gradient.setColorAt(1.0, QColor(_p("accent")))
        p.setBrush(QBrush(chip_gradient))
        p.setPen(QPen(QColor(_p("border")), 0.5))
        p.drawRoundedRect(chip_x, chip_y, chip_w, chip_h, 2, 2)

        # 接触线
        line_color = QColor(_p("text_dim"))
        line_color.setAlpha(80)
        p.setPen(QPen(line_color, 1))
        line_y = cy + 44
        p.drawLine(cx + 14, line_y, cx + card_w - 14, line_y)
        line_y2 = cy + 52
        p.drawLine(cx + 14, line_y2, cx + card_w - 14, line_y2)

        p.end()


class GlowRingWidget(QWidget):
    """呼吸光环 — 套在卡片外圈的动态辉光环"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._phase = RITUAL_PHASE_WAIT_CARD
        self._pulse = 0.0  # 0.0 ~ 1.0
        self._pulse_dir = 1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def set_phase(self, phase: str):
        self._phase = phase
        self._pulse = 0.0
        self._pulse_dir = 1
        # 终态（成功/错误/超时）动画收敛后立即停 timer，节省 CPU（原永久 20fps 重绘）
        if phase in (RITUAL_PHASE_SUCCESS, RITUAL_PHASE_ERROR, RITUAL_PHASE_TIMEOUT):
            # 让最后一次动画播完收敛再停（成功需渐亮到 1.0，错误/超时需渐暗到 0）
            QTimer.singleShot(1500, self._stop_timer_safe)
        self.update()

    def _stop_timer_safe(self):
        """安全停止 timer，避免终态后仍 20fps 重绘浪费 CPU。"""
        try:
            if self._timer.isActive():
                self._timer.stop()
        except Exception:
            pass

    def _tick(self):
        step = 0.03
        if self._phase in (RITUAL_PHASE_WAIT_CARD, RITUAL_PHASE_DETECTED):
            self._pulse += step * self._pulse_dir
            if self._pulse >= 1.0:
                self._pulse = 1.0
                self._pulse_dir = -1
            elif self._pulse <= 0.0:
                self._pulse = 0.0
                self._pulse_dir = 1
        elif self._phase == RITUAL_PHASE_WRITING:
            # 写作时快速小幅度呼吸
            self._pulse += step * 2 * self._pulse_dir
            if self._pulse >= 0.6:
                self._pulse = 0.6
                self._pulse_dir = -1
            elif self._pulse <= 0.2:
                self._pulse = 0.2
                self._pulse_dir = 1
        elif self._phase == RITUAL_PHASE_SUCCESS:
            self._pulse = min(1.0, self._pulse + 0.05)
        elif self._phase in (RITUAL_PHASE_ERROR, RITUAL_PHASE_TIMEOUT):
            self._pulse = max(0.0, self._pulse - 0.04)
        self.update()

    def paintEvent(self, event):
        if self._phase in (RITUAL_PHASE_SUCCESS, RITUAL_PHASE_ERROR, RITUAL_PHASE_TIMEOUT) and self._pulse <= 0.01:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        base_radius = 56

        if self._phase == RITUAL_PHASE_WAIT_CARD:
            # 温柔金色呼吸
            color = QColor(_p("accent"))
            alpha = int(40 + self._pulse * 80)
            radius = base_radius + self._pulse * 10
        elif self._phase == RITUAL_PHASE_DETECTED:
            # 确认瞬间：翠绿闪烁
            color = QColor(_p("amount_positive"))
            alpha = int(60 + self._pulse * 100)
            radius = base_radius + 6 + self._pulse * 4
        elif self._phase == RITUAL_PHASE_WRITING:
            # 写入中：主色稳步呼吸
            color = QColor(_p("primary"))
            alpha = int(50 + self._pulse * 60)
            radius = base_radius + 8 + self._pulse * 4
        elif self._phase == RITUAL_PHASE_SUCCESS:
            # 成功：金色绽放
            color = QColor(_p("accent"))
            alpha = int(180 * (1 - self._pulse * 0.6))
            radius = int(base_radius + self._pulse * 30)
        elif self._phase == RITUAL_PHASE_ERROR:
            color = QColor(_p("danger"))
            alpha = int(100 * (1 - self._pulse))
            radius = int(base_radius + (1 - self._pulse) * 20)
        elif self._phase == RITUAL_PHASE_TIMEOUT:
            color = QColor(_p("danger"))
            alpha = int(60 * (1 - self._pulse))
            radius = int(base_radius + (1 - self._pulse) * 15)
        else:
            return

        color.setAlpha(max(0, min(255, alpha)))
        p.setPen(QPen(color, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # 内侧第二环（更淡）
        if radius > 10:
            color2 = QColor(color)
            color2.setAlpha(max(0, alpha // 2))
            p.setPen(QPen(color2, 1))
            p.drawEllipse(QPointF(cx, cy), radius - 6, radius - 6)

        p.end()


# ────────────────────────────────────────────────────────────────
#  仪式对话框
# ────────────────────────────────────────────────────────────────

class CardRitualDialog(QDialog):
    """
    制卡仪式对话框 — 将发卡从"点击操作"变成一场有温度的仪式。

    使用方式（与现有 IssueCardDialog 相同）：
        dlg = CardRitualDialog(self, room_id="101", guest_name="张三", mode="issue")
        if dlg.exec() == QDialog.Accepted:
            card_id = dlg.result_card_id

    mode: "issue" | "reissue"
    """

    def __init__(
        self,
        parent=None,
        room_id: str = "",
        guest_name: str = "",
        old_card_id: str = "",
        mode: str = "issue",
    ):
        super().__init__(parent)
        self.mode = mode
        self.old_card_id = old_card_id
        self._init_room_id = room_id
        self._init_guest_name = guest_name
        self.result_card_id = ""
        self._phase = RITUAL_PHASE_WAIT_CARD
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_card)
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)
        self._card_uid_detected = ""
        self._pulse_anim: Optional[QPropertyAnimation] = None
        self._write_in_progress = False

        self._build_ui()
        self._apply_theme()

    # ═══════════════════════════════════════════════════════════
    #  构建 UI
    # ═══════════════════════════════════════════════════════════

    def _build_ui(self):
        title_key = "cd_ritual_title_issue" if self.mode == "issue" else "cd_ritual_title_reissue"
        subtitle_key = "cd_ritual_subtitle_issue" if self.mode == "issue" else "cd_ritual_subtitle_reissue"
        self.setWindowTitle(i18n.t(title_key))

        # 全局标准尺寸
        style_dialog(self, size="large")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 统一对话框头部 ────────────────────────────────────
        root.addWidget(build_dialog_header("✦ " + i18n.t(title_key), i18n.t(subtitle_key)))

        # ── 主体区域 ──────────────────────────────────────────
        body = QFrame()
        body.setObjectName("RitualBody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 20, 24, 20)
        bl.setSpacing(16)

        # 仪式盒（卡片动画区 + 状态文案）
        self.ritual_box = QFrame()
        self.ritual_box.setObjectName("RitualAnimationBox")
        self.ritual_box.setMinimumHeight(160)
        self.ritual_box.setMaximumHeight(300)
        rbl = QVBoxLayout(self.ritual_box)
        rbl.setContentsMargins(0, 0, 0, 0)
        rbl.setAlignment(Qt.AlignCenter)

        # 卡片 + 光环容器
        class _CardContainer(QWidget):
            """可自适应大小的卡片容器，glow_ring 随容器 resize 动态调整"""
            def __init__(self, glow_ring, parent=None):
                super().__init__(parent)
                self._glow_ring = glow_ring
            def resizeEvent(self, event):
                super().resizeEvent(event)
                self._glow_ring.setGeometry(0, 0, self.width(), self.height())

        card_container = _CardContainer(None)
        card_container.setMinimumSize(160, 140)
        cc_lay = QVBoxLayout(card_container)
        cc_lay.setContentsMargins(0, 0, 0, 0)
        cc_lay.setAlignment(Qt.AlignCenter)

        self.glow_ring = GlowRingWidget(card_container)
        card_container._glow_ring = self.glow_ring
        self.glow_ring.setGeometry(0, 0, card_container.width(), card_container.height())

        self.card_widget = CardRitualWidget(card_container)
        self.card_widget.move(20, 30)

        cc_lay.addStretch()
        rbl.addWidget(card_container, 0, Qt.AlignCenter)

        # 状态标题（过程提示）
        self.ritual_title = QLabel("")
        self.ritual_title.setObjectName("RitualTitle")
        self.ritual_title.setAlignment(Qt.AlignCenter)
        self.ritual_title.setWordWrap(True)
        rbl.addWidget(self.ritual_title)

        # 状态副标题
        self.ritual_subtitle = QLabel("")
        self.ritual_subtitle.setObjectName("RitualSubtitle")
        self.ritual_subtitle.setAlignment(Qt.AlignCenter)
        self.ritual_subtitle.setWordWrap(True)
        rbl.addWidget(self.ritual_subtitle)

        # 小提示
        self.ritual_hint = QLabel("")
        self.ritual_hint.setObjectName("RitualHint")
        self.ritual_hint.setAlignment(Qt.AlignCenter)
        rbl.addWidget(self.ritual_hint)

        # 进度条（写入时显示）
        self.ritual_progress = QProgressBar()
        self.ritual_progress.setObjectName("RitualProgress")
        self.ritual_progress.setRange(0, 0)
        self.ritual_progress.setFixedHeight(4)
        self.ritual_progress.setVisible(False)
        rbl.addWidget(self.ritual_progress)

        # 仪式盒初始不可见
        self.ritual_box.setVisible(False)
        self.ritual_box.setGraphicsEffect(None)
        bl.addWidget(self.ritual_box)

        # ── 信息表单 ──────────────────────────────────────────
        form_card = QFrame()
        form_card.setObjectName("RitualFormCard")
        fl = QVBoxLayout(form_card)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.room_edit = QLineEdit(self._init_room_id)
        self.room_edit.setPlaceholderText(i18n.t("cd_ph_room"))
        form.addRow(i18n.t("cd_label_room"), self.room_edit)

        self.guest_edit = QLineEdit(self._init_guest_name)
        self.guest_edit.setPlaceholderText(i18n.t("cd_ph_guest"))
        form.addRow(i18n.t("cd_label_guest"), self.guest_edit)

        self.card_count_spin = QSpinBox()
        self.card_count_spin.setRange(1, 99)
        self.card_count_spin.setValue(1)
        self.card_count_spin.setToolTip(i18n.t("cd_tip_card_count"))
        if self.mode == "reissue":
            self.card_count_spin.setVisible(False)
        form.addRow(i18n.t("cd_label_card_count"), self.card_count_spin)

        # 有效期
        self.expire_edit = QDateTimeEdit()
        self.expire_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.expire_edit.setCalendarPopup(True)
        checkout_hour = db.get_config("default_card_checkout_hour") or db.get_config("lock_takeover_checkout_time") or "12:00"
        try:
            hh, mm = checkout_hour.split(":")
            hh_i, mm_i = max(0, min(23, int(hh))), max(0, min(59, int(mm)))
        except (ValueError, AttributeError):
            hh_i, mm_i = 12, 0
        from PySide6.QtCore import QDateTime
        default_expire = QDateTime.currentDateTime().addDays(1)
        default_expire.setTime(default_expire.time().fromMSecsSinceStartOfDay((hh_i * 3600 + mm_i * 60) * 1000))
        self.expire_edit.setDateTime(default_expire)

        self.chk_vip = QCheckBox(i18n.t("cd_label_vip"))
        self.chk_vip.setToolTip(i18n.t("cd_tip_vip"))
        vip_co = db.get_config("lock_takeover_vip_checkout_time") or ""
        self.chk_vip.setVisible(bool(vip_co.strip()))
        self.chk_vip.toggled.connect(lambda checked: self._on_vip_toggle(checked, vip_co))
        form.addRow("", self.chk_vip)
        form.addRow(i18n.t("cd_label_expire"), self.expire_edit)

        if self.mode == "reissue" and hasattr(self, 'old_card_id') and self.old_card_id:
            old_lbl = QLabel(i18n.t("cd_label_old_card").format(card_id=self.old_card_id))
            old_lbl.setObjectName("Small")
            old_lbl.setObjectName("RitualOldCardLabel")
            form.addRow("", old_lbl)

        fl.addLayout(form)
        bl.addWidget(form_card)

        # ── 操作按钮 ──────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_cancel = QPushButton(i18n.t("cd_btn_cancel"))
        self.btn_cancel.setObjectName("FdGhostBtn")
        self.btn_cancel.setMinimumHeight(38)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self._safe_close)
        btn_row.addWidget(self.btn_cancel)

        btn_row.addStretch()

        start_key = "cd_btn_start_issue" if self.mode == "issue" else "cd_btn_start_reissue"
        self.btn_action = QPushButton("✦ " + i18n.t(start_key))
        self.btn_action.setObjectName("SolidPrimaryBtn")
        self.btn_action.setMinimumHeight(36)
        self.btn_action.setMinimumWidth(180)
        self.btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_action.clicked.connect(self._start_ritual)
        btn_row.addWidget(self.btn_action)

        # 重试按钮
        self.btn_retry = QPushButton("⟳ " + i18n.t("cd_btn_retry"))
        self.btn_retry.setObjectName("FdGhostBtn")
        self.btn_retry.setMinimumHeight(38)
        self.btn_retry.setMinimumWidth(140)
        self.btn_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_retry.setVisible(False)
        self.btn_retry.clicked.connect(self._retry)
        btn_row.addWidget(self.btn_retry)

        self.btn_continue = QPushButton("✓ " + i18n.t("cd_btn_done"))
        self.btn_continue.setObjectName("SolidPrimaryBtn")
        self.btn_continue.setMinimumHeight(38)
        self.btn_continue.setMinimumWidth(120)
        self.btn_continue.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_continue.setVisible(False)
        self.btn_continue.clicked.connect(self._on_ritual_success_accept)
        btn_row.addWidget(self.btn_continue)

        bl.addLayout(btn_row)
        root.addWidget(body, 1)

    def _apply_theme(self):
        """主题色已通过全局 style_dialog + QSS 统一管理，无需独立设置。"""
        pass

    # ═══════════════════════════════════════════════════════════
    #  公共设置方法
    # ═══════════════════════════════════════════════════════════

    def set_room_info(self, room_id: str, guest_name: str = ""):
        """外部设置房间号和客人信息"""
        if room_id:
            self.room_edit.setText(room_id)
        if guest_name:
            self.guest_edit.setText(guest_name)

    # ═══════════════════════════════════════════════════════════
    #  VIP 延时退房
    # ═══════════════════════════════════════════════════════════

    def _on_vip_toggle(self, checked: bool, vip_co: str):
        if not checked or not vip_co:
            return
        try:
            hh, mm = vip_co.strip().split(":")
            hh_i, mm_i = max(0, min(23, int(hh))), max(0, min(59, int(mm)))
            from PySide6.QtCore import QTime
            new_dt = self.expire_edit.dateTime()
            new_dt.setTime(QTime(hh_i, mm_i))
            self.expire_edit.setDateTime(new_dt)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  安全关闭
    # ═══════════════════════════════════════════════════════════

    def _safe_close(self):
        """如果写入中，阻止关闭"""
        if self._write_in_progress:
            show_warning(self, i18n.t("cd_msg_hint"), i18n.t("cd_msg_writing_wait"))
            return
        self._stop_polling()
        self.reject()

    # ═══════════════════════════════════════════════════════════
    #  仪式主流程
    # ═══════════════════════════════════════════════════════════

    def _validate_form(self) -> bool:
        """表单校验"""
        room_id = self.room_edit.text().strip()
        guest_name = self.guest_edit.text().strip()
        if not room_id:
            show_warning(self, i18n.t("cd_msg_hint"), i18n.t("cd_msg_need_room"))
            return False
        if not guest_name:
            show_warning(self, i18n.t("cd_msg_hint"), i18n.t("cd_msg_need_guest"))
            return False
        return True

    def _start_ritual(self):
        """开始仪式流程"""
        if not self._validate_form():
            return

        # 从表单收集信息
        self._room_id = self.room_edit.text().strip()
        self._guest_name = self.guest_edit.text().strip()
        self._expire_dt = self.expire_edit.dateTime().toPython()
        self._card_count = self.card_count_spin.value()

        # 批量制卡 >5 张需额外确认
        if self._card_count > 5:
            from ui_helpers import ask_confirm
            if not ask_confirm(
                self, i18n.t("cd_msg_batch_title"),
                i18n.t("cd_msg_batch_confirm").format(count=self._card_count)
            ):
                return

        # 保存以备后用
        self.room_edit.setReadOnly(True)
        self.guest_edit.setReadOnly(True)
        self.card_count_spin.setEnabled(False)
        self.expire_edit.setEnabled(False)
        self.chk_vip.setEnabled(False)
        self.btn_action.setVisible(False)
        self.btn_cancel.setText(i18n.t("cd_btn_back"))

        # 显示仪式盒（优雅滑入）
        self.ritual_box.setVisible(True)
        self._animate_ritual_box_in()

        # 进入等待卡片阶段
        self._set_phase(RITUAL_PHASE_WAIT_CARD)
        self._start_polling()

    def _animate_ritual_box_in(self):
        """仪式盒优雅淡入"""
        opacity_effect = QGraphicsOpacityEffect(self.ritual_box)
        opacity_effect.setOpacity(0.0)
        self.ritual_box.setGraphicsEffect(opacity_effect)

        anim = QPropertyAnimation(opacity_effect, b"opacity", self.ritual_box)
        anim.setDuration(400)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    # ═══════════════════════════════════════════════════════════
    #  阶段管理
    # ═══════════════════════════════════════════════════════════

    def _set_phase(self, phase: str):
        self._phase = phase
        msg = _ritual_msg(phase)

        # 更新文案（带淡入效果）
        self._fade_text(self.ritual_title, msg["title"])
        self._fade_text(self.ritual_subtitle, msg["subtitle"])
        self._fade_text(self.ritual_hint, msg["hint"])

        # 更新光环
        self.glow_ring.set_phase(phase)

        # 进度条
        if phase == RITUAL_PHASE_WRITING:
            self.ritual_progress.setVisible(True)
        else:
            self.ritual_progress.setVisible(False)

        # 按钮可见性
        if phase == RITUAL_PHASE_SUCCESS:
            self.btn_retry.setVisible(False)
            self.btn_continue.setVisible(True)
            self.btn_cancel.setVisible(False)
        elif phase in (RITUAL_PHASE_ERROR, RITUAL_PHASE_TIMEOUT):
            self.btn_retry.setVisible(True)
            self.btn_continue.setVisible(False)
            self.btn_cancel.setText(i18n.t("cd_btn_cancel"))
            self.btn_cancel.setVisible(True)
        else:
            self.btn_retry.setVisible(False)
            self.btn_continue.setVisible(False)
            self.btn_cancel.setText(i18n.t("cd_btn_back") if self.ritual_box.isVisible() else i18n.t("cd_btn_cancel"))
            self.btn_cancel.setVisible(True)

        # 卡片辉光
        if phase == RITUAL_PHASE_WAIT_CARD:
            self.card_widget.set_glow(0.6, _p("accent"))
        elif phase == RITUAL_PHASE_DETECTED:
            self.card_widget.set_glow(0.8, _p("amount_positive"))
        elif phase == RITUAL_PHASE_WRITING:
            self.card_widget.set_glow(0.5, _p("primary"))
        elif phase == RITUAL_PHASE_SUCCESS:
            self.card_widget.set_glow(1.0, _p("accent"))
        elif phase == RITUAL_PHASE_ERROR:
            self.card_widget.set_glow(0.3, _p("danger"))
        elif phase == RITUAL_PHASE_TIMEOUT:
            self.card_widget.set_glow(0.2, _p("danger"))

    def _fade_text(self, label: QLabel, text: str):
        """文字淡入更新"""
        if label.text() == text and label.text():
            return
        label.setText(text)
        if self.ritual_box.isVisible():
            opacity_effect = QGraphicsOpacityEffect(label)
            opacity_effect.setOpacity(0.0)
            label.setGraphicsEffect(opacity_effect)
            anim = QPropertyAnimation(opacity_effect, b"opacity", label)
            anim.setDuration(300)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    # ═══════════════════════════════════════════════════════════
    #  卡片检测（轮询）
    # ═══════════════════════════════════════════════════════════

    def _start_polling(self):
        """开始轮询检测卡片"""
        self._card_uid_detected = ""
        self._poll_count = 0
        self._timeout_timer.start(RITUAL_TIMEOUT_MS)

        # 如果发卡器有接管，先检查
        self._takeover_active = False
        try:
            from lock_issue_service import takeover_configured
            self._takeover_active = takeover_configured()
        except Exception:
            self._takeover_active = False

        # 模拟模式延迟一下再自动"检测"到卡
        driver = get_driver()
        self._is_simulate = driver._simulate if hasattr(driver, '_simulate') else False

        self._poll_timer.start(RITUAL_POLL_INTERVAL_MS)

    def _stop_polling(self):
        self._poll_timer.stop()
        self._timeout_timer.stop()

    def _poll_card(self):
        """检测卡片是否放置"""
        if self._write_in_progress:
            return

        self._poll_count += 1

        # 模拟模式：2.5s 后自动检测到卡
        if self._is_simulate:
            if self._poll_count * RITUAL_POLL_INTERVAL_MS >= 2500:
                self._on_card_detected(f"SIM-{int(time.time()) % 100000:06X}")
            return

        # 先尝试接管读卡
        if self._takeover_active:
            try:
                from lock_issue_service import read_card_payload_via_adapter
                ok, payload = read_card_payload_via_adapter()
                if ok and payload:
                    self._on_card_detected(payload.upper())
                    return
            except Exception:
                pass

        # 回落到通用驱动
        driver = get_driver()
        if not driver.is_connected():
            ok, msg = driver.connect()
            if not ok:
                return  # 继续等待
        try:
            ok, uid = driver.read_card_uid()
            if ok and uid and len(str(uid).strip()) > 2:
                self._on_card_detected(str(uid).strip().upper())
        except Exception:
            pass

    def _on_card_detected(self, uid: str):
        """检测到卡片"""
        self._stop_polling()
        self._card_uid_detected = uid

        # 短暂显示"已感应到"
        self._set_phase(RITUAL_PHASE_DETECTED)
        play_notify()

        # 400ms 后进入写入阶段
        QTimer.singleShot(600, self._do_write)

    def _on_timeout(self):
        """等待超时"""
        self._stop_polling()
        self._set_phase(RITUAL_PHASE_TIMEOUT)
        play_warn()

    # ═══════════════════════════════════════════════════════════
    #  写卡
    # ═══════════════════════════════════════════════════════════

    def _do_write(self):
        """执行写卡"""
        self._write_in_progress = True
        self._set_phase(RITUAL_PHASE_WRITING)

        # 短暂延迟让用户看到"写入中"动画
        QTimer.singleShot(500, self._do_write_actual)

    def _do_write_actual(self):
        """实际执行写卡逻辑"""
        room_id = self._room_id
        guest_name = self._guest_name
        expire_dt = self._expire_dt
        card_count = self._card_count

        # 多张卡场景：先写第一张
        try:
            if self.mode == "issue":
                ok, result = CardService.issue_card(room_id, guest_name, expire_dt, card_no=1)
            else:
                ok, result = CardService.reissue_card(self.old_card_id, room_id, guest_name, expire_dt)

            if ok:
                self.result_card_id = result
                self._write_in_progress = False

                # 多张卡需要用户换卡
                if card_count > 1 and self.mode == "issue":
                    self._stop_polling()
                    show_info(
                        self, i18n.t("cd_msg_switch_card_title"),
                        i18n.t("cd_msg_switch_card").format(card_no=1, card_id=result)
                    )
                    # 继续写剩余的
                    all_ok = True
                    last_id = result
                    for i in range(2, card_count + 1):
                        ok, result = CardService.issue_card(room_id, guest_name, expire_dt, card_no=i)
                        if not ok:
                            all_ok = False
                            self._set_phase(RITUAL_PHASE_ERROR)
                            self.ritual_subtitle.setText(i18n.t("cd_msg_card_fail").format(card_no=i))
                            self.ritual_hint.setText(result)
                            play_fail()
                            return
                        last_id = result
                        if i < card_count:
                            show_info(
                                self, i18n.t("cd_msg_switch_card_title"),
                                i18n.t("cd_msg_switch_card").format(card_no=i, card_id=result)
                            )
                    if all_ok:
                        self.result_card_id = last_id
                        self.ritual_subtitle.setText(i18n.t("cd_msg_total_cards").format(count=card_count))
                        self._on_write_success()
                else:
                    self._on_write_success()
            else:
                self._write_in_progress = False
                self._set_phase(RITUAL_PHASE_ERROR)
                self.ritual_subtitle.setText(result)
                play_fail()
        except Exception as e:
            self._write_in_progress = False
            self._set_phase(RITUAL_PHASE_ERROR)
            self.ritual_subtitle.setText(str(e))
            play_fail()

    def _on_write_success(self):
        """写入成功"""
        self._set_phase(RITUAL_PHASE_SUCCESS)

        # 播放欢快成功音
        play_success()

        # 触发庆祝发光动画（从卡片向外扩散）
        QTimer.singleShot(200, self._celebrate_glow)

        # 统计信息
        power_info = ""
        pc = resolve_power_config()
        if pc.get("enabled"):
            power_info = "\n" + i18n.t("cd_power_info").format(
                brand=pc.get('lock_brand_name', i18n.t("cd_power_default_brand")),
                sector=pc['sector']
            )
        expire_str = self._expire_dt.strftime("%Y-%m-%d %H:%M")
        full_msg = i18n.t("cd_result_summary").format(
            room=self._room_id,
            guest=self._guest_name,
            card_id=self.result_card_id,
            expire=expire_str,
            power_info=power_info
        )
        if self._card_count > 1:
            full_msg += "\n" + i18n.t("cd_result_total_cards").format(count=self._card_count)
        self.ritual_hint.setText(full_msg)

    def _celebrate_glow(self):
        """成功后的绽放辉光"""
        # 卡片高亮绽放
        self.card_widget.set_glow(1.0, _p("accent"))
        self.glow_ring.set_phase(RITUAL_PHASE_SUCCESS)

        # 0.5s 后降为柔光
        QTimer.singleShot(800, lambda: self.card_widget.set_glow(0.4, _p("accent")))

    def _on_ritual_success_accept(self):
        """仪式成功，接受对话框"""
        self.accept()

    # ═══════════════════════════════════════════════════════════
    #  重试
    # ═══════════════════════════════════════════════════════════

    def _retry(self):
        """重试仪式"""
        self.btn_retry.setVisible(False)
        self._write_in_progress = False

        # 重置到等待卡片
        self._card_uid_detected = ""
        self._set_phase(RITUAL_PHASE_WAIT_CARD)

        # 重置进度条
        self.ritual_progress.setRange(0, 0)
        self.ritual_progress.setVisible(False)

        # 重新开始轮询
        self._start_polling()

    # ═══════════════════════════════════════════════════════════
    #  键盘处理
    # ═══════════════════════════════════════════════════════════

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._safe_close()
        elif event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if self.btn_action.isVisible():
                self._start_ritual()
            elif self.btn_continue.isVisible():
                self._on_ritual_success_accept()
            elif self.btn_retry.isVisible():
                self._retry()
        else:
            super().keyPressEvent(event)

    # ═══════════════════════════════════════════════════════════
    #  析构
    # ═══════════════════════════════════════════════════════════

    def closeEvent(self, event):
        self._stop_polling()
        super().closeEvent(event)
