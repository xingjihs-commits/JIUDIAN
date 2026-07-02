"""测试导航 manifest"""
from nav_manifest import NAV_MANIFEST, get_entry

def test_actions_unique():
    """断言 NAV_MANIFEST 中每个 action 唯一"""
    actions = [e.action for e in NAV_MANIFEST]
    assert len(actions) == len(set(actions)), f"Duplicate actions found: {[a for a in actions if actions.count(a) > 1]}"

def test_get_entry():
    """断言 get_entry 正常查找"""
    assert get_entry("matrix") is not None
    assert get_entry("nonexistent") is None

def test_workspace_actions_in_manifest():
    """断言 workspace_dock._tab_refs 里每个 key（除 frontdesk）在 manifest 中有条目"""
    known_workspace_actions = {
        "overview", "finance", "refunds", "report", "inventory",
        "audit", "staff", "member", "pricing", "card", "settings",
        "vendor_console", "night_audit", "room_unified", "item_dict",
        "ota", "hk", "energy",
    }
    manifest_actions = {e.action for e in NAV_MANIFEST}
    missing = known_workspace_actions - manifest_actions
    assert not missing, f"Missing from manifest: {missing}"
