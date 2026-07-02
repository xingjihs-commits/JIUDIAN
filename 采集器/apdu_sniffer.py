"""
apdu_sniffer.py — 通信信道监听引擎

职责：
1. 通过桥接层记录所有发往门锁DLL的读写操作
2. 为未来 winscard.dll hook 预留接口
3. 产出 APDU 轨迹（每条读/写操作的 hex dump + 时间戳）

实现方式（当前）：
- 代理桥接调用，在每次 read_card / write_card / dll_call 前后记录
- 不依赖系统级 hook（winscard.dll 级监听需要独立 DLL 注入，待工程化）

用法：
    from collector.apdu_sniffer import ApduSniffer
    sniffer = ApduSniffer()
    sniffer.start()
    # ... 所有桥接调用被自动记录 ...
    trace = sniffer.stop()  # 返回 ApduTrace 列表
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .forensic_schema import ApduTrace

logger = logging.getLogger(__name__)


class ApduSniffer:
    """桥接层 APDU 轨迹记录器。

    装在 collector_bridge 和 DLL 之间，透明记录所有通信。
    """

    def __init__(self):
        self._traces: list[ApduTrace] = []
        self._lock = threading.Lock()
        self._active = False
        self._start_time: float = 0.0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def trace_count(self) -> int:
        with self._lock:
            return len(self._traces)

    def start(self):
        """开始记录。"""
        self._traces = []
        self._active = True
        self._start_time = time.monotonic()
        logger.info("APDU 监听已启动")

    def stop(self) -> list[ApduTrace]:
        """停止记录，返回全部轨迹。"""
        self._active = False
        with self._lock:
            traces = list(self._traces)
        logger.info("APDU 监听已停止: %d 条记录", len(traces))
        return traces

    def record_send(self, hex_data: str, label: str = ""):
        """记录一次发送操作。"""
        if not self._active:
            return
        ts = self._timestamp()
        trace = ApduTrace(
            direction="send",
            raw_hex=hex_data,
            timestamp=ts,
        )
        if label:
            trace.raw_hex = f"[{label}] {hex_data}"
        with self._lock:
            self._traces.append(trace)

    def record_recv(self, hex_data: str, label: str = ""):
        """记录一次接收操作。"""
        if not self._active:
            return
        ts = self._timestamp()
        trace = ApduTrace(
            direction="recv",
            raw_hex=hex_data,
            timestamp=ts,
        )
        if label:
            trace.raw_hex = f"[{label}] {hex_data}"
        with self._lock:
            self._traces.append(trace)

    def record_call(self, fn_name: str, params: str, result: str):
        """记录一次 DLL 函数调用。"""
        if not self._active:
            return
        ts = self._timestamp()
        # 发送参数
        with self._lock:
            self._traces.append(ApduTrace(
                direction="send",
                raw_hex=f"[{fn_name}] params={params}",
                timestamp=ts,
            ))
            self._traces.append(ApduTrace(
                direction="recv",
                raw_hex=f"[{fn_name}] result={result}",
                timestamp=ts,
            ))

    def get_traces(self) -> list[ApduTrace]:
        """获取当前已记录的全部轨迹（不清空）。"""
        with self._lock:
            return list(self._traces)

    def clear(self):
        """清空已记录轨迹。"""
        with self._lock:
            self._traces = []

    def _timestamp(self) -> str:
        elapsed = time.monotonic() - self._start_time
        return f"T+{elapsed:.3f}s"


# ── 全局单例 ──────────────────────────────────────────────

_sniffer_instance: Optional[ApduSniffer] = None
_sniffer_lock = threading.Lock()


def get_sniffer() -> ApduSniffer:
    global _sniffer_instance
    with _sniffer_lock:
        if _sniffer_instance is None:
            _sniffer_instance = ApduSniffer()
        return _sniffer_instance


# ── 桥接代理包装器 ────────────────────────────────────────

class SniffedBridge:
    """包装 CollectorBridge，自动记录所有 read_card / write_card / dll_call 操作。

    用法：
        bridge = get_bridge()
        sniffer = get_sniffer()
        sniffer.start()
        sniffed = SniffedBridge(bridge)
        # 后续用 sniffed.read_card(...) 替代 bridge.read_card(...)
    """

    def __init__(self, bridge: Any):
        self._bridge = bridge
        self._sniffer = get_sniffer()

    def read_card(self, d12: int = 1, *, timeout: float = 6.0) -> dict:
        resp = self._bridge.read_card(d12=d12, timeout=timeout)
        if self._sniffer.active and resp.get("ok"):
            out = resp.get("out") or {}
            payload = out.get("payload") or out.get("card_hex") or ""
            if payload:
                self._sniffer.record_recv(str(payload), f"read_card(d12={d12})")
        return resp

    def direct_read_usb(self, *, d12: int = 1, timeout: float = 6.0) -> dict:
        resp = self._bridge.direct_read_usb(d12=d12, timeout=timeout)
        if self._sniffer.active and resp.get("ok"):
            out = resp.get("out") or {}
            payload = out.get("payload") or out.get("card_hex") or ""
            if payload:
                self._sniffer.record_recv(str(payload), f"direct_read_usb(d12={d12})")
        return resp

    def direct_write_usb(self, *, d12: int = 1, card_hex: str,
                         timeout: float = 6.0) -> dict:
        if self._sniffer.active:
            self._sniffer.record_send(card_hex, f"direct_write_usb(d12={d12})")
        resp = self._bridge.direct_write_usb(
            d12=d12, card_hex=card_hex, timeout=timeout
        )
        return resp

    def write_card(self, *, d12: int = 1, card_hex: str,
                   variant: str = "binary", timeout: float = 6.0) -> dict:
        if self._sniffer.active:
            self._sniffer.record_send(card_hex, f"write_card(d12={d12})")
        resp = self._bridge.write_card(
            d12=d12, card_hex=card_hex, variant=variant, timeout=timeout
        )
        return resp

    def dll_call(self, fn_name: str, params: list[dict],
                 timeout: float = 10.0) -> dict:
        if self._sniffer.active:
            import json
            self._sniffer.record_call(
                fn_name,
                json.dumps(params, ensure_ascii=False),
                f"pending",
            )
        resp = self._bridge.dll_call(fn_name, params, timeout=timeout)
        if self._sniffer.active:
            self._sniffer.record_call(
                fn_name,
                json.dumps(params, ensure_ascii=False),
                str(resp.get("out") or resp.get("ret") or ""),
            )
        return resp

    def __getattr__(self, name: str):
        """代理所有未包装的属性到原始 bridge。"""
        return getattr(self._bridge, name)
