"""
bridgecore/serial_channel.py — 串口发卡器通信封装

部分门锁品牌（如宝迅达、部分老款必达/力维）用 RS-232 串口替代 USB-HID。
本模块提供统一的串口扫描、打开、APDU 收发接口，可在 bridge32 中串口替代 USB。

用法：
    scanner = SerialScanner()
    ports = scanner.scan()  # [(port, desc), ...]
    ch = SerialChannel(port="COM3", baudrate=9600)
    ch.open()
    ch.send(bytes.fromhex("AA BB CC DD"))
    resp = ch.recv(32)
    ch.close()
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from typing import ClassVar, Optional

logger = logging.getLogger(__name__)

# 尝试导入 pyserial
try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
    serial = None  # type: ignore[assignment]
    serial_errors = ()  # type: ignore[assignment]


# 门锁行业常用串口参数
_BAUDRATES = (9600, 19200, 38400, 115200)
_BAUDRATES_SLOW = (9600, 19200)  # 先试慢速
_DEFAULT_TIMEOUT = 3.0


class SerialPortInfo:
    """串口扫描结果。"""
    __slots__ = ("port", "description", "vid", "pid", "baudrate")

    def __init__(
        self,
        port: str,
        description: str = "",
        vid: str = "",
        pid: str = "",
        baudrate: int = 9600,
    ):
        self.port = port
        self.description = description
        self.vid = vid
        self.pid = pid
        self.baudrate = baudrate

    def to_dict(self) -> dict:
        return {
            "port": self.port,
            "description": self.description,
            "vid": self.vid,
            "pid": self.pid,
            "baudrate": self.baudrate,
        }

    def __repr__(self) -> str:
        return (
            f"SerialPortInfo(port={self.port!r}, "
            f"desc={self.description[:32]!r}, "
            f"vid={self.vid}, pid={self.pid}, "
            f"baud={self.baudrate})"
        )


class SerialScanner:
    """枚举系统串口，尝试握手确认是否为发卡器。"""

    # 常见的门锁串口握手指令（品牌特有识别码）
    _PROBE_COMMANDS: ClassVar[list[bytes]] = [
        bytes.fromhex("AA 00 01 00 00 00 01 AB"),  # 通用探测
        bytes.fromhex("50 4F 4C 4C"),               # POLL
        bytes.fromhex("AA 55 00 FF 00 FF 01 00"),   # 力维式
    ]

    def scan(self) -> list[SerialPortInfo]:
        """枚举系统可用串口。"""
        ports: list[SerialPortInfo] = []
        if not HAS_SERIAL:
            logger.warning("pyserial 未安装，跳过串口扫描")
            return ports
        try:
            for p in serial.tools.list_ports.comports():
                info = SerialPortInfo(
                    port=p.device,
                    description=p.description or "",
                    vid=f"{p.vid:04X}" if p.vid else "",
                    pid=f"{p.pid:04X}" if p.pid else "",
                )
                ports.append(info)
        except Exception as e:
            logger.warning("串口扫描异常: %s", e)
        return ports

    def probe(
        self,
        ports: Optional[list[SerialPortInfo]] = None,
        *,
        timeout: float = 2.0,
    ) -> list[SerialPortInfo]:
        """扫描 + 尝试握手，返回可响应的串口列表。

        会尝试多个波特率与每个端口握手。
        """
        if not HAS_SERIAL:
            return []
        if ports is None:
            ports = self.scan()
        responsive: list[SerialPortInfo] = []
        for info in ports:
            found_baud = self._handshake_one(info.port, timeout=timeout)
            if found_baud:
                info.baudrate = found_baud
                responsive.append(info)
                logger.info("串口 %s 在 %dbps 响应", info.port, found_baud)
            else:
                logger.debug("串口 %s 无响应，跳过", info.port)
        return responsive

    def _handshake_one(self, port: str, *, timeout: float) -> Optional[int]:
        """尝试打开串口，用各波特率发探测指令。"""
        baudrates = _BAUDRATES
        for baud in baudrates:
            try:
                with serial.Serial(
                    port=port,
                    baudrate=baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=timeout,
                ) as ser:
                    # 发探测指令
                    for cmd in self._PROBE_COMMANDS:
                        ser.write(cmd)
                        time.sleep(0.1)
                        resp = ser.read(16)
                        if len(resp) >= 2:
                            return baud
                    # 读裸数据（部分品牌不休眠，直接回应）
                    ser.write(b"\x00")
                    time.sleep(0.15)
                    resp = ser.read(16)
                    if len(resp) >= 2:
                        return baud
            except (serial.SerialException, OSError):
                continue
        return None


class SerialChannel:
    """串口发卡器通信通道。

    线程安全（内部用锁保护串口写 / 清缓冲）。
    支持 with 语句自动关闭。

    Args:
        port: COM 口名称，如 "COM3"。
        baudrate: 波特率，默认 9600。
        timeout: 读写超时秒数。
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: Optional[serial.Serial] = None  # type: ignore[arg-type]
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def open(self) -> bool:
        """打开串口连接。"""
        if self.is_open:
            return True
        if not HAS_SERIAL:
            raise RuntimeError("pyserial 未安装，无法打开串口")
        try:
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
            )
            self._ser.flushInput()
            self._ser.flushOutput()
            logger.info("串口 %s 已打开 (%d bps)", self.port, self.baudrate)
            return True
        except serial.SerialException as e:
            logger.error("打开串口 %s 失败: %s", self.port, e)
            self._ser = None
            return False

    def close(self) -> None:
        """关闭串口。"""
        with self._lock:
            if self._ser and self._ser.is_open:
                try:
                    self._ser.close()
                except Exception:
                    pass
            self._ser = None

    def send(self, data: bytes) -> int:
        """发送数据，返回发送字节数。"""
        with self._lock:
            if not self.is_open:
                raise RuntimeError(f"串口 {self.port} 未打开")
            return self._ser.write(data)

    def recv(self, size: int = 256) -> bytes:
        """读取数据，优先读缓冲内可用字节避免空等超时。

        先检查 in_waiting（已有字节数），有则读已有字节。
        无则按 timeout 读满期望 size 或超时返回。
        """
        with self._lock:
            if not self.is_open:
                raise RuntimeError(f"串口 {self.port} 未打开")
            waiting = self._ser.in_waiting
            if waiting > 0:
                return self._ser.read(min(waiting, size))
            return self._ser.read(size)

    def send_apdu(self, apdu: bytes, *, wait_ms: float = 0.2) -> bytes:
        """发 APDU 指令并等响应。

        清空缓冲 → 发送 → 等待 → 读取。

        Args:
            apdu: APDU 指令字节。
            wait_ms: 发送到读取的等待时间。

        Returns:
            响应字节（可能为空）。
        """
        with self._lock:
            if not self.is_open:
                raise RuntimeError(f"串口 {self.port} 未打开")
            self._ser.flushInput()
            self._ser.flushOutput()
            self._ser.write(apdu)
            time.sleep(wait_ms)
            return self._ser.read(256)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()


