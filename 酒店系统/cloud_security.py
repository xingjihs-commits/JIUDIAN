"""
cloud_security.py — 厂家云端通信 HMAC 签名

目标：
  - 新客户端所有酒店端云通信默认带签名。
  - Worker 可兼容旧安装包；铺市场前打开强制验签开关即可拒绝未签名请求。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

import requests

SIGNATURE_VERSION = "solid-hmac-v1"


def _stable_json_bytes(data: Any) -> bytes:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _path_for_signature(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path or "/"


def _hotel_id() -> str:
    try:
        from license_manager import LicenseManager
        return LicenseManager.get_hotel_id()
    except Exception:
        try:
            from database import db
            return (db.get_config("hotel_id") or db.get_config("hotel_name") or "UNKNOWN").strip()
        except Exception:
            return "UNKNOWN"


def get_client_secret() -> str:
    """本机与云服务共享的酒店端通信密钥。首次生成后加密持久化在数据库中。"""
    from database import db

    cached = (db.get_config("cloud_client_secret") or "").strip()
    if cached:
        if cached.startswith("CS_"):
            return cached  # old plaintext format — keep backward compat
        try:
            from crypto_utils import crypto
            decrypted = crypto.decrypt(cached)
            if decrypted:
                return decrypted
        except Exception:
            pass
        return cached
    raw = f"{uuid.uuid4().hex}:{uuid.getnode()}:{time.time_ns()}"
    secret = "CS_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    try:
        from crypto_utils import crypto
        db.set_config("cloud_client_secret", crypto.encrypt(secret))
    except Exception:
        db.set_config("cloud_client_secret", secret)
    return secret


def signature_headers(
    method: str,
    url: str,
    body: bytes = b"",
    *,
    subject: Optional[str] = None,
    secret: Optional[str] = None,
) -> dict[str, str]:
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    sid = (subject or _hotel_id() or "UNKNOWN").strip()
    key = (secret or get_client_secret()).encode("utf-8")
    msg = "\n".join([
        method.upper(),
        _path_for_signature(url),
        sid,
        ts,
        nonce,
        body.decode("utf-8", errors="ignore"),
    ]).encode("utf-8")
    sig = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return {
        "X-Solid-Signature-Version": SIGNATURE_VERSION,
        "X-Solid-Hotel-Id": sid,
        "X-Solid-Timestamp": ts,
        "X-Solid-Nonce": nonce,
        "X-Solid-Signature": sig,
    }


def signed_request(
    method: str,
    url: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
    timeout: int | float = 8,
    secret: Optional[str] = None,
    subject: Optional[str] = None,
) -> requests.Response:
    method_u = method.upper()
    body = _stable_json_bytes(json_body) if json_body is not None else b""
    headers = signature_headers(method_u, url, body, subject=subject, secret=secret)
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        return requests.request(method_u, url, params=params, data=body, headers=headers, timeout=timeout)
    return requests.request(method_u, url, params=params, headers=headers, timeout=timeout)


def signed_post_json(url: str, body: dict[str, Any], *, timeout: int | float = 8) -> requests.Response:
    return signed_request("POST", url, json_body=body, timeout=timeout)


def signed_get_json(url: str, *, params: Optional[dict[str, Any]] = None, timeout: int | float = 8) -> requests.Response:
    return signed_request("GET", url, params=params, timeout=timeout)


def verify_notification_signature(notification: dict[str, Any]) -> bool:
    """校验下发通知的签名；旧服务未带签名时默认兼容放行。"""
    sig = str(notification.get("payload_sig") or "").strip()
    if not sig:
        return True
    msg = "\n".join([
        str(notification.get("notify_id") or ""),
        str(notification.get("notify_type") or ""),
        str(notification.get("payload_json") or "{}"),
    ]).encode("utf-8")
    expected = hmac.new(get_client_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
