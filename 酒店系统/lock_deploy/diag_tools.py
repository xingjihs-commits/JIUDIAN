"""
lock_deploy/diag_tools.py — 门锁诊断工具增强

提供：
- batch_read_test(count=100): 批量读卡统计
- retry_write_with_backoff(max_retries=3): 发卡失败自动重试
- log_lock_event(event_type, room_id, card_type, result): 门锁事件日志
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 事件日志路径
# ──────────────────────────────────────────────────────────────

_EVENT_LOG_DIR = Path(os.path.expanduser("~/AppData/Local/SolidHotel")) / "lock_events"
_EVENT_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _event_log_path() -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d")
    return _EVENT_LOG_DIR / f"lock_events_{stamp}.jsonl"


# ──────────────────────────────────────────────────────────────
# log_lock_event — 门锁事件日志
# ──────────────────────────────────────────────────────────────

def log_lock_event(
    event_type: str,
    room_id: str = "",
    card_type: str = "",
    result: str = "",
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """记录门锁事件到本地 JSONL 日志。

    Args:
        event_type: 事件类型 (发卡, 擦卡, 读卡, 诊断)
        room_id: 房间号
        card_type: 卡类型 (客人卡, 总卡, 权限卡等)
        result: 结果 (成功, 失败, 错误)
        extra: 额外信息字典
    """
    entry = {
        "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
        "event_type": event_type,
        "room_id": str(room_id),
        "card_type": str(card_type),
        "result": str(result),
    }
    if extra:
        entry["extra"] = extra

    try:
        log_path = _event_log_path()
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("无法写入锁事件日志: %s", e)


def get_lock_events(
    date_str: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """读取指定日期（或今天）的门锁事件日志。

    Args:
        date_str: 日期字符串 YYYYMMDD，默认今天
        event_type: 过滤事件类型，None 表示全部
        limit: 最大返回条数

    Returns:
        事件列表，按时间倒序。
    """
    if date_str is None:
        date_str = _dt.datetime.now().strftime("%Y%m%d")
    log_path = _EVENT_LOG_DIR / f"lock_events_{date_str}.jsonl"

    if not log_path.is_file():
        return []

    events: List[Dict[str, Any]] = []
    try:
        for line in reversed(log_path.read_text(encoding="utf-8").strip().splitlines()):
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type and entry.get("event_type") != event_type:
                continue
            events.append(entry)
            if len(events) >= limit:
                break
    except Exception as e:
        logger.warning("读取锁事件日志失败: %s", e)

    return events


# ──────────────────────────────────────────────────────────────
# batch_read_test — 批量读卡统计
# ──────────────────────────────────────────────────────────────

def batch_read_test(
    adapter,
    count: int = 100,
    *,
    on_progress: Optional[callable] = None,
) -> Dict[str, Any]:
    """批量读卡测试：统计读卡成功率、耗时分布。

    Args:
        adapter: LockAdapter 实例（需要已初始化）
        count: 读卡次数
        on_progress: 进度回调 (当前索引, 总数, 已耗时毫秒)

    Returns:
        {
            "total": 测试次数,
            "success": 成功次数,
            "fail": 失败次数,
            "success_rate": 成功率 (0.0-1.0),
            "avg_ms": 平均耗时 (毫秒),
            "min_ms": 最小耗时,
            "max_ms": 最大耗时,
            "times": [每次耗时列表],
            "errors": [失败原因列表],
        }
    """
    if adapter is None:
        return {"error": "适配器未提供"}

    if not adapter.is_open and not adapter.initialize():
        return {"error": "发卡器未就绪"}

    times_ms: List[float] = []
    errors: List[str] = []
    success_count = 0
    fail_count = 0

    for i in range(count):
        t_start = time.perf_counter()
        try:
            result = adapter.read_card_uid() or adapter.read_card_raw()
            elapsed = (time.perf_counter() - t_start) * 1000.0
            if result:
                success_count += 1
                times_ms.append(elapsed)
            else:
                fail_count += 1
                errors.append(f"第{i + 1}次: 读卡返回空")
                times_ms.append(elapsed)
        except Exception as e:
            elapsed = (time.perf_counter() - t_start) * 1000.0
            fail_count += 1
            errors.append(f"第{i + 1}次: {e}")
            times_ms.append(elapsed)

        if on_progress and (i + 1) % 10 == 0:
            try:
                on_progress(i + 1, count, sum(times_ms))
            except Exception:
                pass

        # 日志记录
        log_lock_event(
            "batch_read",
            card_type="read_test",
            result="success" if result else "fail",
            extra={"index": i + 1, "elapsed_ms": round(elapsed, 2)},
        )

    total = count
    success_rate = success_count / total if total > 0 else 0.0

    return {
        "total": total,
        "success": success_count,
        "fail": fail_count,
        "success_rate": round(success_rate, 4),
        "avg_ms": round(sum(times_ms) / len(times_ms), 2) if times_ms else 0,
        "min_ms": round(min(times_ms), 2) if times_ms else 0,
        "max_ms": round(max(times_ms), 2) if times_ms else 0,
        "times": [round(t, 2) for t in times_ms],
        "errors": errors[:20],  # 最多保留 20 条错误
    }


# ──────────────────────────────────────────────────────────────
# retry_write_with_backoff — 发卡失败自动重试
# ──────────────────────────────────────────────────────────────

def retry_write_with_backoff(
    adapter,
    card_hex: str,
    *,
    max_retries: int = 3,
    base_delay_ms: int = 500,
) -> Dict[str, Any]:
    """发卡失败自动重试（带指数退避）。

    当 DirectWriteUSB 返回非零时自动重试，每次重试前递增延迟。

    Args:
        adapter: LockAdapter 实例
        card_hex: 要写入的卡数据十六进制字符串
        max_retries: 最大重试次数（含首次）
        base_delay_ms: 首次重试基础延迟（毫秒），每次×2

    Returns:
        {
            "success": bool,
            "attempt": 第几次成功,
            "total_attempts": 总尝试次数,
            "errors": [每次失败原因],
            "total_ms": 总耗时,
        }
    """
    if adapter is None:
        return {"success": False, "error": "适配器未提供"}

    t_start = time.perf_counter()
    errors: List[str] = []

    for attempt in range(1, max_retries + 1):
        try:
            # 尝试写卡
            if hasattr(adapter, '_ensure_bridge'):
                bridge = adapter._ensure_bridge()
                if bridge is not None:
                    resp = bridge.direct_write_usb(d12=1, card_hex=card_hex, timeout=6.0)
                    ret = int(resp.get("ret", -1))
                    if ret == 0:
                        log_lock_event(
                            "issue_card_retry",
                            card_type="write",
                            result="success",
                            extra={"attempt": attempt, "total_attempts": attempt},
                        )
                        return {
                            "success": True,
                            "attempt": attempt,
                            "total_attempts": attempt,
                            "errors": errors,
                            "total_ms": round((time.perf_counter() - t_start) * 1000, 2),
                        }
                    errors.append(f"第{attempt}次: DLL 返回 {ret}")
                else:
                    errors.append(f"第{attempt}次: 桥接未就绪")
            else:
                errors.append(f"第{attempt}次: 适配器不支持 DirectWriteUSB")
                break
        except Exception as e:
            errors.append(f"第{attempt}次: {e}")

        log_lock_event(
            "issue_card_retry",
            card_type="write",
            result="fail",
            extra={"attempt": attempt, "error": errors[-1]},
        )

        if attempt < max_retries:
            delay = base_delay_ms * (2 ** (attempt - 1)) / 1000.0
            time.sleep(delay)

    return {
        "success": False,
        "attempt": None,
        "total_attempts": max_retries,
        "errors": errors,
        "total_ms": round((time.perf_counter() - t_start) * 1000, 2),
    }


def retry_issue_card_with_backoff(
    adapter,
    card_type: str = "guest",
    max_retries: int = 3,
    **card_params,
) -> tuple[bool, Any, Dict[str, Any]]:
    """发卡重试：调用适配器的发卡方法，失败自动重试。

    Args:
        adapter: LockAdapter 实例
        card_type: 卡类型 (客人卡, 总卡, 楼栋卡, 楼层卡等)
        max_retries: 最大重试次数
        **card_params: 传给发卡方法的参数

    Returns:
        (success, CardResult, stats_dict)
    """
    method_name = f"issue_{card_type}_card"
    method = getattr(adapter, method_name, None)

    if method is None:
        result = adapter.issue_guest_card(**card_params) if card_type == "guest" else None
        if result is None:
            return False, None, {"error": f"不支持的卡类型: {card_type}"}
    else:
        result = None

    errors: List[str] = []
    t_start = time.perf_counter()

    for attempt in range(1, max_retries + 1):
        try:
            if method is not None:
                result = method(**card_params)
            if result is not None and result.success:
                log_lock_event(
                    "issue_card",
                    room_id=str(card_params.get("room_id", card_params.get("lock_no", ""))),
                    card_type=card_type,
                    result="success",
                    extra={"attempt": attempt},
                )
                stats = {
                    "attempt": attempt,
                    "total_attempts": attempt,
                    "errors": errors,
                    "total_ms": round((time.perf_counter() - t_start) * 1000, 2),
                }
                return True, result, stats
            errors.append(f"第{attempt}次: {result.error if result else '未知错误'}")
        except Exception as e:
            errors.append(f"第{attempt}次: {e}")

        log_lock_event(
            "issue_card",
            room_id=str(card_params.get("room_id", card_params.get("lock_no", ""))),
            card_type=card_type,
            result="fail",
            extra={"attempt": attempt, "error": errors[-1]},
        )

        if attempt < max_retries:
            time.sleep(0.5 * (2 ** (attempt - 1)))

    stats = {
        "attempt": None,
        "total_attempts": max_retries,
        "errors": errors,
        "total_ms": round((time.perf_counter() - t_start) * 1000, 2),
    }
    return False, result, stats
