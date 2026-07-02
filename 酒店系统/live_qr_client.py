"""
live_qr_client.py — 云端活码同步（厂家后台统一机器人 + 固定活码链接）

房间贴纸（固定不变）：https://{worker}/r/{8位活码} —— 印一次即可。
机器人（不固定）：扫码时云端查绑定信息，跳到当前绑定的客人机器人。
换机器人：厂家在管理后台改绑定即可，贴纸不用重印。
退房换令牌：仅更新云端令牌，活码短链不变。
"""

from __future__ import annotations

from typing import Dict, List, Optional

import requests

from cloud_security import signed_post_json, signed_request
from database import db
import logging
logger = logging.getLogger(__name__)


def is_live_qr_enabled() -> bool:
    if (db.get_config("live_qr_enabled") or "1") != "1":
        return False
    return bool((db.get_config("cloud_worker_url") or "").strip())


def _worker_url() -> str:
    return (db.get_config("cloud_worker_url") or "").strip().rstrip("/")


def _hotel_id() -> str:
    from license_manager import LicenseManager
    return LicenseManager.get_hotel_id()


def sync_rooms_to_cloud(rooms: List[Dict[str, str]], timeout: int = 12) -> Optional[dict]:
    """
    rooms: [{"room_id": "101", "token": "xxx"}, ...]
    返回云端 JSON；失败返回空
    """
    url_base = _worker_url()
    hid = _hotel_id()
    if not url_base or not hid or not rooms:
        return None
    try:
        resp = signed_post_json(
            f"{url_base}/api/live-qr-sync",
            {"hotel_id": hid, "rooms": rooms},
            timeout=timeout,
        )
        if resp.status_code != 200:
            logger.warning("[LIVE-QR] sync HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        if data.get("ok"):
            base = data.get("live_qr_base") or f"{url_base}/r/"
            db.set_config("live_qr_base_url", base.rstrip("/") + "/")
            for item in data.get("rooms") or []:
                rid = item.get("room_id")
                code = item.get("code")
                if rid and code:
                    _save_live_code_local(rid, code)
        return data
    except Exception as exc:
        logger.warning("[LIVE-QR] sync error: %s", exc)
        return None


def sync_single_room(room_id: str, token: str) -> Optional[str]:
    """同步单房，返回活码链接（失败则空）"""
    data = sync_rooms_to_cloud([{"room_id": room_id, "token": token}])
    if not data or not data.get("ok"):
        return None
    for item in data.get("rooms") or []:
        if item.get("room_id") == room_id:
            return item.get("live_url")
    return None


def sync_all_rooms_from_db() -> int:
    """将本机全部 room_qr_tokens 同步到云端，返回成功房间数"""
    try:
        from qr_code_service import _ensure_qr_tables
        _ensure_qr_tables()
        rows = db.execute("SELECT room_id, token FROM room_qr_tokens").fetchall()
    except Exception:
        return 0
    if not rows:
        return 0
    payload = [{"room_id": r[0], "token": r[1]} for r in rows]
    ok = 0
    chunk = 80
    for i in range(0, len(payload), chunk):
        part = payload[i : i + chunk]
        data = sync_rooms_to_cloud(part)
        if data and data.get("ok"):
            ok += len(data.get("rooms") or [])
    return ok


def _save_live_code_local(room_id: str, code: str) -> None:
    try:
        db.execute(
            "UPDATE room_qr_tokens SET live_code=? WHERE room_id=?",
            (code, room_id),
        )
    except Exception:
        try:
            db.execute("ALTER TABLE room_qr_tokens ADD COLUMN live_code TEXT")
        except Exception:
            pass
        try:
            db.execute(
                "UPDATE room_qr_tokens SET live_code=? WHERE room_id=?",
                (code, room_id),
            )
        except Exception:
            pass


def get_live_url_for_room(room_id: str) -> Optional[str]:
    row = None
    try:
        row = db.execute(
            "SELECT live_code FROM room_qr_tokens WHERE room_id=?",
            (room_id,),
        ).fetchone()
    except Exception:
        pass
    if not row or not row[0]:
        return None
    base = (db.get_config("live_qr_base_url") or "").strip()
    if not base:
        w = _worker_url()
        if w:
            base = f"{w}/r/"
    if not base:
        return None
    return f"{base.rstrip('/')}/{row[0]}"


def bind_hotel_bots(
    guest_bot_id: str,
    work_bot_id: str = "",
    admin_pwd: str = "",
    timeout: int = 10,
) -> dict:
    """厂家：将当前酒店绑定到指定机器人（云端绑定记录）"""
    url_base = _worker_url()
    hid = _hotel_id()
    if not url_base or not hid:
        return {"ok": False, "error": "未配置云端或酒店 ID"}
    pwd = admin_pwd or (db.get_config("cloud_admin_pwd") or "")
    if not pwd:
        return {"ok": False, "error": "需要厂家云端管理密码"}
    try:
        resp = signed_request(
            "POST",
            f"{url_base}/api/hotel-bot-bind",
            json_body={
                "pwd": pwd,
                "hotel_id": hid,
                "guest_bot_id": guest_bot_id or None,
                "work_bot_id": work_bot_id or None,
            },
            timeout=timeout,
            secret=pwd,
            subject="admin",
        )
        return resp.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
