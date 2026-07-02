"""
一键迁移编排器

流程: 只读扫描 SQLite 旧库 → 推断在住入住与营业表 → 写入房间和客人（含预计退房、跳过已退房）→
订单（可选按账单日期近期天数）+ 账本（遗留导入）→ 房型模板和价规 → 卡表线索。
不修改原库文件；非 SQLite 请用迁移向导或先导出。
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import time
import uuid as _uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal as QtSignal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QProgressBar,
    QCheckBox, QLineEdit, QWidget, QGroupBox, QGridLayout,
    QTextEdit, QApplication, QFrame, QSpinBox,
)

from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import style_dialog, build_dialog_header, show_error, show_info, show_warning
from design_tokens import _p

# 复用遗留迁移核心组件
from legacy_migration import (
    DatabaseScanner, DatabaseCracker, SchemaAnalyzer, DataImporter,
    SCAN_EXCLUDE_DIRS, DB_EXTENSIONS,
    open_readonly_legacy_db, LegacyDbConn,
    suggest_cardlock_import_plan, find_cardlock_mdb_paths,
    CARDLOCK_FIELD_EXTRA,
)
from legacy_migration_guide import GuideAction, one_click_session
from migration_guide_panel import MigrationGuidePanel


# ================================================================
# 卡密数据提取器 — 从旧系统数据库提取门锁卡信息
# ================================================================
class CardDataExtractor:
    """
    从旧系统数据库中提取门锁卡相关信息:
      - 发卡记录（哪张卡、哪个房间、发卡时间、有效时间）
      - 门锁密钥或加密配置（MIFARE 扇区密钥、写卡密钥等）
      - 卡号与房间映射关系
    这些数据提取后注入我们系统，让旧发卡器继续可用。
    """

    # 常见旧系统卡相关表/字段的命名模式
    CARD_TABLE_PATTERNS = [
        # 表名模式
        r"(?i)(card|key|lock|rfid|mifare|nfc|门锁|房卡|发卡|卡|密钥)",
        # 字段名模式
        r"(?i)(card_id|card_no|card_num|cno|card_number|room_key|key_a|key_b)",
        r"(?i)(sector_key|access_key|write_key|master_key|auth_key)",
        r"(?i)(issue|issued|create|发放|发卡|制作|有效期|expire|valid)",
    ]

    # 卡相关字段别名映射（旧系统 → 我们系统）
    CARD_FIELD_ALIASES: Dict[str, List[str]] = {
        "card_id": ["card_id", "card_no", "card_num", "cno", "card_number",
                     "cardid", "cardcode", "卡号", "房卡号", "rfid_uid", "uid",
                     "serialno", "doorcard", "keycard", "card_sn"],
        "room_id": ["room_id", "room_no", "room_num", "room", "房号", "rno",
                     "roomnumber", "r_id", "房间", "roomcode", "rmno", "bldroomno",
                     "លេខបន្ទប់"],
        "issue_time": ["issue_time", "create_time", "created_at", "issue_date",
                        "card_date", "发放时间", "发卡时间", "制作时间"],
        "expire_time": ["expire_time", "expire_date", "valid_until", "end_time",
                         "到期时间", "有效期", "valid_time", "deadline"],
        "key_a": ["key_a", "sector_key_a", "keya", "a_key", "auth_key_a",
                   "密钥A", "认证密钥A", "sector_0_key", "mifarekeya", "readkey"],
        "key_b": ["key_b", "sector_key_b", "keyb", "b_key", "auth_key_b",
                   "密钥B", "认证密钥B", "write_key", "mifarekeyb", "writekey"],
        "card_type": ["card_type", "type", "cardtype", "卡类型", "mifare_type",
                       "卡种", "rfid_type"],
        "status": ["status", "state", "卡状态", "active", "is_active", "is_valid"],
    }

    @staticmethod
    def find_card_tables(tables: Dict[str, Any]) -> List[str]:
        """在所有表中找到与门锁卡相关的表"""
        card_tables = []
        for tname, tinfo in tables.items():
            if not isinstance(tinfo, dict):
                continue
            name_score = any(re.search(p, tname) for p in CardDataExtractor.CARD_TABLE_PATTERNS[:1])
            cols = tinfo.get("column_names", [])
            col_score = sum(
                1 for col in cols
                for p in CardDataExtractor.CARD_TABLE_PATTERNS[1:]
                if re.search(p, col)
            )
            if name_score or col_score >= 2:
                card_tables.append(tname)
        return card_tables

    @staticmethod
    def extract_card_data(
        conn: Any,
        table_name: str,
    ) -> Dict[str, Any]:
        """从指定表中提取卡相关数据"""
        result: Dict[str, Any] = {
            "table": table_name,
            "records": [],
            "mapped_fields": {},
            "total_rows": 0,
        }

        try:
            if isinstance(conn, LegacyDbConn):
                rows, col_names = conn.fetch_table(table_name)
            else:
                cur = conn.cursor()
                cur.execute(f"SELECT * FROM '{table_name}'")
                rows = cur.fetchall()
                col_names = [desc[0] for desc in cur.description]
            result["total_rows"] = len(rows)

            if not rows:
                return result

            # 自动映射字段
            mapping = {}
            for our_field, aliases in CardDataExtractor.CARD_FIELD_ALIASES.items():
                for col in col_names:
                    col_lower = col.lower().strip().replace(" ", "_").replace("-", "_")
                    for alias in aliases:
                        alias_lower = alias.lower().strip().replace(" ", "_").replace("-", "_")
                        if col_lower == alias_lower or col_lower in alias_lower:
                            if col not in mapping:
                                mapping[col] = our_field
                            break
                    if col in mapping:
                        break

            result["mapped_fields"] = mapping

            # 提取数据行
            for row in rows[:500]:  # 最多500条
                row_dict = dict(zip(col_names, row))
                mapped_row = {}
                for src_col, our_field in mapping.items():
                    val = row_dict.get(src_col)
                    if val is not None:
                        mapped_row[our_field] = str(val)
                if mapped_row:
                    result["records"].append(mapped_row)

        except Exception as e:
            result["error"] = str(e)

        return result

    @staticmethod
    def extract_lock_config(db_path: str) -> Dict[str, Any]:
        """
        从旧系统数据库中提取门锁配置密钥。
        通常在配置设置表或专门的门锁配置表中。
        """
        config: Dict[str, Any] = {
            "found_keys": {},
            "lock_type": "unknown",
            "sectors_used": [],
        }

        legacy, _dt, _msg = open_readonly_legacy_db(db_path)
        if not legacy:
            return config
        try:
            if legacy.kind == "sqlite":
                cur = legacy._conn.cursor()
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND (name LIKE '%config%' OR name LIKE '%setting%' "
                    "OR name LIKE '%lock%' OR name LIKE '%key%' OR name LIKE '%param%')"
                )
                key_tables = [r[0] for r in cur.fetchall()]
                for tname in key_tables:
                    try:
                        cur.execute(f"PRAGMA table_info('{tname}')")
                        cols = [r[1] for r in cur.fetchall()]
                        key_cols = [
                            c for c in cols
                            if any(
                                kw in c.lower()
                                for kw in (
                                    "key", "lock", "secret", "password",
                                    "token", "auth", "encrypt", "sector",
                                )
                            )
                        ]
                        if key_cols:
                            cur.execute(f"SELECT * FROM '{tname}' LIMIT 1")
                            row = cur.fetchone()
                            if row:
                                row_dict = dict(zip(cols, row))
                                for kc in key_cols:
                                    val = row_dict.get(kc)
                                    if val and len(str(val)) >= 4:
                                        config["found_keys"][kc] = str(val)[:256]
                    except Exception:
                        pass
            else:
                tables = SchemaAnalyzer.analyze_legacy(legacy)
                for tname, tinfo in tables.items():
                    if not isinstance(tinfo, dict):
                        continue
                    tl = tname.lower()
                    if not any(k in tl for k in ("config", "setting", "lock", "key", "param", "admin")):
                        continue
                    for col in tinfo.get("column_names", []):
                        if any(
                            kw in col.lower()
                            for kw in ("key", "lock", "secret", "password", "auth", "sector")
                        ):
                            try:
                                rows, cols = legacy.fetch_table(tname)
                                if rows:
                                    row_dict = dict(zip(cols, rows[0]))
                                    val = row_dict.get(col)
                                    if val and len(str(val)) >= 4:
                                        config["found_keys"][f"{tname}.{col}"] = str(val)[:256]
                            except Exception:
                                pass
                            break
        except Exception:
            pass
        finally:
            legacy.close()

        return config


# ================================================================
# 后台工作线程
# ================================================================
class QuickScanWorker(QThread):
    progress = QtSignal(str)
    found_db = QtSignal(dict)
    finished = QtSignal(list)

    def __init__(self, scan_path: str, deep_scan: bool = False):
        super().__init__()
        self.scan_path = scan_path
        self.deep_scan = deep_scan

    def run(self):
        results = DatabaseScanner.scan_path_input(
            self.scan_path,
            max_depth=5 if self.deep_scan else 3,
            progress_cb=lambda p: self.progress.emit(i18n.t("one_click.scanning_file").format(name=os.path.basename(p)[:60]))
        )
        for r in results:
            self.found_db.emit(r)
        self.finished.emit(results)


class OneClickImportWorker(QThread):
    """一键导入线程：扫描→破解→映射→导入→卡密提取→状态对齐→报告"""
    progress = QtSignal(str)
    step_done = QtSignal(str, bool)
    found_db = QtSignal(dict)
    import_log = QtSignal(str)
    finished = QtSignal(dict)

    def __init__(self, target_dir: str, options: Dict[str, bool]):
        super().__init__()
        self.target_dir = target_dir
        self.options = options
        self.summary: Dict[str, Any] = {}

    def run(self):
        try:
            self.summary["source_root"] = self.target_dir
            # ── 步骤1: 扫描 ──
            self.step_done.emit(i18n.t("one_click.step_scan"), False)
            self.progress.emit(i18n.t("one_click.progress_step1"))
            db_files = DatabaseScanner.scan_path_input(
                self.target_dir,
                max_depth=5 if self.options.get("deep_scan") else 3,
                progress_cb=lambda p: self.progress.emit(i18n.t("one_click.scanning_file").format(name=os.path.basename(p)[:60]))
            )
            self.summary["scanned_count"] = len(db_files)
            self.step_done.emit(i18n.t("one_click.step_scan_done").format(n=len(db_files)), True)

            if not db_files:
                self.finished.emit({
                    "ok": False,
                    "error": i18n.t("one_click.error_no_db"),
                    "summary": self.summary,
                })
                return

            # ── 步骤2: 按大小排序，逐个尝试破解 ──
            self.step_done.emit(i18n.t("one_click.step_crack"), False)
            self.progress.emit(i18n.t("one_click.progress_step2"))

            best_db = None
            best_tables = {}
            best_db_path = ""

            for db_info in sorted(db_files, key=lambda x: x.get("size", 0), reverse=True)[:8]:
                db_path = db_info["path"]
                ext = (db_info.get("ext") or os.path.splitext(db_path)[1]).lower()
                db_type = db_info.get("magic_type", i18n.t("one_click.unknown_type"))
                self.progress.emit(i18n.t("one_click.progress_crack_attempt").format(name=db_info['name'], db_type=db_type, size=db_info['size_mb']))

                # 按扩展名优先（魔数常被标成未知导致漏试 SQLite）
                if ext in (".db", ".sqlite", ".sqlite3"):
                    ok, conn, msg = DatabaseCracker.try_open_sqlite(db_path)
                    if ok and conn:
                        tables = SchemaAnalyzer.analyze_sqlite(conn)
                        conn.close()
                        if tables:
                            best_db = db_info
                            best_tables = tables
                            best_db_path = db_path
                            self.import_log.emit(i18n.t("one_click.log_sqlite_ok").format(name=db_info['name'], msg=msg))
                            break
                    self.import_log.emit(i18n.t("one_click.log_sqlite_skip").format(name=db_info['name'], msg=msg))
                    continue

                if ext in (".mdb", ".accdb"):
                    legacy, _dt, msg = open_readonly_legacy_db(db_path)
                    if legacy:
                        try:
                            tables = SchemaAnalyzer.analyze_legacy(legacy)
                        finally:
                            legacy.close()
                        if tables:
                            best_db = db_info
                            best_tables = tables
                            best_db_path = db_path
                            self.import_log.emit(
                                i18n.t("one_click.log_access_ok").format(name=db_info['name'], n=len(tables), msg=msg)
                            )
                            break
                    self.import_log.emit(i18n.t("one_click.log_access_skip").format(name=db_info['name'], msg=msg))
                    continue

                if ext == ".dbf":
                    ok, _meta, msg = DatabaseCracker.try_open_dbf(db_path)
                    if ok:
                        self.import_log.emit(
                            i18n.t("one_click.log_dbf_detect").format(name=db_info['name'], msg=msg)
                        )
                    else:
                        self.import_log.emit(i18n.t("one_click.log_dbf_fail").format(name=db_info['name'], msg=msg))
                    continue

                self.import_log.emit(i18n.t("one_click.log_skip_ext").format(name=db_info['name'], ext=ext))

            if not best_db:
                self.step_done.emit(i18n.t("one_click.step_crack"), False)
                self.finished.emit({
                    "ok": False,
                    "error": i18n.t("one_click.error_no_db_available"),
                    "summary": self.summary,
                })
                return

            self.summary["best_db"] = best_db["name"]
            self.summary["best_db_path"] = best_db_path
            self.summary["table_count"] = len(best_tables)
            self.step_done.emit(i18n.t("one_click.step_crack_done").format(name=best_db['name'], n=len(best_tables)), True)

            self.step_done.emit(i18n.t("one_click.step_map"), False)
            self.progress.emit(i18n.t("one_click.progress_step3"))

            mappings = []
            for tname, tinfo in best_tables.items():
                if not isinstance(tinfo, dict):
                    continue
                columns = tinfo.get("column_names", [])
                if not columns:
                    continue

                purpose = SchemaAnalyzer.guess_table_purpose(tname, columns)
                mapping = SchemaAnalyzer.auto_map_fields(columns, extra_aliases=CARDLOCK_FIELD_EXTRA)

                import_type = None
                if purpose == "房间表":
                    import_type = "rooms"
                elif purpose in ("客人表", "入住记录表"):
                    import_type = "guests"
                elif purpose in ("账单/消费表",):
                    import_type = "orders"

                if import_type and mapping:
                    mappings.append({
                        "table": tname,
                        "type": import_type,
                        "mapping": mapping,
                        "purpose": purpose,
                    })
                    self.import_log.emit(f"  📋 {tname} [{purpose}] → {import_type} ({len(mapping)}字段)")

            rank = {"rooms": 0, "guests": 1, "orders": 2}
            mappings.sort(key=lambda m: (rank.get(m["type"], 9), m.get("table") or ""))

            self.summary["mapped_tables"] = len(mappings)
            self.step_done.emit(i18n.t("one_click.step_map_done").format(n=len(mappings)), True)

            if not mappings:
                self.finished.emit({
                    "ok": False,
                    "error": i18n.t("one_click.error_no_mapping"),
                    "summary": self.summary,
                })
                return

            self.step_done.emit(i18n.t("one_click.step_import_data"), False)
            self.progress.emit(i18n.t("one_click.progress_step4"))

            legacy, _dt, _om = open_readonly_legacy_db(best_db_path)
            if not legacy:
                self.finished.emit({
                    "ok": False,
                    "error": i18n.t("one_click.error_open_db").format(e=_om),
                    "summary": self.summary,
                })
                return
            total_imported = 0
            total_ledger = 0

            for m in mappings:
                import_type = m["type"]
                table = m["table"]
                mapping = m["mapping"]

                if import_type == "rooms" and self.options.get("import_rooms", True):
                    self.progress.emit(i18n.t("one_click.progress_import_rooms").format(table=table))
                    r = DataImporter.import_rooms(legacy, table, mapping)
                elif import_type == "guests" and self.options.get("import_guests", True):
                    self.progress.emit(i18n.t("one_click.progress_import_guests").format(table=table))
                    r = DataImporter.import_guests(legacy, table, mapping)
                elif import_type == "orders" and self.options.get("import_orders", True):
                    self.progress.emit(i18n.t("one_click.progress_import_orders").format(table=table))
                    rd = int(self.options.get("recent_bill_days", 120) or 0)
                    r = DataImporter.import_orders(legacy, table, mapping, recent_days=rd)
                else:
                    r = {"imported": 0, "skipped": 0, "errors": 0}

                imported = r.get("imported", 0)
                total_imported += imported
                leg = int(r.get("ledger_imported", 0) or 0)
                total_ledger += leg
                extra = i18n.t("one_click.import_log_ledger").format(leg=leg) if leg else ""
                self.import_log.emit(
                    i18n.t("one_click.import_log_line").format(
                        import_type=import_type, table=table,
                        imported=imported, skipped=r.get('skipped', 0),
                        errors=r.get('errors', 0), extra=extra
                    )
                )

            self.summary["total_imported"] = total_imported
            self.summary["ledger_imported"] = total_ledger

            # ── 步骤4.5: 老系统营业流水（卡片信息、操作员信息、开门记录、操作日志）──
            # 这部分只跑门锁数据库类的库；非门锁系统老库（已处理完）跳过。
            try:
                from legacy_postimport import run_full_legacy_import
                self.progress.emit(i18n.t("one_click.progress_legacy_supplement"))
                extra = run_full_legacy_import(
                    best_db_path,
                    options={
                        "rooms": False, "guests": False, "ckcard": True,
                        "cards": self.options.get("import_cards", True),
                        "operators": self.options.get("import_operators", True),
                        "open_records": self.options.get("import_open_records", True),
                        "actions": self.options.get("import_actions", True),
                        "backfill_lockno": True,
                    },
                )
                if extra.get("ok"):
                    label_map = {
                        "cards": i18n.t("one_click.legacy_label_cards"),
                        "operators": i18n.t("one_click.legacy_label_operators"),
                        "open_records": i18n.t("one_click.legacy_label_open_records"),
                        "actions": i18n.t("one_click.legacy_label_actions"),
                        "ckcard": i18n.t("one_click.legacy_label_ckcard"),
                        "backfill_lockno": i18n.t("one_click.legacy_label_backfill_lockno"),
                    }
                    for k, lab in label_map.items():
                        info = extra.get("steps", {}).get(k) or {}
                        n = int(info.get("imported", 0) or 0)
                        if n > 0:
                            self.import_log.emit(i18n.t("one_click.legacy_log_line").format(lab=lab, n=n))
            except Exception as e:
                self.import_log.emit(i18n.t("one_click.legacy_fail").format(e=e))

            self.step_done.emit(i18n.t("one_click.step_import_done").format(n=total_imported), True)

            # ── 步骤5: 提取卡密数据 ──
            self.step_done.emit(i18n.t("one_click.step_extract"), False)
            self.progress.emit(i18n.t("one_click.progress_step5"))

            card_tables = CardDataExtractor.find_card_tables(best_tables)
            self.import_log.emit(i18n.t("one_click.log_card_tables_found").format(n=len(card_tables)))

            all_card_records = []
            lock_config = {}

            if card_tables:
                for ct in card_tables:
                    card_data = CardDataExtractor.extract_card_data(legacy, ct)
                    if card_data.get("records"):
                        all_card_records.extend(card_data["records"])
                        self.import_log.emit(
                            i18n.t("one_click.log_card_table_detail").format(table=ct, n=card_data['total_rows'], fields=list(card_data['mapped_fields'].keys())[:6])
                        )

            # 提取门锁配置
            lock_config = CardDataExtractor.extract_lock_config(best_db_path)
            if lock_config.get("found_keys"):
                self.import_log.emit(i18n.t("one_click.log_lock_keys_found").format(n=len(lock_config['found_keys'])))

            legacy.close()

            # 保存卡数据到我们系统
            if all_card_records and self.options.get("extract_cards", True):
                saved_cards = 0
                for rec in all_card_records:
                    try:
                        cid = rec.get("card_id") or rec.get("card_no") or f"CARD_{_uuid.uuid4().hex[:8].upper()}"
                        rid = rec.get("room_id", "")
                        issue_time = rec.get("issue_time", "")
                        expire_time = rec.get("expire_time", "")
                        card_type = rec.get("card_type", "MIFARE Classic")
                        status = rec.get("status", "active")

                        cur = db.execute(
                            "INSERT OR IGNORE INTO card_records "
                            "(card_id, room_id, issue_time, expire_time, card_type, status, source_system) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (str(cid)[:64], str(rid)[:32], str(issue_time)[:32],
                             str(expire_time)[:32], str(card_type)[:32], str(status)[:16],
                             best_db["name"][:64])
                        )
                        if (cur.rowcount if cur.rowcount is not None else 0) == 1:
                            saved_cards += 1
                    except Exception:
                        pass

                self.summary["card_records"] = saved_cards
                self.import_log.emit(i18n.t("one_click.log_cards_saved").format(n=saved_cards))

            # 保存密钥配置
            if lock_config.get("found_keys") and self.options.get("extract_cards", True):
                try:
                    db.set_config("legacy_lock_keys", json.dumps(lock_config["found_keys"], ensure_ascii=False))
                    db.set_config("legacy_lock_type", lock_config.get("lock_type", "unknown"))
                    self.import_log.emit(i18n.t("one_click.log_lock_keys_saved"))
                except Exception as e:
                    self.import_log.emit(i18n.t("one_click.log_lock_keys_fail").format(e=e))

            self.summary["card_tables_found"] = len(card_tables)
            self.step_done.emit(i18n.t("one_click.step_extract_done").format(n_tables=len(card_tables), n_records=len(all_card_records)), True)

            # ── 步骤6: 状态对齐 ──
            if self.options.get("align_status", True):
                self.step_done.emit(i18n.t("one_click.step_align"), False)
                self.progress.emit(i18n.t("one_click.progress_step6"))

                inhouse_guests = db.execute(
                    "SELECT DISTINCT room_id FROM guests WHERE status='INHOUSE'"
                ).fetchall()

                aligned = 0
                for (rid,) in inhouse_guests:
                    if rid:
                        room = db.execute(
                            "SELECT status FROM rooms WHERE room_id=?", (rid,)
                        ).fetchone()
                        if room and room[0] != "INHOUSE":
                            db.execute(
                                "UPDATE rooms SET status='INHOUSE' WHERE room_id=?", (rid,)
                            )
                            aligned += 1

                self.summary["aligned_rooms"] = aligned
                self.step_done.emit(i18n.t("one_click.step_align_done").format(n=aligned), True)
            else:
                self.step_done.emit(i18n.t("one_click.step_align"), True)

            # ── 步骤7: 生成报告 ──
            self.step_done.emit(i18n.t("one_click.step_report"), False)
            self.progress.emit(i18n.t("one_click.progress_step7"))

            report_lines = [
                "=" * 55,
                i18n.t("one_click.report_header"),
                "=" * 55,
                i18n.t("one_click.report_time").format(t=datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                i18n.t("one_click.report_dir").format(d=self.target_dir),
                i18n.t("one_click.report_db").format(name=best_db['name'], size=best_db['size_mb']),
                i18n.t("one_click.report_tables").format(n=len(best_tables)),
                i18n.t("one_click.report_mappings").format(n=len(mappings)),
                i18n.t("one_click.report_imported").format(n=total_imported),
                i18n.t("one_click.report_ledger").format(n=self.summary.get('ledger_imported', 0)),
                i18n.t("one_click.report_card_tables").format(n=len(card_tables)),
                i18n.t("one_click.report_card_records").format(n=self.summary.get('card_records', 0)),
                i18n.t("one_click.report_lock_keys").format(n=len(lock_config.get('found_keys', {}))),
                "",
                i18n.t("one_click.report_aligned").format(n=self.summary.get('aligned_rooms', 0)),
                "",
                i18n.t("one_click.report_warning_header"),
                i18n.t("one_click.report_warning1"),
                i18n.t("one_click.report_warning2"),
                i18n.t("one_click.report_warning3"),
                i18n.t("one_click.report_warning4"),
                "=" * 55,
            ]

            self.summary["report"] = "\n".join(report_lines)
            self.import_log.emit(self.summary["report"])
            self.step_done.emit(i18n.t("one_click.step_report_done"), True)

            self.finished.emit({"ok": True, "summary": self.summary})

        except Exception as e:
            self.summary["failure_report"] = i18n.t("one_click.report_failure").format(
                t=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                d=self.target_dir,
                n=self.summary.get('scanned_count', 0),
                p=self.summary.get('best_db_path', '-'),
                e=e,
            )


# ================================================================
# 一键迁移对话框
# ================================================================
class OneClickMigrationDialog(QDialog):
    """一键接管旧系统对话框 — 文件夹扫描 → 自动识别 → 每步提示文件夹名"""

    def __init__(self, parent=None):
        super().__init__(parent)
        from brand_config_v4 import APP_NAME
        self.setWindowTitle(i18n.t("one_click.window_title").format(app_name=APP_NAME))
        style_dialog(self, size="medium")
        self._selected_folder = ""
        self._db_count = 0
        self._detected_brand = ""
        self._oneclick_guide = one_click_session()
        self._scan_worker: Optional[QuickScanWorker] = None
        self._current_step_idx = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(build_dialog_header(i18n.t("one_click.header_title")))

        self.guide_panel = MigrationGuidePanel(on_action=self._on_guide_action)
        self.guide_panel.bind_session(self._oneclick_guide)
        layout.addWidget(self.guide_panel)

        # ── 文件夹识别结果标签 ──
        self.lbl_scan_result = QLabel("")
        self.lbl_scan_result.setWordWrap(True)
        self.lbl_scan_result.setObjectName("Small")
        self.lbl_scan_result.setStyleSheet(
            f"color:{_p('sidebar')}; background:{_p('surface_alt')}; border:1px solid {_p('amount_positive')}; "
            "border-radius:10px; padding:12px 14px;"
        )
        self.lbl_scan_result.setVisible(False)
        layout.addWidget(self.lbl_scan_result)

        # ── 目录选择 ──
        grp_dir = QGroupBox(i18n.t("one_click.grp_dir"))
        dir_ly = QGridLayout(grp_dir)

        self.txt_target_dir = QLineEdit()
        self.txt_target_dir.setPlaceholderText(
            i18n.t("one_click.ph_target_dir")
        )
        self.txt_target_dir.setReadOnly(True)
        dir_ly.addWidget(self.txt_target_dir, 0, 0)

        btn_browse = QPushButton(i18n.t("one_click.btn_browse"))
        btn_browse.setObjectName("SolidPrimaryBtn")
        btn_browse.setMinimumHeight(38)
        btn_browse.clicked.connect(self._browse_dir)
        btn_browse_file = QPushButton(i18n.t("one_click.btn_browse_file"))
        btn_browse_file.setObjectName("FdGhostBtn")
        btn_browse_file.setToolTip(i18n.t("one_click.tip_browse_file"))
        btn_browse_file.clicked.connect(self._browse_db_file)
        path_btns = QHBoxLayout()
        path_btns.addWidget(btn_browse)
        path_btns.addWidget(btn_browse_file)
        dir_ly.addLayout(path_btns, 0, 1)

        layout.addWidget(grp_dir)

        # ── 文件夹内容分析结果 ──
        grp_analysis = QGroupBox(i18n.t("one_click.grp_analysis"))
        analysis_ly = QVBoxLayout(grp_analysis)

        self.lbl_analysis_detail = QLabel(i18n.t("one_click.lbl_analysis_hint"))
        self.lbl_analysis_detail.setWordWrap(True)
        self.lbl_analysis_detail.setObjectName("Small")
        self.lbl_analysis_detail.setStyleSheet(f"color:{_p('text_muted')}; padding:4px 0;")
        analysis_ly.addWidget(self.lbl_analysis_detail)

        self.analysis_list = QTextEdit()
        self.analysis_list.setReadOnly(True)
        self.analysis_list.setMinimumHeight(60)
        self.analysis_list.setMaximumHeight(100)
        self.analysis_list.setPlaceholderText(i18n.t("one_click.ph_scan_result"))
        analysis_ly.addWidget(self.analysis_list)

        layout.addWidget(grp_analysis)

        # ── 导入选项 ──
        grp_opts = QGroupBox(i18n.t("one_click.grp_options"))
        opts_ly = QVBoxLayout(grp_opts)

        self.chk_rooms = QCheckBox(i18n.t("one_click.chk_rooms"))
        self.chk_rooms.setChecked(True)
        opts_ly.addWidget(self.chk_rooms)

        self.chk_guests = QCheckBox(i18n.t("one_click.chk_guests"))
        self.chk_guests.setChecked(True)
        opts_ly.addWidget(self.chk_guests)

        self.chk_orders = QCheckBox(i18n.t("one_click.chk_orders"))
        self.chk_orders.setChecked(True)
        opts_ly.addWidget(self.chk_orders)

        self.chk_cards = QCheckBox(i18n.t("one_click.chk_cards"))
        self.chk_cards.setChecked(True)
        opts_ly.addWidget(self.chk_cards)

        self.chk_align = QCheckBox(i18n.t("one_click.chk_align"))
        self.chk_align.setChecked(True)
        opts_ly.addWidget(self.chk_align)

        self.chk_deep = QCheckBox(i18n.t("one_click.chk_deep"))
        self.chk_deep.setChecked(False)
        opts_ly.addWidget(self.chk_deep)

        row_recent = QHBoxLayout()
        row_recent.addWidget(QLabel(i18n.t("one_click.label_recent_days")))
        self.spin_recent_bills = QSpinBox()
        self.spin_recent_bills.setRange(0, 730)
        self.spin_recent_bills.setValue(120)
        self.spin_recent_bills.setToolTip(
            i18n.t("one_click.tip_recent_days")
        )
        self.spin_recent_bills.setMaximumWidth(88)
        row_recent.addWidget(self.spin_recent_bills)
        row_recent.addStretch()
        opts_ly.addLayout(row_recent)

        layout.addWidget(grp_opts)

        # ── 进度（纵向可随窗口拉伸，便于看日志）──
        grp_progress = QGroupBox(i18n.t("one_click.grp_progress"))
        prog_ly = QVBoxLayout(grp_progress)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        prog_ly.addWidget(self.progress)

        self.lbl_status = QLabel(i18n.t("one_click.lbl_status_ready"))
        self.lbl_status.setObjectName("Small")
        self.lbl_status.setStyleSheet(f"color:{_p('text_muted')};")
        prog_ly.addWidget(self.lbl_status)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(72)
        self.txt_log.setPlaceholderText(i18n.t("one_click.ph_log"))
        prog_ly.addWidget(self.txt_log, 1)

        # 步骤指示器
        steps_frame = QFrame()
        steps_frame.setStyleSheet(f"background:{_p('surface')}; border-radius:8px; padding:8px;")
        steps_ly = QVBoxLayout(steps_frame)
        steps_ly.setSpacing(3)
        self.step_labels = {}
        self.step_keys = [i18n.t("one_click.step_scan"), i18n.t("one_click.step_crack"),
                     i18n.t("one_click.step_map"), i18n.t("one_click.step_import_data"),
                     i18n.t("one_click.step_extract"), i18n.t("one_click.step_align"),
                     i18n.t("one_click.step_report")]
        for i, s in enumerate(self.step_keys):
            lbl = QLabel(i18n.t("one_click.step_waiting").format(step=s))
            lbl.setObjectName("Tiny")
            steps_ly.addWidget(lbl)
            self.step_labels[i] = lbl
        prog_ly.addWidget(steps_frame)

        layout.addWidget(grp_progress, 1)

        # ── 按钮 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_start = QPushButton(i18n.t("one_click.btn_start"))
        self.btn_start.setObjectName("SolidPrimaryBtn")
        self.btn_start.setStyleSheet(
            "border-radius:8px;"
        )
        self.btn_start.clicked.connect(self._start)
        btn_row.addWidget(self.btn_start)

        self.btn_close = QPushButton(i18n.t("one_click.btn_close"))
        self.btn_close.setObjectName("FdGhostBtn")
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_close)

        layout.addLayout(btn_row)

        self.worker: Optional[OneClickImportWorker] = None

    # ── 文件夹名称缩写 ──
    @staticmethod
    def _short_folder(path: str, max_len: int = 50) -> str:
        if not path:
            return ""
        folder_name = os.path.basename(path.rstrip("\\/"))
        if len(folder_name) > max_len:
            folder_name = folder_name[:max_len - 3] + "..."
        return folder_name

    # ── 品牌识别 ──
    def _detect_brand_from_files(self, folder: str) -> str:
        """扫描文件夹中的特征文件，推测旧酒店系统品牌。"""
        import glob as _glob
        if not folder or not os.path.isdir(folder):
            return ""

        # 典型文件特征
        patterns: list[tuple[str, str]] = [
            ("CardLock*", "CardLock 智能门锁系统"),
            ("cardlock*", "CardLock 智能门锁系统"),
            ("*.mdb", "Access 数据库"),
            ("v9*", "V9 门锁系统"),
            ("mwic_*", "MWIC 门锁系统"),
            ("repair*", "门锁维修工具"),
            ("system.ini", "旧酒店管理系统（配置文件）"),
            ("server*.exe", "酒店服务器端程序"),
        ]

        hits: dict[str, int] = {}
        for pattern, label in patterns:
            try:
                matched = _glob.glob(os.path.join(folder, pattern))
                # 也搜一级子目录
                if not matched:
                    try:
                        matched = _glob.glob(os.path.join(folder, "*", pattern))
                    except Exception:
                        pass
                if matched:
                    hits[label] = hits.get(label, 0) + len(matched)
            except Exception:
                pass

        if hits:
            # 按命中数排序，取最可能的一个
            best = max(hits, key=hits.get)
            return best
        return ""

    def _on_guide_action(self, action: str) -> None:
        if action == GuideAction.BROWSE_DIR:
            self._browse_dir()
        elif action == GuideAction.START_ONECLICK:
            self._start()

    def _browse_dir(self):
        path = QFileDialog.getExistingDirectory(self, i18n.t("one_click.browse_dir_title"))
        if path:
            self.txt_target_dir.setText(path)
            self._selected_folder = path
            self._auto_scan_folder(path)

    def _browse_db_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            i18n.t("one_click.browse_file_title"),
            "",
            i18n.t("one_click.browse_file_filter"),
        )
        if path:
            self.txt_target_dir.setText(path)
            self._selected_folder = os.path.dirname(path) if os.path.isfile(path) else path
            self._auto_scan_folder(path)

    def _auto_scan_folder(self, path: str):
        """选择文件夹后自动扫描，识别内容并显示结果"""
        self.lbl_analysis_detail.setText(i18n.t("one_click.scanning").format(folder=self._short_folder(path)))
        self.analysis_list.clear()
        QApplication.processEvents()

        # 检测品牌
        if os.path.isdir(path):
            brand = self._detect_brand_from_files(path)
        else:
            brand = ""

        # 快速扫描数据库文件
        from legacy_migration.schema_analyzer import DatabaseScanner as _Scanner
        if os.path.isdir(path):
            try:
                results = _Scanner.scan_path_input(path, max_depth=2)
            except Exception:
                results = []
        else:
            one = _Scanner.entry_for_database_file(path)
            results = [one] if one else []

        db_count = len(results)
        scan_lines = []
        if results:
            scan_lines.append(i18n.t("one_click.scan_found_header").format(n=db_count))
            for r in results[:10]:
                name = r.get("name", "?")
                size_mb = r.get("size_mb", 0)
                db_type = r.get("magic_type", r.get("ext", ""))
                scan_lines.append(i18n.t("one_click.scan_line").format(name=name, size=size_mb, db_type=db_type))
            if len(results) > 10:
                scan_lines.append(i18n.t("one_click.scan_more").format(n=len(results) - 10))
        else:
            scan_lines.append(i18n.t("one_click.scan_none"))

        self.analysis_list.setPlainText("\n".join(scan_lines))

        self._db_count = db_count
        self._detected_brand = brand

        folder_short = self._short_folder(path)
        folder_label = i18n.t("one_click.folder_label").format(folder=folder_short) if not os.path.isfile(path) else i18n.t("one_click.file_label").format(name=os.path.basename(path))

        # 生成扫描结果摘要
        result_parts = [i18n.t("one_click.scan_result_selected").format(label=folder_label)]
        if db_count > 0:
            result_parts.append(i18n.t("one_click.scan_result_db_count").format(n=db_count))
        if brand:
            result_parts.append(i18n.t("one_click.scan_result_brand").format(brand=brand))
        result_text = " | ".join(result_parts)
        self.lbl_scan_result.setText(result_text)
        self.lbl_scan_result.setVisible(True)

        # 更新指引面板 — 注入文件夹信息
        self._oneclick_guide = one_click_session(
            folder_path=path,
            db_count=db_count,
            detected_brand=brand,
        )
        self.guide_panel.bind_session(self._oneclick_guide)
        self.guide_panel.refresh()

        self.lbl_analysis_detail.setText(i18n.t("one_click.analysis_done").format(folder=folder_short))

    def _start(self):
        raw = self.txt_target_dir.text().strip().strip('"').strip("'")
        if not raw:
            show_warning(self, i18n.t("one_click.tip"), i18n.t("one_click.start_warn_no_path"))
            return
        target = os.path.abspath(os.path.normpath(raw))
        if not os.path.isdir(target) and not os.path.isfile(target):
            show_warning(self, i18n.t("one_click.invalid_path_title"), i18n.t("one_click.invalid_path_body").format(target=target))
            return
        if os.path.isfile(target):
            ext = os.path.splitext(target)[1].lower()
            if ext not in DB_EXTENSIONS:
                show_warning(
                    self,
                    i18n.t("one_click.unsupported_title"),
                    i18n.t("one_click.unsupported_body"),
                )
                return

        folder_short = self._short_folder(target)

        # 重置 UI
        self._current_step_idx = 0
        self.progress.setValue(0)
        self.txt_log.clear()
        for key, lbl in self.step_labels.items():
            lbl.setText(i18n.t("one_click.step_waiting").format(step=self.step_keys[key]))
            lbl.setObjectName("Tiny")
        self.btn_start.setEnabled(False)
        self.btn_start.setText(i18n.t("one_click.migrating").format(folder=folder_short))
        QApplication.processEvents()

        options = {
            "import_rooms": self.chk_rooms.isChecked(),
            "import_guests": self.chk_guests.isChecked(),
            "import_orders": self.chk_orders.isChecked(),
            "extract_cards": self.chk_cards.isChecked(),
            "align_status": self.chk_align.isChecked(),
            "deep_scan": self.chk_deep.isChecked(),
            "recent_bill_days": int(self.spin_recent_bills.value()),
        }

        self.worker = OneClickImportWorker(target, options)
        self.worker.progress.connect(lambda msg: self.lbl_status.setText(msg))
        self.worker.step_done.connect(self._on_step_done)
        self.worker.import_log.connect(lambda msg: self.txt_log.append(msg))
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_step_done(self, step_text: str, success: bool):
        idx = self._current_step_idx
        if idx < len(self.step_keys):
            prefix = "✅" if success else "❌"
            color = _p('amount_positive') if success else _p('danger')
            self.step_labels[idx].setText(i18n.t("one_click.step_prefix").format(prefix=prefix, text=step_text))
            self.step_labels[idx].setObjectName("Tiny")
            self.step_labels[idx].setStyleSheet(f"color:{color}; font-weight:700;")
        if success:
            self._current_step_idx += 1

    def _on_finished(self, result: Dict):
        self.btn_start.setEnabled(True)
        self.btn_start.setText(i18n.t("one_click.btn_restart"))
        self.progress.setValue(100)

        if result.get("ok"):
            s = result.get("summary", {})
            self.lbl_status.setText(
                i18n.t("one_click.finish_ok_status").format(
                    imported=s.get('total_imported', 0),
                    ledger=s.get('ledger_imported', 0),
                    cards=s.get('card_records', 0)
                )
            )
            self.lbl_status.setObjectName("Body")
            self.lbl_status.setStyleSheet(f"color:{_p('amount_positive')}; font-weight:700;")

            try:
                db.set_config("takeover_last_ok_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                src = (s.get("source_root") or s.get("best_db_path") or "").strip()
                if len(src) > 480:
                    src = src[:477] + "..."
                db.set_config("takeover_last_source", src)
            except Exception:
                pass

            show_info(self, i18n.t("one_click.finish_ok_title"), i18n.t("one_click.finish_ok_body").format(
                imported=s.get('total_imported', 0),
                ledger=s.get('ledger_imported', 0),
                cards=s.get('card_records', 0),
                tables=s.get('card_tables_found', 0),
                rooms=s.get('aligned_rooms', 0),
            ))

            bus.room_status_changed.emit("*", "REFRESH")
            bus.show_success_overlay.emit(i18n.t("one_click.overlay_success").format(n=s.get('total_imported', 0)))
        else:
            self.lbl_status.setText(i18n.t("one_click.finish_fail_status").format(error=result.get('error', i18n.t("one_click.unknown_error"))))
            self.lbl_status.setObjectName("Body")
            self.lbl_status.setStyleSheet(f"color:{_p('danger')}; font-weight:700;")
            show_error(self, i18n.t("one_click.finish_fail_title"), result.get("error", i18n.t("one_click.unknown_error")))


# ================================================================
# 创建卡记录表（首次运行时数据库初始化）
# ================================================================
def init_card_records_table():
    """确保卡记录表存在"""
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS card_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id TEXT,
                room_id TEXT,
                issue_time TEXT,
                expire_time TEXT,
                card_type TEXT DEFAULT 'MIFARE Classic',
                status TEXT DEFAULT 'active',
                source_system TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    except Exception:
        pass


# ================================================================
# 入口函数
# ================================================================
def open_one_click_migration(parent=None):
    """打开一键迁移对话框"""
    init_card_records_table()
    dlg = OneClickMigrationDialog(parent)
    dlg.exec()