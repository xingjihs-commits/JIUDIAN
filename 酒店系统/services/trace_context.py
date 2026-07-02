"""services/trace_context.py — 链路追踪上下文管理器

用法:
    from services.trace_context import trace

    with trace("checkin", room_id="101") as ctx:
        # 此块内所有 logger 日志自动带 trace_id
        logger.info("开始入住")
        guest_svc.checkin(...)
        logger.info("入住完成")

    # 日志输出: {"trace_id":"checkin-abc123","span":"checkin","room_id":"101",...}
"""
from __future__ import annotations

import contextvars
import logging
import time
import uuid

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_trace_span: contextvars.ContextVar[str] = contextvars.ContextVar("trace_span", default="")
_trace_extra: contextvars.ContextVar[dict] = contextvars.ContextVar("trace_extra", default={})

logger = logging.getLogger(__name__)


def current_trace_id() -> str:
    return _trace_id.get() or "-"


def current_span() -> str:
    return _trace_span.get() or "-"


class TraceSpan:
    """链路追踪跨度，用作上下文管理器。"""

    def __init__(self, span: str, **extra):
        self.span = span
        self.extra = extra
        self._start = 0.0
        self._token_tid = None
        self._token_span = None
        self._token_extra = None

    def __enter__(self) -> "TraceSpan":
        self._start = time.time()
        tid = f"{self.span}-{uuid.uuid4().hex[:8]}"
        self._token_tid = _trace_id.set(tid)
        self._token_span = _trace_span.set(self.span)
        self._token_extra = _trace_extra.set(self.extra)
        logger.debug("trace start: %s", tid)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = (time.time() - self._start) * 1000
        tid = _trace_id.get()
        if exc_type:
            logger.error("trace error: %s (%.0fms) %s", tid, elapsed_ms, exc_val)
        else:
            logger.info("trace done: %s (%.0fms)", tid, elapsed_ms)
        _trace_id.reset(self._token_tid)
        _trace_span.reset(self._token_span)
        _trace_extra.reset(self._token_extra)


def trace(span: str, **extra) -> TraceSpan:
    """创建链路追踪跨度。"""
    return TraceSpan(span, **extra)
