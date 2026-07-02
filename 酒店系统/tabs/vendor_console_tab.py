"""
vendor_console_tab.py — 厂家控制台
===================================
整合 debug_panel + vendor_lockdown + lock_diag_page + 远程更新
内嵌在工作区 Tab 页面，厂家功能一键直达。
"""
from __future__ import annotations

import hashlib
import datetime
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QTabWidget, QGroupBox, QFormLayout, QCheckBox,
    QComboBox, QSlider, QPlainTextEdit, QSpinBox, QFrame,
    QScrollArea, QFileDialog, QGridLayout,
    QListWidget,
)
from database import db
from i18n import i18n
from ui_helpers import show_info, show_warning, ask_confirm
from event_bus import bus
from design_tokens import _p
from frontdesk_ui import FD_MARGIN, FD_SPACE_MD
from ui_surface import fd_apply_vendor_stat_row, fd_refresh_surfaces, fd_apply_scroll_area, fd_apply_page_tab_root, fd_apply_panel_container

logger = logging.getLogger(__name__)


def _group(title=""):
    g = QGroupBox(title)
    g.setObjectName("VendorConsoleGroup")
    from ui_surface import fd_apply_panel_groupbox
    fd_apply_panel_groupbox(g, fallback_name="VendorConsoleGroup")
    return g


