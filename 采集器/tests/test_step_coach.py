"""9 步操作教练单元测试。"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_COLLECTOR_ROOT = Path(__file__).resolve().parents[1]
if str(_COLLECTOR_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_COLLECTOR_ROOT.parent))

if "collector" not in sys.modules:
    pkg = types.ModuleType("collector")
    pkg.__path__ = [str(_COLLECTOR_ROOT)]
    pkg.__package__ = "collector"
    sys.modules["collector"] = pkg

from collector.step_coach import (  # noqa: E402
    STEP_TOTAL,
    StepCoachState,
    resolve_step_coach,
)
from collector.bridgecore.graduation_coach import evaluate  # noqa: E402


def _identity(site_ok=False, bridge_ok=False, main_dll="", blockers=None, bridge_hint=""):
    ident = MagicMock()
    ident.site_ok = site_ok
    ident.bridge_ok = bridge_ok
    ident.main_dll = main_dll
    ident.blockers = blockers or []
    ident.bridge_hint = bridge_hint
    return ident


def _grad(can_graduate=False, readback_passed=False):
    items = []
    if readback_passed:
        it = MagicMock()
        it.id = "readback"
        it.passed = True
        items.append(it)
    state = MagicMock()
    state.can_graduate = can_graduate
    state.items = items
    return state


class TestStepCoach(unittest.TestCase):
    def test_step1_no_identity(self):
        st = resolve_step_coach(identity=None)
        self.assertEqual(st.step_index, 1)
        self.assertEqual(st.target_widget_id, "detect_btn")

    def test_step1_site_not_ok(self):
        st = resolve_step_coach(identity=_identity(site_ok=False))
        self.assertEqual(st.step_index, 1)

    def test_step2_after_scan(self):
        st = resolve_step_coach(
            identity=_identity(site_ok=True, bridge_ok=True),
            card_type_ready=False,
        )
        self.assertEqual(st.step_index, 2)
        self.assertEqual(st.target_widget_id, "card_type_combo")

    def test_step3_read_blank(self):
        st = resolve_step_coach(
            identity=_identity(site_ok=True, bridge_ok=True, main_dll="Card.dll"),
            current={"blank_hex": "", "written_hex": ""},
        )
        self.assertEqual(st.step_index, 3)
        self.assertIn("Card.dll", st.why_hint)

    def test_step3_bridge_blocked(self):
        st = resolve_step_coach(
            identity=_identity(
                site_ok=True,
                bridge_ok=False,
                blockers=["oem_running"],
                bridge_hint="请关闭 CardLock.exe",
            ),
        )
        self.assertEqual(st.step_index, 3)
        self.assertTrue(st.bridge_blocked)
        self.assertIn("占用", st.action)

    def test_step4_oem_pause(self):
        st = resolve_step_coach(
            identity=_identity(site_ok=True, bridge_ok=True),
            current={"blank_hex": "AA" * 32, "written_hex": ""},
            oem_phase_complete=False,
        )
        self.assertEqual(st.step_index, 4)
        self.assertTrue(st.is_oem_pause)
        self.assertEqual(st.location, "oem")

    def test_step5_after_oem(self):
        st = resolve_step_coach(
            identity=_identity(site_ok=True, bridge_ok=True),
            current={"blank_hex": "AA" * 32, "written_hex": ""},
            oem_phase_complete=True,
        )
        self.assertEqual(st.step_index, 5)
        self.assertEqual(st.target_widget_id, "rw_btn")

    def test_step6_add_sample(self):
        st = resolve_step_coach(
            identity=_identity(site_ok=True, bridge_ok=True),
            current={
                "blank_hex": "AA" * 32,
                "written_hex": "BB" * 32,
                "done": False,
            },
        )
        self.assertEqual(st.step_index, 6)
        self.assertEqual(st.target_widget_id, "add_btn")

    def test_step7_analyze(self):
        st = resolve_step_coach(
            identity=_identity(site_ok=True, bridge_ok=True),
            samples=[{"hex": "BB" * 32, "blank_hex": "AA" * 32}],
            analyze_result=None,
        )
        self.assertEqual(st.step_index, 7)
        self.assertEqual(st.target_widget_id, "analyze_btn")

    def test_step8_readback(self):
        st = resolve_step_coach(
            identity=_identity(site_ok=True, bridge_ok=True),
            samples=[{"hex": "BB" * 32, "blank_hex": "AA" * 32}],
            analyze_result={"success": True},
            graduation_state=_grad(readback_passed=False),
            readback_hex="",
        )
        self.assertEqual(st.step_index, 8)

    def test_step8_bridge_blocked(self):
        st = resolve_step_coach(
            identity=_identity(site_ok=True, bridge_ok=False),
            samples=[{"hex": "BB" * 32}],
            analyze_result={"success": True},
            graduation_state=_grad(),
        )
        self.assertEqual(st.step_index, 8)
        self.assertTrue(st.bridge_blocked)

    def test_step9_can_graduate(self):
        st = resolve_step_coach(
            identity=_identity(site_ok=True, bridge_ok=True, main_dll="Card.dll"),
            graduation_state=_grad(can_graduate=True),
        )
        self.assertEqual(st.step_index, 9)
        self.assertEqual(st.target_widget_id, "handover_build_btn")

    def test_step_total_constant(self):
        st = resolve_step_coach(identity=None)
        self.assertEqual(st.step_total, STEP_TOTAL)
        self.assertIsInstance(st, StepCoachState)

    def test_graduation_alignment_step8(self):
        """分析完成但 readback 未过 → 教练步 8 与 graduation pending 一致。"""
        ident = _identity(site_ok=True, bridge_ok=True, main_dll="Card.dll")
        samples = [{"hex": "BB" * 32, "blank_hex": "AA" * 32, "type": "guest"}]
        probe = {"success": True, "confidence": 0.8}
        analyze = {"success": True, "confidence": 0.8}
        grad = evaluate(
            identity=ident,
            samples=samples,
            analyze_result=analyze,
            probe_result=probe,
            readback_hex=None,
        )
        st = resolve_step_coach(
            identity=ident,
            samples=samples,
            analyze_result=analyze,
            probe_result=probe,
            graduation_state=grad,
        )
        self.assertEqual(st.step_index, 8)
        self.assertFalse(grad.can_graduate)


if __name__ == "__main__":
    unittest.main()
