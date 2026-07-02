# -*- coding: utf-8 -*-
"""
startup_splash.py — Solid PMS 3D 启动动画（5.5秒 · [sub-j] 升级版）

[sub-j] 动画流程（5.5秒，Logo 翻转与进度条同步 2.5s）：
  0.0-1.0s  LOGO 从中心放大浮现（缩放 0→1 + 淡入）
  1.0-3.5s  LOGO 3D Y 轴翻转 360°（QTransform 透视模拟）+ 进度条 0→100% 同步
  1.5-2.3s  品牌名 "Solid" 文字淡入
  2.0-2.8s  副标题 "构筑稳固底座 · 驱动卓越运营" 淡入
  3.5-4.5s  停留期（进度条已满，加载文字停留）
  4.5-5.5s  整体淡出

特色：
- 纯 QPainter 绘制 LOGO（跟随主题变色）
- QPropertyAnimation 驱动缩放/旋转/透明度
- [sub-j] QTransform 透视模拟 3D Y 轴翻转（水平压缩 + 轻微垂直偏移）
- [sub-j] 进度条渐变填充（primary → accent），圆角 4px
- [sub-j] 进度条下方"正在加载 XXX"文字轮播
- [sub-j] 背景加淡渐变（主题色 5% 透明度叠加）
- 主题感知（切换主题时 LOGO 也变色）
"""
from __future__ import annotations

from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPointF, QRectF, QSequentialAnimationGroup, QParallelAnimationGroup,
)
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QFont, QPen, QBrush, QLinearGradient, QRadialGradient, QPainterPath,
    QTransform,
)
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QProgressBar, QApplication, QSplashScreen,
)
from PySide6.QtCore import Property


def _p(key, fallback="#7B8C9E"):
    """启动屏 _p — 委托 design_tokens._p()，内部已有集中兜底。"""
    from design_tokens import _p as _token
    val = _token(key)
    return val if val else fallback


# [sub-j] 进度条加载文字轮播表（与进度条 0→100% 同步显示）
_LOADING_MESSAGES = [
    "正在加载数据库",
    "正在初始化主题",
    "正在注册组件",
    "正在连接云端",
    "正在准备前台",
    "即将完成",
]


