"""
seq_manager.py — 序列号（seq 半字节）持久化管理器

职责
====
管理发卡用的 seq 半字节 (0-15)：
1. 系统卡（Master/Building/Floor/Emergency/Group/Auth）：全局计数器，存 system_config
2. 客人卡（按房间）：房间级计数器，存 rooms.last_seq

seq 在发卡时自动递增，wrap 在 0-15，确保同类型连续两张卡的 seq 不重复。
系统卡和客人卡各自独立计数，互不影响。

Profile 驱动
=============
seq 的存储键名和范围可通过 profile 的 seq_config 配置：

{
  "seq_config": {
    "system_prefix": "last_seq_",   # 系统卡存储前缀
    "room_column": "last_seq",        # 房间表列名
    "wrap": 15,                       # wrap 值 (默认 0x0F)
    "default_seq": 0                  # 默认起始 seq
  }
}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _db():
    """deferred import database 避免循环引用。"""
    from database import db
    return db


# ====================================================================
# Profile 驱动的 seq 配置缓存
# ====================================================================

_SEQ_CONFIG_CACHE: Dict[str, Any] = {}


def _load_seq_config(profile: Optional[dict] = None) -> dict:
    """从 profile 加载 seq 配置，缓存结果避免重复读取。"""
    if profile is None:
        return _SEQ_CONFIG_CACHE.get("_active") or {
            "system_prefix": "last_seq_",
            "room_column": "last_seq",
            "wrap": 15,
            "default_seq": 0,
        }
    config = profile.get("seq_config", {})
    resolved = {
        "system_prefix": config.get("system_prefix", "last_seq_"),
        "room_column": config.get("room_column", "last_seq"),
        "wrap": int(config.get("wrap", 15)),
        "default_seq": int(config.get("default_seq", 0)),
    }
    _SEQ_CONFIG_CACHE["_active"] = resolved
    return resolved


def _wrap(value: int, wrap: int = 15) -> int:
    """带 wrap 的递增。"""
    return (value + 1) & wrap


# ====================================================================
# 系统卡 seq（全局）
# ====================================================================


def get_next_system_seq(card_type: str, profile: Optional[dict] = None) -> int:
    """获取指定系统卡类型的下一个 seq 半字节 (0-15)。

    从 system_config 读取 {prefix}{card_type}，递增后持久化。
    如果记录不存在，从 default_seq 开始。
    不同类型独立计数：last_seq_master, last_seq_building, ...

    Args:
        card_type: 卡类型，如 "master", "building", "floor" 等。
        profile: 可选 profile，用于读取 seq 配置。

    Returns:
        seq nibble (0-15)。
    """
    cfg = _load_seq_config(profile)
    prefix = cfg["system_prefix"]
    wrap = cfg["wrap"]
    default = cfg["default_seq"]
    key = f"{prefix}{card_type}"

    try:
        row = _db().execute(
            "SELECT value FROM system_config WHERE key=?", (key,)
        ).fetchone()
        current = int(row[0]) if row else default
    except Exception:
        current = default

    next_val = _wrap(current, wrap)

    try:
        _db().execute(
            "INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)",
            (key, str(next_val)),
        )
    except Exception as exc:
        logger.warning("保存系统卡 seq[%s]=%d 失败: %s", card_type, next_val, exc)

    return next_val


# ====================================================================
# 客人卡 seq（按房间）
# ====================================================================


def get_next_room_seq(room_id: str, profile: Optional[dict] = None) -> int:
    """获取指定房间的下一个 seq 半字节 (0-15)。

    从 rooms.{column} 读取当前值，递增后持久化。

    Args:
        room_id: 房间 ID。
        profile: 可选 profile，用于读取 seq 配置。

    Returns:
        seq nibble (0-15)。
    """
    cfg = _load_seq_config(profile)
    column = cfg["room_column"]
    wrap = cfg["wrap"]
    default = cfg["default_seq"]

    try:
        row = _db().execute(
            f"SELECT COALESCE({column}, {default}) FROM rooms WHERE room_id=?",
            (room_id,),
        ).fetchone()
        current = int(row[0]) if row else default
    except Exception:
        current = default

    next_val = _wrap(current, wrap)

    try:
        _db().execute(
            f"UPDATE rooms SET {column}=? WHERE room_id=?",
            (next_val, room_id),
        )
    except Exception as exc:
        logger.warning("保存房间 seq[%s]=%d 失败: %s", room_id, next_val, exc)

    return next_val


# ====================================================================
# 读取/设置 seq（用于导入/回滚状态恢复）
# ====================================================================


def peek_system_seq(card_type: str, profile: Optional[dict] = None) -> Optional[int]:
    """读取当前系统卡 seq 值（不递增）。

    Returns:
        当前 seq 值，或 None（没有记录）。
    """
    cfg = _load_seq_config(profile)
    prefix = cfg["system_prefix"]
    key = f"{prefix}{card_type}"
    try:
        row = _db().execute(
            "SELECT value FROM system_config WHERE key=?", (key,)
        ).fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None


def set_system_seq(card_type: str, value: int, profile: Optional[dict] = None) -> bool:
    """设置系统卡 seq 值（用于状态恢复）。"""
    cfg = _load_seq_config(profile)
    prefix = cfg["system_prefix"]
    key = f"{prefix}{card_type}"
    try:
        _db().execute(
            "INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)",
            (key, str(value & cfg["wrap"])),
        )
        return True
    except Exception as exc:
        logger.warning("设置系统卡 seq[%s]=%d 失败: %s", card_type, value, exc)
        return False


def peek_room_seq(room_id: str, profile: Optional[dict] = None) -> Optional[int]:
    """读取当前房间 seq 值（不递增）。"""
    cfg = _load_seq_config(profile)
    column = cfg["room_column"]
    try:
        row = _db().execute(
            f"SELECT {column} FROM rooms WHERE room_id=?", (room_id,)
        ).fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None


def set_room_seq(room_id: str, value: int, profile: Optional[dict] = None) -> bool:
    """设置房间 seq 值（用于状态恢复）。"""
    cfg = _load_seq_config(profile)
    column = cfg["room_column"]
    try:
        _db().execute(
            f"UPDATE rooms SET {column}=? WHERE room_id=?",
            (value & cfg["wrap"], room_id),
        )
        return True
    except Exception as exc:
        logger.warning("设置房间 seq[%s]=%d 失败: %s", room_id, value, exc)
        return False
