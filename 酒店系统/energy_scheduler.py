"""
energy_scheduler.py — C0-delta 30 天能耗对账提醒
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from PySide6.QtCore import QObject, QTimer

from database import db
import energy_audit_engine as engine
import logging
logger = logging.getLogger(__name__)

ENERGY_PERIOD_DAYS = 30
REMINDER_LEAD_DAYS = 3
CHECK_INTERVAL_MS = 60 * 60 * 1000
_MIN_REMIND_GAP_HOURS = 23


def _parse_iso(s: Optional[str]) -> Optional[_dt.datetime]:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def last_energy_audit_at() -> Optional[_dt.datetime]:
    return _parse_iso(db.get_config("last_energy_audit_at"))


def next_energy_audit_due() -> Optional[_dt.datetime]:
    anchor = last_energy_audit_at()
    if not anchor:
        row = db.execute("SELECT MIN(created_at) FROM energy_meter_readings").fetchone()
        anchor = _parse_iso(row[0]) if row and row[0] else None
    if not anchor:
        return None
    return anchor + _dt.timedelta(days=ENERGY_PERIOD_DAYS)


def days_until_due() -> Optional[float]:
    due = next_energy_audit_due()
    if not due:
        return None
    return (due - _dt.datetime.now()).total_seconds() / 86400.0


def is_in_reminder_window() -> bool:
    d = days_until_due()
    return d is not None and d <= REMINDER_LEAD_DAYS and d > -30


def _can_remind_now() -> bool:
    last = _parse_iso(db.get_config("last_energy_reminder_at"))
    if not last:
        return True
    return (_dt.datetime.now() - last).total_seconds() >= _MIN_REMIND_GAP_HOURS * 3600


def _mark_reminded() -> None:
    db.set_config("last_energy_reminder_at", _dt.datetime.now().isoformat(timespec="seconds"))


def _compose_reminder() -> str:
    due = next_energy_audit_due()
    d = days_until_due()
    due_str = due.strftime("%Y-%m-%d %H:%M") if due else "未定"
    if d is not None and d < 0:
        return f"⚡ <b>能耗对账已逾期</b>\n应于 {due_str} 完成，请尽快安排电工录表并对账。"
    return f"⚡ <b>30 天能耗对账提醒</b>\n距离截止约 {max(0, int(d or 0))} 天（{due_str}），请安排电工录入电表。"


class EnergyScheduler(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_once)
        self._main_window = parent

    def start(self) -> None:
        self._timer.start(CHECK_INTERVAL_MS)
        QTimer.singleShot(10_000, self.check_once)

    def check_once(self) -> None:
        try:
            if not is_in_reminder_window() or not _can_remind_now():
                return
            msg = _compose_reminder()
            try:
                from telegram_shadow import telegram_thread
                telegram_thread.send_alert_sync(msg)
            except Exception:
                pass
            try:
                from event_bus import bus
                bus.show_success_overlay.emit("能耗对账提醒")
            except Exception:
                pass
            _mark_reminded()
        except Exception as exc:
            logger.warning("[energy_scheduler] check_once 异常: %s", exc)


_scheduler: Optional[EnergyScheduler] = None


def start_energy_scheduler(main_window=None) -> EnergyScheduler:
    global _scheduler
    if _scheduler is None:
        engine.ensure_default_meter()
        _scheduler = EnergyScheduler(parent=main_window)
        _scheduler.start()
    return _scheduler

