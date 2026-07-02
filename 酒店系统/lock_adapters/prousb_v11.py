"""ProUSB V11 adapter — 针对 proRFLV11.dll

修复记录(2026-06-22): 原文件整层级缩进错误，detect() 之后代码全不可达。
  现按 Python 正确缩进重写。V11 走 GenericLockAdapter 兜底。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .base import CardResult, LockAdapter

logger = logging.getLogger(__name__)


class ProUsbV11Adapter(LockAdapter):
    brand = "proUSB V11"
    display_name = "proUSB V11"
    priority = 44

    DLL_NAME = "proRFLV11.dll"

    @classmethod
    def detect(cls, install_dir) -> Optional["ProUsbV11Adapter"]:
        install_dir = Path(install_dir)
        if not install_dir.is_dir():
            return None
        dll = install_dir / cls.DLL_NAME
        if not dll.is_file():
            return None
        return cls(install_dir)

    def initialize(self) -> bool:
        return self._init_via_generic()

    def get_version(self) -> str:
        return "proUSB V11"

    def _init_via_generic(self):
        """通过 GenericLockAdapter + 品牌配置 初始化。"""
        from .generic_adapter import GenericLockAdapter, _load_profiles_cache
        profiles = _load_profiles_cache()
        profile = profiles.get(self.brand.lower())
        if profile and profile.get("supported"):
            adapter = GenericLockAdapter(Path(self.install_dir), profile=profile)
            return adapter.initialize()
        return False

    def issue_guest_card(self, room_id, guest_name, checkin, checkout):
        from .generic_adapter import GenericLockAdapter
        return GenericLockAdapter(self.install_dir).issue_guest_card(
            lock_no=room_id, b_date=checkin, e_date=checkout,
        )

    def erase_card(self):
        from .generic_adapter import GenericLockAdapter
        return GenericLockAdapter(self.install_dir).erase_card()
