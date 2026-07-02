"""
bridgecore/keepalive.py — 桥接心跳保活

背景：V9 发卡器 30 秒无操作自动断开。采样/分析过程中用户需要反复操作
发卡器（读空白卡、原厂写卡、读已写卡），中间停顿超过 30 秒就要重连。

本模块：
- 用独立线程每 15 秒发一次 ping / buzzer，保持发卡器连接
- 线程安全，不干扰主操作
- 使用桥接层已有的 ping() / buzzer() 方法

用法：
    keepalive = KeepAlive(bridge)
    keepalive.start()       # 开始保活
    # ... 做你的采样/分析 ...
    keepalive.stop()        # 结束保活
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Callable

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 15  # 秒
"""保活心跳间隔。"""


class KeepAlive:
    """桥接心跳保活线程。

    每 _interval 秒调用一次 heartbeat_fn，防止发卡器因长时间无操作自动断开。

    Args:
        bridge: 桥接器实例（需要有 ping() 或 buzzer() 方法）。
        interval: 心跳间隔（秒），默认 15。
        heartbeat_fn: 可选的自定义心跳函数，默认使用 bridge.ping()。
                      如果 ping 返回 False，自动降级为 bridge.buzzer()。
    """

    def __init__(
        self,
        bridge,
        interval: int = _DEFAULT_INTERVAL,
        heartbeat_fn: Optional[Callable[[], bool]] = None,
    ):
        self._bridge = bridge
        self._interval = max(5, interval)
        self._heartbeat_fn = heartbeat_fn or self._default_heartbeat
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _default_heartbeat(self) -> bool:
        """默认心跳：先试 ping，失败则 buzzer。"""
        try:
            if callable(getattr(self._bridge, "ping", None)):
                ok = self._bridge.ping()
                if ok:
                    logger.debug("[KeepAlive] ping OK")
                    return True
        except Exception:
            pass
        try:
            if callable(getattr(self._bridge, "buzzer", None)):
                self._bridge.buzzer(d12=1, t=10)
                logger.debug("[KeepAlive] buzzer OK")
                return True
        except Exception:
            pass
        return False

    def _run(self):
        """心跳循环。"""
        logger.info(
            "[KeepAlive] 保活线程启动 (interval=%ds)", self._interval
        )
        failures = 0
        while not self._stop_event.wait(self._interval):
            try:
                ok = self._heartbeat_fn()
                if ok:
                    failures = 0
                else:
                    failures += 1
                    logger.warning(
                        "[KeepAlive] 心跳第 %d 次失败", failures
                    )
                if failures >= 5:
                    logger.error(
                        "[KeepAlive] 连续 5 次心跳失败，停止保活"
                    )
                    break
            except Exception as e:
                failures += 1
                logger.warning(
                    "[KeepAlive] 心跳异常: %s", e
                )
        self._running = False
        logger.info("[KeepAlive] 保活线程结束")

    def start(self):
        """启动保活线程。"""
        if self._running:
            logger.debug("[KeepAlive] 已经在运行")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="keepalive",
            daemon=True,
        )
        self._thread.start()
        self._running = True
        logger.info("[KeepAlive] 已启动")

    def stop(self):
        """停止保活线程。"""
        self._stop_event.set()
        self._running = False
        logger.info("[KeepAlive] 停止信号已发送")

    def join(self, timeout: float = 5.0):
        """等待保活线程结束。"""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        self.join()
