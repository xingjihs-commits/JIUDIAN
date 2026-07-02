"""
bridgecore/injector.py — 注入核

回放录制的远程调用操作序列，带完整的熔断保护、速率控制、进度回调。

回放模式：
- 同步回放：阻塞直到完成
- 逐批回放：按配置的批大小分批执行
- 熔断切入透传

每个操作结果分类：
- success: 调用成功
- retry_success: 重试后成功
- failed: 调用失败
- skipped: 熔断后跳过
- faulted: 触发熔断
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import config
from .fault_manager import FaultManager, FaultTriggered

logger = logging.getLogger(__name__)

# 写卡方法名列表（需要回放后验证数据包）
_WRITE_METHODS = frozenset({
    "guest_card", "guest_card_v2", "compose_guest_card",
    "master_card", "building_card", "floor_card",
    "emergency_card", "group_card", "auth_card", "ini_card",
    "limit_card", "card_erase", "write_card",
})


# ──────────────────────────────────────────────────────────────────
# 结果分类
# ──────────────────────────────────────────────────────────────────

class ReplayStatus:
    SUCCESS = "success"
    RETRY_SUCCESS = "retry_success"
    FAILED = "failed"
    SKIPPED = "skipped"
    FAULTED = "faulted"


@dataclass
class OpResult:
    """一次操作的回放结果。"""
    seq: int
    fn_name: str
    status: str
    duration: float = 0.0
    error: str = ""
    kind: str = ""
    ret: Optional[dict] = None
    retry_count: int = 0
    args_snapshot: dict = field(default_factory=dict)


@dataclass
class ReplaySummary:
    """回放整体摘要。"""
    total: int = 0
    success: int = 0
    retry_success: int = 0
    failed: int = 0
    skipped: int = 0
    faulted: bool = False
    duration: float = 0.0
    operations: list[OpResult] = field(default_factory=list)
    fault_report: Optional[dict] = None


# ──────────────────────────────────────────────────────────────────
# 注入核
# ──────────────────────────────────────────────────────────────────

class Injector:
    """
    注入核 — 回放录制的操作序列。

    使用方式：
        injector = Injector(bridge)
        summary = injector.replay(records)
        print(summary.success, summary.failed)
    """

    def __init__(
        self,
        bridge: Any,
        fault_manager: Optional[FaultManager] = None,
        panic_recovery: Optional[Any] = None,
    ):
        self._bridge = bridge
        self._fm = fault_manager or FaultManager()
        self._rx_monitor: Optional[Any] = None
        self._panic = panic_recovery  # PanicRecovery 可选实例

        cfg = config.get_settings().replay
        self._batch_size = cfg.batch_size
        self._inter_op_delay = cfg.inter_op_delay
        self._readback_enabled = cfg.readback

    # ── 属性 ────────────────────────────────────────────────

    @property
    def fault_manager(self) -> FaultManager:
        return self._fm

    def set_rx_monitor(self, monitor: Any) -> None:
        self._rx_monitor = monitor

    # ── 核心回放 ────────────────────────────────────────────

    def replay(
        self,
        records: list[dict[str, Any]],
        *,
        progress_callback: Optional[Callable[[int, int, OpResult], None]] = None,
        batch_callback: Optional[Callable[[int, int], None]] = None,
    ) -> ReplaySummary:
        """
        回放录制记录。

        Args:
            records: 录制记录列表（从 JSONL 加载）
            progress_callback: 每次操作完成后的回调（当前序号、总数、结果）
            batch_callback: 每批完成后的回调（当前批号、总批数）

        Returns:
            ReplaySummary 摘要
        """
        summary = ReplaySummary()
        summary.total = len(records)

        if not records:
            logger.info("[Injector] 无记录可回放")
            return summary

        # pause keepalive
        self._pause_keepalive()

        t_start = time.time()

        try:
            # 按批次回放
            if self._batch_size > 0:
                batches = self._split_batches(records, self._batch_size)
                for batch_idx, batch in enumerate(batches):
                    if batch_callback:
                        batch_callback(batch_idx + 1, len(batches))
                    self._replay_batch(batch, summary, progress_callback)
                    if self._fm.is_faulted():
                        break
            else:
                self._replay_batch(records, summary, progress_callback)
        finally:
            summary.duration = time.time() - t_start
            self._resume_keepalive()

            # 提取故障报告
            if self._fm.is_faulted():
                summary.faulted = True
                summary.fault_report = self._fm.get_diagnostic_report()

        return summary

    # ── 单批回放 ────────────────────────────────────────────

    def _replay_batch(
        self,
        batch: list[dict[str, Any]],
        summary: ReplaySummary,
        progress_callback: Optional[Callable] = None,
    ) -> None:
        """回放一批操作。"""
        for idx, rec in enumerate(batch):
            fn_name = rec.get("fn_name", "unknown")
            args_in = rec.get("args_in", {})

            # 检查熔断
            if self._fm.is_faulted():
                op = OpResult(
                    seq=summary.success + summary.failed + summary.skipped + 1,
                    fn_name=fn_name,
                    status=ReplayStatus.SKIPPED,
                    args_snapshot=args_in,
                )
                summary.skipped += 1
                summary.operations.append(op)
                if progress_callback:
                    progress_callback(summary.success + summary.failed + summary.skipped,
                                      summary.total, op)
                continue

            result, error, kind = self._execute_op(fn_name, args_in, current_record=rec)

            # 记录结果
            seq = summary.success + summary.failed + summary.skipped + 1
            if result is not None:
                # 检查是否需要重试
                op = self._classify_op(seq, fn_name, result, error, kind, args_in)
            else:
                op = OpResult(
                    seq=seq,
                    fn_name=fn_name,
                    status=ReplayStatus.FAULTED if self._fm.is_faulted() else ReplayStatus.FAILED,
                    error=error,
                    kind=kind,
                    args_snapshot=args_in,
                )

            # 更新统计
            if op.status == ReplayStatus.SUCCESS:
                summary.success += 1
            elif op.status == ReplayStatus.RETRY_SUCCESS:
                summary.retry_success += 1
            elif op.status in (ReplayStatus.SKIPPED, ReplayStatus.FAULTED):
                summary.skipped += 1
            else:
                summary.failed += 1

            summary.operations.append(op)

            if progress_callback:
                progress_callback(summary.success + summary.failed + summary.skipped,
                                  summary.total, op)

            # 操作间延迟
            if self._inter_op_delay > 0 and idx < len(batch) - 1:
                time.sleep(self._inter_op_delay)

            # 熔断立即停止本批
            if self._fm.is_faulted():
                break

    # ── 单次执行 ────────────────────────────────────────────

    def _execute_op(
        self,
        fn_name: str,
        args_in: dict,
        *,
        current_record: Optional[dict] = None,
    ) -> tuple[Optional[dict], str, str]:
        """执行一次远程调用。返回 (result, error_msg, kind)。

        Args:
            fn_name: 方法名
            args_in: 参数字典
            current_record: 当前操作的完整录制记录（用于写卡验证）
        """
        bridge = self._bridge
        fn = getattr(bridge, fn_name, None)
        if fn is None or not callable(fn):
            return None, f"方法 {fn_name} 不存在", "protocol"

        t_start = time.monotonic()
        try:
            # 构造位置参数和关键字参数（不强制添加 timeout）
            pos = args_in.pop("_positional", [])
            fn_kwargs = dict(args_in)
            fn_kwargs.pop("timeout", None)  # 不强制传 timeout
            result = fn(*pos, **fn_kwargs)
            elapsed = time.monotonic() - t_start

            _log_op_result(fn_name, True, elapsed)

            # 写卡操作：回放后自动读卡验证数据包一致
            if fn_name in _WRITE_METHODS and result.get("ok") and self._readback_enabled:
                payload_verified, readback_hex = self._verify_write(fn_name, args_in, current_record)
                if isinstance(result, dict):
                    result["payload_verified"] = payload_verified
                    if readback_hex:
                        result["readback_hex"] = readback_hex

            # 传递给 FaultManager 记录
            is_faulted = self._fm.record_attempt(fn_name, result)
            return result, "", ""
        except Exception as e:
            elapsed = time.monotonic() - t_start
            error_msg = f"{type(e).__name__}: {e}"
            kind = FaultManager._classify_error({"error": error_msg})

            _log_op_result(fn_name, False, elapsed, error_msg)

            # 记录到 FaultManager
            self._fm.record_attempt(fn_name, {"ok": False, "error": error_msg})

            # 通知 PanicRecovery（如果是硬件/超时错误）
            if self._panic is not None and kind in ("hardware", "timeout"):
                try:
                    self._panic.notify_failure(kind, triggered_by=fn_name)
                except Exception:
                    pass

            return None, error_msg, kind

    def _verify_write(
        self,
        fn_name: str,
        args_in: dict,
        current_record: Optional[dict],
    ) -> tuple[bool, str]:
        """回放写卡后，验证数据包与录制一致。

        Returns:
            (payload_verified, readback_hex)
        """
        if not current_record:
            return False, ""
        try:
            d12 = args_in.get("d12", 1)
            read_fn = getattr(self._bridge, "read_card", None)
            if not callable(read_fn):
                return False, ""
            rr = read_fn(d12=d12)
            if not isinstance(rr, dict) or not rr.get("ok"):
                return False, ""

            out = rr.get("out") or {}
            current_hex = str(out.get("payload") or out.get("hex") or "")

            # 与录制时的数据包对比
            expected_hex = str(current_record.get("payload_hex") or "")

            if expected_hex and current_hex:
                matched = current_hex == expected_hex
                if not matched:
                    logger.warning(
                        "[Injector] %s payload 不匹配: expected=%s, got=%s",
                        fn_name, expected_hex, current_hex,
                    )
                else:
                    logger.debug("[Injector] %s payload 验证通过", fn_name)
                return matched, current_hex

            return bool(current_hex), current_hex
        except Exception:
            return False, ""

    # ── 结果分类 ────────────────────────────────────────────

    def _classify_op(
        self,
        seq: int,
        fn_name: str,
        result: dict,
        error: str,
        kind: str,
        args_snapshot: dict,
    ) -> OpResult:
        """分类操作结果。"""
        is_ok = FaultManager._is_ok(result)
        status = ReplayStatus.SUCCESS if is_ok else ReplayStatus.FAILED
        if self._fm.is_faulted():
            status = ReplayStatus.FAULTED

        op = OpResult(
            seq=seq,
            fn_name=fn_name,
            status=status,
            error=error,
            kind=kind,
            ret=result,
            args_snapshot=args_snapshot,
        )
        return op

    # ── Keepalive 控制 ──────────────────────────────────────

    def _pause_keepalive(self) -> None:
        """采集器独立环境无 PMS 级 keepalive，此处为预留接口。"""
        pass

    def _resume_keepalive(self) -> None:
        """采集器独立环境无 PMS 级 keepalive，此处为预留接口。"""
        pass

    # ── 辅助 ────────────────────────────────────────────────

    @staticmethod
    def _split_batches(records: list, batch_size: int) -> list[list]:
        return [records[i:i + batch_size] for i in range(0, len(records), batch_size)]


# ──────────────────────────────────────────────────────────────────
# 日志辅助
# ──────────────────────────────────────────────────────────────────

def _log_op_result(fn_name: str, ok: bool, elapsed: float,
                   error: str = "") -> None:
    if ok:
        logger.debug("[Injector] %s 成功 (%.1fms)", fn_name, elapsed * 1000)
    else:
        logger.warning("[Injector] %s 失败 (%.1fms): %s",
                        fn_name, elapsed * 1000, error)
