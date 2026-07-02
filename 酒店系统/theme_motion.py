"""theme_motion.py — Lovable v3 老钱版 · 微动效统一封装

落地 Lovable 简报第 5 节《高级动效节奏表》6 条节奏，全部基于
QPropertyAnimation。设计原则：

    1) 一句调用挂上：attach_primary_button_glow(btn) 即可；
    2) 主题感知：颜色随当前 palette 走（懒读 theme_palette.THEMES）；
    3) 永不阻塞主线程：动画 owner 绑在目标 widget 上，widget 析构即停；
    4) 节奏表（Lovable）严格按 ms / easing 复刻——下行注释一一对应：
       #一 主按钮悬浮辉光呼吸 —— 入一百八十毫秒缓出，出二百二十毫秒缓入
       #2 房卡 tile 选中边框过渡 ——  150ms ease-in-out
       #3 KPI 数字变化         ——  out 120ms ease-out / in 160ms ease-in
       #4 页面切换(QStackedWidget) ——  220ms ease-out, opacity 0→1 + x +12→0
       #5 通知                ——  in 240ms ease-out / out 200ms ease-in
       #6 表单错误抖动         ——  ±6→±4→±2→0，3 个周期，total 360ms
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QTimer,
    Qt,
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from design_tokens import _p


def _qcolor(hex_str: str, alpha: int = 255) -> QColor:
    c = QColor(hex_str)
    c.setAlpha(alpha)
    return c


# ────────────────────────────────────────────────────────────────
# #1 主按钮悬浮辉光呼吸
# ────────────────────────────────────────────────────────────────
class _PrimaryGlowFilter(QObject):
    """给按钮挂一层 QGraphicsDropShadowEffect 模拟"香槟金呼吸"。

    进入：blurRadius 0 → 18，color α 0 → 220，180ms ease-out
    离开：blurRadius 18 → 0, color α 220 → 0, 220ms ease-in
    """

    def __init__(self, btn: QWidget):
        super().__init__(btn)
        self._btn = btn
        self._effect = QGraphicsDropShadowEffect(btn)
        self._effect.setOffset(0, 0)
        self._effect.setBlurRadius(0)
        accent = _p("accent")
        self._color = _qcolor(accent, 0)
        self._effect.setColor(self._color)
        btn.setGraphicsEffect(self._effect)
        self._anim: Optional[QPropertyAnimation] = None
        btn.installEventFilter(self)

    def _refresh_color(self) -> None:
        accent = _p("accent")
        self._color = _qcolor(accent, self._color.alpha())
        self._effect.setColor(self._color)

    def _start(self, *, enter: bool) -> None:
        self._refresh_color()
        if self._anim and self._anim.state() == QPropertyAnimation.Running:
            self._anim.stop()
        anim = QPropertyAnimation(self._effect, b"blurRadius", self)
        anim.setDuration(180 if enter else 220)
        anim.setStartValue(self._effect.blurRadius())
        anim.setEndValue(18.0 if enter else 0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic if enter else QEasingCurve.InCubic)
        # 同步把 accent 的 alpha 推到 220 / 0，让呼吸不只是 blur 抖
        accent_target = 220 if enter else 0
        accent = _p("accent")
        target_color = _qcolor(accent, accent_target)

        def _on_value(v):
            ratio = (v / 18.0) if enter else (v / max(self._effect.blurRadius() or 1, 1))
            ratio = max(0.0, min(1.0, ratio))
            alpha = int(220 * ratio) if enter else int(self._color.alpha() * (1 - ratio))
            new_color = QColor(target_color)
            new_color.setAlpha(alpha)
            self._effect.setColor(new_color)

        anim.valueChanged.connect(_on_value)
        self._anim = anim
        anim.start(QPropertyAnimation.DeletionPolicy.KeepWhenStopped)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._btn:
            t = event.type()
            if t == QEvent.Enter:
                self._start(enter=True)
            elif t == QEvent.Leave:
                self._start(enter=False)
        return super().eventFilter(obj, event)


def attach_primary_button_glow(btn: QWidget) -> None:
    """给主操作按钮挂上香槟金呼吸悬浮辉光。"""
    if btn is None:
        return
    if getattr(btn, "_lovable_glow", None):
        return
    btn._lovable_glow = _PrimaryGlowFilter(btn)


def attach_primary_button_glow_many(buttons: list) -> None:
    for b in (buttons or []):
        attach_primary_button_glow(b)


# ────────────────────────────────────────────────────────────────
# #2 房卡 tile 选中边框过渡（150ms ease-in-out，阴影染主色）
# ────────────────────────────────────────────────────────────────
def pulse_room_select(widget: QWidget) -> None:
    """选中房卡时轻轻"盖章"——主色阴影 150ms ease-in-out 进出。"""
    if widget is None:
        return
    primary = _p("primary")
    effect = QGraphicsDropShadowEffect(widget)
    effect.setOffset(0, 0)
    effect.setColor(_qcolor(primary, 200))
    effect.setBlurRadius(0)
    widget.setGraphicsEffect(effect)

    group = QSequentialAnimationGroup(widget)
    a_in = QPropertyAnimation(effect, b"blurRadius")
    a_in.setDuration(150)
    a_in.setStartValue(0.0)
    a_in.setEndValue(22.0)
    a_in.setEasingCurve(QEasingCurve.InOutQuad)
    a_out = QPropertyAnimation(effect, b"blurRadius")
    a_out.setDuration(150)
    a_out.setStartValue(22.0)
    a_out.setEndValue(0.0)
    a_out.setEasingCurve(QEasingCurve.InOutQuad)
    group.addAnimation(a_in)
    group.addAnimation(a_out)

    def _cleanup():
        try:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass

    group.finished.connect(_cleanup)
    group.start(QSequentialAnimationGroup.DeletionPolicy.DeleteWhenStopped)


# ────────────────────────────────────────────────────────────────
# #3 KPI 数字变化（Phase 2 调优：80ms 淡出 + 120ms 滑入，3px 位移，OutQuint）
# ────────────────────────────────────────────────────────────────
def animate_kpi(label: QLabel, new_text: str) -> None:
    """像翻账本一样温柔地换 KPI 数字。"""
    if label is None:
        return
    if new_text == label.text():
        return
    base_pos = label.pos()
    effect = QGraphicsOpacityEffect(label)
    effect.setOpacity(1.0)
    label.setGraphicsEffect(effect)

    # 旧值：80ms ease-out 上移 3px + 淡出
    fade_out = QPropertyAnimation(effect, b"opacity", label)
    fade_out.setDuration(80)
    fade_out.setStartValue(1.0)
    fade_out.setEndValue(0.0)
    fade_out.setEasingCurve(QEasingCurve.OutQuint)

    move_out = QPropertyAnimation(label, b"pos", label)
    move_out.setDuration(80)
    move_out.setStartValue(base_pos)
    move_out.setEndValue(QPoint(base_pos.x(), base_pos.y() - 3))
    move_out.setEasingCurve(QEasingCurve.OutQuint)

    out_group = QParallelAnimationGroup(label)
    out_group.addAnimation(fade_out)
    out_group.addAnimation(move_out)

    def _swap_and_in():
        label.setText(new_text)
        label.move(QPoint(base_pos.x(), base_pos.y() + 3))
        fade_in = QPropertyAnimation(effect, b"opacity", label)
        fade_in.setDuration(120)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutQuint)
        move_in = QPropertyAnimation(label, b"pos", label)
        move_in.setDuration(120)
        move_in.setStartValue(QPoint(base_pos.x(), base_pos.y() + 3))
        move_in.setEndValue(base_pos)
        move_in.setEasingCurve(QEasingCurve.OutQuint)
        in_group = QParallelAnimationGroup(label)
        in_group.addAnimation(fade_in)
        in_group.addAnimation(move_in)

        def _cleanup():
            try:
                if label.graphicsEffect() is effect:
                    label.setGraphicsEffect(None)
            except RuntimeError:
                pass

        in_group.finished.connect(_cleanup)
        in_group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

    out_group.finished.connect(_swap_and_in)
    out_group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)


# ────────────────────────────────────────────────────────────────
# #4 QStackedWidget 切页（Phase 2 升级：新旧页双向动画，≤300ms）
# ────────────────────────────────────────────────────────────────
class _StackFadeFilter(QObject):
    """给 QStackedWidget 装一个 currentChanged → 旧页淡出 + 新页淡入的过渡。"""

    def __init__(self, stack: QStackedWidget):
        super().__init__(stack)
        self._stack = stack
        self._old_index = stack.currentIndex()
        stack.currentChanged.connect(self._on_changed)

    def _on_changed(self, index: int) -> None:
        old_idx = self._old_index
        self._old_index = index
        old_page = self._stack.widget(old_idx) if old_idx >= 0 else None
        new_page = self._stack.widget(index)
        if new_page is None:
            return

        # 旧页：180ms, opacity 1→0 + x 0→(-12)
        if old_page and old_page is not new_page:
            old_base = old_page.pos()
            old_effect = QGraphicsOpacityEffect(old_page)
            old_effect.setOpacity(1.0)
            old_page.setGraphicsEffect(old_effect)

            old_fade = QPropertyAnimation(old_effect, b"opacity", old_page)
            old_fade.setDuration(180)
            old_fade.setStartValue(1.0)
            old_fade.setEndValue(0.0)
            old_fade.setEasingCurve(QEasingCurve.OutCubic)

            old_slide = QPropertyAnimation(old_page, b"pos", old_page)
            old_slide.setDuration(180)
            old_slide.setStartValue(old_base)
            old_slide.setEndValue(QPoint(old_base.x() - 12, old_base.y()))
            old_slide.setEasingCurve(QEasingCurve.OutCubic)

            old_group = QParallelAnimationGroup(old_page)
            old_group.addAnimation(old_fade)
            old_group.addAnimation(old_slide)

            def _old_cleanup(p=old_page, e=old_effect):
                try:
                    if p.graphicsEffect() is e:
                        p.setGraphicsEffect(None)
                except RuntimeError:
                    pass

            old_group.finished.connect(_old_cleanup)
            old_group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

        # 新页：220ms, opacity 0→1 + x +12→0（与新页动画重叠 ≤300ms 总时长）
        base_pos = new_page.pos()
        new_page.move(QPoint(base_pos.x() + 12, base_pos.y()))
        new_effect = QGraphicsOpacityEffect(new_page)
        new_effect.setOpacity(0.0)
        new_page.setGraphicsEffect(new_effect)

        fade = QPropertyAnimation(new_effect, b"opacity", new_page)
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)

        slide = QPropertyAnimation(new_page, b"pos", new_page)
        slide.setDuration(220)
        slide.setStartValue(QPoint(base_pos.x() + 12, base_pos.y()))
        slide.setEndValue(base_pos)
        slide.setEasingCurve(QEasingCurve.OutCubic)

        group = QParallelAnimationGroup(new_page)
        group.addAnimation(fade)
        group.addAnimation(slide)

        def _cleanup():
            try:
                if new_page.graphicsEffect() is new_effect:
                    new_page.setGraphicsEffect(None)
            except RuntimeError:
                pass

        group.finished.connect(_cleanup)
        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)


def attach_stack_fade(stack: QStackedWidget) -> None:
    """给主 QStackedWidget 挂上"翻账册式"切页动效。一次即可。"""
    if stack is None:
        return
    if getattr(stack, "_lovable_stack_fade", None):
        return
    stack._lovable_stack_fade = _StackFadeFilter(stack)


# ────────────────────────────────────────────────────────────────
# #5 Toast —— in 240ms ease-out / 停留 3s / out 200ms ease-in
# ────────────────────────────────────────────────────────────────
class LovableToast(QWidget):
    """像管家在你耳边低语一句然后退下的通知。

    用法：
        LovableToast.show_in(parent, "结账成功")
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("LovableToast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        bg = _p("surface")
        border = _p("border")
        fg = _p("text")
        self.setStyleSheet(
            f"QWidget#LovableToast{{background:{bg};border:1px solid {border};"
            f"border-radius:12px;}}"
        )
        self.setMinimumHeight(52)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 0, 20, 0)
        self._lbl = QLabel(self)
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setStyleSheet(
            f"color:{fg};background:transparent;font-size:13px;font-weight:600;"
        )
        lay.addWidget(self._lbl)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self.hide()

    def _target_pos(self) -> QPoint:
        if self.parent() is not None:
            pr = self.parent().rect()
            return QPoint((pr.width() - self.width()) // 2, 24)
        return QPoint(0, 24)

    def show_toast(self, text: str, dur_ms: int = 2500) -> None:
        self._lbl.setText(text)
        self.adjustSize()
        self.setMinimumHeight(52)
        self.setFixedWidth(max(self._lbl.sizeHint().width() + 60, 240))
        self._show_anim()
        QTimer.singleShot(dur_ms, self._hide)

    def _show_anim(self) -> None:
        """进入动画：x +20→0 + y +12→0 + opacity 0→1, 250ms OutCubic。"""
        tgt = self._target_pos()
        parent_w = self.parent().rect().width() if self.parent() else 800
        self.move(QPoint(parent_w, tgt.y() + 12))
        self.show()
        self.raise_()

        fade_in = QPropertyAnimation(self._opacity, b"opacity", self)
        fade_in.setDuration(250)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)

        slide_in = QPropertyAnimation(self, b"pos", self)
        slide_in.setDuration(250)
        slide_in.setStartValue(QPoint(parent_w, tgt.y() + 12))
        slide_in.setKeyValueAt(0.6, QPoint(tgt.x() + 6, tgt.y() + 4))
        slide_in.setEndValue(tgt)
        slide_in.setEasingCurve(QEasingCurve.OutCubic)

        enter = QParallelAnimationGroup(self)
        enter.addAnimation(fade_in)
        enter.addAnimation(slide_in)
        enter.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

    def _hide(self) -> None:
        tgt = self._target_pos()
        off_x = self.parent().rect().width() if self.parent() else 800
        fade_out = QPropertyAnimation(self._opacity, b"opacity", self)
        fade_out.setDuration(200)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.InCubic)

        slide_out = QPropertyAnimation(self, b"pos", self)
        slide_out.setDuration(200)
        slide_out.setStartValue(self.pos())
        slide_out.setEndValue(QPoint(off_x, tgt.y() + 6))
        slide_out.setEasingCurve(QEasingCurve.InCubic)

        leave = QParallelAnimationGroup(self)
        leave.addAnimation(fade_out)
        leave.addAnimation(slide_out)
        leave.finished.connect(self.hide)
        leave.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

    @classmethod
    def show_in(cls, parent: QWidget, text: str, dur_ms: int = 3000) -> "LovableToast":
        t = cls(parent)
        t.show_toast(text, dur_ms)
        return t


