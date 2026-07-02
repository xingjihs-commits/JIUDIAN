"""
brand_analyzer.py — 从历史卡样本自动提取品牌指纹

职责
====
BrandAnalyzer 输入一批卡 卡数据 十六进制 样本，输出品牌配置字典（brand_profile）。
整个分析过程 **不需要人工干预**，也不需要知道品牌名称。

已集成知识
==========
- 综合成果.md §三「卡数据体系」：16 字节 卡数据 结构定义
- 综合成果.md §八「卡型代码速查表」：type 半字节 与卡型对应
- _crack_analysis.py 的差分分析逻辑
- 3000+ 条已知卡样本的知识模式

工作流程
========
1. Magic 头识别 — 统计样本前 N 字节最常见的模式
2. Payload 布局推断 — 通过差分分析确定字段位置/长度
3. 卡型分类 — 根据 type 半字节 对样本分类
4. 校验和推测 — 尝试多种常用校验算法并验证
5. 锁号格式分析 — 从 卡数据 提取锁号格式
6. 时间编码分析 — 识别 YYMMDD / BCD 等时间格式
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _get_search_paths() -> List[Path]:
    """返回所有可能的搜索路径（_MEIPASS / EXE 目录 / 源码目录 / CWD）。"""
    paths: List[Path] = []
    # PyInstaller 打包后的 _MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass))
    # frozen 模式：EXE 所在目录
    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().parent)
    else:
        # 源码模式：采集器根目录（bridgecore 的父目录）
        paths.append(Path(__file__).resolve().parent.parent)
    paths.append(Path.cwd())
    return paths


def _scan_usb_roots_for_custom_signatures() -> List[Path]:
    """扫描 Windows 所有盘符根目录 / macOS /Volumes/* 下的 custom_signatures.json。

    返回所有找到的路径列表（可能为空）。在 Linux 或无可见盘符环境下返回 []。
    """
    found: List[Path] = []
    try:
        if sys.platform.startswith("win"):
            import string
            for letter in string.ascii_uppercase:
                cand = Path(f"{letter}:") / "custom_signatures.json"
                if cand.is_file():
                    found.append(cand)
        elif sys.platform == "darwin":
            vols = Path("/Volumes")
            if vols.is_dir():
                for v in vols.iterdir():
                    cand = v / "custom_signatures.json"
                    if cand.is_file():
                        found.append(cand)
        else:
            # Linux: 扫描 /media/<user>/* 和 /mnt/* 下挂载点
            for base in (Path("/media") / (os.environ.get("USER") or ""), Path("/mnt")):
                if base.is_dir():
                    for v in base.iterdir():
                        cand = v / "custom_signatures.json"
                        if cand.is_file():
                            found.append(cand)
    except Exception:
        pass
    return found


def _load_signature_json(path: Path) -> Dict[str, Dict[str, Any]]:
    """安全加载签名 JSON 文件，返回 {brand_key: signature_dict}。

    支持两种格式：
    - 新格式（v1）：{"signatures": {brand_key: {...}}}
    - 旧格式（扁平）：{brand_key: {...}}
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning("[BrandAnalyzer] 加载签名文件失败 %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    if "signatures" in data and isinstance(data["signatures"], dict):
        data = data["signatures"]
    # 过滤掉非品牌 key（如 _meta）
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in data.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            out[k] = v
    return out


def load_known_signatures() -> Dict[str, Dict[str, Any]]:
    """加载已知品牌签名库。

    加载顺序（后者覆盖/扩展前者）：
      1. 内置：bridgecore/known_signatures.json（同目录）
      2. PyInstaller 打包后：_MEIPASS/bridgecore/known_signatures.json
      3. 运行目录：当前工作目录下 known_signatures.json
      4. U 盘扩展：所有盘符根目录 / /Volumes/* / /media/* 下的 custom_signatures.json
      5. 预置 profile：采集器根目录/prebuilt_profiles/*.json
      6. 学习成果：采集器根目录/learned_profiles/profiles/*.json

    若所有加载都失败，返回空 dict（_match_known_brand 会自然降级到 Magic/DLL 匹配路径）。
    """
    merged: Dict[str, Dict[str, Any]] = {}

    # 1. 内置 JSON（同目录）
    builtin_path = Path(__file__).resolve().parent / "known_signatures.json"
    if builtin_path.is_file():
        merged.update(_load_signature_json(builtin_path))

    # 2. PyInstaller _MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp_path = Path(meipass) / "bridgecore" / "known_signatures.json"
        if mp_path.is_file() and mp_path != builtin_path:
            merged.update(_load_signature_json(mp_path))

    # 3. 运行目录
    cwd_path = Path.cwd() / "known_signatures.json"
    if cwd_path.is_file():
        merged.update(_load_signature_json(cwd_path))

    # 4. U 盘扩展（用户可现场放 custom_signatures.json 增加新品牌）
    for custom_path in _scan_usb_roots_for_custom_signatures():
        merged.update(_load_signature_json(custom_path))
        logger.info("[BrandAnalyzer] 已合并 U 盘扩展签名: %s", custom_path)

    # 5. 预置 profile（采集器根目录/prebuilt_profiles/*.json）
    # 6. 学习成果（采集器根目录/learned_profiles/profiles/*.json）
    for search_root in _get_search_paths():
        for sub_dir, label in [
            ("prebuilt_profiles", "预置 profile"),
            (os.path.join("learned_profiles", "profiles"), "学习成果"),
        ]:
            profile_dir = search_root / sub_dir
            if not profile_dir.is_dir():
                continue
            for f in profile_dir.glob("*.json"):
                data = _load_signature_json(f)
                if data:
                    merged.update(data)
                    logger.info(
                        "[BrandAnalyzer] 已合并%s: %s/%s (%d 品牌)",
                        label, sub_dir, f.name, len(data),
                    )

    return merged


class BrandAnalyzer:
    """从历史卡样本自动提取品牌指纹，生成 brand_profile。"""

    # 常见 Magic 头候选（按长度优先）
    COMMON_MAGICS = [
        "C92B20B7",
        "AABBCCDD",
        "12345678",
        "00000000",
        "FFFFFFFF",
    ]

    # 已知品牌签名库：通过 品牌→可检测特征 快速匹配
    # [v2 改造] 硬编码字典外置到 known_signatures.json，运行时加载，
    # 支持 U 盘根目录放 custom_signatures.json 扩展（新品牌无需改源码）。
    # 旧调用方通过类属性访问仍可用（首次访问触发惰性加载）。
    _CACHED_SIGNATURES: Optional[Dict[str, Dict[str, Any]]] = None

    @classmethod
    def KNOWN_BRAND_SIGNATURES(cls) -> Dict[str, Dict[str, Any]]:
        """惰性加载并缓存已知品牌签名库（兼容旧调用方写法）。"""
        if cls._CACHED_SIGNATURES is None:
            cls._CACHED_SIGNATURES = load_known_signatures()
        return cls._CACHED_SIGNATURES

    # ================================================================
    # 公开入口
    # ================================================================

    @staticmethod
    def analyze(card_samples: List[str],
                dlsCoID: Optional[int] = None,
                brand_hint: Optional[str] = None,
                dll_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """主入口：输入 卡数据 十六进制 样本，输出品牌配置字典。

        Args:
            card_samples: 卡 卡数据 十六进制 字符串列表（32 字符）。
            dlsCoID: 可选的酒店码，帮助锁定 site 字段。
            brand_hint: 可选的品牌提示（如 "proUSB"），辅助匹配已知签名。
            dll_names: 安装目录下的 DLL 文件名列表，辅助品牌识别。

        Returns:
            品牌配置字典（brand_profile 格式）。

        Raises:
            ValueError: 样本数不足 3 张且非已知品牌时抛出。
        """
        if not card_samples:
            return BrandAnalyzer._empty_profile("无样本")

        analyzer = BrandAnalyzer()

        # 1. Magic 检测
        magic = analyzer._detect_magic(card_samples)
        payload_size = analyzer._detect_payload_size(card_samples)

        # 2. 已知品牌快速匹配（不检查样本数）
        matched_brand = analyzer._match_known_brand(
            magic, payload_size, brand_hint, dll_names or []
        )

        # 3. 如果是已知品牌，直接返回预设 配置(忽略样本数）
        if matched_brand:
            preset = BrandAnalyzer._get_known_profile(matched_brand, dlsCoID)
            if preset:
                preset["source"] = {
                    "samples_count": len(card_samples),
                    "matched_brand": matched_brand,
                }
            return preset

        # 4. 未知品牌要求最低样本数
        if len(card_samples) < 3:
            raise ValueError(
                f"样本不足 (需 >= 3 张，当前 {len(card_samples)} 张)。"
                "少于 3 张无法可靠推断未知品牌的 Magic/checksum/byte15 签名，"
                "请提供更多卡样本后再试。"
            )

        analyzed = {}

        # 5. 未知品牌：自动推断各字段
        layout = analyzer._detect_payload_layout(card_samples)
        analyzed["layout"] = layout

        type_map = analyzer._detect_card_types(card_samples, layout)
        analyzed["card_types"] = type_map

        checksum = analyzer._detect_checksum(card_samples)
        analyzed["checksum"] = checksum

        lock_no_info = analyzer._detect_lock_no_format(card_samples, layout)
        analyzed["lock_no"] = lock_no_info

        date_info = analyzer._detect_date_encoding(card_samples)
        analyzed["date_encoding"] = date_info

        site_info = analyzer._detect_site_code(card_samples, dlsCoID)
        analyzed["site_code"] = site_info

        # 6. 生成最终 profile
        profile = BrandAnalyzer._assemble_profile(
            card_samples, magic, payload_size,
            analyzed, dlsCoID
        )
        return profile

    # ================================================================
    # 内部方法
    # ================================================================

    @staticmethod
    def match_by_protocol_family(protocol_features: Dict[str, Any]) -> Optional[str]:
        """不按品牌名，按协议指纹归家族。

        通过 block_size / checksum_type / data_offset 等特征推断
        协议家族（MIFARE Classic / DESFire / LEGIC / CPU 卡等），
        在品牌名匹配失败时提供降级分类。

        Args:
            protocol_features: 协议特征字典，至少含 block_size 键。

        Returns:
            协议家族名称（如 "MIFARE-Classic-1K"），无法归类时返回 None。
        """
        block_size = protocol_features.get("block_size", 0)
        checksum_type = protocol_features.get("checksum_type", "")

        if block_size == 16:
            if checksum_type in ("xor", "crc8", "none"):
                return "MIFARE-Classic-1K"
            elif checksum_type in ("aes", "des"):
                return "MIFARE-Plus/DESFire"

        if block_size == 8:
            return "LEGIC/HID-iCLASS"

        if block_size in (32, 64):
            return "CPU-Card/PSAM"

        if block_size == 0 and protocol_features.get("data_offset", 0) == 0:
            return "MagStripe-Emulation"

        return None

    def _detect_magic(self, samples: List[str]) -> str:
        """统计所有样本前 N 字节最常见的模式 = Magic。"""
        if not samples:
            return self.COMMON_MAGICS[0]

        # 尝试 4 字节（8 十六进制字符）、3 字节（6 十六进制）、2 字节（4 十六进制）
        for n_bytes in [4, 3, 2]:
            n_chars = n_bytes * 2
            heads = [s[:n_chars] for s in samples if len(s) >= n_chars]
            if not heads:
                continue
            counter = Counter(heads)
            most_common = counter.most_common(1)[0]
            # magic 必须出现超过 80%
            if most_common[1] >= len(heads) * 0.8:
                return most_common[0]

        # 兜底：统计第一位最常见值
        first_chars = [s[:2] for s in samples if len(s) >= 2]
        if first_chars:
            return Counter(first_chars).most_common(1)[0][0] + "????"
        return self.COMMON_MAGICS[0]

    def _detect_payload_size(self, samples: List[str]) -> int:
        """推断 卡数据大小（字节数）。"""
        sizes = Counter(len(s) // 2 for s in samples if s)
        if sizes:
            return sizes.most_common(1)[0][0]
        return 16

    def _match_known_brand(self, magic: str, payload_size: int,
                            brand_hint: Optional[str],
                            dll_names: List[str]) -> Optional[str]:
        """快速匹配已知品牌签名库（从 known_signatures.json 加载）。"""
        sigs = self.KNOWN_BRAND_SIGNATURES()
        # 先按品牌名精准匹配
        if brand_hint:
            for key in sigs:
                if brand_hint.lower() in key.lower():
                    return key

        # 按 DLL特征匹配
        if dll_names:
            dll_lower = [d.lower() for d in dll_names]
            for key, sig in sigs.items():
                sig_dlls = [d.lower() for d in sig.get("dll_names", [])]
                if any(any(sd in dl for sd in sig_dlls) for dl in dll_lower):
                    return key

        # 按 Magic + payload_size 匹配
        for key, sig in sigs.items():
            if sig.get("magic") == magic and sig.get("payload_size") == payload_size:
                return key

        return None

    @staticmethod
    def _get_known_profile(brand_key: str, dlsCoID: Optional[int]) -> Optional[Dict]:
        """根据已知品牌 key 返回标准 品牌配置。"""
        profiles = {
            "proUSB_V9": {
                "brand": "proUSB V9",
                "magic": "C92B20B7",
                "payload_size": 16,
                "adapter_id": "prousb_v9",
                "card_types": {
                    "guest": {"type_byte_high": 0x6, "body_len": 4},
                    "master": {"type_byte_high": 0xB, "body_len": 4, "signature_byte15": "FB"},
                    "building": {"type_byte_high": 0xC, "body_len": 4, "signature_byte15": "FB"},
                    "floor": {"type_byte_high": 0xD, "body_len": 4, "signature_byte15": "FB"},
                    "emergency": {"type_byte_high": 0xA, "body_len": 4},
                    "group": {"type_byte_high": 0x8, "body_len": 4},
                    "auth": {"type_byte_high": 0x0, "body_len": 4, "auth_token_repeat": True},
                    "record": {"type_byte_high": 0x8, "body_len": 4},
                },
                "checksum": {
                    "algorithm": "byte15_fb",
                    "note": "系统卡: byte[15]=0xFB; 客人卡: sum14_zero_byte15",
                },
                "lock_no": {
                    "field": "bytes_2_at_offset_6",
                    "encoding": "hex_be",
                },
                "date_encoding": "legacy_prousb",
                "site_code": {
                    "field": "bytes_2_at_offset_4",
                    "mask": "0x3FFF",
                    "emergency_bit": "0x4000",
                },
                "salt": {"offset": 8, "default": "00"},
                "type_byte": {"offset": 9},
                "body": {"offset": 10, "length": 4},
                "checksum_bytes": {"offset": 14, "length": 2},
                "layout": {
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
                },
                "channel": "dll",
            },
            "baoxunda": {
                "brand": "宝迅达",
                "adapter_id": "baoxunda",
                "payload_size": 16,
                "channel": "serial",
                "serial": {"baudrate": 19200},
            },
            "tongchuangxinjia": {
                "brand": "同创新佳",
                "adapter_id": "tongchuang",
                "payload_size": 16,
                "channel": "serial",
                "serial": {"baudrate": 9600},
            },
            "yadidun": {
                "brand": "雅迪顿",
                "adapter_id": "yadidun",
                "payload_size": 16,
                "channel": "serial",
                "serial": {"baudrate": 38400},
            },
            "rfu_liwei": {
                "brand": "力维 RFU",
                "adapter_id": "rfl_bridge_32",
                "payload_size": 16,
                "channel": "serial",
                "serial": {"baudrate": 9600, "read_command": "AA5500FF00FF0100"},
            },
            "xirong": {
                "brand": "西容",
                "adapter_id": "syron",
                "payload_size": 16,
                "channel": "serial",
                "serial": {"baudrate": 19200},
            },
        }
        return profiles.get(brand_key)

    def _detect_payload_layout(self, samples: List[str]) -> Dict[str, Any]:
        """通过差分分析确定每个字段的位置和含义。"""
        layout: Dict[str, Any] = {}
        if len(samples) < 2:
            return layout

        # 分析方法：看哪些字节在样本之间变化，哪些不变
        n = max(len(s) for s in samples)
        # 按字节位置分析
        for pos in range(0, min(n, 32), 2):  # 每 2 hex 字符 = 1 字节
            vals = [s[pos:pos+2] for s in samples if len(s) > pos + 1]
            unique = set(vals)
            if len(unique) == 1:
                layout[f"byte{pos//2}_constant"] = list(unique)[0]
            else:
                layout[f"byte{pos//2}_variant"] = True
                if len(unique) <= 16:
                    layout[f"byte{pos//2}_unique_vals"] = list(unique)

        # 检测 Magic（前几字节不变）
        for b in range(4):
            key = f"byte{b}_constant"
            if layout.get(key):
                layout.setdefault("magic_bytes", [])
                layout["magic_bytes"].append(b)

        return layout

    def _detect_card_types(self, samples: List[str],
                            layout: Dict[str, Any]) -> Dict[str, Any]:
        """根据 type byte 对样本分类，推断卡型代码。"""
        type_map: Dict[str, Any] = {}
        # 找到 type byte 的可能位置
        # 典型：byte[9] = type
        type_positions = [9]  # 常见位置

        for tp in type_positions:
            if tp * 2 + 2 > max(len(s) for s in samples):
                continue
            nibble_counter: Dict[str, int] = Counter()
            for s in samples:
                if len(s) >= tp * 2 + 2:
                    type_val = int(s[tp*2:tp*2+2], 16)
                    high_nibble = (type_val >> 4) & 0x0F
                    nibble_counter[f"{high_nibble:X}"] = nibble_counter.get(f"{high_nibble:X}", 0) + 1

            if nibble_counter:
                type_map["detected_at_byte"] = tp
                type_map["nibbles"] = dict(nibble_counter.most_common())

                # 尝试映射到已知卡型名称
                known_types = {
                    "0": "auth",
                    "1": "ini",
                    "6": "guest",
                    "8": "record/group",
                    "A": "emergency",
                    "B": "master",
                    "C": "building",
                    "D": "floor",
                }
                type_names = {}
                for nibble, count in nibble_counter.most_common():
                    name = known_types.get(nibble, f"type_{nibble}")
                    type_names[name] = {"type_byte_high": int(nibble, 16), "count": count}
                type_map["type_names"] = type_names

        return type_map

    def _detect_checksum(self, samples: List[str]) -> Dict[str, Any]:
        """尝试多种校验算法匹配。

        校验和通常位于 卡数据 最后 1-2 字节。
        尝试主流算法并验证。
        """
        if not samples:
            return {"algorithm": "none"}

        result: Dict[str, Any] = {"candidates": []}

        # 检测最后 1-2 字节是否类似校验和
        last_byte_vals = [s[-2:] for s in samples if len(s) >= 2]
        last2_vals = [s[-4:-2] for s in samples if len(s) >= 4] if len(samples[0]) >= 4 else []

        unique_last = len(set(last_byte_vals))
        unique_last2 = len(set(last2_vals))

        result["last_byte_unique"] = unique_last
        result["last2_byte_unique"] = unique_last2

        # 检查 byte[15] 是否恒为 FB（系统卡签名）
        byte15_vals = [s[30:32] for s in samples if len(s) >= 32]
        if byte15_vals and len(set(byte15_vals)) == 1:
            val = byte15_vals[0]
            result["candidates"].append({
                "algorithm": "byte15_fb" if val == "FB" else f"byte15_constant_{val}",
                "confidence": "high",
                "note": f"byte[15] 恒定 {val}，可能为系统卡固定签名",
            })

        # 检查 sum(payload[:14]) & 0xFF 匹配率
        total, match = 0, 0
        for s in samples:
            if len(s) < 30:
                continue
            total += 1
            raw = bytes.fromhex(s[:28])
            cs = sum(raw) & 0xFF
            actual = int(s[28:30], 16)
            if cs == actual:
                match += 1

        if total > 0 and match / total > 0.5:
            result["candidates"].append({
                "algorithm": "sum14",
                "confidence": "high" if match == total else "medium",
                "match_rate": f"{match}/{total}",
                "note": "byte[14] = sum(payload[0..13]) & 0xFF",
            })

        # 检查 sum14 + byte[15]=00（客人卡常见）
        total2, match2 = 0, 0
        for s in samples:
            if len(s) < 32:
                continue
            total2 += 1
            raw = bytes.fromhex(s[:28])
            cs = sum(raw) & 0xFF
            actual_cs = int(s[28:30], 16)
            actual_b15 = s[30:32]
            if cs == actual_cs and actual_b15 == "00":
                match2 += 1

        if total2 > 0 and match2 / total2 > 0.3:
            result["candidates"].append({
                "algorithm": "sum14_zero_byte15",
                "confidence": "medium",
                "match_rate": f"{match2}/{total2}",
                "note": "byte[14] = sum[0..13] & 0xFF, byte[15] = 00",
            })

        # 选择最佳候选
        if result["candidates"]:
            # 优先高置信度
            high = [c for c in result["candidates"] if c.get("confidence") == "high"]
            if high:
                result["algorithm"] = high[0]["algorithm"]
            else:
                result["algorithm"] = result["candidates"][0]["algorithm"]
        else:
            result["algorithm"] = "none"

        return result

    def _detect_lock_no_format(self, samples: List[str],
                                layout: Dict[str, Any]) -> Dict[str, Any]:
        """分析锁号格式。"""
        if not samples:
            return {"field": "unknown", "encoding": "unknown"}

        # 常见锁号位置：byte[6..7] 或 byte[4..7]
        # 分析 byte[6..7]（4 十六进制字符）的变化程度
        lock_positions = [(6, 2), (4, 4)]

        for offset, length in lock_positions:
            start = offset * 2
            end = start + length * 2
            vals = [s[start:end] for s in samples if len(s) >= end]
            if not vals:
                continue
            unique = set(vals)
            # 锁号通常有较多不同的值
            if len(unique) >= 4:
                return {
                    "field": f"bytes_{length}_at_offset_{offset}",
                    "encoding": "hex_be",
                    "unique_count": len(unique),
                    "example_values": list(unique)[:5],
                }

        return {"field": "unknown", "encoding": "unknown"}

    def _detect_date_encoding(self, samples: List[str]) -> str:
        """检测日期编码格式。"""
        if not samples or len(samples) < 3:
            return "unknown"

        # 用于分析 卡数据的 byte[10..15] 区域
        # 先看是否有明显的 YYMMDD 模式
        # 典型：byte[12] = YY, byte[13] = MMDD
        body_parts = [s[20:28] for s in samples if len(s) >= 28]  # byte[10..13]
        if not body_parts:
            return "unknown"

        # 看是否前 4 个 十六进制字符像 BCD 年
        year_patterns = [p[:2] for p in body_parts]
        try:
            years = [int(y, 16) for y in year_patterns]
            # BCD 年通常在 00-99 之间
            if all(0 <= y <= 99 for y in years):
                return "legacy_prousb"
        except ValueError:
            pass

        return "unknown"

    def _detect_site_code(self, samples: List[str],
                           dlsCoID: Optional[int]) -> Dict[str, Any]:
        """检测酒店码字段。"""
        if not samples or len(samples) < 1:
            return {"field": "unknown"}

        # 分析 byte[4..5]（酒店码常见位置）
        site_vals = [s[8:12] for s in samples if len(s) >= 12]
        if not site_vals:
            return {"field": "unknown"}

        unique = set(site_vals)
        # 通常所有样本的 site 码相同
        if len(unique) <= 3:  # 含紧急卡可能有高位变化
            site_hex = list(unique)[0]
            try:
                site_int = int(site_hex, 16)
            except ValueError:
                site_int = 0
            return {
                "field": "bytes_2_at_offset_4",
                "detected_value": site_hex,
                "mask": "0x3FFF",
                "hint_dlsCoID": site_int if not dlsCoID else dlsCoID,
            }

        return {"field": "bytes_2_at_offset_4", "unique_count": len(unique)}

    @staticmethod
    def _assemble_profile(samples: List[str], magic: str,
                           payload_size: int,
                           analyzed: Dict[str, Any],
                           dlsCoID: Optional[int]) -> Dict[str, Any]:
        """组装最终 品牌配置 字典。"""
        profile: Dict[str, Any] = {
            "brand": analyzed.get("matched_brand") or "Unknown Brand (Auto-Detected)",
            "magic": magic,
            "payload_size": payload_size,
            "card_types": analyzed.get("card_types", {}),
            "checksum": analyzed.get("checksum", {"algorithm": "none"}),
            "lock_no": analyzed.get("lock_no", {"field": "unknown"}),
            "date_encoding": analyzed.get("date_encoding", "unknown"),
            "site_code": analyzed.get("site_code", {"field": "unknown"}),
            "source": {
                "samples_count": len(samples),
                "matched_brand": analyzed.get("matched_brand"),
            },
            "auto_detected": True,
        }

        # 添加已知布局（若有）
        if "layout" in analyzed:
            profile["layout"] = analyzed["layout"]

        # 如果有 dlsCoID 且有 site 检测，修正 site_code
        if dlsCoID and "site_code" in profile:
            profile["site_code"]["hint_dlsCoID"] = dlsCoID

        return profile

    @staticmethod
    def _empty_profile(reason: str) -> Dict[str, Any]:
        return {
            "brand": "Unknown",
            "magic": "unknown",
            "payload_size": 16,
            "card_types": {},
            "checksum": {"algorithm": "none"},
            "lock_no": {"field": "unknown"},
            "date_encoding": "unknown",
            "site_code": {"field": "unknown"},
            "source": {"empty": True, "reason": reason},
            "auto_detected": True,
        }

    @staticmethod
    def detect_system_card_checksum(samples: List[str]) -> Dict[str, Any]:
        """专门分析系统卡（Master/Building/Floor）的校验和。"""
        result: Dict[str, Any] = {}
        if not samples:
            return result

        # 检查 byte[15] 是否恒定
        b15 = [s[30:32] for s in samples if len(s) >= 32]
        if b15 and len(set(b15)) == 1:
            result["byte15"] = b15[0]
            result["signature"] = f"byte[15] = {b15[0]}"
        else:
            result["byte15"] = "variant"

        # 检查 byte[14]
        b14 = [s[28:30] for s in samples if len(s) >= 30]
        if b14:
            unique_b14 = len(set(b14))
            result["byte14_unique"] = unique_b14
            if unique_b14 == len(b14):
                result["byte14_note"] = "每卡不同，可能是校验和"

        return result

    @staticmethod
    def extract_card_samples_from_mdb(mdb_path: str,
                                       table: str = "CardInfo",
                                       column: str = "CardStr") -> List[str]:
        """从 MDB 数据库提取卡 卡数据 样本。

        兼容多种常见的 MDB 表结构：
        - CardInfo.CardStr
        - CardInfo.CardData
        - CardRecord.CardHex
        """
        import os

        path_str = str(mdb_path)
        if not os.path.isfile(path_str):
            return []

        samples: List[str] = []

        try:
            import pyodbc
        except ImportError:
            try:
                import win32com.client  # type: ignore
                return BrandAnalyzer._extract_via_adodb(path_str, table, column)
            except ImportError:
                logger.warning("需要 pyodbc 或 pywin32 来读取 MDB")
                return []

        conn_str = (
            r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
            f"DBQ={path_str};"
        )
        try:
            conn = pyodbc.connect(conn_str, timeout=3)
            cursor = conn.cursor()
        except Exception as exc:
            logger.debug("MDB 连接失败: %s", exc)
            return BrandAnalyzer._extract_via_adodb(path_str, table, column)

        # 探测可用表和列
        possible_queries = [
            f"SELECT TOP 200 {column} FROM {table} WHERE {column} IS NOT NULL",
            f"SELECT TOP 200 CardData FROM CardInfo WHERE CardData IS NOT NULL",
            f"SELECT TOP 200 CardHex FROM CardRecord WHERE CardHex IS NOT NULL",
            f"SELECT TOP 200 CardStr FROM CardInfo WHERE CardStr IS NOT NULL",
            f"SELECT TOP 200 card_data FROM card_info WHERE card_data IS NOT NULL",
        ]

        for query in possible_queries:
            try:
                cursor.execute(query)
                rows = cursor.fetchall()
                if rows:
                    for row in rows:
                        val = str(row[0] or "").strip().upper()
                        # 过滤有效 十六进制 样本（至少 16 字符 = 8 字节）
                        if len(val) >= 16 and all(c in "0123456789ABCDEF" for c in val):
                            samples.append(val)
                    break
            except Exception:
                continue

        try:
            conn.close()
        except Exception:
            pass

        return samples

    @staticmethod
    def _extract_via_adodb(mdb_path: str, table: str = "CardInfo",
                            column: str = "CardStr") -> List[str]:
        """通过 ADODB COM 方式读取 MDB（pyodbc 失败时的备选）。"""
        samples: List[str] = []
        try:
            import win32com.client  # type: ignore
            conn = win32com.client.Dispatch("ADODB.Connection")
            conn_str = (
                r"Provider=Microsoft.Jet.OLEDB.4.0;"
                f"Data Source={mdb_path};"
            )
            conn.Open(conn_str)
            rs = win32com.client.Dispatch("ADODB.Recordset")

            possible_queries = [
                f"SELECT TOP 200 {column} FROM {table}",
                "SELECT TOP 200 CardData FROM CardInfo",
                "SELECT TOP 200 CardHex FROM CardRecord",
                "SELECT TOP 200 CardStr FROM CardInfo",
            ]

            for query in possible_queries:
                try:
                    rs.Open(query, conn)
                    if not rs.EOF:
                        while not rs.EOF:
                            val = str(rs.Fields(0).Value or "").strip().upper()
                            if len(val) >= 16 and all(c in "0123456789ABCDEF" for c in val):
                                samples.append(val)
                            rs.MoveNext()
                            if len(samples) >= 200:
                                break
                        rs.Close()
                        if samples:
                            break
                except Exception:
                    pass

            conn.Close()
        except Exception:
            pass

        return samples
