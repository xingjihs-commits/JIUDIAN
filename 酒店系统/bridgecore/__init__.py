"""
BridgeCore — 门锁协议接管五层架构

模块:
- panic_recovery.py  — 恐慌恢复引擎（Level 1 软复位 + Level 2 断电复位）
- ARCHITECTURE.md    — 完整架构文档
"""

from bridgecore.panic_recovery import (
    PanicRecovery,
    RecoveryRecord,
    RecoverySummary,
    PowerController,
    WinUsbPowerController,
    SerialRelayPowerController,
)

__all__ = [
    "PanicRecovery",
    "RecoveryRecord",
    "RecoverySummary",
    "PowerController",
    "WinUsbPowerController",
    "SerialRelayPowerController",
]
