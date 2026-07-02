from pathlib import Path
from typing import Dict, Optional, Tuple

from PySide6.QtCore import Qt, QPointF, QRectF, QTimer, QPropertyAnimation, Property, QEasingCurve
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QLinearGradient
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QHeaderView,
)

# ═══════════════════════════════════════════════════════════════════════════
# 全局弹窗尺寸标准（所有弹窗/向导必须通过 style_dialog 应用）
# ───────────────────────────────────────────────────────────────────────────
# · 默认不超过屏幕可用区域的 92% 宽 × 85% 高
# · 绝对上限 920×720，保证 1366×768 本也能看到底部按钮
# · 内容超出时在弹窗内使用滚动区域，禁止靠撑高窗口
# ═══════════════════════════════════════════════════════════════════════════
DIALOG_SCREEN_MAX_W_RATIO = 0.92
DIALOG_SCREEN_MAX_H_RATIO = 0.85
DIALOG_ABSOLUTE_MAX_W = 920
DIALOG_ABSOLUTE_MAX_H = 720
DIALOG_GLOBAL_MIN_W = 360
DIALOG_GLOBAL_MIN_H = 260
DIALOG_MARGIN_SCREEN = 24

# 命名尺寸档（业务代码优先传 size=，不要写死超大宽高）
DIALOG_SIZE_PRESETS: Dict[str, Tuple[int, int]] = {
    "compact": (420, 300),
    "small": (480, 400),
    "medium": (560, 520),
    "large": (680, 580),
    "xlarge": (800, 640),
}

# ── 全局图标（懒加载，避免 QApplication 未初始化时崩溃）──
_APP_ICON: QIcon | None = None

def _get_app_icon() -> QIcon:
    global _APP_ICON
    if _APP_ICON is None:
        icon_path = Path(__file__).parent / "assets" / "app_icon.png"
        _APP_ICON = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
    return _APP_ICON

def _apply_icon(widget):
    """给任意 QWidget（窗口/弹窗）设置应用图标"""
    icon = _get_app_icon()
    if not icon.isNull():
        widget.setWindowIcon(icon)






def _theme_message_box_qss() -> str:
    """v7 消息框 — 圆角 12px + 柔和 + 统一按钮 36px。"""
    from design_tokens import _p
    surface = _p("surface")
    surface_alt = _p("bg")
    foreground = _p("text")
    text_muted = _p("text_muted")
    border = _p("border")
    primary = _p("primary")
    return f"""
QMessageBox {{
    background: {surface};
    border-radius: 12px;
}}
QLabel {{
    color: {foreground};
    font-size: 13px;
    background: transparent;
}}
QPushButton {{
    min-height: 36px;
    max-height: 36px;
    padding: 0 20px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 13px;
    background: {surface_alt};
    color: {foreground};
    border: 1px solid {border};
}}
QPushButton:hover {{
    background: {primary};
    color: {surface};
    border-color: {primary};
}}
"""


def get_dialog_screen_limits() -> Tuple[int, int, int, int]:
    """返回 (max_w, max_h, min_w, min_h)，已综合屏幕比例与绝对上限。"""
    screen = QApplication.primaryScreen()
    ag = screen.availableGeometry() if screen else None
    if ag:
        max_w = min(int(ag.width() * DIALOG_SCREEN_MAX_W_RATIO), DIALOG_ABSOLUTE_MAX_W, ag.width() - DIALOG_MARGIN_SCREEN)
        max_h = min(int(ag.height() * DIALOG_SCREEN_MAX_H_RATIO), DIALOG_ABSOLUTE_MAX_H, ag.height() - DIALOG_MARGIN_SCREEN)
    else:
        max_w, max_h = DIALOG_ABSOLUTE_MAX_W, DIALOG_ABSOLUTE_MAX_H
    return max_w, max_h, DIALOG_GLOBAL_MIN_W, DIALOG_GLOBAL_MIN_H


def _clamp_dim(value: int, lo: int, hi: int) -> int:
    return max(lo, min(value, hi))


def fit_dialog_dimensions(
    width: Optional[int] = None,
    height: Optional[int] = None,
    min_width: Optional[int] = None,
    min_height: Optional[int] = None,
    *,
    size: Optional[str] = None,
) -> Tuple[int, int, int, int]:
    """
    根据全局标准计算 (w, h, min_w, min_h)。
    size 可选: compact | small | medium | large | xlarge
    """
    max_w, max_h, g_min_w, g_min_h = get_dialog_screen_limits()
    if size and size in DIALOG_SIZE_PRESETS:
        pw, ph = DIALOG_SIZE_PRESETS[size]
        width = width if width is not None else pw
        height = height if height is not None else ph
    w = _clamp_dim(width or DIALOG_SIZE_PRESETS["medium"][0], g_min_w, max_w)
    h = _clamp_dim(height or DIALOG_SIZE_PRESETS["medium"][1], g_min_h, max_h)
    mw = _clamp_dim(min_width if min_width is not None else min(w, g_min_w + 80), g_min_w, max_w - 8)
    mh = _clamp_dim(min_height if min_height is not None else min(h, g_min_h + 40), g_min_h, max_h - 8)
    if mw > w:
        w = mw
    if mh > h:
        h = mh
    w = min(w, max_w)
    h = min(h, max_h)
    return w, h, mw, mh


def center_dialog_on_screen(dialog: QDialog) -> None:
    screen = QApplication.primaryScreen()
    if not screen:
        return
    ag = screen.availableGeometry()
    max_w, max_h, _, _ = get_dialog_screen_limits()
    w = _clamp_dim(dialog.width(), DIALOG_GLOBAL_MIN_W, max_w)
    h = _clamp_dim(dialog.height(), DIALOG_GLOBAL_MIN_H, max_h)
    dialog.resize(w, h)
    frame = dialog.frameGeometry()
    frame.moveCenter(ag.center())
    dialog.move(frame.topLeft())


def _install_dialog_show_bounds(dialog: QDialog) -> None:
    if getattr(dialog, "_solid_dialog_bounds", False):
        return
    dialog._solid_dialog_bounds = True
    _orig_show = dialog.showEvent

    def _show_event(event):
        center_dialog_on_screen(dialog)
        if _orig_show:
            _orig_show(event)

    dialog.showEvent = _show_event  # type: ignore[method-assign]