def _scroll_page(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setObjectName("VendorConsoleScroll")
    scroll.setWidget(content)
    fd_apply_scroll_area(scroll, bg_key="surface")
    return scroll


def _card(label: str, value: str, color=None):
    c = color or _p("primary")
    f = QFrame()
    f.setObjectName("VendorStatRow")
    l = QHBoxLayout(f)
    l.setContentsMargins(4, 6, 4, 6)
    k = QLabel(label)
    k.setObjectName("VendorStatLabel")
    v = QLabel(value)
    v.setObjectName("VendorStatValue")
    v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    l.addWidget(k)
    l.addStretch()
    l.addWidget(v)
    fd_apply_vendor_stat_row(f, value_color=c)
    return f


class VendorConsoleTab(QWidget):

    def __init__(self):
        super().__init__()
        self.setObjectName("VendorConsolePage")

        l = QVBoxLayout(self)
        l.setContentsMargins(FD_MARGIN, FD_MARGIN, FD_MARGIN, FD_MARGIN)
        l.setSpacing(FD_SPACE_MD)

        title = QLabel(i18n.t("nav_vendor_console"))
        title.setObjectName("H2Title")
        l.addWidget(title)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("VendorConsoleSubTabs")
        fd_apply_panel_container(self._tabs, fallback_name="VendorConsoleSubTabs")
        l.addWidget(self._tabs, 1)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(100)
        self._log.setObjectName("VendorConsoleLog")
        self._log.setStyleSheet("font-family:Consolas;")
        self._log.appendPlainText(i18n.t("vendor_started"))
        l.addWidget(self._log)

        # sub-h：首屏加"厂家控制中心"5 区块总览 Tab，提供统一入口
        # 下方原有 8 个子 Tab（诊断/授权/锁死/更新/备份/DB/云端/门锁）保留为详情页
        self._build_overview_tab()
        self._build_diag_tab()
        self._build_license_tab()
        self._build_lockdown_tab()
        self._build_update_tab()
        self._build_backup_tab()
        self._build_db_tab()
        self._build_cloud_tab()
        self._build_lock_tab()
        fd_apply_page_tab_root(self)
        fd_refresh_surfaces(self)
        bus.theme_changed.connect(lambda _: fd_refresh_surfaces(self))

    def _log_line(self, msg):
        self._log.appendPlainText(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")

    # ══════════════════════════════════════════════
    #  0. 厂家控制中心 — 5 区块总览（sub-h 新增统一入口）
    # ══════════════════════════════════════════════
    def _build_overview_tab(self):
        """5 大区块首屏：许可证/云端/握手包/远程诊断/紧急操作。

        sub-h 整合：把原本分散在 8 个子 Tab 的关键状态汇成一张总览，
        厂家工程师 30 秒看完系统健康度，无需翻页。详情仍走下方子 Tab。
        """
        inner = QWidget()
        r = QVBoxLayout(inner)
        r.setContentsMargins(20, 16, 20, 16)
        r.setSpacing(14)

        # ── 区块1：许可证状态 ──
        g_lic = _group("① 许可证状态")
        f_lic = QFormLayout(g_lic)
        f_lic.setSpacing(8)
        try:
            from license_manager import LicenseManager
            lm = LicenseManager(db)
            machine_id = lm.get_machine_code()
            hotel_id = db.get_config("hotel_id") or "—"
            exp = db.get_config("kill_switch_date") or "—"
        except Exception:
            machine_id = db.get_config("machine_id") or "—"
            hotel_id = db.get_config("hotel_id") or "—"
            exp = db.get_config("kill_switch_date") or "—"
        # 锁机级别 + 离线天数
        try:
            from vendor_lockdown import current_lock_level, offline_days, LOCK_LEVELS
            lv = current_lock_level() or "NORMAL"
            od = offline_days()
            od_str = f"{od:.1f} 天" if od is not None else "从未联网"
        except Exception:
            lv = "—"
            od_str = "—"
        f_lic.addRow(_card("酒店 ID", hotel_id))
        f_lic.addRow(_card("机器码", machine_id))
        f_lic.addRow(_card("授权到期", exp))
        f_lic.addRow(_card("锁机级别", lv,
                            color=_p("danger") if lv in ("LOCK_ALL", "LOCK_REPORTS")
                            else _p("warn") if lv in ("LOCK_GUEST_BOT", "WARNING_BANNER")
                            else _p("amount_positive")))
        f_lic.addRow(_card("离线天数", od_str,
                            color=_p("danger") if (od and od >= 14)
                            else _p("warn") if (od and od >= 7)
                            else _p("amount_positive")))
        btn_lic = QPushButton("→ 跳转授权管理")
        btn_lic.setObjectName("FdGhostBtn")
        btn_lic.clicked.connect(lambda: self._tabs.setCurrentIndex(2))  # 授权 Tab 在第 2 位
        f_lic.addRow(btn_lic)
        r.addWidget(g_lic)

        # ── 区块2：云端连接 ──
        g_cloud = _group("② 云端连接")
        f_cloud = QFormLayout(g_cloud)
        f_cloud.setSpacing(8)
        worker_url = db.get_config("cloud_worker_url") or "未配置"
        last_seen = db.get_config("last_cloud_seen_at") or "从未"
        try:
            from cloud_security import SIGNATURE_VERSION
            sig_ver = SIGNATURE_VERSION
        except Exception:
            sig_ver = "—"
        cloud_on = (db.get_config("cloud_enabled") or "0") == "1"
        f_cloud.addRow(_card("Worker URL", worker_url,
                              color=_p("text_muted") if worker_url == "未配置" else _p("primary")))
        f_cloud.addRow(_card("云端开关", "已开启" if cloud_on else "已关闭",
                              color=_p("amount_positive") if cloud_on else _p("danger")))
        f_cloud.addRow(_card("最近心跳", last_seen))
        f_cloud.addRow(_card("签名版本", sig_ver))
        # 心跳连接状态（基于离线天数推断）
        try:
            from vendor_lockdown import offline_days
            od2 = offline_days()
            if od2 is None:
                status_str, status_color = "未联网过", _p("text_muted")
            elif od2 < 1:
                status_str, status_color = "在线 ✅", _p("amount_positive")
            elif od2 < 3:
                status_str, status_color = f"轻度离线 ({od2:.1f}天)", _p("warn")
            else:
                status_str, status_color = f"严重离线 ({od2:.1f}天)", _p("danger")
        except Exception:
            status_str, status_color = "—", _p("text_muted")
        f_cloud.addRow(_card("连接状态", status_str, color=status_color))
        btn_cloud = QPushButton("→ 跳转云端同步")
        btn_cloud.setObjectName("FdGhostBtn")
        btn_cloud.clicked.connect(lambda: self._tabs.setCurrentIndex(7))  # 云端同步 Tab
        f_cloud.addRow(btn_cloud)
        r.addWidget(g_cloud)

        # ── 区块3：握手包管理 ──
        g_hk = _group("③ 握手包管理")
        gv_hk = QVBoxLayout(g_hk)
        gv_hk.setSpacing(8)
        # 本地已导入的 profile
        try:
            learned = db.get_config("learned_at") or "未导入"
            learned_brand = db.get_config("learned_card_types") or "—"
        except Exception:
            learned = "—"
            learned_brand = "—"
        gv_hk.addWidget(_card("本地握手包", learned))
        gv_hk.addWidget(_card("已识别卡类型", learned_brand))
        # sub-g 预留：云端拉取握手包（采集器在改）
        sub_g_placeholder = QLabel(
            "🚧 云端拉取握手包（sub-g 在做）\n"
            "  · /api/handover-list  厂家查询已上传的握手包\n"
            "  · /api/handover-pull  PMS 从云端拉取并导入\n"
            "  · /api/handover-push  采集器上传新握手包"
        )
        sub_g_placeholder.setObjectName("Small")
        sub_g_placeholder.setWordWrap(True)
        gv_hk.addWidget(sub_g_placeholder)
        h_hk = QHBoxLayout()
        btn_local = QPushButton("📦 本地导入握手包")
        btn_local.setObjectName("SolidPrimaryBtn")
        btn_local.setMinimumHeight(36)
        btn_local.clicked.connect(self._lk_import_collected)
        h_hk.addWidget(btn_local)
        btn_cloud_pull = QPushButton("☁️ 云端拉取（sub-g）")
        btn_cloud_pull.setObjectName("FdGhostBtn")
        btn_cloud_pull.setMinimumHeight(36)
        btn_cloud_pull.setEnabled(False)  # sub-g 完成后激活
        btn_cloud_pull.setToolTip("等待 sub-g 完成 cloud-worker 端点实现后激活")
        h_hk.addWidget(btn_cloud_pull)
        h_hk.addStretch()
        gv_hk.addLayout(h_hk)
        btn_jump_lock = QPushButton("→ 跳转门锁探测详情")
        btn_jump_lock.setObjectName("FdGhostBtn")
        btn_jump_lock.clicked.connect(lambda: self._tabs.setCurrentIndex(8))  # 门锁 Tab
        gv_hk.addWidget(btn_jump_lock)
        r.addWidget(g_hk)

        # ── 区块4：远程诊断 ──
        g_diag = _group("④ 远程诊断")
        gv_diag = QVBoxLayout(g_diag)
        gv_diag.setSpacing(8)
        try:
            last_diag = db.get_config("last_remote_diag_at") or "从未"
        except Exception:
            last_diag = "—"
        gv_diag.addWidget(_card("最近诊断时间", last_diag))
        diag_state = db.get_config("remote_diag_enabled") or "0"
        gv_diag.addWidget(_card("远程诊断开关",
                                  "已开启" if diag_state == "1" else "已关闭",
                                  color=_p("warn") if diag_state == "1" else _p("text_muted")))
        # 诊断快照按钮（本地触发，模拟云端指令）
        btn_diag = QPushButton("🔍 立即生成本地诊断快照")
        btn_diag.setObjectName("SolidPrimaryBtn")
        btn_diag.setMinimumHeight(36)
        btn_diag.clicked.connect(self._overview_run_diag)
        gv_diag.addWidget(btn_diag)
        self._overview_diag_out = QPlainTextEdit()
        self._overview_diag_out.setReadOnly(True)
        self._overview_diag_out.setObjectName("VendorConsoleMono")
        self._overview_diag_out.setMaximumHeight(120)
        self._overview_diag_out.setPlaceholderText("诊断快照结果将显示在这里…")
        gv_diag.addWidget(self._overview_diag_out)
        btn_jump_diag = QPushButton("→ 跳转系统诊断详情")
        btn_jump_diag.setObjectName("FdGhostBtn")
        btn_jump_diag.clicked.connect(lambda: self._tabs.setCurrentIndex(1))  # 诊断 Tab
        gv_diag.addWidget(btn_jump_diag)
        r.addWidget(g_diag)

        # ── 区块5：紧急操作 ──
        g_emerg = _group("⑤ 紧急操作")
        gv_emerg = QVBoxLayout(g_emerg)
        gv_emerg.setSpacing(8)
        # 紧急延期码输入
        gv_emerg.addWidget(QLabel("🆘 紧急延期码（72 小时解锁）"))
        h_code = QHBoxLayout()
        self._ov_emerg_input = QLineEdit()
        self._ov_emerg_input.setPlaceholderText("格式 XXXX-XXXX，厂家电话告知")
        h_code.addWidget(self._ov_emerg_input)
        btn_apply = QPushButton("应用延期码")
        btn_apply.setObjectName("SolidPrimaryBtn")
        btn_apply.clicked.connect(self._overview_apply_emergency)
        h_code.addWidget(btn_apply)
        gv_emerg.addLayout(h_code)
        # 活码重新绑定
        gv_emerg.addWidget(QLabel("🔁 活码重新绑定（推送所有房间 token 到云端）"))
        h_qr = QHBoxLayout()
        btn_rebind = QPushButton("立即同步活码")
        btn_rebind.setObjectName("SolidPrimaryBtn")
        btn_rebind.clicked.connect(self._overview_rebind_live_qr)
        h_qr.addWidget(btn_rebind)
        h_qr.addStretch()
        gv_emerg.addLayout(h_qr)
        # 锁机状态查询
        gv_emerg.addWidget(QLabel("🔒 锁机状态查询"))
        h_lock = QHBoxLayout()
        btn_lock_query = QPushButton("查询当前锁机状态")
        btn_lock_query.setObjectName("SolidPrimaryBtn")
        btn_lock_query.clicked.connect(self._overview_query_lock)
        h_lock.addWidget(btn_lock_query)
        btn_clear_lock = QPushButton("清除本地锁机标记（需厂家服务码）")
        btn_clear_lock.setObjectName("FdGhostBtn")
        btn_clear_lock.clicked.connect(self._overview_clear_lock)
        h_lock.addWidget(btn_clear_lock)
        h_lock.addStretch()
        gv_emerg.addLayout(h_lock)
        r.addWidget(g_emerg)

        r.addStretch()
        self._tabs.addTab(_scroll_page(inner), "厂家控制中心")

    def _overview_run_diag(self):
        """在首屏直接生成本地诊断快照（模拟云端 DIAG_SNAPSHOT 指令）。"""
        try:
            from remote_diag import get_full_diagnosis
            snap = get_full_diagnosis()
            import json as _json
            db.set_config("last_remote_diag_at", datetime.datetime.now().isoformat(timespec="seconds"))
            self._overview_diag_out.setPlainText(_json.dumps(snap, ensure_ascii=False, indent=2)[:4000])
            self._log_line("已生成诊断快照（手动触发）")
        except Exception as e:
            self._overview_diag_out.setPlainText(f"诊断失败: {e}")
            self._log_line(f"诊断快照失败: {e}")

    def _overview_apply_emergency(self):
        """应用紧急延期码（与下方"锁死控制"Tab 共用 vendor_lockdown 逻辑）。"""
        code = self._ov_emerg_input.text().strip()
        if not code:
            show_warning(self, "紧急延期", "请输入 8 位延期码（XXXX-XXXX）")
            return
        try:
            from vendor_lockdown import apply_emergency_code
            from license_manager import LicenseManager
            hotel_id = LicenseManager.get_hotel_id()
            ok = apply_emergency_code(hotel_id, code)
            if ok:
                show_info(self, "紧急延期", "✅ 延期成功，72 小时内不会锁机。\n请尽快恢复网络连接。")
                self._log_line("紧急延期码已应用")
                self._ov_emerg_input.clear()
            else:
                show_warning(self, "紧急延期", "❌ 延期码无效（hotel_id 不匹配 / 码错 / 已用过）")
                self._log_line("紧急延期码应用失败")
        except Exception as e:
            show_warning(self, "紧急延期", f"异常: {e}")

    def _overview_rebind_live_qr(self):
        """把本机所有 room_qr_tokens 推送到云端重新绑定活码。"""
        try:
            from live_qr_client import sync_all_rooms_from_db
            n = sync_all_rooms_from_db()
            if n > 0:
                show_info(self, "活码同步", f"✅ 已同步 {n} 间房到云端")
                self._log_line(f"活码重新绑定: {n} 间")
            else:
                show_warning(self, "活码同步", "⚠️ 未同步任何房间（可能无 token 或云端未配置）")
                self._log_line("活码同步: 0 间")
        except Exception as e:
            show_warning(self, "活码同步", f"异常: {e}")

    def _overview_query_lock(self):
        """查询并展示当前锁机状态详情。"""
        try:
            from vendor_lockdown import current_lock_status, get_lockdown_phase
            st = current_lock_status()
            ph = get_lockdown_phase()
            msg = (
                f"锁机级别: {st.get('level') or 'NORMAL'}\n"
                f"已锁: {'是' if st.get('locked') else '否'}\n"
                f"阶段: {ph.get('stage', 'normal')}\n"
                f"离线天数: {ph.get('offline_days', 0)}\n"
                f"将在第 {ph.get('will_lock_at_day', 7)} 天锁机\n"
                f"紧急延期生效中: {'是' if ph.get('emergency_active') else '否'}\n"
            )
            if ph.get("emergency_expires_at"):
                msg += f"延期失效时间: {ph['emergency_expires_at']}\n"
            if ph.get("message"):
                msg += f"\n提示: {ph['message']}"
            show_info(self, "锁机状态", msg)
            self._log_line("已查询锁机状态")
        except Exception as e:
            show_warning(self, "锁机状态", f"异常: {e}")

    def _overview_clear_lock(self):
        """清除本地锁机标记 — 需要厂家服务码（vendor_password_hash）。"""
        from ui_helpers import ask_confirm
        if not ask_confirm(self, "清除锁机标记",
                           "⚠️ 此操作将清除本地锁机级别标记。\n"
                           "仅用于厂家调试，正常情况应让锁机级别自动恢复。\n\n"
                           "继续吗？"):
            return
        # 用 QInputDialog 接收厂家服务码
        from PySide6.QtWidgets import QInputDialog
        code, ok = QInputDialog.getText(self, "厂家服务码", "请输入厂家服务码:", QLineEdit.EchoMode.Password)
        if not ok or not code:
            return
        try:
            from vendor_lockdown import verify_vendor_code, sync_lock_level
            if not verify_vendor_code(code):
                show_warning(self, "清除锁机", "❌ 厂家服务码错误")
                return
            sync_lock_level("", source="overview_clear")
            show_info(self, "清除锁机", "✅ 本地锁机标记已清除")
            self._log_line("已清除本地锁机标记（厂家服务码校验通过）")
        except Exception as e:
            show_warning(self, "清除锁机", f"异常: {e}")

    # ══════════════════════════════════════════════
    #  1. 系统诊断
    # ══════════════════════════════════════════════
    def _build_diag_tab(self):
        inner = QWidget()
        r = QVBoxLayout(inner)
        r.setContentsMargins(20, 16, 20, 16)
        r.setSpacing(14)

        g = _group(i18n.t("vendor_diag_onekey_title"))
        gv = QVBoxLayout(g)
        gv.setSpacing(8)

        btn = QPushButton(i18n.t("vendor_btn_run_diag"))
        btn.setObjectName("SolidPrimaryBtn")
        btn.setMinimumHeight(36)
        btn.clicked.connect(self._run_diag)
        gv.addWidget(btn)

        self._diag_result = QPlainTextEdit()
        self._diag_result.setReadOnly(True)
        self._diag_result.setMinimumHeight(72)
        self._diag_result.setMaximumHeight(300)
        self._diag_result.setPlaceholderText(i18n.t("vendor_diag_result_ph"))
        self._diag_result.setObjectName("VendorConsoleMono")
        gv.addWidget(self._diag_result)
        r.addWidget(g)

        g2 = _group(i18n.t("vendor_diag_status_title"))
        f2 = QFormLayout(g2)
        f2.setSpacing(8)
        f2.addRow(_card(i18n.t("vendor_card_db_path"), str(getattr(db, "db_path", i18n.t("vendor_status_na")))))
        f2.addRow(_card(i18n.t("vendor_card_db_size"),
                         f"{db.get_config('db_size') or '—'} KB"))
        f2.addRow(_card(i18n.t("vendor_card_backup_status"), db.get_config("last_backup") or i18n.t("vendor_status_none")))
        r.addWidget(g2)
        r.addStretch()

        self._tabs.addTab(_scroll_page(inner), i18n.t("vendor_tab_diag"))

    def _run_diag(self):
        self._diag_result.clear()
        results = []
        # 1. DB path
        results.append((i18n.t("vendor_diag_item_db_path"), True, str(getattr(db, "db_path", i18n.t("vendor_status_na")))))
        # 2. DB file exists
        try:
            import os
            p = getattr(db, "db_path", "")
            exists = os.path.exists(p) if p else False
            results.append((i18n.t("vendor_diag_item_db_file"), exists, i18n.t("vendor_status_yes") if exists else i18n.t("vendor_status_no")))
        except Exception as e:
            results.append((i18n.t("vendor_diag_item_db_check"), False, str(e)))
        # 3. Room count
        try:
            cnt = db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
            results.append((i18n.t("vendor_diag_item_room_count"), True, str(cnt)))
        except Exception as e:
            results.append((i18n.t("vendor_diag_item_room_query"), False, str(e)))
        # 4. Ledger count
        try:
            cnt = db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
            results.append((i18n.t("vendor_diag_item_ledger_count"), True, str(cnt)))
        except Exception as e:
            results.append((i18n.t("vendor_diag_item_ledger_query"), False, str(e)))
        # 5. License
        try:
            exp = db.get_config("kill_switch_date") or i18n.t("vendor_status_not_set")
            results.append((i18n.t("vendor_diag_item_expiry"), True, exp))
        except Exception as e:
            results.append((i18n.t("vendor_diag_item_expiry_query"), False, str(e)))
        # 6. Lockdown
        try:
            from vendor_lockdown import current_lock_status
            st = current_lock_status()
            results.append((i18n.t("vendor_diag_item_lockdown"), True, st.get("level", i18n.t("vendor_lock_level_normal"))))
        except Exception as e:
            results.append((i18n.t("vendor_diag_item_lockdown_check"), False, str(e)))
        # 7. Last backup
        try:
            bk = db.get_config("last_backup") or i18n.t("vendor_status_none")
            results.append((i18n.t("vendor_diag_item_backup"), True, bk))
        except Exception as e:
            results.append((i18n.t("vendor_diag_item_backup_check"), False, str(e)))

        for name, ok, detail in results:
            icon = "OK" if ok else "XX"
            self._diag_result.appendPlainText(f"[{icon}] {name}: {detail}")
            self._log_line(f"{i18n.t('vendor_diag_log_prefix')}: {name} - {i18n.t('vendor_diag_pass') if ok else i18n.t('vendor_diag_fail')}")

    # ══════════════════════════════════════════════
    #  2. 授权管理
    # ══════════════════════════════════════════════
    def _build_license_tab(self):
        inner = QWidget()
        r = QVBoxLayout(inner)
        r.setContentsMargins(20, 16, 20, 16)
        r.setSpacing(14)

        g = _group(i18n.t("vendor_license_status_title"))
        f = QFormLayout(g)
        f.setSpacing(8)

        try:
            from license_manager import LicenseManager
            lm = LicenseManager(db)
            machine_id = lm.get_machine_id()
            hotel_id = db.get_config("hotel_id") or i18n.t("vendor_status_na")
            exp = db.get_config("kill_switch_date") or i18n.t("vendor_status_not_set")
        except Exception:
            machine_id = db.get_config("machine_id") or i18n.t("vendor_status_na")
            hotel_id = db.get_config("hotel_id") or i18n.t("vendor_status_na")
            exp = db.get_config("kill_switch_date") or i18n.t("vendor_status_not_set")

        f.addRow(_card(i18n.t("vendor_card_machine_id"), str(machine_id)[:40]))
        f.addRow(_card(i18n.t("vendor_card_hotel_id"), hotel_id))
        f.addRow(_card(i18n.t("vendor_card_expiry"), exp))
        r.addWidget(g)

        g2 = _group(i18n.t("vendor_license_input_title"))
        gv2 = QVBoxLayout(g2)
        gv2.setSpacing(8)
        h = QHBoxLayout()
        self._lic_input = QLineEdit()
        self._lic_input.setPlaceholderText(i18n.t("vendor_license_ph"))
        h.addWidget(self._lic_input)
        btn = QPushButton(i18n.t("vendor_btn_activate"))
        btn.setObjectName("SolidPrimaryBtn")
        btn.clicked.connect(self._activate_license)
        h.addWidget(btn)
        gv2.addLayout(h)
        r.addWidget(g2)
        r.addStretch()

        self._tabs.addTab(_scroll_page(inner), i18n.t("vendor_tab_license"))

    def _activate_license(self):
        code = self._lic_input.text().strip()
        if not code:
            show_warning(self, i18n.t("vendor_title_license"), i18n.t("vendor_msg_enter_code"))
            return
        try:
            from license_manager import LicenseManager
            lm = LicenseManager(db)
            ok, msg = lm.activate(code)
            if ok:
                show_info(self, i18n.t("vendor_title_license"), i18n.t("vendor_msg_activate_ok").format(msg))
                self._log_line(i18n.t("vendor_log_activate_ok").format(msg))
            else:
                show_warning(self, i18n.t("vendor_title_license"), i18n.t("vendor_msg_activate_fail").format(msg))
                self._log_line(i18n.t("vendor_log_activate_fail").format(msg))
        except Exception as e:
            show_warning(self, i18n.t("vendor_title_license"), str(e))
            self._log_line(i18n.t("vendor_log_activate_error").format(e))

    # ══════════════════════════════════════════════
    #  3. 锁死控制
    # ══════════════════════════════════════════════
    def _build_lockdown_tab(self):
        from vendor_lockdown import LOCK_LEVELS, current_lock_status

        inner = QWidget()
        r = QVBoxLayout(inner)
        r.setContentsMargins(20, 16, 20, 16)
        r.setSpacing(14)

        g = _group(i18n.t("vendor_lockdown_level_title"))
        gv = QVBoxLayout(g)
        gv.setSpacing(8)

        self._lock_slider = QSlider(Qt.Orientation.Horizontal)
        self._lock_slider.setRange(0, 5)
        self._lock_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._lock_slider.setTickInterval(1)
        gv.addWidget(self._lock_slider)

        lbls = QHBoxLayout()
        for key in ["vendor_lock_lbl_0", "vendor_lock_lbl_1", "vendor_lock_lbl_2", "vendor_lock_lbl_3", "vendor_lock_lbl_4"]:
            lbls.addWidget(QLabel(i18n.t(key)))
        gv.addLayout(lbls)

        self._lock_status = QLabel(i18n.t("vendor_lock_current").format(i18n.t("vendor_lock_level_normal")))
        self._lock_status.setObjectName("Body")
        self._lock_status.setStyleSheet("font-weight:600;")
        gv.addWidget(self._lock_status)
        r.addWidget(g)

        st = current_lock_status()
        cur_level = st.get("level", "")
        idx = list(LOCK_LEVELS).index(cur_level) if cur_level in LOCK_LEVELS else 0
        self._lock_slider.setValue(idx)
        self._lock_slider.valueChanged.connect(self._on_lock_change)

        g2 = _group(i18n.t("vendor_lockdown_unlock_title"))
        gv2 = QVBoxLayout(g2)
        gv2.setSpacing(8)
        self._svc_input = QLineEdit()
        self._svc_input.setPlaceholderText(i18n.t("vendor_lockdown_ph"))
        gv2.addWidget(self._svc_input)
        btn = QPushButton(i18n.t("vendor_btn_unlock"))
        btn.setObjectName("SolidPrimaryBtn")
        btn.clicked.connect(self._unlock)
        gv2.addWidget(btn)
        r.addWidget(g2)
        r.addStretch()

        self._tabs.addTab(_scroll_page(inner), i18n.t("vendor_tab_lockdown"))

    def _on_lock_change(self, val):
        from vendor_lockdown import LOCK_LEVELS, sync_lock_level
        levels = list(LOCK_LEVELS)
        if val < len(levels):
            level = levels[val]
            names = {
                0: i18n.t("vendor_lock_name_0"),
                1: i18n.t("vendor_lock_name_1"),
                2: i18n.t("vendor_lock_name_2"),
                3: i18n.t("vendor_lock_name_3"),
                4: i18n.t("vendor_lock_name_4"),
                5: i18n.t("vendor_lock_name_5"),
            }
            self._lock_status.setText(i18n.t("vendor_lock_current").format(names.get(val, level)))
            if val >= 2:
                sync_lock_level(level, source="vendor_console")
                self._log_line(i18n.t("vendor_log_lock_set").format(level))

    def _unlock(self):
        code = self._svc_input.text().strip()
        if code != "solid2026":
            show_warning(self, i18n.t("vendor_title_unlock"), i18n.t("vendor_msg_code_error"))
            return
        from vendor_lockdown import sync_lock_level
        sync_lock_level("", source="vendor_console")
        self._lock_slider.setValue(0)
        self._lock_status.setText(i18n.t("vendor_lock_unlocked"))
        self._log_line(i18n.t("vendor_log_unlocked"))
        show_info(self, i18n.t("vendor_title_unlock"), i18n.t("vendor_msg_unlocked"))

    # ══════════════════════════════════════════════
    #  4. 远程更新
    # ══════════════════════════════════════════════
    def _build_update_tab(self):
        inner = QWidget()
        r = QVBoxLayout(inner)
        r.setContentsMargins(20, 16, 20, 16)
        r.setSpacing(14)

        g = _group(i18n.t("vendor_update_version_title"))
        f = QFormLayout(g)
        f.setSpacing(8)
        try:
            from brand_config_v4 import APP_VERSION
            ver = APP_VERSION
        except Exception:
            ver = "1.0.0"
        f.addRow(_card(i18n.t("vendor_card_current_ver"), ver))
        r.addWidget(g)

        g2 = _group(i18n.t("vendor_update_check_title"))
        gv2 = QVBoxLayout(g2)
        gv2.setSpacing(8)
        btn = QPushButton(i18n.t("vendor_btn_check_update"))
        btn.setObjectName("SolidPrimaryBtn")
        btn.setMinimumHeight(36)
        btn.clicked.connect(self._check_update)
        gv2.addWidget(btn)
        self._update_status = QLabel(i18n.t("vendor_update_waiting"))
        self._update_status.setWordWrap(True)
        self._update_status.setObjectName("Small")
        # color via objectName="Small"
        gv2.addWidget(self._update_status)
        r.addWidget(g2)

        g3 = _group(i18n.t("vendor_update_manual_title"))
        gv3 = QVBoxLayout(g3)
        gv3.setSpacing(8)
        btn2 = QPushButton(i18n.t("vendor_btn_usb_update"))
        btn2.setObjectName("SolidPrimaryBtn")
        btn2.clicked.connect(self._usb_update)
        gv3.addWidget(btn2)
        r.addWidget(g3)
        r.addStretch()

        self._tabs.addTab(_scroll_page(inner), i18n.t("vendor_tab_update"))

    def _check_update(self):
        try:
            from heartbeat_service import check_cloud_update
            info = check_cloud_update()
            if info.get("update_available"):
                ver = info.get("version", "?")
                self._update_status.setText(i18n.t("vendor_update_available").format(ver, info.get('url', '—')))
                self._log_line(i18n.t("vendor_log_update_found").format(ver))
            else:
                self._update_status.setText(i18n.t("vendor_update_latest"))
                self._log_line(i18n.t("vendor_update_latest"))
        except Exception as e:
            self._update_status.setText(i18n.t("vendor_update_failed").format(e))
            self._log_line(i18n.t("vendor_log_update_error").format(e))

    def _usb_update(self):
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("vendor_title_pick_installer"), "", i18n.t("vendor_filter_installer"))
        if not path:
            return
        self._log_line(i18n.t("vendor_log_manual_update").format(path))
        show_info(self, i18n.t("vendor_title_update"), i18n.t("vendor_msg_usb_update").format(path))

    # ══════════════════════════════════════════════
    #  5. 备份管理
    # ══════════════════════════════════════════════
    def _build_backup_tab(self):
        inner = QWidget()
        r = QVBoxLayout(inner)
        r.setContentsMargins(20, 16, 20, 16)
        r.setSpacing(14)

        g = _group(i18n.t("vendor_backup_title"))
        gv = QVBoxLayout(g)
        gv.setSpacing(8)

        btn1 = QPushButton(i18n.t("vendor_btn_backup"))
        btn1.setObjectName("SolidPrimaryBtn")
        btn1.setMinimumHeight(36)
        btn1.clicked.connect(self._do_backup)
        gv.addWidget(btn1)

        btn2 = QPushButton(i18n.t("vendor_btn_restore"))
        btn2.setObjectName("SolidPrimaryBtn")
        btn2.setMinimumHeight(36)
        btn2.clicked.connect(self._do_restore)
        gv.addWidget(btn2)

        self._backup_info = QLabel(i18n.t("vendor_backup_last").format(db.get_config('last_backup') or i18n.t("vendor_status_none")))
        # color via objectName="Small"
        gv.addWidget(self._backup_info)

        r.addWidget(g)
        r.addStretch()

        self._tabs.addTab(_scroll_page(inner), i18n.t("vendor_tab_backup"))

    def _do_backup(self):
        try:
            result = db.backup_to()
            db.set_config("last_backup", datetime.datetime.now().isoformat())
            self._backup_info.setText(i18n.t("vendor_backup_last").format(datetime.datetime.now().strftime('%Y-%m-%d %H:%M')))
            self._log_line(i18n.t("vendor_log_backup_ok").format(result))
            show_info(self, i18n.t("vendor_title_backup"), i18n.t("vendor_msg_backup_ok").format(result))
        except Exception as e:
            self._log_line(i18n.t("vendor_log_backup_fail").format(e))
            show_warning(self, i18n.t("vendor_title_backup"), str(e))

    def _do_restore(self):
        if not ask_confirm(self, i18n.t("vendor_title_restore"), i18n.t("vendor_msg_restore_confirm")):
            return
        try:
            path, _ = QFileDialog.getOpenFileName(self, i18n.t("vendor_title_pick_backup"), "", i18n.t("vendor_filter_db"))
            if not path:
                return
            db.restore_from(path)
            self._log_line(i18n.t("vendor_log_restore_ok").format(path))
            show_info(self, i18n.t("vendor_title_restore"), i18n.t("vendor_msg_restore_ok"))
        except Exception as e:
            self._log_line(i18n.t("vendor_log_restore_fail").format(e))
            show_warning(self, i18n.t("vendor_title_restore"), str(e))

    # ══════════════════════════════════════════════
    #  6. 数据库工具
    # ══════════════════════════════════════════════
    def _build_db_tab(self):
        inner = QWidget()
        r = QVBoxLayout(inner)
        r.setContentsMargins(20, 16, 20, 16)
        r.setSpacing(14)

        g = _group(i18n.t("vendor_db_stats_title"))
        gv = QVBoxLayout(g)
        gv.setSpacing(8)
        try:
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            for t in tables:
                tn = t[0]
                cnt = db.execute(f"SELECT COUNT(*) FROM [{tn}]").fetchone()[0]
                gv.addWidget(QLabel(i18n.t("vendor_db_row_count").format(tn, cnt)))
        except Exception as e:
            gv.addWidget(QLabel(i18n.t("vendor_db_query_failed").format(e)))
        r.addWidget(g)

        g2 = _group(i18n.t("vendor_db_sql_title"))
        gv2 = QVBoxLayout(g2)
        gv2.setSpacing(8)
        self._sql_input = QPlainTextEdit()
        self._sql_input.setMaximumHeight(80)
        self._sql_input.setPlaceholderText(i18n.t("vendor_sql_ph"))
        gv2.addWidget(self._sql_input)
        btn = QPushButton(i18n.t("vendor_btn_exec_sql"))
        btn.setObjectName("SolidPrimaryBtn")
        btn.clicked.connect(self._exec_sql)
        gv2.addWidget(btn)
        self._sql_result = QPlainTextEdit()
        self._sql_result.setReadOnly(True)
        self._sql_result.setMaximumHeight(200)
        self._sql_result.setObjectName("VendorConsoleMono")
        gv2.addWidget(self._sql_result)
        r.addWidget(g2)
        r.addStretch()

        self._tabs.addTab(_scroll_page(inner), i18n.t("vendor_tab_db"))

    def _exec_sql(self):
        sql = self._sql_input.toPlainText().strip()
        if not sql:
            return
        if not ask_confirm(self, i18n.t("vendor_title_confirm_sql"), i18n.t("vendor_msg_confirm_sql").format(sql)):
            return
        try:
            rows = db.execute(sql).fetchall()
            self._sql_result.clear()
            for row in rows[:50]:
                self._sql_result.appendPlainText(str(row))
            self._log_line(i18n.t("vendor_log_sql_ok").format(len(rows)))
        except Exception as e:
            self._sql_result.setPlainText(i18n.t("vendor_error_prefix").format(e))
            self._log_line(i18n.t("vendor_log_sql_fail").format(e))

    # ══════════════════════════════════════════════
    #  7. 云端同步
    # ══════════════════════════════════════════════
    def _build_cloud_tab(self):
        inner = QWidget()
        r = QVBoxLayout(inner)
        r.setContentsMargins(20, 16, 20, 16)
        r.setSpacing(14)

        g = _group(i18n.t("vendor_cloud_status_title"))
        f = QFormLayout(g)
        f.setSpacing(8)
        f.addRow(_card(i18n.t("vendor_card_cloud_url"),
                        db.get_config("cloud_worker_url") or i18n.t("vendor_status_not_configured")))
        f.addRow(_card(i18n.t("vendor_card_cloud_switch"),
                        i18n.t("vendor_status_on") if (db.get_config("cloud_enabled") or "0") == "1" else i18n.t("vendor_status_off")))
        f.addRow(_card(i18n.t("vendor_card_cloud_interval"),
                        f"{db.get_config('cloud_poll_interval') or '3'} {i18n.t('vendor_seconds')}"))
        r.addWidget(g)

        g2 = _group(i18n.t("vendor_cloud_actions_title"))
        gv2 = QVBoxLayout(g2)
        gv2.setSpacing(8)
        btn = QPushButton(i18n.t("vendor_btn_sync"))
        btn.setObjectName("SolidPrimaryBtn")
        btn.clicked.connect(self._manual_sync)
        gv2.addWidget(btn)
        self._sync_status = QLabel(i18n.t("vendor_status_ready"))
        # color via objectName="Small"
        gv2.addWidget(self._sync_status)
        r.addWidget(g2)

        # ── [sub-g] 云端握手包区块 ──
        # 列出采集器回传到云端的待导入握手包，操作员可一键「下载并导入」。
        g3 = _group("☁ 云端握手包（采集器回传 · 待导入）")
        gv3 = QVBoxLayout(g3)
        gv3.setSpacing(8)

        h_row = QHBoxLayout()
        self._cloud_hv_refresh_btn = QPushButton("刷新待导入列表")
        self._cloud_hv_refresh_btn.setObjectName("SolidPrimaryBtn")
        self._cloud_hv_refresh_btn.setMinimumHeight(36)
        self._cloud_hv_refresh_btn.clicked.connect(self._on_cloud_handover_refresh)
        h_row.addWidget(self._cloud_hv_refresh_btn)
        self._cloud_hv_status = QLabel("点击「刷新」拉取云端待导入列表")
        from design_tokens import _p
        self._cloud_hv_status.setStyleSheet(f"color:{_p('text_muted','#6B7280')}; font-size:12px;")
        self._cloud_hv_status.setWordWrap(True)
        h_row.addWidget(self._cloud_hv_status, 1)
        gv3.addLayout(h_row)

        self._cloud_hv_list = QListWidget()
        self._cloud_hv_list.setMinimumHeight(120)
        self._cloud_hv_list.setMaximumHeight(220)
        gv3.addWidget(self._cloud_hv_list)

        # 详情 + 操作按钮
        self._cloud_hv_detail = QPlainTextEdit()
        self._cloud_hv_detail.setReadOnly(True)
        self._cloud_hv_detail.setObjectName("VendorConsoleMono")
        self._cloud_hv_detail.setMaximumHeight(140)
        gv3.addWidget(self._cloud_hv_detail)

        h_act = QHBoxLayout()
        self._cloud_hv_import_btn = QPushButton("⬇ 下载并导入")
        self._cloud_hv_import_btn.setObjectName("SolidPrimaryBtn")
        self._cloud_hv_import_btn.setMinimumHeight(36)
        self._cloud_hv_import_btn.setEnabled(False)
        self._cloud_hv_import_btn.clicked.connect(self._on_cloud_handover_import)
        h_act.addWidget(self._cloud_hv_import_btn)
        h_act.addStretch()
        gv3.addLayout(h_act)
        r.addWidget(g3)

        # 暂存拉取结果，供选中后导入
        self._cloud_handovers: list[dict] = []
        self._cloud_hv_idx = -1

        r.addStretch()

        self._tabs.addTab(_scroll_page(inner), i18n.t("vendor_tab_cloud"))

    def _on_cloud_handover_refresh(self):
        """[sub-g] 拉取云端待导入握手包列表。"""
        try:
            from lock_deploy.cloud_handover_pull import CloudHandoverPuller
            puller = CloudHandoverPuller()
            if not puller.is_enabled():
                self._cloud_hv_status.setText("⚠ 云端未配置（system_config.cloud_worker_url 为空）")
                self._cloud_hv_list.clear()
                self._cloud_handovers = []
                return
            self._cloud_hv_refresh_btn.setEnabled(False)
            self._cloud_hv_refresh_btn.setText("拉取中...")
            self._cloud_hv_status.setText("正在拉取云端待导入握手包列表...")
            # 后台线程，避免阻塞 UI
            import threading
            def _worker():
                try:
                    items = puller.list_pending_handovers()
                except Exception as e:
                    items = []
                    logger.warning("[sub-g] 云端握手包列表拉取异常: %s", e)
                from PySide6.QtCore import QMetaObject, Qt
                QMetaObject.invokeMethod(
                    self, "_on_cloud_handover_refresh_done",
                    Qt.ConnectionType.QueuedConnection,
                )
                self._cloud_handovers_pending = items
            self._cloud_handovers_pending = []
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
        except Exception as e:
            self._cloud_hv_status.setText(f"⚠ 异常: {e}")
            self._cloud_hv_refresh_btn.setEnabled(True)
            self._cloud_hv_refresh_btn.setText("刷新待导入列表")

    # 让 Qt 能 invokeMethod
    def _on_cloud_handover_refresh_done(self):
        items = getattr(self, "_cloud_handovers_pending", []) or []
        self._cloud_hv_refresh_btn.setEnabled(True)
        self._cloud_hv_refresh_btn.setText("刷新待导入列表")
        self._cloud_handovers = items
        self._cloud_hv_list.clear()
        if not items:
            self._cloud_hv_status.setText("（无待导入握手包，或云端未配置/不可达）")
            self._cloud_hv_import_btn.setEnabled(False)
            self._cloud_hv_detail.clear()
            return
        for h in items:
            hotel = h.get("hotel_name", "?")
            brand = h.get("brand", "?")
            uploaded = (h.get("uploaded_at") or "")[:16].replace("T", " ")
            size_mb = (h.get("size_bytes") or 0) / 1024 / 1024
            self._cloud_hv_list.addItem(
                f"📦 {hotel} · {brand} · {size_mb:.1f}MB · {uploaded}"
            )
        self._cloud_hv_list.setCurrentRow(0)
        self._cloud_hv_status.setText(f"共 {len(items)} 个待导入握手包")
        # 监听选中
        try:
            self._cloud_hv_list.currentRowChanged.disconnect(self._on_cloud_handover_select)
        except (TypeError, RuntimeError):
            pass
        self._cloud_hv_list.currentRowChanged.connect(self._on_cloud_handover_select)

    def _on_cloud_handover_select(self, idx):
        if idx < 0 or idx >= len(self._cloud_handovers):
            self._cloud_hv_import_btn.setEnabled(False)
            return
        self._cloud_hv_idx = idx
        h = self._cloud_handovers[idx]
        lines = []
        for k in ("cloud_id", "task_id", "hotel_id", "hotel_name",
                  "brand", "mode", "filename", "size_bytes",
                  "uploaded_at", "status"):
            if h.get(k) is not None:
                lines.append(f"{k}: {h[k]}")
        self._cloud_hv_detail.setPlainText("\n".join(lines))
        self._cloud_hv_import_btn.setEnabled(True)

    def _on_cloud_handover_import(self):
        """[sub-g] 下载云端握手包并导入 PMS。"""
        if self._cloud_hv_idx < 0 or self._cloud_hv_idx >= len(self._cloud_handovers):
            return
        h = self._cloud_handovers[self._cloud_hv_idx]
        cloud_id = h.get("cloud_id", "")
        hotel = h.get("hotel_name", "?")
        if not cloud_id:
            return
        if not ask_confirm(self, "下载并导入握手包",
                          f"确定要从云端下载并导入「{hotel}」的握手包吗？\n"
                          f"cloud_id: {cloud_id}"):
            return
        self._cloud_hv_import_btn.setEnabled(False)
        self._cloud_hv_import_btn.setText("下载并导入中...")
        # 后台执行（下载 + 导入可能耗时）
        import threading
        def _worker():
            try:
                from lock_deploy.handover_importer import import_from_cloud
                result = import_from_cloud(cloud_id)
            except Exception as e:
                result = {"ok": False, "errors": [str(e)]}
            self._cloud_import_result = result
            from PySide6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(
                self, "_on_cloud_handover_import_done",
                Qt.ConnectionType.QueuedConnection,
            )
        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _on_cloud_handover_import_done(self):
        result = getattr(self, "_cloud_import_result", {}) or {}
        self._cloud_hv_import_btn.setEnabled(True)
        self._cloud_hv_import_btn.setText("⬇ 下载并导入")
        if result.get("ok"):
            lines = [
                f"✅ 云端握手包导入成功",
                f"品牌: {result.get('brand', '?')}",
                f"发卡方式: {result.get('mode', '?')}",
                f"房间数: {result.get('rooms_imported', 0)}",
                f"在住客人: {result.get('guests_imported', 0)}",
                f"cloud_id: {result.get('cloud_id', '')}",
                f"本地路径: {result.get('local_path', '')}",
            ]
            show_info(self, "云端握手包导入成功", "\n".join(lines))
            self._lk_log("云端握手包 %s 导入成功: %s/%s" % (
                result.get("cloud_id", ""),
                result.get("brand", ""),
                result.get("mode", "")))
            # 刷新已导入列表
            try:
                self._lk_refresh_done()
            except Exception:
                pass
        else:
            errs = "\n".join(result.get("errors", ["未知错误"]))
            show_warning(self, "云端握手包导入失败", errs)
            self._lk_log("云端握手包 %s 导入失败: %s" % (
                result.get("cloud_id", ""), errs[:120]))

    def _manual_sync(self):
        try:
            from heartbeat_service import heartbeat_once
            result = heartbeat_once()
            self._sync_status.setText(i18n.t("vendor_sync_ok").format(result))
            self._log_line(i18n.t("vendor_log_sync_ok").format(result))
        except Exception as e:
            self._sync_status.setText(i18n.t("vendor_sync_fail").format(e))
            self._log_line(i18n.t("vendor_log_sync_fail").format(e))

    # ══════════════════════════════════════════════
    #  8. 门锁品牌探测（solo 选手现场收品牌）
    # ══════════════════════════════════════════════

    def _build_lock_tab(self):
        """第8个子Tab：门锁品牌探测 — 扫盘→探针→导入 一站完成。"""
        inner = QWidget()
        r = QVBoxLayout(inner)
        r.setContentsMargins(20, 16, 20, 16)
        r.setSpacing(14)

        # ── 1. 扫描区 ──
        g_scan = _group(i18n.t("vendor_lock_scan_title"))
        gv_scan = QVBoxLayout(g_scan)
        gv_scan.setSpacing(8)

        h_scan = QHBoxLayout()
        self._lk_scan = QPushButton(i18n.t("vendor_btn_auto_scan"))
        self._lk_scan.setObjectName("SolidPrimaryBtn")
        self._lk_scan.setMinimumHeight(36)
        self._lk_scan.clicked.connect(self._lk_do_scan)
        h_scan.addWidget(self._lk_scan)

        self._lk_browse = QPushButton(i18n.t("vendor_btn_browse"))
        self._lk_browse.setObjectName("FdGhostBtn")
        self._lk_browse.clicked.connect(self._lk_do_browse)
        h_scan.addWidget(self._lk_browse)
        h_scan.addStretch()
        gv_scan.addLayout(h_scan)

        self._lk_status = QLabel(i18n.t("vendor_lock_status_idle"))
        self._lk_status.setWordWrap(True)
        self._lk_status.setObjectName("Small")
        # color via objectName="Small"
        gv_scan.addWidget(self._lk_status)
        r.addWidget(g_scan)

        # ── 2. 候选列表 ──
        g_list = _group(i18n.t("vendor_lock_select_title"))
        gv_list = QVBoxLayout(g_list)
        gv_list.setSpacing(8)
        self._lk_list = QListWidget()
        self._lk_list.setMinimumHeight(100)
        self._lk_list.setMaximumHeight(180)
        self._lk_list.currentRowChanged.connect(self._lk_on_select)
        gv_list.addWidget(self._lk_list)
        r.addWidget(g_list)

        # ── 3. 详情 + 操作 ──
        g_detail = _group(i18n.t("vendor_lock_detail_title"))
        gv_detail = QVBoxLayout(g_detail)
        gv_detail.setSpacing(8)

        self._lk_detail = QPlainTextEdit()
        self._lk_detail.setReadOnly(True)
        self._lk_detail.setObjectName("VendorConsoleMono")
        self._lk_detail.setMaximumHeight(140)
        gv_detail.addWidget(self._lk_detail)

        h_act = QHBoxLayout()
        self._lk_btn_diag = QPushButton(i18n.t("vendor_btn_export_diag"))
        self._lk_btn_diag.setObjectName("FdGhostBtn")
        self._lk_btn_diag.setEnabled(False)
        self._lk_btn_diag.clicked.connect(self._lk_export_diag)
        h_act.addWidget(self._lk_btn_diag)

        self._lk_btn_probe = QPushButton(i18n.t("vendor_btn_probe_dll"))
        self._lk_btn_probe.setObjectName("SolidPrimaryBtn")
        self._lk_btn_probe.setEnabled(False)
        self._lk_btn_probe.clicked.connect(self._lk_do_probe)
        h_act.addWidget(self._lk_btn_probe)

        self._lk_btn_import = QPushButton(i18n.t("vendor_btn_import_brand"))
        self._lk_btn_import.setObjectName("SolidPrimaryBtn")
        self._lk_btn_import.setEnabled(False)
        self._lk_btn_import.clicked.connect(self._lk_do_import)
        h_act.addWidget(self._lk_btn_import)
        h_act.addStretch()
        gv_detail.addLayout(h_act)

        self._lk_probe_out = QPlainTextEdit()
        self._lk_probe_out.setObjectName("VendorConsoleMono")
        self._lk_probe_out.setReadOnly(True)
        self._lk_probe_out.hide()
        gv_detail.addWidget(self._lk_probe_out)

        r.addWidget(g_detail)

        # ── 4. 已导入品牌 ──
        g_done = _group(i18n.t("vendor_lock_imported_title"))
        gv_done = QVBoxLayout(g_done)
        gv_done.setSpacing(8)
        self._lk_done = QPlainTextEdit()
        self._lk_done.setObjectName("VendorConsoleMono")
        self._lk_done.setMaximumHeight(80)
        gv_done.addWidget(self._lk_done)

        # 接管向导按钮
        h_wiz = QHBoxLayout()
        btn_wiz = QPushButton(i18n.t("vendor_btn_wizard"))
        btn_wiz.setObjectName("SolidPrimaryBtn")
        btn_wiz.setMinimumHeight(36)
        btn_wiz.clicked.connect(self._lk_open_wizard)
        h_wiz.addWidget(btn_wiz)

        btn_import_handover = QPushButton(i18n.t("vendor_btn_import_handover"))
        btn_import_handover.setObjectName("FdGhostBtn")
        btn_import_handover.setMinimumHeight(36)
        btn_import_handover.clicked.connect(self._lk_import_collected)
        h_wiz.addWidget(btn_import_handover)

        btn_rollback = QPushButton(i18n.t("vendor_btn_rollback"))
        btn_rollback.setObjectName("FdGhostBtn")
        btn_rollback.setMinimumHeight(36)
        btn_rollback.clicked.connect(self._lk_rollback)
        h_wiz.addWidget(btn_rollback)

        h_wiz.addStretch()
        gv_done.addLayout(h_wiz)

        r.addWidget(g_done)
        r.addStretch()

        self._tabs.addTab(_scroll_page(inner), i18n.t("vendor_tab_lock"))

        self._lk_candidates = []
        self._lk_idx = -1
        self._lk_probe_data = {}
        self._lk_refresh_done()

    def _lk_log(self, msg: str):
        self._log_line(f"[{i18n.t('vendor_lock_log_prefix')}] {msg}")

    def _lk_do_scan(self):
        self._lk_scan.setEnabled(False)
        self._lk_status.setText(i18n.t("vendor_lock_scanning"))
        self._lk_list.clear()
        self._lk_detail.clear()
        self._lk_probe_out.hide()
        self._lk_btn_diag.setEnabled(False)
        self._lk_btn_probe.setEnabled(False)
        self._lk_btn_import.setEnabled(False)
        import threading
        t = threading.Thread(target=self._lk_scan_thread, daemon=True)
        t.start()

    def _lk_scan_thread(self):
        try:
            from lock_deploy import scan_for_lock_systems
            self._lk_candidates = scan_for_lock_systems(time_budget_s=8.0)
        except Exception as e:
            self._lk_candidates = []
            self._lk_log(i18n.t("vendor_lock_scan_failed").format(e))
        from PySide6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(
            self, "_lk_scan_done",
            Qt.ConnectionType.QueuedConnection,
        )

    def _lk_scan_done(self):
        self._lk_scan.setEnabled(True)
        self._lk_list.blockSignals(True)
        self._lk_list.clear()
        for i, c in enumerate(self._lk_candidates):
            icon = "✓ " if c.supported else "⚠ "
            self._lk_list.addItem(f"{icon}{c.brand}  [{c.path}]  score={c.score}")
        self._lk_list.blockSignals(False)
        if self._lk_candidates:
            self._lk_list.setCurrentRow(0)
            self._lk_status.setText(i18n.t("vendor_lock_found").format(len(self._lk_candidates)))
        else:
            self._lk_status.setText(i18n.t("vendor_lock_not_found"))

    def _lk_do_browse(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, i18n.t("vendor_lock_browse_title"))
        if not d:
            return
        from lock_deploy import LockSystemScanner
        sc = LockSystemScanner(time_budget_s=3.0)
        try:
            cs = sc.scan(seeds=[d])
        except Exception:
            cs = []
        if not cs:
            from lock_deploy import InstallationCandidate
            from pathlib import Path
            cs = [InstallationCandidate(path=Path(d), brand=i18n.t("vendor_lock_manual_brand"), score=0)]
        self._lk_candidates = cs
        self._lk_list.blockSignals(True)
        self._lk_list.clear()
        for i, c in enumerate(cs):
            icon = "✓ " if c.supported else "⚠ "
            self._lk_list.addItem(f"{icon}{c.brand}  [{c.path}]")
        self._lk_list.blockSignals(False)
        self._lk_list.setCurrentRow(0)
        self._lk_status.setText(i18n.t("vendor_lock_selected").format(d))

    def _lk_on_select(self, idx):
        if idx < 0 or idx >= len(self._lk_candidates):
            return
        self._lk_idx = idx
        c = self._lk_candidates[idx]
        self._lk_probe_out.hide()
        self._lk_probe_data = {}
        lines = [
            i18n.t("vendor_lock_detail_brand").format(c.brand),
            i18n.t("vendor_lock_detail_path").format(c.path),
            i18n.t("vendor_lock_detail_supported").format(i18n.t("vendor_lock_supported") if c.supported else i18n.t("vendor_lock_unsupported")),
            i18n.t("vendor_lock_detail_score").format(c.score),
        ]
        if c.matched_required:
            lines.append(i18n.t("vendor_lock_detail_files").format(', '.join(c.matched_required)))
        if c.has_mdb:
            lines.append(i18n.t("vendor_lock_detail_mdb").format(len(c.mdb_paths)))
        self._lk_detail.setPlainText("\n".join(lines))
        self._lk_btn_diag.setEnabled(True)
        self._lk_btn_probe.setEnabled(True)
        self._lk_btn_import.setEnabled(False)

    def _lk_do_probe(self):
        idx = self._lk_idx
        if idx < 0 or idx >= len(self._lk_candidates):
            return
        install_dir = str(self._lk_candidates[idx].path)
        self._lk_btn_probe.setEnabled(False)
        self._lk_btn_probe.setText(i18n.t("vendor_lock_probing_btn"))
        self._lk_status.setText(i18n.t("vendor_lock_probing"))
        self._lk_probe_out.hide()
        import threading
        t = threading.Thread(
            target=self._lk_probe_thread, args=(install_dir,), daemon=True,
        )
        t.start()

    def _lk_probe_thread(self, install_dir: str):
        try:
            from lock_deploy.dll_probe import probe
            self._lk_probe_data = probe(install_dir)
        except Exception as e:
            self._lk_probe_data = {"detected": False, "error": str(e)}
            self._lk_log(i18n.t("vendor_lock_probe_error").format(e))
        from PySide6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(
            self, "_lk_probe_done",
            Qt.ConnectionType.QueuedConnection,
        )

    def _lk_probe_done(self):
        self._lk_btn_probe.setEnabled(True)
        self._lk_btn_probe.setText(i18n.t("vendor_btn_probe_dll"))
        d = self._lk_probe_data

        if not d.get("detected"):
            self._lk_status.setText(i18n.t("vendor_lock_no_dll"))
            self._lk_probe_out.setPlainText(
                d.get("error", i18n.t("vendor_lock_no_dll_hint"))
            )
            self._lk_probe_out.show()
            return

        brand = d.get("brand_guess", "?")
        lines = [
            i18n.t("vendor_lock_probe_brand").format(brand),
            i18n.t("vendor_lock_probe_dll").format(d.get('dll_path', '—')),
            i18n.t("vendor_lock_probe_exports").format(len(d.get('exports', []))),
            i18n.t("vendor_lock_probe_matched").format(len(d.get('matched_functions', {}))),
            i18n.t("vendor_lock_probe_confidence").format(d.get('confidence', 0)),
            i18n.t("vendor_lock_probe_can_issue").format(i18n.t("vendor_status_yes") if d.get('can_issue') else i18n.t("vendor_status_unknown")),
            "",
            i18n.t("vendor_lock_probe_matched_groups"),
        ]
        for g, fn in sorted(d.get("matched_functions", {}).items()):
            lines.append(f"  {g:20s} → {fn}")

        # 如果有硬编码回退也显示
        hf = d.get("hardcoded_fallback", {})
        if hf:
            lines.append("")
            lines.append(i18n.t("vendor_lock_probe_fallback"))
            for g, fn in sorted(hf.items()):
                lines.append(f"  {g:20s} → {fn}")

        self._lk_probe_out.setPlainText("\n".join(lines))
        self._lk_probe_out.show()
        self._lk_status.setText(i18n.t("vendor_lock_probe_done_status").format(brand, d.get('confidence', 0)))

        cp = d.get("candidate_profile")
        if cp and d.get("confidence", 0) >= 0.3:
            self._lk_btn_import.setEnabled(True)

    def _lk_export_diag(self):
        idx = self._lk_idx
        if idx < 0 or idx >= len(self._lk_candidates):
            return
        c = self._lk_candidates[idx]
        try:
            from pathlib import Path
            from PySide6.QtWidgets import QFileDialog
            from lock_deploy import build_unsupported_report

            out_dir = QFileDialog.getExistingDirectory(self, i18n.t("vendor_lock_diag_save_title"))
            if not out_dir:
                return

            hotel = db.get_config("hotel_name") or ""
            zip_path = build_unsupported_report(c, out_dir=Path(out_dir), hotel_name=hotel)
            show_info(
                self, i18n.t("vendor_lock_diag_exported"),
                i18n.t("vendor_msg_diag_exported").format(zip_path)
            )
            self._lk_log(i18n.t("vendor_lock_log_diag_exported").format(zip_path))
        except Exception as e:
            show_warning(self, i18n.t("vendor_lock_export_fail"), str(e))
            self._lk_log(i18n.t("vendor_lock_log_diag_fail").format(e))

    def _lk_do_import(self):
        cp = self._lk_probe_data.get("candidate_profile")
        if not cp:
            return
        brand = cp.get("brand", "?")
        confidence = cp.get("confidence", 0)
        if not ask_confirm(
            self, i18n.t("vendor_title_confirm_import"),
            i18n.t("vendor_msg_confirm_import").format(brand, confidence, cp.get('dll', {}).get('path', '—')),
        ):
            return
        try:
            from lock_deploy import install_profile
            ok = install_profile(cp)
        except Exception as e:
            show_warning(self, i18n.t("vendor_title_import_fail"), str(e))
            self._lk_log(i18n.t("vendor_lock_log_import_fail").format(e))
            return
        if ok:
            show_info(
                self, i18n.t("vendor_title_import_ok"),
                i18n.t("vendor_msg_import_ok").format(brand),
            )
            self._lk_log(i18n.t("vendor_lock_log_import_ok").format(brand))
            self._lk_btn_import.setEnabled(False)
            self._lk_refresh_done()
        else:
            show_warning(self, i18n.t("vendor_title_import_fail"), i18n.t("vendor_msg_import_fail"))

    def _lk_refresh_done(self):
        try:
            from lock_deploy import list_available_profiles
            profiles = list_available_profiles()
        except Exception:
            profiles = []
        if profiles:
            self._lk_done.setPlainText(
                "\n".join(f"{p['filename']:40s} {p['brand']:20s}" for p in profiles)
            )
        else:
            self._lk_done.setPlainText(i18n.t("vendor_lock_no_profiles"))

    def _lk_open_wizard(self):
        """打开门锁接管向导。"""
        try:
            from lock_deploy import show_deploy_dialog
            show_deploy_dialog(self)
        except ImportError:
            from ui_helpers import show_warning
            show_warning(self, i18n.t("vendor_title_lock"), i18n.t("vendor_lock_module_unavailable"))

    def _lk_import_collected(self):
        """导入采集器学习配置（支持 .solidhandover 和旧版 .json）。"""
        from PySide6.QtWidgets import QFileDialog
        import json, time, shutil
        from pathlib import Path
        from database import db

        fpath, _ = QFileDialog.getOpenFileName(
            self, i18n.t("vendor_lock_import_cfg_title"),
            "",
            "握手包 (*.solidhandover);;配置文件 (*.json);;所有文件 (*)"
        )
        if not fpath:
            return

        # ── 处理 .solidhandover 格式 ──
        if fpath.endswith(".solidhandover"):
            from lock_deploy.handover_importer import HandoverImporter
            imp = HandoverImporter()
            result = imp.run(fpath)
            if result["ok"]:
                lines = [
                    f"品牌: {result.get('brand', '?')}",
                    f"发卡方式: {'DLL直调' if result.get('mode') == 'dll_direct' else '寄生原厂软件'}",
                    f"房间数: {result.get('rooms_imported', 0)}",
                    f"在住客人: {result.get('guests_imported', 0)}",
                    f"Profile: {result.get('profile_file', '')}",
                ]
                show_info(self, "导入成功", "\n".join(lines))
                logger.info(f"握手包导入成功: mode={result.get('mode')}")
                self._lk_refresh_done()
            else:
                errors = "\n".join(result.get("errors", ["未知错误"]))
                show_warning(self, "导入失败", f"请检查握手包文件:\n{errors}")
                imp.rollback()
            return

        # ── 旧版 JSON 导入逻辑（兼容） ──
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            show_warning(self, i18n.t("vendor_title_import_fail"), i18n.t("vendor_msg_cfg_read_fail").format(e))
            return

        profile = None
        profile_path_in = ""
        if "learned_profile" in config:
            profile_path_in = config.get("learned_profile", "")
            if profile_path_in and Path(profile_path_in).exists():
                try:
                    with open(profile_path_in, "r", encoding="utf-8") as f:
                        profile = json.load(f)
                except Exception:
                    pass
        elif "adapter_id" in config or "brand" in config:
            profile = config

        if not profile:
            show_warning(self, i18n.t("vendor_title_import_fail"), i18n.t("vendor_msg_no_profile"))
            return

        profiles_dir = Path(__file__).resolve().parent.parent / "lock_adapters" / "profile" / "profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%y%m%d_%H%M%S")
        fname = f"collected_{ts}.json"
        dst = profiles_dir / fname

        try:
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
        except Exception as e:
            show_warning(self, i18n.t("vendor_title_import_fail"), i18n.t("vendor_msg_cfg_write_fail").format(e))
            return

        try:
            db.set_config("learned_profile", str(dst))
            db.set_config("learned_at", time.strftime("%Y-%m-%d %H:%M:%S"))
            card_types = ", ".join(profile.get("card_types", {}).keys())
            db.set_config("learned_card_types", card_types)

            # ── 法医级 v2.0 配置 ──
            is_forensic = config.get("version") == "2.0" or "forensic_config" in config
            if is_forensic:
                identity = config.get("identity", {})
                if identity.get("dls_co_id"):
                    db.set_config("lock_takeover_dlsCoID", identity["dls_co_id"])
                if identity.get("hotel_id"):
                    db.set_config("lock_takeover_hotel_id", identity["hotel_id"])
                if identity.get("pc_id"):
                    db.set_config("lock_takeover_pc_id", identity["pc_id"])

                # CardLockAuto 回放配置
                filesystem = config.get("filesystem", {})
                if filesystem.get("install_dir"):
                    db.set_config("cardlockauto_install_dir", filesystem["install_dir"])

                ui_map = config.get("ui_map", {})
                btn_map = ui_map.get("card_type_buttons", {})
                if btn_map:
                    db.set_config("cardlockauto_button_map", json.dumps(btn_map, ensure_ascii=False))

                # 工作流
                workflow = config.get("workflow", {})
                if workflow:
                    db.set_config("cardlockauto_workflow", json.dumps(workflow, ensure_ascii=False))

                # 法医配置路径
                forensic_file = config.get("forensic_config", "")
                if forensic_file:
                    db.set_config("learned_forensic_path", forensic_file)

                db.set_config("learned_config_version", "2.0")
                logger.info("法医级 v2.0 配置已入库: dlsCoID=%s, btn_map=%d键",
                            identity.get("dls_co_id", ""), len(btn_map))
        except Exception as e:
            logger.warning("配置入库部分失败: %s", e)

        imported_rooms = 0
        imported_guests = 0
        room_data_file = config.get("room_data", "")
        if room_data_file:
            rd_dir = Path(fpath).parent
            rd_path = rd_dir / room_data_file
            if not rd_path.exists():
                rd_path = profiles_dir / room_data_file
            if rd_path.exists():
                try:
                    with open(rd_path, "r", encoding="utf-8") as _f:
                        room_data = json.load(_f)
                except Exception:
                    room_data = None
                if room_data and room_data.get("rooms"):
                    for r in room_data["rooms"]:
                        try:
                            rid = r.get("room_id", "")
                            if not rid:
                                continue
                            exist = db.execute(
                                "SELECT 1 FROM rooms WHERE room_id=?", (rid,)
                            ).fetchone()
                            if exist:
                                if r.get("lock_no"):
                                    db.execute(
                                        "UPDATE rooms SET lock_no=?, bld_no=?, flr_no=?, rom_id=? WHERE room_id=?",
                                        (r["lock_no"], r.get("bld_no", 1),
                                         r.get("flr_no", 0), r.get("rom_id", 0), rid),
                                    )
                            else:
                                db.execute(
                                    "INSERT INTO rooms (room_id, floor, room_type, status, lock_no, bld_no, flr_no, rom_id) "
                                    "VALUES (?, ?, ?, 'VC', ?, ?, ?, ?)",
                                    (rid, r.get("floor", ""), r.get("room_type", i18n.t("vendor_std_room_type")),
                                     r.get("lock_no", ""), r.get("bld_no", 1),
                                     r.get("flr_no", 0), r.get("rom_id", 0)),
                                )
                                try:
                                    db.execute(
                                        "INSERT OR IGNORE INTO buildings (building_id, bld_no, name, sort_order) VALUES (?, ?, ?, ?)",
                                        (str(r.get("bld_no", 1)), r.get("bld_no", 1),
                                         f"{r.get('bld_no', 1):02d}", r.get("bld_no", 1)),
                                    )
                                except Exception:
                                    pass
                            imported_rooms += 1
                        except Exception:
                            pass
                    for g in room_data.get("guests", []):
                        try:
                            g_rid = g.get("room_id", "")
                            g_name = g.get("name", i18n.t("vendor_guest_migrated"))
                            if not g_rid:
                                continue
                            exist_g = db.execute(
                                "SELECT 1 FROM guests WHERE room_id=? AND status='INHOUSE'",
                                (g_rid,),
                            ).fetchone()
                            if exist_g:
                                continue
                            db.execute(
                                "INSERT INTO guests (room_id, name, id_card, phone, checkin_time, status) "
                                "VALUES (?, ?, ?, ?, COALESCE(NULLIF(?, ''), datetime('now')), 'INHOUSE')",
                                (g_rid, g_name, g.get("id_card", ""),
                                 g.get("phone", ""), g.get("checkin_time", "")),
                            )
                            imported_guests += 1
                        except Exception:
                            pass

        summary_parts = [
            i18n.t("vendor_import_summary_profile").format(fname),
            i18n.t("vendor_import_summary_card_types").format(', '.join(profile.get('card_types', {}).keys())),
            i18n.t("vendor_import_summary_confidence").format(profile.get('confidence', '?')),
        ]
        if imported_rooms:
            summary_parts.append(i18n.t("vendor_import_summary_rooms").format(imported_rooms))
        if imported_guests:
            summary_parts.append(i18n.t("vendor_import_summary_guests").format(imported_guests))
        if not imported_rooms and not imported_guests:
            summary_parts.append(i18n.t("vendor_import_summary_no_data"))
        summary_parts.append("")
        summary_parts.append(i18n.t("vendor_import_summary_next"))
        show_info(self, i18n.t("vendor_title_import_ok"), "\n".join(summary_parts))

    def _lk_rollback(self):
        """回滚门锁配置到导入握手包前的状态。"""
        from lock_deploy.handover_importer import HandoverImporter

        if not ask_confirm(self, "回滚确认", "确定要回滚门锁配置吗？\n这将恢复到导入握手包前的状态。"):
            return

        imp = HandoverImporter()
        result = imp.rollback()
        if result["ok"]:
            show_info(self, "回滚完成", "门锁配置已恢复到导入前的状态。")
            self._lk_refresh_done()
        else:
            errors = "\n".join(result.get("errors", ["未知错误"]))
            show_warning(self, "回滚失败", f"回滚过程中出现错误:\n{errors}")
