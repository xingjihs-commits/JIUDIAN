"""
bridgecore/orchestrator.py — 编排器 (Orchestrator)

将观察器/处理器/注入器三核串联成完整的录制→回放生命周期。

功能：
- record_session(): 上下文管理器，录制期间自动管理观察器和接收监控器
- replay(): 从录制会话重建回放，带写卡验证
- replay_last(): 快捷回放最近一次录制

用法：
    orch = BridgeCoreOrchestrator(bridge, rx_monitor)

    # 录制
    with orch.record_session(session_tag="guest_test"):
        bridge.guest_card(lock_no="0101", ...)

    # 回放
    summary = orch.replay_last()
    print(summary.success, summary.failed)
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config
from .fault_manager import FaultManager
from .injector import Injector, ReplaySummary
from .observer import Observer, RecordingSession, load_recording, list_sessions

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# 编排器
# ──────────────────────────────────────────────────────────────────

class BridgeCoreOrchestrator:
    """
    编排器 — 完整的录制→回放生命周期管理。

    线程安全：record_session 和 replay 使用锁串行化，防止并发录制/回放。
    """

    def __init__(
        self,
        bridge: Any,
        rx_monitor: Optional[Any] = None,
        *,
        panic_recovery: Optional[Any] = None,
        recording_dir: str | Path = "",
    ):
        self._bridge = bridge
        self._rx_monitor = rx_monitor
        self._panic = panic_recovery
        self._recording_dir = Path(recording_dir) if recording_dir else (
            Path(tempfile.gettempdir()) / "bridgecore_recordings"
        )
        self._recording_dir.mkdir(parents=True, exist_ok=True)

        # Observer / Injector 懒加载
        self._observer: Optional[Observer] = None
        self._injector: Optional[Injector] = None
        self._fault_manager: Optional[FaultManager] = None

        self._last_session_path: Optional[str] = None
        self._lock = threading.RLock()
        self._recording = False  # 是否正在录制（防止嵌套）

    # ── 属性 ────────────────────────────────────────────────

    @property
    def observer(self) -> Observer:
        if self._observer is None:
            self._observer = Observer()
        return self._observer

    @property
    def injector(self) -> Injector:
        if self._injector is None:
            fm = self._fault_manager or FaultManager()
            self._fault_manager = fm
            self._injector = Injector(self._bridge, fault_manager=fm, panic_recovery=self._panic)
            # 如果恐慌恢复存在，挂钩熔断回调
            if self._panic is not None:
                try:
                    fm.on_fuse(lambda kind: self._panic.notify_fuse(kind, triggered_by="fault_manager"))
                except Exception:
                    pass
        return self._injector

    @property
    def fault_manager(self) -> FaultManager:
        if self._fault_manager is None:
            self._fault_manager = FaultManager()
        return self._fault_manager

    @property
    def recording_dir(self) -> Path:
        return self._recording_dir

    @property
    def last_session_path(self) -> Optional[str]:
        return self._last_session_path

    # ── 录制上下文 ──────────────────────────────────────────

    @contextlib.contextmanager
    def record_session(
        self,
        *,
        hotel_id: str = "",
        brand: str = "",
        dll_version: str = "",
        dll_path: str = "",
        session_tag: str = "",
    ):
        """
        录制会话上下文管理器。

        进入时：暂停接收监控器 → 创建新观察器会话 → 挂接到桥接层
        退出时：解除观察器 → 保存 JSONL → 恢复接收监控器

        用法：
            with orch.record_session(session_tag="guest_card"):
                bridge.guest_card(lock_no="0101")
        """
        if self._recording:
            raise RuntimeError("已有录制会话进行中，不支持嵌套")

        with self._lock:
            self._recording = True

            # 暂停接收监控器（避免探测流量污染录制）
            rx_was_running = False
            if self._rx_monitor is not None and self._rx_monitor.running:
                rx_was_running = True
                self._rx_monitor.stop()
                logger.debug("[Orchestrator] RxMonitor 已暂停")

            # 创建新会话并挂接观察器
            observer = self.observer
            observer.new_session(
                hotel_id=hotel_id,
                brand=brand,
                dll_version=dll_version,
                dll_path=dll_path,
                session_tag=session_tag,
            )
            observer.attach(self._bridge, auto_session=False)

            session_ok = False
            try:
                yield  # 执行业务代码
                session_ok = True
            finally:
                # 解除观察器
                try:
                    observer.detach()
                except Exception:
                    pass

                # 保存录制文件
                session = observer.session
                if session is not None and session.record_count > 0:
                    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
                    tag_part = f"_{session_tag}" if session_tag else ""
                    filename = f"rec_{timestamp}{tag_part}_{session.session_id}.jsonl"
                    filepath = self._recording_dir / filename
                    try:
                        session.save(str(filepath))
                        self._last_session_path = str(filepath)
                        logger.info("[Orchestrator] 录制已保存: %s (%d 条)",
                                     filepath, session.record_count)
                    except Exception as e:
                        logger.error("[Orchestrator] 保存录制失败: %s", e)
                else:
                    logger.info("[Orchestrator] 录制会话为空，不保存")

                # 恢复接收监控器（如果之前是运行状态）
                if rx_was_running and self._rx_monitor is not None:
                    try:
                        self._rx_monitor.start()
                        logger.debug("[Orchestrator] RxMonitor 已恢复")
                    except Exception:
                        pass

                self._recording = False

    # ── 回放 ────────────────────────────────────────────────

    def replay(
        self,
        session_path: str | Path,
        *,
        progress_callback=None,
        batch_callback=None,
    ) -> ReplaySummary:
        """
        回放一个录制会话。

        Args:
            session_path: JSONL 录制文件路径
            progress_callback: 进度回调 (current, total, OpResult)
            batch_callback: 批回调 (batch_idx, total_batches)

        Returns:
            ReplaySummary 摘要
        """
        with self._lock:
            records = load_recording(session_path)
            if not records:
                logger.warning("[Orchestrator] 录制文件 %s 无有效记录", session_path)
                return ReplaySummary()

            logger.info("[Orchestrator] 开始回放 %s (%d 条操作)", session_path, len(records))

            # 暂停接收监控器（回放期间不需要健康探测）
            rx_was_running = False
            if self._rx_monitor is not None and self._rx_monitor.running:
                rx_was_running = True
                self._rx_monitor.stop()
                logger.debug("[Orchestrator] RxMonitor 已暂停（回放期间）")

            try:
                summary = self.injector.replay(
                    records,
                    progress_callback=progress_callback,
                    batch_callback=batch_callback,
                )
            finally:
                if rx_was_running and self._rx_monitor is not None:
                    try:
                        self._rx_monitor.start()
                    except Exception:
                        pass

            logger.info("[Orchestrator] 回放完成: %d/%d 成功, %d 失败, %d 跳过 (熔断=%s)",
                         summary.success, summary.total,
                         summary.failed, summary.skipped, summary.faulted)

            return summary

    def replay_last(self, **kwargs) -> ReplaySummary:
        """
        快捷回放最近一次录制的会话。

        Raises:
            RuntimeError: 没有可回放的录制
        """
        if self._last_session_path is None or not os.path.exists(self._last_session_path):
            raise RuntimeError("没有可回放的录制（请先执行 record_session）")
        return self.replay(self._last_session_path, **kwargs)

    # ── 录制列表 ────────────────────────────────────────────

    def list_recordings(self) -> list[dict[str, Any]]:
        """列出录制目录下的所有 JSONL 文件及其元信息。"""
        return list_sessions(str(self._recording_dir))

    # ── 重置 ────────────────────────────────────────────────

    def reset_fault(self) -> None:
        """手动复位熔断器。"""
        self.fault_manager._reset()
        logger.info("[Orchestrator] 熔断器已手动复位")
