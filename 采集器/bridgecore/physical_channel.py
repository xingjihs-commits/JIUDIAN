"""
bridgecore/physical_channel.py — 物理通道抽象层

统一三种物理通信通道，上层不需要关心读卡器的连接方式。

通道类型：
- DllChannel — 通过厂家 DLL 通信（封装 RflBridge）
- SerialChannel — 直连 RS232 串口（pyserial）
- UsbHidChannel — 直连 USB HID 设备（pywinusb）

自动检测：ChannelDetector 扫描安装目录，自动选出最优通道。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 数据类
# ──────────────────────────────────────────────────────────────────

@dataclass
class ChannelInfo:
    """物理通道的元信息。"""
    channel_type: str          # "dll" / "serial" / "usb_hid"
    device_name: str           # 设备名称
    vid: str = ""              # USB Vendor ID
    pid: str = ""              # USB Product ID
    dll_path: str = ""         # DLL 路径（DllChannel）
    dll_functions: dict = field(default_factory=dict)  # 探测到的函数映射
    com_port: str = ""         # COM 口（SerialChannel）
    baud_rate: int = 0         # 波特率（SerialChannel）

    @property
    def label(self) -> str:
        if self.channel_type == "dll":
            return f"DLL: {Path(self.dll_path).name}"
        elif self.channel_type == "serial":
            return f"COM: {self.com_port} @ {self.baud_rate}"
        elif self.channel_type == "usb_hid":
            return f"HID: {self.vid}:{self.pid}"
        return self.channel_type


@dataclass
class ProbeResult:
    """DLL 探测结果的结构化表示。"""
    dll_name: str = ""
    dll_path: str = ""
    is_32bit: bool = False
    exports: list[dict] = field(default_factory=list)
    classified: dict[str, str] = field(default_factory=dict)
    hardcoded_match: dict[str, str] = field(default_factory=dict)
    brand_guess: str = ""
    confidence: float = 0.0
    can_issue: bool = False

    def has_function(self, group: str) -> bool:
        return group in self.classified or group in self.hardcoded_match

    def get_function(self, group: str) -> str:
        return self.classified.get(group) or self.hardcoded_match.get(group) or ""


# ──────────────────────────────────────────────────────────────────
# 抽象基类
# ──────────────────────────────────────────────────────────────────

class PhysicalChannel(ABC):
    """物理通道抽象基类。所有具体通道必须实现的方法。"""

    def __init__(self, info: ChannelInfo):
        self._info = info
        self._opened = False

    @property
    def info(self) -> ChannelInfo:
        return self._info

    @property
    def opened(self) -> bool:
        return self._opened

    @abstractmethod
    def open(self) -> bool:
        """打开发卡器连接。"""

    @abstractmethod
    def close(self) -> None:
        """关闭发卡器连接。"""

    @abstractmethod
    def send(self, data: bytes) -> bytes:
        """发送数据并返回响应。"""

    @abstractmethod
    def probe(self) -> bool:
        """探测链路是否健康。"""

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


# ──────────────────────────────────────────────────────────────────
# DllChannel — 封装 RflBridge
# ──────────────────────────────────────────────────────────────────

class DllChannel(PhysicalChannel):
    """通过厂家 DLL 通信。封装现有的 RflBridge。"""

    def __init__(self, info: ChannelInfo, bridge: Any = None):
        super().__init__(info)
        self._bridge = bridge
        self._dll_loaded = False

    def open(self) -> bool:
        try:
            from ..collector_bridge import get_bridge
            bridge = self._bridge or get_bridge()
            self._bridge = bridge
            bridge.start()

            dll_path = self._info.dll_path
            dll_fns = self._info.dll_functions

            if dll_path:
                extra = [str(Path(dll_path).parent)]
                resp = bridge.load_dll(dll_path, extra)
                if resp.get("ok") and resp.get("loaded"):
                    self._dll_loaded = True
                    init_fn = dll_fns.get("init_usb") or dll_fns.get("init") or "initializeUSB"
                    if hasattr(bridge, "initialize"):
                        init_resp = bridge.initialize(d12=1)
                        if init_resp.get("ok") and init_resp.get("ret") == 0:
                            self._opened = True
                            return True
            return False
        except Exception as e:
            logger.error("[DllChannel] 打开失败: %s", e)
            return False

    def close(self) -> None:
        try:
            if self._bridge:
                self._bridge.close()
        except Exception:
            pass
        self._opened = False

    def send(self, data: bytes) -> bytes:
        raise NotImplementedError("DllChannel 通过 RPC 调用而非原始字节通信")

    def probe(self) -> bool:
        try:
            if self._bridge and hasattr(self._bridge, "ping"):
                return bool(self._bridge.ping())
            return False
        except Exception:
            return False

    @property
    def bridge(self) -> Any:
        return self._bridge


# ──────────────────────────────────────────────────────────────────
# SerialChannel — 直连串口
# ──────────────────────────────────────────────────────────────────

class SerialChannel(PhysicalChannel):
    """直连 RS232 串口。通过 pyserial 通信。"""

    BAUD_CANDIDATES: ClassVar[list[int]] = [9600, 19200, 38400, 57600, 115200]

    def __init__(self, info: ChannelInfo):
        super().__init__(info)
        self._serial: Any = None

    def open(self) -> bool:
        try:
            import serial
        except ImportError:
            logger.error("[SerialChannel] pyserial 未安装")
            return False

        port = self._info.com_port
        baud = self._info.baud_rate

        if not baud:
            for candidate in self.BAUD_CANDIDATES:
                try:
                    ser = serial.Serial(port, candidate, timeout=1.0)
                    ser.close()
                    baud = candidate
                    break
                except Exception:
                    continue
            if not baud:
                return False

        try:
            self._serial = serial.Serial(port, baud, timeout=2.0)
            self._opened = True
            logger.info("[SerialChannel] %s @ %d 已打开", port, baud)
            return True
        except Exception as e:
            logger.error("[SerialChannel] 打开 %s 失败: %s", port, e)
            return False

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        self._opened = False

    def send(self, data: bytes) -> bytes:
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("串口未打开")
        self._serial.write(data)
        self._serial.flush()
        time.sleep(0.05)
        if self._serial.in_waiting:
            return self._serial.read(self._serial.in_waiting)
        return b""

    def probe(self) -> bool:
        try:
            return self._serial is not None and self._serial.is_open
        except Exception:
            return False


# ──────────────────────────────────────────────────────────────────
# UsbHidChannel — 直连 USB HID
# ──────────────────────────────────────────────────────────────────

class UsbHidChannel(PhysicalChannel):
    """直连 USB HID 设备。通过 pywinusb 通信。"""

    def __init__(self, info: ChannelInfo):
        super().__init__(info)
        self._device: Any = None
        self._recv_buffer: bytearray = bytearray()
        self._recv_lock = threading.Lock()

    def open(self) -> bool:
        try:
            import pywinusb.hid as hid
        except ImportError:
            logger.error("[UsbHidChannel] pywinusb 未安装")
            return False

        vid = int(self._info.vid, 16) if self._info.vid else None
        pid = int(self._info.pid, 16) if self._info.pid else None

        filter_args = {}
        if vid:
            filter_args["vendor_id"] = vid
        if pid:
            filter_args["product_id"] = pid

        try:
            devices = hid.HidDeviceFilter(**filter_args).get_devices()
            if not devices:
                logger.warning("[UsbHidChannel] 未找到匹配的 HID 设备")
                return False

            self._device = devices[0]
            self._device.open()
            self._device.set_raw_data_handler(self._on_data)
            self._opened = True
            logger.info("[UsbHidChannel] %s:%s 已打开", self._info.vid, self._info.pid)
            return True
        except Exception as e:
            logger.error("[UsbHidChannel] 打开失败: %s", e)
            return False

    def close(self) -> None:
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass
        self._opened = False

    def _on_data(self, data: Any) -> None:
        if data is None:
            return
        try:
            raw = bytes(data) if not isinstance(data, (bytes, bytearray)) else bytes(data)
        except Exception:
            return
        with self._recv_lock:
            self._recv_buffer.extend(raw)

    def recv(self, max_bytes: int = 64, timeout: float = 1.0) -> bytes:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._recv_lock:
                if self._recv_buffer:
                    chunk = bytes(self._recv_buffer[:max_bytes])
                    del self._recv_buffer[:len(chunk)]
                    return chunk
            time.sleep(0.02)
        return b""

    def send(self, data: bytes) -> bytes:
        if not self._device:
            raise RuntimeError("HID 设备未打开")
        try:
            self._device.send_output_report(data)
            return b""
        except Exception as e:
            logger.error("[UsbHidChannel] 发送失败: %s", e)
            return b""

    def probe(self) -> bool:
        try:
            return self._device is not None and self._device.is_plugged()
        except Exception:
            return False


# ──────────────────────────────────────────────────────────────────
# 通道检测器
# ──────────────────────────────────────────────────────────────────

def find_bridgecore_root() -> Path:
    """找到酒店系统根目录（bridgecore 的父目录）。"""
    return Path(__file__).resolve().parent.parent


def _import_dll_probe():
    """动态导入 lock_deploy.dll_probe 模块。"""
    import importlib.util
    root = find_bridgecore_root()
    probe_path = root / "lock_deploy" / "dll_probe.py"
    if not probe_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("dll_probe", str(probe_path))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ChannelDetector:
    """
    自动检测最优物理通道。

    扫描策略：
    1. USB HID 设备扫描（依赖 pywinusb，复用 hardware_support）
    2. DLL 文件扫描（复用 lock_deploy/dll_probe）
    3. COM 口扫描（依赖 pyserial）
    """

    @staticmethod
    def detect(install_dir: str | Path = "") -> list[ChannelInfo]:
        """返回所有可用的物理通道列表，按优先级排序。"""
        channels: list[ChannelInfo] = []

        # 2. 扫描 DLL
        if install_dir:
            try:
                dp = _import_dll_probe()
                if dp:
                    result = dp.probe(str(install_dir))
                    if result.get("detected"):
                        dll_path = result.get("dll_path", "")
                        channels.append(ChannelInfo(
                            channel_type="dll",
                            device_name=result.get("brand_guess", "Unknown DLL"),
                            dll_path=dll_path,
                            dll_functions=result.get("candidate_profile", {}).get("dll", {}),
                        ))
            except Exception as e:
                logger.debug("[ChannelDetector] DLL 扫描: %s", e)

        # 3. 扫描 COM 口
        try:
            import serial.tools.list_ports
            for port in serial.tools.list_ports.comports():
                channels.append(ChannelInfo(
                    channel_type="serial",
                    device_name=port.description or port.device,
                    com_port=port.device,
                    baud_rate=0,
                    vid=f"{port.vid:04X}" if port.vid else "",
                    pid=f"{port.pid:04X}" if port.pid else "",
                ))
        except ImportError:
            pass
        except Exception as e:
            logger.debug("[ChannelDetector] COM 扫描: %s", e)

        return channels
