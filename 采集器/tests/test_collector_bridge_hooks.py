"""CollectorBridge hook 单元测试。"""

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

from collector.collector_bridge import CollectorBridge  # noqa: E402


class TestCollectorBridgeHooks(unittest.TestCase):
    def test_register_hook(self):
        bridge = CollectorBridge()
        seen: list[str] = []

        def pre(method: str, args: dict) -> None:
            seen.append(method)

        bridge.register_call_hook(pre_fn=pre)
        self.assertEqual(len(bridge._call_pre_hooks), 1)
        bridge.unregister_call_hook(pre_fn=pre)
        self.assertEqual(len(bridge._call_pre_hooks), 0)


if __name__ == "__main__":
    unittest.main()
