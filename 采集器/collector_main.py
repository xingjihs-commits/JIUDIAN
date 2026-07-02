"""
collector_main.py — Solid 学习助手 主入口

U盘万能采集工具，不依赖 PMS。
用法：python collector_main.py
或打包成 SolidCollector.exe 双击运行。
"""

import sys
import os
import logging
import atexit
import signal


def _shutdown_bridge(*_args):
    """程序退出/被杀时清理 bridge32.exe 子进程，防僵尸进程占 USB 锁。"""
    try:
        _bridge_shutdown = None  # type: ignore
        from collector.collector_bridge import _shutdown as _bridge_shutdown
        _bridge_shutdown()
    except Exception:
        pass


# 注册清理：正常退出、SIGTERM、SIGINT
atexit.register(_shutdown_bridge)
try:
    signal.signal(signal.SIGTERM, _shutdown_bridge)
    signal.signal(signal.SIGINT, _shutdown_bridge)
except (ValueError, AttributeError):
    # Windows 下 SIGTERM 可能不可用，忽略
    pass


def _ensure_collector_package() -> str:
    """源码运行：把「采集器」目录注册为 collector 伪包。
    打包版：模块扁平化为顶级，直接返回 EXE 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    pkg_root = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(pkg_root)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    if "collector" not in sys.modules:
        import types
        pkg = types.ModuleType("collector")
        pkg.__path__ = [pkg_root]
        pkg.__package__ = "collector"
        sys.modules["collector"] = pkg
    return pkg_root


_COLLECTOR_DIR = _ensure_collector_package()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QLocale
from PySide6.QtGui import QIcon
from collector.collector_ui import CollectorWizard


def setup_logging():
    log_dir = os.path.join(_COLLECTOR_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "collector.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Solid 学习助手 启动")

    app = QApplication(sys.argv)
    QLocale.setDefault(QLocale(QLocale.Chinese, QLocale.China))

    _icon = os.path.join(_COLLECTOR_DIR, "assets", "app_icon.png")
    if os.path.isfile(_icon):
        app.setWindowIcon(QIcon(_icon))

    # 设置全局样式
    app.setStyleSheet("""
        QGroupBox#FdGhost {
            border: 1px solid #E2E8F0;
            border-radius: 10px;
            margin-top: 6px;
            padding: 16px 12px 12px 12px;
            font-size: 13px;
            font-weight: 600;
        }
        QGroupBox#FdGhost::title {
            subcontrol-origin: margin;
            padding: 0 6px;
        }
        QPushButton#SolidPrimaryBtn {
            background-color: #2563EB;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 8px 18px;
            font-size: 13px;
            font-weight: 600;
        }
        QPushButton#SolidPrimaryBtn:hover {
            background-color: #1D4ED8;
        }
        QPushButton#SolidPrimaryBtn:disabled {
            background-color: #93C5FD;
        }
        QPushButton#SolidSecondaryBtn {
            background-color: #E0E7FF;
            color: #3730A3;
            border: 1px solid #C7D2FE;
            border-radius: 6px;
            padding: 8px 18px;
            font-size: 13px;
            font-weight: 600;
        }
        QPushButton#SolidSecondaryBtn:hover {
            background-color: #C7D2FE;
        }
        QPushButton#SolidSecondaryBtn:disabled {
            background-color: #EEF2FF;
            color: #6366F1;
            border: 1px solid #C7D2FE;
        }
        QPushButton#FdGhostBtn {
            background-color: #F1F5F9;
            color: #0F172A;
            border: 1px solid #CBD5E1;
            border-radius: 6px;
            padding: 8px 18px;
            font-size: 13px;
        }
        QPushButton#FdGhostBtn:hover {
            background-color: #E2E8F0;
        }
        QLineEdit {
            border: 1px solid #CBD5E1;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 13px;
        }
        QProgressBar {
            border: none;
            border-radius: 3px;
            background: #E2E8F0;
            text-align: center;
        }
        QProgressBar::chunk {
            background: #2563EB;
            border-radius: 3px;
        }
        QDateEdit {
            border: 1px solid #CBD5E1;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 13px;
        }
    """)

    w = CollectorWizard()
    w.show()
    # Qt 退出时也触发清理（覆盖窗口关闭场景）
    app.aboutToQuit.connect(_shutdown_bridge)
    exit_code = app.exec()
    _shutdown_bridge()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
