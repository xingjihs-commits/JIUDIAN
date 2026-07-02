"""
config_extractor.py — 从检测结果提取品牌配置

职责
====
BrandConfigExtractor 将 USB 扫描检测结果转化为 GenericLockAdapter 可用的
payload 品牌配置。自动串联 profiles.json（检测特征）→ profiles/*.json（卡数据）→ 发卡。

工作流程
========
1. scanner 发现品牌（通过文件名/指纹）
2. config_extractor 匹配到 卡数据 profile
3. 从 INI/MDB 提取 dlsCoID 等运行期配置
4. 返回完整配置交给 GenericLockAdapter
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..profile.brand_analyzer import BrandAnalyzer
from ..profile.payload_factory import BrandProfileLoader

logger = logging.getLogger(__name__)

_PROFILES_DIR = Path(__file__).resolve().parent.parent / "profile" / "profiles"

# USB_LOCK_PROFILES 品牌 ID → lock_adapters 品牌配置 adapter标识 映射
# 当两个系统的品牌名不同时，需要手动bridge
_BRAND_ID_MAP: Dict[str, str] = {
    "anjubao": None,  # 暂无 payload profile
    "kaidisite": None,
    "tcl": None,
    "onity": None,
    "vingcard": None,
    "dormakaba": None,
    "adel": None,
    "beian": None,
    "dessmann": None,
    "salto": None,
    "bitech": None,
    "hune": None,
    "ygs": None,
    "dinggu": None,
    "orbita_cn": None,
    "level": None,
    "tengo": None,
    "prousb_cardlock": "prousb_v9",
    "jweilink": None,
    "generic_mifare": None,
    # 已知adapter品牌
    "aidier_9200": "aidier_9200",
    "aidier_3200": "aidier_3200",
    "aidier_maindll": "aidier_maindll",
    "bida_ib": "bida_ib",
    "level_lock": "level_lock",
    "syron": "syron",
    "yadidun": "yadidun",
    "tongchuang": "tongchuang",
    "baoxunda": "baoxunda",
}


class BrandConfigExtractor:
    """品牌配置提取器：检测结果 → GenericLockAdapter 可用配置。"""

    @staticmethod
    def extract(brand_id: str, install_dir: Optional[str] = None,
                mdb_path: Optional[str] = None,
                ini_path: Optional[str] = None) -> Dict[str, Any]:
        """主入口：从检测结果提取完整品牌配置。

        Args:
            brand_id: USB_LOCK_PROFILES 中的品牌 ID
            install_dir: 门锁系统安装目录（可选，用于定位 INI/DLL）
            mdb_path: MDB 数据库路径（可选，用于提取卡样本）
            ini_path: System.ini 路径（可选，直接指定）

        Returns:
            dict: 包含 品牌配置 + runtime_config 的完整配置。
            profile=None 表示无可用 卡数据 品牌配置。
        """
        result: Dict[str, Any] = {
            "brand_id": brand_id,
            "profile": None,
            "runtime": {},
            "status": "unsupported",
            "message": "",
        }

        # 1. 尝试匹配已知 卡数据结构配置
        profile = BrandConfigExtractor._match_profile(brand_id)
        if profile:
            result["profile"] = profile
            result["status"] = "known"
            result["message"] = "已匹配已知品牌配置"

            # 2. 提取运行时配置（dlsCoID、波特率等）
            runtime = BrandConfigExtractor._extract_runtime(
                brand_id, install_dir, ini_path
            )
            result["runtime"] = runtime

            # 3. 没有已知 品牌配置 时，尝试从 MDB 自动生成
            if profile is None and mdb_path and os.path.isfile(mdb_path):
                auto_profile = BrandConfigExtractor._auto_generate(mdb_path)
                if auto_profile:
                    result["profile"] = auto_profile
                    result["status"] = "auto_generated"
                    result["message"] = "从 MDB 卡样本自动生成配置"

            # 4. 最后状态
            if result["profile"] is None:
                result["message"] = (
                    "无可用 payload profile。\n"
                    "该品牌可通过串口嗅探（第②种方式）提取配置。"
                )

        return result

    @classmethod
    def _match_profile(cls, brand_id: str) -> Optional[Dict[str, Any]]:
        """按 brand_id 匹配已知 卡数据结构配置。"""
        # 1. 直接通过 adapter标识 加载
        adapter_id = _BRAND_ID_MAP.get(brand_id)
        if adapter_id:
            profile = BrandProfileLoader.load(adapter_id)
            if profile:
                return profile

        # 2. 按 brand_id 本身加载（兼容器）
        profile = BrandProfileLoader.load(brand_id)
        if profile:
            return profile

        # 3. 遍历所有 品牌配置，按 brand 名字段匹配
        for fpath in sorted(_PROFILES_DIR.glob("*.json")):
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                if data.get("brand", "").lower().replace(" ", "_") == brand_id.lower():
                    return data
            except Exception:
                continue

        return None

    @classmethod
    def _extract_runtime(cls, brand_id: str,
                         install_dir: Optional[str],
                         ini_path: Optional[str]) -> Dict[str, Any]:
        """从 System.ini / MDB 提取运行时参数。"""
        runtime: Dict[str, Any] = {
            "dlsCoID": 0,
            "baud_rate": 9600,
            "source": "",
        }

        # 确定 INI 路径
        if ini_path and os.path.isfile(ini_path):
            ini_file = ini_path
        elif install_dir:
            ini_file = os.path.join(install_dir, "System.ini")
            if not os.path.isfile(ini_file):
                ini_file = os.path.join(install_dir, "system.ini")
                if not os.path.isfile(ini_file):
                    ini_file = None
                else:
                    ini_file = None
        else:
            ini_file = None

        # 解析 INI
        if ini_file and os.path.isfile(ini_file):
            try:
                import configparser
                cp = configparser.ConfigParser()
                cp.read(ini_file, encoding="utf-8-sig")
                for sect in ("System", "SYSTEM", "system"):
                    if cp.has_section(sect):
                        if cp.has_option(sect, "dlsCoID"):
                            try:
                                runtime["dlsCoID"] = int(cp.get(sect, "dlsCoID"))
                            except (ValueError, TypeError):
                                runtime["dlsCoID"] = 0
                        if cp.has_option(sect, "BaudRate") or cp.has_option(sect, "baud"):
                            baud_key = "BaudRate" if cp.has_option(sect, "BaudRate") else "baud"
                            try:
                                runtime["baud_rate"] = int(cp.get(sect, baud_key))
                            except (ValueError, TypeError):
                                pass
                        runtime["source"] = f"INI: {ini_file}"
                        break
            except Exception as exc:
                logger.debug("INI 解析失败: %s", exc)

        return runtime

    @classmethod
    def _auto_generate(cls, mdb_path: str) -> Optional[Dict[str, Any]]:
        """从 数据库卡样本自动生成 卡数据结构配置。"""
        try:
            samples = BrandAnalyzer.extract_card_samples_from_mdb(mdb_path)
            if len(samples) < 3:
                logger.info("MDB 卡样本不足 3 张 (%d 张)，无法自动生成 profile", len(samples))
                return None

            profile = BrandAnalyzer.analyze(samples)
            if profile.get("magic") in ("unknown", None):
                logger.warning("自动生成 profile 失败：无法识别 magic")
                return None

            profile["auto_generated"] = True
            profile["source_mdb"] = mdb_path
            return profile
        except Exception as exc:
            logger.warning("自动生成 profile 异常: %s", exc)
            return None

    @classmethod
    def find_dls_co_id(cls, install_dir: str) -> int:
        """快捷方法：从安装目录 System.ini 提取 dlsCoID。"""
        runtime = cls._extract_runtime("", install_dir, None)
        return int(runtime.get("dlsCoID", 0))

    @classmethod
    def list_supported_brands(cls) -> List[Dict[str, str]]:
        """列出所有有 卡数据结构配置 支持的品牌。"""
        brands: List[Dict[str, str]] = []

        # 1. 来自 _BRAND_ID_MAP 的已知映射
        seen = set()
        for scanner_id, adapter_id in _BRAND_ID_MAP.items():
            if adapter_id:
                profile = BrandProfileLoader.load(adapter_id)
                brand_name = profile.get("brand", adapter_id) if profile else adapter_id
                brands.append({
                    "brand_id": scanner_id,
                    "adapter_id": adapter_id,
                    "brand_name": brand_name,
                    "source": "mapped",
                })
                seen.add(adapter_id)

        # 2. 来自 profiles/ 目录的额外 profile
        for fpath in sorted(_PROFILES_DIR.glob("*.json")):
            if fpath.stem in seen:
                continue
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                brands.append({
                    "brand_id": data.get("adapter_id", fpath.stem),
                    "adapter_id": data.get("adapter_id", fpath.stem),
                    "brand_name": data.get("brand", fpath.stem),
                    "source": "direct",
                })
            except Exception:
                continue

        return brands
