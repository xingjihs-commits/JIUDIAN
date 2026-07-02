"""
客房取电器（节电开关）写卡 — 按业内惯例自动配置。

行业通用做法（国内经济型/中端酒店）：
  1. 发卡机写入的 MIFARE 扇区 = 门锁软件写的扇区（同密钥）。
  2. 取电器读同一扇区校验房号/有效期，插卡才通电。
  3. PMS 不单独维护「取电参数」，而是跟随门锁品牌 + 迁移来的密钥。

Solid 默认「跟随门锁系统」：根据 lock_brand、legacy_lock_keys、USB 迁移自动推断。
"""

from __future__ import annotations

import json
import re
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

from database import db

_ROOT = Path(__file__).resolve().parent
_PROFILES_PATH = _ROOT / "POWER_CONTROLLER_PROFILES" / "profiles.json"
_LOCK_MAP_PATH = _ROOT / "POWER_CONTROLLER_PROFILES" / "lock_power_map.json"

_CONFIG_KEYS = {
    "mode": "power_ctrl_mode",
    "enabled": "power_ctrl_enabled",
    "profile_id": "power_ctrl_profile_id",
    "sector": "power_ctrl_sector",
    "block": "power_ctrl_block",
    "key_a": "power_ctrl_key_a",
    "key_b": "power_ctrl_key_b",
    "data_format": "power_ctrl_data_format",
    "notes": "power_ctrl_notes",
}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_profile_catalog() -> dict:
    return _load_json(_PROFILES_PATH)


def load_lock_power_map() -> dict:
    return _load_json(_LOCK_MAP_PATH)


def list_power_profiles() -> list[dict]:
    return load_profile_catalog().get("profiles") or []


def list_data_formats() -> list[dict]:
    return load_profile_catalog().get("data_formats") or []


def get_profile_by_id(profile_id: str) -> dict | None:
    pid = (profile_id or "").strip()
    for p in list_power_profiles():
        if p.get("id") == pid:
            return p
    return None


def normalize_key_hex(key: str) -> str:
    k = re.sub(r"[^0-9A-Fa-f]", "", key or "")
    if len(k) != 12:
        raise ValueError("密钥须为 6 字节（12 位十六进制）")
    return k.upper()


def get_active_lock_brand_id() -> str:
    return (
        (db.get_config("lock_brand") or "").strip()
        or (db.get_config("lock_brand_id") or "").strip()
        or "generic_mifare"
    )


def get_active_lock_brand_name() -> str:
    return (db.get_config("lock_brand_name") or "").strip() or "未识别门锁"


def _brand_power_defaults(brand_id: str) -> dict[str, Any]:
    m = load_lock_power_map()
    defaults = {
        "sector": int(m.get("default_guest_sector", 1)),
        "block": int(m.get("default_block", 0)),
        "key_a": m.get("default_key_a", "FFFFFFFFFFFF"),
        "key_b": m.get("default_key_b", "FFFFFFFFFFFF"),
        "data_format": m.get("default_data_format", "room_ascii8_ts4"),
        "notes": "",
    }
    by = (m.get("by_lock_brand") or {}).get(brand_id) or {}
    for k in ("sector", "block", "data_format", "note", "notes"):
        if k in by and by[k] is not None:
            if k in ("note", "notes"):
                defaults["notes"] = str(by.get("note") or by.get("notes") or "")
            else:
                defaults[k] = by[k]
    return defaults


def import_keys_from_legacy_lock(sector: int | None = None) -> tuple[str, str] | None:
    raw = db.get_config("legacy_lock_keys") or "{}"
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    sec = sector if sector is not None else int(db.get_config(_CONFIG_KEYS["sector"]) or "1")
    for key in (str(sec), f"sector_{sec}", f"sector_{sec}_key"):
        item = data.get(key)
        if isinstance(item, dict):
            ka = item.get("key_a") or item.get("a") or item.get("KeyA")
            kb = item.get("key_b") or item.get("b") or item.get("KeyB")
            if ka:
                try:
                    return normalize_key_hex(str(ka)), normalize_key_hex(str(kb or ka))
                except ValueError:
                    continue
        if isinstance(item, str) and len(re.sub(r"[^0-9A-Fa-f]", "", item)) >= 12:
            try:
                k = normalize_key_hex(item)
                return k, k
            except ValueError:
                continue
    for k in ("default_key_a", "key_a", "master_key"):
        if data.get(k):
            try:
                ka = normalize_key_hex(str(data[k]))
                kb = normalize_key_hex(str(data.get("key_b") or data.get("default_key_b") or ka))
                return ka, kb
            except ValueError:
                pass
    for k, v in data.items():
        if isinstance(v, str) and k.endswith("_key_a"):
            try:
                ka = normalize_key_hex(v)
                kb_key = k.replace("_key_a", "_key_b")
                kb = normalize_key_hex(str(data.get(kb_key) or ka))
                return ka, kb
            except ValueError:
                continue
    return None