# #6 表单校验失败抖动 → 见 Phase 2 §2.9 调优版（±8→±5→±3→±1→0，250ms）


# ── Phase 4: 阴影层次 — 卡片浮起阴影 ─────────────────────────

def attach_card_shadow(card: QWidget, level: str = "sm") -> None:
    """给 FdCard / ContentBox 等卡片组件挂上阴影 QGraphicsDropShadowEffect。"""
    # 避免与 motion_gate 循环导入，直接检查动效开关
    try:
        from motion_gate import _motion_enabled
        if not _motion_enabled():
            return
    except Exception:
        pass
    text_color = _p("text")
    blur_map = {"sm": 12, "md": 18, "lg": 24}
    offset_map = {"sm": 2, "md": 4, "lg": 6}
    alpha_map = {"sm": 0.15, "md": 0.15, "lg": 0.20}
    blur = blur_map.get(level, 12)
    off = offset_map.get(level, 2)
    alpha = alpha_map.get(level, 0.15)
    shadow = QGraphicsDropShadowEffect(card)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, off)
    # Parse hex to QColor with alpha
    c = QColor(text_color)
    c.setAlphaF(alpha)
    shadow.setColor(c)
    # 清理旧 shadow 以防泄漏
    old = card.graphicsEffect()
    if old is not None:
        old.deleteLater()
    card.setGraphicsEffect(shadow)


