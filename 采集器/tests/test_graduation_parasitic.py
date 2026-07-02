"""寄生模式毕业 + protocol_verified 分流测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_COLLECTOR_ROOT = Path(__file__).resolve().parents[1]
if str(_COLLECTOR_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_COLLECTOR_ROOT.parent))

import types

if "collector" not in sys.modules:
    pkg = types.ModuleType("collector")
    pkg.__path__ = [str(_COLLECTOR_ROOT)]
    pkg.__package__ = "collector"
    sys.modules["collector"] = pkg

from collector.bridgecore.graduation_coach import evaluate  # noqa: E402


def _make_identity(site_ok=True, bridge_ok=True, main_dll="", install_dir=""):
    from unittest.mock import MagicMock
    ident = MagicMock()
    ident.site_ok = site_ok
    ident.bridge_ok = bridge_ok
    ident.main_dll = main_dll
    ident.install_dir = install_dir
    ident.blockers = []
    return ident


def _make_probe_result(mode="dll_direct", exe_found=False):
    result = {"mode": mode, "detail": {}}
    if mode == "dll_direct":
        result["detail"]["dll_direct"] = {
            "dll_path": "C:\\Lock\\V9RFL.dll",
            "dll_loaded": True,
            "initialized": True,
        }
        result["detail"]["parasitic"] = {"exe_found": False}
    elif mode == "parasitic":
        result["detail"]["parasitic"] = {
            "cardlock_exe": "CARDLOCK.EXE",
            "exe_found": exe_found,
        }
    return result


class TestGraduationParasitic(unittest.TestCase):
    def test_parasitic_graduates_without_protocol_verified(self):
        identity = _make_identity(site_ok=True, bridge_ok=True, main_dll="")
        samples = [{"blank_hex": "FF", "written_hex": "AA"}]
        analyze = {
            "success": True,
            "confidence": 0.8,
            "card_types": ["guest"],
            "protocol_verified": False,
        }
        probe = _make_probe_result(mode="parasitic", exe_found=True)
        state = evaluate(
            identity=identity,
            samples=samples,
            analyze_result=analyze,
            probe_result=probe,
            readback_hex="AA",
            workflow_recorded=True,
        )
        self.assertTrue(state.items[3].passed)  # protocol
        self.assertTrue(state.can_graduate)

    def test_parasitic_fails_without_workflow(self):
        analyze = {
            "success": True,
            "confidence": 0.8,
            "card_types": ["guest"],
            "protocol_verified": False,
        }
        probe = _make_probe_result(mode="parasitic", exe_found=True)
        state = evaluate(
            samples=[{"blank_hex": "FF", "written_hex": "AA"}],
            analyze_result=analyze,
            probe_result=probe,
            readback_hex="AA",
            workflow_recorded=False,
        )
        self.assertFalse(state.items[3].passed)

    def test_dll_requires_protocol_verified_true(self):
        identity = _make_identity(site_ok=True, bridge_ok=True)
        samples = [{"blank_hex": "FF", "written_hex": "AA"}]
        analyze = {
            "success": True,
            "confidence": 0.8,
            "card_types": ["guest"],
            "protocol_verified": True,
        }
        probe = _make_probe_result(mode="dll_direct")
        state = evaluate(
            identity=identity,
            samples=samples,
            analyze_result=analyze,
            probe_result=probe,
            readback_hex="AA",
        )
        self.assertTrue(state.items[3].passed)

    def test_dll_protocol_verified_none_skips_block(self):
        analyze = {
            "success": True,
            "confidence": 0.8,
            "card_types": ["guest"],
            "protocol_verified": None,
        }
        probe = _make_probe_result(mode="dll_direct")
        state = evaluate(
            identity=_make_identity(site_ok=True, bridge_ok=True),
            samples=[{"blank_hex": "FF", "hex": "AA"}],
            analyze_result=analyze,
            probe_result=probe,
            readback_hex="AA",
        )
        self.assertTrue(state.items[3].passed)

    def test_hex_field_compat_for_pair(self):
        state = evaluate(samples=[{"blank_hex": "FF", "hex": "AA"}])
        self.assertTrue(state.items[2].passed)


if __name__ == "__main__":
    unittest.main()
