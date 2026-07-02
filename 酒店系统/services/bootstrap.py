"""
Solid PMS — 启动引导编排

从 app_main.main() 抽离的 4 个阶段：
  1. preflight     — 环境 / 日志 / 单实例
  2. init_app      — QApplication + 主题 + 字体 + 启动画面
  3. gate          — 激活 / 锁死 / 登录 / 厂家向导
  4. post_login    — 定时器 / 主窗口 / 后台服务 / 向导

所有阶段均无副作用（除 init_app 创建 QApplication），
便于在测试中 mock 或跳过。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_DIR: Path = Path(__file__).resolve().parent.parent  # 酒店系统/

if getattr(sys, "frozen", False):
    PROJECT_DIR = Path(sys.executable).parent

_main_window_ref = None
_panic_recovery_inst = None


def get_main_window():
    return _main_window_ref


def get_panic_recovery():
    return _panic_recovery_inst


def _get_resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return PROJECT_DIR


# ────────────────────────────────────────────────────────────────────
# Stage 1: preflight
# ────────────────────────────────────────────────────────────────────

def preflight() -> None:
    """日志、工作目录、单实例。"""
    from app_main import setup_logging  # 仍在 app_main 中
    setup_logging()

    from single_instance import ensure_single_instance
    if not ensure_single_instance():
        sys.exit(0)

    os.chdir(str(PROJECT_DIR))
    sys.path.insert(0, str(PROJECT_DIR))

    # Qt DirectWrite
    os.environ.setdefault("QT_ENABLE_DIRECTWRITE", "1")
    _qpa = os.environ.get("QT_QPA_PLATFORM", "windows:darkmode=0")
    if "darkmode=" not in _qpa:
        _qpa += ":darkmode=0"
    os.environ["QT_QPA_PLATFORM"] = _qpa


# ────────────────────────────────────────────────────────────────────
# Stage 2: init_app
# ────────────────────────────────────────────────────────────────────

def init_app(db) -> tuple:
    """创建 QApplication、主题、字体、启动画面。

    Returns:
        (app, splash, icon_path)
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication

    from brand_config_v4 import APP_NAME_FULL
    from database import db as _db
    from startup_splash import StepSplash
    from theme_palette import resolve_theme_name
    from app_main import apply_theme, _shutdown_v9_bridge

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName(APP_NAME_FULL)
    try:
        app.aboutToQuit.connect(_shutdown_v9_bridge)
    except Exception:
        pass

    icon_path = _get_resource_dir() / "assets" / "app_icon.png"
    default_theme = resolve_theme_name(db.get_config("theme"))

    splash = StepSplash(icon_path, theme_name=default_theme)
    splash.show()
    app.processEvents()

    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    splash.pulse("正在加载主题...")
    apply_theme(app, default_theme)
    splash.pulse()
    splash.advance(0)

    splash.pulse("正在加载配置...")
    from production_defaults import apply_production_defaults
    apply_production_defaults(db, force_cloud=True)
    splash.pulse()

    try:
        from permission_system import init_permission_tables
        init_permission_tables()
    except Exception:
        pass

    font = QFont("Microsoft YaHei UI", 13)
    font.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(font)

    splash.advance(1)
    return app, splash, icon_path


# ────────────────────────────────────────────────────────────────────
# Stage 3: gate
# ────────────────────────────────────────────────────────────────────

def gate(app, splash) -> bool:
    """激活 / 锁死 / 登录 / 厂家向导。

    Returns:
        True  → 继续启动
        False → 退出
    """
    from PySide6.QtWidgets import QDialog

    # 激活硬锁
    from license_manager import LicenseManager
    if LicenseManager.is_activation_required():
        from vendor_activation_screen import VendorActivationScreen
        gate_screen = VendorActivationScreen(splash)
        if gate_screen.exec() != QDialog.DialogCode.Accepted:
            return False
    elif not LicenseManager.is_active():
        from license_manager import LicenseExpiredDialog
        dlg = LicenseExpiredDialog(splash)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False

    # 厂家锁死
    try:
        from vendor_lockdown import LOCK_ALL, current_lock_level, lock_message
        if current_lock_level() == LOCK_ALL:
            from ui_helpers import show_error
            show_error(None, "系统已锁定", lock_message(LOCK_ALL))
            return False
    except Exception as exc:
        logger.warning("厂家锁死闸门: %s", exc)

    splash.advance(2)
    splash.hide()
    app.processEvents()

    from permission_system import ensure_authenticated
    if not ensure_authenticated():
        return False

    splash.set_loading_mode()
    splash.show()
    app.processEvents()

    from vendor_gate import is_first_run, current_is_vendor
    from permission_system import PermissionManager
    from ui_helpers import show_warning

    while is_first_run() and not current_is_vendor():
        show_warning(
            None,
            "待厂家完成初始化",
            "本机尚未完成厂家初始化。\n"
            "请用厂家账号登录并跑完向导（含接管门锁），完成后再用酒店账号使用。",
        )
        PermissionManager.logout()
        splash.hide()
        app.processEvents()
        if not ensure_authenticated():
            return False
        splash.show()
        app.processEvents()

    splash.advance(3)
    return True