def resolve_power_config() -> dict[str, Any]:
    """
    解析最终写卡参数（发卡前调用）。
    auto 模式：跟随门锁品牌 + 已迁移密钥。
    """
    mode = (db.get_config(_CONFIG_KEYS["mode"]) or "auto").strip().lower()
    profile_id = db.get_config(_CONFIG_KEYS["profile_id"]) or "follow_lock"
    enabled = (db.get_config(_CONFIG_KEYS["enabled"]) or "1") == "1"
    if mode == "auto" or profile_id == "follow_lock":
        enabled = True
        brand = get_active_lock_brand_id()
        d = _brand_power_defaults(brand)
        cfg = {
            "enabled": True,
            "mode": "auto",
            "profile_id": "follow_lock",
            "sector": int(d["sector"]),
            "block": int(d["block"]),
            "key_a": str(d["key_a"]).upper(),
            "key_b": str(d["key_b"]).upper(),
            "data_format": d["data_format"],
            "notes": d.get("notes") or "",
            "lock_brand_id": brand,
            "lock_brand_name": get_active_lock_brand_name(),
            "source": "follow_lock",
        }
        imported = import_keys_from_legacy_lock(cfg["sector"])
        if imported:
            cfg["key_a"], cfg["key_b"] = imported
            cfg["source"] = "follow_lock+legacy_keys"
        return cfg

    return {
        "enabled": enabled,
        "mode": "manual",
        "profile_id": profile_id,
        "sector": int(db.get_config(_CONFIG_KEYS["sector"]) or "1"),
        "block": int(db.get_config(_CONFIG_KEYS["block"]) or "0"),
        "key_a": (db.get_config(_CONFIG_KEYS["key_a"]) or "FFFFFFFFFFFF").upper(),
        "key_b": (db.get_config(_CONFIG_KEYS["key_b"]) or "FFFFFFFFFFFF").upper(),
        "data_format": db.get_config(_CONFIG_KEYS["data_format"]) or "room_ascii8_ts4",
        "notes": db.get_config(_CONFIG_KEYS["notes"]) or "",
        "lock_brand_id": get_active_lock_brand_id(),
        "lock_brand_name": get_active_lock_brand_name(),
        "source": "manual",
    }


def load_power_config() -> dict[str, Any]:
    """兼容旧调用：返回解析后的有效配置。"""
    return resolve_power_config()


def save_power_config(cfg: dict[str, Any]) -> None:
    mode = cfg.get("mode") or ("auto" if cfg.get("profile_id") == "follow_lock" else "manual")
    db.set_config(_CONFIG_KEYS["mode"], mode)
    db.set_config(_CONFIG_KEYS["enabled"], "1" if cfg.get("enabled", True) else "0")
    db.set_config(_CONFIG_KEYS["profile_id"], str(cfg.get("profile_id") or "follow_lock"))
    if mode == "manual":
        db.set_config(_CONFIG_KEYS["sector"], str(int(cfg.get("sector", 1))))
        db.set_config(_CONFIG_KEYS["block"], str(int(cfg.get("block", 0))))
        db.set_config(_CONFIG_KEYS["key_a"], normalize_key_hex(cfg.get("key_a") or "FFFFFFFFFFFF"))
        db.set_config(_CONFIG_KEYS["key_b"], normalize_key_hex(cfg.get("key_b") or "FFFFFFFFFFFF"))
        db.set_config(_CONFIG_KEYS["data_format"], str(cfg.get("data_format") or "room_ascii8_ts4"))
        db.set_config(_CONFIG_KEYS["notes"], str(cfg.get("notes") or ""))


def ensure_power_config_initialized() -> dict[str, Any]:
    """首次运行：业内默认开启「跟随门锁」。"""
    if db.get_config(_CONFIG_KEYS["mode"]) is None:
        db.set_config(_CONFIG_KEYS["mode"], "auto")
        db.set_config(_CONFIG_KEYS["profile_id"], "follow_lock")
        db.set_config(_CONFIG_KEYS["enabled"], "1")
    return resolve_power_config()


def sync_power_from_lock_brand(brand_id: str, brand_name: str = "") -> dict[str, Any]:
    """USB 门锁迁移 / 识别品牌后调用。"""
    if brand_name:
        db.set_config("lock_brand_name", brand_name)
    db.set_config("lock_brand", brand_id)
    db.set_config(_CONFIG_KEYS["mode"], "auto")
    db.set_config(_CONFIG_KEYS["profile_id"], "follow_lock")
    db.set_config(_CONFIG_KEYS["enabled"], "1")
    cfg = resolve_power_config()
    db.log_action(
        "SYSTEM",
        "POWER_CTRL_SYNC",
        f"brand={brand_id} sector={cfg['sector']} keys={cfg['key_a'][:6]}… source={cfg.get('source')}",
    )
    return cfg


