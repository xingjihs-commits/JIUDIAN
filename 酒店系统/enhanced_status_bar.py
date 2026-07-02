# [UI-REDESIGN] 2026-06-15 v4 改动: 使用 v4 主题色板 + 金线品牌元素 + 紧凑度量
"""enhanced_status_bar.py — 增强状态栏 (28px)

设计原则：
  - 将原侧栏底部角色信息条(RoleStrip)迁移至状态栏左侧
  - 使用 _p() 动态色值，跟随主题切换
  - 金线品牌元素贯穿（按钮悬浮金边）
  - 紧凑度量，PC 端不浪费像素
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QWidget,
    QSizePolicy,
)
from PySide6.QtCore import Qt

from i18n import i18n
from design_tokens import SPACE_SM, SPACE_XS, SPACE_LG, BTN_HEIGHT_SM

ENHANCED_STATUSBAR_HEIGHT = 32


class EnhancedStatusBar(QFrame):
    """增强状态栏：左侧角色信息 + 中间诊断 + 右侧版本/时钟。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("EnhancedStatusBar")
        self.setMinimumHeight(ENHANCED_STATUSBAR_HEIGHT)
        self.setMaximumHeight(int(ENHANCED_STATUSBAR_HEIGHT * 1.3))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(SPACE_LG, 0, SPACE_LG, 0)
        lay.setSpacing(SPACE_SM)

        # ── 左段：角色信息徽章（从侧栏底部迁移）──
        self.lbl_status_role = QLabel("")
        self.lbl_status_role.setObjectName("StatusTag")
        lay.addWidget(self.lbl_status_role)

        # ── 诊断指示器组 ──
        self._status_diag_host = QWidget()
        diag_lay = QHBoxLayout(self._status_diag_host)
        diag_lay.setContentsMargins(0, 0, 0, 0)
        diag_lay.setSpacing(SPACE_XS)
        self.lbl_status_db = QLabel(f"● {i18n.t('status_db_ok')}")
        self.lbl_status_db.setObjectName("StatusTag")
        self.lbl_status_lock = QLabel(f"● {i18n.t('status_lock_ok')}")
        self.lbl_status_lock.setObjectName("StatusTag")
        self.lbl_status_hb = QLabel(f"● {i18n.t('status_heartbeat')}")
        self.lbl_status_hb.setObjectName("StatusTag")
        for w in (self.lbl_status_db, self.lbl_status_lock, self.lbl_status_hb):
            diag_lay.addWidget(w)
        self._status_diag_host.hide()
        lay.addWidget(self._status_diag_host)

        # ── 购物车待处理 ──
        self.btn_cart = QPushButton(f"{i18n.t('cart_pending')}: 0")
        self.btn_cart.setObjectName("CartBtn")
        self.btn_cart.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cart.setToolTip(i18n.t("cart_goto_shop_tip"))
        # clicked 信号由 MainWindow 连接
        lay.addWidget(self.btn_cart)

        lay.addStretch(1)

        # ── 右段：版本 + 时钟 ──
        self.lbl_status_ver = QLabel("v3")
        self.lbl_status_ver.setObjectName("StatusTag")
        lay.addWidget(self.lbl_status_ver)

        self.lbl_status_clock = QLabel("")
        self.lbl_status_clock.setObjectName("StatusTag")
        lay.addWidget(self.lbl_status_clock)

        # ── 兼容旧代码引用 ──
        self.lbl_role_badge = self.lbl_status_role

    def refresh_theme(self) -> None:
        """主题切换后刷新（样式由 base.qss 统一管理，此处仅 re-polish）。"""
        self.style().unpolish(self)
        self.style().polish(self)
        for w in (
            self.lbl_status_role, self.lbl_status_db, self.lbl_status_lock,
            self.lbl_status_hb, self.lbl_status_ver, self.lbl_status_clock, self.btn_cart,
        ):
            w.style().unpolish(w)
            w.style().polish(w)

    def set_role_info(self, display_name: str, role_name: str) -> None:
        """设置角色信息徽章。"""
        self.lbl_status_role.setText(f"\U0001f464 {display_name} \u00b7 {role_name}")
        self.lbl_status_role.setProperty("role", role_name)
        self.lbl_status_role.style().unpolish(self.lbl_status_role)
        self.lbl_status_role.style().polish(self.lbl_status_role)

    def clear_role_info(self) -> None:
        """清除角色信息。"""
        self.lbl_status_role.setText("")
