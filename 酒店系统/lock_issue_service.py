"""
发卡服务模块 — 接管后统一交 ProUsbV9Adapter 处理。

前台（`cardlock_frontdesk.py`）在门锁诊断页批量验证通过后调用这些辅助函数。
包含写卡后回读校验（看门狗）。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Optional

from lock_adapters.base import CardResult
from lock_adapters.prousb_v9 import ProUsbV9Adapter, format_date
from lock_legacy_bridge import normalize_lock_no_hex

def _log(hypothesis_id: str, message: str, data: dict[str, Any]) -> None:
    return


def _log_786b36(hypothesis_id: str, message: str, data: dict[str, Any]) -> None:
    return


def _adapter() -> Optional[ProUsbV9Adapter]:
    """获取 V9 动态库直调适配器（首次尝试）。"""
    try:
        from lock_deploy.importer import get_active_adapter

        ad = get_active_adapter()
        return ad if isinstance(ad, ProUsbV9Adapter) else None
    except Exception:
        return None


def _any_adapter() -> Optional[Any]:
    """获取任意已注册的适配器实例（V9 或 pywinauto 降级）。"""
    try:
        from lock_deploy.importer import get_active_adapter
        return get_active_adapter()
    except Exception:
        return None


def takeover_configured() -> bool:
    try:
        from lock_deploy.importer import load_takeover_config

        cfg = load_takeover_config() or {}
        return bool(cfg.get("lock_takeover_install_dir"))
    except Exception:
        return False


def _candidate_mdb_paths() -> list[Path]:
    try:
        from lock_deploy.importer import load_takeover_config

        cfg = load_takeover_config() or {}
    except Exception:
        cfg = {}
    out: list[Path] = []
    for key in ("lock_takeover_live_mdb_path", "lock_takeover_mdb_path"):
        raw = cfg.get(key) or ""
        if raw:
            out.append(Path(raw))
    install = cfg.get("lock_takeover_install_dir") or ""
    if install:
        out.append(Path(install) / "CardLock.mdb")
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        s = str(p)
        if s not in seen:
            seen.add(s)
            uniq.append(p)
    return uniq


def _normalize_lock_no_hex(raw: str) -> str:
    return normalize_lock_no_hex(raw)


def _solid_room_row(room_no: str) -> Optional[dict[str, Any]]:
    """从房间表取房号、楼栋、楼层、已绑定锁号。"""
    target = (room_no or "").strip()
    if not target:
        return None
    try:
        from database import db

        row = db.execute(
            "SELECT room_id, COALESCE(floor,''), COALESCE(building,''), COALESCE(lock_no,''), "
            "COALESCE(bld_no, 0), COALESCE(flr_no, 0), COALESCE(rom_id, 0), COALESCE(dai, 0) "
            "FROM rooms WHERE room_id=?",
            (target,),
        ).fetchone()
    except Exception as exc:
        _log_786b36("H6", "Solid 房间查询失败", {"room_no": target, "error": str(exc)})
        return None
    if not row:
        return None
    return {
        "room_id": row[0],
        "floor": row[1],
        "building": row[2],
        "lock_no": row[3],
        "bld_no": row[4],
        "flr_no": row[5],
        "rom_id": row[6],
        "dai": row[7],
    }


def _lock_no_from_solid_row(solid: dict[str, Any]) -> str:
    """只接受 Solid 房间表锁号字段里的真值；空值不再自动推算。"""
    return _normalize_lock_no_hex(str(solid.get("lock_no") or ""))


def _dai_for_room(room_no: str, legacy: Optional[dict[str, Any]] = None) -> int:
    """房间信息中的发卡参数是老系统发卡参数，接管后必须带进客人卡。"""
    try:
        if legacy is None:
            legacy = _legacy_room_for(room_no)
        if legacy and legacy.get("Dai") not in (None, ""):
            return int(float(legacy.get("Dai") or 0))
    except Exception:
        pass
    try:
        solid = _solid_room_row(room_no)
        if solid and solid.get("dai") not in (None, ""):
            return int(float(solid.get("dai") or 0))
    except Exception:
        pass
    return 0


def _resolve_lock_no_for_frontdesk(room_no: str) -> tuple[Optional[str], str, dict[str, Any]]:
    """
    解析前台发卡用的 8 位锁号。
    顺序：旧数据库房间信息 → Solid 房间表锁号。空锁号必须先补全，不能自动推算。
    """
    target = (room_no or "").strip()
    debug: dict[str, Any] = {"room_no": target}

    legacy = _legacy_room_for(target)
    if legacy:
        lock_no = ProUsbV9Adapter.lock_no_from_roominfo_row(legacy)
        dai = _dai_for_room(target, legacy)
        debug["legacy_room"] = {
            "RoomNo": legacy.get("RoomNo"),
            "BldNo": legacy.get("BldNo"),
            "FlrNo": legacy.get("FlrNo"),
            "RomID": legacy.get("RomID"),
            "Dai": dai,
        }
        return lock_no, "mdb", debug

    solid = _solid_room_row(target)
    debug["solid_found"] = solid is not None
    if not solid:
        return None, "none", debug

    debug["solid"] = solid
    lock_no = _lock_no_from_solid_row(solid)
    if lock_no:
        debug["lock_no"] = lock_no
        debug["source"] = "solid_lock_no"
        debug["dai"] = _dai_for_room(target)
        return lock_no, "solid_lock_no", debug
    debug["lock_no"] = ""
    debug["source"] = "missing"
    return None, "missing", debug


def _legacy_room_for(room_no: str) -> Optional[dict[str, Any]]:
    target = (room_no or "").strip()
    if not target:
        return None
    for mdb in _candidate_mdb_paths():
        if not mdb.is_file():
            continue
        try:
            from mdb_import_backend import open_mdb_via_sqlite_cache

            conn, _msg = open_mdb_via_sqlite_cache(str(mdb))
            if conn is None:
                continue
            try:
                rows, cols = conn.fetch_table("RoomInfo")
            finally:
                conn.close()
            dicts = [dict(zip(cols, row)) for row in rows]
            exact = [r for r in dicts if str(r.get("RoomNo", "")).strip() == target]
            if exact:
                row = exact[0]
            else:
                slim = target.lstrip("0") or target
                suffix = [
                    r for r in dicts
                    if str(r.get("RoomNo", "")).strip().lstrip("0").endswith(slim)
                ]
                row = suffix[0] if len(suffix) == 1 else None
            _log(
                "H2",
                "查询旧系统房号",
                {
                    "room_no": target,
                    "mdb": str(mdb),
                    "found": row is not None,
                    "legacy_room": {
                        "RoomNo": row.get("RoomNo"),
                        "BldNo": row.get("BldNo"),
                        "FlrNo": row.get("FlrNo"),
                        "RomID": row.get("RomID"),
                    } if row else {},
                },
            )
            if row:
                return row
        except Exception as exc:
            _log("H2", "查询旧系统房号失败", {"room_no": target, "mdb": str(mdb), "error": str(exc)})
    return None


def _watchdog(fn_name: str, result: CardResult, payload: str) -> CardResult:
    if not result.success:
        return result
    ok, msg = ProUsbV9Adapter.validate_payload(payload or result.card_hex or "", fn_name)
    if not ok:
        return CardResult.fail(f"写卡后校验失败: {msg}", raw_ret=result.raw_ret)
    return result


def issue_guest_for_room(room: dict, *, hours: int = 24) -> CardResult:
    ad = _adapter()
    if ad is None:
        return CardResult.fail("未接管 proUSB 或适配器不可用")
    lock_no = ProUsbV9Adapter.lock_no_from_solid_room(room)
    now = _dt.datetime.now()
    b = format_date(now)
    e = format_date(now + _dt.timedelta(hours=hours))
    try:
        dai = int(float(room.get("dai") or 0))
    except Exception:
        dai = 0
    res = ad.issue_guest_card_direct(lock_no=lock_no, b_date=b, e_date=e, dai=dai)
    pl = res.card_hex or ad.read_card_payload() or ""
    return _watchdog("GuestCard", res, pl)


def issue_guest_for_frontdesk(room_no: str, expire_dt: _dt.datetime, card_no: int = 1,
                               seq: int = -1) -> CardResult:
    """发客人卡。序号为 -1 表示由适配器自行管理序号（如动态库内部计数）。"""
    ad = _adapter()
    if ad is None:
        _log("H1", "未找到老系统接管适配器", {"room_no": room_no})
        _log_786b36("H1", "未找到老系统接管适配器", {"room_no": room_no})
        return CardResult.fail("未接管老门锁系统或发卡器不可用")

    lock_no, source, resolve_dbg = _resolve_lock_no_for_frontdesk(room_no)
    _log_786b36("H6", "前台锁号解析", resolve_dbg)

    if not lock_no:
        solid = _solid_room_row(room_no)
        if not solid:
            return CardResult.fail(
                f"系统里没有房间 {room_no}，请先在「房态」里建好这间房并绑定锁号。"
            )
        return CardResult.fail(
            f"房间 {room_no} 还没有门锁号。\n"
            "已装老系统：请到「房态 → 添加 → 从老系统导入房间」导入真锁号。\n"
            "未装老系统：请右键该房卡「编辑锁号」，填入 8 位 hex 后再发卡。"
        )

    b = format_date(_dt.datetime.now())
    e = format_date(expire_dt)
    try:
        if not ad.is_open:
            ok = ad.initialize()
            if not ok:
                _log("H3", "发卡器初始化失败", {"room_no": room_no, "lock_no": lock_no, "error": "无法连接发卡器"})
                _log_786b36("H3", "发卡器初始化失败", {"room_no": room_no, "lock_no": lock_no, "error": "无法连接发卡器"})
                return CardResult.fail("发卡器连接失败：请检查 USB 连接和驱动。")
        kwargs = dict(lock_no=lock_no, b_date=b, e_date=e, dai=_dai_for_room(room_no), card_no=card_no)
        if seq >= 0:
            kwargs["seq"] = seq
        res = ad.issue_guest_card_direct(**kwargs)
        pl = res.card_hex or ad.read_card_payload() or ""
        checked = _watchdog("GuestCard", res, pl)
        _log(
            "H3,H4",
            "正式前台发客人卡结果",
            {
                "room_no": room_no,
                "lock_no": lock_no,
                "lock_source": source,
                "success": checked.success,
                "raw_ret": checked.raw_ret,
                "error": checked.error,
                "card_hex": checked.card_hex or pl,
            },
        )
        _log_786b36(
            "H4",
            "正式前台发客人卡结果",
            {
                "room_no": room_no,
                "lock_no": lock_no,
                "lock_source": source,
                "success": checked.success,
                "error": checked.error,
            },
        )
        if checked.success and not checked.card_hex:
            checked.card_hex = pl
        return checked
    except Exception as exc:
        _log("H4", "正式前台发客人卡异常", {"room_no": room_no, "lock_no": lock_no, "error": str(exc)})
        _log_786b36("H4", "正式前台发客人卡异常", {"room_no": room_no, "lock_no": lock_no, "error": str(exc)})
        return CardResult.fail(f"老门锁发卡失败：{exc}")


def issue_guest_for_frontdesk_direct(room_no: str, expire_dt: _dt.datetime) -> CardResult:
    """发客人卡 — 原生直接写卡路径。

    与普通前台发卡相同逻辑，但走高级发卡器适配
    的直写函数而非发卡函数，输入输出路径与
    CardLock.exe 一致。固件解锁后优先调用此函数。
    """
    ad = _adapter()
    if ad is None:
        _log("H1", "未找到老系统接管适配器 (direct)", {"room_no": room_no})
        _log_786b36("H1", "未找到老系统接管适配器 (direct)", {"room_no": room_no})
        return CardResult.fail("未接管老门锁系统或发卡器不可用")

    lock_no, source, resolve_dbg = _resolve_lock_no_for_frontdesk(room_no)
    _log_786b36("H6", "前台锁号解析 (direct)", resolve_dbg)

    if not lock_no:
        solid = _solid_room_row(room_no)
        if not solid:
            return CardResult.fail(
                f"系统里没有房间 {room_no}，请先在「房态」里建好这间房并绑定锁号。"
            )
        return CardResult.fail(
            f"房间 {room_no} 还没有门锁号。\n"
            "已装老系统：请到「房态 → 添加 → 从老系统导入房间」导入真锁号。\n"
            "未装老系统：请右键该房卡「编辑锁号」，填入 8 位 hex 后再发卡。"
        )

    b = format_date(_dt.datetime.now())
    e = format_date(expire_dt)
    try:
        if not ad.is_open:
            ok = ad.initialize()
            if not ok:
                _log("H3", "发卡器初始化失败 (direct)", {"room_no": room_no, "lock_no": lock_no, "error": "无法连接发卡器"})
                _log_786b36("H3", "发卡器初始化失败 (direct)", {"room_no": room_no, "lock_no": lock_no, "error": "无法连接发卡器"})
                return CardResult.fail("发卡器连接失败：请检查 USB 连接和驱动。")
        res = ad.issue_guest_card_direct(
            lock_no=lock_no, b_date=b, e_date=e, dai=_dai_for_room(room_no)
        )
        pl = res.card_hex or ad.read_card_payload() or ""
        checked = _watchdog("GuestCard", res, pl)
        _log(
            "H3,H4",
            "正式前台发客人卡结果 (direct)",
            {
                "room_no": room_no,
                "lock_no": lock_no,
                "lock_source": source,
                "success": checked.success,
                "raw_ret": checked.raw_ret,
                "error": checked.error,
                "card_hex": checked.card_hex or pl,
            },
        )
        _log_786b36(
            "H4",
            "正式前台发客人卡结果 (direct)",
            {
                "room_no": room_no,
                "lock_no": lock_no,
                "lock_source": source,
                "success": checked.success,
                "error": checked.error,
            },
        )
        if checked.success and not checked.card_hex:
            checked.card_hex = pl
        return checked
    except Exception as exc:
        _log("H4", "正式前台发客人卡异常 (direct)", {"room_no": room_no, "lock_no": lock_no, "error": str(exc)})
        _log_786b36("H4", "正式前台发客人卡异常 (direct)", {"room_no": room_no, "lock_no": lock_no, "error": str(exc)})
        return CardResult.fail(f"老门锁发卡失败：{exc}")


def read_card_payload_via_adapter() -> tuple[bool, str]:
    """正式前台「读卡」走 V9 发卡器读 16 字节数据。返回（成功标志, 数据或错误信息）。"""
    # region agent log
    _log_786b36("H1", "进入 read_card_payload_via_adapter", {})
    # endregion
    ad = _adapter()
    if ad is None:
        # region agent log
        _log_786b36("H1", "未取得 V9 适配器（接管未完成？）", {})
        # endregion
        return False, "未接管老门锁系统或发卡器不可用"
    try:
        if not ad.is_open:
            ok = ad.initialize()
            if not ok:
                # region agent log
                _log_786b36("H2", "V9 适配器初始化失败", {"error": "无法连接发卡器"})
                # endregion
                return False, "发卡器连接失败：请检查 USB 连接和驱动。"
        payload = ad.read_card_payload()
        # region agent log
        _log_786b36("H2", "V9 读卡数据结果", {"payload": payload, "has_card": bool(payload)})
        # endregion
        if not payload:
            return False, "发卡器上没有读到卡"
        return True, str(payload)
    except Exception as exc:
        # region agent log
        _log_786b36("H4", "V9 读卡异常", {"error": str(exc)})
        # endregion
        return False, f"读卡异常：{exc}"


def cancel_card_via_adapter(card_hex: str = "") -> CardResult:
    """正式前台「卡片注销」走 V9 擦卡物理擦卡。"""
    # region agent log
    _log_786b36("H3", "进入 cancel_card_via_adapter（卡片注销）", {})
    # endregion
    ad = _adapter()
    if ad is None:
        # region agent log
        _log_786b36("H3", "未取得 V9 适配器", {})
        # endregion
        return CardResult.fail("未接管老门锁系统或发卡器不可用")
    try:
        if not ad.is_open:
            ok = ad.initialize()
            if not ok:
                return CardResult.fail("发卡器连接失败：请检查 USB 连接和驱动。")
        res = ad.erase_card(card_hex or "")
        pl = res.card_hex or ad.read_card_payload() or ""
        checked = _watchdog("BlankCard", res, pl)
        # region agent log
        _log_786b36(
            "H3",
            "V9 擦卡注销结果",
            {
                "success": checked.success,
                "raw_ret": checked.raw_ret,
                "error": checked.error,
                "card_hex": checked.card_hex or pl,
            },
        )
        # endregion
        return checked
    except Exception as exc:
        # region agent log
        _log_786b36("H4", "V9 擦卡异常", {"error": str(exc)})
        # endregion
        return CardResult.fail(f"擦卡异常：{exc}")


def diagnose_frontdesk_path() -> dict[str, Any]:
    """供 UI 启动时调用：把当前接管状态写到日志，方便调试。"""
    cfg_ok = takeover_configured()
    ad_v9 = _adapter()
    ad_any = _any_adapter()
    install_dir = ""
    try:
        from lock_deploy.importer import load_takeover_config

        cfg = load_takeover_config() or {}
        install_dir = str(cfg.get("lock_takeover_install_dir") or "")
    except Exception:
        cfg = {}
    info = {
        "takeover_configured": cfg_ok,
        "adapter_loaded": ad_any is not None,
        "adapter_class": type(ad_any).__name__ if ad_any else "",
        "adapter_v9": ad_v9 is not None,
        "adapter_v9_ok": ad_v9.is_open if ad_v9 else False,
        "install_dir": install_dir,
        "dll_path": str(cfg.get("lock_takeover_dll_path") or ""),
        "dlsCoID": str(cfg.get("lock_takeover_dlsCoID") or ""),
        "hotel_id_set": bool(cfg.get("lock_takeover_hotel_id")),
    }
    # region agent log
    _log_786b36("H1", "前台 V9/CardLockAuto 接管状态诊断", info)
    # endregion
    return info


def get_or_create_auto_adapter() -> Optional[Any]:
    """获取当前适配器，V9 优先，否则尝试 CardLockAutoAdapter 降级。

    当 V9 动态库不可用（非 proUSB 品牌、动态库缺失、或 32 位桥接无法启动）
    时，自动降级到 pywinauto CardLock.exe 寄生模式。
    """
    # 1. 首先尝试 V9 动态库直调路径
    ad_v9 = _adapter()
    if ad_v9 is not None:
        try:
            if not ad_v9.is_open:
                ad_v9.initialize()
            if ad_v9.is_open:
                return ad_v9
        except Exception:
            pass

    # 2. 尝试 CardLockAutoAdapter
    try:
        from lock_adapters.cardlock_auto import auto_takeover
        from lock_deploy.importer import load_takeover_config

        cfg = load_takeover_config() or {}
        install_dir = cfg.get("lock_takeover_install_dir")
        if not install_dir:
            return None

        auto_ad = auto_takeover(
            Path(install_dir),
            dlsCoID=int(cfg.get("lock_takeover_dlsCoID") or 0),
            hotel_id=str(cfg.get("lock_takeover_hotel_id") or ""),
            reader_adapter=ad_v9,  # 用 V9 桥接回读卡
        )
        if auto_ad is not None and auto_ad.is_open:
            return auto_ad
    except Exception as e:
        _log_786b36("H4", "CardLockAuto 降级失败", {"error": str(e)})

    return None


def issue_functional(fn: str, **kwargs: Any) -> CardResult:
    """功能卡类型：退房 | 记录 | 设房号 | 设时间 | 总卡 | ..."""
    ad = _adapter()
    if ad is None:
        return CardResult.fail("未接管 proUSB 或适配器不可用")
    now = _dt.datetime.now()
    b_src = kwargs.get("b_date")
    e_src = kwargs.get("e_date")
    b = format_date(b_src) if isinstance(b_src, _dt.datetime) else (str(b_src) if b_src else format_date(now))
    e = format_date(e_src) if isinstance(e_src, _dt.datetime) else (str(e_src) if e_src else format_date(now + _dt.timedelta(days=365)))
    mapping = {
        "checkout": ("CheckOutCard", lambda: ad.issue_check_out_card(b_date=b)),
        "record": ("RecordCard", lambda: ad.issue_record_card(b_date=b)),
        "roomset": ("RoomSetCard", lambda: ad.issue_room_no_card(lock_no=kwargs.get("lock_no", "80050301"), b_date=b)),
        "timeset": ("TimeSetCard", lambda: ad.issue_clock_card(b_date=b)),
        "loss": ("LimitCard", lambda: ad.issue_loss_report_card(l_card_no=str(kwargs.get("l_card_no") or kwargs.get("card_no") or "0001"), b_date=b)),
        "master": ("MasterCard", lambda: ad.issue_master_card(b_date=b, e_date=e)),
        "auth": ("IniCard", lambda: ad.issue_auth_card(b_date=b)),
        "building": ("BuildingCard", lambda: ad.issue_building_card(b_date=b, e_date=e, building_no=int(kwargs.get("building_no", 1)))),
        "floor": ("FloorCard", lambda: ad.issue_floor_card(b_date=b, e_date=e, building_no=int(kwargs.get("building_no", 1)), floor_no=int(kwargs.get("floor_no", 1)))),
        "emergency": ("EmergencyCard", lambda: ad.issue_emergency_card(b_date=b, e_date=e)),
        "group": ("GroupCard", lambda: ad.issue_group_card(b_date=b, e_date=e, group_no=int(kwargs.get("group_no", 1)))),
        "groupset": ("GroupSetCard", lambda: ad.issue_group_set_card(b_date=b, e_date=e, group_no=int(kwargs.get("group_no", 1)))),
        "blank": ("BlankCard", lambda: ad.issue_blank_card(count=int(kwargs.get("count", 1)))),
    }
    entry = mapping.get(fn.lower())
    if not entry:
        return CardResult.fail(f"未知功能卡: {fn}")
    fn_name, call = entry
    res = call()
    pl = res.card_hex or ad.read_card_payload() or ""
    return _watchdog(fn_name, res, pl)
