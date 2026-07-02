# -*- coding: utf-8 -*-
"""握手包 P0：checkout_time / INHOUSE / bld-flr-rom 合并。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def real_db(tmp_path: Path):
    with patch("database._get_app_dir", return_value=tmp_path):
        from database import ShadowDatabase

        db = ShadowDatabase("test_handover.db")
        yield db


def test_merge_rooms_writes_bld_flr_rom(real_db):
    from lock_deploy.handover_importer import HandoverImporter

    imp = HandoverImporter()
    with patch("lock_deploy.handover_importer._db", return_value=real_db):
        n = imp._merge_rooms([{
            "room_id": "301",
            "lock_no": "801C0301",
            "building_no": "1",
            "bld_no": 1,
            "flr_no": 3,
            "rom_id": 1,
            "floor": "3",
            "room_type": "标准间",
            "current_seq": 5,
        }])
    assert n == 1
    row = real_db.execute(
        "SELECT lock_no, bld_no, flr_no, rom_id, room_type, status FROM rooms WHERE room_id=?",
        ("301",),
    ).fetchone()
    assert row[0] == "801C0301"
    assert row[1] == 1
    assert row[2] == 3
    assert row[3] == 1
    assert row[4] == "标准间"
    assert row[5] == "VC"


def test_merge_guests_sets_inhouse_and_checkout(real_db):
    from lock_deploy.handover_importer import HandoverImporter

    imp = HandoverImporter()
    with patch("lock_deploy.handover_importer._db", return_value=real_db):
        imp._merge_rooms([{"room_id": "301", "lock_no": "801C0301", "bld_no": 1, "flr_no": 3, "rom_id": 1}])
        n = imp._merge_guests([{
            "room_id": "301",
            "guest_name": "张三",
            "checkin_time": "2026-06-18 14:00",
            "checkout_time": "2026-06-20 12:00",
            "phone": "13800000000",
        }])
    assert n == 1
    guest = real_db.execute(
        "SELECT name, checkout_time, status FROM guests WHERE room_id=?",
        ("301",),
    ).fetchone()
    assert guest[0] == "张三"
    assert "2026-06-20" in str(guest[1])
    assert guest[2] == "INHOUSE"
    st = real_db.execute("SELECT status FROM rooms WHERE room_id=?", ("301",)).fetchone()
    assert st[0] == "INHOUSE"