# ────────────────────────────────────────────────────────────────
# 便捷批量挂载：在调用方一行接入工作台 5 按钮 / 收款按钮
# ────────────────────────────────────────────────────────────────
def install_workspace_dock_motion(dock) -> None:
    """把 workspace_dock 的 5 厚按钮 + 收款按钮一次性挂上呼吸辉光。"""
    if dock is None:
        return
    for name in (
        "btn_issue_card",
        "btn_read_card",
        "btn_cancel_card",
        "btn_co",
        "btn_lost_card",
        "btn_commit",
        "btn_pay",
    ):
        btn = getattr(dock, name, None)
        if btn is not None:
            attach_primary_button_glow(btn)


# ────────────────────────────────────────────────────────────────
# Phase 2: 九项微交互注入（120% 终极优雅方案）
# ────────────────────────────────────────────────────────────────

# ── 2.1 按钮按压缩放 ──────────────────────────────────────────
class _ButtonPressFilter(QObject):
    """按钮按压时 0.97x 缩入，释放时 1.0x 弹回 + 弹簧过冲。"""

    def __init__(self, btn: QWidget):
        super().__init__(btn)
        self._btn = btn
        self._effect = None
        btn.installEventFilter(self)

    def _start_press(self) -> None:
        if self._effect is None:
            from PySide6.QtWidgets import QGraphicsScaleEffect
            self._effect = QGraphicsScaleEffect(self._btn)
            self._btn.setGraphicsEffect(self._effect)
        anim = QPropertyAnimation(self._effect, b"horizontalScale", self)
        anim.setDuration(80)
        anim.setStartValue(1.0)
        anim.setEndValue(0.97)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        v_anim = QPropertyAnimation(self._effect, b"verticalScale", self)
        v_anim.setDuration(80)
        v_anim.setStartValue(1.0)
        v_anim.setEndValue(0.97)
        v_anim.setEasingCurve(QEasingCurve.OutCubic)
        group = QParallelAnimationGroup(self)
        group.addAnimation(anim)
        group.addAnimation(v_anim)
        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

    def _start_release(self) -> None:
        if self._effect is None:
            return
        anim = QPropertyAnimation(self._effect, b"horizontalScale", self)
        anim.setDuration(120)
        anim.setStartValue(0.97)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutBack)
        v_anim = QPropertyAnimation(self._effect, b"verticalScale", self)
        v_anim.setDuration(120)
        v_anim.setStartValue(0.97)
        v_anim.setEndValue(1.0)
        v_anim.setEasingCurve(QEasingCurve.OutBack)
        group = QParallelAnimationGroup(self)
        group.addAnimation(anim)
        group.addAnimation(v_anim)
        v_anim.finished.connect(self._cleanup_effect)
        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

    def _cleanup_effect(self) -> None:
        try:
            if self._btn is not None and self._btn.graphicsEffect() is self._effect:
                self._btn.setGraphicsEffect(None)
        except RuntimeError:
            pass
        self._effect = None

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._btn:
            t = event.type()
            if t == QEvent.MouseButtonPress:
                self._start_press()
            elif t == QEvent.MouseButtonRelease:
                self._start_release()
        return super().eventFilter(obj, event)