def make_dialog_scroll_area(
    content: QWidget,
    *,
    horizontal: Qt.ScrollBarPolicy = Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
) -> QScrollArea:
    """把易撑高的内容包进滚动区，避免弹窗顶破屏幕。"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(horizontal)
    scroll.setWidget(content)
    content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return scroll


# ── 模态遮罩管理 ────────────────────────────────────────────────────
_MODAL_OVERLAYS: dict[int, QWidget] = {}

def _show_modal_overlay(dialog: QDialog) -> None:
    """在弹窗父容器上铺一层半透明遮罩。"""
    parent = dialog.parent()
    if parent is None or not parent.isWidgetType():
        return
    # 先清理该 dialog 的旧遮罩（防止重复调用）
    old = _MODAL_OVERLAYS.pop(id(dialog), None)
    if old is not None:
        try:
            old.hide()
            old.deleteLater()
        except RuntimeError:
            pass
    overlay = QWidget(parent)
    overlay.setObjectName("ModalOverlay")
    overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
    overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    from design_tokens import _p
    bg = _p("bg")
    overlay.setStyleSheet(
        f"QWidget#ModalOverlay{{background:{bg};border:none;}}"
    )
    overlay.setGraphicsEffect(_make_overlay_blur(overlay))
    overlay.resize(parent.size())
    overlay.show()
    overlay.raise_()
    _MODAL_OVERLAYS[id(dialog)] = overlay


def _hide_modal_overlay(dialog: QDialog) -> None:
    """移除 dialog 的模态遮罩。"""
    overlay = _MODAL_OVERLAYS.pop(id(dialog), None)
    if overlay is not None:
        try:
            overlay.hide()
            overlay.deleteLater()
        except RuntimeError:
            pass


def _make_overlay_blur(parent: QWidget):
    """半透明遮罩效果（未使用模糊效果因为性能开销大，改纯透明）。"""
    try:
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        effect = QGraphicsOpacityEffect(parent)
        effect.setOpacity(0.4)
        return effect
    except (RuntimeError, ImportError, AttributeError):
        # RuntimeError: C++ 对象已被销毁；ImportError: 极少见的环境问题；
        # AttributeError: 老版 PySide6 无此 API
        return None


def style_dialog(
    dialog,
    width: Optional[int] = None,
    height: Optional[int] = None,
    min_width: Optional[int] = None,
    min_height: Optional[int] = None,
    *,
    size: Optional[str] = None,
    no_overlay: bool = False,
):
    """
    应用全局弹窗尺寸标准。推荐：style_dialog(dlg, size='medium')
    禁止单独 setMinimumSize(>720 高) 而不包滚动区。

    no_overlay=True 跳过模态遮罩（确保全屏弹窗不误点穿透时使用）。
    """
    _apply_icon(dialog)
    max_w, max_h, _, _ = get_dialog_screen_limits()
    w, h, mw, mh = fit_dialog_dimensions(width, height, min_width, min_height, size=size)
    dialog.resize(w, h)
    dialog.setMaximumSize(max_w, max_h)
    dialog.setMinimumSize(mw, mh)
    if isinstance(dialog, QDialog):
        _install_dialog_show_bounds(dialog)
        if not no_overlay:
            original_exec = dialog.exec
            def _exec_with_overlay(*a, **kw):
                _show_modal_overlay(dialog)
                try:
                    return original_exec(*a, **kw)
                finally:
                    _hide_modal_overlay(dialog)
            dialog.exec = _exec_with_overlay

            original_open = dialog.open
            def _open_with_overlay(*a, **kw):
                _show_modal_overlay(dialog)
                original_open(*a, **kw)
            dialog.open = _open_with_overlay

            original_done = dialog.done
            def _done_with_overlay(*a, **kw):
                _hide_modal_overlay(dialog)
                original_done(*a, **kw)
            dialog.done = _done_with_overlay

    # 追加对话框主题 QSS（颜色跟随当前主题，覆盖全局 QSS）
    try:
        dialog.setStyleSheet(dialog.styleSheet() + "\n" + _theme_dialog_qss())
    except (RuntimeError, AttributeError):
        # RuntimeError: dialog 已被销毁；AttributeError: styleSheet 缺失
        pass


def style_wizard(wizard, size: str = "large"):
    """QWizard 等宽向导窗口，规则与 style_dialog 相同。"""
    style_dialog(wizard, size=size)


def build_dialog_header(title, subtitle=""):
    """v7 弹窗头部 — 顶部 accent 色条 + 标题 + 副标题。"""
    header = QFrame()
    header.setObjectName("DialogHeader")
    header.setAutoFillBackground(True)
    try:
        from design_tokens import _p
        p = header.palette()
        p.setColor(QPalette.ColorRole.Window, QColor(_p("surface")))
        header.setPalette(p)
    except (RuntimeError, KeyError, ValueError):
        # RuntimeError: widget 已销毁；KeyError/ValueError: design_tokens 缺失该 key
        pass
    layout = QVBoxLayout(header)
    layout.setContentsMargins(20, 16, 20, 14)
    layout.setSpacing(4)

    lbl_title = QLabel(title)
    lbl_title.setObjectName("DialogTitle")
    layout.addWidget(lbl_title)

    if subtitle:
        lbl_subtitle = QLabel(subtitle)
        lbl_subtitle.setObjectName("DialogSubtitle")
        lbl_subtitle.setWordWrap(True)
        layout.addWidget(lbl_subtitle)

    return header


def _make_message_box(parent, icon, title, text, buttons):
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(buttons)
    box.setStyleSheet(_theme_message_box_qss())
    _apply_icon(box)             # ← 统一注入图标

    yes_btn = box.button(QMessageBox.Yes)
    no_btn = box.button(QMessageBox.No)
    ok_btn = box.button(QMessageBox.Ok)
    cancel_btn = box.button(QMessageBox.Cancel)

    if yes_btn:
        yes_btn.setText("确定")
    if no_btn:
        no_btn.setText("取消")
    if ok_btn:
        ok_btn.setText("确定")
    if cancel_btn:
        cancel_btn.setText("取消")
    return box


def style_data_table(tbl: QTableWidget, *, min_height: int = 140) -> None:
    """统一数据表密度与可读性。"""
    tbl.setAlternatingRowColors(False)
    tbl.setShowGrid(False)
    tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tbl.verticalHeader().setVisible(False)
    tbl.setMinimumHeight(min_height)
    hdr = tbl.horizontalHeader()
    hdr.setStretchLastSection(True)
    hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    hdr.setMinimumHeight(24)
    hdr.setMaximumHeight(40)


# sub-c UI/UX 打磨：长列表样式（max-h-96 = 384px）
LONG_LIST_MAX_HEIGHT = 384  # 对应 Tailwind max-h-96


def style_long_list(widget, *, max_height: int = LONG_LIST_MAX_HEIGHT) -> None:
    """给 QTableWidget / QListWidget 打上 max-h-96（384px）+ 长列表视觉规则。

    与 style_data_table 的区别：
    - style_data_table：只调密度/排版，不限高（适合房态矩阵等大表）
    - style_long_list：限高 384px，超出自动滚动（适合员工列表/历史记录等"长尾"列表）

    用法::

        style_long_list(self.staff_table)        # QTableWidget
        style_long_list(self.history_list)       # QListWidget
    """
    widget.setObjectName("SolidLongList")
    try:
        widget.setMaximumHeight(int(max_height))
    except (AttributeError, TypeError):
        # AttributeError: 极少 widget 不支持 setMaximumHeight
        # TypeError: max_height 非数字
        pass
    # 复用 style_data_table 的密度规则（仅 QTableWidget 受益）
    if isinstance(widget, QTableWidget):
        style_data_table(widget, min_height=120)
    # QListWidget 也加上交替行 + 不可编辑
    elif isinstance(widget, QListWidget):
        try:
            widget.setAlternatingRowColors(False)
            widget.setUniformItemSizes(True)
            widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        except (RuntimeError, AttributeError):
            pass


# ── 制卡四态状态卡（前台 / 诊断页共用）────────────────────────────────
CARD_PROMPT_IDLE = "idle"
CARD_PROMPT_WAIT = "wait_card"
CARD_PROMPT_WRITING = "writing"
CARD_PROMPT_SUCCESS = "success"
CARD_PROMPT_FAIL = "fail"

def _get_card_prompt_theme() -> dict:
    """动态构建制卡状态色板，跟随当前主题。"""
    from design_tokens import _p
    return {
        CARD_PROMPT_IDLE: ("•", _p("text_muted"), _p("surface"), _p("border")),
        CARD_PROMPT_WAIT: ("📡", _p("primary"), _p("surface"), _p("primary")),
        CARD_PROMPT_WRITING: ("✍️", _p("primary"), _p("surface"), _p("primary")),
        CARD_PROMPT_SUCCESS: ("✅", _p("amount_positive"), _p("surface"), _p("amount_positive")),
        CARD_PROMPT_FAIL: ("❌", _p("danger"), _p("surface"), _p("danger")),
    }


# ── emoji 安全降级 ──────────────────────────────────────────────

_STATUS_EMOJI_MAP = {
    "ACTIVE":               ("✅", "[OK]"),
    "CANCELLED":            ("🚫", "[X]"),
    "ERASED":               ("🚫", "[X]"),
    "EXPIRED":              ("⏰", "[!]"),
    "LOST":                 ("⚠️", "[!]"),
    "LOST_PENDING":         ("⚠️", "[!]"),
    "LOST_PENDING_PHYSICAL":("⚠️", "[!]"),
}


def safe_status_icon(status: str) -> str:
    """优先使用表情符号，无法渲染时降级为文字符号。"""
    emoji, fallback = _STATUS_EMOJI_MAP.get(str(status).upper(), ("", ""))
    try:
        import sys
        if getattr(sys, 'frozen', False) and sys.platform == 'win32' and sys.getwindowsversion().build < 10240:
            return fallback
    except (AttributeError, OSError):
        # AttributeError: sys.getwindowsversion 在非 Windows 不存在
        # OSError: 极端情况下系统调用失败
        pass
    return emoji


def _theme_dialog_qss() -> str:
    """弹窗 v7 四时之色视觉 — 圆角 12px + 柔和浮起 + 统一按钮 36px。"""
    from design_tokens import _p

    surface = _p("surface")
    surface_alt = _p("bg")
    bg_container = _p("bg")
    foreground = _p("text")
    text_muted = _p("text_muted")
    text_dim = _p("text_dim")
    border = _p("border")
    primary = _p("primary")
    accent = _p("accent")
    danger = _p("danger")

    return f"""
