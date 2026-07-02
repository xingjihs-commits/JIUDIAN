"""PyInstaller 运行时：把 _MEIPASS 注册为 collector 包根目录。"""
import os
import sys
import types

root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if "collector" not in sys.modules:
    pkg = types.ModuleType("collector")
    # PyInstaller 会把 collector/ 目录按包层级提取到 _MEIPASS 下，
    # 所以 __path__ 必须指到 _MEIPASS/collector/，不能指 _MEIPASS 本身。
    pkg_root = os.path.join(root, "collector")
    pkg.__path__ = [pkg_root] if os.path.isdir(pkg_root) else [root]
    pkg.__package__ = "collector"
    sys.modules["collector"] = pkg
