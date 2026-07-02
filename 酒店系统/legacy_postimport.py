"""legacy_postimport.py — 接管完老门锁系统后，一键把旧门锁数据库里所有
营业相关数据全量导入系统。

调用方：
  - 门锁接管页面接管成功之后
  - 前台接管工作线程
  - 设置页面 → 老系统迁移
  - 配置向导（房间录入步骤）

涉及系统表：
  rooms / buildings           ← 房间信息
  guests                      ← 客人信息（仅未退房）
  card_records                ← 卡片信息（去重）
  blank_card_registry         ← 空白卡
  legacy_operator_permissions ← 操作员信息
  legacy_open_records         ← 开门记录
  legacy_operator_actions     ← 操作日志

幂等：所有导入器都是忽略或替换，重复跑无副作用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional


def _open_legacy_db(path: str):
    """打开老库（mdb、db、accdb），返回连接和消息。"""
    from legacy_migration import open_readonly_legacy_db

    legacy, _kind, msg = open_readonly_legacy_db(str(path))
    return legacy, msg


def _table_exists(legacy, name: str) -> bool:
    try:
        rows, _ = legacy.fetch_table(name)
        return True
    except Exception:
        return False


def _backfill_lock_no_from_roominfo(legacy) -> int:
    """从房间信息反算锁号，回填给现有房间表里锁号为空的房间。"""
    try:
        from database import db
        from lock_adapters.prousb_v9 import ProUsbV9Adapter
    except Exception:
        return 0
    try:
        rows, cols = legacy.fetch_table("RoomInfo")
    except Exception:
        return 0
    updated = 0
    for raw in rows:
        item = dict(zip(cols, raw))
        room_no = str(item.get("RoomNo", "")).strip()
        if not room_no:
            continue
        try:
            lock_no = ProUsbV9Adapter.lock_no_from_roominfo_row(item)
        except Exception:
            lock_no = ""
        if not lock_no:
            continue
        try:
            db.execute(
                "UPDATE rooms SET lock_no=? WHERE room_id=? AND (lock_no IS NULL OR lock_no='')",
                (lock_no, room_no),
            )
            changed = db.execute("SELECT changes()").fetchone()[0] or 0
            updated += int(changed)
        except Exception:
            continue
    return updated


def _import_ckcard(legacy, mdb_path: str) -> int:
    """老空白卡片表 → 空白卡注册表。复用批量创建对话框里那段。"""
    try:
        from database import db
    except Exception:
        return 0
    if not _table_exists(legacy, "CKCard"):
        return 0
    try:
        rows, cols = legacy.fetch_table("CKCard")
    except Exception:
        return 0
    n = 0
    for raw in rows:
        item = dict(zip(cols, raw))
        uid = str(item.get("CardUID") or "").strip().upper()
        data = str(item.get("CardData") or "").strip().upper()
        if not uid:
            continue
        try:
            db.execute(
                "INSERT OR REPLACE INTO blank_card_registry "
                "(card_uid, card_data, source, note) VALUES (?, ?, 'legacy_ckcard', ?)",
                (uid, data, str(mdb_path)),
            )
            n += 1
        except Exception:
            continue
    return n


def run_full_legacy_import(
    mdb_path: str,
    *,
    options: Optional[Dict[str, bool]] = None,
    progress_cb=None,
) -> Dict[str, Any]:
    """对单个老库执行全套导入。

    options 默认全开：
      rooms / guests / cards / operators / open_records / actions / ckcard / backfill_lockno

    返回字典：每个步骤的导入数、跳过数、错误数，以及成功状态和错误信息。
    """
    options = dict(options or {})
    flags = {
        "rooms":           options.get("rooms", True),
        "guests":          options.get("guests", True),
        "cards":           options.get("cards", True),
        "operators":       options.get("operators", True),
        "open_records":    options.get("open_records", True),
        "actions":         options.get("actions", True),
        "ckcard":          options.get("ckcard", True),
        "backfill_lockno": options.get("backfill_lockno", True),
    }
    summary: Dict[str, Any] = {"ok": False, "path": str(mdb_path), "steps": {}}

    legacy, msg = _open_legacy_db(mdb_path)
    if not legacy:
        summary["error"] = f"无法打开老库: {mdb_path}: {msg}"
        return summary

    try:
        from legacy_migration import DataImporter
    except Exception as exc:
        summary["error"] = f"加载迁移模块失败: {exc}"
        return summary

    step = summary["steps"]

    if flags["rooms"] and _table_exists(legacy, "RoomInfo"):
        if progress_cb:
            progress_cb("rooms", "开始导入房间")
        try:
            mapping = {
                "RoomNo": "room_id",
                "FlrNo": "floor",
                "RoomType": "room_type",
                "Status": "status",
                "Price": "price",
            }
            step["rooms"] = DataImporter.import_rooms(legacy, "RoomInfo", mapping)
        except Exception as exc:
            step["rooms"] = {"imported": 0, "skipped": 0, "errors": 1, "error": str(exc)}

    if flags["guests"] and _table_exists(legacy, "GuestInfo"):
        if progress_cb:
            progress_cb("guests", "开始导入在住客人")
        try:
            mapping = {
                "RoomNo": "room_id", "GuestName": "guest_name", "Name": "guest_name",
                "IDNo": "id_card", "Phone": "phone",
                "CheckInTime": "checkin_time", "CheckOutTime": "checkout_time",
                "Deposit": "deposit",
            }
            step["guests"] = DataImporter.import_guests(legacy, "GuestInfo", mapping)
        except Exception as exc:
            step["guests"] = {"imported": 0, "skipped": 0, "errors": 1, "error": str(exc)}

    if flags["cards"] and _table_exists(legacy, "CardInfo"):
        if progress_cb:
            progress_cb("cards", "开始导入发卡台账")
        try:
            step["cards"] = DataImporter.import_card_registry(legacy, "CardInfo")
        except Exception as exc:
            step["cards"] = {"imported": 0, "skipped": 0, "errors": 1, "error": str(exc)}

    if flags["operators"] and _table_exists(legacy, "OperatorInfo"):
        if progress_cb:
            progress_cb("operators", "开始导入操作员")
        try:
            step["operators"] = DataImporter.import_operators(legacy, "OperatorInfo")
        except Exception as exc:
            step["operators"] = {"imported": 0, "skipped": 0, "errors": 1, "error": str(exc)}

    if flags["open_records"]:
        if progress_cb:
            progress_cb("open_records", "开始导入开门记录")
        total = {"imported": 0, "skipped": 0, "errors": 0}
        for tname in ("RecordOpen", "OpenRecord"):
            if not _table_exists(legacy, tname):
                continue
            try:
                r = DataImporter.import_open_records(legacy, tname)
                for k in ("imported", "skipped", "errors"):
                    total[k] = total[k] + int(r.get(k, 0) or 0)
            except Exception as exc:
                total["errors"] += 1
                total["error"] = str(exc)
        step["open_records"] = total

    if flags["actions"]:
        if progress_cb:
            progress_cb("actions", "开始导入操作员行为")
        total = {"imported": 0, "skipped": 0, "errors": 0}
        for tname in ("CaoZuoInfo", "LogInfo"):
            if not _table_exists(legacy, tname):
                continue
            try:
                r = DataImporter.import_operator_actions(legacy, tname)
                for k in ("imported", "skipped", "errors"):
                    total[k] = total[k] + int(r.get(k, 0) or 0)
            except Exception as exc:
                total["errors"] += 1
                total["error"] = str(exc)
        step["actions"] = total

    if flags["ckcard"]:
        if progress_cb:
            progress_cb("ckcard", "开始导入空白卡")
        try:
            n = _import_ckcard(legacy, str(mdb_path))
            step["ckcard"] = {"imported": n, "skipped": 0, "errors": 0}
        except Exception as exc:
            step["ckcard"] = {"imported": 0, "skipped": 0, "errors": 1, "error": str(exc)}

    if flags["backfill_lockno"]:
        if progress_cb:
            progress_cb("backfill_lockno", "回填房间锁号")
        try:
            n = _backfill_lock_no_from_roominfo(legacy)
            step["backfill_lockno"] = {"imported": n, "skipped": 0, "errors": 0}
        except Exception as exc:
            step["backfill_lockno"] = {"imported": 0, "skipped": 0, "errors": 1, "error": str(exc)}

    try:
        legacy.close()
    except Exception:
        pass

    # 汇总
    total_imported = sum(int((v or {}).get("imported", 0) or 0) for v in step.values())
    summary["total_imported"] = total_imported
    summary["ok"] = True
    return summary


def discover_legacy_mdb_for_takeover() -> Optional[Path]:
    """根据接管配置选定一个最佳老库路径，找不到返回 None。

    优先级：
      活跃库路径
      → 接管时记录的门锁数据库路径
      → 安装目录下的门锁数据库
    """
    try:
        from lock_deploy.importer import load_takeover_config

        cfg = load_takeover_config() or {}
    except Exception:
        cfg = {}
    for key in ("lock_takeover_live_mdb_path", "lock_takeover_mdb_path"):
        raw = cfg.get(key) or ""
        if raw and Path(raw).is_file():
            return Path(raw)
    install = cfg.get("lock_takeover_install_dir") or ""
    if install:
        guess = Path(install) / "CardLock.mdb"
        if guess.is_file():
            return guess
    return None


def format_summary(result: Dict[str, Any]) -> str:
    """把 run_full_legacy_import 的结果格式化成中文清单。"""
    if not result or not result.get("ok"):
        return f"❌ 老系统导入失败：{result.get('error') or '未知错误'}"
    label_map = {
        "rooms":           "房间",
        "guests":          "在住客人",
        "cards":           "发卡台账",
        "operators":       "操作员",
        "open_records":    "开门记录",
        "actions":         "操作员行为",
        "ckcard":          "空白卡",
        "backfill_lockno": "锁号回填",
    }
    lines = [f"✅ 老库已对接：{result.get('path')}"]
    for key, label in label_map.items():
        info = result.get("steps", {}).get(key)
        if not info:
            continue
        imp = int(info.get("imported", 0) or 0)
        skp = int(info.get("skipped", 0) or 0)
        err = int(info.get("errors", 0) or 0)
        line = f"  • {label}: 导入 {imp}"
        if skp:
            line += f" / 跳过 {skp}"
        if err:
            line += f" / 错误 {err}"
        if info.get("error"):
            line += f"（{info['error']}）"
        lines.append(line)
    lines.append(f"合计写入 {result.get('total_imported', 0)} 条记录。")
    return "\n".join(lines)
