# -*- coding: utf-8 -*-
from bridgecore.handover_assembler import normalize_guests, normalize_rooms


def test_normalize_guests_keeps_checkout():
    guests = normalize_guests([{
        "room_id": "101",
        "name": "李四",
        "checkout_time": "2026-06-21 11:00",
    }])
    assert guests[0]["checkout_time"] == "2026-06-21 11:00"
    assert guests[0]["guest_name"] == "李四"


def test_normalize_rooms_bld_flr_rom():
    rooms = normalize_rooms([{
        "room_id": "101",
        "bld_no": 2,
        "flr_no": 5,
        "rom_id": 8,
        "lock_no": "802050801",
    }])
    assert rooms[0]["bld_no"] == 2
    assert rooms[0]["flr_no"] == 5
    assert rooms[0]["rom_id"] == 8
    assert rooms[0]["room_type"] == "标准间"


def test_build_workflow_bundle_merges_all_types():
    from bridgecore.handover_assembler import build_workflow_bundle

    bundle = build_workflow_bundle(
        workflows_by_type={
            "building_card": {"steps": [{"action": "click"}]},
            "floor_card": {"steps": [{"action": "type"}]},
        },
    )
    assert bundle["building_card"]["steps"][0]["action"] == "click"
    assert bundle["floor_card"]["steps"][0]["action"] == "type"
