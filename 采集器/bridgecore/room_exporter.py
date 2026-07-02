"""
bridgecore/room_exporter.py — 从原厂 CardLock.mdb 导出房间/锁号/客人数据

完全独立，不依赖 PMS 任何模块。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _lock_no_from_roominfo(bld_no: int, flr_no: int, rom_id: int) -> str:
    """默认 V9 格式锁号编码。

    编码: byte0=0x80, byte1=RomID, byte2=FlrNo, byte3=BldNo
    用于无 profile 时的兼容模式。
    """
    b = max(0, min(255, int(bld_no)))
    f = max(0, min(255, int(flr_no)))
    r = max(0, min(255, int(rom_id)))
    return f"80{r:02X}{f:02X}{b:02X}"


# ── 锁号编码器注册表（与 PMS 的 payload_factory 一致，避免跨项目依赖） ──

def _encode_hex_be(lock_no: str) -> str:
    """默认 4 hex 字符 = 2 字节，大端。"""
    s = (lock_no or "").strip().upper()
    if len(s) >= 4 and all(c in "0123456789ABCDEF" for c in s[:4]):
        return s[:4]
    if len(s) >= 8 and all(c in "0123456789ABCDEF" for c in s[:8]):
        return s[4:8]
    return "0001"


def _encode_hex_le(lock_no: str) -> str:
    """4 hex 字符，小端（字节交换）。"""
    s = _encode_hex_be(lock_no)
    if len(s) == 4:
        return s[2:4] + s[0:2]
    return s


def _encode_bcd_3byte(lock_no: str) -> str:
    """3 字节 BCD 编码：锁号转为 6 位 BCD。"""
    s = (lock_no or "").strip()
    digits = "".join(c for c in s if c.isdigit())[:6].zfill(6)
    result = ""
    for i in range(0, 6, 2):
        d1 = int(digits[i])
        d2 = int(digits[i + 1])
        result += f"{(d1 << 4) + d2:02X}"
    return result


def _encode_ascii(lock_no: str) -> str:
    """ASCII 编码：原样转为 hex。"""
    s = (lock_no or "").strip()
    return s.encode().hex()[:8]


_LOCK_NO_ENCODERS = {
    "hex_be": _encode_hex_be,
    "hex_le": _encode_hex_le,
    "bcd_3byte": _encode_bcd_3byte,
    "ascii": _encode_ascii,
}


def _encode_lock_no_profile(lock_no: str, encoding: str) -> str:
    """使用 profile 指定的编码方式编码锁号。

    Args:
        lock_no: 房间号字符串。
        encoding: 编码方式（hex_be / hex_le / bcd_3byte / ascii）。

    Returns:
        编码后的 hex 字符串。
    """
    encoder = _LOCK_NO_ENCODERS.get(encoding, _encode_hex_be)
    return encoder(lock_no)


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(str(val)))
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_str(val: Any, default: str = "") -> str:
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


def export_room_data(
    install_dir: str,
    profile: Optional[dict] = None,
) -> dict[str, Any]:
    """从安装目录下找 CardLock.mdb，导出房间和客人数据。

    Args:
        install_dir: 原厂门锁安装目录。
        profile: 品牌 profile（可选）。如果提供，锁号编码从 profile 读取，
                 不再硬编码 V9 格式。编码方式支持：hex_be / hex_le / bcd_3byte / ascii。
                 如果为 None，使用默认 V9 锁号拼接。

    Returns:
        {
            "exported_at": "2026-06-12 10:00:00",
            "source": "CardLock.mdb",
            "rooms": [...],
            "occupied_rooms": ["101", ...],
            "guests": [...],
            "error": ""  # 空字符串表示成功
        }
    """
    result: dict[str, Any] = {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "",
        "rooms": [],
        "occupied_rooms": [],
        "guests": [],
        "error": "",
    }

    # 从 profile 读取锁号编码方式
    encode_lock_no: bool = False
    lock_no_encoding: str = "hex_be"
    if profile:
        ln_cfg = profile.get("lock_no", {}) or {}
        lock_no_encoding = ln_cfg.get("encoding", "hex_be") or "hex_be"
        encode_lock_no = True

    # 找 MDB 文件
    install_path = Path(install_dir)
    mdb_files = list(install_path.glob("*.mdb")) + list(install_path.glob("*.accdb"))
    if not mdb_files:
        result["error"] = "未找到 .mdb/.accdb 文件"
        return result

    mdb_path = str(mdb_files[0])
    result["source"] = Path(mdb_path).name
    logger.info("[RoomExporter] 找到 MDB: %s", mdb_path)

    # 尝试用 access_parser 读取
    try:
        from access_parser import AccessParser

        db = AccessParser(mdb_path)
        catalog = [str(t) for t in db.catalog if not str(t).startswith("MSys")]
        logger.info("[RoomExporter] MDB 表: %s", catalog)

        # ── RoomInfo ──
        if "RoomInfo" not in catalog:
            result["error"] = "MDB 中没有 RoomInfo 表"
            return result

        raw = db.parse_table("RoomInfo")
        if not raw:
            result["error"] = "RoomInfo 表为空"
            return result

        col_names = list(raw.keys())
        n_rows = max(len(v) for v in raw.values()) if raw else 0

        for i in range(n_rows):
            row: dict[str, Any] = {}
            for c in col_names:
                vals = raw.get(c, [])
                row[c] = str(vals[i]) if i < len(vals) else ""

            room_no = _safe_str(row.get("RoomNo") or row.get("roomno") or row.get("RoomNO"))
            if not room_no:
                continue

            bld_no = _safe_int(row.get("BldNo") or row.get("bldno") or row.get("BldNO"), 1)
            flr_no = _safe_int(row.get("FlrNo") or row.get("flrno") or row.get("FlrNO"), 0)
            rom_id = _safe_int(row.get("RomID") or row.get("romid") or row.get("RomId"), 0)
            floor_str = _safe_str(row.get("FloorNo") or row.get("floorno") or "")
            room_type = _safe_str(row.get("RoomType") or row.get("roomtype") or row.get("RoomTypeName") or "标准间")

            if encode_lock_no:
                # 使用 profile 编码（抽象锁号格式，不依赖 V9 的 80|RomID|FlrNo|BldNo）
                lock_no = _encode_lock_no_profile(str(rom_id), lock_no_encoding)
            else:
                lock_no = _lock_no_from_roominfo(bld_no, flr_no, rom_id)

            result["rooms"].append({
                "room_id": room_no,
                "lock_no": lock_no,
                "bld_no": bld_no,
                "flr_no": flr_no,
                "rom_id": rom_id,
                "floor": floor_str,
                "room_type": room_type,
            })

        logger.info("[RoomExporter] 导出 %d 间房", len(result["rooms"]))

        # ── 尝试找在住客人表 ──
        guest_tables = [t for t in catalog if any(kw in t.lower() for kw in
                        ("guest", "inhouse", "in_house", "hotel_card", "入住", "客人", "checin"))]

        for gtable in guest_tables:
            try:
                g_raw = db.parse_table(gtable)
                if not g_raw:
                    continue
                g_cols = list(g_raw.keys())
                g_rows = max(len(v) for v in g_raw.values()) if g_raw else 0

                for i in range(g_rows):
                    g_row: dict[str, Any] = {}
                    for c in g_cols:
                        vals = g_raw.get(c, [])
                        g_row[c] = str(vals[i]) if i < len(vals) else ""

                    g_room = _safe_str(g_row.get("RoomNo") or g_row.get("roomno") or
                                       g_row.get("RoomNO") or g_row.get("CardNo") or "")
                    if not g_room:
                        continue

                    # 尝试找客人名字
                    g_name = _safe_str(g_row.get("GuestName") or g_row.get("guestname") or
                                       g_row.get("Name") or g_row.get("name") or g_row.get("Guest") or "")
                    g_idcard = _safe_str(g_row.get("IdCard") or g_row.get("idcard") or
                                         g_row.get("IDCard") or g_row.get("PaperNO") or "")
                    g_phone = _safe_str(g_row.get("Phone") or g_row.get("phone") or
                                        g_row.get("Tel") or g_row.get("tel") or "")
                    g_checkin = _safe_str(g_row.get("CheckIn") or g_row.get("checkin") or
                                          g_row.get("Checkin") or g_row.get("CheckInTime") or
                                          g_row.get("SDate") or g_row.get("sdate") or "")
                    g_checkout = _safe_str(g_row.get("CheckOut") or g_row.get("checkout") or
                                           g_row.get("EDate") or g_row.get("edate") or "")

                    # 如果客人名字和房间号都有，才算有效
                    if g_name or g_room:
                        result["guests"].append({
                            "room_id": g_room,
                            "name": g_name or "（迁移）",
                            "id_card": g_idcard,
                            "phone": g_phone,
                            "checkin_time": g_checkin,
                            "checkout_time": g_checkout,
                        })
                        if g_room not in result["occupied_rooms"]:
                            result["occupied_rooms"].append(g_room)
            except Exception as e:
                logger.warning("[RoomExporter] 读取客人表 %s 失败: %s", gtable, e)
                continue

        logger.info("[RoomExporter] 导出 %d 位在住客人", len(result["guests"]))

    except ImportError:
        result["error"] = "access_parser 库未安装，无法读取 MDB"
        logger.warning("[RoomExporter] access_parser 不可用")
    except Exception as e:
        result["error"] = f"读取 MDB 失败: {e}"
        logger.error("[RoomExporter] 读取 MDB 失败: %s", e, exc_info=True)

    return result
