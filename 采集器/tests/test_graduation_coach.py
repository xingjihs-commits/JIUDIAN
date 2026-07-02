"""Collector 毕业教练引擎单元测试（无需硬件）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# 源码运行时注册 collector 包
_COLLECTOR_ROOT = Path(__file__).resolve().parents[1]
if str(_COLLECTOR_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_COLLECTOR_ROOT.parent))

import types

if "collector" not in sys.modules:
    pkg = types.ModuleType("collector")
    pkg.__path__ = [str(_COLLECTOR_ROOT)]
    pkg.__package__ = "collector"
    sys.modules["collector"] = pkg

from collector.bridgecore.graduation_coach import (  # noqa: E402
    evaluate,
    GraduationItem,
    GraduationState,
    _find_valid_sample,
    _collect_written_hexes,
)


# ====================================================================
# 辅助函数：构建 mock identity / probe_result
# ====================================================================


def _make_identity(
    site_ok: bool = False,
    bridge_ok: bool = False,
    main_dll: str = "",
    install_dir: str = "",
    bridge_hint: str = "",
    blockers: list[str] | None = None,
):
    """构建模拟 IdentityResult。"""
    ident = MagicMock()
    ident.site_ok = site_ok
    ident.bridge_ok = bridge_ok
    ident.main_dll = main_dll
    ident.install_dir = install_dir
    ident.bridge_hint = bridge_hint
    ident.blockers = blockers or []
    return ident


def _make_analyze_result(
    success: bool = True,
    confidence: float = 0.82,
    card_types: list | None = None,
) -> dict:
    return {
        "success": success,
        "confidence": confidence,
        "card_types": card_types or ["guest"],
    }


def _make_probe_result(mode: str = "dll_direct", dll_path: str = "", exe_found: bool = False) -> dict:
    result: dict = {"mode": mode, "detail": {}}
    if mode == "dll_direct":
        result["detail"]["dll_direct"] = {"dll_path": dll_path, "dll_found": True, "dll_loaded": True, "initialized": True}
        result["detail"]["parasitic"] = {"cardlock_exe": "", "exe_found": False}
    elif mode == "parasitic":
        result["detail"]["dll_direct"] = {}
        result["detail"]["parasitic"] = {"cardlock_exe": "CARDLOCK.EXE", "exe_found": exe_found}
    return result


# ====================================================================
# 测试用例
# ====================================================================


class TestGraduationCoachBasic(unittest.TestCase):
    """基础内部函数测试。"""

    def test_find_valid_sample_hit(self):
        samples = [
            {"blank_hex": "AABB", "written_hex": "CCDD"},
        ]
        self.assertEqual(_find_valid_sample(samples), 0)

    def test_find_valid_sample_miss_same(self):
        samples = [
            {"blank_hex": "AABB", "written_hex": "AABB"},
        ]
        self.assertIsNone(_find_valid_sample(samples))

    def test_find_valid_sample_miss_empty(self):
        samples = [
            {"blank_hex": "", "written_hex": "CCDD"},
            {"blank_hex": "AABB", "written_hex": ""},
        ]
        self.assertIsNone(_find_valid_sample(samples))

    def test_collect_written_hexes(self):
        samples = [
            {"blank_hex": "AABB", "written_hex": "CCDD"},
            {"blank_hex": "EEFF", "written_hex": ""},
            {"blank_hex": "1122", "hex": "3344"},
        ]
        self.assertEqual(_collect_written_hexes(samples), ["CCDD", "3344"])

    def test_find_valid_sample_hex_alias(self):
        samples = [{"blank_hex": "AABB", "hex": "CCDD"}]
        self.assertEqual(_find_valid_sample(samples), 0)


# ====================================================================
# 用例 1：空输入 — can_graduate=False, blockers 含 site
# ====================================================================


class TestGraduationCoachEmpty(unittest.TestCase):
    def test_empty_input(self):
        state = evaluate()
        self.assertFalse(state.can_graduate)
        self.assertIn("site", state.blockers)
        # token 维度自动通过（无 auth_token_repeat），其余均为未过
        self.assertEqual(state.required_count, 6)
        # next_action 应为第一个未过项（site）的 pending_hint
        self.assertIn("开始扫描", state.next_action)


# ====================================================================
# 用例 2：仅 site_ok — next 指向 bridge
# ====================================================================


class TestGraduationCoachOnlySite(unittest.TestCase):
    def test_only_site_ok(self):
        identity = _make_identity(site_ok=True, install_dir="C:\\CardLock")
        state = evaluate(identity=identity)
        self.assertFalse(state.can_graduate)
        # site 应通过
        self.assertTrue(state.items[0].passed)   # site
        # bridge（第2项）未过
        self.assertFalse(state.items[1].passed)  # bridge
        # next_action 应指向 bridge（因为 bridge 在 pair 之前）
        self.assertIn("发卡器", state.next_action)
        # pair（第3项）不应被列为 next_action
        self.assertNotIn("样本", state.next_action)


# ====================================================================
# 用例 3：pair 一组空白+已写 — pair.passed=True
# ====================================================================


class TestGraduationCoachPair(unittest.TestCase):
    def test_pair_valid(self):
        samples = [
            {"blank_hex": "FFFFFFFF", "written_hex": "C92B001A"},
        ]
        state = evaluate(samples=samples)
        # pair 应通过
        self.assertTrue(state.items[2].passed)   # pair 是第3项
        self.assertIn("有有效对照", state.items[2].evidence)


# ====================================================================
# 用例 4：confidence 0.3 — protocol.passed=False
# ====================================================================


class TestGraduationCoachLowConfidence(unittest.TestCase):
    def test_confidence_below_threshold(self):
        analyze_result = _make_analyze_result(confidence=0.3)
        state = evaluate(analyze_result=analyze_result)
        self.assertFalse(state.items[3].passed)  # protocol 是第4项
        self.assertIn("30%", state.items[3].evidence)
        self.assertIn("55%", state.items[3].evidence)


# ====================================================================
# 用例 5：readback 匹配 — readback.passed=True
# ====================================================================


class TestGraduationCoachReadback(unittest.TestCase):
    def test_readback_match(self):
        samples = [
            {"blank_hex": "FFFFFFFF", "written_hex": "C92B001A"},
        ]
        state = evaluate(
            samples=samples,
            readback_hex="C92B001A",
        )
        self.assertTrue(state.items[4].passed)  # readback 是第5项
        self.assertIn("C92B001A", state.items[4].evidence)

    def test_readback_case_insensitive(self):
        samples = [
            {"blank_hex": "FFFFFFFF", "written_hex": "c92b001a"},
        ]
        state = evaluate(
            samples=samples,
            readback_hex="C92B001A",
        )
        self.assertTrue(state.items[4].passed)

    def test_readback_no_match(self):
        samples = [
            {"blank_hex": "FFFFFFFF", "written_hex": "C92B001A"},
        ]
        state = evaluate(
            samples=samples,
            readback_hex="DEADBEEF",
        )
        self.assertFalse(state.items[4].passed)


# ====================================================================
# 用例 6：probe parasitic 无 exe — path.passed=False
# ====================================================================


class TestGraduationCoachPath(unittest.TestCase):
    def test_parasitic_no_exe(self):
        probe_result = _make_probe_result(mode="parasitic", exe_found=False)
        state = evaluate(probe_result=probe_result)
        self.assertFalse(state.items[5].passed)  # path 是第6项
        self.assertIn("未找到", state.items[5].evidence)

    def test_parasitic_with_exe(self):
        probe_result = _make_probe_result(mode="parasitic", exe_found=True)
        state = evaluate(probe_result=probe_result)
        self.assertTrue(state.items[5].passed)

    def test_dll_direct_path(self):
        probe_result = _make_probe_result(mode="dll_direct", dll_path="C:\\Lock\\V9RFL.dll")
        state = evaluate(probe_result=probe_result)
        self.assertTrue(state.items[5].passed)
        self.assertIn("V9RFL.dll", state.items[5].evidence)


# ====================================================================
# 用例 7：六项全过 — can_graduate=True
# ====================================================================


class TestGraduationCoachAllPass(unittest.TestCase):
    def test_all_pass(self):
        identity = _make_identity(
            site_ok=True,
            bridge_ok=True,
            main_dll="V9RFL.dll",
            install_dir="C:\\CardLock",
        )
        samples = [
            {"blank_hex": "FFFFFFFF", "written_hex": "C92B001A"},
        ]
        analyze_result = _make_analyze_result(
            success=True,
            confidence=0.82,
            card_types=["guest"],
        )
        probe_result = _make_probe_result(mode="dll_direct", dll_path="C:\\Lock\\V9RFL.dll")
        readback_hex = "C92B001A"

        state = evaluate(
            identity=identity,
            samples=samples,
            analyze_result=analyze_result,
            probe_result=probe_result,
            readback_hex=readback_hex,
        )
        self.assertTrue(state.can_graduate)
        self.assertEqual(state.passed_count, 8)
        self.assertEqual(state.required_count, 6)
        self.assertEqual(len(state.blockers), 0)
        self.assertEqual(state.next_action, "")


# ====================================================================
# 用例 8：next_action 优先级 — bridge 未过时不说 pair
# ====================================================================


class TestGraduationCoachNextActionPriority(unittest.TestCase):
    def test_bridge_blocked_first(self):
        """site ok, bridge failed → next_action 指向 bridge, 不是 pair。"""
        identity = _make_identity(
            site_ok=True,
            bridge_ok=False,
            main_dll="V9RFL.dll",
            bridge_hint="USB 未就绪",
        )
        samples = [
            {"blank_hex": "FFFFFFFF", "written_hex": "C92B001A"},
        ]
        state = evaluate(
            identity=identity,
            samples=samples,
        )
        # bridge 是第一个未过项
        self.assertFalse(state.items[1].passed)  # bridge
        self.assertTrue(state.items[2].passed)   # pair 实际已过
        self.assertIn("发卡器", state.next_action)
        self.assertNotIn("样本", state.next_action)

    def test_site_blocked_first(self):
        """site failed → next_action 指向 site 而不是 bridge。"""
        identity = _make_identity(
            site_ok=False,
            bridge_ok=False,
        )
        state = evaluate(identity=identity)
        self.assertIn("开始扫描", state.next_action)


if __name__ == "__main__":
    unittest.main()
