"""数据导入相关：DataImporter、LegacyMigrationWizard、向导页面"""
from __future__ import annotations
import csv
import hashlib
import json
import os
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import uuid as _uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal as QtSignal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QProgressBar, QTabWidget,
    QCheckBox, QLineEdit, QWidget, QGroupBox, QGridLayout,
    QTableWidget, QTableWidgetItem, QComboBox, QTextEdit,
    QWizard, QWizardPage, QRadioButton, QButtonGroup,
    QHeaderView, QApplication, QListWidget, QListWidgetItem,
    QSplitter, QFrame, QSpinBox,
)

from database import db
from design_tokens import _p
from event_bus import bus
from ui_helpers import (
    style_dialog,
    style_wizard,
    build_dialog_header,
    show_error,
    show_info,
    show_warning,
    make_dialog_scroll_area,
)
from ui_surface import fd_apply_table_palette
from legacy_migration_guide import legacy_wizard_page_session
from migration_guide_panel import MigrationGuidePanel


from .schema_analyzer import (DatabaseScanner, SchemaAnalyzer, _coerce_date,
                              LegacyDbConn, open_readonly_legacy_db,
                              suggest_cardlock_import_plan)
from .cardlock_scanner import (MifareKeyExtractor, ScanWorker, CrackWorker)


def ensure_room_type_and_pricing(room_type: str) -> None:
    """迁移用：房型字符串在模板或价规中不存在时，补最小可用行（避免获取价格或入住算价失败）。"""
    rt = (room_type or "").strip()
    if not rt:
        return
    try:
        exists = db.execute("SELECT 1 FROM room_type_templates WHERE type_id=?", (rt,)).fetchone()
        if not exists:
            dep = db.get_config_float("default_deposit", 50.0)
            base = max(100.0, float(dep) * 2.0)
            hourly = max(40.0, base * 0.45)
            db.execute(
                "INSERT OR IGNORE INTO room_type_templates "
                "(type_id, type_name, base_price, hourly_price, consumables_json) VALUES (?,?,?,?,?)",
                (rt, rt, base, hourly, "{}"),
            )
        has_pr = db.execute("SELECT 1 FROM pricing_rules WHERE room_type=?", (rt,)).fetchone()
        if not has_pr:
            from pricing_engine import PricingEngine

            row = db.execute("SELECT base_price, hourly_price FROM room_type_templates WHERE type_id=?", (rt,)).fetchone()
            if row:
                bp, hp = float(row[0] or 100), float(row[1] or 50)
            else:
                bp, hp = 100.0, 50.0
            PricingEngine.save_rule(rt, bp, hp)
    except Exception:
        pass


# ================================================================
# 数据导入器
# ================================================================
def _i(row_dict: dict, mapping: Dict[str, str], target_field: str, default=0):
    """从旧数据行字典里按映射关系取出对应我们系统语义的目标字段，尝试转为整数，失败或缺失时返回默认值。

    映射的语义是：旧表字段名 → 我们的目标字段名（如房间号、楼层、楼栋、房内编号等）。
    """
    src_key = next((k for k, v in mapping.items() if v == target_field), "")
    if not src_key:
        return default
    raw = row_dict.get(src_key, "")
    if raw is None:
        return default
    s = str(raw).strip()
    if not s:
        return default
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def _calc_lock_no_for_import(row_dict: dict, mapping: Dict[str, str], rid: str) -> str:
    """通用导入路径下，给一行旧房间数据估算出门锁编号。

    - 映射里若已经把楼栋或楼层或房内编号字段对上，直接用那些值。
    - 房内编号缺省时回退到房间号数字段（便于"房间号即锁内房号"的常见酒店）。
    - 任何异常都吞掉返回空字符串，由发卡前校验补做硬拦截。
    """
    bld = _i(row_dict, mapping, "building", default=1)
    flr = _i(row_dict, mapping, "floor", default=1)
    rid_int = int(rid) if rid.isdigit() else 0
    romid = _i(row_dict, mapping, "rom_id", default=rid_int)
    try:
        from lock_adapters.prousb_v9 import ProUsbV9Adapter
        return ProUsbV9Adapter.lock_no_from_room(bld, flr, romid) or ""
    except Exception:
        return ""


# 已知老系统字段别名 → 扩展属性键（优先于系统属性定义表）
_LEGACY_EXTRA_ALIAS = {
    "meter_no": ["水表", "水表号", "MeterNo", "Meter", "meter"],
    "bath_no": ["浴室", "浴室号", "浴室编号", "BathNo", "Bath"],
    "heat_no": ["暖气", "暖气号", "暖气编号", "HeatNo", "Heat"],
    "no_smoking": ["禁烟", "禁烟房", "NoSmoking", "Smoking"],
    "has_windows": ["有窗", "窗户", "窗", "Window"],
    "has_bathtub": ["有浴缸", "浴缸", "Bathtub"],
    "floor_level": ["楼层等级", "楼层级别", "FlrLevel", "FloorLevel"],
    "decoration_year": ["装修年份", "装修年", "DecoYear", "DecYear"],
    "custom_note": ["特殊备注", "特别备注", "Remark", "备注"],
}


def _collect_extra_props(row_dict: dict, mapping: dict) -> str:
    """从老系统数据行中收集已知扩展属性外的额外列，匹配到系统属性定义表。

    返回 JSON 字符串，空时为空的 JSON 对象。
    """
    # 已知已被映射的列名（去重）
    mapped_cols = {v for v in mapping.values()} | {
        "BldNo", "bldno", "FlrNo", "flrno", "RomID", "romid",
        "Dai", "MaxCards", "Price",
    }

    # 读取系统已定义的扩展属性
    known_defs = {}
    try:
        rows = db.execute(
            "SELECT key, label, field_type FROM room_prop_definitions WHERE enabled=1"
        ).fetchall()
        for k, lbl, ft in rows:
            known_defs[k] = lbl
            known_defs[lbl] = k
    except Exception:
        pass

    extra = {}
    for col, val in row_dict.items():
        col_s = str(col).strip()
        if not col_s or not val or str(val).strip() == "":
            continue
        if col_s in mapped_cols:
            continue
        # 尝试匹配已定义的字段键
        prop_key = known_defs.get(col_s)
        if prop_key:
            extra[prop_key] = str(val).strip()
            continue
        # 尝试别名匹配
        for key, aliases in _LEGACY_EXTRA_ALIAS.items():
            if any(a.lower() == col_s.lower() for a in aliases):
                extra[key] = str(val).strip()
                break
    return json.dumps(extra)


