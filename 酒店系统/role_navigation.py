# -*- coding: utf-8 -*-
# [UI-REVAMP] 2026-06-20 v7 — 菜单合并优化，聚焦核心动线
"""按角色裁剪主导航与前台默认层级 — 对齐 PMS 岗位动线。

v7 改动：
- 侧栏精简至 5 项 2 组，聚焦高频核心
- 财务/库存合并到"管理"组
- 厂家/系统设置合并到"系统"组
- 其他低频功能通过 MiniTab 或命令面板访问
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Optional

from frontdesk_layers import DEFAULT_FD_LAYERS, get_frontdesk_layers, layers_to_json
from i18n import i18n


@dataclass
class NavItem:
    """侧栏导航项"""
    label: str
    action: str
    icon: str = ""
    perm: list = field(default_factory=list)


@dataclass
class NavGroup:
    """侧栏分组"""
    name: str
    items: list


# v8 侧栏精简至 6 项 — 核心动线
SIDEBAR_NAV_GROUPS: List[NavGroup] = [
    NavGroup("运营", [
        NavItem("房态", "matrix", icon="🏨", perm=[]),
        NavItem("收银", "checkin", icon="💳", perm=[]),
        NavItem("总览", "overview", icon="📊", perm=[]),
    ]),
    NavGroup("管理", [
        NavItem("财务", "finance", icon="💰", perm=["finance"]),
        NavItem("库存", "inventory", icon="📦", perm=["inventory"]),
        NavItem("系统", "settings", icon="⚙", perm=["admin"]),
        NavItem("厂家", "vendor_console", icon="🔧", perm=["debug_panel"]),
    ]),
]

ACTION_PERMISSIONS = {
    "overview": "view_rooms",
    "matrix": "view_rooms",
    "checkin": "checkin",
    "shop": "view_shop",
    "hk": "housekeeping",
    "energy": "energy_monitor",
    "finance": "view_ledger",
    "refunds": "refund.approve",
    "report": "view_reports",
    "shift": "shift_settle",
    "night_audit": "shift_settle",
    "audit": "view_audit",
    "pricing": "manage_pricing",
    "member": "manage_staff",
    "ota": "manage_pricing",
    "staff": "manage_staff",
    "room_unified": "manage_pricing",
    "item_dict": "manage_shop",
    "inventory": "manage_shop",
    "card": "settings_view",
    "settings": "settings_view",
    "console": "settings_view",
    "vendor_console": "debug_panel",
    "debug": "debug_panel",
    "vendor_takeover": "debug_panel",
    "vendor_lock": "debug_panel",
    "vendor_sniffer": "debug_panel",
    "vendor_cloud": "debug_panel",
    "vendor_debug": "debug_panel",
    "service": "view_dashboard",
}

ROLE_HOME_ACTION = {
    "frontdesk": "matrix",
    "manager": "overview",
    "boss": "overview",
    "guest": "matrix",
}

ROLE_LAYER_PRESETS = {
    "frontdesk": {
        "matrix_page": {
            "embedded_dashboard": False,
            "dashboard_kpi_row": False,
            "dashboard_room_snap": False,
            "flow_hint_strip": True,
        },
        "stats_bar": {
            "pending_cart": True,
            "chips_room_status": True,
            "chips_business": False,
            "finance_hub_menu": False,
            "timeline_toggle": True,
            "batch_mode": False,
        },
        "frontdesk_hub": {
            "checkin": True,
            "roster": True,
            "shop": True,
            "service": True,
            "shift": True,
        },
    },
}


def action_permission(action: str) -> Optional[str]:
    return ACTION_PERMISSIONS.get(action)


def home_action_for_role(role: str) -> str:
    return ROLE_HOME_ACTION.get(role, "matrix")


def role_display_name(role: str) -> str:
    _names = {
        "frontdesk": "前台",
        "manager": "经理",
        "boss": "老板",
        "vendor": "厂家工程师",
        "guest": "访客",
    }
    return _names.get(role, role)


def seed_role_layer_preset(role: str) -> Optional[dict]:
    return deepcopy(ROLE_LAYER_PRESETS.get(role))
