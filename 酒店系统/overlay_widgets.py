from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QDialog, QPushButton
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QRect, QEasingCurve
from PySide6.QtGui import QColor, QFont
from event_bus import bus
from ui_helpers import style_dialog, build_dialog_header
from design_tokens import _p
from sound_helper import play_success, play_alert
import random


class OverlayBase(QWidget):
    """统一 Overlay 基类"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

class SuccessOverlay(OverlayBase):
    def __init__(self, p=None):
        super().__init__(p); self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setObjectName("SuccessOverlay")
        self.setStyleSheet('background:transparent;'); l=QVBoxLayout(self)
        self.lb=QLabel('', alignment=Qt.AlignCenter)
        self.lb.setStyleSheet(
            f"color:{_p('surface')}; font-size:24px; font-weight:bold; "
            f"background:{_p('sidebar')}; border-radius:10px; padding:20px; "
            f"border: 2px solid {_p('accent')};"
        )
        l.addWidget(self.lb, 0, Qt.AlignCenter); self.hide()
        bus.show_success_overlay.connect(self._show)
    def _show(self, t):
        self.lb.setText(f"✅ {t}")
        self.show(); self.raise_(); QTimer.singleShot(2000, self.hide)
        # 入住 / 退房 / 收款 / 制卡 / 注销 等所有"成功"事件都会路由到这里
        # 给老板隔着柜台也能听到的"双滴"
        try:
            play_success()
        except (RuntimeError, OSError):
            # RuntimeError: QSoundEffect 已销毁；OSError: 音频设备不可用
            pass

class CelebrationOverlay(QWidget):
    def __init__(self, p=None):
        super().__init__(p); self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setObjectName("CelebrationOverlay")
        from design_tokens import _p
        c = QColor(_p("sidebar"))
        c.setAlphaF(0.4)
        self.setStyleSheet(f"background:rgba({c.red()},{c.green()},{c.blue()},0.4);")
        self.hide()
        bus.show_celebration.connect(self._start_celebration)
        self.labels = []

    def _start_celebration(self):
        self.show()
        self.raise_()
        self.labels = []
        emojis = ["🎉", "🎊", "💸", "✨", "🔥"]
        for _ in range(30):
            lbl = QLabel(random.choice(emojis), self)
            lbl.setFont(QFont("Segoe UI Emoji", random.randint(24, 48)))
            lbl.setStyleSheet("background:transparent;")
            lbl.show()
            self.labels.append(lbl)
            
            start_x = random.randint(0, self.width())
            end_x = start_x + random.randint(-100, 100)
            start_y = self.height() + 50
            end_y = random.randint(-100, self.height() // 2)

            anim = QPropertyAnimation(lbl, b"geometry")
            anim.setDuration(random.randint(1500, 3000))
            anim.setStartValue(QRect(start_x, start_y, 60, 60))
            anim.setEndValue(QRect(end_x, end_y, 60, 60))
            anim.setEasingCurve(QEasingCurve.OutQuad)
            anim.start(QPropertyAnimation.DeleteWhenStopped)
            lbl.anim = anim # keep reference

        QTimer.singleShot(3500, self._cleanup_celebration)

    def _cleanup_celebration(self):
        self.hide()
        for lbl in self.labels:
            if hasattr(lbl, 'anim'):
                del lbl.anim
            lbl.deleteLater()
        self.labels.clear()


# ── P2 新增: Loading骨架屏 ──
class LoadingOverlay(QWidget):
    """统一加载态 — 半透明遮罩 + 脉冲圆点动画，阻止下层操作。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setObjectName("LoadingOverlay")
        from design_tokens import _p
        bg = QColor(_p("bg"))
        bg.setAlphaF(0.55)
        self.setStyleSheet(f"background: rgba({bg.red()},{bg.green()},{bg.blue()},0.55);")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        row = QHBoxLayout()
        row.setAlignment(Qt.AlignCenter)
        self._dots = []
        for i in range(3):
            dot = QLabel("●", self)
            dot.setStyleSheet(
                f"color: {_p('primary')}; font-size: 24px; background: transparent;"
            )
            dot.setAlignment(Qt.AlignCenter)
            dot.setFixedSize(40, 40)
            self._dots.append(dot)
            row.addWidget(dot)
        lay.addLayout(row)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._pulse)
        self._phase = 0
        self.hide()

    def show_loading(self):
        self.show()
        self.raise_()
        self._timer.start(400)

    def hide_loading(self):
        self._timer.stop()
        for d in self._dots:
            d.setStyleSheet(d.styleSheet().replace("opacity:1.0", "opacity:0.35"))
        self._phase = 0
        self.hide()

    def _pulse(self):
        for i, dot in enumerate(self._dots):
            active = (self._phase % 3 == i)
            if active:
                dot.setStyleSheet(dot.styleSheet().replace("opacity:0.35", "opacity:1.0"))
            else:
                dot.setStyleSheet(dot.styleSheet().replace("opacity:1.0", "opacity:0.35"))
        self._phase = (self._phase + 1) % 3


class OfflineLockOverlay(QDialog):
    """断网超时锁定（主窗口调用 .exec()）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("OfflineLockDialog")
        self.setWindowTitle("离线锁定")
        self.setModal(True)
        from ui_helpers import style_dialog
        style_dialog(self, size="compact")
        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                "系统已长时间无法连接网络，已进入锁定状态。\n"
                "请恢复网络连接后重新启动本程序，或联系管理员。",
                wordWrap=True,
            )
        )
        btn = QPushButton("确定")
        btn.setObjectName("SolidPrimaryBtn")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)


class ShiftWarningOverlay(QWidget):
    """班次超时轻提示（不拦截鼠标）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setObjectName("ShiftWarningOverlay")
        self.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 0)
        self.lbl = QLabel("⚠ 已超过 8 小时未交班，请及时处理。")
        self.lbl.setObjectName("ShiftWarningBanner")
        # 修复警告色：原 accent(浅蓝)对比度仅 3.7:1 且不像警告，改 warn(蜜金) + border-left
        self.lbl.setStyleSheet(
            f"background:{_p('surface')};"
            f"color:{_p('warn')};"
            f"border:1px solid {_p('border')};"
            f"border-left:3px solid {_p('warn')};"
            "padding:10px 14px;border-radius:8px;font-weight:600;"
        )
        lay.addWidget(self.lbl, 0, Qt.AlignTop)
        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent():
            self.setGeometry(0, 0, max(self.parent().width(), 400), 52)
        self.raise_()
        QTimer.singleShot(8000, self.hide)


class ShiftOverdueOverlay(QWidget):
    """班次严重超时提醒（不拦截鼠标）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setObjectName("ShiftOverdueOverlay")
        self.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 0)
        self.lbl = QLabel("⛔ 已超过 16 小时未交班，请立即处理并完成交班。")
        self.lbl.setObjectName("ShiftOverdueBanner")
        self.lbl.setStyleSheet(
            f"background:{_p('danger')};color:{_p('surface')};padding:10px 14px;"
            "border-radius:8px;font-weight:700;"
        )
        lay.addWidget(self.lbl, 0, Qt.AlignTop)
        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent():
            self.setGeometry(0, 0, max(self.parent().width(), 400), 52)
        self.raise_()
        QTimer.singleShot(12000, self.hide)