class DataImporter:
    """将旧系统数据按映射关系导入我们系统"""

    @staticmethod
    def import_rooms(
        conn: Any,
        table_name: str,
        mapping: Dict[str, str],
        progress_cb=None,
    ) -> Dict[str, int]:
        """导入房间数据"""
        imported = 0
        skipped = 0
        errors = 0

        try:
            if isinstance(conn, LegacyDbConn):
                rows, col_names = conn.fetch_table(table_name)
            else:
                cur = conn.cursor()
                cur.execute(f"SELECT * FROM '{table_name}'")
                rows = cur.fetchall()
                col_names = [desc[0] for desc in cur.description]

            for i, row in enumerate(rows):
                try:
                    row_dict = dict(zip(col_names, row))
                    rid = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "room_id"), ""), ""
                    )).strip()
                    if not rid:
                        skipped += 1
                        continue

                    # 检查是否已存在
                    exist = db.execute(
                        "SELECT 1 FROM rooms WHERE room_id=?", (rid,)
                    ).fetchone()
                    if exist:
                        skipped += 1
                        continue

                    floor = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "floor"), ""), ""
                    )).strip() or "1"
                    rtype = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "room_type"), ""), ""
                    )).strip() or "标准间"
                    status = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "status"), ""), ""
                    )).strip()

                    # 状态映射
                    status_map = {
                        "空净房": "VC", "空": "VC", "空房": "VC", "ready": "VC",
                        "clean": "VC", "备好": "VC", "可售": "VC", "VC": "VC", "vacant": "VC",
                        "钟点房": "OH", "OH": "OH",
                        "预订房": "OT", "预订": "OT", "OT": "OT",
                        "催租房": "TO", "催租": "TO", "TO": "TO",
                        "脏": "VD", "脏房": "VD", "dirty": "VD", "待清": "VD", "VD": "VD",
                        "维修": "OO", "停用": "OO", "ooo": "OO", "outoforder": "OO", "OO": "OO",
                        "散客房": "OC_WalkIn", "住": "OC_WalkIn", "入住": "OC_WalkIn",
                        "inhouse": "OC_WalkIn", "occ": "OC_WalkIn", "occupied": "OC_WalkIn", "OC": "OC_WalkIn",
                        "团体房": "OC_Team",
                    }
                    mapped_status = "VC"
                    status_l = status.lower()
                    for k, v in status_map.items():
                        if k.lower() in status_l:
                            mapped_status = v
                            break
                    try:
                        bld_no = int(row_dict.get("BldNo") or row_dict.get("bldno") or 1)
                    except Exception:
                        bld_no = 1
                    try:
                        flr_no = int(row_dict.get("FlrNo") or row_dict.get("flrno") or floor or 0)
                    except Exception:
                        flr_no = 0
                    try:
                        rom_id = int(row_dict.get("RomID") or row_dict.get("romid") or 0)
                    except Exception:
                        rom_id = 0
                    try:
                        dai = int(float(row_dict.get("Dai") or 0))
                    except Exception:
                        dai = 0
                    try:
                        max_cards = int(float(row_dict.get("MaxCards") or 100))
                    except Exception:
                        max_cards = 100
                    try:
                        rate_override = float(row_dict.get("Price")) if row_dict.get("Price") not in (None, "") else None
                    except Exception:
                        rate_override = None
                    try:
                        db.execute(
                            "INSERT OR IGNORE INTO buildings (building_id, bld_no, name, sort_order) VALUES (?, ?, ?, ?)",
                            (str(bld_no), bld_no, f"{bld_no:02d}", bld_no),
                        )
                    except Exception:
                        pass

                    db.execute(
                        "INSERT INTO rooms (room_id, floor, room_type, status, lock_no, bld_no, flr_no, rom_id, dai, max_cards, rate_override, extra_props) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            rid, floor, rtype, mapped_status,
                            _calc_lock_no_for_import(row_dict, mapping, rid),
                            bld_no, flr_no, rom_id, dai, max_cards, rate_override,
                            _collect_extra_props(row_dict, mapping),
                        ),
                    )
                    imported += 1
                    ensure_room_type_and_pricing(rtype)
                except Exception:
                    errors += 1

                if progress_cb and i % 50 == 0:
                    progress_cb(i, len(rows))

        except Exception as e:
            return {"imported": imported, "skipped": skipped, "errors": errors, "error": str(e)}

        return {"imported": imported, "skipped": skipped, "errors": errors}

    @staticmethod
    def import_guests(
        conn: Any,
        table_name: str,
        mapping: Dict[str, str],
        progress_cb=None,
    ) -> Dict[str, int]:
        """导入在住客人数据"""
        imported = 0
        skipped = 0
        errors = 0

        try:
            if isinstance(conn, LegacyDbConn):
                rows, col_names = conn.fetch_table(table_name)
            else:
                cur = conn.cursor()
                cur.execute(f"SELECT * FROM '{table_name}'")
                rows = cur.fetchall()
                col_names = [desc[0] for desc in cur.description]

            for i, row in enumerate(rows):
                try:
                    row_dict = dict(zip(col_names, row))
                    name = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "guest_name"), ""), ""
                    )).strip()
                    rid = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "room_id"), ""), ""
                    )).strip()
                    phone = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "phone"), ""), ""
                    )).strip()
                    checkin = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "checkin_time"), ""), ""
                    )).strip()
                    idcard = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "id_card"), ""), ""
                    )).strip()
                    ck_col = next((k for k, v in mapping.items() if v == "checkout_time"), None)
                    co_raw = row_dict.get(ck_col, "") if ck_col else None
                    co_d = _coerce_date(co_raw) if co_raw is not None and str(co_raw).strip() else None
                    if co_d is not None and co_d < date.today():
                        skipped += 1
                        continue

                    dep_col = next((k for k, v in mapping.items() if v == "deposit"), None)
                    dep_val = ""
                    if dep_col:
                        dep_val = str(row_dict.get(dep_col, "") or "").strip()

                    if not name and not rid:
                        skipped += 1
                        continue
                    if not rid:
                        skipped += 1
                        continue
                    if not db.execute("SELECT 1 FROM rooms WHERE room_id=?", (rid,)).fetchone():
                        skipped += 1
                        continue
                    # 与客人表及工作区入住写入一致
                    exist_in = db.execute(
                        "SELECT 1 FROM guests WHERE room_id=? AND status='INHOUSE'",
                        (rid,),
                    ).fetchone()
                    if exist_in:
                        skipped += 1
                        continue

                    display_name = name or "（迁移）"
                    checkin_sql = checkin if checkin else None
                    checkout_sql = (
                        str(co_raw).strip()
                        if co_raw is not None and str(co_raw).strip()
                        else None
                    )
                    db.execute(
                        "INSERT INTO guests (room_id, name, id_card, phone, checkin_time, checkout_time, status) "
                        "VALUES (?, ?, ?, ?, COALESCE(?, datetime('now')), ?, 'INHOUSE')",
                        (rid, display_name, idcard or "", phone or "", checkin_sql, checkout_sql),
                    )

                    if dep_val:
                        try:
                            db.execute(
                                "UPDATE rooms SET note = TRIM(COALESCE(note,'') || ?) WHERE room_id=?",
                                (f" [迁移押金:{dep_val}]", rid),
                            )
                        except Exception:
                            pass

                    # 如果房间存在且状态不是入住中，更新为入住中
                    if rid:
                        room = db.execute(
                            "SELECT status FROM rooms WHERE room_id=?", (rid,)
                        ).fetchone()
                        if room and room[0] != "INHOUSE":
                            db.execute(
                                "UPDATE rooms SET status='INHOUSE' WHERE room_id=?", (rid,)
                            )

                    imported += 1
                except Exception:
                    errors += 1

                if progress_cb and i % 50 == 0:
                    progress_cb(i, len(rows))

        except Exception as e:
            return {"imported": imported, "skipped": skipped, "errors": errors, "error": str(e)}

        return {"imported": imported, "skipped": skipped, "errors": errors}

    @staticmethod
    def import_orders(
        conn: Any,
        table_name: str,
        mapping: Dict[str, str],
        progress_cb=None,
        recent_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """导入历史账单：订单和账本；近期天数大于零时按账单日期映射列只拉近期数据。"""
        imported = 0
        skipped = 0
        errors = 0
        ledger_imported = 0

        try:
            date_src = next(
                (src for src, dst in mapping.items() if dst == "bill_date"),
                None,
            )
            rd = 0 if recent_days is None else int(recent_days)
            if isinstance(conn, LegacyDbConn):
                rows, col_names = conn.fetch_table(
                    table_name,
                    date_col=date_src if rd > 0 else None,
                    recent_days=rd,
                )
            else:
                cur = conn.cursor()
                cur.execute(f"SELECT * FROM '{table_name}' LIMIT 0")
                col_names = [d[0] for d in (cur.description or ())]
                if rd > 0 and date_src and date_src in col_names and re.match(
                    r"^[A-Za-z0-9_\u4e00-\u9fff]+$", date_src
                ):
                    try:
                        cur.execute(
                            f"SELECT * FROM '{table_name}' WHERE date(\"{date_src}\") "
                            f">= date('now', '-{rd} days')"
                        )
                    except sqlite3.Error:
                        cur.execute(f"SELECT * FROM '{table_name}'")
                else:
                    cur.execute(f"SELECT * FROM '{table_name}'")
                rows = cur.fetchall()
                col_names = [desc[0] for desc in cur.description]

            for i, row in enumerate(rows):
                try:
                    row_dict = dict(zip(col_names, row))
                    rid = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "room_id"), ""), ""
                    )).strip()
                    amount = float(row_dict.get(
                        next((k for k, v in mapping.items() if v == "price"), ""), 0
                    ) or 0)
                    note = str(row_dict.get(
                        next((k for k, v in mapping.items() if v == "consumption"), ""), ""
                    )).strip()

                    if not rid:
                        skipped += 1
                        continue
                    if not db.execute("SELECT 1 FROM rooms WHERE room_id=?", (rid,)).fetchone():
                        skipped += 1
                        continue

                    stable_blob = json.dumps(
                        {"tbl": table_name, "row": [row_dict.get(c) for c in col_names]},
                        ensure_ascii=False,
                        default=str,
                    )
                    h = hashlib.sha256(stable_blob.encode("utf-8")).hexdigest()
                    order_id = "ORDLEG_" + h

                    had = db.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone()
                    db.execute(
                        "INSERT OR IGNORE INTO orders (order_id, chat_id, hotel_id, room_id, order_status, "
                        "total_amount, items_json, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            order_id,
                            "legacy",
                            db.get_config("hotel_id") or "",
                            rid,
                            "IMPORTED_LEGACY",
                            amount,
                            "[]",
                            (note or "")[:2000],
                        ),
                    )
                    has = db.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone()
                    if has and not had:
                        imported += 1
                    elif had:
                        skipped += 1
                    else:
                        errors += 1

                    if abs(amount) >= 1e-6:
                        leg_note = f"[老系统表:{table_name}] {(note or '')}"[:950]
                        tx_leg = "LEGACY_" + h
                        tid = db.append_ledger(
                            "LEGACY_IMPORT",
                            amount,
                            "CASH",
                            0,
                            room_id=rid,
                            note=leg_note,
                            pay_method="CASH",
                            is_deposit=0,
                            tx_id_override=tx_leg,
                            emit_event=False,
                        )
                        if tid is not None:
                            ledger_imported += 1
                except Exception:
                    errors += 1

                if progress_cb and i % 50 == 0:
                    progress_cb(i, len(rows))

            if ledger_imported > 0:
                bus.ledger_updated.emit("LEGACY_IMPORT", {"bulk": ledger_imported})

        except Exception as e:
            return {
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
                "ledger_imported": ledger_imported,
                "error": str(e),
            }

        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "ledger_imported": ledger_imported,
        }

    # ──────────────────────────────────────────────
