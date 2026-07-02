"""
energy_audit_engine.py — C0-delta 能耗周期对账
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from typing import Optional

from cloud_security import signature_headers
from database import db

ENERGY_PERIOD_DAYS = 30
ENERGY_ANOMALY_RATE = 0.20
DEFAULT_METER_ID = "MAIN"


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def ensure_default_meter() -> str:
    row = db.execute("SELECT meter_id FROM energy_meters WHERE meter_id=?", (DEFAULT_METER_ID,)).fetchone()
    if not row:
        db.execute(
            "INSERT INTO energy_meters (meter_id,label,location,multiplier) VALUES (?,?,?,?)",
            (DEFAULT_METER_ID, "总电表", "酒店总表", 1.0),
        )
    return DEFAULT_METER_ID


def list_meters() -> list[dict]:
    ensure_default_meter()
    rows = db.execute(
        "SELECT meter_id,label,location,multiplier,is_active FROM energy_meters ORDER BY meter_id"
    ).fetchall()
    return [{
        "meter_id": r[0],
        "label": r[1],
        "location": r[2] or "",
        "multiplier": float(r[3] or 1),
        "is_active": bool(int(r[4] or 0)),
    } for r in rows]


def record_meter_reading(meter_id: str, reading_kwh: float, recorded_by: str,
                         *, source: str = "manual", note: str = "") -> str:
    ensure_default_meter()
    mid = (meter_id or DEFAULT_METER_ID).strip() or DEFAULT_METER_ID
    rid = uuid.uuid4().hex
    db.execute(
        """INSERT INTO energy_meter_readings
           (reading_id,meter_id,reading_kwh,recorded_by,source,note,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (rid, mid, float(reading_kwh), recorded_by or "SYSTEM", source or "manual", note or "", _now_iso()),
    )
    return rid


