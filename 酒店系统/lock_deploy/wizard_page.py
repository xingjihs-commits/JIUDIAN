"""
lock_deploy/wizard_page.py — setup_wizard 用的"接管门锁系统"页

替代旧的 _LegacyLockPage —— 一个按钮跳到 cardlock_frontdesk 的简陋设计。

新版三步走：
  ① 点「扫描」→ 后台扫盘找门锁系统安装目录
  ② 列出候选 → 用户挑一个 (默认选分数最高的)
  ③ 点「接管」→
      支持的品牌：直接导入 dlsCoID / HotelID 等配置到 Solid
      未支持品牌：导出 unsupported_report.zip 给用户带回我们这里
      没找到任何：「手动浏览选择目录」走同样的流程

UI 风格对齐 setup_wizard 其它步骤（白底深色字 + 圆角 + ghost 按钮）。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal as QtSignal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
import logging
from ui_helpers import show_warning, show_info, show_error, ask_confirm
from design_tokens import _p
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 后台扫描线程
# ──────────────────────────────────────────────────────────────────

class _ScanThread(QThread):
    finished_with_results = QtSignal(list)  # list[InstallationCandidate]
    progress_text = QtSignal(str)

    def __init__(self, parent=None, *, seeds: Optional[List[str]] = None,
                 time_budget_s: float = 8.0):
        super().__init__(parent)
        self.seeds = seeds
        self.time_budget_s = time_budget_s

    def run(self):
        try:
            from lock_deploy import scan_for_lock_systems
            self.progress_text.emit("正在扫描本地驱动器...")
            results = scan_for_lock_systems(
                time_budget_s=self.time_budget_s, seeds=self.seeds,
            )
            self.finished_with_results.emit(results)
        except Exception as e:
            self.progress_text.emit(f"扫描出错: {e}")
            self.finished_with_results.emit([])


# ──────────────────────────────────────────────────────────────────
# 主页面
# ──────────────────────────────────────────────────────────────────

class LockTakeoverPage(QWidget):
    """setup_wizard 里"接管门锁"步骤的页面。"""

    def __init__(self):
        super().__init__()
        self._candidates: List = []
        self._selected_index: int = -1
        self._scan_thread: Optional[_ScanThread] = None
        self._manual_live_mdb: Optional[Path] = None
        self._build_ui()

    # ──────────── UI ────────────

    def _build_ui(self):
        l = QVBoxLayout(self)
        l.setContentsMargins(24, 16, 24, 16)
        l.setSpacing(12)

        # 标题区
        try:
            from ui_helpers import build_dialog_header
            l.addWidget(build_dialog_header(
                "④ 接管门锁系统",
                "Solid 会在你电脑里自动找到已装的门锁系统（如 proUSB / 智能门锁），"
                "并复用它的发卡器与注册数据 —— 不卸老软件、不动加密、零破解。"
            ))
        except Exception:
            t = QLabel("④ 接管门锁系统")
            t.setStyleSheet("font-size:16px;font-weight:bold;")
            l.addWidget(t)

        # 操作按钮行
        btn_row = QHBoxLayout()
        self.btn_scan = QPushButton("🔍 自动扫描门锁系统")
        self.btn_scan.setObjectName("SolidPrimaryBtn")
        self.btn_scan.clicked.connect(self._do_scan)
        btn_row.addWidget(self.btn_scan)

        self.btn_browse = QPushButton("📁 手动选择目录")
        self.btn_browse.setObjectName("FdGhostBtn")
        self.btn_browse.clicked.connect(self._browse_dir)
        btn_row.addWidget(self.btn_browse)

        self.btn_import_json = QPushButton("📄 导入 hotel_profile.json")
        self.btn_import_json.setObjectName("FdGhostBtn")
        self.btn_import_json.clicked.connect(self._import_json)
        btn_row.addWidget(self.btn_import_json)

        btn_row.addStretch()
        l.addLayout(btn_row)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        l.addWidget(self.progress)

        self.lbl_status = QLabel("尚未扫描。点击「自动扫描门锁系统」开始。")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(
            f"color:{_p('text_muted')}; font-size:12px;"
        )
        l.addWidget(self.lbl_status)

        # 候选列表
        self.list_candidates = QListWidget()
        self.list_candidates.setMinimumHeight(140)
        self.list_candidates.itemSelectionChanged.connect(self._on_select_changed)
        l.addWidget(self.list_candidates)

        # 详情面板
        self.txt_detail = QTextBrowser()
        self.txt_detail.setMinimumHeight(120)
        self.txt_detail.setStyleSheet(
            f"background:{_p('surface')}; border:1px solid {_p('border')};"
            f" border-radius:6px; padding:8px; color:{_p('text')};"
        )
        l.addWidget(self.txt_detail)

        live_row = QHBoxLayout()
        self.btn_live_mdb = QPushButton("📁 手动选择活数据库")
        self.btn_live_mdb.setObjectName("FdGhostBtn")
        self.btn_live_mdb.clicked.connect(self._browse_live_mdb)
        self.btn_live_mdb.setVisible(False)
        live_row.addWidget(self.btn_live_mdb)
        live_row.addStretch()
        l.addLayout(live_row)

        # 接管 / 诊断按钮
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.btn_diag = QPushButton("⚠ 导出诊断包（未支持品牌时用）")
        self.btn_diag.setObjectName("FdGhostBtn")
        self.btn_diag.clicked.connect(self._export_diag)
        self.btn_diag.setEnabled(False)
        action_row.addWidget(self.btn_diag)

        self.btn_takeover = QPushButton("✓ 接管这个系统")
        self.btn_takeover.setObjectName("SolidPrimaryBtn")
        self.btn_takeover.clicked.connect(self._do_takeover)
        self.btn_takeover.setEnabled(False)
        action_row.addWidget(self.btn_takeover)

        l.addLayout(action_row)

        # 已接管历史显示
        self.lbl_done = QLabel("")
        self.lbl_done.setWordWrap(True)
        self.lbl_done.setStyleSheet(
            f"color:{_p('amount_positive')}; font-size:11px;"
        )
        l.addWidget(self.lbl_done)
        self._refresh_done_label()

    # ──────────── 扫描 ────────────

    def _do_scan(self):
        if self._scan_thread and self._scan_thread.isRunning():
            return
        self.btn_scan.setEnabled(False)
        self.progress.setVisible(True)
        self.lbl_status.setText("正在扫描所有本地驱动器（最长 8 秒）...")
        self.list_candidates.clear()
        self.txt_detail.clear()
        self.btn_takeover.setEnabled(False)
        self.btn_diag.setEnabled(False)

        self._scan_thread = _ScanThread(self)
        self._scan_thread.progress_text.connect(self.lbl_status.setText)
        self._scan_thread.finished_with_results.connect(self._on_scan_done)
        self._scan_thread.start()

    def _on_scan_done(self, candidates: list):
        self.btn_scan.setEnabled(True)
        self.progress.setVisible(False)
        self._candidates = candidates
        if not candidates:
            self.lbl_status.setText(
                "未发现已安装的门锁系统。可以点「手动选择目录」指向已知路径，"
                "或跳过本步骤稍后在「设置」里再做。"
            )
            return
        self.lbl_status.setText(
            f"找到 {len(candidates)} 个候选系统。"
            f"绿色 ✓ 表示 Solid 已支持，可直接接管；橙色 ⚠ 是未支持品牌，需要导出诊断包。"
        )
        for i, c in enumerate(candidates):
            mark = "✓" if c.supported else "⚠"
            text = f"{mark}  [{c.brand}]  {c.path}   (score={c.score})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.list_candidates.addItem(item)
        # 默认选第一个（分数最高的）
        self.list_candidates.setCurrentRow(0)

    def _on_select_changed(self):
        items = self.list_candidates.selectedItems()
        if not items:
            self._selected_index = -1
            self.btn_takeover.setEnabled(False)
            self.btn_diag.setEnabled(False)
            self.txt_detail.clear()
            return
        idx = items[0].data(Qt.ItemDataRole.UserRole)
        self._selected_index = int(idx)
        c = self._candidates[self._selected_index]

        # 详情
        lines = [
            f"<b>品牌</b>: {c.brand}",
            f"<b>路径</b>: {c.path}",
            f"<b>识别置信度</b>: {c.score}",
            f"<b>Solid 是否已支持</b>: {'✅ 是（可一键接管）' if c.supported else '⚠️ 否（需导出诊断包）'}",
            f"<b>必备文件</b>: {', '.join(c.matched_required) or '—'}",
            f"<b>可选文件</b>: {', '.join(c.matched_optional) or '—'}",
            f"<b>System.ini</b>: {c.system_ini or '—'}",
            f"<b>MDB 数据库</b>: {len(c.mdb_paths)} 个" + (
                "<br>　　" + "<br>　　".join(str(p) for p in c.mdb_paths) if c.mdb_paths else ""
            ),
        ]
        lines.extend(self._live_mdb_detail_lines(c))
        self.txt_detail.setHtml("<br>".join(lines))

        self.btn_takeover.setEnabled(c.supported)
        self.btn_diag.setEnabled(True)  # 诊断包永远可以导

    def _live_mdb_detail_lines(self, c) -> list:
        """活 MDB 探测摘要（选中候选后即时发现）。"""
        share_hint = ""
        db_bak_hint = ""
        checkout = vip_co = flag_co = guest_ll = ""
        try:
            if c.system_ini and Path(c.system_ini).is_file():
                from lock_deploy.importer import parse_system_ini, extract_prousb_fields
                ini = parse_system_ini(Path(c.system_ini))
                fld = extract_prousb_fields(ini)
                share_hint = fld.get("share_db_path") or ""
                db_bak_hint = fld.get("db_bak_path") or ""
                checkout = fld.get("checkout_time") or ""
                vip_co = fld.get("vip_checkout_time") or ""
                flag_co = fld.get("flag_checkout") or ""
                guest_ll = fld.get("guest_llock") or ""
        except Exception:
            pass

        try:
            from lock_deploy.live_mdb import discover_live_mdb
            live = discover_live_mdb(
                install_dir=Path(c.path),
                share_db_path_hint=share_hint or None,
                db_bak_path_hint=db_bak_hint or None,
                manual_override=self._manual_live_mdb,
            )
        except Exception as e:
            self.btn_live_mdb.setVisible(True)
            return [
                "<hr>",
                f"<b style='color:{_p('danger')};'>今日活数据库</b>: 探测失败 — {e}",
            ]

        self.btn_live_mdb.setVisible(not live.path or not live.validated)

        if not live.path:
            return [
                "<hr>",
                f"<b style='color:{_p('warn')};'>今日活数据库</b>: 未找到（{live.error or '可手动选择'}）",
            ]

        ok_mark = "✓ 校验通过" if live.validated else "⚠ 未校验"
        n_cand = len(live.candidates)
        src_map = {
            "manual": "手动指定",
            "share": "System.ini ShareDBPath",
            "ini": "System.ini DBBakPath",
            "install_backup": "安装目录备份子文件夹",
            "common_root": "常见盘符浅扫",
            "install_root": "安装目录 CardLock.mdb（快照）",
        }
        src_txt = src_map.get(live.source, live.source or "—")
        flag_txt = "脏房" if str(flag_co).strip() == "1" else "空净房" if flag_co != "" else "—"
        share_line = share_hint or "（ini 为空，仅本机使用）"
        lines = [
            "<hr>",
            f"<b>今日活数据库</b>: <code>{live.path}</code> {ok_mark}",
            f"<b>来源</b>: {src_txt}"
            + (f"（同目录 {n_cand} 个滚动备份，修改时间 {live.mtime_iso or '—'}）" if n_cand else ""),
            f"<b>共享数据库路径</b>: {share_line}",
        ]
        if checkout or vip_co:
            lines.append(
                f"<b>退房默认</b>: {checkout or '—'} / 会员 {vip_co or '—'} / 退房后房态 → {flag_txt}"
            )
        if guest_ll:
            lines.append(f"<b>客人卡反锁</b>: {guest_ll}")
        return lines

    def _browse_live_mdb(self):
        """选择活数据库 MDB — 优先选文件夹（自动扫描 *.mdb），降级直选文件。"""
        from pathlib import Path

        # 主入口：选文件夹 → 自动找 MDB
        directory = QFileDialog.getExistingDirectory(
            self, "选择门锁系统文件夹（自动找活数据库 .mdb）",
            "D:\\",
        )
        if directory:
            dp = Path(directory)
            mdb_files = sorted(
                list(dp.glob("*.mdb")) + list(dp.glob("*.MDB"))
                + list(dp.glob("*.accdb")) + list(dp.glob("*.ACCDB"))
                + list(dp.glob("*/*.mdb")) + list(dp.glob("*/*.MDB"))
            )
            if mdb_files:
                preferred = [f for f in mdb_files if f.name.lower() == "cardlock.mdb"]
                chosen = preferred[0] if preferred else mdb_files[0]
                self._manual_live_mdb = chosen
                self._on_select_changed()
                return
            # 文件夹无 MDB → 提示后降级
            show_info(
                self, "提示",
                "所选文件夹中未找到 .mdb 或 .accdb 文件。\
请直接选择 CardLock.mdb 文件。",
            )
        else:
            return  # 用户取消

        # 降级：直接选文件
        path, _ = QFileDialog.getOpenFileName(
            self, "选择活数据库 MDB（备选：直接选文件）",
            "D:\\",
            "Access 数据库 (*.MDB *.mdb);;所有文件 (*.*)",
        )
        if path:
            self._manual_live_mdb = Path(path)
            self._on_select_changed()

    # ──────────── 手动浏览 ────────────

    def _browse_dir(self):
        directory = QFileDialog.getExistingDirectory(
            self, "选择门锁系统安装目录",
            "D:\\",
        )
        if not directory:
            return
        # 用单目录扫描
        from lock_deploy.scanner import LockSystemScanner
        scanner = LockSystemScanner(time_budget_s=3.0, max_depth=2)
        results = scanner.scan(seeds=[directory])
        if results:
            self._on_scan_done(results)
            return
        # 没识别出品牌也允许用户硬塞一个候选（用于诊断模式）
        try:
            from lock_deploy.scanner import InstallationCandidate
            c = InstallationCandidate(
                path=Path(directory),
                brand="未识别品牌",
                adapter_id=None,
                score=10,
                supported=False,
            )
            # 找 mdb / ini
            for p in Path(directory).iterdir():
                try:
                    if p.is_file():
                        if p.suffix.lower() == ".mdb":
                            c.mdb_paths.append(p)
                            c.has_mdb = True
                        elif p.name.lower() == "system.ini":
                            c.system_ini = p
                except Exception:
                    pass
            self._on_scan_done([c])
            self.lbl_status.setText(
                "该目录里没识别到任何已知品牌的 DLL —— 可以导出诊断包给厂家分析。"
            )
        except Exception as e:
            show_warning(self, "浏览失败", str(e))

    # ──────────── JSON 导入 ────────────

    def _import_json(self):
        """从 Solid_Field_Box 产出的 hotel_profile.json 直接导入配置。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 hotel_profile.json", "D:\\", "JSON 文件 (*.json *.JSON)",
        )
        if not path:
            return

        json_path = Path(path)
        try:
            # 先预览 JSON 内容
            import json
            data = json.loads(json_path.read_text("utf-8"))
        except Exception as e:
            from sound_helper import play_fail
            play_fail()
            show_error(self, "无法读取 JSON", f"文件格式错误：\n{e}")
            return

        brand = data.get("brand", "未知")
        profile_ver = data.get("version", "1.0")
        sc = data.get("system_config", {})
        hotel_name = sc.get("hotel_name", "—")
        dls = sc.get("dlsCoID", "—")
        pc_id = sc.get("pc_id", "—")
        install_dir = data.get("install_dir", "—")
        confidence = data.get("brand_confidence", "")

        preview = (
            f"即将从以下配置信息导入参数：\n\n"
            f"  品牌：{brand}"
            + (f"（置信度: {confidence}）" if confidence else "") + "\n"
            f"  酒店：{hotel_name}\n"
            f"  dlsCoID：{dls}\n"
            f"  PCID：{pc_id}\n"
            f"  原始安装目录：{install_dir}\n"
            f"  版本：v{profile_ver}\n\n"
            "导入后 Solid 将使用这些参数来发卡。确认导入？"
        )

        if not ask_confirm(
            self, "确认导入 hotel_profile.json", preview,
        ):
            return

        # 执行导入
        try:
            from lock_deploy.importer import LockTakeoverImporter
            imp = LockTakeoverImporter.from_json(json_path)
            report = imp.run()
        except Exception as e:
            from sound_helper import play_fail
            play_fail()
            show_error(self, "导入失败", f"{e}")
            return

        if not report.get("ok"):
            from sound_helper import play_warn
            play_warn()
            err_msg = "\n".join(report.get("errors") or [])
            show_warning(self, "导入不完整", f"部分配置写入失败：\n{err_msg}")

        fields = report.get("fields") or {}
        warnings = report.get("warnings") or []
        warn_block = ""
        if warnings:
            warn_block = "\n⚠ 注意：\n" + "\n".join(f"  • {w}" for w in warnings[:5])
            if len(warnings) > 5:
                warn_block += f"\n  … 还有 {len(warnings) - 5} 条"

        msg = (
            f"已从 hotel_profile.json 导入 ✓\n\n"
            f"  • 品牌：{brand}\n"
            f"  • dlsCoID：{fields.get('dlsCoID', '—')}\n"
            f"  • HotelID：{(fields.get('hotel_id') or '—')[:16]}...\n"
            f"  • PCID：{fields.get('pc_id', '—')}\n"
            f"  • 酒店名：{fields.get('hotel_name', '—')}\n"
            f"  • 来源：{json_path.name}\n"
            + warn_block
        )
        from sound_helper import play_success
        play_success()
        show_info(self, "导入成功", msg)
        self._refresh_done_label()
        self._maybe_run_full_import()

    # ──────────── 接管 ────────────

    def _do_takeover(self):
        if self._selected_index < 0:
            return
        c = self._candidates[self._selected_index]
        if not c.supported:
            from sound_helper import play_notify
            play_notify()
            show_info(
                self, "未支持品牌",
                f"Solid 当前还没有为「{c.brand}」实现适配器。\n"
                "请点「导出诊断包」把这个品牌的指纹寄回，我们将在下一版加入支持。",
            )
            return

        try:
            from lock_deploy import LockTakeoverImporter
            imp = LockTakeoverImporter(c)
            report = imp.run()
        except Exception as e:
            from sound_helper import play_fail
            play_fail()
            show_error(self, "接管失败", f"导入配置时出错：\n{e}")
            return

        if not report.get("ok"):
            from sound_helper import play_warn
            play_warn()
            show_warning(
                self, "接管不完整",
                "部分配置导入失败：\n" + "\n".join(report.get("errors") or []),
            )

        fields = report.get("fields") or {}
        msg = (
            f"已接管「{c.brand}」 ✓\n\n"
            f"  • 安装路径：{c.path}\n"
            f"  • dlsCoID：{fields.get('dlsCoID', '—')}\n"
            f"  • HotelID：{(fields.get('hotel_id') or '—')[:16]}...\n"
            f"  • PCID：{fields.get('pc_id', '—')}\n"
            f"  • 酒店名：{fields.get('hotel_name', '—')}\n\n"
            "今后 Solid 通过这家酒店的发卡器直接发卡 —— 无需再打开老系统。"
        )
        from sound_helper import play_success
        play_success()
        show_info(self, "接管成功", msg)
        self._refresh_done_label()

        # ── 接管后自动流程：BrandAnalyzer 分析 + 测试发卡 ──────────
        self._auto_post_takeover(c, fields)

    def _auto_post_takeover(self, c, fields: dict):
        """接管后自动跑品牌指纹分析 + 保存配置 + 询问是否测试发卡。"""

        # 1. 如果有卡样本且未分配配置，自动运行 BrandAnalyzer
        if c.card_samples and not c.sample_stat:
            try:
                from lock_adapters.profile.brand_analyzer import BrandAnalyzer
                dlsCoID = int(fields.get("dlsCoID", 0)) or None
                profile = BrandAnalyzer.analyze(
                    c.card_samples,
                    dlsCoID=dlsCoID,
                    brand_hint=c.brand,
                )
                if profile and not profile.get("source", {}).get("empty"):
                    # 保存配置到配置文件
                    try:
                        from lock_adapters.profile.payload_factory import BrandProfileLoader
                        BrandProfileLoader.save(profile)
                        logger.info("品牌配置已自动保存: %s", profile.get("brand"))
                    except Exception as e2:
                        logger.warning("保存品牌配置失败: %s", e2)

                    # 记录到接管配置
                    try:
                        from lock_deploy.importer import load_takeover_config, save_takeover_config
                        cfg = load_takeover_config()
                        if isinstance(cfg, dict):
                            cfg["brand_profile"] = profile
                            cfg["brand_profile_auto"] = True
                            cfg["card_samples_count"] = len(c.card_samples)
                            save_takeover_config(cfg)
                    except Exception:
                        pass

                    # 更新统计供后续使用
                    from lock_deploy.scanner import CardSampleStat
                    c.sample_stat = CardSampleStat(
                        total_samples=len(c.card_samples),
                        detected_brand=profile.get("brand", ""),
                        payload_patterns=profile,
                    )
            except Exception as exc:
                logger.warning("接管后 BrandAnalyzer 分析失败: %s", exc)

        # 2. 询问是否测试发卡
        if not ask_confirm(
            self, "测试发卡？",
            "接管已成功。是否现在放一张空白卡到发卡器上，\n"
            "发一张测试卡验证整套链路是否正常？\n\n"
            "（发卡器必须已插 USB，卡放上去，待写卡。）\n"
            "如不想测试，点「否」跳过。",
        ):
            from sound_helper import play_notify
            play_notify()
            return

        self._test_card_issue(c, fields)

    def _test_card_issue(self, c, fields: dict):
        """发一张测试客人卡。"""

        self.lbl_status.setText("正在测试发卡...")
        self.progress.setVisible(True)

        try:
            # 获取已配置的适配器
            from lock_adapters import detect_adapter
            adapter = detect_adapter(c.path)
            if adapter is None:
                # 尝试用 GenericAdapter + 配置
                if c.sample_stat and c.sample_stat.payload_patterns:
                    from lock_adapters.generic_adapter import GenericLockAdapter
                    adapter = GenericLockAdapter(c.path, profile=c.sample_stat.payload_patterns)
                else:
                    self.progress.setVisible(False)
                    show_warning(self, "测试跳过了",
                                 "未能识别适配器，发卡测试已跳过。\n"
                                 "稍后可在「发卡」页面手动测试。")
                    return

            # 配置适配器
            adapter.configure(**fields)

            # 初始化
            if not adapter.initialize():
                self.progress.setVisible(False)
                show_warning(self, "发卡器初始化失败",
                             "请检查发卡器 USB 连接是否正常。")
                return

            # 读测试卡
            before = adapter.read_card_raw()
            if before is None:
                self.progress.setVisible(False)
                show_warning(self, "未检测到卡片",
                             "发卡器上未放卡。请放一张空白卡后再试。")
                adapter.close()
                return

            # 发客人卡（用演示参数）
            import datetime as _dt
            now = _dt.datetime.now()
            b_date = now.strftime("%y%m%d%H%M")
            e_date = (now + _dt.timedelta(days=1)).strftime("%y%m%d%H%M")

            result = adapter.issue_guest_card(
                lock_no="80000001",
                b_date=b_date,
                e_date=e_date,
                card_no=1,
                llock=False,
            )

            adapter.close()

            self.progress.setVisible(False)

            if result.success:
                from sound_helper import play_success
                play_success()
                msg = (
                    f"测试发卡成功！✓\n\n"
                    f"写到卡的数据：\n"
                    f"  {result.card_hex[:16]}\n"
                    f"  {result.card_hex[16:]}\n\n"
                    f"DLL 返回码：{result.raw_ret}\n"
                    f"payload 已记录到卡样本库。"
                )
                show_info(self, "测试发卡成功", msg)
            else:
                from sound_helper import play_fail
                play_fail()
                msg = (
                    f"测试发卡失败 ✗\n\n"
                    f"错误：{result.error}\n"
                    f"DLL 返回码：{result.raw_ret}\n\n"
                    f"可以尝试以下措施：\n"
                    f"  1. 确认发卡器 USB 已插稳\n"
                    f"  2. 确认卡片是空白可写卡\n"
                    f"  3. 点「诊断导出」获取日志\n"
                )
                show_warning(self, "测试发卡失败", msg)

        except Exception as exc:
            self.progress.setVisible(False)
            logger.error("测试发卡异常: %s", exc)
            show_error(self, "测试发卡异常", str(exc))

    def _maybe_run_full_import(self) -> None:
        """接管成功后引导用户一键把老库的房间/客人/卡/操作员/开门记录全量带过来。"""
        try:
            from legacy_postimport import (
                discover_legacy_mdb_for_takeover,
                run_full_legacy_import,
                format_summary,
            )
        except Exception as e:
            logger.warning("[wizard_page] legacy_postimport 不可用: %s", e)
            return

        mdb = discover_legacy_mdb_for_takeover()
        if mdb is None:
            if not ask_confirm(
                self, "未找到老数据库",
                "Solid 未自动找到 CardLock.mdb（老门锁系统的数据库文件）。\n\n"
                "要手动选择 CardLock.mdb 文件的位置吗？\n"
                "（选「否」可稍后通过系统控制台手动导入）",
            ):
                return
            mdb_path, _ = QFileDialog.getOpenFileName(
                self, "选择 CardLock.mdb 文件", "",
                "MDB 数据库 (*.mdb);;所有文件 (*.*)",
            )
            if not mdb_path:
                return
            mdb = Path(mdb_path)
            if not mdb.is_file():
                show_warning(self, "文件不存在", f"无法访问：{mdb_path}")
                return

        title = "立即把老系统的营业数据搬过来？"
        body = (
            f"已找到老库：\n  {mdb}\n\n"
            "现在把以下数据一次性导进 Solid：\n"
            "  • 房间 / 楼栋 / 锁号 / Dai\n"
            "  • 在住客人（已退房不导）\n"
            "  • 已发卡台账\n"
            "  • 操作员账号 + 权限位\n"
            "  • 开门记录 / 操作员行为\n"
            "  • 空白卡 UID 库\n\n"
            "全部幂等，重复运行无副作用。要现在就做吗？"
        )
        if not ask_confirm(
            self, title, body,
        ):
            return

        try:
            result = run_full_legacy_import(str(mdb))
        except Exception as e:
            show_warning(self, "老库导入失败", str(e))
            return

        msg = format_summary(result)
        show_info(self, "老库导入完成", msg)

    def _export_diag(self):
        if self._selected_index < 0:
            return
        c = self._candidates[self._selected_index]
        from sound_helper import play_success, play_fail
        try:
            from lock_deploy import build_unsupported_report
            try:
                from database import db
                hotel_name = db.get_config("hotel_name") or "酒店"
            except Exception:
                hotel_name = "酒店"
            zip_path = build_unsupported_report(c, hotel_name=hotel_name)
        except Exception as e:
            from sound_helper import play_fail
            play_fail()
            show_error(self, "导出失败", f"生成诊断包出错：\n{e}")
            return

        from sound_helper import play_success
        play_success()
        body = (
            f"已保存到：\n{zip_path}\n\n"
            f"请把这个 zip 发回给 Solid 厂家，等下一版即可支持「{c.brand}」。\n"
            "已自动遮蔽 SN/LD/密码字段，不含 DLL 本身和任何客人数据。"
        )
        show_info(self, "诊断包已生成", body)

    # ──────────── 状态显示 ────────────

    def _refresh_done_label(self):
        try:
            from lock_deploy.importer import load_takeover_config
            cfg = load_takeover_config()
        except Exception:
            cfg = {}
        if cfg.get("lock_takeover_done_at"):
            live_p = cfg.get("lock_takeover_live_mdb_path") or ""
            live_bit = f" | 活库: {live_p}" if live_p else ""
            self.lbl_done.setText(
                f"✓ 已接管: {cfg.get('lock_takeover_brand', '?')} "
                f"于 {cfg.get('lock_takeover_done_at')} "
                f"（dlsCoID={cfg.get('lock_takeover_dlsCoID')}）{live_bit}"
            )
        else:
            self.lbl_done.setText("尚未完成接管。")

    # ──────────── 向导接口 ────────────

    def save(self) -> bool:
        """setup_wizard 翻页时调用。返回 True 才能继续下一页。"""
        # 本步骤是可选的（"全新酒店、没有旧门锁软件" 可以直接跳过）
        return True
