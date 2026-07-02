"""proxy_log_parser 单元测试。"""

from __future__ import annotations

import sys
import tempfile
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

from collector.bridgecore.proxy_log_parser import parse_proxy_log  # noqa: E402


class TestProxyLogParser(unittest.TestCase):
    def test_parse_direct_write(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as f:
            f.write("DirectWriteUSB hex=C92B20B701020304\n")
            path = f.name
        try:
            records = parse_proxy_log(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["fn_name"], "direct_write_usb")
            self.assertIn("C92B20B7", records[0]["payload_hex"])
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
