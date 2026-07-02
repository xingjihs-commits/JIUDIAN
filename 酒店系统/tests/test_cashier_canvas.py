"""收银参考画布缩放算子"""
from tools.cashier_canvas import (
    CHECKIN_DESIGN_H,
    CHECKIN_DESIGN_W,
    compute_checkin_scale,
    px,
    checkin_bottom_dock_min,
)


def test_scale_at_design_size_is_one():
    assert compute_checkin_scale(CHECKIN_DESIGN_W, CHECKIN_DESIGN_H) == 1.0


def test_scale_clamps_small_viewport():
    s = compute_checkin_scale(300, 400)
    assert 0.82 <= s <= 1.15


def test_px_rounds():
    assert px(30, 1.0) == 30
    assert px(30, 1.1) == 33


def test_bottom_dock_min_scales():
    assert checkin_bottom_dock_min(1.0) >= 200
    assert checkin_bottom_dock_min(0.9) < checkin_bottom_dock_min(1.0)


def test_zone_heights_respect_ratio():
    from tools.cashier_canvas import checkin_split_sizes, checkin_zone_heights
    z = checkin_zone_heights(720, 1.0)
    assert z["rest"] == 720 - z["banner"] - 1
    top, bottom = checkin_split_sizes(z["rest"], 1.0)
    assert top + bottom <= z["rest"] + 2
    assert bottom >= checkin_bottom_dock_min(1.0)
