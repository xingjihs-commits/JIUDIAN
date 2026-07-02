"""
bridgecore/rx_monitor.py — RX 监控器

后台线程持续探测桥接层链路健康状态，检测到异常后通知熔断管理器。

探测策略：
1. ping — 调用 bridge.ping() 确认子进程响应
2. keepalive — 调用 bridge.keepalive() 确认 USB 正常
3. read_card — 尝试读卡，确认硬件可访问

三种策略优先级递减，ping 最快最轻量。
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any, Callable, Optional

from . import config
from .fault_manager import FaultManager, FaultKind

logger = logging.getLogger(__name__)


class RxMonitor:
    """RX 监控器 — 后台守护线程。

    使用方式：
        monitor = RxMonitor(bridge, fault_manager)
        monitor.start()   # 启动后台探测线程
        ...
        monitor.stop()    # 停止
    """

    def __init__(
        self,
        bridge: Any,
        fault_manager: FaultManager,
    ):
        cfg = config.get_settings().rx_monitor
        self._bridge = bridge
        self._fm = fault_manager
        self._interval = cfg.interval
        self._fail_threshold = cfg.fail_threshold
        self._probe_timeout = cfg.probe_timeout

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._consecutive_failures = 0

    # ── 生命周期 ────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._consecutive_failures = 0
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="bridgecore-rxmon",
        )
        self._thread.start()
        logger.info("[RxMonitor] 已启动 (间隔 %.1fs, 阈值 %d)",
                     self._interval, self._fail_threshold)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("[RxMonitor] 已停止")

    @property
    def running(self) -> bool:
        return self._running

    # ── 主循环 ──────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            time.sleep(self._interval)

            if not self._running:
                break

            # 如果已熔断，跳过探测
            if self._fm.is_faulted():
                self._consecutive_failures = 0
                continue

            healthy = self._probe_all()

            if healthy:
                if self._consecutive_failures > 0:
                    logger.info("[RxMonitor] 链路恢复 (连续失败 %d → 0)",
                                 self._consecutive_failures)
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
                logger.warning(
                    "[RxMonitor] 探测失败 #%d/%d",
                    self._consecutive_failures, self._fail_threshold,
                )
                if self._consecutive_failures >= self._fail_threshold:
                    logger.critical(
                        "[RxMonitor] 连续 %d 次探测失败，触发熔断",
                        self._consecutive_failures,
                    )
                    self._fm.force_fuse(
                        f"RxMonitor: 连续 {self._consecutive_failures} 次探测失败",
                        kind=FaultKind.HARDWARE,
                    )
                    self._consecutive_failures = 0  # 复位，避免重复触发

    def _probe_all(self) -> bool:
        """多策略探测。任一策略成功即视为链路正常。"""
        # 策略1：心跳探测（最轻量）
        if self._try_probe("ping", self._bridge.ping, timeout=self._probe_timeout):
            return True

        # 策略2：保活探测
        with contextlib.suppress(Exception):
            if hasattr(self._bridge, "keepalive") and callable(self._bridge.keepalive):
                if self._try_probe("keepalive", self._bridge.keepalive, timeout=self._probe_timeout * 2):
                    return True

        # 策略3：读卡探测（最重）
        with contextlib.suppress(Exception):
            if hasattr(self._bridge, "read_card") and callable(self._bridge.read_card):
                if self._try_probe("read_card", lambda: self._bridge.read_card(d12=1), timeout=self._probe_timeout * 3):
                    return True

        return False

    def _try_probe(self, name: str, fn: Callable, *, timeout: float) -> bool:
        """尝试一次探测，返回是否成功。"""
        if not self._running:
            return True
        try:
            # 设置探针标记，Observer 会跳过录制这些调用
            try:
                self._bridge._probe_call = True
            except Exception:
                pass
            result = fn()
            if isinstance(result, dict):
                return result.get("ok", False)
            # bool / int / str 等隐式转 bool
            return bool(result)
        except Exception:
            return False
        finally:
            try:
                self._bridge._probe_call = False
            except Exception:
                pass
