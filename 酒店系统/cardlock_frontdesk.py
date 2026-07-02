"""
cardlock_frontdesk.py — 前台现场对接（强制顺序指引）

适用：换系统酒店、前台电脑、旧「智能门锁／proUSB」与发卡器同在。
必须按 ①→⑤ 操作；跳步会弹窗说明「当前应做第几步」。
"""
from __future__ import annotations

import json
import os
import uuid as _uuid
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal as QtSignal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QGroupBox,
    QFileDialog,
    QProgressBar,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QWidget,
    QSizePolicy,
    QFrame,
)

from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import style_dialog, build_dialog_header, show_info, show_warning, ask_confirm, show_error
from design_tokens import _p

from legacy_migration import (
    open_readonly_legacy_db,
    SchemaAnalyzer,
    DataImporter,
    find_cardlock_mdb_paths,
    scan_cardlock_candidates,
    suggest_cardlock_import_plan,
)
from legacy_preflight import (
    run_front_desk_preflight,
    open_access_engine_download,
)
from legacy_flow_guide import (
    cardlock_flow,
    sync_hub_from_cardlock,
    CARDLOCK_STEPS,
)
from legacy_migration_guide import GuideAction, get_cardlock_step_session
from migration_guide_panel import MigrationGuidePanel
from one_click_migration import CardDataExtractor


def _count_table(name: str) -> int:
    """统计指定表的行数。

    [sub-e] SQL 注入加固：name 必须在 database._ALLOWED_TABLES 白名单中，
    否则 raise ValueError；FROM 后表名用 [name] 方括号包裹（SQLite 标识符语法）。
    调用方（build_post_verify_report）目前传入 rooms / guests / card_records 等硬编码值，
    白名单主要防万一调用方被改成接收用户输入。
    """
    try:
        # [sub-e] 防御性 import：避免模块加载顺序问题
        from database import _ALLOWED_TABLES, _validate_identifier
        safe_name = _validate_identifier(name, _ALLOWED_TABLES)
        row = db.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def build_post_verify_report() -> Dict[str, Any]:
    lk: Dict[str, Any] = {}
    try:
        lk = json.loads(db.get_config("legacy_lock_keys") or "{}")
    except Exception:
        pass
    items = [
        {"id": "rooms", "label": i18n.t("cardlock_frontdesk.report_local_rooms_label"), "ok": _count_table("rooms") > 0,
         "detail": i18n.t("cardlock_frontdesk.report_local_rooms_detail").format(n=_count_table("rooms"))},
        {"id": "guests", "label": i18n.t("cardlock_frontdesk.report_guests_label"), "ok": _count_table("guests") > 0,
         "detail": i18n.t("cardlock_frontdesk.report_guests_detail").format(n=_count_table("guests"))},
        {"id": "cards", "label": i18n.t("cardlock_frontdesk.report_cards_label"), "ok": _count_table("card_records") > 0,
         "detail": i18n.t("cardlock_frontdesk.report_cards_detail").format(n=_count_table("card_records"))},
        {"id": "lock_keys", "label": i18n.t("cardlock_frontdesk.report_lock_keys_label"), "ok": len(lk) > 0,
         "detail": i18n.t("cardlock_frontdesk.report_lock_keys_detail").format(n=len(lk))},
        {"id": "brand", "label": i18n.t("cardlock_frontdesk.report_brand_label"), "ok": bool(db.get_config("lock_brand_name")),
         "detail": db.get_config("lock_brand_name") or "—"},
    ]
    return {"ok": items[0]["ok"] and (items[3]["ok"] or items[4]["ok"]), "items": items}


def format_verify_report(report: Dict[str, Any]) -> str:
    title = i18n.t("cardlock_frontdesk.report_title")
    lines = [f"── {title} ──"]
    for it in report.get("items", []):
        mark = "✓" if it.get("ok") else "○"
        lines.append(f"  [{mark}] {it.get('label')}: {it.get('detail', '')}")
    if report.get("ok"):
        lines.append("\n" + i18n.t("cardlock_frontdesk.report_pass"))
    else:
        lines.append("\n" + i18n.t("cardlock_frontdesk.report_fail"))
    return "\n".join(lines)