QDialog {{
    background-color: {surface};
}}
QFrame#DialogHeader {{
    background: {surface};
    border: none;
    border-bottom: 1px solid {border};
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
    padding: 16px 20px;
    min-height: 56px;
}}
QLabel#DialogTitle {{
    color: {foreground};
    font-size: 16px;
    font-weight: 700;
    background: transparent;
    letter-spacing: 0.3px;
}}
QLabel#DialogSubtitle {{
    color: {text_muted};
    font-size: 12px;
    background: transparent;
    margin-top: 2px;
}}
QDialogButtonBox QPushButton {{
    min-height: 36px;
    max-height: 36px;
    padding: 0 20px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 13px;
    background: {surface_alt};
    color: {foreground};
    border: 1px solid {border};
}}
QDialogButtonBox QPushButton:hover {{
    background: {primary};
    color: {surface};
    border-color: {primary};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background: {surface};
    color: {foreground};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 7px 12px;
    font-size: 13px;
    min-height: 36px;
    selection-background-color: {primary};
    selection-color: {surface};
}}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{
    border: 2px solid {accent};
    padding: 6px 11px;
}}
QTableWidget {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 8px;
    gridline-color: {border};
    alternate-background-color: {surface_alt};
}}
QHeaderView::section {{
    background: {bg_container};
    color: {text_muted};
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.8px;
    border: none;
    border-bottom: 1px solid {border};
    padding: 9px 12px;
    min-height: 36px;
}}
QTableWidget::item {{
    padding: 8px 12px;
    border-bottom: 1px solid {border};
}}
QTableWidget::item:selected {{
    background: {primary};
    color: {surface};
}}
QGroupBox {{
    background: transparent;
    border: 1px solid {border};
    border-radius: 8px;
    margin-top: 16px;
    padding: 14px;
    font-weight: 600;
}}
QCheckBox, QRadioButton {{
    color: {foreground};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {border};
    border-radius: 4px;
    background: {surface};
}}
QCheckBox::indicator:checked {{
    background: {primary};
    border-color: {primary};
}}
"""


class CardPromptWidget(QFrame):
    """请放置房卡 → 检测中 → 滴 → 成功/失败 四态动画面板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        from design_tokens import _p
        self._phase = CARD_PROMPT_IDLE
        self._pulse_on = False
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(420)
        self._pulse_timer.timeout.connect(self._pulse)
        self._current_bg = _p("bg")
        self._current_border = _p("border")
        self.setObjectName("StateCard")
        sv = QVBoxLayout(self)
        sv.setContentsMargins(14, 12, 14, 12)
        sv.setSpacing(6)

        top_row = QHBoxLayout()
        self.state_icon = QLabel("•")
        self.state_icon.setStyleSheet("font-size:22px; font-weight:800;")
        self.state_icon.setFixedWidth(36)
        top_row.addWidget(self.state_icon)
        self.state_title = QLabel("待开始")
        self.state_title.setObjectName("H2Title")
        self.state_title.setStyleSheet("font-weight:700;")
        top_row.addWidget(self.state_title, 1)
        sv.addLayout(top_row)

        self.state_sub = QLabel("点击下方「开始制卡」后，把房卡贴到发卡器上。")
        self.state_sub.setObjectName("Small")
        self.state_sub.setWordWrap(True)
        sv.addWidget(self.state_sub)

        self.state_detail = QLabel("")
        self.state_detail.setObjectName("Small")
        self.state_detail.setWordWrap(True)
        self.state_detail.setVisible(False)
        sv.addWidget(self.state_detail)

        self.set_phase(CARD_PROMPT_IDLE)

    def _apply_card_qss(self, bg: str, border: str, *, strong: bool = False) -> None:
        width = 2 if strong else 1
        self.setStyleSheet(
            "QFrame#StateCard {"
            f" background:{bg}; border:{width}px solid {border};"
            " border-radius:14px; padding:14px;"
            "}"
        )

    def _pulse(self) -> None:
        if self._phase not in (CARD_PROMPT_WAIT, CARD_PROMPT_WRITING):
            self._pulse_timer.stop()
            return
        self._pulse_on = not self._pulse_on
        self._apply_card_qss(self._current_bg, self._current_border, strong=self._pulse_on)
        if self._phase == CARD_PROMPT_WAIT:
            self.state_icon.setText("📡" if self._pulse_on else "📶")
        elif self._phase == CARD_PROMPT_WRITING:
            self.state_icon.setText("✍️" if self._pulse_on else "▰")

    def _flash_done(self) -> None:
        self._apply_card_qss(self._current_bg, self._current_border, strong=True)
        QTimer.singleShot(520, lambda: self._apply_card_qss(self._current_bg, self._current_border))

    def set_phase(
        self,
        phase: str,
        *,
        title: str = "",
        sub: str = "",
        detail: str = "",
    ) -> None:
        self._phase = phase
        icon, color, bg, border = _get_card_prompt_theme().get(
            phase, _get_card_prompt_theme()[CARD_PROMPT_IDLE]
        )
        self._current_bg = bg
        self._current_border = border
        if phase in (CARD_PROMPT_WAIT, CARD_PROMPT_WRITING):
            if not self._pulse_timer.isActive():
                self._pulse_on = False
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._pulse_on = False
        self.state_icon.setText(icon)
        self.state_icon.setStyleSheet(f"font-size:22px; font-weight:800; color:{color};")
        if title:
            self.state_title.setText(title)
            self.state_title.setObjectName("H2Title")
            self.state_title.setStyleSheet(f"font-weight:700; color:{color};")
        if sub:
            self.state_sub.setText(sub)
        if detail:
            self.state_detail.setText(detail)
            self.state_detail.setVisible(True)
        else:
            self.state_detail.setVisible(False)
        self._apply_card_qss(bg, border)
        if phase in (CARD_PROMPT_SUCCESS, CARD_PROMPT_FAIL):
            self._flash_done()


