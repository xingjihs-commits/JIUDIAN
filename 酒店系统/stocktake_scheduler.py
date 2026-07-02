"""
stocktake_scheduler.py — C0-gamma 周期盘点调度器

设计意图（来自 商业重构执行清单.md / C0-gamma）：
- 每 15 天做一次手动盘点
- 盘点前 2 天给老板推送提醒，让老板安排人员盘点
- 客户端启动后挂在主循环里，每小时检查一次是否到推送窗口
- 已到期未盘点 → 主界面顶部红色横幅 + 推送提醒
- 完成盘点会调用 audit_engine.finalize_session()，自动更新 last_periodic_stocktake_at

驱动两个 system_config：
- last_periodic_stocktake_at：上一次完成盘点的时间（audit_engine.finalize 写入）
- last_periodic_reminder_at：上一次提醒推送的时间（避免每小时刷屏）

不依赖系统计划任务，直接用定时器，跟着主进程跑（断电后启动会立刻补检查）。
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from PySide6.QtCore import QTimer, QObject

from database import db
import logging
logger = logging.getLogger(__name__)


STOCKTAKE_PERIOD_DAYS = 15
REMINDER_LEAD_DAYS = 2
CHECK_INTERVAL_MS = 60 * 60 * 1000  # 1 小时


# ─────────────────────────────────────────────────────────────────────────────
#  时间判断
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> _dt.datetime:
    return _dt.datetime.now()


def _parse_iso(s: Optional[str]) -> Optional[_dt.datetime]:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        # 兼容 "YYYY-MM-DD HH:MM:SS" 等
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return _dt.datetime.strptime(s, fmt)
            except ValueError:
                pass
        return None


def last_stocktake_at() -> Optional[_dt.datetime]:
    return _parse_iso(db.get_config("last_periodic_stocktake_at"))


def baseline_done_at() -> Optional[_dt.datetime]:
    return _parse_iso(db.get_config("initial_stocktake_done_at"))


def next_stocktake_due() -> Optional[_dt.datetime]:
    """下一次必须盘点的截止时间。基准：last_periodic_stocktake_at（没有则用 initial）。"""
    anchor = last_stocktake_at() or baseline_done_at()
    if not anchor:
        return None
    return anchor + _dt.timedelta(days=STOCKTAKE_PERIOD_DAYS)


def days_until_due() -> Optional[float]:
    due = next_stocktake_due()
    if not due:
        return None
    delta = due - _now()
    return delta.total_seconds() / 86400.0


def is_in_reminder_window() -> bool:
    """是否进入提醒窗口：距离到期 <= REMINDER_LEAD_DAYS 天 且 还没到期超 30 天（避免远古）。"""
    d = days_until_due()
    if d is None:
        return False
    return d <= REMINDER_LEAD_DAYS and d > -30


def is_overdue() -> bool:
    d = days_until_due()
    return d is not None and d < 0


# ─────────────────────────────────────────────────────────────────────────────
#  防刷屏：每 23 小时最多推一次
# ─────────────────────────────────────────────────────────────────────────────
_MIN_REMIND_GAP_HOURS = 23


def _can_remind_now() -> bool:
    last = _parse_iso(db.get_config("last_periodic_reminder_at"))
    if not last:
        return True
    return (_now() - last).total_seconds() >= _MIN_REMIND_GAP_HOURS * 3600


def _mark_reminded() -> None:
    db.set_config("last_periodic_reminder_at", _now().isoformat(timespec="seconds"))


# ─────────────────────────────────────────────────────────────────────────────
#  推送通知
# ─────────────────────────────────────────────────────────────────────────────

def _send_telegram(msg: str) -> bool:
    try:
        from telegram_shadow import telegram_thread
        if telegram_thread.isRunning():
            telegram_thread.send_alert_sync(msg)
            return True
    except Exception as exc:
        logger.warning("[stocktake_scheduler] Telegram 推送异常: %s", exc)
    return False


def _compose_reminder() -> str:
    d = days_until_due()
    due = next_stocktake_due()
    due_str = due.strftime("%Y-%m-%d %H:%M") if due else "未定"
    overdue = is_overdue()
    if overdue:
        head = "🚨 *周期盘点已逾期*"
        action = (
            f"已经超过 15 天没盘点（应于 {due_str} 完成），"
            "客户端将开始在『账实差异』标题栏标红，并把所有未盘的 SKU 视为可疑。"
        )
    else:
        head = "🧾 *15 天周期盘点提醒*"
        days_left = max(0, int(d or 0))
        action = (
            f"距离下一次盘点截止只剩约 *{days_left}* 天（截止 {due_str}）。\n"
            "请安排员工在客户端『账实差异 → 开始盘点』中按真实数量盘库存，"
            "差异 ≥ 5% 的 SKU 会自动锁定并要求解释。"
        )
    return f"{head}\n\n{action}\n\n— Solid 账实差异调度器"


# ─────────────────────────────────────────────────────────────────────────────
#  调度器主循环
# ─────────────────────────────────────────────────────────────────────────────
class StocktakeScheduler(QObject):
    """挂在主窗口上的 QTimer 调度器。
    - 每 1 小时检查一次；进入提醒窗口且未在 23 小时内推过 → 推送 + 客户端横幅。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_once)
        self._main_window = parent

    def start(self) -> None:
        self._timer.start(CHECK_INTERVAL_MS)
        # 启动后 8 秒先做一次（避开首屏抖动）
        QTimer.singleShot(8000, self.check_once)

    def stop(self) -> None:
        self._timer.stop()

    def check_once(self) -> None:
        try:
            if not is_in_reminder_window() and not is_overdue():
                return
            if not _can_remind_now():
                return
            msg = _compose_reminder()
            _send_telegram(msg)
            _mark_reminded()
            self._push_inapp_banner(msg)
        except Exception as exc:
            logger.warning("[stocktake_scheduler] check_once 异常: %s", exc)

    def _push_inapp_banner(self, msg: str) -> None:
        """让主窗口右下角弹一个 toast；找不到 toast 时静默。"""
        win = self._main_window
        if win is None:
            return
        try:
            # 尝试主窗口上挂的事件总线
            from event_bus import bus
            short = msg.splitlines()[0] if msg else "周期盘点提醒"
            bus.show_success_overlay.emit(short)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  对外便捷入口
# ─────────────────────────────────────────────────────────────────────────────
_scheduler_singleton: Optional[StocktakeScheduler] = None


def start_stocktake_scheduler(main_window=None) -> StocktakeScheduler:
    global _scheduler_singleton
    if _scheduler_singleton is None:
        _scheduler_singleton = StocktakeScheduler(parent=main_window)
        _scheduler_singleton.start()
    return _scheduler_singleton


def status_summary() -> dict:
    """给 UI 顶部横幅 / 设置页用的状态摘要。"""
    due = next_stocktake_due()
    d = days_until_due()
    return {
        "last_stocktake_at": (last_stocktake_at() or "").isoformat(timespec="seconds")
            if last_stocktake_at() else "",
        "baseline_done_at": (baseline_done_at() or "").isoformat(timespec="seconds")
            if baseline_done_at() else "",
        "next_due_at": due.isoformat(timespec="seconds") if due else "",
        "days_until_due": round(d, 2) if d is not None else None,
        "in_reminder_window": is_in_reminder_window(),
        "overdue": is_overdue(),
    }
