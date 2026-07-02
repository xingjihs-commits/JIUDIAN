# SYNC_WITH: 采集器/bridgecore/panic_recovery.py  2026-06-22 同步
# 采集器版新增：_COLLECTOR_KNOWN_READERS VID/PID 表（panic_recovery.recover()
#   在无法从桥接层获取 VID/PID 时降级使用 proUSB 320F:1000）。
"""
bridgecore/panic_recovery.py — 恐慌恢复引擎

双重冗余恢复机制，当注入器回放或运行时发生连续通信失败时自动执行：

Level 1 — 软复位:
  重启桥接子进程 → 重新加载 DLL → 重新初始化 USB
  相当于 libusb_reset_device 的软件等效
  解决 90% 的僵死问题

Level 2 — 强制断电复位:
  通过 USB 集线器端口禁用/启用或 USB 继电器模块，
  物理断电重连发卡器。由抽象电源控制器接口实现。

反馈闭环:
  恢复后立即发送初始化帧，确保设备回到初始状态。
  如果恢复成功，自动标记熔断管理器复位。
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import config
from .fault_manager import FaultManager, FaultKind

logger = logging.getLogger(__name__)

# 常见 proUSB 发卡器 VID/PID（采集器内置，不依赖 PMS）
_COLLECTOR_KNOWN_READERS = (
    {"vid": "320F", "pid": "1000", "name": "proUSB encoder"},
    {"vid": "0C27", "pid": "3BFA", "name": "RF encoder"},
)


# ──────────────────────────────────────────────────────────────────
# 恢复记录
# ──────────────────────────────────────────────────────────────────

@dataclass
class RecoveryRecord:
    """一次恢复操作的记录。"""
    level: str                # "soft_reset" / "power_cycle"
    success: bool             # 是否成功
    duration: float = 0.0     # 耗时（秒）
    error: str = ""           # 错误描述
    triggered_by: str = ""    # 触发原因（哪个方法/什么错误）
    timestamp: float = 0.0    # 时间戳

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class RecoverySummary:
    """恢复过程的完整摘要。"""
    level_1_attempted: bool = False
    level_1_success: bool = False
    level_1_record: Optional[RecoveryRecord] = None
    level_2_attempted: bool = False
    level_2_success: bool = False
    level_2_record: Optional[RecoveryRecord] = None
    recovered: bool = False           # 最终是否恢复
    initialized: bool = False         # 是否执行了初始化
    duration: float = 0.0             # 总耗时
    error: str = ""                   # 最终错误


# ──────────────────────────────────────────────────────────────────
# 强制断电控制器抽象
# ──────────────────────────────────────────────────────────────────

class PowerController(ABC):
    """
    强制断电控制器抽象接口。

    默认实现 WinUsbPowerController 通过 Windows 设备管理API 禁用/启用 USB 设备。
    可替换为 USB 继电器模块（串口指令）等硬件方案。
    """

    @abstractmethod
    def cycle_port(self, vid: str = "", pid: str = "") -> bool:
        """对指定 VID/PID 的 USB 设备执行端口断电重连。

        Args:
            vid: USB 厂商识别码（十六进制字符串，如 "320F"）
            pid: USB 产品识别码（十六进制字符串，如 "1001"）

        Returns:
            True 表示操作成功
        """

    @abstractmethod
    def name(self) -> str:
        """返回控制器名称用于日志。"""


class WinUsbPowerController(PowerController):
    """
    通过 Windows 设备管理API 禁用/启用 USB 设备实现断电重连。

    原理：
    1. SetupDiGetClassDevs 枚举 USB 设备
    2. 按 VID/PID 匹配到目标设备
    3. SetupDiCallClassInstaller 先禁用再启用（DIF_PROPERTYCHANGE）
       → SP_PROPCHANGE_PARAMS StateChange = DICS_DISABLE / DICS_ENABLE

    等效于设备管理器中的"禁用设备 → 启用设备"操作。
    适用于标准 USB HUB / USB 根集线器。
    """

    def cycle_port(self, vid: str = "", pid: str = "") -> bool:
        try:
            import win32api
            import win32con
            import pywinusb.hid as hid
        except ImportError:
            logger.warning("[PowerCycle] 需要 pywin32 和 pywinusb 支持")
            return False

        try:
            # Step 1: 找到设备实例 ID
            target_vid = int(vid, 16) if vid else None
            target_pid = int(pid, 16) if pid else None

            filter_args = {}
            if target_vid:
                filter_args["vendor_id"] = target_vid
            if target_pid:
                filter_args["product_id"] = target_pid

            devices = hid.HidDeviceFilter(**filter_args).get_devices()
            if not devices:
                logger.warning("[PowerCycle] 未找到匹配的 HID 设备 %s:%s", vid, pid)
                return False

            device = devices[0]
            logger.info("[PowerCycle] 找到目标设备: %s", device.product_name or device)

            # Step 2: 尝试通过 Windows 设备管理 API 禁用再启用
            import win32file
            from pywinusb import winusb

            # 关闭设备（如果已打开）
            try:
                device.close()
            except Exception:
                pass

            # 通过 devcon.exe 或 CfgMgr32 API 禁用/启用设备
            # 这里先尝试简单方法：用 devcon.exe（如果存在）
            import subprocess
            import os

            devcon_paths = [
                r"C:\Program Files (x86)\Windows Kits\10\Tools\x64\devcon.exe",
                r"C:\Program Files\Windows Kits\10\Tools\x64\devcon.exe",
                os.path.join(os.environ.get("SYSTEMROOT", "C:\\Windows"), "System32", "devcon.exe"),
            ]

            # 获取硬件 ID
            hw_id = ""
            try:
                hw_id = str(device)
                # 提取 VID/PID 格式的硬件 ID
                import re
                match = re.search(r"VID_([0-9A-F]{4}).*PID_([0-9A-F]{4})", hw_id, re.I)
                if match:
                    hw_id = f"USB\\VID_{match.group(1).upper()}&PID_{match.group(2).upper()}"
            except Exception:
                hw_id = f"USB\\VID_{vid}&PID_{pid}" if vid and pid else ""

            if not hw_id:
                return False

            # 先禁用再启用
            for action in ("disable", "enable"):
                ok = self._run_devcon(devcon_paths, action, hw_id)
                if not ok:
                    logger.warning("[PowerCycle] devcon %s 失败", action)
                    return False
                time.sleep(0.5)

            logger.info("[PowerCycle] %s 端口电源已重置", hw_id)
            return True

        except Exception as e:
            logger.error("[PowerCycle] 断电重连异常: %s", e)
            return False

    def _run_devcon(self, candidates: list[str], action: str,
                    hw_id: str) -> bool:
        """尝试用 devcon.exe 执行操作。"""
        import subprocess
        for dc in candidates:
            if not dc or not __import__("os").path.isfile(dc):
                continue
            try:
                result = subprocess.run(
                    [dc, action, hw_id],
                    capture_output=True, text=True, timeout=10,
                    creationflags=__import__("subprocess").CREATE_NO_WINDOW
                    if hasattr(__import__("subprocess"), "CREATE_NO_WINDOW") else 0,
                )
                if result.returncode == 0:
                    return True
                logger.debug("[PowerCycle] devcon %s %s: ret=%d, stderr=%s",
                             dc, action, result.returncode, result.stderr[:100])
            except Exception as e:
                logger.debug("[PowerCycle] devcon %s 失败: %s", dc, e)
        return False

    def name(self) -> str:
        return "WinUSB_PowerCycle"


class SerialRelayPowerController(PowerController):
    """
    通过串口 USB 继电器模块强制断电。

    典型模块：LCUS-1 / USB Relay 等，通过串口指令控制继电器开合。
    接线：继电器常闭端接发卡器电源正极。

    配置参数在配置文件的恐慌恢复配置中指定:
      panic.relay_port = "COM3"
      panic.relay_on_cmd = b"\\xFE\\x01\\x01"    # 关
      panic.relay_off_cmd = b"\\xFE\\x01\\x00"   # 开
      panic.power_off_duration = 1.5             # 断电时长（秒）
    """

    def __init__(self):
        self._cfg = config.get_settings().panic
        self._serial: Any = None

    def cycle_port(self, vid: str = "", pid: str = "") -> bool:
        try:
            import serial
        except ImportError:
            logger.warning("[SerialRelay] pyserial 未安装")
            return False

        port = self._cfg.relay_port or ""
        if not port:
            logger.warning("[SerialRelay] 未配置 relay_port")
            return False

        try:
            self._serial = serial.Serial(port, 9600, timeout=2)
            # 断电
            off_cmd = self._cfg.relay_off_cmd
            if off_cmd:
                self._serial.write(off_cmd.encode() if isinstance(off_cmd, str) else off_cmd)
            self._serial.flush()
            time.sleep(self._cfg.power_off_duration or 1.5)
            # 上电
            on_cmd = self._cfg.relay_on_cmd
            if on_cmd:
                self._serial.write(on_cmd.encode() if isinstance(on_cmd, str) else on_cmd)
            self._serial.flush()
            self._serial.close()
            logger.info("[SerialRelay] 串口继电器断电重连完成 (%s)", port)
            return True
        except Exception as e:
            logger.error("[SerialRelay] 继电器控制失败: %s", e)
            return False

    def name(self) -> str:
        return f"SerialRelay({self._cfg.relay_port})"


# ──────────────────────────────────────────────────────────────────
# 恐慌恢复引擎
# ──────────────────────────────────────────────────────────────────

class PanicRecovery:
    """
    恐慌恢复引擎 — 双重冗余 + 反馈闭环。

    使用方式（自动模式）：
        panic = PanicRecovery(bridge)
        panic.register_with(fault_manager)   # 挂钩熔断管理器熔断触发
        panic.register_with(injector)        # 挂钩注入器的执行失败

    手动模式：
        summary = panic.execute()
        if summary.recovered:
            print("恢复成功!")

    阈值：
    - 连续 3 次硬件/超时错误 → 触发 Level 1（软复位）
    - Level 1 失败或连续 2 次 → 触发 Level 2（强制断电）
    - Level 2 失败 → 报告无法恢复，等待人工
    """

    def __init__(
        self,
        bridge: Any,
        power_controller: Optional[PowerController] = None,
        *,
        soft_reset_threshold: int = 3,
        max_soft_resets: int = 2,
        max_power_cycles: int = 1,
        recovery_pause: float = 1.0,
    ):
        """
        Args:
            bridge: RflBridge 或其兼容实例
            power_controller: 强制断电控制器（默认自动选择）
            soft_reset_threshold: 连续失败多少次触发 Level 1
            max_soft_resets: Level 1 最大尝试次数
            max_power_cycles: Level 2 最大尝试次数
            recovery_pause: 恢复操作间的暂停时间
        """
        self._bridge = bridge

        # 恢复阈值
        self._soft_reset_threshold = soft_reset_threshold
        self._max_soft_resets = max_soft_resets
        self._max_power_cycles = max_power_cycles
        self._recovery_pause = recovery_pause

        # 自动选择断电控制器
        self._power_controller = power_controller or self._auto_select_controller()

        # 连续失败计数
        self._consecutive_hw_fails: int = 0

        # 恢复历史
        self._soft_reset_count: int = 0
        self._power_cycle_count: int = 0
        self._history: list[RecoveryRecord] = []
        self._last_recovery_time: float = 0.0

        # 是否正在恢复中（防止重入）
        self._recovering = False

        # 线程锁
        self._lock = threading.RLock()

        # 外部回调（恢复成功/失败通知）
        self._on_recovery_success: Optional[Callable[[str], None]] = None
        self._on_recovery_failure: Optional[Callable[[str], None]] = None

    # ── 配置 ────────────────────────────────────────────────

    def set_on_recovery_success(self, cb: Callable[[str], None]) -> None:
        """注册恢复成功回调。参数: level_name"""
        self._on_recovery_success = cb

    def set_on_recovery_failure(self, cb: Callable[[str], None]) -> None:
        """注册恢复失败回调。参数: error_msg"""
        self._on_recovery_failure = cb

    @staticmethod
    def _auto_select_controller() -> PowerController:
        """自动选择可用的断电控制器。"""
        # 优先用 WinUSB 方式
        try:
            import pywinusb.hid
            return WinUsbPowerController()
        except ImportError:
            pass
        # 降级为串口继电器
        try:
            import serial
            return SerialRelayPowerController()
        except ImportError:
            pass
        # 无可用控制器
        class NoopController(PowerController):
            def cycle_port(self, vid="", pid=""):
                return False
            def name(self):
                return "none"
        return NoopController()

    # ── 通知接口（外部组件通过此接口通知失败/熔断） ─────

    def notify_failure(self, kind: str, *, triggered_by: str = "") -> bool:
        """
        外部组件通知一次硬件/超时失败。达到阈值时自动触发恢复。

        Args:
            kind: 熔断原因中的硬件或超时
            triggered_by: 触发来源描述（如方法名）

        Returns:
            True 表示已触发恢复流程
        """
        return self._increment_and_maybe_recover(kind, triggered_by=triggered_by)

    def notify_fuse(self, kind: str, *, triggered_by: str = "") -> bool:
        """
        外部组件通知熔断已触发。**直接触发恢复**（不等待阈值累积）。

        Args:
            kind: FaultKind
            triggered_by: 触发来源描述

        Returns:
            True 表示已触发恢复流程
        """
        # 不在此设 _recovering — _do_recovery 内部有重入保护
        t = threading.Thread(
            target=self._do_recovery,
            args=(kind, triggered_by or "fuse"),
            daemon=True,
            name="panic-recovery-fuse",
        )
        t.start()
        return True

    # ── 失败计数与触发 ─────────────────────────────────────

    def _increment_and_maybe_recover(
        self, kind: str, *, triggered_by: str = ""
    ) -> bool:
        """
        累加失败计数，达到阈值时触发恢复。

        Returns:
            True 表示已触发恢复
        """
        with self._lock:
            if kind not in (FaultKind.HARDWARE, FaultKind.TIMEOUT):
                return False

            self._consecutive_hw_fails += 1

            total_fails = self._consecutive_hw_fails
            if total_fails >= self._soft_reset_threshold:
                self._reset_counters()
                # 在独立线程执行恢复（不阻塞调用者）
                t = threading.Thread(
                    target=self._do_recovery,
                    args=(kind, triggered_by),
                    daemon=True,
                    name="panic-recovery",
                )
                t.start()
                return True
            return False

    def _reset_counters(self) -> None:
        self._consecutive_hw_fails = 0

    # ── 恢复执行 ───────────────────────────────────────────

    def execute(self, triggered_by: str = "") -> RecoverySummary:
        """
        手动执行完整恢复流程（Level 1 → Level 2 → Initialize）。

        Args:
            triggered_by: 触发原因描述

        Returns:
            RecoverySummary 完整摘要
        """
        return self._do_recovery("manual", triggered_by)

    def _do_recovery(self, kind: str, triggered_by: str = "") -> RecoverySummary:
        """执行恢复流程（线程安全，防重入）。"""
        with self._lock:
            if self._recovering:
                logger.warning("[PanicRecovery] 恢复已在执行中，跳过")
                return RecoverySummary(error="恢复已在执行中")
            self._recovering = True

        summary = RecoverySummary()
        t_start = time.time()

        try:
            logger.critical(
                "[PanicRecovery] 触发恢复! kind=%s, triggered_by=%s, "
                "soft_resets=%d/%d, power_cycles=%d/%d",
                kind, triggered_by or "(none)",
                self._soft_reset_count, self._max_soft_resets,
                self._power_cycle_count, self._max_power_cycles,
            )

            # ── Level 1: 软复位 ─────────────────────────────────
            if self._soft_reset_count < self._max_soft_resets:
                summary.level_1_attempted = True
                record = self._try_soft_reset(triggered_by)
                summary.level_1_record = record
                summary.level_1_success = record.success
                if record.success:
                    self._soft_reset_count = 0  # 成功后重置计数
                else:
                    self._soft_reset_count += 1

                # 暂停后发初始化
                if record.success:
                    time.sleep(self._recovery_pause)
                    summary.initialized = self._send_initialize()
            else:
                logger.info("[PanicRecovery] Level 1 已达上限 %d, 跳过",
                             self._max_soft_resets)

            # ── 如果 Level 1 成功，不再执行 Level 2 ─────────────
            if summary.level_1_success:
                summary.recovered = True
                self._last_recovery_time = time.time()
                summary.duration = time.time() - t_start
                self._history.append(summary.level_1_record)
                self._fire_success("level_1")
                return summary

            # ── Level 2: 强制断电复位 ───────────────────────────
            if self._power_cycle_count < self._max_power_cycles:
                summary.level_2_attempted = True
                record = self._try_power_cycle(triggered_by)
                summary.level_2_record = record
                summary.level_2_success = record.success
                if record.success:
                    self._power_cycle_count = 0  # 成功后重置
                else:
                    self._power_cycle_count += 1

                if record.success:
                    time.sleep(self._recovery_pause + 0.5)
                    summary.initialized = self._send_initialize()
            else:
                logger.info("[PanicRecovery] Level 2 已达上限 %d, 跳过",
                             self._max_power_cycles)

            # ── 最终判断 ────────────────────────────────────────
            if summary.level_2_success:
                summary.recovered = True
                self._last_recovery_time = time.time()
                self._history.append(summary.level_2_record)
                self._fire_success("level_2")
            else:
                summary.error = "所有恢复手段均失败"
                logger.critical("[PanicRecovery] %s", summary.error)
                self._fire_failure(summary.error)

        except Exception as e:
            summary.error = f"恢复异常: {e}"
            logger.error("[PanicRecovery] %s", summary.error, exc_info=True)
            self._fire_failure(summary.error)
        finally:
            summary.duration = time.time() - t_start
            self._recovering = False

        return summary

    # ── Level 1: 软复位 ───────────────────────────────────

    def _try_soft_reset(self, triggered_by: str = "") -> RecoveryRecord:
        """
        软复位：重启桥接子进程 → 重新加载 DLL → 重新初始化。

        Returns:
            RecoveryRecord
        """
        logger.critical("[PanicRecovery] Level 1: 执行软复位...")
        t_start = time.time()

        try:
            bridge = self._bridge

            # Step 1: 强制停止桥接（含关闭 USB + 释放锁）
            bridge.stop()
            time.sleep(0.5)

            # Step 2: 重新启动
            bridge.start(force_restart=True)
            time.sleep(0.3)

            # Step 3: 重新加载 DLL（用缓存的上次成功参数）
            if not bridge.dll_loaded:
                if bridge._last_dll_path:
                    resp = bridge.load_dll(
                        bridge._last_dll_path,
                        bridge._last_extra_paths or [],
                    )
                    if not (resp.get("ok") and resp.get("loaded")):
                        raise RuntimeError(
                            f"load_dll 失败: {resp.get('error', 'unknown')}"
                        )
                else:
                    logger.warning("[PanicRecovery] 无缓存的 DLL 路径，跳过加载")

            # Step 4: 重新初始化
            if bridge._last_init_d12 is not None:
                resp = bridge.initialize(bridge._last_init_d12)
                ret = -1
                try:
                    ret = int(resp.get("ret", -1))
                except Exception:
                    pass
                if not resp.get("ok") or ret != 0:
                    raise RuntimeError(
                        f"initializeUSB 失败: ret={ret}, resp={resp}"
                    )

            elapsed = time.time() - t_start
            logger.critical("[PanicRecovery] Level 1 成功 (%.1f秒)", elapsed)

            return RecoveryRecord(
                level="soft_reset",
                success=True,
                duration=elapsed,
                triggered_by=triggered_by,
            )

        except Exception as e:
            elapsed = time.time() - t_start
            error_msg = f"{type(e).__name__}: {e}"
            logger.error("[PanicRecovery] Level 1 失败 (%.1f秒): %s", elapsed, error_msg)

            return RecoveryRecord(
                level="soft_reset",
                success=False,
                duration=elapsed,
                error=error_msg,
                triggered_by=triggered_by,
            )

    # ── Level 2: 强制断电复位 ─────────────────────────────

    def _try_power_cycle(self, triggered_by: str = "") -> RecoveryRecord:
        """
        强制断电复位：先软停止，再执行 USB 端口断电重连。

        Returns:
            RecoveryRecord
        """
        logger.critical("[PanicRecovery] Level 2: 执行强制断电复位...")
        t_start = time.time()

        try:
            bridge = self._bridge

            # Step 1: 先尝试软停止桥接（可能已经没反应了）
            try:
                bridge.stop()
            except Exception:
                pass
            time.sleep(0.3)

            # Step 2: 执行 USB 端口断电重连
            vid = ""
            pid = ""
            try:
                from lock_adapters.hardware_support import KNOWN_READERS
                if KNOWN_READERS:
                    first = list(KNOWN_READERS.values())[0]
                    vid = first.get("vid", "")
                    pid = first.get("pid", "")
            except Exception:
                if _COLLECTOR_KNOWN_READERS:
                    first = _COLLECTOR_KNOWN_READERS[0]
                    vid = first.get("vid", "")
                    pid = first.get("pid", "")

            if not vid or not pid:
                vid = "320F"
                pid = "1000"

            ok = self._power_controller.cycle_port(vid=vid, pid=pid)

            if not ok:
                raise RuntimeError(f"电源控制失败: {self._power_controller.name()}")

            # Step 3: 等待设备枚举
            time.sleep(2.0)

            # Step 4: 重新启动桥接层
            try:
                bridge.start(force_restart=True)
                time.sleep(0.5)
            except Exception as e:
                # 可能需要重新发现 32 位 Python
                logger.warning("[PanicRecovery] bridge 启动失败: %s", e)
                # 重试一次
                time.sleep(1.0)
                bridge.start(force_restart=True)

            # Step 5: 重新加载 DLL
            if bridge._last_dll_path:
                resp = bridge.load_dll(
                    bridge._last_dll_path,
                    bridge._last_extra_paths or [],
                )
                if not (resp.get("ok") and resp.get("loaded")):
                    raise RuntimeError(
                        f"load_dll 失败: {resp.get('error', 'unknown')}"
                    )

            # Step 6: 重新初始化
            if bridge._last_init_d12 is not None:
                resp = bridge.initialize(bridge._last_init_d12)
                ret = -1
                try:
                    ret = int(resp.get("ret", -1))
                except Exception:
                    pass
                if not resp.get("ok") or ret != 0:
                    raise RuntimeError(
                        f"initializeUSB 失败: ret={ret}, resp={resp}"
                    )

            elapsed = time.time() - t_start
            logger.critical("[PanicRecovery] Level 2 成功 (%.1f秒)", elapsed)

            return RecoveryRecord(
                level="power_cycle",
                success=True,
                duration=elapsed,
                triggered_by=triggered_by,
            )

        except Exception as e:
            elapsed = time.time() - t_start
            error_msg = f"{type(e).__name__}: {e}"
            logger.error("[PanicRecovery] Level 2 失败 (%.1f秒): %s",
                         elapsed, error_msg)

            return RecoveryRecord(
                level="power_cycle",
                success=False,
                duration=elapsed,
                error=error_msg,
                triggered_by=triggered_by,
            )

    # ── 反馈闭环: 初始化 ──────────────────────────────

    def _send_initialize(self) -> bool:
        """
        恢复后发送初始化帧，确保设备回到初始状态。

        关键：不依赖缓存参数，尝试多种参数值（d12=1, 0, 2）直到成功。
        """
        logger.info("[PanicRecovery] 发送初始化帧...")
        try:
            bridge = self._bridge
            for d12_val in (1, 0, 2):
                try:
                    resp = bridge.initialize(d12=d12_val, timeout=3.0)
                    if resp.get("ok") and int(resp.get("ret", -1)) == 0:
                        logger.info("[PanicRecovery] 初始化成功 (d12=%d)", d12_val)
                        return True
                except Exception:
                    continue
            logger.warning("[PanicRecovery] 所有初始化尝试均失败")
            return False
        except Exception as e:
            logger.error("[PanicRecovery] 初始化异常: %s", e)
            return False

    # ── 状态查询 ──────────────────────────────────────────

    def reset_stats(self) -> None:
        """重置统计计数（不清除历史记录）。"""
        with self._lock:
            self._soft_reset_count = 0
            self._power_cycle_count = 0
            self._consecutive_hw_fails = 0
            logger.info("[PanicRecovery] 统计已重置")

    @property
    def history(self) -> list[RecoveryRecord]:
        with self._lock:
            return list(self._history)

    @property
    def is_recovering(self) -> bool:
        return self._recovering

    @property
    def last_recovery_time(self) -> float:
        return self._last_recovery_time

    def get_diagnostic_report(self) -> dict[str, Any]:
        """生成诊断报告。"""
        with self._lock:
            return {
                "status": "recovering" if self._recovering else "idle",
                "soft_reset_count": self._soft_reset_count,
                "max_soft_resets": self._max_soft_resets,
                "power_cycle_count": self._power_cycle_count,
                "max_power_cycles": self._max_power_cycles,
                "consecutive_hw_fails": self._consecutive_hw_fails,
                "soft_reset_threshold": self._soft_reset_threshold,
                "power_controller": self._power_controller.name(),
                "last_recovery_seconds_ago": (
                    time.time() - self._last_recovery_time
                    if self._last_recovery_time > 0 else None
                ),
                "history_count": len(self._history),
                "recent_history": [
                    {
                        "level": r.level,
                        "success": r.success,
                        "duration": round(r.duration, 2),
                        "error": r.error,
                        "triggered_by": r.triggered_by,
                    }
                    for r in self._history[-10:]
                ],
            }

    # ── 回调 ──────────────────────────────────────────────

    def _fire_success(self, level: str) -> None:
        try:
            if self._on_recovery_success:
                self._on_recovery_success(level)
        except Exception:
            pass

    def _fire_failure(self, error: str) -> None:
        try:
            if self._on_recovery_failure:
                self._on_recovery_failure(error)
        except Exception:
            pass
