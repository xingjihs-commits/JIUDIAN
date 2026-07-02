"""
wizard.py — Solid 学习助手主窗口（瘦身版）

职责：
  - 组装子面板（Identify / Sample / Graduation / Handover / CoachBar）
  - 协调各 Worker 线程
  - 管理 UI 状态流转

改动要点：
  - 从 4200 行压缩到 ~1200 行
  - Bug 全修：QSplitter 导入、self._sf、scroll 变量名、CARD_FIELDS 默认值、to_dict 恢复
  - UI 重构：两栏布局更清晰，去掉重复按钮
  - 所有子面板逻辑移入 widgets/
"""

from __future__ import annotations

import json as _json
import logging
import os
import shutil
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from PySide6.QtCore import Qt, QThread, Signal, QDate, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QGroupBox, QFileDialog, QLineEdit,
    QWidget, QScrollArea, QFrame,
    QComboBox, QSpinBox, QStackedWidget, QSizePolicy, QCheckBox,
    QSplitter,  # [FIX] 原版遗漏此导入 → NameError
)

from .constants import (
    BUILD_TAG, PALETTE, C_PRIMARY, C_GREEN, C_TEXT, C_MUTED,
    C_BG, C_BG_ALT, C_DANGER, C_WARN,
    CARD_TYPES, CARD_NAMES, CARD_KEY_MAP, CARD_KEY_TO_NAME,
    CARD_FIELDS, CARD_DESC_MAP, GRADUATION_DIMS,
    trunc, collector_work_dir,
)
from .models import SampleCapture
from .workers import (
    DetectWorker, ReadCardWorker, ProbeWorker, BuildWorker,
    AnalyzeWorker, TokenCollectionWorker, EraseWorker, TaskFetchWorker,
)
from .widgets import StepCoachBar, GraduationPanel, IdentifyPanel, SamplePanel, HandoverPanel

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  主窗口
# ══════════════════════════════════════════════════════════