def card_prompt(parent=None) -> CardPromptWidget:
    """创建制卡四态状态卡（供发卡对话框/诊断页复用）。"""
    return CardPromptWidget(parent)


def _solid_message_dialog(parent, title: str, text: str, *, kind: str = "info", confirm: bool = False) -> bool:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    style_dialog(dlg, size="small")
    dlg.setStyleSheet(_theme_dialog_qss())
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(14)

    icon_map = {
        "info": "i",
        "warning": "!",
        "error": "x",
        "question": "?",
    }
    layout.addWidget(build_dialog_header(f"{icon_map.get(kind, 'i')}  {title}", text))

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    if confirm:
        btn_no = QPushButton("取消")
        btn_no.setObjectName("SecondaryBtn")
        btn_no.clicked.connect(dlg.reject)
        buttons.addWidget(btn_no)
    btn_ok = QPushButton("确定")
    btn_ok.setObjectName("SolidPrimaryBtn")
    btn_ok.clicked.connect(dlg.accept)
    buttons.addWidget(btn_ok)
    layout.addLayout(buttons)
    return dlg.exec() == QDialog.Accepted


def show_info(parent, title, text):
    return _solid_message_dialog(parent, title, text, kind="info")


def show_warning(parent, title, text):
    return _solid_message_dialog(parent, title, text, kind="warning")


def show_error(parent, title, text):
    # Lovable v3 · 节奏 #6：错误时给触发控件来一记轻巧的水平抖动
    from motion_gate import shake_invalid
    from PySide6.QtWidgets import QApplication
    focus_w = QApplication.focusWidget()
    if focus_w is not None and focus_w is not parent:
        shake_invalid(focus_w)
    elif parent is not None:
        shake_invalid(parent)
    return _solid_message_dialog(parent, title, text, kind="error")


def ask_confirm(parent, title, text):
    return _solid_message_dialog(parent, title, text, kind="question", confirm=True)


class SelectListDialog(QDialog):
    def __init__(self, parent, title, subtitle, items):
        super().__init__(parent)
        self.setWindowTitle(title)
        style_dialog(self, size="medium")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(build_dialog_header(title, subtitle))

        self.search = QLineEdit()
        self.search.setPlaceholderText("输入关键词快速筛选")
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        for item in items:
            QListWidgetItem(item, self.list_widget)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.search.textChanged.connect(self._filter)
        self.list_widget.itemDoubleClicked.connect(lambda *_: self.accept())

    def _filter(self, text):
        keyword = text.strip().upper()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(keyword not in item.text().upper())

    def selected_value(self):
        item = self.list_widget.currentItem()
        return item.text() if item else ""


class PromptTextDialog(QDialog):
    def __init__(self, parent, title, subtitle, placeholder="", password=False, default_text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        style_dialog(self, size="compact")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(build_dialog_header(title, subtitle))

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setText(default_text)
        if password:
            self.edit.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self):
        return self.edit.text().strip()


