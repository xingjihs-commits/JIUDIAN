"""
payload_factory.py — 数据驱动的 16 字节卡 卡数据 构造器

职责
====
BrandPayloadFactory 根据 brand_profile 的描述生成符合厂家规范的卡 卡数据。
不写任何品牌特定的硬编码逻辑——所有差异来自 品牌配置 配置。

支持场景
========
1. 已知品牌（有 品牌配置）：直接按 品牌配置 的字段描述构造 payload
2. 未知品牌（品牌分析器自动推断）：也能构造基础的可工作载荷

profile 格式
=============
{
"brand": "proUSB V9",
"magic": "C92B20B7",
"payload_size": 16,
"card_types": {
"guest": { "type_byte_high": 0x6, "body_len": 4 },
"master": { "type_byte_high": 0xB, "body_len": 4, "signature_byte15": "FB" },
"building": { "type_byte_high": 0xC, "body_len": 4, "signature_byte15": "FB" },
"floor": { "type_byte_high": 0xD, "body_len": 4, "signature_byte15": "FB" },
"emergency": { "type_byte_high": 0xA, "body_len": 4 },
"group": { "type_byte_high": 0x8, "body_len": 4 },
"auth": { "type_byte_high": 0x0, "body_len": 4, "auth_token_repeat": true },
},
"checksum": {
"algorithm": "byte15_fb",
"byte14_algo": "unknown",
},
"date_encoding": {
"algorithm": "legacy_prousb",
"begin_time_algo": "prousb"
},
"lock_no": {
"field": "bytes_2_at_offset_6",
"encoding": "hex_be",
},
"site_code": {
"field": "bytes_2_at_offset_4",
"mask": "0x3FFF",
"emergency_bit": "0x4000",
},
"salt": {"offset": 8, "default": "00"},
"type_byte": {"offset": 9},
"body": {"offset": 10, "length": 4},
"checksum_bytes": {"offset": 14, "length": 2},
}
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"


def _now() -> _dt.datetime:
    return _dt.datetime.now()


# ====================================================================
# 常用日期编码/转换工具（品牌不可知）
# ====================================================================


def _normalize_date(value: str) -> str:
    """保证返回 YYMMDDHHMM 格式（10 字符）。"""
    s = (value or "").strip()
    if len(s) >= 10:
        return s[:10]
    now = _now().strftime("%y%m%d%H%M")
    if not s:
        return now
    return (s + now[len(s):])[:10]


def _legacy_date_marker_prousb(date_value: str) -> str:
    """proUSB 系统卡用 3 字节时间编码。

    CardLock.exe 的 YYMMDDHHMM -> 3-byte 编码（源码反推）：
    byte0 = ((year-9) & 0x0F) << 4 + month
    byte1 = day << 3 + hour // 4
    byte2 = (hour & 3) << 6 + minute
    """
    s = _normalize_date(date_value)
    yy = int(s[0:2])
    mm = int(s[2:4])
    dd = int(s[4:6])
    hh = int(s[6:8])
    minute = int(s[8:10])
    return f"{(((yy - 9) & 0x0F) << 4) + mm:02X}{(dd << 3) + (hh // 4):02X}{((hh & 0x03) << 6) + minute:02X}"


def _legacy_begin_time_marker_prousb(date_value: str) -> str:
    """proUSB 系统卡 1 字节开始时间编码（用于 byte[8] 后 1 字节）。"""
    s = _normalize_date(date_value)
    hh = int(s[6:8])
    minute = int(s[8:10])
    return f"{(hh << 3) + (minute // 10):02X}"


def _legacy_guest_date_encoding(b_date: str, e_date: str) -> str:
    """proUSB 客人卡时间戳编码到 byte[12:14]。

    byte[12] = YY (BCD)
    byte[13] = MMDD (BCD)
    当前实现参考锁软件实际行为。
    """
    b_norm = _normalize_date(b_date)
    yy = int(b_norm[0:2])
    mm = int(b_norm[2:4])
    dd = int(b_norm[4:6])
    bcd_year = ((yy // 10) << 4) + (yy % 10)
    bcd_md_single = ((mm % 10) << 4) + dd
    return f"{bcd_year:02X}{bcd_md_single:02X}"


def _full_day_range(b_date: str, e_date: str) -> tuple[str, str]:
    """系统卡用全天范围：00:00 -> 23:59。"""
    now = _now()
    b_src = _normalize_date(b_date or now.strftime("%y%m%d%H%M"))
    e_src = _normalize_date(e_date or (now + _dt.timedelta(days=365)).strftime("%y%m%d%H%M"))
    return b_src[:6] + "0000", e_src[:6] + "2359"


# 日期编码算法注册表（Profile 驱动）
_DATE_ENCODING_REGISTRY: Dict[str, Any] = {
    "legacy_prousb": _legacy_date_marker_prousb,
    "legacy_begin_time_prousb": _legacy_begin_time_marker_prousb,
}

_BEGIN_TIME_ENCODING_REGISTRY: Dict[str, Any] = {
    "prousb": _legacy_begin_time_marker_prousb,
}


# ====================================================================
# 校验和工具
# ====================================================================


def _checksum_none(payload_hex: str) -> str:
    """不修改校验和位。"""
    return payload_hex


def _checksum_byte15_fb(payload_hex: str) -> str:
    """系统卡：byte[15] = 0xFB，byte[14] 保持不变。"""
    if len(payload_hex) < 32:
        return payload_hex
    return payload_hex[:30] + "FB"


def _checksum_sum14(payload_hex: str) -> str:
    """byte[14] = sum(bytes[0..13]) & 0xFF（GuestCard 当前实现）。"""
    if len(payload_hex) < 30:
        return payload_hex
    raw = bytes.fromhex(payload_hex[:28])
    cs = sum(raw) & 0xFF
    return payload_hex[:28] + f"{cs:02X}" + payload_hex[30:32]


def _checksum_sum14_with_zero_byte15(payload_hex: str) -> str:
    """客人卡：byte[14] = sum[0..13] & 0xFF, byte[15] = 0x00。"""
    if len(payload_hex) < 32:
        return payload_hex
    raw = bytes.fromhex(payload_hex[:28])
    cs = sum(raw) & 0xFF
    return payload_hex[:28] + f"{cs:02X}00"


_CHECKSUM_FUNCS: Dict[str, Any] = {
    "none": _checksum_none,
    "byte15_fb": _checksum_byte15_fb,
    "sum14": _checksum_sum14,
    "sum14_zero_byte15": _checksum_sum14_with_zero_byte15,
}


def _get_checksum_func(algo: str) -> Any:
    return _CHECKSUM_FUNCS.get(algo, _checksum_none)


# ====================================================================
# 锁号编码工具
# ====================================================================


def _encode_lock_no_hex_be(lock_no: str) -> str:
    """默认 4 hex 字符 = 2 字节，大端。"""
    s = (lock_no or "").strip().upper()
    if len(s) >= 4 and all(c in "0123456789ABCDEF" for c in s[:4]):
        return s[:4]
    if len(s) >= 8 and all(c in "0123456789ABCDEF" for c in s[:8]):
        return s[4:8]
    return "0001"


def _encode_lock_no_hex_le(lock_no: str) -> str:
    """4 hex 字符，小端（字节交换）。"""
    s = _encode_lock_no_hex_be(lock_no)
    if len(s) == 4:
        return s[2:4] + s[0:2]
    return s


def _encode_lock_no_bcd_3byte(lock_no: str) -> str:
    """3 字节 BCD 编码：锁号转为 6 位 BCD。"""
    s = (lock_no or "").strip()
    # 取纯数字，最多 6 位
    digits = "".join(c for c in s if c.isdigit())[:6].zfill(6)
    result = ""
    for i in range(0, 6, 2):
        d1 = int(digits[i])
        d2 = int(digits[i + 1])
        result += f"{(d1 << 4) + d2:02X}"
    return result


def _encode_lock_no_ascii(lock_no: str) -> str:
    """ASCII 编码：原样转为 hex。"""
    s = (lock_no or "").strip()
    return s.encode().hex()[:8]


_LOCK_NO_ENCODING_REGISTRY: Dict[str, Any] = {
    "hex_be": _encode_lock_no_hex_be,
    "hex_le": _encode_lock_no_hex_le,
    "bcd_3byte": _encode_lock_no_bcd_3byte,
    "ascii": _encode_lock_no_ascii,
}


# ====================================================================
# BrandPayloadFactory
# ====================================================================


class BrandPayloadFactory:
    """根据品牌配置（brand_profile）生成 16 字节 卡数据 十六进制。

    用法：
    factory = BrandPayloadFactory(profile, dlsCoID=2826423)
    payload = factory.build("guest", lock_no="80010301",
                           b_date="2605221200", e_date="2605231200")
    """

    def __init__(self, profile: dict, dlsCoID: int = 0):
        self._profile = profile
        self._magic = profile.get("magic", "C92B20B7")
        self._payload_size = profile.get("payload_size", 16)
        self._card_types = profile.get("card_types", {})
        self._dlsCoID = dlsCoID

        chk = profile.get("checksum", {})
        algo_name = chk.get("algorithm", "none")
        self._checksum_fn = _get_checksum_func(algo_name)

        # 日期编码：从 profile.date_encoding 读取算法名
        de = profile.get("date_encoding", {})
        if isinstance(de, str):
            self._date_encoding_algo = de
            self._begin_time_algo = ""
        else:
            self._date_encoding_algo = de.get("algorithm", "")
            self._begin_time_algo = de.get("begin_time_algo", "")
        self._date_encoder = _DATE_ENCODING_REGISTRY.get(self._date_encoding_algo, lambda v: v[:6])
        self._begin_time_encoder = _BEGIN_TIME_ENCODING_REGISTRY.get(self._begin_time_algo, self._encode_begin_time_fallback)

        self._system_card_fb = profile.get("system_card_fb", True)

        # 站点码配置
        sc = profile.get("site_code", {})
        try:
            self._site_mask = int(sc.get("mask", "0x3FFF"), 16)
        except (ValueError, TypeError):
            self._site_mask = 0x3FFF
        try:
            self._emergency_bit = int(sc.get("emergency_bit", "0x4000"), 16)
        except (ValueError, TypeError):
            self._emergency_bit = 0x4000

        # Salt 配置
        salt_cfg = profile.get("salt", {})
        self._salt_default = salt_cfg.get("default", "00") or "00"

        # 锁号编码方式
        ln_cfg = profile.get("lock_no", {})
        self._lock_no_encoding = ln_cfg.get("encoding", "hex_be")
        self._lock_no_encoder = _LOCK_NO_ENCODING_REGISTRY.get(self._lock_no_encoding, _encode_lock_no_hex_be)

        # 字段位置配置
        self._lo = {
            "site_offset": 4,
            "site_len": 2,
            "lock_no_offset": 6,
            "lock_no_len": 2,
            "salt_offset": 8,
            "type_offset": 9,
            "body_offset": 10,
            "body_len": 4,
            "chk_offset": 14,
            "chk_len": 2,
        }
        self._lo.update(profile.get("layout", {}))

    # ────────────── 公开方法 ──────────────

    def build(self, card_type: str, **fields) -> str:
        """生成指定卡型的 卡数据 hex（32 字符）。

        Args:
            card_type: 卡类型 key，如 "guest", "master", "building" 等。
            **fields: 该卡型需要的自定义字段（lock_no, b_date, e_date, card_no 等）。

        Returns:
            32 字符 十六进制 字符串（16 字节 卡数据）。

        Raises:
            ValueError: 不支持的卡型或字段缺失。
        """
        builder = self._get_builder(card_type)
        payload = builder(**fields)
        if len(payload) != self._payload_size * 2:
            raise ValueError(
                f"{card_type} payload 长度异常: {len(payload)} (预期 {self._payload_size * 2})"
            )
        return payload

    def _get_builder(self, card_type: str) -> Any:
        type_info = self._card_types.get(card_type, {})
        type_high = type_info.get("type_byte_high")
        method_name = f"_build_{card_type}"
        builder = getattr(self, method_name, None)
        if builder is not None:
            return builder
        # 泛用回退：任何卡型都能用原始字节构造
        return lambda **kw: self._build_raw(card_type, type_high, **kw)

    # ────────────── 核心构造助手 ──────────────

    def _encode_type_byte(self, type_high: int, seq: int = 0) -> int:
        """type byte = (high nibble << 4) | (low nibble = seq & 0x0F)。"""
        return ((type_high & 0x0F) << 4) | (seq & 0x0F)

    def _encode_date(self, date_value: str) -> str:
        """按 profile 配置的日期编码算法编码日期。"""
        if self._date_encoder is not None:
            return self._date_encoder(date_value)
        return date_value[:6]  # 降级：YYMMDD

    def _encode_begin_time(self, date_value: str) -> str:
        """按 profile 配置编码 begin_time。"""
        return self._begin_time_encoder(date_value)

    def _encode_begin_time_fallback(self, date_value: str) -> str:
        """中性回退：用小时编码 00-23 → 0x00-0x17"""
        s = _normalize_date(date_value)
        hh = int(s[6:8])
        return f"{hh:02X}"

    def _preamble(self, lock_no: str, type_high: int, seq: int = 0,
                  salt: str = "") -> str:
        """生成 卡数据前 10 字节（特征码 + 站点 + 锁号 + 随机数 + 类型）。"""
        site_code = (int(self._dlsCoID) & self._site_mask)
        lock_part = self._encode_lock_no(lock_no) if lock_no else "7FFF"
        type_byte = self._encode_type_byte(type_high, seq)
        salt_val = salt if salt else self._salt_default
        return (
            f"{self._magic}"
            f"{site_code:04X}"
            f"{lock_part}"
            f"{salt_val}"
            f"{type_byte:02X}"
        )

    def _encode_lock_no(self, lock_no: str) -> str:
        """把锁号字符串编码为 profile 指定的格式。"""
        return self._lock_no_encoder(lock_no)

    def _build_common_body(self, preamble: str, body_hex: str) -> str:
        """拼装 preamble + body（12 字节），然后加校验和得到完整 16 字节。"""
        partial = preamble + body_hex
        hex_16 = partial + "00" * (self._payload_size * 2 - len(partial))
        hex_16 = hex_16[:self._payload_size * 2]
        return self._apply_checksum(hex_16)

    def _apply_checksum(self, payload_hex: str) -> str:
        return self._checksum_fn(payload_hex)

    def _get_seq(self, **fields: Any) -> int:
        """序列号：来自 fields.get('seq')，未提供则返回 0。"""
        return int(fields.get("seq", 0))

    # ────────────── 各卡型构造 ──────────────

    def _build_guest(self, **fields: Any) -> str:
        """客人卡 — 按 配置的 card_types.guest.type_byte_high 构造。"""
        return self._build_raw("guest", **fields)

    def _build_raw(self, card_type: str, type_high: Optional[int] = None,
                   **fields: Any) -> str:
        """通用原始 卡数据 构造，适用于所有系统卡类型。

        支持的字段：
        lock_no: 锁号
        b_date, e_date: 起止日期
        card_no: 卡号
        llock: 是否反锁
        extra_hex: 自定义 body hex
        seq: 序列号 (0-15)
        emergency_site_bit: 应急卡设置 site 高位
        """
        if type_high is None:
            type_info = self._card_types.get(card_type, {})
            type_high = type_info.get("type_byte_high", 0)

        seq = self._get_seq(**fields)
        lock_no = fields.get("lock_no", "")
        b_date = fields.get("b_date", "")
        e_date = fields.get("e_date", "")
        card_no = int(fields.get("card_no", 1))
        llock = bool(fields.get("llock", False))
        extra_hex = (fields.get("extra_hex") or "").upper()
        emergency_bit = bool(fields.get("emergency_site_bit", False))

        b_norm, e_norm = _full_day_range(b_date, e_date)

        # 构造字节体：起始标记 + 额外十六进制 + 卡号
        begin_time = self._encode_begin_time(b_norm)
        body = f"{begin_time}{extra_hex}{int(card_no) & 0xFF:02X}"

        # type byte
        type_code = self._encode_type_byte(type_high, seq)

        # site code（含紧急卡高位）
        site_val = int(self._dlsCoID) & self._site_mask
        if emergency_bit:
            site_val += self._emergency_bit

        lock_part = self._encode_lock_no(lock_no) if lock_no else ("FF00" if llock else "7F00")

        # 日期标记
        date_marker = self._encode_date(e_norm)

        payload = (
            f"{self._magic}"
            f"{site_val:04X}"
            f"{lock_part}"
            f"{self._salt_default}"
            f"{type_code:02X}"
            f"{body}"
            f"{date_marker}"
        )

        # 确保 32 字符，不足补 "00"
        if len(payload) < 32:
            payload = payload.ljust(32, "0")
        else:
            payload = payload[:32]

        return self._apply_checksum(payload)

    def _build_master(self, **fields: Any) -> str:
        return self._build_raw("master", type_high=0xB, llock=True, **fields)

    def _build_building(self, **fields: Any) -> str:
        return self._build_raw("building", type_high=0xC, **fields)

    def _build_floor(self, **fields: Any) -> str:
        return self._build_raw("floor", type_high=0xD, **fields)

    def _build_emergency(self, **fields: Any) -> str:
        return self._build_raw("emergency", type_high=0xA, emergency_site_bit=True, **fields)

    def _build_group(self, **fields: Any) -> str:
        return self._build_raw("group", type_high=0x8, **fields)

    def _build_auth(self, **fields: Any) -> str:
        """授权卡：锁号固定，令牌重复使用。"""
        seq = self._get_seq(**fields)
        site_val = int(self._dlsCoID) & 0x3FFF
        type_code = self._encode_type_byte(0x0, seq)
        token_hex = fields.get("token_hex", "000000")
        body = token_hex + token_hex
        payload = (
            f"{self._magic}"
            f"{site_val:04X}"
            f"809C"
            f"00"
            f"{type_code:02X}"
            f"{body}"
        )
        return payload[:32]

    def _build_ini(self, **fields: Any) -> str:
        return self._build_raw("ini", type_high=0x1, **fields)

    def _build_record(self, **fields: Any) -> str:
        return self._build_raw("record", type_high=0x8, **fields)


# ====================================================================
# BrandProfileLoader — 品牌配置管理器
# ====================================================================


class BrandProfileLoader:
    """从 profiles/ 目录加载品牌配置 JSON。"""

    @staticmethod
    def list_known_brands() -> List[Dict[str, Any]]:
        """列出所有已知品牌的简略信息。"""
        results: List[Dict[str, Any]] = []
        if not _PROFILES_DIR.is_dir():
            return results
        for fpath in sorted(_PROFILES_DIR.glob("*.json")):
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                results.append({
                    "brand": data.get("brand", fpath.stem),
                    "file": fpath.name,
                    "adapter_id": data.get("adapter_id", ""),
                })
            except Exception as exc:
                logger.warning("加载 profile %s 失败: %s", fpath, exc)
        return results

    @staticmethod
    def load(brand_or_file: str) -> Optional[Dict[str, Any]]:
        """按品牌名或文件名加载 品牌配置。"""
        if not _PROFILES_DIR.is_dir():
            return None

        # 1. 精确文件名
        fpath = _PROFILES_DIR / brand_or_file
        if not fpath.suffix:
            fpath = fpath.with_suffix(".json")
        if fpath.is_file():
            return json.loads(fpath.read_text(encoding="utf-8"))

        # 2. 按品牌名匹配
        for f in _PROFILES_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("brand", "").lower() == brand_or_file.lower():
                    return data
                if data.get("adapter_id", "").lower() == brand_or_file.lower():
                    return data
            except Exception:
                continue

        # 3. 文件名前缀模糊匹配
        for f in _PROFILES_DIR.glob(f"{brand_or_file}*.json"):
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue

        return None

    @staticmethod
    def save(profile: dict) -> bool:
        """将 品牌配置 保存到 profiles/ 目录。"""
        _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        brand = profile.get("brand", "unknown").replace(" ", "_").lower()
        fpath = _PROFILES_DIR / f"{brand}.json"
        try:
            fpath.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except Exception as exc:
            logger.error("保存 profile %s 失败: %s", fpath, exc)
            return False