def attach_button_press_effect(btn: QWidget) -> None:
    """挂载按钮按压缩放动效。"""
    if btn is None:
        return
    if getattr(btn, "_lovable_btn_press", None):
        return
    btn._lovable_btn_press = _ButtonPressFilter(btn)


# ── 2.4 输入框焦点光晕 ──────────────────────────────────────
class _InputGlowFilter(QObject):
    """输入框获得焦点时挂主色外发光，失焦时移除。"""

    def __init__(self, widget: QWidget):
        super().__init__(widget)
        self._widget = widget
        widget.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._widget:
            t = event.type()
            if t == QEvent.FocusIn:
                self._apply_glow()
            elif t == QEvent.FocusOut:
                self._remove_glow()
        return super().eventFilter(obj, event)

    def _apply_glow(self) -> None:
        gold = _p("accent")
        c = QColor(gold)
        c.setAlphaF(0.3)
        shadow = QGraphicsDropShadowEffect(self._widget)
        shadow.setBlurRadius(10)
        shadow.setOffset(0, 0)
        shadow.setColor(c)
        old = self._widget.graphicsEffect()
        if old is not None:
            old.deleteLater()
        self._widget.setGraphicsEffect(shadow)

    def _remove_glow(self) -> None:
        try:
            if self._widget.graphicsEffect() is not None:
                self._widget.setGraphicsEffect(None)
        except RuntimeError:
            pass