# 以下四个静态方法面向门锁数据库的固定表结构（卡片信息、操作员信息、开门记录、操作日志），不需要分析引擎推字段映射。
# 都是插入忽略或替换，重复跑无副作用。
    # ──────────────────────────────────────────────

    @staticmethod
    def _fetch_rows_dict(conn: Any, table_name: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        """统一拿表数据为字典列表，用字段名取值即可，列名按老库原样保留。"""
        try:
            if isinstance(conn, LegacyDbConn):
                rows, cols = conn.fetch_table(table_name)
            else:
                cur = conn.cursor()
                cur.execute(f"SELECT * FROM '{table_name}'")
                rows = cur.fetchall()
                cols = [d[0] for d in (cur.description or ())]
        except Exception:
            return [], []
        return [dict(zip(cols, r)) for r in rows], cols

    @staticmethod
    def _first(d: Dict[str, Any], *keys: str) -> str:
        """从字典里按候选键名拿第一个非空值，大小写不敏感。"""
        if not d:
            return ""
        lower = {str(k).lower(): k for k in d.keys()}
        for k in keys:
            real = lower.get(str(k).lower())
            if real is None:
                continue
            v = d.get(real)
            if v is None:
                continue
            s = str(v).strip()
            if s:
                return s
        return ""

    @staticmethod
    def import_card_registry(
        conn: Any,
        table_name: str = "CardInfo",
        progress_cb=None,
    ) -> Dict[str, int]:
        """老系统发卡台账 → 系统发卡记录表。

        识别多种卡类型（来自锁遗留桥接的卡规格定义）；卡号唯一。

        老门锁数据库的卡片信息表只有编号或卡号数据等字段，
        没有独立卡号或卡唯一编号。有效负载是 32 位十六进制的门锁协议数据。
        """
        imported = skipped = errors = 0
        try:
            from lock_legacy_bridge import CARD_TYPE_LABELS  # 仅用于类型展示
        except Exception:
            CARD_TYPE_LABELS = {}

        rows, _cols = DataImporter._fetch_rows_dict(conn, table_name)
        if not rows:
            return {"imported": 0, "skipped": 0, "errors": 0}

        for i, item in enumerate(rows):
            try:
                card_id = DataImporter._first(
                    item, "CardNo", "CardUID", "card_id", "card_no", "uid", "卡号"
                )
                if not card_id:
                    # 门锁数据库的卡片信息：从卡片数据字符串里抽字节唯一标识。
                    card_data = DataImporter._first(item, "CardData", "card_data", "Payload")
                    if card_data and len(card_data) >= 26:
                        card_id = card_data[18:26]
                    elif card_data:
                        card_id = card_data  # 短数据兜底
                if not card_id:
                    skipped += 1
                    continue
                card_id = card_id.upper().replace(" ", "").replace(":", "")
                # 持卡人字段在老门锁数据库里写的是房号标签
                holder_raw = DataImporter._first(item, "Holder", "GuestName", "Name", "持卡人", "姓名")
                room_id = DataImporter._first(item, "RoomNo", "room_id", "room_no", "房号")
                if not room_id and holder_raw:
                    # "[WalkIn]1-101" / "[Team]2-202" / "1-101" → 取末段
                    if "]" in holder_raw:
                        tail = holder_raw.split("]", 1)[1].strip()
                    else:
                        tail = holder_raw.strip()
                    if "-" in tail:
                        room_id = tail.split("-", 1)[1].strip()
                    else:
                        room_id = tail
                guest = holder_raw
                issue = DataImporter._first(item, "IssueTime", "MakeTime", "CreateTime", "发卡时间")
                expire = DataImporter._first(item, "ExpireTime", "ValidTo", "EndTime", "有效期")
                ctype_raw = DataImporter._first(
                    item, "CardType", "Kind", "Type", "卡类型", "Status"
                ) or ""
                # 简单归一：能识别的写进注册类型，否则归为客人卡
                ck = ctype_raw.lower()
                registry_kind = "guest"
                if ck in CARD_TYPE_LABELS:
                    registry_kind = ck
                elif "guest" in ck or "客" in ctype_raw:
                    registry_kind = "guest"
                elif "master" in ck or "总" in ctype_raw:
                    registry_kind = "master"
                elif "auth" in ck or "授权" in ctype_raw:
                    registry_kind = "auth"
                elif "house" in ck or "保洁" in ctype_raw or "清洁" in ctype_raw:
                    registry_kind = "housekeeping"
                elif "floor" in ck or "楼层" in ctype_raw:
                    registry_kind = "floor"
                elif "build" in ck or "楼栋" in ctype_raw or "栋" in ctype_raw:
                    registry_kind = "building"
                elif "emerg" in ck or "应急" in ctype_raw:
                    registry_kind = "emergency"
                elif "loss" in ck or "挂失" in ctype_raw:
                    registry_kind = "loss"
                elif "record" in ck or "记录" in ctype_raw:
                    registry_kind = "record"
                elif "group" in ck or "团队" in ctype_raw or "团" in ctype_raw:
                    registry_kind = "group"
                row = db.execute(
                    "SELECT 1 FROM card_records WHERE card_id=?", (card_id,)
                ).fetchone()
                if row:
                    skipped += 1
                    continue
                db.execute(
                    "INSERT INTO card_records (card_id, room_id, guest_name, "
                    "issue_time, expire_time, card_type, status, registry_kind, source_system) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'IMPORTED_LEGACY', ?, 'cardlock_mdb')",
                    (
                        card_id,
                        room_id or ("__REGISTRY__" if registry_kind != "guest" else ""),
                        guest,
                        issue or None,
                        expire or None,
                        ctype_raw or "MIFARE Classic",
                        registry_kind,
                    ),
                )
                imported += 1
            except Exception:
                errors += 1
            if progress_cb and i % 100 == 0:
                progress_cb(i, len(rows))
        return {"imported": imported, "skipped": skipped, "errors": errors}

    @staticmethod
    def import_operators(
        conn: Any,
        table_name: str = "OperatorInfo",
        progress_cb=None,
    ) -> Dict[str, int]:
        """老系统操作员信息 → 系统遗留操作员权限表。"""
        imported = skipped = errors = 0
        rows, _cols = DataImporter._fetch_rows_dict(conn, table_name)
        if not rows:
            return {"imported": 0, "skipped": 0, "errors": 0}
        try:
            from permission_legacy_map import import_legacy_operator
        except Exception as exc:
            return {"imported": 0, "skipped": 0, "errors": len(rows), "error": str(exc)}

        for i, item in enumerate(rows):
            try:
                gh = DataImporter._first(
                    item, "GongHao", "Gonghao", "OperatorID", "ID", "工号", "Code"
                )
                name = DataImporter._first(item, "Name", "OperatorName", "姓名", "XingMing")
                quanxian = DataImporter._first(item, "QuanXian", "Role", "Permission", "角色")
                bitmask = DataImporter._first(
                    item, "BitMap", "PermissionBits", "Bits", "权限位", "QuanXianMa"
                )
                if not gh:
                    skipped += 1
                    continue
                import_legacy_operator(gh, name, quanxian, bitmask)
                imported += 1
            except Exception:
                errors += 1
            if progress_cb and i % 50 == 0:
                progress_cb(i, len(rows))
        return {"imported": imported, "skipped": skipped, "errors": errors}

    @staticmethod
    def import_open_records(
        conn: Any,
        table_name: str = "RecordOpen",
        progress_cb=None,
    ) -> Dict[str, int]:
        """老系统开门记录 → 系统遗留开门记录表。"""
        imported = skipped = errors = 0
        rows, _cols = DataImporter._fetch_rows_dict(conn, table_name)
        if not rows:
            return {"imported": 0, "skipped": 0, "errors": 0}

        for i, item in enumerate(rows):
            try:
                src_id = DataImporter._first(
                    item, "ID", "Id", "RecordID", "OpenID", "Sequence"
                ) or str(i)
                card_no = DataImporter._first(
                    item, "CardNo", "CardUID", "UID", "卡号"
                ).upper().replace(" ", "").replace(":", "")
                room_no = DataImporter._first(item, "RoomNo", "room_id", "房号")
                bld_no = int(DataImporter._first(item, "BldNo", "bld_no") or 0 or "0")
                flr_no = int(DataImporter._first(item, "FlrNo", "flr_no") or 0 or "0")
                rom_id = int(DataImporter._first(item, "RomID", "rom_id") or 0 or "0")
                open_time = DataImporter._first(
                    item, "OpenTime", "OpTime", "RecordTime", "Time", "开门时间"
                )
                kind = DataImporter._first(
                    item, "Kind", "Type", "OpKind", "OpType", "开门方式", "类型"
                )
                raw_json = json.dumps(
                    {k: (v if isinstance(v, (str, int, float, type(None))) else str(v))
                     for k, v in item.items()},
                    ensure_ascii=False,
                    default=str,
                )[:4000]
                # 重复导入幂等（按来源表名加来源编号唯一）
                cur = db.execute(
                    "INSERT OR IGNORE INTO legacy_open_records "
                    "(source_id, source_table, card_no, room_id, bld_no, flr_no, rom_id, "
                    " open_time, op_kind, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(src_id), table_name, card_no, room_no, bld_no, flr_no, rom_id,
                     open_time, kind, raw_json),
                )
                if cur and getattr(cur, "rowcount", 0) > 0:
                    imported += 1
                else:
                    skipped += 1
            except Exception:
                errors += 1
            if progress_cb and i % 200 == 0:
                progress_cb(i, len(rows))
        return {"imported": imported, "skipped": skipped, "errors": errors}

    @staticmethod
    def import_operator_actions(
        conn: Any,
        table_name: str = "CaoZuoInfo",
        progress_cb=None,
    ) -> Dict[str, int]:
        """老系统操作日志 → 系统遗留操作行为表。"""
        imported = skipped = errors = 0
        rows, _cols = DataImporter._fetch_rows_dict(conn, table_name)
        if not rows:
            return {"imported": 0, "skipped": 0, "errors": 0}

        for i, item in enumerate(rows):
            try:
                src_id = DataImporter._first(
                    item, "ID", "Id", "Sequence", "RecordID"
                ) or str(i)
                gonghao = DataImporter._first(
                    item, "GongHao", "Gonghao", "OperatorID", "ID2", "工号"
                )
                op_name = DataImporter._first(item, "Name", "OperatorName", "姓名")
                action = DataImporter._first(
                    item, "Action", "OpAction", "Operation", "动作", "事件"
                )
                target = DataImporter._first(
                    item, "Target", "Object", "Detail", "对象", "目标", "Note", "Remark"
                )
                happened_at = DataImporter._first(
                    item, "OpTime", "Time", "DateTime", "操作时间", "时间"
                )
                raw_json = json.dumps(
                    {k: (v if isinstance(v, (str, int, float, type(None))) else str(v))
                     for k, v in item.items()},
                    ensure_ascii=False,
                    default=str,
                )[:4000]
                cur = db.execute(
                    "INSERT OR IGNORE INTO legacy_operator_actions "
                    "(source_id, source_table, gonghao, op_name, action, target, happened_at, raw_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(src_id), table_name, gonghao, op_name, action, target, happened_at, raw_json),
                )
                if cur and getattr(cur, "rowcount", 0) > 0:
                    imported += 1
                else:
                    skipped += 1
            except Exception:
                errors += 1
            if progress_cb and i % 200 == 0:
                progress_cb(i, len(rows))
        return {"imported": imported, "skipped": skipped, "errors": errors}