# ── 兼容桥接的 SerialBridge ──────────────────────────────────
# 让串口通道也能像 CollectorBridge.direct_read_usb 那样被调用


class SerialBridge:
    """串口桥接适配器——外观与 CollectorBridge 部分方法一致。

    用于在串口通道上模拟 direct_read_usb / direct_write_usb / buzzer / ping。
    并非完整桥接层，只暴露采集器需要的子集。
    """

    def __init__(self, port: str, baudrate: int = 9600,
                 profile: Optional[dict] = None):
        self._channel = SerialChannel(port, baudrate)
        self._port = port
        self._baudrate = baudrate
        self._started = False
        self._profile = profile or {}

    @property
    def channel(self) -> SerialChannel:
        return self._channel

    @property
    def port(self) -> str:
        return self._port

    @property
    def is_ready(self) -> bool:
        return self._started and self._channel.is_open

    def _get_read_cmd(self) -> bytes:
        """从 profile 取读卡指令，无则返回通用探测。"""
        serial_cfg = self._profile.get("serial", {})
        cmd_hex = serial_cfg.get("read_command", "")
        if cmd_hex:
            return bytes.fromhex(cmd_hex.replace(" ", ""))
        return bytes.fromhex("AA 00 01 00 00 00 01 AB")

    def _get_write_cmd(self) -> bytes:
        """从 profile 取写卡指令前缀，无则返回通用前缀。"""
        serial_cfg = self._profile.get("serial", {})
        cmd_hex = serial_cfg.get("write_command", "")
        if cmd_hex:
            return bytes.fromhex(cmd_hex.replace(" ", ""))
        return bytes.fromhex("BB 00 01 00 DD")

    def _get_ack_byte(self) -> int:
        """从 profile 取写卡确认字节。"""
        serial_cfg = self._profile.get("serial", {})
        ack_str = serial_cfg.get("ack_byte", "BB")
        try:
            return int(ack_str, 16)
        except ValueError:
            return 0xBB

    def _get_buzzer_cmd(self) -> bytes:
        """从 profile 取蜂鸣指令。"""
        serial_cfg = self._profile.get("serial", {})
        cmd_hex = serial_cfg.get("buzzer_command", "")
        if cmd_hex:
            return bytes.fromhex(cmd_hex.replace(" ", ""))
        return bytes.fromhex("CC 00 01 00 01 01")

    def start(self, *, force_restart: bool = False) -> None:
        """启动（打开串口）。"""
        if self._started and not force_restart:
            return
        ok = self._channel.open()
        self._started = ok
        if not ok:
            raise RuntimeError(f"无法打开串口 {self._port}")

    def stop(self) -> None:
        self._channel.close()
        self._started = False

    def ping(self, timeout: float = 2.0) -> bool:
        """串口探针：发读卡指令等短响应，验证通道存活。"""
        if not self.is_ready:
            return False
        try:
            cmd = self._get_read_cmd()
            self._channel.send(cmd)
            time.sleep(0.15)
            resp = self._channel.recv(16)
            return len(resp) >= 2
        except Exception:
            return False

    def ensure_alive(self) -> bool:
        """检测通道存活，断开时自动重连。"""
        if self.ping():
            return True
        logger.warning("串口 %s 无响应，尝试重连...", self._port)
        self.stop()
        try:
            self.start()
            return self.ping()
        except Exception:
            return False

    def direct_read_usb(self, *, d12: int = 1,
                        timeout: float = 6.0) -> dict:
        """模拟 direct_read_usb，走串口读卡。

        Args:
            d12: 忽略（串口单通道）。
            timeout: 超时秒数。

        Returns:
            {"ok": True/False, "hex": "...", "data": "...", "error": "..."}
        """
        try:
            if not self.is_ready:
                if not self.ensure_alive():
                    return {"ok": False, "error": "串口未连接",
                            "hex": "", "data": ""}
            ch = self._channel
            cmd = self._get_read_cmd()
            ch.send(cmd)
            time.sleep(0.3)
            resp = ch.recv(256)
            if len(resp) < 2:
                return {"ok": False, "error": "无响应",
                        "hex": "", "data": ""}
            hex_str = resp.hex().upper()
            return {"ok": True, "hex": hex_str, "data": resp.hex(),
                    "error": ""}
        except Exception as e:
            return {"ok": False, "error": str(e), "hex": "", "data": ""}

    def direct_write_usb(self, *, d12: int = 1, card_hex: str,
                         timeout: float = 6.0) -> dict:
        """模拟 direct_write_usb，走串口写卡。

        Args:
            d12: 忽略（串口单通道）。
            card_hex: 要写入的卡片 hex 字符串。
            timeout: 超时秒数。

        Returns:
            {"ok": True/False, "error": "..."}
        """
        try:
            if not self.is_ready:
                if not self.ensure_alive():
                    return {"ok": False, "error": "串口未连接"}
            data = bytes.fromhex(card_hex)
            ch = self._channel
            prefix = self._get_write_cmd()
            cmd = prefix + data
            ch.send(cmd)
            time.sleep(0.5)
            resp = ch.recv(32)
            ack_byte = self._get_ack_byte()
            if resp and resp[0] == ack_byte:
                return {"ok": True, "error": ""}
            return {"ok": False, "error": f"写卡无确认: {resp.hex()[:32]}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def buzzer(self, d12: int = 1, t: int = 20) -> dict:
        """模拟 buzzer（蜂鸣器），走串口发短鸣指令。"""
        try:
            if not self.is_ready:
                return {"ok": False, "error": "串口未连接"}
            cmd = self._get_buzzer_cmd()
            self._channel.send(cmd)
            time.sleep(0.1)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
