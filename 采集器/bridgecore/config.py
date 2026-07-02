"""
bridgecore/config.py — 集中配置系统

所有可调参数集中管理，支持运行时重载。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# 默认配置
# ──────────────────────────────────────────────────────────────────

DEFAULT_FAULT_THRESHOLD = 3
DEFAULT_FAULT_BACKOFF_BASE = 1.0  # 秒
DEFAULT_FAULT_BACKOFF_MAX = 30.0  # 秒
DEFAULT_FAULT_COOLDOWN = 30.0

DEFAULT_RX_INTERVAL = 0.5  # 秒
DEFAULT_RX_FAIL_THRESHOLD = 3
DEFAULT_RX_PROBE_TIMEOUT = 1.0

DEFAULT_REPLAY_BATCH_SIZE = 0  # 0 = 一次性全部回放
DEFAULT_REPLAY_INTER_OP_DELAY = 0.0  # 操作间延迟
DEFAULT_REPLAY_READBACK = True

DEFAULT_RECORDING_DIR = "bridgecore_recordings"
DEFAULT_RECORDING_MAX_SIZE_MB = 50
DEFAULT_RECORDING_AUTO_ROTATE = True

DEFAULT_LOG_DIR = "logs/bridgecore"
DEFAULT_LOG_LEVEL = "INFO"


DEFAULT_RELAY_PORT = ""
DEFAULT_RELAY_ON_CMD = ""
DEFAULT_RELAY_OFF_CMD = ""
DEFAULT_POWER_OFF_DURATION = 1.5
DEFAULT_PANIC_SOFT_RESET_THRESHOLD = 3
DEFAULT_PANIC_MAX_SOFT_RESETS = 2
DEFAULT_PANIC_MAX_POWER_CYCLES = 1


@dataclass
class FaultConfig:
    """熔断器配置"""
    threshold: int = DEFAULT_FAULT_THRESHOLD
    backoff_base: float = DEFAULT_FAULT_BACKOFF_BASE
    backoff_max: float = DEFAULT_FAULT_BACKOFF_MAX
    cooldown: float = DEFAULT_FAULT_COOLDOWN


@dataclass
class RxMonitorConfig:
    """RX 监控配置"""
    interval: float = DEFAULT_RX_INTERVAL
    fail_threshold: int = DEFAULT_RX_FAIL_THRESHOLD
    probe_timeout: float = DEFAULT_RX_PROBE_TIMEOUT


@dataclass
class ReplayConfig:
    """Injector 回放配置"""
    batch_size: int = DEFAULT_REPLAY_BATCH_SIZE
    inter_op_delay: float = DEFAULT_REPLAY_INTER_OP_DELAY
    readback: bool = DEFAULT_REPLAY_READBACK


@dataclass
class RecordingConfig:
    """录制配置"""
    directory: str = DEFAULT_RECORDING_DIR
    max_size_mb: int = DEFAULT_RECORDING_MAX_SIZE_MB
    auto_rotate: bool = DEFAULT_RECORDING_AUTO_ROTATE


@dataclass
class PanicConfig:
    """恐慌恢复配置"""
    soft_reset_threshold: int = DEFAULT_PANIC_SOFT_RESET_THRESHOLD
    max_soft_resets: int = DEFAULT_PANIC_MAX_SOFT_RESETS
    max_power_cycles: int = DEFAULT_PANIC_MAX_POWER_CYCLES
    relay_port: str = DEFAULT_RELAY_PORT
    relay_on_cmd: str = DEFAULT_RELAY_ON_CMD
    relay_off_cmd: str = DEFAULT_RELAY_OFF_CMD
    power_off_duration: float = DEFAULT_POWER_OFF_DURATION


@dataclass
class BridgeCoreSettings:
    """BridgeCore 全局配置"""
    fault: FaultConfig = field(default_factory=FaultConfig)
    rx_monitor: RxMonitorConfig = field(default_factory=RxMonitorConfig)
    replay: ReplayConfig = field(default_factory=ReplayConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    panic: PanicConfig = field(default_factory=PanicConfig)
    log_dir: str = DEFAULT_LOG_DIR
    log_level: str = DEFAULT_LOG_LEVEL
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BridgeCoreSettings":
        settings = cls()
        if "fault" in d:
            settings.fault = FaultConfig(**d["fault"])
        if "rx_monitor" in d:
            settings.rx_monitor = RxMonitorConfig(**d["rx_monitor"])
        if "replay" in d:
            settings.replay = ReplayConfig(**d["replay"])
        if "recording" in d:
            settings.recording = RecordingConfig(**d["recording"])
        if "panic" in d:
            settings.panic = PanicConfig(**d["panic"])
        if "log_dir" in d:
            settings.log_dir = d["log_dir"]
        if "log_level" in d:
            settings.log_level = d["log_level"]
        if "enabled" in d:
            settings.enabled = d["enabled"]
        return settings

    def save(self, filepath: str | Path) -> str:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return str(path)

    @classmethod
    def load(cls, filepath: str | Path) -> "BridgeCoreSettings":
        path = Path(filepath)
        if not path.is_file():
            logger.info("[BridgeCore] 配置文件不存在 %s，使用默认", path)
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return cls.from_dict(d)
        except Exception as e:
            logger.warning("[BridgeCore] 加载配置失败 %s: %s，使用默认", path, e)
            return cls()


# ── 全局单例 ─────────────────────────────────────────────────────

_settings: BridgeCoreSettings = BridgeCoreSettings()


def get_settings() -> BridgeCoreSettings:
    return _settings


def reload_settings(filepath: str | Path = "") -> BridgeCoreSettings:
    global _settings
    if filepath:
        _settings = BridgeCoreSettings.load(filepath)
    else:
        _settings = BridgeCoreSettings()
    logger.info("[BridgeCore] 配置已重载: threshold=%d, rx_interval=%.1fs",
                _settings.fault.threshold, _settings.rx_monitor.interval)
    return _settings
