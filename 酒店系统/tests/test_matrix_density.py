"""测试房态密度估算"""
from tools.matrix_density import estimate, DensityReport


def test_estimate_basic():
    """基本场景: 800x600 viewport, 172x124 卡, 100间房"""
    r = estimate(800, 600, 172, 124, room_count=100)
    assert r.cols >= 4, f"Expected cols>=4, got {r.cols}"
    assert r.screens_for_rooms > 0, f"Expected screens_for_rooms>0, got {r.screens_for_rooms}"
    assert r.rooms_per_screen > 0
    assert isinstance(r, DensityReport)


def test_estimate_small_viewport():
    """极小窗口仍能工作"""
    r = estimate(400, 300, 200, 150, room_count=50)
    assert r.cols >= 2
    assert r.rooms_per_screen > 0


def test_estimate_large_viewport():
    """大窗口: 1920x1080"""
    r = estimate(1920, 1080, 172, 124, room_count=120)
    assert r.cols >= 8
    assert r.screens_for_rooms < 10, f"Large screen should fit many rooms, got {r.screens_for_rooms:.1f} screens"


def test_estimate_zero_room_count():
    """0 间房时不崩溃，返回合理值"""
    r = estimate(800, 600, 172, 124, room_count=0)
    assert r.cols >= 2
    # screens_for_rooms 可能为 0 或 999（分母为 0 时）