class FrontDeskTakeoverWorker(QThread):
    log = QtSignal(str)
    progress = QtSignal(int)
    finished = QtSignal(dict)

    def __init__(self, mdb_path: str, recent_bill_days: int = 120):
        super().__init__()
        self.mdb_path = mdb_path
        self.recent_bill_days = recent_bill_days

    def run(self) -> None:
        out: Dict[str, Any] = {"ok": False, "path": self.mdb_path}
        self.log.emit(i18n.t("cardlock_frontdesk.worker_reading_db"))
        legacy, dtype, msg = open_readonly_legacy_db(self.mdb_path)
        if not legacy:
            out["error"] = msg
            self.finished.emit(out)
            return
        self.log.emit(i18n.t("cardlock_frontdesk.worker_connected_db"))
        try:
            tables = SchemaAnalyzer.analyze_legacy(legacy)
            self.log.emit(i18n.t("cardlock_frontdesk.worker_scanned_tables").format(n=len(tables)))
            if not tables:
                out["error"] = i18n.t("cardlock_frontdesk.worker_no_biz_tables")
                self.finished.emit(out)
                return

            plan = suggest_cardlock_import_plan(tables)
            if not plan:
                self.log.emit(i18n.t("cardlock_frontdesk.worker_no_std_tables"))
                from legacy_migration import CARDLOCK_FIELD_EXTRA
                for tname, tinfo in tables.items():
                    if not isinstance(tinfo, dict):
                        continue
                    cols = tinfo.get("column_names", [])
                    purpose = SchemaAnalyzer.guess_table_purpose(tname, cols)
                    mapping = SchemaAnalyzer.auto_map_fields(cols, extra_aliases=CARDLOCK_FIELD_EXTRA)
                    itype = None
                    if purpose == "房间表":
                        itype = "rooms"
                    elif purpose in ("客人表", "入住记录表"):
                        itype = "guests"
                    if itype and mapping:
                        plan.append({"table": tname, "type": itype, "mapping": mapping})

            total = 0
            for i, item in enumerate(plan):
                self.progress.emit(int(25 + 55 * (i + 1) / max(len(plan), 1)))
                itype = item["type"]
                self.log.emit(i18n.t("cardlock_frontdesk.worker_importing").format(t=itype))
                if itype == "rooms":
                    r = DataImporter.import_rooms(legacy, item["table"], item["mapping"])
                elif itype == "guests":
                    r = DataImporter.import_guests(legacy, item["table"], item["mapping"])
                elif itype == "orders":
                    r = DataImporter.import_orders(
                        legacy, item["table"], item["mapping"],
                        recent_days=self.recent_bill_days,
                    )
                else:
                    r = {"imported": 0}
                total += int(r.get("imported", 0))
                self.log.emit(i18n.t("cardlock_frontdesk.worker_imported_count").format(n=r.get('imported', 0)))

            # ── 第三步：从老数据库的房间信息反推每间房的锁号并回填房间表 ──
            try:
                from lock_adapters.prousb_v9 import ProUsbV9Adapter
                ri_rows, ri_cols = legacy.fetch_table("RoomInfo")
                ri_dicts = [dict(zip(ri_cols, row)) for row in ri_rows]
                updated = 0
                for r in ri_dicts:
                    room_no = str(r.get("RoomNo", "")).strip()
                    if not room_no:
                        continue
                    lock_no = ProUsbV9Adapter.lock_no_from_roominfo_row(r)
                    if not lock_no:
                        continue
                    db.execute(
                        "UPDATE rooms SET lock_no=? WHERE room_id=? AND (lock_no IS NULL OR lock_no='')",
                        (lock_no, room_no),
                    )
                    try:
                        changed = db.execute("SELECT changes()").fetchone()[0] or 0
                    except Exception:
                        changed = 0
                    updated += int(changed)
                if updated:
                    self.log.emit(i18n.t("cardlock_frontdesk.worker_locked_bind").format(n=updated))
            except Exception as e:
                self.log.emit(i18n.t("cardlock_frontdesk.worker_locked_bind_fail").format(e=e))

            saved_cards = 0
            for ct in CardDataExtractor.find_card_tables(tables):
                cd = CardDataExtractor.extract_card_data(legacy, ct)
                for rec in cd.get("records", [])[:500]:
                    try:
                        cid = rec.get("card_id") or f"CL_{_uuid.uuid4().hex[:8].upper()}"
                        db.execute(
                            "INSERT OR IGNORE INTO card_records "
                            "(card_id, room_id, issue_time, expire_time, card_type, status, source_system) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                str(cid)[:64], str(rec.get("room_id", ""))[:32],
                                str(rec.get("issue_time", ""))[:32],
                                str(rec.get("expire_time", ""))[:32],
                                "MIFARE Classic", "migrated", "cardlock_mdb",
                            ),
                        )
                        saved_cards += 1
                    except Exception:
                        pass
            if saved_cards:
                self.log.emit(i18n.t("cardlock_frontdesk.worker_imported_cards").format(n=saved_cards))

            # ── 老库营业流水 ──
            try:
                from legacy_postimport import run_full_legacy_import
                extra = run_full_legacy_import(
                    self.mdb_path,
                    options={
                        "rooms": False, "guests": False,
                        "cards": True, "operators": True,
                        "open_records": True, "actions": True,
                        "ckcard": True, "backfill_lockno": True,
                    },
                )
                if extra.get("ok"):
                    label_map = {
                        "cards": i18n.t("cardlock_frontdesk.legacy_label_cards"),
                        "operators": i18n.t("cardlock_frontdesk.legacy_label_operators"),
                        "open_records": i18n.t("cardlock_frontdesk.legacy_label_open_records"),
                        "actions": i18n.t("cardlock_frontdesk.legacy_label_actions"),
                        "ckcard": i18n.t("cardlock_frontdesk.legacy_label_ckcard"),
                        "backfill_lockno": i18n.t("cardlock_frontdesk.legacy_label_backfill_lockno"),
                    }
                    for k, lab in label_map.items():
                        info = extra.get("steps", {}).get(k) or {}
                        n = int(info.get("imported", 0) or 0)
                        if n > 0:
                            self.log.emit(i18n.t("cardlock_frontdesk.legacy_log_line").format(lab=lab, n=n))
                            out[f"legacy_{k}"] = n
                else:
                    self.log.emit(i18n.t("cardlock_frontdesk.legacy_flow_fail"))
            except Exception as e:
                pass

            lock_cfg = CardDataExtractor.extract_lock_config(self.mdb_path)
            if lock_cfg.get("found_keys"):
                existing: Dict[str, Any] = {}
                try:
                    existing = json.loads(db.get_config("legacy_lock_keys") or "{}")
                except Exception:
                    pass
                if isinstance(existing, dict):
                    existing.update(lock_cfg["found_keys"])
                    db.set_config("legacy_lock_keys", json.dumps(existing, ensure_ascii=False))

            db.set_config("lock_brand", "prousb_cardlock")
            db.set_config("lock_brand_name", "proUSB / 智能门锁 CardLock")
            db.set_config("card_system", "prousb_cardlock")
            try:
                from power_controller_config import sync_power_from_lock_brand
                sync_power_from_lock_brand("prousb_cardlock", "proUSB / 智能门锁 CardLock")
            except Exception:
                pass
            db.set_config("card_sniffer_profile", "prousb_cardlock")
            db.set_config("cardlock_mdb_path", self.mdb_path)
            db.set_config("takeover_last_source", self.mdb_path)
            db.set_config("takeover_last_ok_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            db.set_config("legacy_takeover_kind", "cardlock_frontdesk")

            out["ok"] = True
            out["imported"] = total
            out["cards"] = saved_cards
        except Exception as e:
            out["error"] = str(e)
            self.log.emit(i18n.t("cardlock_frontdesk.worker_error").format(e=e))
        finally:
            legacy.close()
        self.progress.emit(100)
        self.finished.emit(out)


# ══════════════════════════════════════════════════════════════
# 步骤面板键定义（嗅探+配置合并为「配置」）
_STEP_KEYS = ["preflight", "import", "config", "verify"]
_STEP_LABELS = {
    "preflight": i18n.t("cardlock_frontdesk.step_preflight"),
    "import": i18n.t("cardlock_frontdesk.step_import"),
    "config": i18n.t("cardlock_frontdesk.step_config"),
    "verify": i18n.t("cardlock_frontdesk.step_verify"),
}
_STEP_ICONS = {
    "preflight": "🔍",
    "import": "📥",
    "config": "🔧",
    "verify": "✅",
}


class CardLockFrontDeskDialog(QDialog):
    """前台门锁对接 — 左导航 + 右面板子菜单式布局，强制顺序，错步提示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        style_dialog(self, size="xlarge")
        self._worker: Optional[FrontDeskTakeoverWorker] = None
        self._flow = cardlock_flow()
        self._auto_preflight_done = False
        self._build_ui()
        self._auto_pick_mdb()
        self._refresh_flow_ui()
        QTimer.singleShot(700, self._auto_preflight_once)

    def _build_ui(self) -> None:
        """整体布局：顶栏 → 左导航+右面板 → 底栏。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── 顶栏 ──
        layout.addWidget(
            build_dialog_header(
                i18n.t("cardlock_frontdesk.title"),
                i18n.t("cardlock_frontdesk.subtitle"),
            )
        )

        # GuidePanel（醒目指引）
        self.guide_panel = MigrationGuidePanel(on_action=self._on_guide_action)
        self.guide_panel.setTitle(i18n.t("cardlock_frontdesk.guide_title"))
        layout.addWidget(self.guide_panel)

        # ── 主体：左导航 + 右面板 ──
        body = QHBoxLayout()
        body.setSpacing(10)

        # 左：导航列表
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("CardlockNavList")
        self.nav_list.setMinimumWidth(120)
        self.nav_list.setSpacing(2)
        self.nav_list.setFrameShape(QFrame.Shape.NoFrame)
        self.nav_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)

        for key in _STEP_KEYS:
            label = _STEP_LABELS[key]
            icon = _STEP_ICONS[key]
            item = QListWidgetItem(f"  {icon}  {label}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(Qt.QSizeHint(0, 42))
            self.nav_list.addItem(item)

        body.addWidget(self.nav_list, 0)

        # 分隔线
        sep_line = QFrame()
        sep_line.setFrameShape(QFrame.Shape.VLine)
        sep_line.setFrameShadow(QFrame.Shadow.Sunken)
        sep_line.setObjectName("FdColSep")
        body.addWidget(sep_line, 0)

        # 右：步骤面板堆栈
        self.panel_stack = QStackedWidget()
        self._build_panel_preflight()
        self._build_panel_import()
        self._build_panel_config()
        self._build_panel_verify()
        body.addWidget(self.panel_stack, 1)

        layout.addLayout(body, 1)

        # ── 进度条 ──
        self.progress = QProgressBar()
        self.progress.setMaximumHeight(16)
        layout.addWidget(self.progress)

        # ── 日志 ──
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(72)
        self.txt_log.setVisible(False)
        layout.addWidget(self.txt_log)

        # ── 底栏 ──
        foot = QHBoxLayout()
        foot.setSpacing(6)
        btn_enable = QPushButton(i18n.t("cardlock_frontdesk.btn_deploy_kit"))
        btn_enable.setObjectName("FdGhostBtn")
        btn_enable.clicked.connect(self._enable_deploy_kit)
        foot.addWidget(btn_enable)
        btn_ms = QPushButton(i18n.t("cardlock_frontdesk.btn_ms_driver"))
        btn_ms.setObjectName("FdGhostBtn")
        btn_ms.clicked.connect(open_access_engine_download)
        foot.addWidget(btn_ms)
        foot.addStretch()
        btn_close = QPushButton(i18n.t("cardlock_frontdesk.btn_close"))
        btn_close.setObjectName("FdGhostBtn")
        btn_close.clicked.connect(self.accept)
        foot.addWidget(btn_close)

        sep2 = QLabel("│")
        sep2.setObjectName("H4Title")
        foot.addWidget(sep2)

        btn_reset = QPushButton(i18n.t("cardlock_frontdesk.btn_reset"))
        btn_reset.setObjectName("FdGhostBtn")
        btn_reset.setStyleSheet(f"QPushButton#FdGhostBtn {{ color: {_p('danger')}; }}")
        btn_reset.clicked.connect(self._reset_flow)
        foot.addWidget(btn_reset)
        layout.addLayout(foot)

    # ── 面板构建 ──────────────────────────────────────────────

    def _build_panel_preflight(self) -> None:
        """面板 0：预检 — 数据库路径 + 运行按钮 + 结果列表。"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # MDB 路径区
        grp_path = QGroupBox(i18n.t("cardlock_frontdesk.grp_db_path"))
        gl = QVBoxLayout(grp_path)
        row = QHBoxLayout()
        self.ed_path = QLineEdit()
        self.ed_path.setPlaceholderText(i18n.t("cardlock_frontdesk.ph_scanning"))
        row.addWidget(self.ed_path, 1)
        btn_browse = QPushButton(i18n.t("cardlock_frontdesk.btn_browse"))
        btn_browse.setObjectName("FdGhostBtn")
        btn_browse.clicked.connect(self._browse_mdb)
        row.addWidget(btn_browse)
        gl.addLayout(row)
        self.lbl_found = QLabel("")
        self.lbl_found.setObjectName("Tiny")
        gl.addWidget(self.lbl_found)
        layout.addWidget(grp_path)

        # 预检按钮
        self.btn_preflight = QPushButton(i18n.t("cardlock_frontdesk.btn_preflight"))
        self.btn_preflight.setObjectName("SolidPrimaryBtn")
        self.btn_preflight.setMinimumHeight(36)
        self.btn_preflight.clicked.connect(lambda: self._run_step("preflight", self._do_preflight))
        layout.addWidget(self.btn_preflight)

        # 预检结果
        self.lst_preflight = QListWidget()
        self.lst_preflight.setMaximumHeight(100)
        self.lst_preflight.setAlternatingRowColors(False)
        self.lst_preflight.setVisible(False)
        layout.addWidget(self.lst_preflight, 1)
        layout.addStretch()
        self.panel_stack.addWidget(w)

    def _build_panel_import(self) -> None:
        """面板 1：导入 — 进度 + 日志面板。"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        lbl = QLabel(i18n.t("cardlock_frontdesk.panel_import_title"))
        lbl.setObjectName("H2Title")
        layout.addWidget(lbl)
        desc = QLabel(i18n.t("cardlock_frontdesk.panel_import_desc"))
        desc.setWordWrap(True)
        desc.setObjectName("Small")
        layout.addWidget(desc)

        self.btn_import = QPushButton(i18n.t("cardlock_frontdesk.btn_import"))
        self.btn_import.setObjectName("SolidPrimaryBtn")
        self.btn_import.setMinimumHeight(36)
        self.btn_import.clicked.connect(lambda: self._run_step("import", self._do_import))
        layout.addWidget(self.btn_import)

        # 导入日志预览区
        self.import_log_preview = QTextEdit()
        self.import_log_preview.setReadOnly(True)
        self.import_log_preview.setPlaceholderText(i18n.t("cardlock_frontdesk.ph_import_log"))
        self.import_log_preview.setMaximumHeight(150)
        self.import_log_preview.setVisible(False)
        layout.addWidget(self.import_log_preview)
        layout.addStretch()
        self.panel_stack.addWidget(w)

    def _build_panel_config(self) -> None:
        """面板 2：配置 — USB 门锁迁移 / 发卡串口嗅探。"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        lbl = QLabel(i18n.t("cardlock_frontdesk.panel_config_title"))
        lbl.setObjectName("H2Title")
        layout.addWidget(lbl)
        desc = QLabel(i18n.t("cardlock_frontdesk.panel_config_desc"))
        desc.setWordWrap(True)
        desc.setObjectName("Small")
        layout.addWidget(desc)

        # USB 迁移
        grp_usb = QGroupBox(i18n.t("cardlock_frontdesk.grp_usb"))
        usb_l = QVBoxLayout(grp_usb)
        usb_desc = QLabel(i18n.t("cardlock_frontdesk.grp_usb_desc"))
        usb_desc.setWordWrap(True)
        usb_desc.setObjectName("Small")
        usb_l.addWidget(usb_desc)
        self.btn_usb = QPushButton(i18n.t("cardlock_frontdesk.btn_usb"))
        self.btn_usb.setObjectName("SolidPrimaryBtn")
        self.btn_usb.setMinimumHeight(36)
        self.btn_usb.clicked.connect(lambda: self._run_step("usb", self._do_usb))
        usb_l.addWidget(self.btn_usb)
        layout.addWidget(grp_usb)

        # 嗅探
        grp_sniff = QGroupBox(i18n.t("cardlock_frontdesk.grp_sniff"))
        sniff_l = QVBoxLayout(grp_sniff)
        sniff_desc = QLabel(i18n.t("cardlock_frontdesk.grp_sniff_desc"))
        sniff_desc.setWordWrap(True)
        sniff_desc.setObjectName("Small")
        sniff_l.addWidget(sniff_desc)
        self.btn_sniff = QPushButton(i18n.t("cardlock_frontdesk.btn_sniff"))
        self.btn_sniff.setObjectName("SolidPrimaryBtn")
        self.btn_sniff.setMinimumHeight(36)
        self.btn_sniff.clicked.connect(lambda: self._run_step("sniff", self._do_sniff))
        sniff_l.addWidget(self.btn_sniff)
        layout.addWidget(grp_sniff)

        layout.addStretch()
        self.panel_stack.addWidget(w)

    def _build_panel_verify(self) -> None:
        """面板 3：验收 — 结果显示。"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        lbl = QLabel(i18n.t("cardlock_frontdesk.panel_verify_title"))
        lbl.setObjectName("H2Title")
        layout.addWidget(lbl)
        desc = QLabel(i18n.t("cardlock_frontdesk.panel_verify_desc"))
        desc.setWordWrap(True)
        desc.setObjectName("Small")
        layout.addWidget(desc)

        self.btn_verify = QPushButton(i18n.t("cardlock_frontdesk.btn_verify"))
        self.btn_verify.setObjectName("SolidPrimaryBtn")
        self.btn_verify.setMinimumHeight(36)
        self.btn_verify.clicked.connect(lambda: self._run_step("verify", self._do_verify))
        layout.addWidget(self.btn_verify)

        self.verify_result = QTextEdit()
        self.verify_result.setReadOnly(True)
        self.verify_result.setPlaceholderText(i18n.t("cardlock_frontdesk.ph_verify_click"))
        self.verify_result.setMinimumHeight(120)
        layout.addWidget(self.verify_result, 1)

        self.verify_pass_badge = QLabel("")
        self.verify_pass_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.verify_pass_badge.setObjectName("Small")
        self.verify_pass_badge.setVisible(False)
        layout.addWidget(self.verify_pass_badge)
        layout.addStretch()
        self.panel_stack.addWidget(w)

    # ── 导航 ──────────────────────────────────────────────────

    def _on_nav_changed(self, row: int) -> None:
        """用户点击左导航时切换面板，但不校验步骤顺序（仅在执行时校验）。"""
        if 0 <= row < len(_STEP_KEYS):
            self.panel_stack.setCurrentIndex(row)

    def _switch_to_step(self, key: str) -> None:
        """程序切换到指定步骤面板。"""
        for i, k in enumerate(_STEP_KEYS):
            if k == key:
                self.nav_list.setCurrentRow(i)
                self.panel_stack.setCurrentIndex(i)
                break

    def _update_nav_state(self) -> None:
        """更新左导航每项的状态图标（已完成 / 当前 / 待办）。"""
        cur_key = self._flow.current_step().key
        for i in range(self.nav_list.count()):
            item = self.nav_list.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            # "config" 面板是 usb/sniff 的组合
            if key == "config":
                done = self._flow.is_done("usb") or self._flow.is_done("sniff")
                is_cur = cur_key in ("usb", "sniff")
                ok, _ = self._flow.can_execute("usb")
            else:
                done = self._flow.is_done(key)
                is_cur = key == cur_key
                ok, _ = self._flow.can_execute(key)
            prefix = "   "
            if done:
                prefix = "✅ "
            elif is_cur:
                prefix = "👉 "
            elif ok:
                prefix = "   "
            else:
                prefix = "🔒 "
            label = _STEP_LABELS[key]
            icon = _STEP_ICONS[key]
            item.setText(f" {prefix}{icon}  {label}")

    def _show_preflight_items(self, report: Dict[str, Any]) -> None:
        """将预检结果以彩色列表形式显示在预检列表中。"""
        self.lst_preflight.clear()
        self.lst_preflight.setVisible(True)
        for it in report.get("items", []):
            ok = it.get("ok", False)
            label = it.get("label", "")
            detail = it.get("detail", "")
            text = f"  {'✅' if ok else '⏳'}  {label}\n       {detail}" if detail else f"  {'✅' if ok else '⏳'}  {label}"
            item = QListWidgetItem(text)
            item.setForeground(Qt.GlobalColor.darkGreen if ok else Qt.GlobalColor.darkRed)
            font = item.font()
            font.setPointSize(10)
            item.setFont(font)
            self.lst_preflight.addItem(item)
        # 最末加一行总体结论
        conclusion = report.get("ok", False)
        tail = QListWidgetItem(
            i18n.t("cardlock_frontdesk.preflight_conclusion_pass")
            if conclusion else
            i18n.t("cardlock_frontdesk.preflight_conclusion_fail")
        )
        tail.setForeground(Qt.GlobalColor.darkGreen if conclusion else Qt.GlobalColor.darkRed)
        bold = tail.font()
        bold.setBold(True)
        bold.setPointSize(10)
        tail.setFont(bold)
        self.lst_preflight.addItem(tail)

    def _run_step(self, key: str, action: Callable[[], None]) -> None:
        ok, msg = self._flow.can_execute(key)
        if not ok:
            cur = self._flow.current_step()
            show_warning(
                self,
                i18n.t("cardlock_frontdesk.warn_step_first"),
                msg,
            )
            self._log(i18n.t("cardlock_frontdesk.log_skip_step").format(key=cur.key))
            self._switch_to_step(cur.key)
            self._refresh_flow_ui()
            return
        action()

    def _on_guide_action(self, action: str) -> None:
        mapping = {
            GuideAction.EXECUTE_PREFLIGHT: ("preflight", self._do_preflight),
            GuideAction.EXECUTE_IMPORT: ("import", self._do_import),
            GuideAction.OPEN_USB: ("usb", self._do_usb),
            GuideAction.OPEN_SNIFF: ("sniff", self._do_sniff),
            GuideAction.EXECUTE_VERIFY: ("verify", self._do_verify),
        }
        if action in mapping:
            key, fn = mapping[action]
            self._run_step(key, fn)

    def _refresh_flow_ui(self) -> None:
        cur_key = self._flow.current_step().key
        self.guide_panel.bind_session(
            get_cardlock_step_session(cur_key, step_done=self._flow.is_done(cur_key))
        )

        # 更新左导航状态
        self._update_nav_state()

        # 更新各面板按钮
        for key, btn_key in [("preflight", "btn_preflight"), ("import", "btn_import"),
                             ("verify", "btn_verify")]:
            btn = getattr(self, btn_key, None)
            if not btn:
                continue
            done = self._flow.is_done(key)
            is_current = key == cur_key
            ok, _ = self._flow.can_execute(key)
            btn.setEnabled(ok or (key == "preflight" and not self._flow.is_done("import")))
            state = "done" if done else ("current" if is_current else "pending")
            btn.setProperty("flowState", state)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        # config 面板（内部按钮独立控制）
        done_usb = self._flow.is_done("usb")
        done_sniff = self._flow.is_done("sniff")
        ok_cfg, _ = self._flow.can_execute("usb")
        self.btn_usb.setEnabled(ok_cfg)
        self.btn_sniff.setEnabled(ok_cfg)
        if done_usb or done_sniff:
            self.btn_usb.setProperty("flowState", "done")
            self.btn_sniff.setProperty("flowState", "done")
        else:
            self.btn_usb.setProperty("flowState", "current" if cur_key in ("usb", "sniff") else "pending")
            self.btn_sniff.setProperty("flowState", "current" if cur_key in ("usb", "sniff") else "pending")

    def _log(self, msg: str) -> None:
        self.txt_log.append(msg)
        self.txt_log.setVisible(True)

    def _auto_pick_mdb(self) -> None:
        self.lbl_found.setText(i18n.t("cardlock_frontdesk.auto_pick_scanning"))
        candidates = scan_cardlock_candidates(limit=8)
        if candidates:
            top = candidates[0]
            path = str(top.get("path") or "")
            self.ed_path.setText(path)
            self.lbl_found.setText(
                i18n.t("cardlock_frontdesk.auto_pick_found").format(path=path)
            )
            return
        found = find_cardlock_mdb_paths()
        if found:
            self.ed_path.setText(found[0])
            self.lbl_found.setText(i18n.t("cardlock_frontdesk.auto_pick_found").format(path=found[0]))
        else:
            self.lbl_found.setText(i18n.t("cardlock_frontdesk.auto_pick_not_found"))

    def _auto_preflight_once(self) -> None:
        if self._auto_preflight_done or self._flow.is_done("preflight"):
            return
        self._auto_preflight_done = True
        self._log(i18n.t("cardlock_frontdesk.auto_preflight_start"))
        self._do_preflight()

    def _browse_mdb(self) -> None:
        """选择旧门锁系统的数据库文件，支持两种方式：
        1. 选文件夹 → 自动扫描目录内所有数据库文件，优先门锁数据库
        2. 选单个数据库文件 → 直接使用
        """
        if self._flow.is_done("import"):
            show_warning(self, i18n.t("cardlock_frontdesk.tip"), i18n.t("cardlock_frontdesk.browse_mdb_warn"))
            return

        from pathlib import Path

        # 主入口：选文件夹
        directory = QFileDialog.getExistingDirectory(
            self, i18n.t("cardlock_frontdesk.browse_title"),
            self.ed_path.text() or "D:\\",
        )
        if directory:
            dp = Path(directory)
            mdb_files = sorted(list(dp.glob("*.mdb")) + list(dp.glob("*.MDB"))
                               + list(dp.glob("*.accdb")) + list(dp.glob("*.ACCDB")))
            if not mdb_files:
                sub_mdbs = list(dp.glob("*/*.mdb")) + list(dp.glob("*/*.MDB"))
                if sub_mdbs:
                    mdb_files = sub_mdbs
            if mdb_files:
                preferred = [f for f in mdb_files if f.name.lower() == "cardlock.mdb"]
                chosen = preferred[0] if preferred else mdb_files[0]
                self.ed_path.setText(str(chosen))
                extra = i18n.t("cardlock_frontdesk.browse_mdb_multi").format(n=len(mdb_files)) if len(mdb_files) > 1 else ""
                self.lbl_found.setText(i18n.t("cardlock_frontdesk.browse_mdb_selected").format(name=chosen.name) + extra)
                return
            show_warning(self, i18n.t("cardlock_frontdesk.tip"),
                         i18n.t("cardlock_frontdesk.browse_mdb_not_found"))
        else:
            return

        # 降级：直接选 mdb 文件
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("cardlock_frontdesk.browse_title_file"),
            self.ed_path.text() or "D:\\",
            i18n.t("cardlock_frontdesk.browse_filter"),
        )
        if path:
            self.ed_path.setText(path)

    def _enable_deploy_kit(self) -> None:
        try:
            from runtime_deps import ensure_hotel_runtime_deps

            rep = ensure_hotel_runtime_deps(install_ace=True, prefer_mdbtools=False)
            self._show_preflight_items(rep)
            if rep.get("ok"):
                show_info(self, i18n.t("cardlock_frontdesk.deploy_title"), i18n.t("cardlock_frontdesk.deploy_ok"))
            else:
                show_warning(
                    self,
                    i18n.t("cardlock_frontdesk.tip"),
                    i18n.t("cardlock_frontdesk.deploy_fail"),
                )
        except Exception as e:
            show_warning(self, "错误", str(e))

    def _do_preflight(self) -> None:
        path = self.ed_path.text().strip()
        try:
            from runtime_deps import ensure_hotel_runtime_deps

            ensure_hotel_runtime_deps(install_ace=True, prefer_mdbtools=True)
        except Exception:
            pass
        report = run_front_desk_preflight(path)
        self._show_preflight_items(report)
        if report.get("ok"):
            self._flow.mark_done("preflight")
            self._log(i18n.t("cardlock_frontdesk.log_preflight_pass"))
        else:
            self._log(i18n.t("cardlock_frontdesk.log_preflight_fail"))
        if report.get("mdb_path") and not path:
            self.ed_path.setText(report["mdb_path"])
        self._refresh_flow_ui()

    def _do_import(self) -> None:
        path = self.ed_path.text().strip()
        if not path or not os.path.isfile(path):
            show_warning(self, i18n.t("cardlock_frontdesk.tip"), i18n.t("cardlock_frontdesk.do_import_warn"))
            return
        if self._worker and self._worker.isRunning():
            return
        self.btn_import.setEnabled(False)
        self.progress.setValue(0)
        self.txt_log.clear()
        self.import_log_preview.clear()
        self.import_log_preview.setVisible(True)
        self._log(i18n.t("cardlock_frontdesk.log_import_start"))
        self._worker = FrontDeskTakeoverWorker(path)
        self._worker.log.connect(self._log)
        self._worker.log.connect(self.import_log_preview.append)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished.connect(self._on_import_done)
        self._worker.start()

    def _on_import_done(self, result: Dict) -> None:
        if result.get("ok"):
            self._flow.mark_done("import")
            sync_hub_from_cardlock()
            self._log(i18n.t("cardlock_frontdesk.log_import_done"))
        else:
            show_warning(self, i18n.t("cardlock_frontdesk.import_fail_title"), str(result.get("error", "")))
        self._refresh_flow_ui()

    def _do_usb(self) -> None:
        try:
            from usb_lock_migrate_dialog import UsbLockMigrateDialog

            dlg = UsbLockMigrateDialog(self)
            code = dlg.exec()
            if code == QDialog.DialogCode.Accepted:
                self._flow.mark_done("usb")
                sync_hub_from_cardlock()
                self._log(i18n.t("cardlock_frontdesk.log_usb_done"))
            else:
                self._log(i18n.t("cardlock_frontdesk.log_usb_cancelled"))
        except Exception as e:
            show_warning(self, i18n.t("cardlock_frontdesk.cant_open"), str(e))
        self._refresh_flow_ui()

    def _do_sniff(self) -> None:
        try:
            from card_sniffer import open_card_sniffer

            code = open_card_sniffer(self, initial_brand="prousb_cardlock")
            if code == QDialog.DialogCode.Accepted:
                self._flow.mark_done("sniff")
                sync_hub_from_cardlock()
                self._log(i18n.t("cardlock_frontdesk.log_sniff_done"))
            else:
                self._log(i18n.t("cardlock_frontdesk.log_sniff_cancelled"))
        except Exception as e:
            show_warning(self, i18n.t("cardlock_frontdesk.cant_open"), str(e))
        self._refresh_flow_ui()

    def _do_verify(self) -> None:
        rep = build_post_verify_report()
        report_text = format_verify_report(rep)
        self._log(report_text)
        self.verify_result.setPlainText(report_text)
        if rep.get("ok"):
            self._flow.mark_done("verify")
            sync_hub_from_cardlock()
            self._log(i18n.t("cardlock_frontdesk.log_verify_pass"))
            self.verify_pass_badge.setObjectName("H3Title")
            self.verify_pass_badge.setText(i18n.t("cardlock_frontdesk.verify_pass_badge"))
            self.verify_pass_badge.setStyleSheet(
                f"padding: 8px; color: {_p('amount_positive')}; background: {_p('surface_alt')}; border-radius: 8px;"
            )
            self.verify_pass_badge.setVisible(True)
        else:
            self.verify_pass_badge.setVisible(False)
            need = []
            if _count_table("rooms") == 0:
                need.append(i18n.t("cardlock_frontdesk.verify_fail_rooms"))
            lk = db.get_config("legacy_lock_keys") or "{}"
            if lk == "{}" and not db.get_config("lock_migrated_at"):
                need.append(i18n.t("cardlock_frontdesk.verify_fail_keys"))
            msg = i18n.t("cardlock_frontdesk.verify_fail_header") + "\n" + "\n".join(need)
            self._log(msg)
            self.verify_result.append("\n\n" + msg)
            # 自动跳转到对应步骤的引导面板
            if _count_table("rooms") == 0:
                self.guide_panel.bind_session(
                    get_cardlock_step_session("import", step_done=False)
                )
                self._switch_to_step("import")
            else:
                self.guide_panel.bind_session(
                    get_cardlock_step_session("usb", step_done=False)
                )
                self._switch_to_step("config")
        self._refresh_flow_ui()

    def _reset_flow(self) -> None:
        if ask_confirm(self, i18n.t("cardlock_frontdesk.reset_title"), i18n.t("cardlock_frontdesk.reset_confirm")):
            self._flow.reset()
            self.lst_preflight.clear()
            self.lst_preflight.setVisible(False)
            self.import_log_preview.clear()
            self.import_log_preview.setVisible(False)
            self.verify_result.clear()
            self.verify_pass_badge.setVisible(False)
            self.txt_log.clear()
            self.txt_log.setVisible(False)
            self.progress.setValue(0)
            self._switch_to_step("preflight")
            self._log(i18n.t("cardlock_frontdesk.log_reset"))
            self._refresh_flow_ui()

    def closeEvent(self, event) -> None:
        sync_hub_from_cardlock()
        if not self._flow.is_done("verify"):
            cur = self._flow.current_step()
            if not ask_confirm(self, i18n.t("cardlock_frontdesk.not_done_title"), i18n.t("cardlock_frontdesk.not_done_body").format(title=cur.title)):
                event.ignore()
                return
        super().closeEvent(event)


def open_cardlock_frontdesk(parent=None) -> None:
    CardLockFrontDeskDialog(parent).exec()