class CollectorWizard(QDialog):

    def __init__(self, parent=None, install_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Solid 学习助手 · 两栏v4")
        self.setMinimumSize(920, 720)

        # ── 状态 ──────────────────────────────────────
        self._install_dir   = install_dir
        self._loaded_dll    = ""
        self._bridge_ready  = False
        self._site_ok       = False
        self._identity_result: Any = None
        self._candidate_profile: Optional[dict] = None
        self._samples: list[SampleCapture] = []
        self._current: Optional[SampleCapture] = None
        self._current_type_key = "guest"
        self._worker: Optional[QThread] = None
        self._analyze_result: Optional[dict] = None

        self._fs_report: Any = None
        self._process_monitor: Any = None
        self._change_monitor: Any = None
        self._apdu_traces: list = []
        self._workflow_guest: Any = None
        self._workflow_master: Any = None
        self._workflows: dict[str, Any] = {}
        self._ui_recorder: Any = None
        self._ui_map_report: Any = None
        self._is_recording = False

        self._forensic_data: dict = {}
        self._handover_payload: Optional[dict] = None
        self._verification_result: Optional[str] = None
        self._probe_result: Optional[dict] = None
        self._handover_path: Optional[str] = None
        self._last_handover_file: Optional[str] = None

        self._welcome_shown = True
        self._readback_hex: Optional[str] = None
        self._token_collected: bool = False
        self._graduation_state: Any = None
        self._graduation_report_data: Optional[dict] = None

        self._orchestrator: Any = None
        self._panic: Any = None
        self._read_fail_count = 0
        self._proxy_log_offset = 0
        self._dll_traces: list = []
        self._oem_running = False

        self._step_coach_state: Any = None
        self._oem_phase_complete = False
        self._card_type_ready = False
        self._readback_fail_count = 0
        self._resample_requested = False

        # 升级模块惰性导入（仅 __init__ 用到）
        try:
            from ..bridgecore.experience_engine import ExperienceMatcher, FailureMemory, SectorKeyRing
        except Exception:
            ExperienceMatcher = FailureMemory = SectorKeyRing = None
        try:
            from ..bridgecore.mifare_weak_keys import WeakKeyBruteForcer
        except Exception:
            WeakKeyBruteForcer = None

        self._upgrade_ready = (
            ExperienceMatcher is not None
            and WeakKeyBruteForcer is not None
        )
        self._exp_matcher: Any = None
        self._failure_mem: Any = None
        self._key_ring: Any = None
        self._ghidra_in_progress = False
        self._upgrade_stats: dict = {}
        if self._upgrade_ready:
            self._exp_matcher = ExperienceMatcher()
            self._failure_mem = FailureMemory()
            self._key_ring = SectorKeyRing()

        self._current_task: Optional[dict] = None

        # S2: Worker 线程互斥锁
        self._worker_lock = threading.Lock()

        self._build_ui()
        self._refresh_all()
        QTimer.singleShot(100, self._run_environment_check)

    # ══════════════════════════════════════════════════════
    #  UI 构建
    # ══════════════════════════════════════════════════════

    def _build_ui(self):
        self._stack = QStackedWidget()
        self._stack.setFrameShape(QStackedWidget.Shape.NoFrame)

        # ── 欢迎页 ──
        welcome = QWidget()
        welcome.setStyleSheet(f"background:{C_BG};")
        wl = QVBoxLayout(welcome)
        wl.setAlignment(Qt.AlignCenter)
        wl.setSpacing(20)
        wl.setContentsMargins(40, 40, 40, 60)

        try:
            from ..brand_assets import make_brand_mark_label
            icon = make_brand_mark_label(64, object_name="CollectorWelcomeMark")
            wl.addWidget(icon)
        except Exception:
            pass

        wt = QLabel("Solid 学习助手 【新UI两栏】")
        wt.setStyleSheet(f"font-size:28px; font-weight:800; color:{C_TEXT};")
        wt.setAlignment(Qt.AlignCenter)
        wl.addWidget(wt)

        wd = QLabel(
            "自动分析酒店的旧门锁系统，学习它的发卡协议。\n\n"
            "你只需三件事：\n"
            "  ① 选原厂目录 — 扫描识别门锁系统\n"
            "  ② 放空白卡读一次 — 去原厂软件写卡 — 回来读已写\n"
            "  ③ 点分析 — 自动生成 .solidhandover 握手包\n\n"
            "带回 Solid PMS「厂家控制台 → 门锁 → 导入握手包」即可。"
        )
        wd.setWordWrap(True)
        wd.setAlignment(Qt.AlignCenter)
        wd.setStyleSheet(f"color:{C_MUTED}; font-size:15px; line-height:1.6;")
        wl.addWidget(wd)

        start_btn = QPushButton("🚀 开始使用")
        start_btn.setStyleSheet(
            "QPushButton {"
            f"  background-color: {C_PRIMARY}; color: white; border: none;"
            "  border-radius: 12px; padding: 16px 48px; font-size: 18px; font-weight: 700;"
            "}"
            f"QPushButton:hover {{ background-color: {PALETTE['primary_hover']}; }}"
        )
        start_btn.setCursor(Qt.PointingHandCursor)
        start_btn.setFixedHeight(56)
        start_btn.clicked.connect(self._on_start)
        wl.addWidget(start_btn, 0, Qt.AlignCenter)

        ver = QLabel("SolidCollector v2.1 · 三栏v3")
        ver.setStyleSheet("color:#94A3B8; font-size:11px;")
        ver.setAlignment(Qt.AlignCenter)
        wl.addWidget(ver)
        self._stack.addWidget(welcome)

        # ── 工作区 ──
        work_page = QWidget()
        work_page.setStyleSheet(f"background:{C_BG};")
        work_layout = QVBoxLayout(work_page)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_layout.setSpacing(0)

        # 教练条
        self._coach_bar = StepCoachBar()
        work_layout.addWidget(self._coach_bar)

        # 三栏布局
        splitter = QSplitter(Qt.Horizontal)  # [FIX] 原版缺 QSplitter 导入
        splitter.setObjectName("DashboardSplitter")
        splitter.setHandleWidth(3)
        splitter.setChildrenCollapsible(False)

        # ═══ 左栏 ═══
        left_panel = QFrame()
        left_panel.setObjectName("LeftPanel")
        left_panel.setFixedWidth(320)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(8)
        left_layout.setContentsMargins(14, 14, 10, 14)

        self._graduation_panel = GraduationPanel()
        left_layout.addWidget(self._graduation_panel)

        # 实时状态
        stats_gb = QGroupBox("实时状态")
        stats_gb.setObjectName("FdGhost")
        stats_l = QVBoxLayout(stats_gb)
        stats_l.setSpacing(4)
        self._stats_samples = QLabel("样本: 0")
        self._stats_samples.setStyleSheet(f"color:{C_MUTED}; font-size:12px;")
        stats_l.addWidget(self._stats_samples)
        self._stats_bridge = QLabel("桥接: 未连接")
        self._stats_bridge.setStyleSheet(f"color:{C_MUTED}; font-size:12px;")
        stats_l.addWidget(self._stats_bridge)
        self._stats_brand = QLabel("品牌: —")
        self._stats_brand.setStyleSheet(f"color:{C_MUTED}; font-size:12px;")
        stats_l.addWidget(self._stats_brand)
        self._stats_mode = QLabel("发卡: —")
        self._stats_mode.setStyleSheet(f"color:{C_MUTED}; font-size:12px;")
        stats_l.addWidget(self._stats_mode)
        left_layout.addWidget(stats_gb)
        left_layout.addStretch()

        # 快捷操作
        shortcuts_l = QVBoxLayout()
        shortcuts_l.setSpacing(6)
        self._resample_btn = QPushButton("再采一组")
        self._resample_btn.setObjectName("SolidSecondaryBtn")
        self._resample_btn.setMinimumHeight(42)
        self._resample_btn.clicked.connect(self._on_resample)
        self._resample_btn.setEnabled(False)
        shortcuts_l.addWidget(self._resample_btn)
        self._clear_btn = QPushButton("全部清空")
        self._clear_btn.setObjectName("FdGhostBtn")
        self._clear_btn.setMinimumHeight(42)
        self._clear_btn.clicked.connect(self._on_clear_all)
        self._clear_btn.setEnabled(False)
        shortcuts_l.addWidget(self._clear_btn)
        self._analyze_btn = QPushButton("开始分析")
        self._analyze_btn.setObjectName("SolidPrimaryBtn")
        self._analyze_btn.setMinimumHeight(48)
        self._analyze_btn.clicked.connect(self._on_analyze)
        self._analyze_btn.setEnabled(False)
        shortcuts_l.addWidget(self._analyze_btn)
        close_btn = QPushButton("退出")
        close_btn.setObjectName("FdGhostBtn")
        close_btn.setMinimumHeight(42)
        close_btn.clicked.connect(self.accept)
        shortcuts_l.addWidget(close_btn)
        left_layout.addLayout(shortcuts_l)

        splitter.addWidget(left_panel)

        # ═══ 中栏 ═══
        center_scroll = QScrollArea()
        center_scroll.setWidgetResizable(True)
        center_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        center_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        center_scroll.setObjectName("CenterPanel")
        self._work_scroll = center_scroll

        inner = QWidget()
        inner.setStyleSheet(f"background:{C_BG};")
        vbox = QVBoxLayout(inner)
        vbox.setSpacing(10)
        vbox.setContentsMargins(14, 14, 10, 14)

        # 标题
        t = QLabel("Solid 学习助手 【新UI两栏】")
        t.setStyleSheet(f"font-size:20px; font-weight:700; color:{C_TEXT};")
        vbox.addWidget(t)
        d = QLabel(
            "自动分析门锁系统，学习发卡协议，生成 PMS 握手包。\n"
            "操作只需三步：①选原厂目录扫描 → ②读空白卡 + 去原厂写卡 + 回来读已写 → ③点分析。"
        )
        d.setWordWrap(True)
        d.setStyleSheet(f"color:{C_MUTED}; font-size:13px;")
        vbox.addWidget(d)

        # 识别面板
        self._identify_panel = IdentifyPanel()
        self._identify_panel.scan_requested.connect(self._on_detect)
        self._identify_panel.browse_requested.connect(self._browse)
        self._identify_panel.task_fetch_requested.connect(self._on_fetch_tasks)
        self._identify_panel.task_selected.connect(self._on_task_selected)
        if self._install_dir:
            self._identify_panel.set_install_dir(self._install_dir)
        vbox.addWidget(self._identify_panel)

        # 采样面板
        self._sample_panel = SamplePanel()
        self._sample_panel.card_type_changed.connect(self._on_card_type_changed)
        self._sample_panel.read_blank_requested.connect(self._on_read_blank)
        self._sample_panel.read_written_requested.connect(self._on_read_written)
        self._sample_panel.add_sample_requested.connect(self._on_add_sample)
        self._sample_panel.launch_oem_requested.connect(self._on_launch_oem)
        self._sample_panel.toggle_recording.connect(self._on_toggle_recording)
        self._sample_panel.erase_requested.connect(self._on_erase)
        vbox.addWidget(self._sample_panel)

        # 进度条
        self._pb = QProgressBar()
        self._pb.setVisible(False)
        self._pb.setMinimumHeight(6)
        vbox.addWidget(self._pb)

        # 日志
        lg = QGroupBox("日志")
        lg.setObjectName("FdGhost")
        ll = QVBoxLayout(lg)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(100)
        self._log.setMaximumHeight(140)
        self._log.setAcceptRichText(True)
        self._log.setStyleSheet(
            f"font-family:Consolas,monospace; font-size:12px; background:{C_BG_ALT}; "
            f"color:{C_TEXT}; border:1px solid {PALETTE['border']}; border-radius:6px;")
        ll.addWidget(self._log)
        vbox.addWidget(lg)

        # 分析结果
        self._result = QLabel("")
        self._result.setWordWrap(True)
        self._result.setVisible(False)
        vbox.addWidget(self._result)

        # 法医摘要
        self._forensic_summary = QLabel("")
        self._forensic_summary.setWordWrap(True)
        self._forensic_summary.setVisible(False)
        vbox.addWidget(self._forensic_summary)

        # 探测面板
        self._probe_gb = QGroupBox("④ 发卡方式（分析后自动探测）")
        self._probe_gb.setObjectName("FdGhost")
        self._probe_gb.setVisible(False)
        pv = QVBoxLayout(self._probe_gb)
        pv.setSpacing(6)
        self._probe_btn = QPushButton("开始探测")
        self._probe_btn.setObjectName("SolidPrimaryBtn")
        self._probe_btn.clicked.connect(self._on_probe)
        pv.addWidget(self._probe_btn)
        self._probe_status = QLabel("分析完成后会自动探测，无需手动点击")
        self._probe_status.setWordWrap(True)
        self._probe_status.setStyleSheet(f"color:{C_MUTED}; font-size:13px;")
        pv.addWidget(self._probe_status)
        self._probe_detail = QLabel("")
        self._probe_detail.setWordWrap(True)
        self._probe_detail.setVisible(False)
        pv.addWidget(self._probe_detail)
        self._reprobe_btn = QPushButton("重新探测")
        self._reprobe_btn.setObjectName("FdGhostBtn")
        self._reprobe_btn.clicked.connect(self._on_reprobe_upgrade)
        self._reprobe_btn.setVisible(False)
        pv.addWidget(self._reprobe_btn)
        vbox.addWidget(self._probe_gb)

        # 握手包面板
        self._handover_panel = HandoverPanel()
        self._handover_panel.build_requested.connect(self._on_handover_build)
        self._handover_panel.select_path_requested.connect(self._on_handover_select_path)
        self._handover_panel.copy_to_usb_requested.connect(self._on_handover_copy_usb)
        self._handover_panel.setVisible(False)
        vbox.addWidget(self._handover_panel)

        center_scroll.setWidget(inner)
        splitter.addWidget(center_scroll)
        work_layout.addWidget(splitter, 1)

        # 底栏
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet(
            f"background:{C_BG}; border-top:1px solid {PALETTE['border']}; padding:12px 20px;")
        bot = QHBoxLayout(bottom_bar)
        self._verify_destructive_cb = QCheckBox("此卡可作废（允许写测试卡验证协议）")
        self._verify_destructive_cb.setStyleSheet(f"color:{C_MUTED}; font-size:12px;")
        self._verify_destructive_cb.setVisible(False)
        bot.addWidget(self._verify_destructive_cb)
        bot.addStretch()
        work_layout.addWidget(bottom_bar)

        self._stack.addWidget(work_page)

        # 根布局
        rl = QVBoxLayout(self)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._stack)

        # 连接毕业面板信号
        self._graduation_panel.readback_requested.connect(self._on_readback_for_graduation)
        self._graduation_panel.token_collection_requested.connect(self._on_token_collection)

    # ══════════════════════════════════════════════════════
    #  欢迎页 → 工作区
    # ══════════════════════════════════════════════════════

    def _on_start(self):
        self._welcome_shown = False
        self._stack.setCurrentIndex(1)
        self._refresh_all()

    # ══════════════════════════════════════════════════════
    #  日志 + 环境检查
    # ══════════════════════════════════════════════════════

    def _log_msg(self, msg: str):
        color = ""
        if msg.startswith("✅") or msg.startswith("完成") or msg.startswith("成功"):
            color = C_GREEN
        elif msg.startswith("❌") or msg.startswith("失败") or msg.startswith("错误"):
            color = C_DANGER
        elif msg.startswith("⚠") or msg.startswith("跳过"):
            color = C_WARN
        elif msg.startswith("▶"):
            color = C_PRIMARY
        if color:
            self._log.append('<span style="color:%s;">%s</span>' % (color, msg))
        else:
            self._log.append(msg)
        s = self._log.verticalScrollBar()
        s.setValue(s.maximum())
        # D1: 写入文件日志（延迟刷盘，防 Windows U 盘卡滞）
        try:
            if not hasattr(self, "_log_file"):
                log_dir = collector_work_dir() / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                self._log_file = open(str(log_dir / "collector.log"), "a", encoding="utf-8")
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._log_file.write(f"{ts} {msg}\n")
        except Exception:
            pass

    def _run_environment_check(self):
        results = []
        bridge_paths = [
            os.path.join(os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                       else __file__), "bridge32.exe"),
            os.path.join(os.path.dirname(__file__), "bridge32.exe"),
        ]
        bridge_found = any(os.path.isfile(p) for p in bridge_paths)
        results.append(("bridge32.exe", bridge_found, "桥接子进程" if bridge_found else "缺失"))
        java_found = any(shutil.which(j) for j in ["java", "java.exe"])
        results.append(("Java", java_found, "Ghidra 分析依赖"))
        ghidra_found = os.path.isdir(os.path.join(os.path.dirname(__file__), "ghidra"))
        results.append(("Ghidra", ghidra_found, "深度反编译" if ghidra_found else "降级到 strings"))
        try:
            disk_usage = shutil.disk_usage(os.getcwd())
            free_gb = disk_usage.free / (1024 ** 3)
            results.append(("磁盘空间", free_gb > 0.1, f"{free_gb:.1f} GB 可用"))
        except Exception:
            results.append(("磁盘空间", True, "未知"))
        lines = ["🔍 环境自检:"]
        for name, ok, note in results:
            lines.append(f"  {'✓' if ok else '✗'} {name}: {note}")
        for line in lines:
            self._log_msg(line)

    # ══════════════════════════════════════════════════════
    #  事件：浏览 / 卡型切换
    # ══════════════════════════════════════════════════════

    def _browse(self):
        f = QFileDialog.getExistingDirectory(self, "选择原厂门锁软件目录")
        if f:
            self._identify_panel.set_install_dir(f)

    def _on_card_type_changed(self, text: str):
        # [FIX] 原版用 CARD_FIELDS.get(text, "guest") 返回 set/str 混合
        self._current_type_key = CARD_KEY_MAP.get(text, "guest")
        self._current = SampleCapture(text)
        self._card_type_ready = True
        sp = self._sample_panel
        sp.rb_btn.setEnabled(True)
        sp.rb_btn.setText("读空白卡（采样本）")
        sp.rw_btn.setEnabled(False)
        sp.add_btn.setVisible(False)
        sp.launch_btn.setVisible(False)
        sp.record_btn.setVisible(False)
        sp.payload_label.setText("")
        n = len(self._samples)
        sp.count_label.setText(f"已采集 {n} 个样本")
        sp.set_tip("①放空白卡到发卡器 → ②点「读空白卡」→ ③拿去原厂软件写这张卡 → ④放回来点「读已写卡」")
        self._refresh_all()

    # ══════════════════════════════════════════════════════
    #  Step 0: 识别 / 扫描
    # ══════════════════════════════════════════════════════

    def _on_detect(self, install_dir: str):
        if not install_dir or not os.path.isdir(install_dir):
            self._sample_panel.set_tip("目录不存在，请重新选择", "error")
            return
        self._install_dir = install_dir
        self._identify_panel.set_scan_loading()
        self._log_msg(f"开始扫描: {install_dir}")
        self._start_forensic_monitors()
        w = DetectWorker(install_dir, parent=self)
        w.done.connect(self._on_detect_result)
        w.start()
        with self._worker_lock:
            self._worker = w

    def _start_forensic_monitors(self):
        try:
            from ..process_monitor import ProcessMonitor
            self._process_monitor = ProcessMonitor()
            self._process_monitor.snapshot("before")
        except Exception as e:
            self._log_msg(f"进程监控启动失败: {e}")
        try:
            from ..change_monitor import ChangeMonitor
            self._change_monitor = ChangeMonitor(self._install_dir)
            self._change_monitor.snapshot("before")
        except Exception as e:
            self._log_msg(f"变化监控启动失败: {e}")

    def _on_detect_result(self, identity: Any):
        self._identify_panel.set_scan_done()
        if not identity or not getattr(identity, "site_ok", False):
            self._identify_panel.set_identity_summary(
                f"检测失败: {identity}" if not identity else str(identity.summary_title),
                ok=False)
            self._site_ok = False
            self._bridge_ready = False
            return

        self._identity_result = identity
        self._site_ok = identity.site_ok
        self._bridge_ready = identity.bridge_ok
        self._loaded_dll = identity.main_dll or ""
        if identity.candidate_profile:
            self._candidate_profile = identity.candidate_profile
        if identity.fs_report is not None:
            self._fs_report = identity.fs_report

        # 身份摘要
        site_lines = [identity.summary_title]
        if identity.install_dir:
            site_lines.append(f"目录: {identity.install_dir}")
        if identity.main_dll:
            site_lines.append(f"主 DLL: {identity.main_dll}（置信 {identity.confidence:.2f}）")
        self._identify_panel.set_identity_summary("\n".join(site_lines), ok=identity.site_ok)

        # 桥接状态
        if identity.bridge_ok:
            serial_ports = getattr(identity, "serial_responsive", [])
            if serial_ports:
                ports_desc = ", ".join(f"{p.port}@{p.baudrate}" for p in serial_ports)
                self._identify_panel.set_bridge_status(f"✅ 串口发卡器就绪: {ports_desc}", "ok")
            else:
                self._identify_panel.set_bridge_status(f"✅ USB 发卡器就绪 · DLL: {identity.main_dll}", "ok")

            # 初始化 orchestrator
            if not serial_ports:
                try:
                    from ..bridgecore.orchestrator import BridgeCoreOrchestrator
                    from ..bridgecore.panic_recovery import PanicRecovery
                    from ..collector_bridge import get_bridge
                    bridge = get_bridge()
                    self._panic = PanicRecovery(bridge)
                    self._orchestrator = BridgeCoreOrchestrator(
                        bridge, recording_dir=collector_work_dir() / "recordings",
                        panic_recovery=self._panic)
                except Exception:
                    pass
        elif "oem_running" in identity.blockers:
            self._identify_panel.set_bridge_status(
                "⚠ " + (identity.bridge_hint or "请先关闭原厂门锁软件"), "warn")
        else:
            self._identify_panel.set_bridge_status(
                "❌ " + (identity.bridge_hint or "发卡器未连接"), "error")

        if identity.site_ok:
            self._identify_panel.set_completed()
            self._try_restore_autosave()
            self._on_card_type_changed(self._sample_panel.card_type_combo.currentText())

        self._sample_panel.rb_btn.setEnabled(self._site_ok)
        self._refresh_all()

    # ══════════════════════════════════════════════════════
    #  Step 1: 读卡
    # ══════════════════════════════════════════════════════

    def _guard_bridge_for_read(self) -> bool:
        profile = self._candidate_profile or {}
        if profile.get("channel") == "serial":
            return True
        if self._oem_running:
            self._sample_panel.set_tip(
                "原厂软件正在占用发卡器。请先关闭 CardLock.exe，完成写卡后再回到本工具读卡。", "warn")
            return False
        if self._bridge_ready:
            return True
        self._sample_panel.set_tip(
            "发卡器未就绪：请先关闭原厂软件，点「重新扫描」确认发卡器就绪后再读卡", "warn")
        return False

    def _make_read_worker(self, session_tag: str = "read_card") -> Optional[ReadCardWorker]:
        if not self._guard_bridge_for_read():
            return None
        return ReadCardWorker(
            self._candidate_profile,
            orchestrator=self._orchestrator,
            session_tag=session_tag,
        )

    def _on_read_blank(self):
        w = self._make_read_worker("read_blank")
        if w is None:
            return
        sp = self._sample_panel
        sp.rb_btn.setEnabled(False)
        sp.rb_btn.setText("读取中...")
        sp.set_tip("正在读取空白卡...")
        w.done.connect(self._on_blank_result)
        w.start()
        with self._worker_lock:
            self._worker = w

    def _on_blank_result(self, ok: bool, msg: str):
        sp = self._sample_panel
        sp.rb_btn.setText("读空白卡（采样本）")
        if ok:
            self._current.blank_hex = msg
            self._oem_phase_complete = False
            sp.rb_btn.setEnabled(False)
            sp.rb_btn.setText("空白卡已读 ✓")
            sp.rw_btn.setEnabled(True)
            sp.payload_label.setText(f"空白卡数据: {trunc(msg)}")
            self._log_msg(f"空白卡已读取: {trunc(msg)}")

            cardlock_exe = self._find_cardlock_exe()
            if cardlock_exe:
                sp.launch_btn.setVisible(True)
                sp.launch_btn.setEnabled(True)
                sp.set_tip(
                    f"空白卡已读! 请切换到原厂门锁软件发「{self._current.card_type}」。\n"
                    "发完后回到本工具，点「读已写卡（采样本）」。")

            if self._process_monitor:
                self._process_monitor.snapshot("during")
            sp.set_monitor_status("后台监控已启动: 进程树 + 文件变化 + 注册表变化")
        else:
            sp.rb_btn.setEnabled(True)
            self._handle_read_failure(msg)
            sp.set_tip(f"读卡失败: {msg}", "error")
        self._refresh_all()

    def _on_read_written(self):
        self._oem_phase_complete = True
        self._oem_running = False
        w = self._make_read_worker("read_written")
        if w is None:
            return
        sp = self._sample_panel
        sp.rw_btn.setEnabled(False)
        sp.rw_btn.setText("读取中...")
        sp.set_tip("正在读取已写卡...")
        w.done.connect(self._on_written_result)
        w.start()
        with self._worker_lock:
            self._worker = w

    def _on_written_result(self, ok: bool, msg: str):
        sp = self._sample_panel
        sp.rw_btn.setText("读已写卡（采样本）")
        if ok:
            if msg == self._current.blank_hex:
                sp.rw_btn.setEnabled(True)
                sp.erase_btn.setVisible(True) if sp.erase_btn else None
                sp.set_tip("卡片数据没变，可能没写成功。再去原厂发卡，或擦卡重试。", "warn")
                self._refresh_all()
                return
            self._current.written_hex = msg
            self._read_fail_count = 0
            self._ingest_proxy_log_delta()
            sp.rw_btn.setEnabled(False)
            sp.rw_btn.setText("已写卡已读 ✓")
            sp.payload_label.setText(f"已写卡数据: {trunc(msg)}")
            self._log_msg(f"已写卡读取成功({self._current.card_type}): {trunc(msg)}")

            # 采集额外字段
            field_set = CARD_FIELDS.get(self._current.card_type, set())
            if "room" in field_set:
                self._current.room = sp.room_input.text().strip()
            if "b_date" in field_set:
                self._current.b_date = sp.bd_input.date().toString("yyMMdd")
                self._current.e_date = sp.ed_input.date().toString("yyMMdd")
            if "building_no" in field_set:
                self._current.building_no = sp.building_no_input.value()
            if "floor_no" in field_set:
                self._current.floor_no = sp.floor_no_input.value()
            if "group_no" in field_set:
                self._current.group_no = sp.group_no_input.value()

            sp.add_btn.setVisible(True)
            sp.launch_btn.setVisible(False)
            sp.record_btn.setVisible(False)
            sp.recording_status.setVisible(False)
            sp.set_tip("对照样本已就绪! 请点「添加样本」。\n（擦卡为选做，首次请跳过）")

            if self._is_recording:
                try:
                    wf = self._ui_recorder.stop_recording()
                    ck = self._current.card_type_key
                    if ck:
                        self._store_workflow(ck, wf)
                except Exception as e:
                    self._log_msg(f"停止录制失败: {e}")
                self._is_recording = False
        else:
            sp.rw_btn.setEnabled(True)
            self._handle_read_failure(msg)
            sp.set_tip(f"读卡失败: {msg}", "error")
        self._refresh_all()

    def _handle_read_failure(self, msg: str):
        self._read_fail_count += 1
        if self._read_fail_count >= 3 and self._panic is not None:
            try:
                self._panic.execute(level=1, triggered_by=msg)
                self._read_fail_count = 0
                self._log_msg("读卡连续失败，已执行发卡器软复位")
            except Exception as e:
                self._log_msg(f"软复位失败: {e}")

    # ══════════════════════════════════════════════════════
    #  添加样本
    # ══════════════════════════════════════════════════════

    def _on_add_sample(self):
        self._current.done = True
        self._samples.append(self._current)
        self._log_msg(f"样本已保存: {self._current.card_type} ({len(self._samples)})")
        self._autosave_samples()

        self._oem_phase_complete = False
        sp = self._sample_panel
        sp.reset_read_buttons()

        collected_cts = {s.card_type for s in self._samples}
        sp.set_tip(
            f"样本已保存! 当前 {len(self._samples)} 个样本。已采卡型: {', '.join(collected_cts)}\n\n"
            "点左栏「开始分析」即可开始协议学习。\n如需更稳妥，可再采一张其他卡型。")

        n = len(self._samples)
        sp.count_label.setText(f"已采集 {n} 个样本")
        sp.update_progress_grid({s.card_type_key for s in self._samples})

        self._current = SampleCapture(sp.card_type_combo.currentText())
        self._refresh_all()

    # ══════════════════════════════════════════════════════
    #  分析
    # ══════════════════════════════════════════════════════

    def _on_analyze(self):
        if not self._samples:
            self._sample_panel.set_tip("请先至少采集 1 组对照样本（空白卡 + 已写卡）", "warn")
            return
        self._analyze_btn.setEnabled(False)
        self._analyze_btn.setText("分析中...")
        self._pb.setVisible(True)
        self._pb.setValue(0)
        self._log.clear()
        self._result.setVisible(False)
        self._forensic_summary.setVisible(False)

        forensic_data = self._collect_forensic_data()
        samples_dict = [s.to_dict() for s in self._samples]
        blank_hex = next((s.blank_hex for s in self._samples if s.blank_hex), "")
        recording_dir = str(self._orchestrator.recording_dir) if self._orchestrator else ""

        w = AnalyzeWorker(
            samples_dict, self._install_dir, self._loaded_dll,
            candidate_profile=self._candidate_profile,
            forensic_data=forensic_data,
            allow_destructive_verify=self._verify_destructive_cb.isChecked(),
            blank_hex=blank_hex,
            recording_dir=recording_dir,
            parent=self,
        )
        w.log.connect(self._log_msg)
        w.progress.connect(self._pb.setValue)
        w.done.connect(self._on_analyze_done)
        w.start()
        with self._worker_lock:
            self._worker = w

    def _collect_forensic_data(self) -> dict:
        data = {}
        if self._fs_report:
            data["filesystem"] = self._fs_report
            if self._fs_report.system_ini:
                data["system_ini"] = self._fs_report.system_ini
        try:
            if self._process_monitor:
                self._process_monitor.snapshot("after")
                data["process_tree"] = self._process_monitor.diff()
        except Exception:
            pass
        try:
            if self._change_monitor:
                self._change_monitor.snapshot("after")
                fr, rr = self._change_monitor.diff()
                data["file_changes"] = fr
                data["registry_changes"] = rr
        except Exception:
            pass
        try:
            from ..apdu_sniffer import get_sniffer
            sniffer = get_sniffer()
            if sniffer.active:
                data["apdu_traces"] = sniffer.stop()
        except Exception:
            pass
        if self._ui_map_report:
            data["ui_map"] = self._ui_map_report
        if self._dll_traces:
            data["dll_traces"] = list(self._dll_traces)
        self._forensic_data = data
        return data

    def _on_analyze_done(self, result: dict):
        self._pb.setValue(100)
        if result.get("success"):
            self._result.setStyleSheet(
                "font-size:14px; padding:12px; background:#ECFDF5; "
                "border:1px solid #6EE7B7; border-radius:10px;")
            ct = ", ".join(result.get("card_types", []))
            cf = "%.0f%%" % (result.get("confidence", 0) * 100)
            self._result.setText(
                f"✅ 分析完成\n识别卡型: {ct}\n置信度: {cf} · 品牌: {result.get('profile', {}).get('brand', '?')}\n\n"
                "正在自动探测发卡方式并生成握手包，请稍候…")
            self._result.setVisible(True)
            self._analyze_result = result

            # B1: 经验自学习 → 分析成功后写入经验库
            profile = result.get("profile", {}) or {}
            dll_path = self._loaded_dll
            brand = profile.get("brand", "")
            if self._upgrade_ready and self._exp_matcher and dll_path and brand and profile:
                try:
                    self._exp_matcher.save_experience(dll_path, profile, brand)
                except Exception:
                    pass

            # 同步 probe_meta（Ghidra 先于 analyze 完成时合并）
            cp_meta = (self._candidate_profile or {}).get("probe_meta", {})
            if cp_meta:
                self._analyze_result.setdefault("probe_meta", {}).update(cp_meta)

            self._on_probe()
        else:
            self._result.setStyleSheet(
                "font-size:14px; padding:12px; background:#FEF2F2; "
                "border:1px solid #FCA5A5; border-radius:10px;")
            error_msg = result.get('error', '?')
            tips = [f"分析失败: {error_msg}"]

            # D2: 加密卡检测提示
            if result.get("encrypted_suspected"):
                tips.append("此门锁系统可能使用加密卡，建议走寄生模式（PMS 控制原厂软件写卡）")
            elif not error_msg.startswith("未能识别"):
                # 样本不足提示
                n_samples = len(self._samples)
                if n_samples < 3:
                    tips.append(f"当前仅 {n_samples} 组样本，建议至少采集 3 组对照样本")
                tips.append("可点左栏「再采一组」补充其他卡型")

            self._result.setText("\n".join(tips))
            self._result.setVisible(True)
            # D2: 一键重采按钮启用
            self._resample_btn.setEnabled(True)
            self._log_msg("分析未通过，请按提示补采样本后重试")
        self._analyze_btn.setEnabled(True)
        self._analyze_btn.setText("重新分析")
        self._refresh_all()

    # ══════════════════════════════════════════════════════
    #  探测
    # ══════════════════════════════════════════════════════

    def _on_probe(self):
        if not self._install_dir:
            return
        self._probe_btn.setEnabled(False)
        self._probe_btn.setText("探测中...")
        profile = (self._analyze_result or {}).get("profile", {})
        w = ProbeWorker(self._install_dir, profile, self._identity_hint_dict(), self)
        w.done.connect(self._on_probe_done)
        w.start()
        with self._worker_lock:
            self._worker = w

    def _identity_hint_dict(self) -> dict:
        ident = self._identity_result
        if not ident:
            return {}
        return {
            "main_dll": getattr(ident, "main_dll", "") or "",
            "dll_confidence": float(getattr(ident, "confidence", 0) or 0),
        }

    def _on_probe_done(self, ok: bool, mode: str, detail: dict):
        self._probe_btn.setEnabled(False)
        self._probe_btn.setText("探测完成")
        self._reprobe_btn.setVisible(True)

        if ok and mode != "failed":
            labels = {
                "dll_direct": "DLL 直调（PMS 自己发卡）",
                "parasitic": "寄生原厂软件（PMS 控制 CardLock）",
                "serial": "串口发卡器（COM 直连）",
            }
            self._probe_status.setText(f"✅ {labels.get(mode, mode)}")
            self._probe_status.setStyleSheet(
                f"color:{C_GREEN}; font-size:13px; font-weight:600;")
            self._probe_result = {"mode": mode, "detail": detail}
            self._handover_panel.setVisible(True)
            self._update_handover_preview()
            if self._graduation_state and self._graduation_state.can_graduate:
                self._auto_build_handover()
        else:
            self._probe_result = {"mode": "failed", "detail": detail}
            self._probe_status.setText(f"❌ 探测失败: {mode}")
            self._probe_status.setStyleSheet(f"color:{C_DANGER}; font-size:13px;")
        self._refresh_all()

    # ══════════════════════════════════════════════════════
    #  握手包
    # ══════════════════════════════════════════════════════

    def _update_handover_preview(self):
        if not self._analyze_result or not self._probe_result:
            return
        result = self._analyze_result
        profile = result.get("profile", {})
        card_types = ", ".join(profile.get("card_types", {}).keys())
        mode = self._probe_result["mode"]
        self._handover_panel.set_preview(
            f"📦 包内容预览\n"
            f"  品牌: {profile.get('brand', '?')}\n"
            f"  已学卡型: {card_types}\n"
            f"  发卡方式: {'DLL直调' if mode == 'dll_direct' else '寄生原厂软件'}")

    def _auto_build_handover(self):
        if not self._analyze_result or not self._probe_result:
            return
        if self._probe_result.get("mode") == "failed":
            return
        profiles_dir = collector_work_dir() / "learned_profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        self._handover_path = str(profiles_dir)
        self._handover_panel.set_path_label(f"默认保存到: {profiles_dir}")
        self._do_build_handover()

    def _on_handover_build(self):
        if not self._graduation_state or not self._graduation_state.can_graduate:
            self._sample_panel.set_tip("请先完成毕业（含核对读数）后再生成握手包。", "warn")
            return
        target_dir = self._handover_path or ""
        # S1: 用实际写测试替代不可靠的 os.access
        if target_dir:
            test_file = os.path.join(target_dir, ".write_test_solid")
            try:
                with open(test_file, 'w') as tmp:
                    tmp.write('ok')
                os.remove(test_file)
            except (IOError, OSError):
                desktop = os.path.join(os.path.expanduser("~"), "Desktop")
                self._handover_panel.set_usb_readonly_error(target_dir, desktop)
                return
        self._do_build_handover()

    def _do_build_handover(self):
        payload = self._get_handover_payload()
        mode = self._probe_result["mode"]
        hotel_name = self._handover_panel.hotel_name_input.text().strip()
        grad_report = self._graduation_report_data
        if self._graduation_state and not grad_report:
            try:
                from ..bridgecore.graduation_coach import build_graduation_report
                grad_report = build_graduation_report(
                    self._graduation_state,
                    identity=self._identity_result,
                    analyze_result=self._analyze_result,
                    probe_result=self._probe_result,
                    readback_hex=self._readback_hex,
                    sample_count=len(self._samples),
                )
                self._graduation_report_data = grad_report
            except Exception:
                pass

        self._handover_panel.set_build_loading()
        w = BuildWorker(payload, mode, self._install_dir,
                        self._handover_path or "", hotel_name, self,
                        graduation_report=grad_report)
        w.progress.connect(self._handover_panel.set_progress)
        w.done.connect(self._on_handover_build_done)
        w.start()
        with self._worker_lock:
            self._worker = w

    def _on_handover_build_done(self, ok: bool, msg: str):
        self._handover_panel.set_build_done(ok, msg)
        if ok:
            parts = msg.split("\n", 2)
            self._last_handover_file = parts[0]
            self._log_msg(f"握手包已生成: {parts[0]}")
            self._maybe_submit_task_result(parts[0])
        else:
            self._log_msg(f"握手包生成失败: {msg}")

    def _on_handover_select_path(self):
        dlg = QFileDialog.getExistingDirectory(self, "选择握手包保存位置")
        if dlg:
            self._handover_path = dlg
            self._handover_panel.set_path_label(dlg)

    def _on_handover_copy_usb(self):
        if not self._last_handover_file:
            return
        dst_dir = QFileDialog.getExistingDirectory(self, "选择 U 盘目录")
        if not dst_dir:
            return
        try:
            dst = os.path.join(dst_dir, os.path.basename(self._last_handover_file))
            shutil.copy2(self._last_handover_file, dst)
            self._handover_panel.append_copy_info(dst_dir)
            self._log_msg(f"握手包已复制到: {dst}")
        except Exception as e:
            self._log_msg(f"复制失败: {e}")

    def _get_handover_payload(self) -> dict:
        from ..bridgecore.handover_assembler import assemble_handover_payload
        from ..bridgecore.field_checklist import build_field_checklist

        cardlock_exe = self._find_cardlock_exe() or ""
        if self._probe_result:
            pd = self._probe_result.get("detail", {}).get("parasitic", {})
            if pd.get("cardlock_exe"):
                cardlock_exe = pd["cardlock_exe"]

        payload = assemble_handover_payload(
            self._analyze_result or {}, self._install_dir,
            loaded_dll=self._loaded_dll, forensic=self._forensic_data,
            ui_map=self._ui_map_report,
            workflow_guest=self._workflow_guest,
            workflow_master=self._workflow_master,
            workflows_by_type=self._workflows or None,
            cardlock_exe=cardlock_exe,
            hotel_name=self._handover_panel.hotel_name_input.text().strip(),
        )
        payload["dll_traces"] = list(self._dll_traces)
        payload["evidence_level"] = (self._analyze_result or {}).get("evidence_level", "hex_only")
        payload["field_checklist"] = build_field_checklist(
            graduation_state=self._graduation_state,
            analyze_result=self._analyze_result,
            probe_result=self._probe_result,
            samples=[s.to_dict() for s in self._samples] if self._samples else [],
        )
        if self._token_collected:
            payload["token_matrix"] = {"collected": True}
        self._handover_payload = payload
        return payload

    # ══════════════════════════════════════════════════════
    #  毕业验证
    # ══════════════════════════════════════════════════════

    def _on_readback_for_graduation(self):
        w = self._make_read_worker("readback_grad")
        if w is None:
            self._graduation_panel.set_readback_result(False, "发卡器未就绪")
            return
        self._graduation_panel.set_readback_loading()
        w.done.connect(self._on_readback_done)
        w.start()
        with self._worker_lock:
            self._worker = w

    def _on_readback_done(self, ok: bool, msg: str):
        if ok:
            self._readback_fail_count = 0
            self._readback_hex = msg.upper().strip()
            from ..bridgecore.graduation_coach import _collect_written_hexes
            samples_dict = [s.to_dict() for s in self._samples]
            written = _collect_written_hexes(samples_dict)
            matched = self._readback_hex in [h.upper().strip() for h in written]
            self._graduation_panel.set_readback_result(
                ok=True, msg=self._readback_hex, match=matched)
            if matched:
                self._log_msg(f"核对读数通过: {self._readback_hex[:32]}")
            else:
                self._readback_fail_count += 1
                self._log_msg(f"核对读数不匹配 ({self._readback_fail_count}/3)")
                if self._readback_fail_count >= 3:
                    self._readback_hex = None
                    self._readback_fail_count = 0
        else:
            self._graduation_panel.set_readback_result(ok=False, msg=msg)
        self._refresh_all()

    def _on_token_collection(self):
        if not self._guard_bridge_for_read():
            return
        from PySide6.QtWidgets import QMessageBox
        mb = QMessageBox(self)
        mb.setWindowTitle("采集授权卡 Token")
        mb.setText("将采集 5 张授权卡 Token 差分样本。\n\n▶ 请确保发卡器已就绪，放上空卡。")
        mb.setIcon(QMessageBox.Information)
        mb.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        if mb.exec_() != QMessageBox.Ok:
            return

        self._graduation_panel.set_token_loading()
        try:
            from ..collector_bridge import get_bridge
        except ImportError:
            from collector_bridge import get_bridge
        bridge = get_bridge()

        w = TokenCollectionWorker(bridge=bridge, count=5, parent=self)
        w.log.connect(self._log_msg)
        w.progress.connect(lambda t: self._graduation_panel._token_status.setText(t))
        w.done.connect(self._on_token_collection_done)
        w.start()
        with self._worker_lock:
            self._worker = w

    def _on_token_collection_done(self, ok: bool, msg: str, path: str):
        self._token_collected = ok
        self._graduation_panel.set_token_result(ok, msg, path)
        if ok:
            self._log_msg(f"Token 采集完成: {msg}")
        else:
            self._log_msg(f"Token 采集失败: {msg}")
        self._refresh_all()

    # ══════════════════════════════════════════════════════
    #  厂家任务
    # ══════════════════════════════════════════════════════

    def _on_fetch_tasks(self):
        self._identify_panel.set_task_fetch_loading()
        hotel_id = self._handover_panel.hotel_name_input.text().strip() if self._handover_panel.isVisible() else ""
        self._task_fetch_worker = TaskFetchWorker(hotel_id)
        self._task_fetch_worker.done.connect(self._on_fetch_tasks_done)
        self._task_fetch_worker.start()

    def _on_fetch_tasks_done(self, result):
        if isinstance(result, Exception):
            self._identify_panel.set_task_fetch_error(str(result))
            return
        self._identify_panel.set_tasks(result or [])
        self._log_msg(f"ℹ 拉取到 {len(result or [])} 个任务")

    def _on_task_selected(self, task: dict):
        self._current_task = task
        if task.get("hotel_name"):
            self._handover_panel.hotel_name_input.setText(task["hotel_name"])
        task_id = task.get("task_id", "")
        if task_id:
            try:
                from ..bridgecore.task_fetcher import TaskFetcher
                TaskFetcher().ack_task(task_id)
            except Exception:
                pass

    def _maybe_submit_task_result(self, handover_path: str):
        task = self._current_task or {}
        task_id = task.get("task_id", "")
        if not task_id:
            return
        try:
            from ..bridgecore.task_fetcher import TaskFetcher
            ok = TaskFetcher().submit_result(task_id, handover_path)
            if ok:
                self._log_msg(f"✅ 任务 {task_id} 结果已回传云端")
        except Exception:
            pass

    # ══════════════════════════════════════════════════════
    #  OEM / 录制
    # ══════════════════════════════════════════════════════

    def _on_launch_oem(self):
        exe_path = self._find_cardlock_exe()
        if not exe_path:
            self._sample_panel.set_tip("未找到 CardLock.exe，请手动启动原厂软件", "warn")
            return
        sp = self._sample_panel
        sp.launch_btn.setEnabled(False)
        sp.launch_btn.setText("启动中...")
        self._oem_running = True
        try:
            from ..ui_workflow import UIRecorder
            self._ui_recorder = UIRecorder(exe_path)
            ok = self._ui_recorder.start(timeout=30.0)
            if ok:
                self._ui_map_report = self._ui_recorder.capture_ui_map()
                sp.launch_btn.setText("原厂已启动 ✓")
                sp.record_btn.setVisible(True)
                sp.record_btn.setEnabled(True)
                sp.set_tip(f"原厂软件已启动! 在原厂软件中操作发「{self._current.card_type}」，\n可点「开始录制操作」自动记录。发完后回来点「读已写卡」。")
            else:
                self._oem_running = False
                sp.launch_btn.setText("启动失败，手动启动")
                sp.launch_btn.setEnabled(True)
        except Exception as e:
            self._oem_running = False
            self._log_msg(f"启动原厂软件异常: {e}")
            sp.launch_btn.setText("启动失败，手动启动")
            sp.launch_btn.setEnabled(True)

    def _on_toggle_recording(self):
        if not self._ui_recorder or not self._ui_recorder.is_running():
            self._sample_panel.set_tip("原厂软件未运行", "warn")
            return
        sp = self._sample_panel
        if self._is_recording:
            try:
                wf = self._ui_recorder.stop_recording()
                ck = self._current.card_type_key if self._current else ""
                if ck:
                    self._store_workflow(ck, wf)
                sp.set_tip(f"录制完成! 共 {len(wf.steps)} 步。把卡放回发卡器，点「读已写卡」。")
            except Exception as e:
                self._log_msg(f"停止录制失败: {e}")
        else:
            try:
                self._ui_recorder.start_recording(self._current.card_type_key if self._current else "")
                sp.set_tip("正在录制...操作完后点「停止录制」。")
            except Exception as e:
                self._log_msg(f"开始录制失败: {e}")
        self._is_recording = not self._is_recording
        sp.record_btn.setText("停止录制" if self._is_recording else "重新录制")
        sp.recording_status.setVisible(self._is_recording)
        sp.recording_status.setText("录制中..." if self._is_recording else "")

    # ══════════════════════════════════════════════════════
    #  擦卡
    # ══════════════════════════════════════════════════════

    def _on_erase(self):
        w = EraseWorker()
        w.done.connect(self._on_erase_done)
        w.start()
        with self._worker_lock:
            self._worker = w
        self._sample_panel.set_tip("正在擦卡...")

    def _on_erase_done(self, ok: bool, msg: str):
        sp = self._sample_panel
        sp.erase_btn.setVisible(False) if sp.erase_btn else None
        if ok:
            self._log_msg("擦卡成功，自动重新读空白卡")
            self._on_read_blank()
        else:
            self._log_msg(f"擦卡失败: {msg}")
            sp.set_tip(f"擦卡失败: {msg}，请手动处理", "error")

    # ══════════════════════════════════════════════════════
    #  清空 / 重采
    # ══════════════════════════════════════════════════════

    def _on_resample(self):
        self._resample_requested = True
        self._refresh_all()

    def _on_clear_all(self):
        from PySide6.QtWidgets import QMessageBox
        mb = QMessageBox(self)
        mb.setWindowTitle("确认清空")
        mb.setText("确定要清空所有采集数据吗？此操作不可撤销。")
        mb.setIcon(QMessageBox.Warning)
        mb.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if mb.exec_() != QMessageBox.Yes:
            return
        self._samples.clear()
        self._analyze_result = None
        self._identity_result = None
        self._log.clear()
        self._log_msg("已清空全部数据")
        self._result.setVisible(False)
        self._forensic_summary.setVisible(False)
        self._refresh_all()

    # ══════════════════════════════════════════════════════
    #  统一刷新
    # ══════════════════════════════════════════════════════

    def _refresh_all(self):
        if self._welcome_shown:
            return
        self._refresh_graduation()
        self._refresh_ui()
        self._refresh_step_coach()
        self._sync_buttons()

    def _refresh_ui(self):
        try:
            self._sample_panel.setVisible(self._site_ok)
        except RuntimeError:
            pass
        try:
            self._probe_gb.setVisible(self._analyze_result is not None)
        except RuntimeError:
            pass
        try:
            self._handover_panel.setVisible(
                self._analyze_result is not None and self._probe_result is not None)
        except RuntimeError:
            pass
        try:
            self._verify_destructive_cb.setVisible(self._analyze_result is not None)
        except RuntimeError:
            pass
        n = len(self._samples)
        for attr, val in [
            ("_stats_samples", f"样本: {n}"),
            ("_stats_brand", f"品牌: {getattr(self._identity_result, 'main_dll', '?')}" if self._identity_result else "品牌: —"),
            ("_stats_mode", f"发卡: {self._probe_result.get('mode', '—')}" if self._probe_result else "发卡: —"),
            ("_stats_bridge", "桥接: 已连接" if self._bridge_ready else "桥接: 未连接"),
        ]:
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    w.setText(val)
            except RuntimeError:
                pass
        try:
            self._sample_panel.update_progress_grid({s.card_type_key for s in self._samples})
        except RuntimeError:
            pass

    def _refresh_step_coach(self):
        try:
            from ..step_coach import resolve_step_coach
            current_dict = None
            if self._current:
                current_dict = {"blank_hex": self._current.blank_hex, "written_hex": self._current.written_hex, "done": self._current.done}
            samples_dict = [s.to_dict() for s in self._samples] if self._samples else []
            state = resolve_step_coach(
                identity=self._identity_result,
                samples=samples_dict,
                current=current_dict,
                analyze_result=self._analyze_result,
                probe_result=self._probe_result,
                readback_hex=self._readback_hex,
                graduation_state=self._graduation_state,
                oem_phase_complete=self._oem_phase_complete,
                card_type_ready=self._card_type_ready,
                resample_requested=self._resample_requested,
            )
            self._step_coach_state = state
            self._resample_requested = False
            self._coach_bar.set_state(state)
        except Exception:
            pass

    def _refresh_graduation(self):
        try:
            from ..bridgecore.graduation_coach import evaluate as evaluate_graduation
            samples_dict = [s.to_dict() for s in self._samples] if self._samples else []
            state = evaluate_graduation(
                identity=self._identity_result,
                samples=samples_dict,
                analyze_result=self._analyze_result,
                probe_result=self._probe_result,
                readback_hex=self._readback_hex,
                token_collected=self._token_collected,
                workflow_recorded=self._has_workflow_recorded(),
            )
            self._graduation_state = state
            self._graduation_panel.update_state(state)

            # Token 可见性
            show_token = False
            if self._analyze_result:
                _auth = ((self._analyze_result.get("profile", {}) or {}).get("card_types", {}) or {}).get("auth", {}) or {}
                if _auth.get("auth_token_repeat"):
                    show_token = True
            self._graduation_panel.set_token_visible(show_token, self._token_collected)

            # 握手包按钮
            self._handover_panel.set_build_enabled(state.can_graduate)
            if state.can_graduate:
                self._handover_panel.set_intro_graduated()
            else:
                self._handover_panel.set_intro_not_graduated()
        except Exception:
            pass

    def _sync_buttons(self):
        can_analyze = bool(
            self._samples and self._identity_result and self._site_ok
        )
        for btn, enabled in [
            (getattr(self, "_analyze_btn", None), can_analyze and not (self._worker and self._worker.isRunning())),
            (getattr(self, "_resample_btn", None), bool(self._analyze_result is None and len(self._samples) >= 1)),
            (getattr(self, "_clear_btn", None), bool(self._analyze_result is None and self._samples)),
        ]:
            if btn is None:
                continue
            try:
                btn.setEnabled(enabled)
            except RuntimeError:
                pass

    # ══════════════════════════════════════════════════════
    #  辅助
    # ══════════════════════════════════════════════════════

    def _find_cardlock_exe(self) -> Optional[str]:
        try:
            from ..bridgecore.oem_process import find_primary_oem_exe
            return find_primary_oem_exe(self._install_dir)
        except Exception:
            return None

    def _has_workflow_recorded(self) -> bool:
        for wf in self._workflows.values():
            if wf and getattr(wf, "steps", None) and len(wf.steps) > 0:
                return True
        return False

    def _store_workflow(self, card_type_key: str, wf: Any):
        if not card_type_key or wf is None:
            return
        self._workflows[card_type_key] = wf
        if card_type_key == "guest":
            self._workflow_guest = wf
        elif card_type_key == "master":
            self._workflow_master = wf
        # 自动保存寄生模板（供再探时回放验证）
        try:
            from ..bridgecore.parasitic_replay import save_template as _save_template
            _save_template(card_type_key, wf)
        except Exception:
            pass

    def _ingest_proxy_log_delta(self):
        try:
            from ..bridgecore.proxy_log_parser import find_proxy_log, parse_proxy_log
            path = find_proxy_log(self._install_dir)
            if not path:
                return
            records = parse_proxy_log(path, offset=self._proxy_log_offset)
            self._proxy_log_offset = os.path.getsize(path) if path else 0
            if records:
                self._dll_traces.extend(records)
                self._log_msg(f"代理日志解析: +{len(records)} 条 DLL 记录")
        except Exception:
            pass

    def _autosave_samples(self):
        try:
            save_dir = collector_work_dir() / "learned_profiles"
            save_dir.mkdir(parents=True, exist_ok=True)
            autosave_path = save_dir / "session_autosave.json"
            data = {
                "samples": [s.to_dict() for s in self._samples],
                "card_type_key": self._current_type_key,
                "install_dir": self._install_dir,
                "loaded_dll": self._loaded_dll,
                "candidate_profile": self._candidate_profile,
            }
            with open(autosave_path, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _try_restore_autosave(self):
        try:
            save_dir = collector_work_dir() / "learned_profiles"
            autosave_path = save_dir / "session_autosave.json"
            if not autosave_path.is_file():
                return
            with open(autosave_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            for sd in data.get("samples", []):
                self._samples.append(SampleCapture.from_dict(sd))  # [FIX] 用 from_dict 代替手动解析
            self._install_dir = data.get("install_dir", self._install_dir)
            self._loaded_dll = data.get("loaded_dll", self._loaded_dll)
            self._candidate_profile = data.get("candidate_profile", self._candidate_profile)
            self._log_msg(f"已恢复 {len(self._samples)} 个样本（上次未完成会话）")
            autosave_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _on_reprobe_upgrade(self):
        """再探按钮 — 8 层破解流水线：
        ① 递归搜 DLL → ② 字符串扫描 → ③ 加密指纹匹配 → ④ 线索猎人 →
        ⑤ 翻注册表 → ⑥ 寄生回放 → ⑦ 换波特率 → ⑧ Ghidra 反编译 →
        全部失败 → 法医诊断包。
        """
        install_dir = self._install_dir
        if not install_dir:
            self._log_msg("无安装目录，无法再探")
            return

        self._reprobe_btn.setEnabled(False)
        self._reprobe_btn.setText("深入搜索中...")
        self._log_msg("🔄 启动深度再探...")

        # ── ① 递归搜所有子目录 DLL ────────────────────
        all_dlls = []
        try:
            for root, dirs, files in os.walk(install_dir):
                for f in files:
                    if f.lower().endswith(('.dll', '.exe')):
                        all_dlls.append(os.path.join(root, f))
            self._log_msg(f"📁 递归搜索发现 {len(all_dlls)} 个可执行文件")
        except Exception as e:
            self._log_msg(f"⚠ 递归搜索异常: {e}")

        probe_meta = {}
        if self._candidate_profile is not None:
            probe_meta = self._candidate_profile.setdefault("probe_meta", {})
        probe_meta["all_dlls_found"] = all_dlls

        # ── ② DLL 字符串扫描（逐文件抠密钥/品牌线索） ──
        strings_found = 0
        all_strings_clues: dict[str, list] = {}
        for dll_path in all_dlls:
            try:
                from ..bridgecore.dll_string_scanner import scan_dll_strings
                result = scan_dll_strings(dll_path)
                if result and result.get("keys"):
                    all_strings_clues[dll_path] = result["keys"]
                    strings_found += len(result["keys"])
            except Exception:
                pass
        if strings_found:
            probe_meta["strings_found"] = strings_found
            probe_meta["strings_clues"] = all_strings_clues
            self._log_msg(f"🔑 DLL 字符串扫描：发现 {strings_found} 个密钥/线索候选")
        else:
            self._log_msg("字符串扫描：未发现有效密钥")

        # ── ③ 加密指纹匹配 ────────────────────────────
        encryption_matched = False
        if strings_found:
            try:
                from ..bridgecore.encryption_fingerprints import match as _encryption_match
                dll_exports = []
                try:
                    from ..bridgecore.dll_prober import probe_dll
                    for dp in all_dlls[:3]:
                        r = probe_dll(dp)
                        if r and r.get("exports"):
                            dll_exports.extend(r["exports"])
                except Exception:
                    pass
                protocol_features = (self._analyze_result or {}).get("profile", {}) or {}
                algo = _encryption_match(protocol_features, dll_exports)
                if algo and algo != "unknown":
                    probe_meta["encryption_match"] = algo
                    encryption_matched = True
                    self._log_msg(f"🔐 加密指纹匹配: {algo}")
            except Exception:
                pass
        if not encryption_matched:
            self._log_msg("加密指纹：未匹配到已知算法")

        # ── ④ 线索猎人（递归追踪 MDB/INI/DAT 深层线索） ──
        clues_hunted = 0
        try:
            from ..bridgecore.clue_hunter import hunt as _clue_hunt
            hunt_input = list(all_strings_clues.keys()) + [install_dir]
            hunted = _clue_hunt(install_dir, hunt_input, depth=3)
            if hunted and hunted.get("found"):
                clues_hunted = len(hunted["found"])
                probe_meta["hunted_clues"] = hunted["found"]
                self._log_msg(f"🔎 线索猎人：追踪到 {clues_hunted} 条深层线索")
        except Exception:
            pass
        if not clues_hunted:
            self._log_msg("线索猎人：未发现深层线索")

        # ── ⑤ 翻注册表 ────────────────────────────────
        if sys.platform.startswith("win"):
            try:
                import subprocess
                registry_snapshot = ""
                for subkey in [
                    r"HKCU\Software",
                    r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                ]:
                    try:
                        output = subprocess.check_output(
                            ["reg", "query", subkey, "/s"],
                            timeout=15,
                            stderr=subprocess.PIPE,
                        )
                        registry_snapshot += output.decode("utf-8", errors="replace")
                    except Exception:
                        pass
                if registry_snapshot:
                    probe_meta["registry"] = registry_snapshot[:10000]
                    self._log_msg("📋 注册表快照已采集")
            except Exception as e:
                self._log_msg(f"⚠ 注册表采集异常: {e}")

        # ── ⑥ 寄生回放（已有模板则试重放） ────────────
        parasitic_ok = False
        try:
            from ..bridgecore.parasitic_replay import load_template as _load_template
            from ..bridgecore.parasitic_replay import replay as _replay_template
            for ct_key in ("guest", "master", "building", "floor"):
                tpl = _load_template(ct_key)
                if tpl:
                    self._log_msg(f"🔄 尝试寄生回放 {ct_key} 模板...")
                    try:
                        replay_ok = _replay_template(tpl)
                        if replay_ok:
                            probe_meta["parasitic_replay_ok"] = True
                            parasitic_ok = True
                            self._log_msg(f"✅ 寄生回放 {ct_key} 成功")
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        # ── ⑦ 换波特率重试（串口模式） ─────────────────
        if self._candidate_profile and self._candidate_profile.get("channel") == "serial":
            baud_rates = [9600, 19200, 38400, 57600, 115200]
            for baud in baud_rates:
                if self._try_connect_baud(baud):
                    probe_meta["working_baud"] = baud
                    self._log_msg(f"🔌 波特率重试成功! {baud}")
                    self._reprobe_btn.setEnabled(True)
                    self._reprobe_btn.setText("再次探测")
                    self._refresh_all()
                    return

        # ── ⑧ Ghidra 深度反编译（重武器） ─────────────
        ghidra_ok = False
        if not (strings_found >= 3 or encryption_matched or clues_hunted >= 3 or parasitic_ok):
            try:
                from ..ghidra_toolkit import run_ghidra_scan
                for dll_path in all_dlls[:1]:  # Ghidra 重，只扫第一份
                    self._log_msg(f"🧬 启动 Ghidra 深度分析 {os.path.basename(dll_path)}...")
                    result = run_ghidra_scan(dll_path, timeout=120)
                    if result and result.success and result.data:
                        probe_meta["ghidra_enriched"] = True
                        probe_meta["ghidra_keys_found"] = int(result.data.get("keys_found", 0) or 0)
                        probe_meta["ghidra_xrefs_found"] = int(result.data.get("xrefs_found", 0) or 0)
                        ghidra_ok = True
                        self._log_msg(f"🔬 Ghidra 完成：{probe_meta['ghidra_keys_found']} 密钥, {probe_meta['ghidra_xrefs_found']} 交叉引用")
                        break
            except Exception as e:
                self._log_msg(f"⚠ Ghidra 分析异常: {e}")

        if not ghidra_ok and strings_found < 3:
            self._log_msg("Ghidra：未执行或无可执行文件")

        # ── 兜底：法医诊断包 ──────────────────────────
        self._log_msg("❌ 全部探测未达成")
        try:
            from ..bridgecore.forensic_packager import package_forensic as _package_forensic
        except Exception:
            _package_forensic = None
        if _package_forensic is not None:
            tried = ["recursive_dll_search"]
            if strings_found:
                tried.append("dll_string_scanner")
            if encryption_matched:
                tried.append("encryption_fingerprints")
            if clues_hunted:
                tried.append("clue_hunter")
            tried.append("registry_snapshot")
            if parasitic_ok:
                tried.append("parasitic_replay")
            tried.append("baud_rate_retry")
            if ghidra_ok:
                tried.append("ghidra_analysis")
            try:
                self._log_msg("🩺 正在生成法医诊断包...")
                context = {
                    "failure_reason": "全部探测未达成",
                    "tried_methods": tried,
                    "stuck_at": "reprobe",
                    "all_dlls": all_dlls,
                    "strings_found": strings_found,
                    "encryption_match": probe_meta.get("encryption_match", "未匹配"),
                    "clues_hunted": clues_hunted,
                    "probe_meta": probe_meta,
                }
                zip_path = _package_forensic(install_dir, context)
                self._log_msg(f"📦 法医诊断包已生成: {zip_path}")
            except Exception as e:
                self._log_msg(f"⚠ 法医诊断包生成失败: {e}")

        self._reprobe_btn.setEnabled(True)
        self._reprobe_btn.setText("再次探测")

    def _try_connect_baud(self, baud: int) -> bool:
        """尝试指定波特率连接串口发卡器。"""
        try:
            from ..bridgecore.serial_channel import SerialBridge
            port = (self._candidate_profile or {}).get("serial", {}).get("port", "COM1")
            sb = SerialBridge(port, baud)
            sb.start()
            try:
                resp = sb.direct_read_usb(d12=1, timeout=3.0)
                return bool(resp.get("ok"))
            finally:
                sb.stop()
        except Exception:
            return False

    def closeEvent(self, event):
        try:
            self._autosave_samples()
        except Exception:
            pass
        # D1: 关闭日志文件
        try:
            if hasattr(self, "_log_file") and self._log_file:
                self._log_file.close()
        except Exception:
            pass
        # D3: 安全停 Worker（含锁保护）
        with self._worker_lock:
            if self._worker and self._worker.isRunning():
                self._worker.quit()
                self._worker.wait(2000)
        # D3: 停止 APDU sniffer
        try:
            from ..apdu_sniffer import get_sniffer
            get_sniffer().stop()
        except Exception:
            pass
        if self._ui_recorder:
            try:
                self._ui_recorder.close()
            except Exception:
                pass
        super().closeEvent(event)