def attach_input_glow(input_widget: QWidget) -> None:
    """挂载输入框焦点光晕。"""
    if input_widget is None:
        return
    if getattr(input_widget, "_lovable_input_glow", None):
        return
    input_widget._lovable_input_glow = _InputGlowFilter(input_widget)


# ── 2.5 表格行 hover 过渡 ─────────────────────────────────────
class _TableHoverFilter(QObject):
    """表格行在 150ms 内渐变背景色。"""

    def __init__(self, table):
        super().__init__(table)
        self._table = table
        self._last_hover_row = -1
        table.installEventFilter(self)
        table.viewport().installEventFilter(self)
        self._hover_color = QColor()
        self._base_color = QColor()
        self._refresh_colors()

    def _refresh_colors(self) -> None:
        base_hex = _p("surface")
        hover_hex = _p("primary_10pct")
        self._base_color = QColor(base_hex)
        self._hover_color = QColor(hover_hex)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.MouseMove:
            pos = event.position() if hasattr(event, "position") else event.pos()
            idx = self._table.indexAt(pos.toPoint()) if hasattr(pos, "toPoint") else self._table.indexAt(pos)
            row = idx.row() if idx.isValid() else -1
            if row != self._last_hover_row:
                self._restore_row(self._last_hover_row)
                self._hover_row(row)
                self._last_hover_row = row
        elif event.type() == QEvent.Leave:
            self._restore_row(self._last_hover_row)
            self._last_hover_row = -1
        return super().eventFilter(obj, event)

    def _hover_row(self, row: int) -> None:
        if row < 0:
            return
        try:
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item is not None:
                    item.setBackground(QBrush(self._hover_color))
        except Exception:
            pass

    def _restore_row(self, row: int) -> None:
        if row < 0:
            return
        try:
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item is not None:
                    item.setBackground(QBrush(self._base_color))
        except Exception:
            pass


