"""Collector 身份引擎单元测试（无需硬件）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

from collector.bridgecore.identity_engine import analyze, IdentityResult  # noqa: E402
from collector.bridgecore.oem_process import (  # noqa: E402
    OemProcess,
    find_oem_exes,
    _exe_name_score,
)


class TestOemProcess(unittest.TestCase):
    def test_exe_name_score_cardlock_variant(self):
        self.assertGreater(_exe_name_score("CARDLOCK-N8.9.1.EXE"), 0)
        self.assertLess(_exe_name_score("uninstall.exe"), 0)

    def test_find_oem_exes_in_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CARDLOCK-N8.9.1.EXE").write_bytes(b"MZ")
            (Path(tmp) / "RepairAccess.exe").write_bytes(b"MZ")
            exes = find_oem_exes(tmp)
            self.assertTrue(exes)
            self.assertIn("cardlock", exes[0].name.lower())


class TestIdentityEngine(unittest.TestCase):
    def test_oem_running_blocks_bridge(self):
        with tempfile.TemporaryDirectory() as install_dir:
            running = [
                OemProcess(pid=1234, name="CARDLOCK-N8.9.1.EXE",
                           exe_path=os.path.join(install_dir, "CARDLOCK-N8.9.1.EXE"),
                           reason="exe在所选目录"),
            ]
            fake_fs = MagicMock()
            fake_fs.system_ini = MagicMock(dls_co_id="12345", hotel_id="H1")
            fake_fs.mdb_summary = MagicMock(source="CardLock.MDB", tables=["a"], room_count=10)
            fake_fs.file_count = 100
            fake_fs.total_size_mb = 12.5
            fake_fs.dll_exports = [MagicMock()]

            fake_candidate = {
                "dll_name": "Lock9200.dll",
                "confidence": 0.85,
                "matched_functions": {"init": "init", "read_card": "readcard", "guest_card": "guestcard"},
                "candidate_profile": {
                    "brand": "auto_lock9200",
                    "dll": {"path": "Lock9200.dll", "init": "init", "read": "readcard"},
                },
            }

            with patch("collector.filesystem_scanner.FileSystemScanner") as Scanner, \
                 patch("collector.bridgecore.identity_engine.probe_candidates",
                       return_value=[fake_candidate]), \
                 patch("collector.bridgecore.identity_engine.find_running_oem_processes",
                       return_value=running), \
                 patch("collector.bridgecore.identity_engine.find_oem_exes", return_value=[]):
                Scanner.return_value.scan.return_value = fake_fs
                bridge = MagicMock()
                result = analyze(install_dir, bridge=bridge)

            self.assertTrue(result.site_ok)
            self.assertFalse(result.bridge_ok)
            self.assertIn("oem_running", result.blockers)
            self.assertIn("CARDLOCK", result.bridge_hint)
            bridge.load_dll.assert_not_called()

    def test_site_ok_without_bridge(self):
        with tempfile.TemporaryDirectory() as install_dir:
            fake_fs = MagicMock()
            fake_fs.system_ini = None
            fake_fs.mdb_summary = MagicMock(source="x.mdb", tables=[], room_count=5)
            fake_fs.file_count = 10
            fake_fs.total_size_mb = 1.0
            fake_fs.dll_exports = []

            with patch("collector.filesystem_scanner.FileSystemScanner") as Scanner, \
                 patch("collector.bridgecore.identity_engine.probe_candidates", return_value=[]), \
                 patch("collector.bridgecore.identity_engine.find_running_oem_processes",
                       return_value=[]), \
                 patch("collector.bridgecore.identity_engine.find_oem_exes",
                       return_value=[MagicMock(name="Door.exe", path="x", score=50)]):
                Scanner.return_value.scan.return_value = fake_fs
                result = analyze(install_dir, bridge=None, skip_bridge=True)

            self.assertTrue(result.site_ok)
            self.assertFalse(result.bridge_ok)

    def test_bridge_ret_hint_259(self):
        from collector.bridgecore.identity_engine import _hint_for_ret
        self.assertIn("占用", _hint_for_ret(259))


if __name__ == "__main__":
    unittest.main()
