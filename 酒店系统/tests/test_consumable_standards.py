# -*- coding: utf-8 -*-
from consumable_standards import (
    default_consumable_seed_rows,
    standard_qty_for,
    tier_for_type_id,
)


def test_default_consumable_rows_not_all_zero():
    rows = default_consumable_seed_rows()
    assert len(rows) == 10
    assert any(c > 0 for _, _, c, t in rows for v in (c, t))


def test_standard_qty_twin_vs_suite():
    assert standard_qty_for("twin", "毛巾") == 2
    assert standard_qty_for("suite", "毛巾") == 4
    assert tier_for_type_id("double") == "double"
    assert standard_qty_for("double", "卷纸") == 2
