"""
migration_guide_panel.py — 迁移现场指引通用黄框面板（所有迁移窗共用）
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGroupBox,
)

from ui_helpers import show_warning, show_info, show_error, ask_confirm

from legacy_migration_guide import (
    GuideAction,
    SniffGuideSession,
    get_cardlock_step_session,
)


class MigrationGuidePanel(QGroupBox):
    """黄框 + 进度 + 主次按钮；绑定任意指引会话。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        title: str = "现场操作指引（请按顺序点按钮）",
        on_action: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(title, parent)
        self.setObjectName("MigrationGuidePanel")
        self._on_action = on_action
        self._session: object = SniffGuideSession()
        self._sniff_start_cb: Optional[Callable[[], None]] = None

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        self.lbl_progress = QLabel("")
        self.lbl_progress.setObjectName("MigrationGuideProgress")
        lay.addWidget(self.lbl_progress)

        self.lbl_banner = QLabel("")
        self.lbl_banner.setObjectName("MigrationGuideBanner")
        self.lbl_banner.setWordWrap(True)
        lay.addWidget(self.lbl_banner)

        row = QHBoxLayout()
        self.btn_primary = QPushButton("")
        self.btn_primary.setObjectName("SolidPrimaryBtn")
        self.btn_primary.clicked.connect(self._on_primary)
        row.addWidget(self.btn_primary, 1)

        self.btn_secondary = QPushButton("")
        self.btn_secondary.setObjectName("FdGhostBtn")
        self.btn_secondary.clicked.connect(self._on_secondary)
        self.btn_secondary.hide()
        row.addWidget(self.btn_secondary)
        lay.addLayout(row)

    def set_action_handler(self, handler: Callable[[str], None]) -> None:
        self._on_action = handler

    def set_sniff_start_callback(self, cb: Callable[[], None]) -> None:
        """嗅探窗：进入监听阶段时自动开始串口嗅探。"""
        self._sniff_start_cb = cb

    def bind_session(self, session: object) -> None:
        self._session = session
        self.refresh()

    def refresh(self) -> None:
        s = self._session
        self.lbl_progress.setText(s.progress_label())
        self.lbl_banner.setText(s.banner_text())

        primary = s.primary_button_label()
        if primary:
            self.btn_primary.setText(primary)
            enabled = True
            if hasattr(s, "listen_button_enabled"):
                enabled = s.listen_button_enabled()
            self.btn_primary.setEnabled(enabled)
            self.btn_primary.show()
        else:
            self.btn_primary.hide()

        sec = s.secondary_button_label() if hasattr(s, "secondary_button_label") else None
        if sec:
            self.btn_secondary.setText(sec)
            self.btn_secondary.show()
        else:
            self.btn_secondary.hide()

    def _on_primary(self) -> None:
        s = self._session
        if isinstance(s, SniffGuideSession):
            msg, start_sniff = s.advance_primary()
            if msg:
                show_warning(self.window(), "请稍等", msg)
                return
            if start_sniff and self._sniff_start_cb:
                self._sniff_start_cb()
            self.refresh()
            return

        msg, action = s.advance_primary()
        if msg:
            show_warning(self.window(), "请稍等", msg)
            return
        self.refresh()
        if action and action != GuideAction.NONE and self._on_action:
            self._on_action(action)

    def _on_secondary(self) -> None:
        s = self._session
        sec = s.secondary_button_label() if hasattr(s, "secondary_button_label") else None
        if isinstance(s, SniffGuideSession):
            if sec and "跳过" in sec:
                s.skip_optional_round()
            elif s.retry_listen():
                pass
            self.refresh()
            return
        if sec and "跳过" in sec and hasattr(s, "skip_optional"):
            s.skip_optional()
            self.refresh()
            return
        if self._on_action:
            self._on_action(GuideAction.RETRY)
