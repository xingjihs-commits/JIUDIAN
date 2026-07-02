"""
telemetry.py — C0-6 业务员来源埋点
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import urllib.request

from cloud_security import signature_headers
from database import db


def report_event(event_type: str, payload: dict | None = None) -> None:
    worker = (db.get_config("cloud_worker_url") or "").strip().rstrip("/")
    if not worker:
        return
    body = {
        "hotel_id": (db.get_config("hotel_id") or db.get_config("hotel_name") or "UNKNOWN").strip(),
        "salesperson_id": (db.get_config("salesperson_id") or "").strip(),
        "event_type": (event_type or "").strip().upper(),
        "payload": payload or {},
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }

    def _send() -> None:
        try:
            body_bytes = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            url = f"{worker}/api/telemetry"
            headers = {"Content-Type": "application/json"}
            headers.update(signature_headers("POST", url, body_bytes, subject=body["hotel_id"]))
            req = urllib.request.Request(
                url,
                data=body_bytes,
                method="POST",
                headers=headers,
            )
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()