class PromptIntDialog(QDialog):
    def __init__(self, parent, title, subtitle, minimum=1, maximum=1000, value=1):
        super().__init__(parent)
        self.setWindowTitle(title)
        style_dialog(self, size="compact")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(build_dialog_header(title, subtitle))

        self.spin = QSpinBox()
        self.spin.setMinimum(minimum)
        self.spin.setMaximum(maximum)
        self.spin.setValue(value)
        self.spin.setButtonSymbols(QSpinBox.UpDownArrows)
        layout.addWidget(self.spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self):
        return int(self.spin.value())


def select_from_list(parent, title, subtitle, items):
    dlg = SelectListDialog(parent, title, subtitle, items)
    if dlg.exec():
        value = dlg.selected_value()
        if value:
            return value, True
    return "", False


def prompt_text(parent, title, subtitle, placeholder="", password=False, default_text=""):
    dlg = PromptTextDialog(parent, title, subtitle, placeholder=placeholder, password=password, default_text=default_text)
    if dlg.exec():
        return dlg.value(), True
    return "", False


def apply_money_table_item(item: QTableWidgetItem, *, warn: bool = False) -> QTableWidgetItem:
    """金额列：仅数字前景色强调，不用整行/整格底色。"""
    from PySide6.QtGui import QBrush, QColor, QFont
    from design_tokens import _p

    color = _p("danger") if warn else _p("text")
    item.setForeground(QBrush(QColor(color)))
    f = item.font()
    f.setWeight(QFont.Weight.DemiBold)
    item.setFont(f)
    return item


def prompt_int(parent, title, subtitle, minimum=1, maximum=1000, value=1):
    dlg = PromptIntDialog(parent, title, subtitle, minimum=minimum, maximum=maximum, value=value)
    if dlg.exec():
        return dlg.value(), True
    return 0, False


# ═══════════════════════════════════════════════════════════════════════════
# 全局状态组件 — 空状态 / 加载中 / 错误重试
# ═══════════════════════════════════════════════════════════════════════════

class _EmptyStateIcon(QWidget):
    """用 QPainter 自绘风格空状态矢量图标，替代表情符号的标签。

    根据变体绘制不同图案，64×64 大小，边框色 2px 线宽。
    """

    def __init__(self, variant: str = "generic", parent=None):
        super().__init__(parent)
        self._variant = variant
        self.setFixedSize(64, 64)

    def set_variant(self, variant: str):
        self._variant = variant
        self.update()

    def _border_color(self) -> QColor:
        from design_tokens import _p
        return QColor(_p("border"))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._border_color(), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(QBrush())  # transparent fill

        r = QRectF(0, 0, 64, 64)
        m = 4.0  # margin
        draw_area = r.adjusted(m, m, -m, -m)

        if self._variant == "rooms":
            self._draw_rooms(p, draw_area)
        elif self._variant == "items":
            self._draw_items(p, draw_area)
        elif self._variant == "cards":
            self._draw_cards(p, draw_area)
        elif self._variant == "staff":
            self._draw_staff(p, draw_area)
        elif self._variant == "guests":
            self._draw_guests(p, draw_area)

        p.end()

    def _draw_rooms(self, p: QPainter, r: QRectF):
        """房屋轮廓：矩形墙体 + 三角形屋顶"""
        cx = r.center().x()
        # 墙体
        wall = QRectF(cx - 18, r.center().y() - 6, 36, 28)
        p.drawRect(wall)
        # 屋顶 (三角形)
        roof = QPainterPath()
        roof.moveTo(cx - 22, r.center().y() - 6)
        roof.lineTo(cx, r.top() + 4)
        roof.lineTo(cx + 22, r.center().y() - 6)
        roof.closeSubpath()
        p.drawPath(roof)
        # 门
        door = QRectF(cx - 4, wall.bottom() - 14, 8, 14)
        p.drawRect(door)

    def _draw_items(self, p: QPainter, r: QRectF):
        """箱子/盒子轮廓：圆角矩形主体 + 上盖"""
        cx, cy = r.center().x(), r.center().y()
        # 箱体
        body = QRectF(cx - 18, cy - 10, 36, 26)
        p.drawRoundedRect(body, 3, 3)
        # 盖子（上沿稍凸起的长条）
        lid = QPainterPath()
        lid.moveTo(cx - 20, cy - 10)
        lid.lineTo(cx - 18, cy - 16)
        lid.lineTo(cx + 18, cy - 16)
        lid.lineTo(cx + 20, cy - 10)
        p.drawPath(lid)
        # 锁扣小方块
        lock = QRectF(cx - 3, cy - 16, 6, 6)
        p.drawRect(lock)

    def _draw_cards(self, p: QPainter, r: QRectF):
        """卡片轮廓：两张叠加的圆角矩形"""
        cx, cy = r.center().x(), r.center().y()
        # 底卡（略微偏移）
        back = QRectF(cx - 17, cy - 11, 34, 24)
        p.drawRoundedRect(back, 3, 3)
        # 面卡
        front = QRectF(cx - 15, cy - 9, 34, 24)
        p.drawRoundedRect(front, 3, 3)
        # 芯片标志（小矩形）
        chip = QRectF(cx - 6, cy - 2, 10, 8)
        p.drawRoundedRect(chip, 2, 2)

    def _draw_staff(self, p: QPainter, r: QRectF):
        """人物轮廓：圆头 + 身体"""
        cx, cy = r.center().x(), r.center().y()
        # 头（圆形）
        head_center_y = r.top() + 18
        p.drawEllipse(QPointF(cx, head_center_y), 8, 8)
        # 身体（梯形）
        body = QPainterPath()
        body.moveTo(cx - 16, r.bottom() - 2)
        body.lineTo(cx - 10, head_center_y + 10)
        body.lineTo(cx + 10, head_center_y + 10)
        body.lineTo(cx + 16, r.bottom() - 2)
        body.closeSubpath()
        p.drawPath(body)

    def _draw_guests(self, p: QPainter, r: QRectF):
        """两人轮廓：两个小人物并列"""
        cx, cy = r.center().x(), r.center().y()
        head_r = 6
        # 左侧人物
        lx = cx - 11
        p.drawEllipse(QPointF(lx, r.top() + 16), head_r, head_r)
        body1 = QPainterPath()
        body1.moveTo(lx - 12, r.bottom() - 2)
        body1.lineTo(lx - 7, r.top() + 23)
        body1.lineTo(lx + 7, r.top() + 23)
        body1.lineTo(lx + 12, r.bottom() - 2)
        body1.closeSubpath()
        p.drawPath(body1)
        # 右侧人物
        rx = cx + 11
        p.drawEllipse(QPointF(rx, r.top() + 16), head_r, head_r)
        body2 = QPainterPath()
        body2.moveTo(rx - 12, r.bottom() - 2)
        body2.lineTo(rx - 7, r.top() + 23)
        body2.lineTo(rx + 7, r.top() + 23)
        body2.lineTo(rx + 12, r.bottom() - 2)
        body2.closeSubpath()
        p.drawPath(body2)


