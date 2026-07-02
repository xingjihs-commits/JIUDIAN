"""Legacy proUSB domain helpers shared by UI and migration code."""
from __future__ import annotations

from dataclasses import dataclass


CARD_TYPE_LABELS = {
    "auth": "授权卡",
    "record": "记录卡",
    "roomset": "房号设置卡",
    "timeset": "时钟设置卡",
    "loss": "挂失卡",
    "guest": "客人卡",
    "checkout": "退房卡",
    "group": "组控卡",
    "groupset": "组号设置卡",
    "emergency": "应急卡",
    "master": "总卡",
    "building": "楼栋卡",
    "floor": "层控卡",
}

CARD_STATUS_ACTIVE = "客人卡"
CARD_STATUS_ERASED = "已注销"
CARD_STATUS_LOST = "已挂失"
CARD_STATUS_EXPIRED = "已过期"
CARD_STATUS_PENDING = "待写入"
CARD_STATUS_LOST_PENDING = "待刷挂失卡"

LEGACY_ACTIVE_CARD_STATUSES = (CARD_STATUS_ACTIVE, "ACTIVE")
LEGACY_ERASED_CARD_STATUSES = (CARD_STATUS_ERASED, "CANCELLED")
LEGACY_LOST_CARD_STATUSES = (CARD_STATUS_LOST, "LOST", "BLACKLISTED")
LEGACY_LOST_PENDING_STATUSES = (CARD_STATUS_LOST_PENDING, "LOST_PENDING_PHYSICAL")
LEGACY_EXPIRED_CARD_STATUSES = (CARD_STATUS_EXPIRED, "EXPIRED")


ROOM_STATUS_LEGACY = {
    "VC": "空净房",
    "OH": "钟点房",
    "OT": "预订房",
    "TO": "催租房",
    "VD": "脏房",
    "OO": "维修房",
    "OC_WalkIn": "散客房",
    "OC_Team": "团体房",
}


ROOM_STATUS_TO_LEGACY = {
    "READY": "VC",
    "INHOUSE": "OC_WalkIn",
    "DIRTY": "VD",
    "OVERTIME": "TO",
    "MAINTENANCE": "OO",
    "RESERVED": "OT",
}


LOCK_SOUND_MESSAGES = [
    ("2声", "正确提示，表示是设置卡"),
    ("3声", "门锁已反锁：用能开反锁的卡或解除反锁"),
    ("4声", "此卡号已经被挂失"),
    ("6声", "房号不对：需要设置门锁房号"),
    ("7声", "卡已过期：需要设置门锁时钟"),
    ("8声", "客人卡被后卡覆盖/退房卡限制，功能卡则是开锁时段不正确"),
    ("9声", "卡已被挂失，已进入黑名单"),
    ("10声", "授权码无效：需要机械钥匙或重新授权"),
    ("11声", "楼栋卡/层控卡楼栋号或层号无效：需要设置房号"),
    ("12声", "员工卡被后卡覆盖：刷授权卡恢复"),
    ("15声", "非本酒店卡：刷授权卡或重新发卡"),
    ("30声", "非本系统卡：重新发卡"),
]


def normalize_lock_no_hex(raw: str) -> str:
    """Return Solid 8-char lock_no.

    Old proUSB screens expose six hex chars (BldNo + FlrNo + RomID). Solid's
    V9 write pipeline uses the eight-char payload form with an 0x80 prefix.
    """
    s = (raw or "").strip().upper().replace(" ", "").replace("-", "")
    if len(s) >= 8 and all(c in "0123456789ABCDEF" for c in s[:8]):
        return s[:8]
    if len(s) >= 6 and all(c in "0123456789ABCDEF" for c in s[:6]):
        six = s[:6]
        return f"80{six[4:6]}{six[2:4]}{six[0:2]}"
    return ""


def display_lock_no(raw: str) -> str:
    """Return the six-char legacy display value for bosses/front desk."""
    s = normalize_lock_no_hex(raw)
    if not s:
        return ""
    return f"{s[6:8]}{s[4:6]}{s[2:4]}"


def lock_no_from_parts(bld_no: int, flr_no: int, rom_id: int) -> str:
    b = max(0, min(255, int(bld_no or 0)))
    f = max(0, min(255, int(flr_no or 0)))
    r = max(0, min(255, int(rom_id or 0)))
    return f"80{r:02X}{f:02X}{b:02X}"


@dataclass(frozen=True)
class LegacyCardSpec:
    key: str
    title: str
    group: str
    adapter_method: str


LEGACY_CARD_SPECS = [
    LegacyCardSpec("auth", "授权卡", "门锁工程卡", "issue_auth_card"),
    LegacyCardSpec("roomset", "房号设置卡", "门锁工程卡", "issue_room_no_card"),
    LegacyCardSpec("timeset", "时钟设置卡", "门锁工程卡", "issue_clock_card"),
    LegacyCardSpec("groupset", "组号设置卡", "门锁工程卡", "issue_group_set_card"),
    LegacyCardSpec("floor", "层控卡", "员工用的开门卡", "issue_floor_card"),
    LegacyCardSpec("building", "楼栋卡", "员工用的开门卡", "issue_building_card"),
    LegacyCardSpec("master", "总卡", "员工用的开门卡", "issue_master_card"),
    LegacyCardSpec("emergency", "应急卡", "员工用的开门卡", "issue_emergency_card"),
    LegacyCardSpec("group", "组控卡", "员工用的开门卡", "issue_group_card"),
    LegacyCardSpec("record", "记录卡", "用于特定功能的卡", "issue_record_card"),
    LegacyCardSpec("loss", "挂失卡", "用于特定功能的卡", "issue_loss_report_card"),
    LegacyCardSpec("checkout", "退房卡", "用于特定功能的卡", "issue_check_out_card"),
]
