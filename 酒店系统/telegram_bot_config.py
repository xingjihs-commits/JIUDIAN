"""
telegram_bot_config.py — 厂家统一机器人配置（单一事实来源）

分工（与云端一致）：
  · 厂家：客人机器人 / 工作机器人令牌、机器人用户名（@BotFather）
  · 酒店：老板聊天标识、前台/保洁群标识、花名册员工标识、通知开关

酒店端界面不得写入令牌；仅厂家面板 (Alt+D) 或云端下发的配置可更新。"""


from __future__ import annotations

from typing import Any, Dict, Optional

from database import db

CFG_GUEST = "telegram_bot_token"
CFG_WORK = "work_bot_token"
CFG_USERNAME = "telegram_bot_username"
CFG_PROVISION = "bot_tokens_provisioned_by"


def get_guest_bot_token() -> str:
    return (db.get_config(CFG_GUEST) or "").strip()


def get_work_bot_token() -> str:
    work = (db.get_config(CFG_WORK) or "").strip()
    return work or get_guest_bot_token()


def get_bot_username() -> str:
    return (db.get_config(CFG_USERNAME) or "").strip().lstrip("@")


def is_bot_configured() -> bool:
    return bool(get_work_bot_token())


def get_provision_source() -> str:
    return (db.get_config(CFG_PROVISION) or "").strip()


def apply_manufacturer_provision(
    guest_token: str = "",
    work_token: str = "",
    bot_username: str = "",
    manufacturer_chat_id: str = "",
    *,
    source: str = "manufacturer_local",
) -> None:
    """仅厂家装机 / 厂家面板调用。"""
    guest_token = (guest_token or "").strip()
    work_token = (work_token or guest_token or "").strip()
    if guest_token:
        db.set_config(CFG_GUEST, guest_token)
    if work_token:
        db.set_config(CFG_WORK, work_token)
    if bot_username:
        db.set_config(CFG_USERNAME, bot_username.lstrip("@"))
    if manufacturer_chat_id:
        db.set_config("manufacturer_chat_id", manufacturer_chat_id.strip())
    if guest_token or work_token:
        db.set_config(CFG_PROVISION, source)


def apply_cloud_bot_config(payload: Optional[Dict[str, Any]]) -> bool:
    """云端 hotel-poll 返回的 bot_config → 写入本机（酒店不可编辑）。"""
    if not payload or not isinstance(payload, dict):
        return False
    guest = (payload.get("guest_token") or payload.get("bot1") or "").strip()
    work = (payload.get("work_token") or payload.get("bot2") or "").strip()
    username = (payload.get("bot_username") or "").strip()
    if not guest and not work and not username:
        return False
    apply_manufacturer_provision(
        guest_token=guest,
        work_token=work or guest,
        bot_username=username,
        source="manufacturer_cloud",
    )
    return True


def apply_cloud_poll_response(data: Optional[Dict[str, Any]]) -> bool:
    if not data:
        return False
    ok = apply_cloud_bot_config(data.get("bot_config"))
    bc = data.get("bot_config") or {}
    base = (bc.get("live_qr_base") or data.get("live_qr_base") or "").strip()
    if base:
        db.set_config("live_qr_base_url", base if base.endswith("/") else base + "/")
        db.set_config("live_qr_enabled", "1")
    return ok


def request_roulette_assign(hotel_id: str = "") -> Dict[str, Any]:
    """调用云端 /api/bot-roulette 获取负载最低的客人 Bot。"""
    import requests
    try:
        worker = (db.get_config("cloud_worker_url") or "").strip().rstrip("/")
        if not worker or not hotel_id:
            return {"ok": False, "error": "未配置云端地址或 hotel_id"}
        r = requests.post(
            f"{worker}/api/bot-roulette",
            json={"hotel_id": hotel_id},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def status_label() -> str:
    if not is_bot_configured():
        return "未配置"
    return "已配置"