def build_empty_state(
    icon_text: str = "📭",
    title: str = "",
    description: str = "",
    action_text: Optional[str] = None,
    action_callback=None,
    *,
    variant: str = "generic",
) -> QFrame:
    """返回居中空状态框架，带虚线边框/矢量图标/标题/引导文案/可选操作按钮。

    用法:
        # 传统表情符号模式
        empty = build_empty_state("📭", "暂无数据", "请先创建一条记录",
                                   action_text="新建", action_callback=on_create)
        # 矢量图标模式（指定变体自动绘制风格图标）
        empty = build_empty_state("🏠", "暂无房间", action_text="添加房间",
                                   action_callback=on_create, variant="rooms")
        layout.addWidget(empty)

    变体可选: 通用(默认) | 房间 | 物品 | 卡片 | 人员 | 客人
    """
    from PySide6.QtCore import Qt as _Qt

    frame = QFrame()
    frame.setObjectName("StaffEmptyCard")
    from design_tokens import _p
    border_color = _p("border")
    surface_color = _p("surface")
    text_color = _p("text")
    text_muted = _p("text_muted")
    primary_color = _p("primary")
    primary_hover = _p("primary_hover")
    primary_fg = _p("surface")

    frame.setStyleSheet(
        f"QFrame#StaffEmptyCard{{"
        f"background:{surface_color};"
        f"border:2px dashed {border_color};"
        f"border-radius:16px;"
        f"}}"
    )
    frame.setMinimumSize(280, 200)

    layout = QVBoxLayout(frame)
    layout.setAlignment(_Qt.AlignmentFlag.AlignCenter)
    layout.setContentsMargins(32, 32, 32, 32)
    layout.setSpacing(8)

    # ── 图标：变体决定用矢量自绘还是表情符号标签 ──
    if variant == "generic":
        icon_widget = QLabel(icon_text)
        icon_widget.setObjectName("StaffEmptyIcon")
        icon_widget.setAlignment(_Qt.AlignmentFlag.AlignCenter)
        icon_widget.setStyleSheet("font-size:48px; background:transparent;")
    else:
        icon_widget = _EmptyStateIcon(variant)
    layout.addWidget(icon_widget)

    # ── 标题 ──
    if title:
        title_label = QLabel(title)
        title_label.setObjectName("StaffEmptyTitle")
        title_label.setAlignment(_Qt.AlignmentFlag.AlignCenter)
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

    # ── 引导文案：未传 description 但有 action 时自动生成 ──
    effective_desc = description
    if not effective_desc and title and action_text:
        effective_desc = f"还没创建{title}，点击下方按钮开始吧"
    if effective_desc:
        desc_label = QLabel(effective_desc)
        desc_label.setObjectName("StaffEmptyHint")
        desc_label.setAlignment(_Qt.AlignmentFlag.AlignCenter)
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

    if action_text and action_callback:
        btn = QPushButton(action_text)
        btn.setObjectName("FdCommitBtn")
        btn.setCursor(_Qt.CursorShape.PointingHandCursor)
        btn.setMinimumWidth(100)
        btn.setMaximumWidth(240)
        btn.setStyleSheet(
            f"QPushButton#FdCommitBtn{{"
            f"background:{primary_color};color:{primary_fg};border:none;"
            f"border-radius:8px;min-height:36px;padding:0 24px;"
            f"font-weight:600;"
            f"}}"
            f"QPushButton#FdCommitBtn:hover{{background:{primary_hover};}}"
        )
        btn.clicked.connect(action_callback)
        btn_layout = QHBoxLayout()
        btn_layout.setAlignment(_Qt.AlignmentFlag.AlignCenter)
        btn_layout.addWidget(btn)
        layout.addLayout(btn_layout)

    return frame


def build_loading_indicator(message: str = "加载中...") -> QWidget:
    """返回带旋转动画的加载状态控件。

    用法:
        loader = build_loading_indicator("正在查询房间数据...")
        layout.addWidget(loader)
    """
    from PySide6.QtCore import Qt as _Qt

    container = QWidget()
    container.setMinimumSize(200, 120)

    from design_tokens import _p
    text_muted = _p("text_muted")
    primary_color = _p("primary")

    layout = QVBoxLayout(container)
    layout.setAlignment(_Qt.AlignmentFlag.AlignCenter)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)

    # 旋转指示器：使用标签加动画文件或定时器旋转文字
    spinner = QLabel("◌")
    spinner.setAlignment(_Qt.AlignmentFlag.AlignCenter)
    spinner.setStyleSheet(
        f"font-size:32px; color:{primary_color}; background:transparent;"
    )
    layout.addWidget(spinner)

    # 旋转动画
    from PySide6.QtCore import QTimer, QPropertyAnimation, QEasingCurve

    def _rotate():
        chars = ["◌", "◍", "●", "◍"]
        if not hasattr(spinner, "_rot_idx"):
            spinner._rot_idx = 0
        spinner._rot_idx = (spinner._rot_idx + 1) % len(chars)
        spinner.setText(chars[spinner._rot_idx])

    timer = QTimer(spinner)
    timer.timeout.connect(_rotate)
    timer.start(200)
    # 绑定生命周期：container 析构时自动停
    spinner._spinner_timer = timer

    msg_label = QLabel(message)
    msg_label.setObjectName("Body")
    msg_label.setAlignment(_Qt.AlignmentFlag.AlignCenter)
    msg_label.setStyleSheet(
        f"color:{text_muted};background:transparent;"
    )
    layout.addWidget(msg_label)

    return container


