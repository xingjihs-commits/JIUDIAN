"""
hardware_support.py — 硬件层增强：读卡器检测与热插拔监控

提供：
- detect_card_readers(): 扫描所有可用的 NFC/串口读卡器
- 支持 ACR122U / PN532 USB / PN532 UART
- 热插拔检测（监控 USB 设备变更事件）
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 已知读卡器 VID/PID 表
# ──────────────────────────────────────────────────────────────

KNOWN_READERS: Dict[str, Dict[str, Any]] = {
    "ACR122U": {
        "vid": "072F",
        "pid": "2200",
        "name": "ACS ACR122U NFC Reader",
        "interface": "USB",
        "protocol": "CCID/PCSC",
    },
    "PN532_USB": {
        "vid": "239A",
        "pid": "8016",
        "name": "Adafruit PN532 NFC/RFID (USB)",
        "interface": "USB",
        "protocol": "HID/UART",
    },
    "PN532_UART": {
        "name": "PN532 NFC/RFID (UART/Serial)",
        "interface": "UART",
        "protocol": "Serial",
        "baud_rate": 115200,
    },
    "proUSB_Encoder": {
        "vid": "320F",
        "pid": None,
        "name": "proUSB 发卡器 (d12 芯片)",
        "interface": "USB",
        "protocol": "HID",
    },
}


# ──────────────────────────────────────────────────────────────
# 读卡器检测
# ──────────────────────────────────────────────────────────────

def detect_card_readers() -> List[Dict[str, Any]]:
    """扫描所有可用的 NFC/串口读卡器。

    Returns:
        检测到的读卡器列表，每项包含 name, interface, vid, pid 等信息。
    """
    readers: List[Dict[str, Any]] = []

    # 1. 通过 pywinusb 扫描 HID 设备
    try:
        import pywinusb.hid as hid
        all_devices = hid.HidDeviceFilter().get_devices()
        for dev in all_devices:
            vid = f"{dev.vendor_id:04X}" if dev.vendor_id else ""
            pid = f"{dev.product_id:04X}" if dev.product_id else ""
            info = {
                "name": dev.product_name or f"HID {vid}:{pid}",
                "interface": "USB",
                "vid": vid,
                "pid": pid,
                "hid_path": dev.device_path,
            }
            # 匹配已知读卡器
            for reader_name, spec in KNOWN_READERS.items():
                if spec.get("vid") and spec["vid"].upper() == vid.upper():
                    if spec.get("pid") is None or spec.get("pid", "").upper() == pid.upper():
                        info["matched_reader"] = reader_name
                        info["protocol"] = spec.get("protocol", "")
                        break
            readers.append(info)
    except ImportError:
        logger.debug("pywinusb 未安装，跳过 HID 设备扫描")
    except Exception as e:
        logger.warning("HID 设备扫描异常: %s", e)

    # 2. 通过 pyscard 扫描 PC/SC 读卡器
    try:
        from smartcard.System import readers as sc_readers
        for r in sc_readers():
            readers.append({
                "name": str(r),
                "interface": "USB",
                "protocol": "PC/SC",
            })
    except ImportError:
        logger.debug("pyscard 未安装，跳过 PC/SC 扫描")
    except Exception as e:
        logger.warning("PC/SC 扫描异常: %s", e)

    # 3. 通过 serial.tools.list_ports 扫描串口设备
    try:
        import serial.tools.list_ports
        for port in serial.tools.list_ports.comports():
            readers.append({
                "name": port.description or port.device,
                "device": port.device,
                "interface": "UART",
                "vid": f"{port.vid:04X}" if port.vid else "",
                "pid": f"{port.pid:04X}" if port.pid else "",
            })
    except ImportError:
        logger.debug("pyserial 未安装，跳过串口扫描")
    except Exception as e:
        logger.warning("串口扫描异常: %s", e)

    return readers


def find_pro_usb_encoder() -> Optional[Dict[str, Any]]:
    """专门找 proUSB 发卡器 (VID_320F)。"""
    readers = detect_card_readers()
    for r in readers:
        if r.get("vid", "").upper() == "320F":
            return r
    return None


# ──────────────────────────────────────────────────────────────
# 热插拔监控
# ──────────────────────────────────────────────────────────────

class ReaderHotplugMonitor:
    """USB 读卡器热插拔监控器。

    在后台线程中轮询设备列表，检测到变化时调用回调。
    """

    def __init__(self, poll_interval: float = 2.0):
        self._poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_readers: List[Dict[str, Any]] = []
        self._on_plug: Optional[callable] = None
        self._on_unplug: Optional[callable] = None

    def on_plug(self, callback):
        """注册设备插入回调。callback(reader_info: dict)。"""
        self._on_plug = callback
        return self

    def on_unplug(self, callback):
        """注册设备拔出回调。callback(reader_info: dict)。"""
        self._on_unplug = callback
        return self

    def start(self):
        """启动后台监控线程。"""
        if self._running:
            return
        self._running = True
        self._last_readers = detect_card_readers()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("读卡器热插拔监控已启动")

    def stop(self):
        """停止后台监控。"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("读卡器热插拔监控已停止")

    def _poll_loop(self):
        while self._running:
            try:
                current = detect_card_readers()
                self._detect_changes(current)
                self._last_readers = current
            except Exception as e:
                logger.warning("热插拔轮询异常: %s", e)
            time.sleep(self._poll_interval)

    def _detect_changes(self, current: List[Dict[str, Any]]):
        """对比当前设备列表与上次列表，检测插拔事件。"""
        last_names = {r.get("name", "") for r in self._last_readers}
        current_names = {r.get("name", "") for r in current}

        plugged = current_names - last_names
        unplugged = last_names - current_names

        for name in plugged:
            matched = next((r for r in current if r.get("name") == name), None)
            if matched and self._on_plug:
                try:
                    self._on_plug(matched)
                except Exception:
                    pass
            logger.info("读卡器已插入: %s", name)

        for name in unplugged:
            matched = next((r for r in self._last_readers if r.get("name") == name), None)
            if matched and self._on_unplug:
                try:
                    self._on_unplug(matched)
                except Exception:
                    pass
            logger.info("读卡器已拔出: %s", name)

    @property
    def is_running(self) -> bool:
        return self._running


# ──────────────────────────────────────────────────────────────
# 便捷入口
# ──────────────────────────────────────────────────────────────

_hotplug_monitor: Optional[ReaderHotplugMonitor] = None


def get_hotplug_monitor() -> ReaderHotplugMonitor:
    """获取全局热插拔监控单例。"""
    global _hotplug_monitor
    if _hotplug_monitor is None:
        _hotplug_monitor = ReaderHotplugMonitor()
    return _hotplug_monitor
