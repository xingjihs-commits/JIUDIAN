"""services/vendor_service.py — 厂家门禁与锁定服务（从 vendor_gate/vendor_lockdown 提取）

提供:
  - check_license — 许可证校验
  - check_lockdown — 锁定状态检查
  - get_vendor_config — 厂家配置读取
"""
from __future__ import annotations


def get_vendor_config(db, key: str) -> str:
    """读取厂家配置项。"""
    return (db.get_config(key) or "").strip()


def set_vendor_config(db, key: str, value: str) -> None:
    """写入厂家配置项。"""
    db.set_config(key, value)


def check_license_valid(db) -> bool:
    """检查许可证是否有效。"""
    kill_date = get_vendor_config(db, "kill_switch_date")
    if kill_date:
        from datetime import datetime
        try:
            if datetime.now() > datetime.fromisoformat(kill_date):
                return False
        except (ValueError, TypeError):
            pass
    license_key = get_vendor_config(db, "license_key")
    return bool(license_key)


def is_hotel_suspended(db) -> bool:
    """检查酒店是否被暂停。"""
    status = get_vendor_config(db, "hotel_status")
    return status.upper() == "SUSPENDED"


def get_lock_level(db) -> str:
    """获取厂家锁定级别。"""
    return get_vendor_config(db, "lock_level") or "NONE"