def recent_readings(limit: int = 30) -> list[dict]:
    rows = db.execute(
        """SELECT r.created_at,r.meter_id,m.label,r.reading_kwh,r.recorded_by,r.source,r.note
           FROM energy_meter_readings r
           LEFT JOIN energy_meters m ON m.meter_id=r.meter_id
           ORDER BY r.created_at DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [{
        "created_at": r[0] or "",
        "meter_id": r[1] or "",
        "label": r[2] or r[1] or "",
        "reading_kwh": float(r[3] or 0),
        "recorded_by": r[4] or "",
        "source": r[5] or "",
        "note": r[6] or "",
    } for r in rows]


def open_period_id() -> Optional[str]:
    row = db.execute(
        "SELECT period_id FROM energy_periods WHERE status='IN_PROGRESS' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def start_energy_period(operator_id: str = "SYSTEM", note: str = "") -> str:
    existing = open_period_id()
    if existing:
        return existing
    pid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO energy_periods (period_id,operator_id,note) VALUES (?,?,?)",
        (pid, operator_id or "SYSTEM", note or ""),
    )
    return pid


def _period_bounds(period_id: str) -> tuple[str, str]:
    row = db.execute(
        "SELECT started_at, COALESCE(finished_at, datetime('now')) FROM energy_periods WHERE period_id=?",
        (period_id,),
    ).fetchone()
    if not row:
        raise ValueError("energy period not found")
    return str(row[0] or ""), str(row[1] or "")


def _actual_kwh(started_at: str, finished_at: str) -> float:
    total = 0.0
    for meter in list_meters():
        rows = db.execute(
            """SELECT reading_kwh FROM energy_meter_readings
               WHERE meter_id=? AND datetime(created_at)>=datetime(?) AND datetime(created_at)<=datetime(?)
               ORDER BY created_at""",
            (meter["meter_id"], started_at, finished_at),
        ).fetchall()
        if len(rows) >= 2:
            total += max(0.0, float(rows[-1][0] or 0) - float(rows[0][0] or 0)) * meter["multiplier"]
    return total


def _theoretical_kwh(started_at: str, finished_at: str) -> float:
    row = db.execute(
        """SELECT COALESCE(SUM(kwh_consumed),0) FROM energy_audit
           WHERE datetime(reading_time) >= datetime(?) AND datetime(reading_time) <= datetime(?)""",
        (started_at, finished_at),
    ).fetchone()
    return float(row[0] or 0) if row else 0.0


def summarize_period(period_id: str) -> dict:
    row = db.execute(
        """SELECT period_id,started_at,finished_at,status,theoretical_kwh,actual_kwh,
                  diff_kwh,diff_rate,is_anomaly,note
           FROM energy_periods WHERE period_id=?""",
        (period_id,),
    ).fetchone()
    if not row:
        return {}
    return {
        "period_id": row[0],
        "started_at": row[1] or "",
        "finished_at": row[2] or "",
        "status": row[3] or "",
        "theoretical_kwh": float(row[4] or 0),
        "actual_kwh": float(row[5] or 0),
        "diff_kwh": float(row[6] or 0),
        "diff_rate": float(row[7] or 0),
        "is_anomaly": bool(int(row[8] or 0)),
        "note": row[9] or "",
    }


def upload_energy_report_to_cloud(period_id: str, summary: dict) -> bool:
    try:
        import urllib.request as _ur
        import urllib.error as _ue

        worker = (db.get_config("cloud_worker_url") or "").strip().rstrip("/")
        if not worker:
            return False
        hotel_id = (db.get_config("hotel_id") or db.get_config("hotel_name") or "UNKNOWN").strip()
        payload = {**summary, "hotel_id": hotel_id}
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        url = f"{worker}/api/energy-report"
        headers = {"Content-Type": "application/json"}
        headers.update(signature_headers("POST", url, body, subject=hotel_id))
        req = _ur.Request(
            url,
            data=body,
            method="POST",
            headers=headers,
        )
        with _ur.urlopen(req, timeout=8) as resp:
            ok = 200 <= resp.status < 300
    except (_ue.URLError, _ue.HTTPError, TimeoutError, OSError, ValueError):
        return False
    if ok:
        db.set_config("last_energy_report_uploaded_at", _now_iso())
    return ok


def finalize_energy_period(period_id: str, *, operator_id: str = "SYSTEM") -> dict:
    started_at, _ = _period_bounds(period_id)
    finished_at = _now_iso()
    actual = _actual_kwh(started_at, finished_at)
    theoretical = _theoretical_kwh(started_at, finished_at)
    diff = actual - theoretical
    base = max(theoretical, 1.0)
    rate = abs(diff) / base
    anomaly = rate >= ENERGY_ANOMALY_RATE
    status = "ANOMALY" if anomaly else "COMPLETED"
    db.execute(
        """UPDATE energy_periods SET finished_at=?,status=?,operator_id=?,
              theoretical_kwh=?,actual_kwh=?,diff_kwh=?,diff_rate=?,is_anomaly=?
           WHERE period_id=?""",
        (finished_at, status, operator_id or "SYSTEM", theoretical, actual, diff, rate, 1 if anomaly else 0, period_id),
    )
    db.set_config("last_energy_audit_at", finished_at)
    result = summarize_period(period_id)
    result["cloud_uploaded"] = upload_energy_report_to_cloud(period_id, result)
    if anomaly:
        try:
            from telegram_shadow import telegram_thread
            telegram_thread.send_alert_sync(format_energy_alert(result))
        except Exception:
            pass
    return result


def format_energy_alert(summary: dict) -> str:
    return (
        "⚡ <b>能耗对账异常</b>\n"
        f"实际用电：{summary.get('actual_kwh', 0):.2f} 千瓦时\n"
        f"理论用电：{summary.get('theoretical_kwh', 0):.2f} 千瓦时\n"
        f"差异：{summary.get('diff_kwh', 0):+.2f} 千瓦时 "
        f"({summary.get('diff_rate', 0) * 100:.1f}%)\n"
        "请安排电工核查电表、空调和公共区域用电。"
    )


def list_periods(limit: int = 20) -> list[dict]:
    rows = db.execute(
        "SELECT period_id FROM energy_periods ORDER BY started_at DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [summarize_period(r[0]) for r in rows]

