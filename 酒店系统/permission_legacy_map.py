"""Translate legacy CardLock OperatorInfo permissions into Solid permission keys."""
from __future__ import annotations

import json

from database import db


LEGACY_ROLE_TO_SOLID = {
    "S": "boss",
    "A": "manager",
    "M": "manager",
    "G": "frontdesk",
}


BIT_POSITIONS = [
    "checkin", "checkout", "room_change", "batch_create", "manage_inventory",
    "manage_pricing", "view_finance", "manage_staff", "system_settings",
]


def permissions_from_legacy_bitmask(bitmask: str) -> list[str]:
    """Best-effort parse of old 8x9 permission bit groups.

    The legacy string uses 1/0/x. We treat any `1` in the same column across
    groups as allowing that Solid permission key.
    """
    cols = [False] * len(BIT_POSITIONS)
    for group in (bitmask or "").split(","):
        group = group.strip()
        for idx, ch in enumerate(group[: len(BIT_POSITIONS)]):
            if ch == "1":
                cols[idx] = True
    return [BIT_POSITIONS[i] for i, enabled in enumerate(cols) if enabled]


def import_legacy_operator(gonghao: str, name: str, quanxian: str, bitmask: str) -> dict:
    role = LEGACY_ROLE_TO_SOLID.get((quanxian or "").strip().upper(), "frontdesk")
    perms = permissions_from_legacy_bitmask(bitmask)
    payload = {"role": role, "permissions": perms}
    db.execute(
        "INSERT OR REPLACE INTO legacy_operator_permissions "
        "(gonghao, name, legacy_role, bitmask, mapped_permissions) VALUES (?, ?, ?, ?, ?)",
        (gonghao or "", name or "", quanxian or "", bitmask or "", json.dumps(payload, ensure_ascii=False)),
    )
    return payload
