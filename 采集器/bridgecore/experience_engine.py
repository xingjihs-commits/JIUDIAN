"""
经验值系统 — 三大职责：
  ExperienceMatcher: DLL MD5 命中已有经验 → 直接返回完整配置
  FailureMemory: 失败教训读写
  SectorKeyRing: 扇区密钥按品牌存取
"""

import os
import json
import time
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, List


def _get_collector_root() -> Path:
    import sys
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _experienced_brands_path() -> Path:
    p = _get_collector_root() / "learned_profiles" / "experienced_brands.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _failure_lessons_dir() -> Path:
    d = _get_collector_root() / "learned_profiles" / "failure_lessons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sector_keys_dir() -> Path:
    d = _get_collector_root() / "learned_profiles" / "sector_keys"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dll_md5(dll_path: str) -> str:
    h = hashlib.md5()
    with open(dll_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


class ExperienceMatcher:
    """DLL MD5 命中已有经验 → 秒级返回完整配置"""

    def __init__(self):
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load_index()

    def _load_index(self):
        path = _experienced_brands_path()
        if not path.exists():
            return
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    md5 = record.get("dll_md5")
                    if md5:
                        self._index[md5] = record
                except Exception:
                    continue

    def match(self, dll_path: str) -> Optional[Dict[str, Any]]:
        """秒级返回完整配置，或 None"""
        md5 = _dll_md5(dll_path)
        return self._index.get(md5)

    def save_experience(self, dll_path: str, profile: Dict[str, Any], brand: str) -> None:
        """保存成功经验"""
        md5 = _dll_md5(dll_path)
        record = {
            "dll_md5": md5,
            "brand": brand,
            "profile": profile,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "dll_name": os.path.basename(dll_path),
        }
        self._index[md5] = record
        path = _experienced_brands_path()
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


class FailureMemory:
    """失败教训读写"""

    def remember_failure(self, dll_path: str, reason: str, context: Dict[str, Any]) -> None:
        """写教训"""
        md5 = _dll_md5(dll_path)
        record = {
            "dll_md5": md5,
            "dll_name": os.path.basename(dll_path),
            "reason": reason,
            "context": context,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        d = _failure_lessons_dir()
        fpath = d / f"{md5}.json"
        existing = []
        if fpath.exists():
            try:
                existing = json.loads(fpath.read_text(encoding='utf-8'))
                if not isinstance(existing, list):
                    existing = [existing]
            except Exception:
                pass
        existing.append(record)
        fpath.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding='utf-8')

    def warn_before_retry(self, dll_path: str) -> Optional[str]:
        """返回上次失败原因或 None"""
        md5 = _dll_md5(dll_path)
        fpath = _failure_lessons_dir() / f"{md5}.json"
        if not fpath.exists():
            return None
        try:
            records = json.loads(fpath.read_text(encoding='utf-8'))
            if isinstance(records, list) and records:
                last = records[-1]
                return f"上次失败 ({last.get('timestamp')}): {last.get('reason')}"
        except Exception:
            pass
        return None


class SectorKeyRing:
    """扇区密钥按品牌存取"""

    def load_keys(self, brand: str) -> Dict[str, Any]:
        """返回该品牌已知密钥"""
        fpath = _sector_keys_dir() / f"{brand}.json"
        if fpath.exists():
            try:
                return json.loads(fpath.read_text(encoding='utf-8'))
            except Exception:
                pass
        return {"keys": [], "sectors": {}}

    def save_keys(self, brand: str, keys_dict: Dict[int, str]) -> None:
        """存密钥"""
        fpath = _sector_keys_dir() / f"{brand}.json"
        existing = self.load_keys(brand)
        existing.setdefault("keys", [])
        existing.setdefault("sectors", {})
        for sector, key in keys_dict.items():
            existing["sectors"][str(sector)] = key
            if key not in existing["keys"]:
                existing["keys"].append(key)
        existing["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        fpath.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding='utf-8')