class ImportWorker(QThread):
    """后台线程：执行数据导入"""
    progress = QtSignal(int, int)  # current, total
    log = QtSignal(str)
    finished = QtSignal(dict)

    def __init__(self, db_path: str, import_config: Dict):
        super().__init__()
        self.db_path = db_path
        self.config = import_config

    def run(self):
        results = {}
        legacy, _dtype, open_msg = open_readonly_legacy_db(self.db_path)
        if not legacy:
            results["error"] = open_msg
            self.finished.emit(results)
            return
        try:
            items = list(self.config.get("imports", []))
            rank = {"rooms": 0, "guests": 1, "orders": 2}
            items.sort(key=lambda it: (rank.get(it.get("type"), 9), it.get("table") or ""))

            for item in items:
                table = item["table"]
                mapping = item["mapping"]
                import_type = item["type"]

                self.log.emit(f"正在导入 {import_type}: 表 {table}...")

                if import_type == "rooms":
                    r = DataImporter.import_rooms(
                        legacy, table, mapping,
                        progress_cb=lambda c, t: self.progress.emit(c, t),
                    )
                elif import_type == "guests":
                    r = DataImporter.import_guests(
                        legacy, table, mapping,
                        progress_cb=lambda c, t: self.progress.emit(c, t),
                    )
                elif import_type == "orders":
                    rd = self.config.get("recent_bill_days")
                    recent_days = 120 if rd is None else int(rd)
                    r = DataImporter.import_orders(
                        legacy, table, mapping,
                        progress_cb=lambda c, t: self.progress.emit(c, t),
                        recent_days=recent_days,
                    )
                else:
                    r = {"imported": 0, "skipped": 0, "errors": 0}

                results[import_type] = r
                extra = ""
                if import_type == "orders" and int(r.get("ledger_imported", 0) or 0):
                    extra = f", 账本 {r['ledger_imported']} 笔"
                self.log.emit(f"  {import_type}: 导入 {r.get('imported',0)}, "
                    f"跳过 {r.get('skipped',0)}, 错误 {r.get('errors',0)}{extra}"
                )

        except Exception as e:
            results["error"] = str(e)
        finally:
            legacy.close()

        self.finished.emit(results)


