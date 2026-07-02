"""tabs/card_system/_shared.py — 卡片系统共享常量与工具函数"""

from __future__ import annotations

from typing import Any

# ── 门锁卡品牌注册表 ──────────────────────────────────────────────
# 每个品牌的协议参数（baud / protocol / name）
CARD_BRANDS: dict[str, dict[str, Any]] = {
    "simulate": {"name": "模拟", "baud": 9600, "protocol": "mifare"},
    "prousb_v9": {"name": "proUSB V9", "baud": 115200, "protocol": "mifare"},
    "prousb_v10": {"name": "proUSB V10", "baud": 115200, "protocol": "mifare"},
    "prousb_v11": {"name": "proUSB V11", "baud": 115200, "protocol": "mifare"},
    "aidier": {"name": "爱迪尔", "baud": 9600, "protocol": "t5577"},
    "bida": {"name": "必达", "baud": 9600, "protocol": "mifare"},
    "level_lock": {"name": "力维", "baud": 9600, "protocol": "mifare"},
    "syron": {"name": "西容", "baud": 9600, "protocol": "mifare"},
    "yadidun": {"name": "雅迪顿", "baud": 9600, "protocol": "mifare"},
    "tongchuang": {"name": "同创新佳", "baud": 9600, "protocol": "mifare"},
    "baoxunda": {"name": "宝迅达", "baud": 9600, "protocol": "mifare"},
}

# ── 注册卡类型 ────────────────────────────────────────────────────
REGISTRY_CARD_KINDS: set[str] = {
    "guest", "master", "building", "floor", "emergency",
    "checkout", "clock", "record", "auth",
}


def _registry_kind_display(kind: str) -> str:
    """注册卡类型中文显示名。"""
    mapping = {
        "guest": "客人卡",
        "master": "总卡",
        "building": "楼栋卡",
        "floor": "楼层卡",
        "emergency": "应急卡",
        "checkout": "退房卡",
        "clock": "时钟卡",
        "record": "记录卡",
        "auth": "授权卡",
    }
    return mapping.get(kind, kind)


def _list_serial_ports() -> list[str]:
    """列出可用串口。"""
    try:
        import serial.tools.list_ports
        return [p.device for p in serial.tools.list_ports.comports()]
    except ImportError:
        return []
    except Exception:
        return []