def draw_solid_logo(size: int) -> QPixmap:
    """纯 Qt 绘制 Solid LOGO — 跟随主题变色。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    primary = _p("primary", "#7B8C9E")
    accent = _p("accent", "#8A9AAA")

    # 圆角方块底（渐变）
    grad = QLinearGradient(0, 0, 0, size)
    c1 = QColor(primary).lighter(115)
    c2 = QColor(primary)
    grad.setColorAt(0, c1)
    grad.setColorAt(1, c2)
    p.setBrush(QBrush(grad))
    p.setPen(Qt.PenStyle.NoPen)
    radius = size * 0.1875
    p.drawRoundedRect(QRectF(size*0.0625, size*0.0625, size*0.875, size*0.875), radius, radius)

    # 顶部高光
    shine = QLinearGradient(0, 0, 0, size * 0.5)
    shine.setColorAt(0, QColor(255, 255, 255, 80))
    shine.setColorAt(1, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(shine))
    p.drawRoundedRect(QRectF(size*0.0625, size*0.0625, size*0.875, size*0.4375), radius, radius)

    # 底部 accent 色条（三面亮渐隐）
    bar_grad = QLinearGradient(0, 0, size, 0)
    t0 = QColor(accent); t0.setAlpha(0)
    t1 = QColor(accent); t1.setAlpha(0)
    bar_grad.setColorAt(0, t0)
    bar_grad.setColorAt(0.2, QColor(accent))
    bar_grad.setColorAt(0.8, QColor(accent))
    bar_grad.setColorAt(1, t1)
    bar_h = max(2, size // 32)
    p.setBrush(QBrush(bar_grad))
    p.drawRoundedRect(QRectF(size*0.0625, size - size*0.0625 - bar_h, size*0.875, bar_h), bar_h//2, bar_h//2)

    # 左右 accent 色条
    side_color = QColor(accent)
    side_color.setAlpha(204)
    p.setBrush(side_color)
    side_w = max(1, size // 85)
    p.drawRoundedRect(QRectF(size*0.0625, size*0.25, side_w, size*0.625), side_w//2, side_w//2)
    p.drawRoundedRect(QRectF(size - size*0.0625 - side_w, size*0.25, side_w, size*0.625), side_w//2, side_w//2)

    # S 字母
    font = QFont("Segoe UI", int(size * 0.5), QFont.Weight.Bold)
    p.setFont(font)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRectF(0, -bar_h//2, size, size), Qt.AlignmentFlag.AlignCenter, "S")

    p.end()
    return pixmap


class StartupSplash(QWidget):
    """3D 5.5秒启动动画窗口（[sub-j] 升级版：3D 透视翻转 + 同步进度条 + 渐变背景）。"""

    # [sub-j] 同步时长常量：Logo 翻转与进度条共用 2.5 秒
    ROTATION_DURATION_MS = 2500

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.SplashScreen |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(480, 600)

        # ── 本地暗色调色板：豁免启动屏不受全局浅色调色板污染 ──
        try:
            from PySide6.QtGui import QPalette
            dark_pal = self.palette()
            dark_pal.setColor(QPalette.ColorRole.Window, QColor("#1A1625"))
            dark_pal.setColor(QPalette.ColorRole.Base, QColor("#221E30"))
            dark_pal.setColor(QPalette.ColorRole.WindowText, QColor("#E8E4F0"))
            dark_pal.setColor(QPalette.ColorRole.Text, QColor("#E8E4F0"))
            dark_pal.setColor(QPalette.ColorRole.Button, QColor("#2A2538"))
            dark_pal.setColor(QPalette.ColorRole.ButtonText, QColor("#E8E4F0"))
            self.setPalette(dark_pal)
        except Exception:
            pass

        # 居中显示
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - 480) // 2,
                geo.y() + (geo.height() - 600) // 2,
            )

        # 动画状态
        self._scale = 0.0
        self._opacity = 0.0
        self._rotation = 0.0
        self._brand_opacity = 0.0
        self._tagline_opacity = 0.0
        self._progress_value = 0

        # 预渲染 LOGO
        self._logo_pixmap = draw_solid_logo(160)

        # [sub-j] 进度条样式升级：渐变填充（primary → accent），圆角 4px，宽度 320px
        primary_c = _p('primary', '#5B8FB9')
        accent_c = _p('accent', '#7BA7C9')
        self._progress = QProgressBar(self)
        self._progress.setFixedWidth(320)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.setValue(0)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background: rgba(255,255,255,0.08);
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {primary_c}, stop:1 {accent_c});
                border-radius: 4px;
            }}
        """)

        # 主布局
        lay = QVBoxLayout(self)
        lay.setContentsMargins(80, 120, 80, 80)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # LOGO 占位
        self._logo_label = QLabel()
        self._logo_label.setFixedSize(160, 160)
        self._logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._logo_label, 0, Qt.AlignmentFlag.AlignCenter)

        lay.addSpacing(24)

        # 品牌名
        self._brand_label = QLabel("Solid")
        self._brand_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._brand_label.setStyleSheet(f"""
            QLabel {{
                font-size: 36px;
                font-weight: 700;
                color: {_p('primary', '#5B8FB9')};
                letter-spacing: 8px;
                background: transparent;
            }}
        """)
        self._brand_label.setGraphicsEffect(None)
        lay.addWidget(self._brand_label, 0, Qt.AlignmentFlag.AlignCenter)

        lay.addSpacing(8)

        # [sub-j] 副标题改为"精品酒店管理系统 · 四时之色"（用户原话：精品、长期运营）
        self._tagline_label = QLabel("构筑稳固底座 · 驱动卓越运营")
        self._tagline_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tagline_label.setStyleSheet(f"""
            QLabel {{
                font-size: 13px;
                font-weight: 400;
                color: rgba(255,255,255,0.55);
                letter-spacing: 3px;
                background: transparent;
            }}
        """)
        lay.addWidget(self._tagline_label, 0, Qt.AlignmentFlag.AlignCenter)

        lay.addSpacing(40)

        # 进度条
        lay.addWidget(self._progress, 0, Qt.AlignmentFlag.AlignCenter)

        lay.addSpacing(10)

        # [sub-j] 加载文字轮播标签（"正在加载 XXX"）
        self._loading_label = QLabel(_LOADING_MESSAGES[0])
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(f"""
            QLabel {{
                font-size: 11px;
                font-weight: 400;
                color: rgba(255,255,255,0.4);
                letter-spacing: 1.5px;
                background: transparent;
            }}
        """)
        lay.addWidget(self._loading_label, 0, Qt.AlignmentFlag.AlignCenter)

        lay.addStretch()

        # 背景色 — 从当前主题取 sidebar 色，登录页也是 @sidebar@ 底色，无缝过渡
        self._bg_color = QColor(_p("sidebar", "#2A3441"))
        self._overlay_color = QColor(_p("primary", "#5B8FB9"))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # [sub-j] 背景：圆角 + 深色底 + 主题色 5% 透明叠加渐变（不再是纯色）
        bg = QColor(self._bg_color)
        bg.setAlpha(int(255 * self._opacity))
        p.setBrush(QBrush(bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 24, 24)

        # [sub-j] 主题色淡渐变叠加层（5% 透明度，从上到下）
        if self._overlay_color.isValid():
            overlay = QColor(self._overlay_color)
            overlay.setAlpha(int(255 * 0.05 * self._opacity))
            grad_overlay = QLinearGradient(0, 0, 0, self.height())
            grad_overlay.setColorAt(0, overlay)
            overlay_bottom = QColor(self._overlay_color)
            overlay_bottom.setAlpha(0)
            grad_overlay.setColorAt(1, overlay_bottom)
            p.setBrush(QBrush(grad_overlay))
            p.drawRoundedRect(self.rect(), 24, 24)

        # LOGO 绘制（缩放 + 3D Y 轴旋转 + 透明度）
        if self._scale > 0 and self._logo_pixmap:
            p.setOpacity(self._opacity)

            logo_size = int(160 * self._scale)
            if logo_size > 0:
                scaled = self._logo_pixmap.scaled(
                    logo_size, logo_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

                # [sub-j] 3D Y 轴翻转 — 用 QTransform 模拟透视：
                # 1) 水平压缩 cos(θ) 模拟 Y 轴旋转
                # 2) 水平平移保持中心对齐
                # 3) 当 θ ∈ (90°, 270°) 时水平镜像（看到 Logo 背面）
                import math
                angle = self._rotation
                rad = math.radians(angle)
                cos_a = math.cos(rad)
                # 水平压缩比，最低保留 8% 避免完全消失
                compress = max(0.08, abs(cos_a))
                w = max(1, int(logo_size * compress))

                # 翻转阶段（90°~270°）：水平镜像 Logo，模拟看到背面
                if 90 < angle < 270:
                    scaled = scaled.transformed(
                        QTransform.fromScale(-1, 1),
                        Qt.TransformationMode.SmoothTransformation,
                    )

                scaled = scaled.scaled(
                    w, logo_size,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

                # 轻微垂直偏移模拟透视高度（cos 影响"上下俯仰"感）
                v_shift = int(8 * (1 - cos_a) * (1 if angle < 180 else -1))

                x = (self.width() - scaled.width()) // 2
                y = 120 + (160 - logo_size) // 2 + v_shift
                p.drawPixmap(x, y, scaled)

        # 品牌名透明度
        self._brand_label.setGraphicsEffect(None)
        if self._brand_opacity > 0:
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            eff = QGraphicsOpacityEffect()
            eff.setOpacity(self._brand_opacity)
            self._brand_label.setGraphicsEffect(eff)

        # 标语透明度
        if self._tagline_opacity > 0:
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            eff = QGraphicsOpacityEffect()
            eff.setOpacity(self._tagline_opacity)
            self._tagline_label.setGraphicsEffect(eff)

    # ═══ 动画属性（QPropertyAnimation 驱动）═══

    def _get_scale(self): return self._scale
    def _set_scale(self, v):
        self._scale = v
        self.update()
    scale = Property(float, _get_scale, _set_scale)

    def _get_opacity(self): return self._opacity
    def _set_opacity(self, v):
        self._opacity = v
        self.update()
    opacity = Property(float, _get_opacity, _set_opacity)

    def _get_rotation(self): return self._rotation
    def _set_rotation(self, v):
        self._rotation = v
        self.update()
    rotation = Property(float, _get_rotation, _set_rotation)

    def _get_brand_opacity(self): return self._brand_opacity
    def _set_brand_opacity(self, v):
        self._brand_opacity = v
        self.update()
    brandOpacity = Property(float, _get_brand_opacity, _set_brand_opacity)

    def _get_tagline_opacity(self): return self._tagline_opacity
    def _set_tagline_opacity(self, v):
        self._tagline_opacity = v
        self.update()
    taglineOpacity = Property(float, _get_tagline_opacity, _set_tagline_opacity)

    def _start_animations(self):
        """[sub-j] 5.5 秒动画序列（Logo 翻转与进度条同步 2.5s）。"""

        # 阶段 1: 0-1.0s — LOGO 缩放浮现 + 淡入
        scale_anim = QPropertyAnimation(self, b"scale")
        scale_anim.setDuration(1000)
        scale_anim.setStartValue(0.0)
        scale_anim.setEndValue(1.0)
        scale_anim.setEasingCurve(QEasingCurve.Type.OutBack)

        fade_in = QPropertyAnimation(self, b"opacity")
        fade_in.setDuration(1000)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.InOutQuad)

        phase1 = QParallelAnimationGroup()
        phase1.addAnimation(scale_anim)
        phase1.addAnimation(fade_in)

        # [sub-j] 阶段 2: 1.0-3.5s — LOGO 3D Y 轴翻转 360°（2.5 秒，与进度条同步）
        rotate_anim = QPropertyAnimation(self, b"rotation")
        rotate_anim.setDuration(self.ROTATION_DURATION_MS)  # 2500ms
        rotate_anim.setStartValue(0.0)
        rotate_anim.setEndValue(360.0)
        rotate_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        # 阶段 3: 1.5-2.3s — 品牌名淡入（与旋转并行，延迟 500ms）
        brand_anim = QPropertyAnimation(self, b"brandOpacity")
        brand_anim.setDuration(800)
        brand_anim.setStartValue(0.0)
        brand_anim.setEndValue(1.0)
        brand_anim.setEasingCurve(QEasingCurve.Type.OutQuad)

        # 阶段 4: 2.0-2.8s — 副标题淡入（与旋转并行，延迟 1000ms）
        tagline_anim = QPropertyAnimation(self, b"taglineOpacity")
        tagline_anim.setDuration(800)
        tagline_anim.setStartValue(0.0)
        tagline_anim.setEndValue(1.0)
        tagline_anim.setEasingCurve(QEasingCurve.Type.OutQuad)

        # 品牌名在 rotate 开始 0.5s 后淡入（对应设计 1.5s）
        QTimer.singleShot(1500, brand_anim.start)
        # 副标题在 rotate 开始 1s 后淡入（对应设计 2.0s）
        QTimer.singleShot(2000, tagline_anim.start)

        # [sub-j] 阶段 5: 3.5-4.5s — 停留期（进度条已满，加载文字停留）
        pause_anim = QPropertyAnimation(self, b"opacity")
        pause_anim.setDuration(1000)
        pause_anim.setStartValue(1.0)
        pause_anim.setEndValue(1.0)

        # 阶段 6: 4.5-5.5s — 整体淡出
        fade_out = QPropertyAnimation(self, b"opacity")
        fade_out.setDuration(1000)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # 序列组装
        self._sequence = QSequentialAnimationGroup()
        self._sequence.addAnimation(phase1)         # 0-1.0s
        self._sequence.addAnimation(rotate_anim)    # 1.0-3.5s
        self._sequence.addAnimation(pause_anim)     # 3.5-4.5s
        self._sequence.addAnimation(fade_out)       # 4.5-5.5s

        # [sub-j] 进度条 + 加载文字轮播与 Logo 翻转同步启动（1.0s 后开始，2.5s 时长）
        QTimer.singleShot(1000, self._start_progress)

        # [sub-j] 加载文字轮播定时器（每 450ms 切换一次，6 条共 2.7s，覆盖 2.5s 翻转期）
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(450)
        self._loading_timer.timeout.connect(self._rotate_loading_message)
        QTimer.singleShot(1000, lambda: self._loading_timer.start())

        # 动画结束关闭窗口
        self._sequence.finished.connect(self.close)

        self._sequence.start()

    def _start_progress(self):
        """[sub-j] 1.0s 开始进度条动画 — 与 Logo 翻转同步，2.5s 走完 0→100%。"""
        self._progress_value = 0
        # 25 步 × 100ms = 2.5s，与 ROTATION_DURATION_MS 对齐
        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._update_progress)
        self._progress_timer.start(100)

    def _rotate_loading_message(self):
        """[sub-j] 轮播"正在加载 XXX"文字，与进度条同步。"""
        idx = min(len(_LOADING_MESSAGES) - 1, self._progress_value // 18)
        self._loading_label.setText(_LOADING_MESSAGES[idx])

    def _update_progress(self):
        # [sub-j] 25 步 × 4 = 100%，2.5s 完成（与 Logo 翻转同步）
        self._progress_value += 4
        if self._progress_value >= 100:
            self._progress_value = 100
            if hasattr(self, '_progress_timer'):
                self._progress_timer.stop()
            if hasattr(self, '_loading_timer'):
                self._loading_timer.stop()
            self._loading_label.setText(_LOADING_MESSAGES[-1])
        self._progress.setValue(self._progress_value)
        self._rotate_loading_message()


def show_splash(app=None) -> StartupSplash:
    """显示启动动画，返回 splash 实例（动画结束自动关闭）。"""
    splash = StartupSplash()
    splash._start_animations()
    splash.show()
    if app:
        app.processEvents()
    return splash


class StepSplash(StartupSplash):
    """ADVANCE 驱动的启动动画 — app_main.py 每完成一步加载推进一步。
    
    不再自驱动 5.5 秒序列，改为 advance(n) 逐步触发微动画，
    确保登录弹窗出来时动画不会已跑完。
    """

    def __init__(self, icon_path=None, theme_name=None, parent=None):
        super().__init__(parent)
        # 父类构建了全部 UI 但不启动动画，初始态设为不可见
        self._scale = 0.0
        self._opacity = 0.0
        self._rotation = 0.0
        self._brand_opacity = 0.0
        self._tagline_opacity = 0.0
        self._progress_value = 0
        self._current_step = -1
        self._loading_mode = False  # 是否登录后简洁加载模式
        # 保持动画对象引用，防止 Python GC + Qt 双重销毁导致动画中断
        self._active_anims: list = []
        self.update()

    def set_loading_mode(self) -> None:
        """登录后切换为简洁加载模式：纯背景 + 进度条 + 文字，无透明叠加。"""
        self._loading_mode = True
        self._opacity = 1.0
        self._scale = 1.0
        self._rotation = 360.0
        from design_tokens import _p as _token
        bg_hex = _token("sidebar", "#2A3441")
        self._bg_color = QColor(bg_hex)
        # 移除半透明叠加层
        self._overlay_color = QColor()
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background: rgba(255,255,255,0.12);
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: {_token("primary", "#5B8FB9")};
                border-radius: 4px;
            }}
        """)
        self.update()

    def _keep_alive(self, anim) -> None:
        """注册动画到实例属性，完成后自动清理引用。"""
        self._active_anims.append(anim)
        anim.destroyed.connect(lambda *_: self._active_anims.remove(anim) if anim in self._active_anims else None)

    def pulse(self, message=None):
        if message:
            self._loading_label.setText(message)
            self.update()

    def advance(self, step):
        if step <= self._current_step:
            return
        self._current_step = step

        if step == 0:
            # Logo 缩放浮现 + 淡入 (800ms)
            s = QPropertyAnimation(self, b"scale")
            s.setDuration(800); s.setStartValue(0.0); s.setEndValue(1.0)
            s.setEasingCurve(QEasingCurve.Type.OutBack)
            o = QPropertyAnimation(self, b"opacity")
            o.setDuration(800); o.setStartValue(0.0); o.setEndValue(1.0)
            g = QParallelAnimationGroup(self)
            g.addAnimation(s); g.addAnimation(o)
            self._keep_alive(g)
            g.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._loading_label.setText("正在加载主题...")
            self._progress.setValue(18)
        elif step == 1:
            # 3D Y 轴旋转 360° (1200ms)
            r = QPropertyAnimation(self, b"rotation")
            r.setDuration(1200); r.setStartValue(0.0); r.setEndValue(360.0)
            r.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._keep_alive(r)
            r.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            # 品牌名在旋转 400ms 后淡入
            def _show_brand():
                self._brand_opacity = 1.0
                self.update()
            QTimer.singleShot(400, _show_brand)
            self._loading_label.setText("正在加载配置...")
            self._progress.setValue(40)
        elif step == 2:
            self._tagline_opacity = 1.0
            self._loading_label.setText("正在验证授权...")
            self._progress.setValue(60)
            self.update()
        elif step == 3:
            # 登录后恢复 — 强制全可见，消灭 hide/show 残影
            self._scale = 1.0
            self._opacity = 1.0
            self._rotation = 360.0
            self._brand_opacity = 1.0
            self._tagline_opacity = 1.0
            self._loading_label.setText("正在构建主界面...")
            self._progress.setValue(82)
            self.update()
        elif step == 4:
            self._opacity = 1.0
            self._loading_label.setText("即将完成")
            self._progress.setValue(100)
            self.update()

    def finish(self, widget=None):
        """播放淡出 500ms → 关闭。"""
        self._fade = QPropertyAnimation(self, b"opacity")
        self._fade.setDuration(500); self._fade.setStartValue(1.0); self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade.finished.connect(self.close)
        self._fade.start()
