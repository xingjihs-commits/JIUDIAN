"""全局命令面板 — 跳页、搜房、快捷命令。"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import db
from i18n import i18n


CommandItem = Tuple[str, str, str, Optional[Callable[[], None]]]


class CommandPalette(QDialog):
    """模态命令面板；由 MainWindow 注册导航与动作。"""

    navigated = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("CommandPalette")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMaximumHeight(420)
        self._commands: List[CommandItem] = []
        self._room_hits: List[str] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.inp = QLineEdit()
        self.inp.setObjectName("CommandPaletteInput")
        self.inp.setPlaceholderText(i18n.t("cmdk_placeholder"))
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(150)
        self._debounce_timer.timeout.connect(self._do_search)
        self.inp.textChanged.connect(self._on_text_changed)
        lay.addWidget(self.inp)

        self.list = QListWidget()
        self.list.setObjectName("CommandPaletteList")
        self.list.itemActivated.connect(self._on_activate)
        lay.addWidget(self.list, stretch=1)

        hint = QLabel(i18n.t("cmdk_hint"))
        hint.setObjectName("FdMutedLabel")
        hint.setContentsMargins(12, 4, 12, 8)
        lay.addWidget(hint)

    def set_commands(self, commands: List[CommandItem]) -> None:
        self._commands = list(commands)
        self._rebuild_list(self.inp.text())

    def open_palette(self) -> None:
        self.inp.clear()
        self._rebuild_list("")
        self.inp.setFocus()
        self.adjustSize()
        if self.parentWidget():
            pg = self.parentWidget().geometry()
            # 响应式宽度：小屏 380px，中屏 480px，大屏 520px
            w = min(520, max(380, pg.width() // 3))
            self.setMinimumWidth(w)
            self.move(
                pg.x() + max(0, (pg.width() - self.width()) // 2),
                pg.y() + 80,
            )
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_text_changed(self, text: str) -> None:
        """每次输入触发 150ms 防抖，避免同步搜库阻塞 UI。"""
        self._debounce_timer.stop()
        self._debounce_timer.start()

    def _do_search(self) -> None:
        """防抖倒计时结束，执行实际搜索。"""
        self._rebuild_list(self.inp.text())

    def _rebuild_list(self, text: str) -> None:
        q = (text or "").strip()
        self.list.clear()
        self._room_hits = []

        if q.startswith(">"):
            cmd_q = q[1:].strip().lower()
            for _id, title, subtitle, cb in self._commands:
                if cmd_q and cmd_q not in title.lower() and cmd_q not in subtitle.lower():
                    continue
                it = QListWidgetItem(f"{title}  —  {subtitle}")
                it.setData(Qt.ItemDataRole.UserRole, ("cmd", _id, cb))
                self.list.addItem(it)
            if self.list.count():
                self.list.setCurrentRow(0)
            return

        if len(q) >= 2:
            # 搜索房号
            try:
                rows = db.execute(
                    "SELECT room_id FROM rooms WHERE room_id LIKE ? ORDER BY room_id LIMIT 12",
                    (f"%{q}%",),
                ).fetchall()
                self._room_hits = [r[0] for r in rows]
            except Exception:
                self._room_hits = []
            for rid in self._room_hits:
                it = QListWidgetItem(f"▣  {rid}")
                it.setData(Qt.ItemDataRole.UserRole, ("room", rid, None))
                self.list.addItem(it)

            # 搜索客人名
            try:
                guest_rows = db.execute(
                    "SELECT name, room_id FROM guests WHERE name LIKE ? ORDER BY checkin_time DESC LIMIT 8",
                    (f"%{q}%",),
                ).fetchall()
                for gname, groom in guest_rows:
                    it = QListWidgetItem(f"👤  {gname}  @  {groom}")
                    it.setData(Qt.ItemDataRole.UserRole, ("room", groom, None))
                    self.list.addItem(it)
            except Exception:
                pass

        for _id, title, subtitle, cb in self._commands:
            blob = f"{title} {subtitle}".lower()
            if q and q.lower() not in blob:
                continue
            it = QListWidgetItem(f"{title}  ·  {subtitle}")
            it.setData(Qt.ItemDataRole.UserRole, ("nav", _id, cb))
            self.list.addItem(it)

        if self.list.count():
            self.list.setCurrentRow(0)

    def _on_activate(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, payload, cb = data
        if kind == "room":
            self.accept()
            self.navigated.emit(f"room:{payload}")
            return
        if kind in ("nav", "cmd") and cb:
            self.accept()
            cb()
            return
        if kind == "nav":
            self.accept()
            self.navigated.emit(str(payload))


def install_command_palette_shortcut(host: QWidget, opener: Callable[[], None]) -> QShortcut:
    sc = QShortcut(QKeySequence("Ctrl+K"), host)
    sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
    sc.activated.connect(opener)
    sc_mac = QShortcut(QKeySequence("Meta+K"), host)
    sc_mac.setContext(Qt.ShortcutContext.ApplicationShortcut)
    sc_mac.activated.connect(opener)
    return sc
