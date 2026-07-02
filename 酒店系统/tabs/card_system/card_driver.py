"""
硬件驱动层 — CardReaderDriver 与全局单例
支持：串口协议的多品牌读写器 / 模拟模式（无硬件时可测试）
"""
import threading
import uuid
import struct
from typing import Optional
from database import db
from power_controller_config import (
    encode_power_block,
    normalize_key_hex,
    power_config_summary,
    resolve_power_config,
)
from ._shared import CARD_BRANDS


class CardReaderDriver:
    """统一门卡读写器驱动接口：实际硬件通过串口通信；无硬件时使用模拟模式"""

    def __init__(self, brand: str = "simulate", port: str = "COM1"):
        self.brand = brand
        self.port = port
        self.protocol = CARD_BRANDS.get(brand, CARD_BRANDS["simulate"])["protocol"]
        self._ser = None
        self._simulate = (brand == "simulate")
        self._lock = threading.Lock()

    def connect(self) -> tuple[bool, str]:
        if self._simulate:
            return True, "模拟模式已就绪"
        try:
            import serial
            baud = CARD_BRANDS[self.brand]["baud"]
            self._ser = serial.Serial(self.port, baud, timeout=2)
            return True, f"已连接 {self.port}（{CARD_BRANDS[self.brand]['name']}）"
        except ImportError:
            self._simulate = True
            return True, "pyserial 未安装，已自动切换模拟模式"
        except Exception as e:
            return False, f"连接失败：{e}"

    def disconnect(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
        self._ser = None

    def is_connected(self) -> bool:
        if self._simulate:
            return True
        return self._ser is not None and self._ser.is_open

    def read_card_uid(self) -> tuple[bool, str]:
        if self._simulate:
            uid = uuid.uuid4().hex[:8].upper()
            return True, uid
        try:
            with self._lock:
                cmd = self._build_cmd("READ_UID")
                self._ser.write(cmd)
                resp = self._ser.read(16)
                uid = self._parse_uid(resp)
                return True, uid
        except Exception as e:
            return False, str(e)

    def write_card(self, room_id: str, expire_ts: int) -> tuple[bool, str]:
        if self._simulate:
            card_id = f"SIM-{uuid.uuid4().hex[:6].upper()}"
            ok_p, msg_p = self._write_power_sector(room_id, expire_ts)
            if not ok_p:
                return False, msg_p
            return True, card_id
        try:
            with self._lock:
                payload = self._encode_payload(room_id, expire_ts)
                cmd = self._build_cmd("WRITE", payload)
                self._ser.write(cmd)
                resp = self._ser.read(16)
                ok, card_id = self._parse_write_resp(resp)
                if not ok:
                    return False, card_id
                ok_p, msg_p = self._write_power_sector(room_id, expire_ts)
                if not ok_p:
                    return False, f"门锁数据已写，但取电扇区失败：{msg_p}"
                return True, card_id
        except Exception as e:
            return False, str(e)

    def _write_power_sector(self, room_id: str, expire_ts: int) -> tuple[bool, str]:
        cfg = resolve_power_config()
        if not cfg.get("enabled"):
            return True, "取电器写卡未启用"
        try:
            key_a = normalize_key_hex(cfg["key_a"])
            key_b = normalize_key_hex(cfg["key_b"])
        except ValueError as e:
            return False, f"取电密钥无效：{e}"
        sector = int(cfg["sector"])
        block = int(cfg["block"])
        if sector < 0 or sector > 15 or block < 0 or block > 2:
            return False, "扇区须 0–15，数据块须 0–2"
        data = encode_power_block(room_id, expire_ts, cfg["data_format"])
        db.set_config("power_ctrl_last_preview",
                      f"S{sector}B{block} {data.hex().upper()} room={room_id}")
        if self._simulate:
            return True, power_config_summary(cfg)
        try:
            with self._lock:
                payload = bytes([sector & 0xFF, block & 0xFF]) + bytes.fromhex(key_a) + data
                cmd = self._build_cmd("WRITE_MIFARE", payload)
                self._ser.write(cmd)
                resp = self._ser.read(8)
                if resp and resp[0] == 0x06:
                    return True, power_config_summary(cfg)
                return False, "读卡器未确认取电扇区写入（请确认固件支持 WRITE_MIFARE）"
        except Exception as e:
            return False, str(e)

    def cancel_card(self, card_id: str) -> tuple[bool, str]:
        if self._simulate:
            return True, f"卡 {card_id} 已注销（模拟）"
        try:
            with self._lock:
                cmd = self._build_cmd("CANCEL", card_id.encode())
                self._ser.write(cmd)
                resp = self._ser.read(8)
                if resp and resp[0] == 0x06:
                    return True, "注销成功"
                return False, "读卡器无响应"
        except Exception as e:
            return False, str(e)

    def _build_cmd(self, cmd_type: str, payload: bytes = b"") -> bytes:
        cmd_map = {"READ_UID": 0x01, "WRITE": 0x02, "CANCEL": 0x03, "WRITE_MIFARE": 0x04}
        cmd_byte = cmd_map.get(cmd_type, 0x00)
        frame = bytes([0x02, cmd_byte, len(payload)]) + payload
        crc = sum(frame) & 0xFF
        return frame + bytes([crc, 0x03])

    def _parse_uid(self, resp: bytes) -> str:
        if len(resp) >= 6:
            return resp[2:6].hex().upper()
        return uuid.uuid4().hex[:8].upper()

    def _encode_payload(self, room_id: str, expire_ts: int) -> bytes:
        room_bytes = room_id.encode("utf-8")[:8].ljust(8, b"\x00")
        ts_bytes = struct.pack(">I", expire_ts & 0xFFFFFFFF)
        return room_bytes + ts_bytes

    def _parse_write_resp(self, resp: bytes) -> tuple[bool, str]:
        if resp and resp[0] == 0x06:
            card_id = resp[1:5].hex().upper() if len(resp) >= 5 else uuid.uuid4().hex[:8].upper()
            return True, card_id
        return False, "写卡失败"


# ── 全局单例 ──
_driver: Optional[CardReaderDriver] = None


def get_driver() -> CardReaderDriver:
    global _driver
    if _driver is None:
        brand = db.get_config("card_reader_brand") or "simulate"
        port = db.get_config("card_reader_port") or "COM1"
        _driver = CardReaderDriver(brand, port)
    return _driver


def reset_driver():
    global _driver
    if _driver:
        _driver.disconnect()
    _driver = None
