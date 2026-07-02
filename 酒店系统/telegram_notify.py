"""
telegram_notify.py — Telegram 通知发送模块

统一入口，供全项目各模块调用发送推送。
适配机器人线程；线程未运行时仅记日志，不报错。

[sub-i] 新增 send_photo：用 requests multipart 上传图片到 Telegram sendPhoto API。
失败自动回退 send_message 文字，保证不阻断调用方。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


# ── [sub-i] 图片 MIME 类型猜测 ─────────────────────────────────
_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _guess_mime(path: Union[str, Path]) -> str:
    """根据扩展名猜 MIME 类型；未知返回 application/octet-stream。"""
    ext = Path(path).suffix.lower()
    return _MIME_MAP.get(ext, "application/octet-stream")


def _normalize_markup(reply_markup) -> Optional[dict]:
    """reply_markup 兼容三种输入：dict / JSON 字符串 / None。统一返回 dict|None。"""
    if reply_markup is None:
        return None
    if isinstance(reply_markup, str):
        try:
            return json.loads(reply_markup)
        except (json.JSONDecodeError, ValueError):
            return None
    if isinstance(reply_markup, dict):
        return reply_markup
    return None


def _fallback_send_message(
    token: str,
    chat_id,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> bool:
    """send_photo 失败时的文字兜底；不抛异常，失败返回 False。"""
    import requests
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": (text or "（图片发送失败）")[:4096]}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    markup = _normalize_markup(reply_markup)
    if markup is not None:
        payload["reply_markup"] = markup
    try:
        resp = requests.post(url, json=payload, timeout=5)
        ok = resp.status_code == 200 and resp.json().get("ok", False)
        if not ok:
            logger.warning("[telegram_notify] fallback sendMessage %s: %s",
                           resp.status_code, resp.text[:200])
        return ok
    except Exception as e:
        logger.warning("[telegram_notify] fallback sendMessage error: %s", e)
        return False


def send_photo(
    token: str,
    chat_id,
    photo_path: Union[str, Path],
    caption: str = "",
    *,
    reply_markup=None,
    parse_mode: str = "HTML",
    timeout: int = 15,
) -> bool:
    """用 requests multipart 上传图片到 Telegram sendPhoto API。

    Args:
        token: bot token（不含 "bot" 前缀的纯 token）
        chat_id: 目标 chat id（str|int）
        photo_path: 图片文件路径（Path 或 str），支持 jpg/png/webp/gif
        caption: 图片说明文本（Telegram 限 1024 字符，超出自动截断）
        reply_markup: 可选 inline keyboard（dict / JSON 字符串）
        parse_mode: "HTML"（默认）或 "Markdown" 或 "MarkdownV2"
        timeout: requests 超时秒数

    Returns:
        True = 发图成功；False = 发图失败（已自动回退 send_message 文字）

    行为：
        • photo_path 不存在或读取失败 → 直接走 send_message 文字兜底
        • sendPhoto 返回非 200 / 非 ok → 走 send_message 文字兜底
        • 兜底失败也返回 False，但不抛异常（调用方需自行检查返回值）
    """
    import requests
    p = Path(photo_path)
    if not p.is_file():
        logger.debug("[telegram_notify] photo 不存在，走文字兜底: %s", p)
        return _fallback_send_message(token, chat_id, caption, reply_markup, parse_mode)

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    # Telegram caption 上限 1024 字符
    cap = (caption or "")[:1024]
    data: dict = {"chat_id": chat_id, "caption": cap}
    if parse_mode:
        data["parse_mode"] = parse_mode
    markup = _normalize_markup(reply_markup)
    if markup is not None:
        data["reply_markup"] = markup

    try:
        with open(p, "rb") as f:
            files = {"photo": (p.name, f, _guess_mime(p))}
            resp = requests.post(url, data=data, files=files, timeout=timeout)
        if resp.status_code == 200 and resp.json().get("ok", False):
            return True
        logger.warning("[telegram_notify] sendPhoto 失败 %s: %s",
                       resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("[telegram_notify] sendPhoto 异常: %s", e)

    # 任何失败 → 回退文字
    return _fallback_send_message(token, chat_id, caption, reply_markup, parse_mode)


def send_telegram(message: str) -> bool:
    """发送一条通知消息。

    自动查找运行中的机器人线程。
    成功后返回真值，失败或机器人未配置则返回假值（静默忽略）。
    """
    try:
        from telegram_shadow import telegram_thread
    except ImportError:
        logger.debug("[telegram_notify] telegram_shadow 未加载")
        return False

    try:
        if telegram_thread is None:
            logger.debug("[telegram_notify] telegram_thread 为 None")
            return False
        if not hasattr(telegram_thread, "isRunning"):
            logger.debug("[telegram_notify] telegram_thread 无 isRunning 方法")
            return False
        if not telegram_thread.isRunning():
            logger.debug("[telegram_notify] telegram_thread 未运行")
            return False
    except Exception:
        return False

    try:
        # 尝试 send_alert_sync（支持按钮的消息发送方法）
        if hasattr(telegram_thread, "send_alert_sync"):
            telegram_thread.send_alert_sync(message)
            return True

        # 回退：尝试 send_message
        if hasattr(telegram_thread, "send_message"):
            telegram_thread.send_message(message)
            return True
    except Exception as e:
        logger.warning("[telegram_notify] 发送失败: %s", e)
        return False

    logger.debug("[telegram_notify] telegram_thread 无兼容的发送方法")
    return False