def attach_table_hover_transition(table) -> None:
    """挂载表格行 hover 过渡动效。"""
    if table is None:
        return
    if getattr(table, "_lovable_table_hover", None):
        return
    table._lovable_table_hover = _TableHoverFilter(table)


# ── 2.6 侧栏 active 指示条脉冲 ──────────────────────────────
class _SidebarPulseFilter(QObject):
    """侧栏按钮 active 时左金线透明度脉冲 0.6→1.0→0.6。"""

    def __init__(self, btn: QWidget):
        super().__init__(btn)
        self._btn = btn
        self._anim: Optional[QPropertyAnimation] = None

    def start_pulse(self) -> None:
        if self._anim and self._anim.state() == QPropertyAnimation.Running:
            return
        effect = QGraphicsOpacityEffect(self._btn)
        effect.setOpacity(0.6)
        self._btn.setGraphicsEffect(effect)
        self._anim = QPropertyAnimation(effect, b"opacity", self._btn)
        self._anim.setDuration(2000)
        self._anim.setStartValue(0.6)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setEndValue(0.6)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.setLoopCount(-1)  # 无限循环
        self._anim.start(QPropertyAnimation.DeletionPolicy.KeepWhenStopped)

    def stop_pulse(self) -> None:
        if self._anim:
            self._anim.stop()
            self._anim = None
        try:
            if self._btn.graphicsEffect() is not None:
                self._btn.setGraphicsEffect(None)
        except RuntimeError:
            pass


def attach_sidebar_pulse(btn: QPushButton) -> None:
    """挂载侧栏 active 按钮指示条脉冲动效。"""
    if btn is None:
        return
    filter_obj = getattr(btn, "_lovable_sidebar_pulse", None)
    if filter_obj is None:
        filter_obj = _SidebarPulseFilter(btn)
        btn._lovable_sidebar_pulse = filter_obj
    if btn.property("active"):
        filter_obj.start_pulse()
    else:
        filter_obj.stop_pulse()


# ── 2.9 表单抖动调优 ─────────────────────────────────────────
def shake_invalid(widget: QWidget) -> None:
    """老派会计的轻轻摇头（Phase 2 调优版：±8→±5→±3→±1→0，5步250ms）。"""
    if widget is None:
        return
    base = widget.pos()
    seq = QSequentialAnimationGroup(widget)
    offsets = [+8, -8, +5, -5, +3, -3, +1, -1]
    for off in offsets:
        step = QPropertyAnimation(widget, b"pos", seq)
        step.setDuration(50)
        step.setStartValue(widget.pos() if seq.animationCount() == 0 else
                           QPoint(base.x() + offsets[seq.animationCount() - 1], base.y()))
        step.setEndValue(QPoint(base.x() + off, base.y()))
        step.setEasingCurve(QEasingCurve.InOutQuad)
        seq.addAnimation(step)
    home = QPropertyAnimation(widget, b"pos", seq)
    home.setDuration(0)
    home.setStartValue(QPoint(base.x() + offsets[-1], base.y()))
    home.setEndValue(base)
    seq.addAnimation(home)
    seq.start(QSequentialAnimationGroup.DeletionPolicy.DeleteWhenStopped)
