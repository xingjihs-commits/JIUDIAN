"""
bridgecore/operator_lib.py — 标准校验算子库（Collector 独立版）
"""
from __future__ import annotations
import logging
from typing import Callable

logger = logging.getLogger(__name__)

# CRC8 系列
def crc8_maxim(data: bytes) -> bytes:
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x01:
                crc = (crc >> 1) ^ 0x8C
            else:
                crc >>= 1
    return bytes([crc])

def crc8_dallas(data: bytes) -> bytes:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x01:
                crc = (crc >> 1) ^ 0x8C
            else:
                crc >>= 1
    return bytes([crc])

def crc8_itu(data: bytes) -> bytes:
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x07
            else:
                crc <<= 1
            crc &= 0xFF
    crc ^= 0x55
    return bytes([crc])

def crc8_rohc(data: bytes) -> bytes:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x01:
                crc = (crc >> 1) ^ 0xE0
            else:
                crc >>= 1
    return bytes([crc])

# CRC16 系列
def _reverse_byte(b: int) -> int:
    b = ((b & 0xF0) >> 4) | ((b & 0x0F) << 4)
    b = ((b & 0xCC) >> 2) | ((b & 0x33) << 2)
    b = ((b & 0xAA) >> 1) | ((b & 0x55) << 1)
    return b

def _reverse_16(v: int) -> int:
    v = ((v & 0xFF00) >> 8) | ((v & 0x00FF) << 8)
    v = ((v & 0xF0F0) >> 4) | ((v & 0x0F0F) << 4)
    v = ((v & 0xCCCC) >> 2) | ((v & 0x3333) << 2)
    v = ((v & 0xAAAA) >> 1) | ((v & 0x5555) << 1)
    return v

def _crc16(data: bytes, poly: int, init: int, ref_in: bool, ref_out: bool, xor_out: int) -> int:
    crc = init
    for byte in data:
        if ref_in:
            byte = _reverse_byte(byte)
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ poly
            else:
                crc <<= 1
            crc &= 0xFFFF
    if ref_out:
        crc = _reverse_16(crc)
    crc ^= xor_out
    return crc & 0xFFFF

def crc16_modbus(data: bytes) -> bytes:
    crc = _crc16(data, 0x8005, 0xFFFF, True, True, 0x0000)
    return crc.to_bytes(2, byteorder="little")

def crc16_ccitt(data: bytes) -> bytes:
    crc = _crc16(data, 0x1021, 0xFFFF, False, False, 0x0000)
    return crc.to_bytes(2, byteorder="big")

def crc16_xmodem(data: bytes) -> bytes:
    crc = _crc16(data, 0x1021, 0x0000, False, False, 0x0000)
    return crc.to_bytes(2, byteorder="big")

def crc16_dnp(data: bytes) -> bytes:
    crc = _crc16(data, 0x3D65, 0x0000, True, True, 0xFFFF)
    return crc.to_bytes(2, byteorder="little")

# XOR / 加法
def xor_sum(data: bytes) -> bytes:
    result = 0
    for byte in data:
        result ^= byte
    return bytes([result & 0xFF])

def xor_sum_16(data: bytes) -> bytes:
    result = 0
    for i in range(0, len(data), 2):
        word = data[i] | (data[i + 1] << 8) if i + 1 < len(data) else data[i]
        result ^= word
    return result.to_bytes(2, byteorder="little")

def add_sum_truncate(data: bytes, modulus: int = 256) -> bytes:
    result = sum(data) % modulus
    return bytes([result & 0xFF])

def add_sum_16(data: bytes) -> bytes:
    result = 0
    for i in range(0, len(data), 2):
        word = data[i] | (data[i + 1] << 8) if i + 1 < len(data) else data[i]
        result = (result + word) & 0xFFFF
    return result.to_bytes(2, byteorder="little")

# V9 品牌算法
def checksum_byte15_fb(data: bytes) -> bytes:
    if len(data) < 16:
        return bytes(2)
    result = bytearray(data[14:16]) if len(data) >= 16 else bytearray(2)
    result[-1] = 0xFB
    return bytes(result)

def checksum_sum14(data: bytes) -> bytes:
    if len(data) < 14:
        return bytes(2)
    cs = sum(data[:14]) & 0xFF
    return bytes([cs & 0xFF, 0x00])

def checksum_sum14_zero_byte15(data: bytes) -> bytes:
    if len(data) < 14:
        return bytes(2)
    cs = sum(data[:14]) & 0xFF
    return bytes([cs & 0xFF, 0x00])

def no_checksum(data: bytes) -> bytes:
    return b"\x00\x00"

OPERATOR_REGISTRY: dict[str, Callable[[bytes], bytes]] = {
    "byte15_fb":             checksum_byte15_fb,
    "signature_byte15":      checksum_byte15_fb,
    "sum14":                 checksum_sum14,
    "sum14_zero_byte15":     checksum_sum14_zero_byte15,
    "crc8_maxim":            crc8_maxim,
    "crc8_dallas":           crc8_dallas,
    "crc8_itu":              crc8_itu,
    "crc8_rohc":             crc8_rohc,
    "crc16_modbus":          crc16_modbus,
    "crc16_ccitt":           crc16_ccitt,
    "crc16_xmodem":          crc16_xmodem,
    "crc16_dnp":             crc16_dnp,
    "xor_sum":               xor_sum,
    "xor_sum_16":            xor_sum_16,
    "add_sum_truncate":      add_sum_truncate,
    "add_sum_16":            add_sum_16,
    "none":                  no_checksum,
}

def compute_checksum(algorithm: str, data: bytes) -> bytes:
    fn = OPERATOR_REGISTRY.get(algorithm)
    if fn is None:
        raise ValueError(f"未知校验算法: {algorithm}")
    return fn(data)

def list_algorithms() -> list[str]:
    return sorted(OPERATOR_REGISTRY.keys())