# ────────────────────────────────────────────────────────────────────
# Stage 4: post_login
# ────────────────────────────────────────────────────────────────────

def post_login(app, splash, icon_path, db) -> None:
    """定时器、主窗口、后台服务、向导。"""
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QIcon

    # ── 定时器 ───────────────────────────────────────────────
    def _check_daily_report():
        now = datetime.now()
        if now.hour < 7:
            return
        last = db.get_config("last_daily_report_date")
        today = now.strftime("%Y-%m-%d")
        if last == today:
            return
        try:
            report = db.build_daily_risk_report()
            from telegram_shadow import telegram_thread
            if telegram_thread.isRunning():
                telegram_thread.send_alert_sync(report.get("report_text", "日报生成完毕"))
            db.set_config("last_daily_report_date", today)
        except Exception as e:
            logger.warning("日报发送失败: %s", e)

    def _check_night_audit():
        now = datetime.now()
        if not (2 <= now.hour <= 3):
            return
        last = db.get_config("last_night_audit_date")
        today = now.strftime("%Y-%m-%d")
        if last == today:
            return
        try:
            report = db.run_night_audit()
            from telegram_shadow import telegram_thread
            if telegram_thread.isRunning():
                telegram_thread.send_alert_sync(report)
            db.set_config("last_night_audit_date", today)
        except Exception as e:
            logger.warning("夜审发送失败: %s", e)

    def _auto_backup():
        now = datetime.now()
        if now.hour < 2:
            return
        last = db.get_config("last_auto_backup_date")
        today = now.strftime("%Y-%m-%d")
        if last == today:
            return
        try:
            from backup_service import auto_backup as encrypted_auto_backup
            result = encrypted_auto_backup(str(PROJECT_DIR / "shadow_guard.db"))
            if result:
                db.set_config("last_auto_backup_date", today)
                logger.info("加密自动备份完成: %s", result)
        except Exception as e:
            logger.warning("自动备份失败: %s", e)

    def _auto_monthly_integrity_pdf():
        now = datetime.now()
        if now.day != 1 or now.hour < 9:
            return
        month_key = now.strftime("%Y-%m")
        if db.get_config("last_integrity_report_month") == month_key:
            return
        try:
            year = now.year if now.month > 1 else now.year - 1
            month = now.month - 1 if now.month > 1 else 12
            from integrity_report import default_output_path, export_integrity_pdf
            ok, path = export_integrity_pdf(year, month, default_output_path(year, month))
            if ok:
                db.set_config("last_integrity_report_month", month_key)
                try:
                    from telegram_shadow import telegram_thread
                    if telegram_thread.isRunning():
                        telegram_thread.send_alert_sync(f"📄 月度诚信报告已生成：{path}")
                except Exception:
                    pass
        except Exception as e:
            logger.warning("月度诚信报告生成失败: %s", e)

    audit_timer = QTimer()
    audit_timer.timeout.connect(_check_daily_report)
    audit_timer.timeout.connect(_check_night_audit)
    audit_timer.timeout.connect(_auto_backup)
    audit_timer.timeout.connect(_auto_monthly_integrity_pdf)
    audit_timer.start(60_000)

    # 离线告警
    def _check_offline_alert(force: bool = False):
        try:
            from vendor_lockdown import get_offline_alert
            alert = get_offline_alert()
        except Exception as exc:
            logger.warning("离线告警检查失败: %s", exc)
            return
        level = alert.get("level", "normal")
        if level == "normal":
            return
        marker_key = f"offline_toast_{level}_shown"
        try:
            if not force and db.get_config(marker_key) == "1":
                return
        except Exception:
            pass
        try:
            win_ref = _main_window_ref
            if win_ref is None or not hasattr(win_ref, "toast"):
                return
            toast_level = alert.get("toast_level", "warning")
            msg = f"{alert.get('title', '')}: {alert.get('message', '')}"
            win_ref.toast.show_toast(msg, dur=0, level=toast_level)
            logger.info("[offline_alert] toast 已弹: level=%s days=%s", level, alert.get("days"))
        except Exception as exc:
            logger.warning("[offline_alert] toast 弹出失败: %s", exc)
        try:
            from event_bus import bus as _bus
            _bus.show_warning.emit(alert.get("title", "离线告警"), alert.get("message", ""))
        except Exception:
            pass
        try:
            db.set_config(marker_key, "1")
        except Exception:
            pass

    QTimer.singleShot(60_000, lambda: _check_offline_alert(force=True))
    offline_alert_timer = QTimer()
    offline_alert_timer.timeout.connect(lambda: _check_offline_alert(force=False))
    offline_alert_timer.start(6 * 3600 * 1000)

    splash.advance(4)
    splash.pulse("正在构建主界面...")

    from main_window_impl import MainWindow
    win = MainWindow()
    splash.pulse("正在打开主界面...")
    global _main_window_ref
    _main_window_ref = win
    if icon_path.exists():
        win.setWindowIcon(QIcon(str(icon_path)))
    win.apply_session_after_login()

    from event_bus import bus
    bus.theme_changed.connect(lambda t: __import__("app_main").apply_theme(app, t, win))

    def _on_hotel_suspended():
        from ui_helpers import show_warning
        show_warning(win, "账号已暂停",
                     "您的酒店账号已被暂停。\n系统将进入只读模式，请联系服务商。")
    bus.hotel_suspended.connect(_on_hotel_suspended)

    from PySide6.QtCore import Qt as _QtCore
    try:
        app.styleHints().setColorScheme(_QtCore.ColorScheme.Light)
        logger.info("ColorScheme forced to Light")
    except Exception:
        pass

    win.show()
    splash.finish(win)

    # 后台服务延后启动
    from app_main import _start_background_services
    QTimer.singleShot(2_000, _start_background_services)

    # setup wizard
    setup_done = db.get_config("setup_done")
    if not setup_done or setup_done != "1":
        def _maybe_wizard():
            try:
                from setup_wizard import SetupWizard
                wizard = SetupWizard(win)
                wizard.exec()
            except Exception as exc:
                logger.warning("向导: %s", exc)
        QTimer.singleShot(400, _maybe_wizard)

    # 期初盘点
    try:
        from vendor_gate import should_block_for_initial_stocktake
        if should_block_for_initial_stocktake():
            from ui_helpers import ask_confirm
            def _force_initial_stocktake():
                try:
                    from initial_stocktake_wizard import open_initial_stocktake_wizard
                    from vendor_gate import is_initial_stocktake_done
                    while not is_initial_stocktake_done():
                        ok = open_initial_stocktake_wizard(win)
                        if ok:
                            break
                        keep = ask_confirm(
                            win, "期初盘点未完成",
                            "你尚未完成期初盘点。\n\n"
                            "未完成前，系统拒绝生成账实差异审计证据，"
                            "也就是说所有库存数字都是『糊涂账』。\n\n"
                            "选 [确定] 重新打开向导继续盘点；\n"
                            "选 [取消] 直接退出，下次启动会再弹。",
                        )
                        if not keep:
                            from PySide6.QtWidgets import QApplication
                            QApplication.instance().quit()
                            return
                except Exception as exc:
                    logger.warning("期初盘点向导异常: %s", exc)
            QTimer.singleShot(800, _force_initial_stocktake)
    except Exception as exc:
        logger.warning("期初盘点闸门: %s", exc)

    # 库存 & 能耗调度器
    def _start_stocktake_scheduler_delayed():
        try:
            from stocktake_scheduler import start_stocktake_scheduler
            start_stocktake_scheduler(win)
        except Exception as exc:
            logger.warning("账实差异调度器启动: %s", exc)
    QTimer.singleShot(2_500, _start_stocktake_scheduler_delayed)

    def _start_energy_scheduler_delayed():
        try:
            from energy_scheduler import start_energy_scheduler
            start_energy_scheduler(win)
        except Exception as exc:
            logger.warning("能耗对账调度器启动: %s", exc)
    QTimer.singleShot(2_700, _start_energy_scheduler_delayed)