def build_error_retry(message: str, retry_callback) -> QWidget:
    """返回错误状态控件，带红色警告图标 + 重试按钮。

    用法:
        error_widget = build_error_retry("网络连接失败", retry_callback=on_retry)
        layout.addWidget(error_widget)
    """
    from PySide6.QtCore import Qt as _Qt

    container = QWidget()
    container.setMinimumSize(280, 160)

    from design_tokens import _p
    danger_color = _p("danger")
    text_color = _p("text")
    text_muted = _p("text_muted")
    surface_color = _p("surface")
    border_color = _p("border")

    layout = QVBoxLayout(container)
    layout.setAlignment(_Qt.AlignmentFlag.AlignCenter)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(10)

    # 警告图标
    icon_label = QLabel("⚠")
    icon_label.setAlignment(_Qt.AlignmentFlag.AlignCenter)
    icon_label.setStyleSheet(
        f"font-size:40px; color:{danger_color}; background:transparent;"
    )
    layout.addWidget(icon_label)

    # 错误消息
    msg_label = QLabel(message)
    msg_label.setObjectName("Body")
    msg_label.setAlignment(_Qt.AlignmentFlag.AlignCenter)
    msg_label.setStyleSheet(
        f"color:{text_muted};background:transparent;"
    )
    layout.addWidget(msg_label)

    # 重试按钮
    btn = QPushButton("重试")
    btn.setObjectName("FdGhostBtn")
    btn.setCursor(_Qt.CursorShape.PointingHandCursor)
    btn.setMinimumWidth(80)
    btn.setMaximumWidth(180)
    btn.setStyleSheet(
        f"QPushButton{{"
        f"background:transparent;color:{danger_color};"
        f"border:2px solid {danger_color};border-radius:8px;"
        f"min-height:34px;font-size:14px;font-weight:600;"
        f"}}"
        f"QPushButton:hover{{"
        f"background:{danger_color};color:white;"
        f"}}"
    )
    btn.clicked.connect(retry_callback)
    btn_layout = QHBoxLayout()
    btn_layout.setAlignment(_Qt.AlignmentFlag.AlignCenter)
    btn_layout.addWidget(btn)
    layout.addLayout(btn_layout)

    return container


class SkeletonCard(QWidget):
    """占位骨架卡片：灰色渐变闪烁动画，数据加载完成后隐藏。"""

    def __init__(self, width: int = 200, height: int = 144, parent=None):
        super().__init__(parent)
        self.setObjectName("SkeletonCard")
        self.setFixedSize(width, height)
        self.setAttribute(Qt.WA_StyledBackground, True)
        from design_tokens import _p
        self._base_color = _p("bg")
        self._shimmer_color = QColor(255, 255, 255, 60)

        # 使用 objectName 选择器确保 CSS 生效
        self.setStyleSheet(
            f"QWidget#SkeletonCard {{"
            f" background:{self._base_color};"
            f" border-radius:10px;"
            f"}}"
        )

        # 流光扫描光效动画
        self._shimmer_offset = 0.0
        self._anim = QPropertyAnimation(self, b"shimmer_offset", self)
        self._anim.setDuration(1200)
        self._anim.setLoopCount(-1)
        self._anim.setStartValue(-1.0)
        self._anim.setEndValue(2.0)
        self._anim.setEasingCurve(QEasingCurve.Linear)
        self._anim.start()

    def get_shimmer_offset(self) -> float:
        return self._shimmer_offset

    def set_shimmer_offset(self, val: float):
        self._shimmer_offset = val
        self.update()

    shimmer_offset = Property(float, get_shimmer_offset, set_shimmer_offset)

    def hideEvent(self, event):
        """隐藏时停止动画，避免 deleteLater 后动画残留。"""
        if self._anim and self._anim.state() == QPropertyAnimation.State.Running:
            self._anim.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 圆角裁剪
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 10, 10)
        p.setClipPath(path)

        # 基础填充
        p.fillRect(self.rect(), QColor(self._base_color))

        # 流光扫描光
        w = self.width()
        gradient = QLinearGradient(
            self._shimmer_offset * w, 0,
            (self._shimmer_offset + 0.5) * w, 0
        )
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.5, self._shimmer_color)
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), gradient)
        p.end()


def skeleton_container(content: QWidget, width: int = 200, height: int = 144):
    """骨架屏包裹器：数据加载中显示骨架占位，完成后切到真实内容。

    用法:
        wrapper = skeleton_container(real_widget, width=200, height=144)
        layout.addWidget(wrapper)
        # 数据加载后:
        wrapper.setCurrentIndex(1)
    """
    from PySide6.QtWidgets import QStackedWidget
    stack = QStackedWidget()
    sk = SkeletonCard(width, height)
    stack.addWidget(sk)    # index 0 = skeleton
    stack.addWidget(content)  # index 1 = real content
    stack.setCurrentIndex(0)
    # 给 content 挂个引用方便切换
    content._skeleton_stack = stack
    content._show_content = lambda: stack.setCurrentIndex(1)
    return stack


# ═══════════════════════════════════════════════════════════════════════════
# sub-c UI/UX 打磨（2026-06-22）：列表骨架屏 + Toast 4 态便捷入口
# ═══════════════════════════════════════════════════════════════════════════

def show_skeleton(parent: QWidget, rows: int = 3, *, row_height: int = 56,
                  row_spacing: int = 10) -> QWidget:
    """在 parent 内插入一个垂直堆叠的骨架屏容器，加载完成后调 .deleteLater() 即可消失。

    典型用法（在 tab 切换、列表查询前）::

        sk = show_skeleton(self.list_holder, rows=4)
        # 异步加载 → 数据就绪后：
        sk.deleteLater()

    设计要点
    --------
    - 每行用 SkeletonCard 自带 shimmer 动画，避免裸 QLabel 灰块
    - 容器无固定高度，自适应 parent；行高 row_height 默认 56（接近表格行高）
    - 间距 row_spacing 默认 10，跟全局 gap-2.5 接近
    """
    if parent is None:
        # 无父容器时无法挂载，返回一个空 widget 避免调用方 NPE
        return QWidget()
    container = QWidget(parent)
    container.setObjectName("SkeletonListContainer")
    lay = QVBoxLayout(container)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(row_spacing)
    # 行高可小于 144（SkeletonCard 默认值），所以走自定义尺寸
    for _ in range(max(1, int(rows))):
        sk = SkeletonCard(width=max(parent.width() - 24, 200),
                          height=row_height,
                          parent=container)
        lay.addWidget(sk)
    lay.addStretch(1)
    parent_layout = parent.layout()
    if parent_layout is not None:
        parent_layout.addWidget(container)
    return container