# ================================================================
# 向导式迁移对话框
# ================================================================
class LegacyMigrationWizard(QWizard):
    """分步向导：找文件 → 打开 → 核对 → 导入，一般工作人员不用。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("其它旧软件 · 分步导入，一般不用")
        style_wizard(self, size="large")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        # 共享数据
        self.scanned_dbs: List[Dict] = []
        self.selected_db: Optional[Dict] = None
        self.cracked_tables: Dict = {}
        self.field_mappings: Dict[str, Dict[str, str]] = {}
        self.import_config: Dict = {"imports": []}

        # 添加页面
        self.page1 = ScanPage(self)
        self.page2 = CrackPage(self)
        self.page3 = MappingPage(self)
        self.page4 = ImportPage(self)

        self.addPage(self.page1)
        self.addPage(self.page2)
        self.addPage(self.page3)
        self.addPage(self.page4)


# ================================================================
# 第1页：扫描
# ================================================================
class ScanPage(QWizardPage):
    def __init__(self, wizard: LegacyMigrationWizard):
        super().__init__()
        self.wizard = wizard
        self.setTitle("第1步：找到旧软件里的数据")
        self.setSubTitle(
            "请选择旧软件的安装文件夹，点开始扫描。\n"
            "系统会自动找出里面的数据文件，您只需选中列表里最大的那一个。"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self._guide_panel = MigrationGuidePanel()
        self._guide_panel.bind_session(legacy_wizard_page_session(0))
        layout.addWidget(self._guide_panel)

        # 扫描范围
        grp = QGroupBox("扫描范围")
        g_ly = QVBoxLayout(grp)

        self.rb_all = QRadioButton("全盘扫描（推荐）— 遍历所有盘符")
        self.rb_all.setChecked(True)
        g_ly.addWidget(self.rb_all)

        self.rb_custom = QRadioButton("指定目录")
        g_ly.addWidget(self.rb_custom)

        h_ly = QHBoxLayout()
        self.txt_custom_path = QLineEdit()
        self.txt_custom_path.setPlaceholderText("例如盘符:\HotelSystem")
        self.txt_custom_path.setEnabled(False)
        h_ly.addWidget(self.txt_custom_path)
        btn_browse = QPushButton("浏览...")
        btn_browse.setObjectName("FdGhostBtn")
        btn_browse.clicked.connect(self._browse)
        h_ly.addWidget(btn_browse)
        g_ly.addLayout(h_ly)

        self.rb_custom.toggled.connect(
            lambda checked: self.txt_custom_path.setEnabled(checked)
        )
        layout.addWidget(grp)

        # 进度
        self.lbl_status = QLabel("就绪，点击开始扫描")
        self.lbl_status.setStyleSheet(f"color:{_p('text_muted')}; font-size:12px;")
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        # 结果列表
        self.list_dbs = QListWidget()
        self.list_dbs.setAlternatingRowColors(False)
        self.list_dbs.itemClicked.connect(self._on_select)
        layout.addWidget(self.list_dbs)

        # 按钮
        btn_scan = QPushButton("🔍 开始扫描")
        btn_scan.setObjectName("SolidPrimaryBtn")
        btn_scan.clicked.connect(self._start_scan)

        self.scan_worker: Optional[ScanWorker] = None

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            self.txt_custom_path.setText(path)

    def _start_scan(self):
        self.list_dbs.clear()
        self.wizard.scanned_dbs = []
        self.lbl_status.setText("正在扫描...")
        self.progress.setValue(0)

        if self.rb_all.isChecked():
            paths = DatabaseScanner.scan_drives()
        else:
            p = self.txt_custom_path.text().strip()
            paths = [p] if p else DatabaseScanner.scan_drives()

        self.scan_worker = ScanWorker(paths)
        self.scan_worker.progress.connect(
            lambda msg: self.lbl_status.setText(msg)
        )
        self.scan_worker.found_db.connect(self._on_found)
        self.scan_worker.finished.connect(self._on_scan_done)
        self.scan_worker.start()

    def _on_found(self, db_info: Dict):
        self.wizard.scanned_dbs.append(db_info)
        item = QListWidgetItem(
            f"📁 {db_info['name']}  [{db_info['magic_type']}]  "
            f"{db_info['size_mb']}MB  —  {db_info['path']}"
        )
        item.setData(Qt.ItemDataRole.UserRole, db_info)
        self.list_dbs.addItem(item)

    def _on_scan_done(self, results):
        self.lbl_status.setText(f"扫描完成！发现 {len(results)} 个数据库文件")
        self.progress.setValue(100)
        self.completeChanged.emit()

    def _on_select(self, item):
        self.wizard.selected_db = item.data(Qt.ItemDataRole.UserRole)

    def isComplete(self) -> bool:
        return len(self.wizard.scanned_dbs) > 0

    def nextId(self) -> int:
        return 1


# ================================================================
# 第2页：破解
# ================================================================
class CrackPage(QWizardPage):
    def __init__(self, wizard: LegacyMigrationWizard):
        super().__init__()
        self.wizard = wizard
        self.setTitle("第2步：打开并查看旧数据")
        self.setSubTitle(
            "点开始读取。系统会尝试打开上一步选中的文件，并列出里面的房间、客人等表格名称。\n"
                    "若提示要安装 Access 引擎，请按前台门锁对接里的同样方法安装。"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        gp = MigrationGuidePanel()
        gp.bind_session(legacy_wizard_page_session(1))
        layout.addWidget(gp)

        self.lbl_db_info = QLabel("未选择数据库")
        self.lbl_db_info.setStyleSheet(
            f"background:{_p('surface_alt')}; padding:8px; border-radius:6px; font-size:12px;"
        )
        self.lbl_db_info.setWordWrap(True)
        layout.addWidget(self.lbl_db_info)

        self.lbl_status = QLabel("")
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        self.txt_tables = QTextEdit()
        self.txt_tables.setReadOnly(True)
        self.txt_tables.setPlaceholderText("读取成功后，这里会显示所有表名和字段...")
        layout.addWidget(self.txt_tables)

        btn_crack = QPushButton("📂 开始读取")
        btn_crack.setObjectName("SolidPrimaryBtn")
        btn_crack.clicked.connect(self._start_crack)

        self.crack_worker: Optional[CrackWorker] = None

    def initializePage(self):
        db = self.wizard.selected_db
        if db:
            self.lbl_db_info.setText(
                f"目标文件: {db['name']}\n"
                f"路径: {db['path']}\n"
                f"类型: {db['magic_type']}  |  大小: {db['size_mb']}MB"
            )

    def _start_crack(self):
        db = self.wizard.selected_db
        if not db:
            return

        self.txt_tables.clear()
        self.lbl_status.setText("正在读取...")

        self.crack_worker = CrackWorker(db["path"], db["magic_type"])
        self.crack_worker.progress.connect(lambda msg: self.lbl_status.setText(msg))
        self.crack_worker.finished.connect(self._on_crack_done)
        self.crack_worker.start()

    def _on_crack_done(self, result: Dict):
        if result["ok"]:
            self.wizard.cracked_tables = result.get("tables", {})
            self.lbl_status.setText(f"✅ 读取成功！{result.get('msg', '')}")

            # 显示表结构
            lines = []
            for tname, tinfo in result.get("tables", {}).items():
                if isinstance(tinfo, dict) and "columns" in tinfo:
                    cols = ", ".join(
                        f"{c['name']}({c['type']})" for c in tinfo["columns"]
                    )
                    purpose = SchemaAnalyzer.guess_table_purpose(
                        tname, tinfo.get("column_names", [])
                    )
                    lines.append(
                        f"📋 {tname} [{purpose}] — {tinfo['row_count']} 行\n"
                        f"   字段: {cols}"
                    )
                else:
                    lines.append(f"📋 {tname}: {tinfo}")
            self.txt_tables.setText("\n\n".join(lines))
        else:
            self.lbl_status.setText(f"❌ 读取失败: {result.get('error', '')}")

        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return len(self.wizard.cracked_tables) > 0

    def nextId(self) -> int:
        return 2


# ================================================================
# 第3页：映射
# ================================================================
class MappingPage(QWizardPage):
    def __init__(self, wizard: LegacyMigrationWizard):
        super().__init__()
        self.wizard = wizard
        self.setTitle("第3步：核对房间、客人怎么对应")
        self.setSubTitle(
            "选好一张表，点自动映射，再点添加到导入列表。\n"
            "一般选房间表导入房间，选客人表或入住表导入在住客人。不懂可让售后远程协助。"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        gp = MigrationGuidePanel()
        gp.bind_session(legacy_wizard_page_session(2))
        layout.addWidget(gp)

        self.lbl_auto = QLabel("")
        self.lbl_auto.setWordWrap(True)
        self.lbl_auto.setStyleSheet(
            f"background:{_p('surface_alt')}; padding:8px; border-radius:6px; font-size:12px;"
        )
        layout.addWidget(self.lbl_auto)

        # 表选择
        h_ly = QHBoxLayout()
        h_ly.addWidget(QLabel("选择要导入的表:"))
        self.cmb_table = QComboBox()
        self.cmb_table.currentTextChanged.connect(self._on_table_changed)
        h_ly.addWidget(self.cmb_table, 1)
        layout.addLayout(h_ly)

        # 导入类型
        h_ly2 = QHBoxLayout()
        h_ly2.addWidget(QLabel("导入为:"))
        self.cmb_import_type = QComboBox()
        self.cmb_import_type.addItems(["rooms", "guests", "orders"])
        h_ly2.addWidget(self.cmb_import_type, 1)
        layout.addLayout(h_ly2)

        # 映射表
        self.tbl_mapping = QTableWidget()
        self.tbl_mapping.setColumnCount(3)
        from brand_config_v4 import APP_NAME
        self.tbl_mapping.setHorizontalHeaderLabels(["旧系统字段", "→", f"{APP_NAME} 字段"])
        self.tbl_mapping.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.tbl_mapping.setAlternatingRowColors(False)
        fd_apply_table_palette(self.tbl_mapping)
        layout.addWidget(self.tbl_mapping)

        # 按钮
        h_btn = QHBoxLayout()
        btn_auto = QPushButton("🪄 自动映射")
        btn_auto.setObjectName("FdGhostBtn")
        btn_auto.clicked.connect(self._auto_map)
        h_btn.addWidget(btn_auto)

        btn_add = QPushButton("➕ 添加到导入列表")
        btn_add.setObjectName("SolidPrimaryBtn")
        btn_add.clicked.connect(self._add_to_imports)
        h_btn.addWidget(btn_add)
        layout.addLayout(h_btn)

        # 已添加列表
        self.lbl_added = QLabel("已添加: 无")
        self.lbl_added.setStyleSheet(f"color:{_p('text_muted')}; font-size:11px;")
        layout.addWidget(self.lbl_added)

    def initializePage(self):
        tables = self.wizard.cracked_tables
        self.cmb_table.clear()
        for tname in tables:
            if isinstance(tables[tname], dict) and "columns" in tables[tname]:
                self.cmb_table.addItem(tname)

        # CardLock / proUSB：自动填充推荐导入列表，减少现场手工映射
        db_path = ""
        if self.wizard.selected_db:
            db_path = (self.wizard.selected_db.get("path") or "").lower()
        if "cardlock" in db_path or "门锁" in db_path or "prousb" in db_path:
            plan = suggest_cardlock_import_plan(tables)
            if plan:
                self.wizard.import_config["imports"] = list(plan)
                names = ", ".join(f"{p['type']}:{p['table']}" for p in plan)
                self.lbl_added.setText(f"已加载门锁推荐导入: {names}")

        self._on_table_changed(self.cmb_table.currentText())

    def _on_table_changed(self, tname: str):
        if not tname:
            return
        tables = self.wizard.cracked_tables
        tinfo = tables.get(tname, {})
        if isinstance(tinfo, dict) and "columns" in tinfo:
            purpose = SchemaAnalyzer.guess_table_purpose(
                tname, tinfo.get("column_names", [])
            )
            self.lbl_auto.setText(
                f"表: {tname}  |  推测用途: {purpose}  |  "
                f"{tinfo.get('row_count', 0)} 行数据"
            )
            # 自动推荐导入类型
            if purpose == "房间表":
                self.cmb_import_type.setCurrentText("rooms")
            elif purpose in ("客人表", "入住记录表"):
                self.cmb_import_type.setCurrentText("guests")
            elif purpose in ("账单/消费表",):
                self.cmb_import_type.setCurrentText("orders")

    def _auto_map(self):
        tname = self.cmb_table.currentText()
        if not tname:
            return
        tables = self.wizard.cracked_tables
        tinfo = tables.get(tname, {})
        if not isinstance(tinfo, dict):
            return

        columns = tinfo.get("column_names", [])
        mapping = SchemaAnalyzer.auto_map_fields(columns)

        self.tbl_mapping.setRowCount(len(columns))
        for i, col in enumerate(columns):
            self.tbl_mapping.setItem(i, 0, QTableWidgetItem(col))
            self.tbl_mapping.setItem(i, 1, QTableWidgetItem("→"))
            mapped = mapping.get(col, "（跳过）")
            cmb = QComboBox()
            cmb.addItems([
                "（跳过）", "room_id", "floor", "room_type", "status",
                "guest_name", "phone", "checkin_time", "checkout_time",
                "id_card", "deposit", "price", "consumption", "bill_date",
            ])
            if mapped != "（跳过）":
                idx = cmb.findText(mapped)
                if idx >= 0:
                    cmb.setCurrentIndex(idx)
            self.tbl_mapping.setCellWidget(i, 2, cmb)

    def _add_to_imports(self):
        tname = self.cmb_table.currentText()
        import_type = self.cmb_import_type.currentText()

        mapping = {}
        for i in range(self.tbl_mapping.rowCount()):
            src = self.tbl_mapping.item(i, 0).text()
            dst_widget = self.tbl_mapping.cellWidget(i, 2)
            if isinstance(dst_widget, QComboBox):
                dst = dst_widget.currentText()
                if dst != "（跳过）":
                    mapping[src] = dst

        self.wizard.import_config["imports"].append({
            "table": tname,
            "type": import_type,
            "mapping": mapping,
        })

        added = [f"{i['table']} → {i['type']}" for i in self.wizard.import_config["imports"]]
        self.lbl_added.setText("已添加: " + ", ".join(added))
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return len(self.wizard.import_config.get("imports", [])) > 0

    def nextId(self) -> int:
        return 3


# ================================================================
# 第4页：导入
# ================================================================
class ImportPage(QWizardPage):
    def __init__(self, wizard: LegacyMigrationWizard):
        super().__init__()
        self.wizard = wizard
        self.setTitle("第4步：导入到我们系统")
        self.setSubTitle("确认上面列表无误后，点开始导入。完成后回前台正常入住、开卡即可。")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        gp = MigrationGuidePanel()
        gp.bind_session(legacy_wizard_page_session(3))
        layout.addWidget(gp)

        # 摘要
        self.txt_summary = QTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setMaximumHeight(150)
        layout.addWidget(self.txt_summary)

        grp_recent = QGroupBox("📅 近期营业数据")
        ryl = QHBoxLayout(grp_recent)
        ryl.addWidget(QLabel("仅导入最近 N 天的消费账单（账单映射中需有账单日期；零表示不限制）:"))
        self.spin_recent_bills = QSpinBox()
        self.spin_recent_bills.setRange(0, 730)
        self.spin_recent_bills.setValue(120)
        ryl.addWidget(self.spin_recent_bills)
        ryl.addStretch()
        layout.addWidget(grp_recent)

        # 门锁接管
        grp_lock = QGroupBox("🔑 门锁系统接管（可选）")
        lock_ly = QVBoxLayout(grp_lock)

        self.lbl_nfc_status = QLabel("点击检测读卡器检查设备")
        lock_ly.addWidget(self.lbl_nfc_status)

        h_lock = QHBoxLayout()
        btn_detect = QPushButton("📡 检测读卡器")
        btn_detect.setObjectName("FdGhostBtn")
        btn_detect.clicked.connect(self._detect_reader)
        h_lock.addWidget(btn_detect)

        btn_crack_card = QPushButton("🗝️ 破解房卡密钥")
        btn_crack_card.setObjectName("SolidPrimaryBtn")
        btn_crack_card.clicked.connect(self._crack_card)
        btn_crack_card.setEnabled(False)
        h_lock.addWidget(btn_crack_card)
        lock_ly.addLayout(h_lock)

        self.lbl_card_result = QLabel("")
        self.lbl_card_result.setWordWrap(True)
        lock_ly.addWidget(self.lbl_card_result)

        layout.addWidget(grp_lock)

        # 进度
        self.lbl_status = QLabel("")
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(200)
        layout.addWidget(self.txt_log)

        btn_import = QPushButton("🚀 开始导入到我们系统")
        btn_import.setObjectName("SolidPrimaryBtn")
        btn_import.clicked.connect(self._start_import)
        layout.addWidget(btn_import)

        self.import_worker: Optional[ImportWorker] = None
        self._reader_available = False

    def initializePage(self):
        config = self.wizard.import_config
        lines = ["即将导入以下数据:\n"]
        for item in config.get("imports", []):
            lines.append(
                f"  • 表 [{item['table']}] → {item['type']} "
                f"({len(item.get('mapping', {}))} 个字段映射)"
            )
        self.txt_summary.setText("\n".join(lines))

    def _detect_reader(self):
        ok, msg = MifareKeyExtractor.check_nfc_reader()
        self._reader_available = ok
        self.lbl_nfc_status.setText(msg)
        if ok:
            self.findChild(QPushButton, "🗝️ 破解房卡密钥").setEnabled(True)

    def _crack_card(self):
        self.lbl_card_result.setText("正在破解房卡... 请将有效房卡放在读卡器上")
        QApplication.processEvents()

        result = MifareKeyExtractor.crack_with_mfoc()
        if result["ok"]:
            self.lbl_card_result.setText(
                f"✅ 破解成功！\n"
                f"发现密钥: {len(result.get('keys_found', []))} 个\n"
                f"Dump 文件: {result.get('dump_file', '')}"
            )
            # 分析转储
            analysis = MifareKeyExtractor.analyze_card_dump(result["dump_file"])
            if analysis["ok"]:
                extra = []
                if analysis.get("possible_room_number"):
                    extra.append(f"推测房号: {analysis['possible_room_number']}")
                if analysis.get("possible_dates"):
                    extra.append(f"日期: {', '.join(analysis['possible_dates'][:3])}")
                self.lbl_card_result.setText(
                    self.lbl_card_result.text() + "\n" + "\n".join(extra)
                )
        else:
            self.lbl_card_result.setText(f"❌ 破解失败: {result.get('error', '')}")

    def _start_import(self):
        db = self.wizard.selected_db
        if not db:
            return

        self.txt_log.clear()
        self.lbl_status.setText("正在迁移...")

        cfg = dict(self.wizard.import_config)
        cfg["recent_bill_days"] = int(self.spin_recent_bills.value())

        self.import_worker = ImportWorker(db["path"], cfg)
        self.import_worker.progress.connect(
            lambda c, t: self.progress.setValue(int(c / max(t, 1) * 100))
        )
        self.import_worker.log.connect(
            lambda msg: self.txt_log.append(msg)
        )
        self.import_worker.finished.connect(self._on_import_done)
        self.import_worker.start()

    def _on_import_done(self, results: Dict):
        if "error" in results:
            self.lbl_status.setText(f"❌ 迁移出错: {results['error']}")
        else:
            total_imported = sum(
                r.get("imported", 0) for r in results.values()
                if isinstance(r, dict)
            )
            total_ledger = sum(
                int(r.get("ledger_imported", 0) or 0)
                for r in results.values()
                if isinstance(r, dict)
            )
            msg = f"✅ 迁移完成！共导入 {total_imported} 条记录"
            self.lbl_status.setText(msg)
            if total_ledger:
                msg += f"，账本 {total_ledger} 笔（LEGACY_IMPORT）"
            self.lbl_status.setText(msg)
            self.txt_log.append(f"\n===== 迁移完成 =====")
            bus.show_success_overlay.emit(f"老系统迁移完成: {total_imported} 条")

        self.progress.setValue(100)
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self.progress.value() >= 100


# ================================================================
# 快速入口对话框（从数据导入服务调用）
# ================================================================
def open_legacy_migration_wizard(parent=None):
    """打开其它旧软件分步导入向导，一般不用。"""
    wizard = LegacyMigrationWizard(parent)
    wizard.exec()