def sync_power_from_sniffer(packet: dict) -> None:
    """串口嗅探到密钥/扇区时合并进 legacy_lock_keys 并刷新取电配置。"""
    if not isinstance(packet, dict):
        return
    sector = packet.get("sector")
    key_a = packet.get("key_a") or ""
    key_b = packet.get("key_b") or ""
    if not key_a and not sector:
        return
    try:
        raw = json.loads(db.get_config("legacy_lock_keys") or "{}")
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if sector not in ("", None):
        sec = int(sector) if str(sector).isdigit() else None
        if sec is not None:
            entry = raw.get(str(sec)) if isinstance(raw.get(str(sec)), dict) else {}
            if key_a:
                entry["key_a"] = normalize_key_hex(str(key_a))
            if key_b:
                entry["key_b"] = normalize_key_hex(str(key_b))
            raw[str(sec)] = entry
    if key_a and "default_key_a" not in raw:
        raw["default_key_a"] = normalize_key_hex(str(key_a))
    db.set_config("legacy_lock_keys", json.dumps(raw, ensure_ascii=False))
    sync_power_from_lock_brand(
        packet.get("brand_id") or get_active_lock_brand_id(),
        packet.get("brand_profile") or "",
    )


def apply_profile_to_config(profile_id: str) -> dict[str, Any]:
    p = get_profile_by_id(profile_id)
    if not p:
        return resolve_power_config()
    if p.get("auto") or profile_id == "follow_lock":
        db.set_config(_CONFIG_KEYS["mode"], "auto")
        db.set_config(_CONFIG_KEYS["profile_id"], "follow_lock")
        db.set_config(_CONFIG_KEYS["enabled"], "1")
        return resolve_power_config()
    cfg = resolve_power_config()
    cfg["mode"] = "manual"
    cfg["profile_id"] = profile_id
    cfg["sector"] = int(p.get("sector", 1))
    cfg["block"] = int(p.get("block", 0))
    cfg["data_format"] = p.get("data_format") or "room_ascii8_ts4"
    cfg["notes"] = p.get("notes") or ""
    if p.get("key_a"):
        cfg["key_a"] = p["key_a"]
    if p.get("key_b"):
        cfg["key_b"] = p["key_b"]
    save_power_config(cfg)
    return resolve_power_config()


def _room_digits(room_id: str) -> str:
    digits = re.sub(r"\D", "", room_id or "")
    return digits or "0"


def encode_power_block(room_id: str, expire_ts: int, data_format: str) -> bytes:
    fmt = (data_format or "room_ascii8_ts4").strip()
    block = bytearray(16)
    rid = (room_id or "").strip()
    if fmt == "room_bcd4_ts4":
        digits = _room_digits(rid)[:8].zfill(8)
        for i in range(4):
            block[i] = (int(digits[i * 2]) << 4) | int(digits[i * 2 + 1])
        struct.pack_into(">I", block, 4, expire_ts & 0xFFFFFFFF)
    elif fmt == "flag_room_ascii6":
        block[0] = 0x01
        rb = rid.encode("ascii", errors="ignore")[:6]
        block[1 : 1 + len(rb)] = rb
    elif fmt == "room_hex2_ts4_pad":
        n = int(_room_digits(rid) or "0")
        struct.pack_into(">H", block, 0, min(n, 0xFFFF))
        struct.pack_into(">I", block, 2, expire_ts & 0xFFFFFFFF)
    else:
        rb = rid.encode("utf-8", errors="ignore")[:8].ljust(8, b"\x00")
        block[0:8] = rb
        struct.pack_into(">I", block, 8, expire_ts & 0xFFFFFFFF)
    return bytes(block)


def power_config_summary(cfg: dict | None = None) -> str:
    c = cfg or resolve_power_config()
    if not c.get("enabled"):
        return "取电：未启用"
    if c.get("mode") == "auto" or c.get("profile_id") == "follow_lock":
        name = c.get("lock_brand_name") or "门锁"
        return f"取电跟随 {name} · 扇区{c['sector']}"
    return f"取电手动 · 扇区{c['sector']} 块{c['block']}"


def power_config_detail_text(cfg: dict | None = None) -> str:
    c = cfg or resolve_power_config()
    if not c.get("enabled"):
        return "发卡时不会写入取电器数据，插卡可能无电。"
    lines = [
        f"模式：{'自动跟随门锁' if c.get('mode') == 'auto' else '高级手动'}",
        f"门锁：{c.get('lock_brand_name', '—')}",
        f"写卡扇区：第 {c['sector']} 扇区，第 {c['block']} 块",
        f"密钥来源：{c.get('source', '—')}",
    ]
    if c.get("notes"):
        lines.append(f"说明：{c['notes']}")
    return "\n".join(lines)


def preview_power_write(room_id: str, expire_ts: int | None = None) -> dict[str, Any]:
    cfg = resolve_power_config()
    ts = expire_ts if expire_ts is not None else int(datetime.now().timestamp()) + 86400
    data = encode_power_block(room_id, ts, cfg["data_format"])
    return {
        "cfg": cfg,
        "sector": cfg["sector"],
        "block": cfg["block"],
        "key_a": cfg["key_a"],
        "key_b": cfg["key_b"],
        "data_hex": data.hex().upper(),
        "data_format": cfg["data_format"],
    }
