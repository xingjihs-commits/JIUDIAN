"""导航 manifest — action 唯一登记。侧栏/MiniTab/快捷键只读此表。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class NavEntry:
    action: str
    container: str          # matrix | workspace | hub | dialog
    hub_sub: Optional[str]  # checkin/roster/shop/service/shift
    focus_mode: str         # matrix | overview | split | workspace_full
    sidebar: bool           # 是否在侧栏显示（本轮不改侧栏，只登记）
    mini_tab: bool          # 是否应在 MiniTab 显示
    perm: Optional[str]

# perm 值来自 role_navigation.ACTION_PERMISSIONS，缺失项从 _perm_map 补全
NAV_MANIFEST: list[NavEntry] = [
    NavEntry("matrix", "matrix", None, "matrix", True, False, "view_rooms"),
    NavEntry("overview", "workspace", None, "overview", True, True, "view_rooms"),
    NavEntry("checkin", "hub", "checkin", "split", True, False, "checkin"),
    NavEntry("roster", "hub", "roster", "split", False, False, "view_guests"),
    NavEntry("shop", "hub", "shop", "split", False, False, "view_shop"),
    NavEntry("service", "hub", "service", "split", True, False, "view_dashboard"),
    NavEntry("shift", "hub", "shift", "split", True, False, "shift_settle"),
    NavEntry("finance", "workspace", None, "workspace_full", True, True, "view_ledger"),
    NavEntry("refunds", "workspace", None, "workspace_full", False, True, "refund.approve"),
    NavEntry("report", "workspace", None, "workspace_full", False, True, "view_reports"),
    NavEntry("inventory", "workspace", None, "workspace_full", True, True, "manage_shop"),
    NavEntry("audit", "workspace", None, "workspace_full", True, True, "view_audit"),
    NavEntry("staff", "workspace", None, "workspace_full", True, True, "manage_staff"),
    NavEntry("settings", "workspace", None, "workspace_full", True, True, "settings_view"),
    NavEntry("vendor_console", "workspace", None, "workspace_full", True, True, "debug_panel"),
    # 以下从 workspace_dock._tab_refs 键补全
    NavEntry("night_audit", "workspace", None, "workspace_full", False, True, "shift_settle"),
    NavEntry("hk", "workspace", None, "workspace_full", False, True, "housekeeping"),
    NavEntry("energy", "workspace", None, "workspace_full", False, True, "energy_monitor"),
    NavEntry("ota", "workspace", None, "workspace_full", False, True, "manage_pricing"),
    NavEntry("pricing", "workspace", None, "workspace_full", False, True, "manage_pricing"),
    NavEntry("member", "workspace", None, "workspace_full", False, True, "manage_staff"),
    NavEntry("room_unified", "workspace", None, "workspace_full", False, True, "manage_pricing"),
    NavEntry("item_dict", "workspace", None, "workspace_full", False, True, "manage_shop"),
    NavEntry("card", "workspace", None, "workspace_full", False, True, "settings_view"),
]

def get_entry(action: str) -> Optional[NavEntry]:
    for e in NAV_MANIFEST:
        if e.action == action:
            return e
    return None