def show_toast(message: str, *, level: str = "info", duration: int = 0) -> bool:
    """弹一条非阻塞 toast 通知（4 态：info/success/warning/error）。

    优先复用 ui.components.toast.ToastManager 实例；不存在时回退到主窗口。

    参数
    ----
    message : str
        文本内容
    level : str
        info | success | warning | error（大小写不敏感，未知值降级为 info）
    duration : int
        停留毫秒；<=0 时由 ToastWidget 按级别默认值决定

    返回
    ----
    bool : True 表示成功投递；False 表示当前没有可用 toast 容器（调用方可改用 dialog 兜底）
    """
    try:
        from main_window_impl import MainWindow
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return False
        # 遍历顶层窗口，找到 MainWindow 实例（登录前可能未创建）
        for w in app.topLevelWidgets():
            if isinstance(w, MainWindow) and getattr(w, "toast", None) is not None:
                w.toast.show_toast(message, duration, level=level)
                return True
    except (RuntimeError, ImportError):
        # RuntimeError: app/topLevelWidgets 已销毁；ImportError: main_window_impl 加载失败
        pass
    # 回退：ui.components.toast
    try:
        from ui.components.toast import ToastManager, ToastType
        _lvl = (level or "info").strip().lower()
        ttype = {
            "success": ToastType.SUCCESS,
            "warning": ToastType.WARNING,
            "error": ToastType.ERROR,
        }.get(_lvl, ToastType.INFO)
        ToastManager.instance().show(message, ttype, duration=duration or 3000)
        return True
    except (RuntimeError, ImportError, AttributeError):
        return False


def show_info_toast(message: str, duration: int = 3000) -> bool:
    """快捷入口：信息级 toast。"""
    return show_toast(message, level="info", duration=duration)


def show_success_toast(message: str, duration: int = 2400) -> bool:
    """快捷入口：成功级 toast（默认 2.4s，更短让用户感觉利索）。"""
    return show_toast(message, level="success", duration=duration)


def show_warning_toast(message: str, duration: int = 3600) -> bool:
    """快捷入口：警告级 toast（默认 3.6s，给用户更多阅读时间）。"""
    return show_toast(message, level="warning", duration=duration)


def show_error_toast(message: str, duration: int = 4500) -> bool:
    """快捷入口：错误级 toast（默认 4.5s，最长停留）。"""
    return show_toast(message, level="error", duration=duration)



def apply_app_light_chrome(app: QApplication | None = None, theme_name: str | None = None) -> None:
    """应用级：系统标题栏浅色 + 全局调色板跟随主题，根治 Fusion 暗色渲染露白/露黑。

    Fusion 样式在 Windows 暗色模式下将 QComboBox 弹出层 / QTableWidget 表头 /
    QGroupBox / QDoubleSpinBox 等控件渲染为黑底。

    当 theme_name='ink' 时使用暗色调色板，防止浅色 fallback 在黑主题下露白。"""
    if app is None:
        app = QApplication.instance()
    if app is None:
        return
    is_dark = (theme_name or getattr(app, "_solid_theme_name", "mist")) == "ink"
    try:
        pal = app.palette()
        if is_dark:
            pal.setColor(QPalette.ColorRole.Base, QColor("#1F2937"))
            pal.setColor(QPalette.ColorRole.Window, QColor("#0F1419"))
            pal.setColor(QPalette.ColorRole.Text, QColor("#EAEDEF"))
            pal.setColor(QPalette.ColorRole.Button, QColor("#2D3748"))
            pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#2D3748"))
            pal.setColor(QPalette.ColorRole.Highlight, QColor("#7BA7C9"))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
            pal.setColor(QPalette.ColorRole.WindowText, QColor("#EAEDEF"))
            pal.setColor(QPalette.ColorRole.ButtonText, QColor("#EAEDEF"))
            pal.setColor(QPalette.ColorRole.BrightText, QColor("#C47070"))
        else:
            pal.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
            pal.setColor(QPalette.ColorRole.Window, QColor("#F8F6F3"))
            pal.setColor(QPalette.ColorRole.Text, QColor("#2A2A2E"))
            pal.setColor(QPalette.ColorRole.Button, QColor("#F2F0EE"))
            pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#F2F0EE"))
            pal.setColor(QPalette.ColorRole.Highlight, QColor("#7B8C9E"))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
            pal.setColor(QPalette.ColorRole.WindowText, QColor("#2A2A2E"))
            pal.setColor(QPalette.ColorRole.ButtonText, QColor("#2A2A2E"))
            pal.setColor(QPalette.ColorRole.BrightText, QColor("#B5586A"))
        app.setPalette(pal)
        app._solid_theme_name = theme_name or "mist"
    except (RuntimeError, OSError, ValueError):
        pass


def apply_windows_light_title_bar(window: QWidget | None) -> None:
    """Windows：强制浅色原生标题栏（最大化/最小化那条）。"""
    import sys

    if window is None or sys.platform != "win32":
        return
    apply_app_light_chrome()
    try:
        import ctypes

        hwnd = int(window.winId())
        if hwnd == 0:
            return
        off = ctypes.c_int(0)
        for attr in (20, 19):  # Win11 / Win10 兼容
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                attr,
                ctypes.byref(off),
                ctypes.sizeof(off),
            )
        # 触发非客户区重绘，避免 DWM 切换后标题栏仍滞留深色
        user32 = ctypes.windll.user32
        WM_NCACTIVATE = 0x0086
        user32.SendMessageW(hwnd, WM_NCACTIVATE, 0, 0)
        user32.SendMessageW(hwnd, WM_NCACTIVATE, 1, 0)
    except (OSError, AttributeError, RuntimeError):
        # OSError: ctypes 调用失败（DWM 未就绪 / Win 缺组件）
        # AttributeError: 非预期的 windll 字段
        # RuntimeError: hwnd 已失效
        pass


def fix_fusion_combo_popup(combo: QComboBox) -> None:
    """修复 Fusion 样式下 QComboBox 弹出层不认 QSS background 的 Qt 已知问题。
    通过 monkey-patch showPopup 在弹出前强制给 view 注入 inline stylesheet。"""
    _orig_show_popup = combo.showPopup

    def _patched_show_popup():
        view = combo.view()
        if view is not None:
            from design_tokens import _p
            bg = _p("bg")
            fg = _p("text")
            sel_bg = _p("primary_10pct")
            view.setStyleSheet(
                f"background-color: {bg}; selection-background-color: {sel_bg}; color: {fg};"
            )
        _orig_show_popup()

    combo.showPopup = _patched_show_popup
