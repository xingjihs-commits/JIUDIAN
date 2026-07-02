"""
bridgecore/fault_manager.py — 熔断管理器 (FaultManager)

工业级熔断机制：

1. 指数退避重试
2. 错误分类（网络/协议/硬件/未知）
3. 可配阈值 + 冷却期
4. 完整诊断报告
5. 线程安全

熔断状态机：
  NORMAL → (连续失败达阈值) → FAULTED → (冷却期过后) → RECOVERING → NORMAL
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import config

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 错误分类
# ──────────────────────────────────────────────────────────────────

class FaultKind:
    """熔断原因分类。"""
    HARDWARE = "hardware"      # 硬件无响应、USB 断开
    PROTOCOL = "protocol"      # DLL 返回错误码、协议异常
    NETWORK = "network"        # 子进程崩溃、通讯超时
    TIMEOUT = "timeout"        # 调用超时
    UNKNOWN = "unknown"        # 未分类


# ──────────────────────────────────────────────────────────────────
# 异常
# ──────────────────────────────────────────────────────────────────

class FaultTriggered(RuntimeError):
    """熔断器已触发。"""

    def __init__(self, message: str, kind: str = FaultKind.UNKNOWN,
                 diagnostics: Optional[dict] = None):
        super().__init__(message)
        self.kind = kind
        self.diagnostics = diagnostics or {}


# ──────────────────────────────────────────────────────────────────
# 熔断器
# ──────────────────────────────────────────────────────────────────

class FaultManager:
    """
    熔断管理器。

    特性：
    - 指数退避重试（退避时间 = 基数 × 2 的失败次数次方，上限最大）
    - 自动冷却复位
    - 错误分类统计
    - 丰富诊断报告
    """

    def __init__(self, threshold: Optional[int] = None):
        cfg = config.get_settings().fault
        self._threshold = threshold if threshold is not None else cfg.threshold
        self._backoff_base = cfg.backoff_base
        self._backoff_max = cfg.backoff_max
        self._cooldown = cfg.cooldown

        self._consecutive_fails = 0
        self._total_fails = 0
        self._total_attempts = 0
        self._faulted = False
        self._fault_time: Optional[float] = None
        self._last_success_time: Optional[float] = None

        # 按类别统计
        self._fail_by_kind: dict[str, int] = {}
        self._fail_by_fn: dict[str, int] = {}

        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []

        # ── 熔断回调（由恐慌恢复等注册） ─────
        self._on_fuse_callbacks: list[Callable[[str], None]] = []

    def on_fuse(self, callback: Callable[[str], None]) -> None:
        """注册熔断触发回调。回调参数: kind (str)"""
        self._on_fuse_callbacks.append(callback)

    # ── 属性 ────────────────────────────────────────────────

    @property
    def consecutive_fails(self) -> int:
        return self._consecutive_fails

    @property
    def total_fails(self) -> int:
        return self._total_fails

    @property
    def total_attempts(self) -> int:
        return self._total_attempts

    @property
    def threshold(self) -> int:
        return self._threshold

    def is_faulted(self) -> bool:
        """熔断是否正在生效。冷却期过后自动复位。"""
        with self._lock:
            if not self._faulted:
                return False
            if self._fault_time is not None:
                elapsed = time.time() - self._fault_time
                if elapsed > self._cooldown:
                    logger.info("[FaultManager] 冷却期 %.1fs 已过，自动复位", elapsed)
                    self._reset()
                    return False
            return True

    def remaining_cooldown(self) -> float:
        """返回剩余冷却时间（秒）。"""
        with self._lock:
            if not self._faulted or self._fault_time is None:
                return 0.0
            remaining = self._cooldown - (time.time() - self._fault_time)
            return max(0.0, remaining)

    def get_backoff(self) -> float:
        """返回当前退避时间（秒）。"""
        with self._lock:
            if self._consecutive_fails == 0:
                return 0.0
            backoff = self._backoff_base * (2 ** (self._consecutive_fails - 1))
            return min(backoff, self._backoff_max)

    def get_last_success_ago(self) -> Optional[float]:
        if self._last_success_time is None:
            return None
        return time.time() - self._last_success_time

    # ── 记录与判定 ──────────────────────────────────────────

    def record_attempt(self, fn_name: str, result: dict[str, Any], *,
                       kind: str = "") -> bool:
        """
        记录一次调用结果。

        Args:
            fn_name: 调用方法名
            result: 调用结果字典
            kind: 错误分类（留空则自动判断）

        Returns:
            True 表示熔断已触发
        """
        with self._lock:
            self._total_attempts += 1
            self._fail_by_fn.setdefault(fn_name, 0)

            is_ok = self._is_ok(result)

            if is_ok:
                self._consecutive_fails = 0
                self._last_success_time = time.time()
                return False

            # 失败处理
            self._consecutive_fails += 1
            self._total_fails += 1
            self._fail_by_fn[fn_name] += 1

            if not kind:
                kind = self._classify_error(result)
            self._fail_by_kind[kind] = self._fail_by_kind.get(kind, 0) + 1

            backoff = self.get_backoff()
            self._log_event({
                "type": "fail",
                "time": _dt.datetime.now().isoformat(timespec="microseconds"),
                "fn_name": fn_name,
                "kind": kind,
                "consecutive": self._consecutive_fails,
                "threshold": self._threshold,
                "backoff": backoff,
                "total_attempts": self._total_attempts,
            })

            logger.warning(
                "[FaultManager] %s #%d: %s (连续 %d/%d, 退避 %.1fs)",
                fn_name, self._total_attempts, kind,
                self._consecutive_fails, self._threshold, backoff,
            )

            if self._consecutive_fails >= self._threshold:
                self._trigger_fuse(fn_name, result, kind)
                return True

            return False

    def force_fuse(self, reason: str, *, kind: str = FaultKind.HARDWARE) -> None:
        """手动触发熔断。"""
        with self._lock:
            self._trigger_fuse("system", {"error": reason}, kind)

    # ── 错误判定 ────────────────────────────────────────────

    @staticmethod
    def _is_ok(result: dict[str, Any]) -> bool:
        if result is None:
            return False
        if not result.get("ok", False):
            return False
        if result.get("error"):
            return False
        ret = result.get("ret")
        if ret is not None:
            try:
                if int(ret) != 0:
                    return False
            except (ValueError, TypeError):
                pass
        return True

    @staticmethod
    def _classify_error(result: dict[str, Any]) -> str:
        """自动分类错误类型。"""
        error = result.get("error", "") or str(result)
        error_lower = error.lower()

        # 超时
        if "timeout" in error_lower or "超时" in error:
            return FaultKind.TIMEOUT

        # 网络/进程
        if any(kw in error_lower for kw in ("bridge", "subprocess", "子进程",
                                             "connection", "pipe", "broken")):
            return FaultKind.NETWORK

        # 硬件
        if any(kw in error_lower for kw in ("usb", "device", "硬件", "d12",
                                             "initialize", "init", "card reader")):
            return FaultKind.HARDWARE

        # DLL/协议
        if any(kw in error_lower for kw in ("dll", "ret", "error code",
                                             "invalid", "protocol", "协议",
                                             "checksum", "校验")):
            return FaultKind.PROTOCOL

        return FaultKind.UNKNOWN

    # ── 熔断触发 ────────────────────────────────────────────

    def _trigger_fuse(self, fn_name: str, result: dict[str, Any],
                      kind: str = FaultKind.UNKNOWN) -> None:
        if self._faulted:
            return

        self._faulted = True
        self._fault_time = time.time()

        info = {
            "type": "fuse_triggered",
            "time": _dt.datetime.now().isoformat(timespec="microseconds"),
            "trigger_fn": fn_name,
            "kind": kind,
            "consecutive_fails": self._consecutive_fails,
            "total_attempts": self._total_attempts,
            "total_fails": self._total_fails,
            "last_result": {
                "ok": result.get("ok"),
                "ret": result.get("ret"),
                "error": result.get("error") if isinstance(result.get("error"), str) else str(result.get("error", "")),
            },
            "cooldown": self._cooldown,
            "mode_after": "passthrough",
        }
        self._log_event(info)

        logger.critical(
            "[FaultManager] 熔断触发! %s %s 连续 %d 次失败 → 透传模式 "
            "(总尝试 %d / 总失败 %d, 冷却 %.1fs)",
            fn_name, kind, self._consecutive_fails,
            self._total_attempts, self._total_fails, self._cooldown,
        )

        # ── 通知熔断回调 ──
        for cb in self._on_fuse_callbacks:
            try:
                cb(kind)
            except Exception:
                pass

    def _reset(self) -> None:
        self._faulted = False
        self._fault_time = None
        self._consecutive_fails = 0
        self._log_event({
            "type": "reset",
            "time": _dt.datetime.now().isoformat(timespec="microseconds"),
        })
        logger.info("[FaultManager] 已自动复位")

    # ── 内部日志 ────────────────────────────────────────────

    def _log_event(self, event: dict) -> None:
        self._events.append(event)
        # 保留最近 1000 条，防内存泄露
        if len(self._events) > 1000:
            self._events = self._events[-500:]

    # ── 诊断报告 ────────────────────────────────────────────

    def get_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def get_diagnostic_report(self) -> dict[str, Any]:
        """生成完整诊断报告。"""
        with self._lock:
            return {
                "status": "faulted" if self._faulted else "normal",
                "consecutive_fails": self._consecutive_fails,
                "threshold": self._threshold,
                "total_attempts": self._total_attempts,
                "total_fails": self._total_fails,
                "fail_rate": self._total_fails / max(self._total_attempts, 1),
                "fault_kind_summary": dict(self._fail_by_kind),
                "fail_by_method": dict(self._fail_by_fn),
                "backoff_current": self.get_backoff(),
                "backoff_base": self._backoff_base,
                "backoff_max": self._backoff_max,
                "cooldown": self._cooldown,
                "remaining_cooldown": self.remaining_cooldown(),
                "last_success_seconds_ago": self.get_last_success_ago(),
                "event_count": len(self._events),
            }

    def save_diagnostic_log(self, filepath: str | Path) -> str:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        report = self.get_diagnostic_report()
        report["events"] = self._events
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2,
                      default=str)
        logger.info("[FaultManager] 诊断报告已保存到 %s", path)
        return str(path)
