import sys
import os
import time
import threading
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 确定项目目录（兼容 PyInstaller EXE）──
if getattr(sys, 'frozen', False):
    PROJECT_DIR = Path(sys.executable).parent
else:
    PROJECT_DIR = Path(__file__).resolve().parent

_main_window_ref = None
_panic_recovery_inst = None  # BridgeCore PanicRecovery 惰性实例


def get_main_window():
    return _main_window_ref

def get_panic_recovery():
    """获取全局 BridgeCore PanicRecovery 实例。"""
    return _panic_recovery_inst

os.chdir(str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR))


def _get_resource_dir() -> Path:
    """获取只读资源目录（themes/translations 等打包资源）"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return PROJECT_DIR


def setup_logging():
    """Configure structured JSON logging to file and human-readable to console.

    文件日志：JSON 格式，包含 trace_id / span / module，便于日志分析工具解析。
    控制台日志：人类可读格式。
    """
    log_path = PROJECT_DIR / "logs" / "solid.log"
    log_path.parent.mkdir(exist_ok=True)

    # ── JSON 格式（写入文件）──
    class TraceJsonFormatter(logging.Formatter):
        def format(self, record):
            import json
            from datetime import datetime, timezone
            trace_id = "-"
            span = "-"
            try:
                from services.trace_context import current_trace_id, current_span
                trace_id = current_trace_id()
                span = current_span()
            except Exception:
                pass
            log_entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "trace_id": trace_id,
                "span": span,
                "msg": record.getMessage(),
            }
            if record.exc_info and record.exc_info[0]:
                import traceback
                log_entry["exc"] = traceback.format_exception(*record.exc_info)
            return json.dumps(log_entry, ensure_ascii=False)

    # ── 人类可读格式（控制台）──
    class ConsoleTraceFormatter(logging.Formatter):
        def format(self, record):
            trace_id = "-"
            try:
                from services.trace_context import current_trace_id
                tid = current_trace_id()
                if tid != "-":
                    trace_id = tid[:20]
            except Exception:
                pass
            return f"{self.formatTime(record)} [{record.levelname}] [{trace_id}] {record.name}: {record.getMessage()}"

    file_handler = RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5,
        encoding="utf-8", delay=True,
    )
    file_handler.setFormatter(TraceJsonFormatter())

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ConsoleTraceFormatter())

    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler],
    )


_QSS_CACHE: dict[str, str] = {}


def apply_theme(app, theme_name, window=None):
    from theme_palette import resolve_theme_name

    theme_name = resolve_theme_name(theme_name)

    import solid_ui_v2.runtime as v2rt
    v2rt.set_theme_resolver(lambda: theme_name)
    v2rt.invalidate_cache()

    import solid_ui_v2.qss as v2qss
    qss = v2qss.compile_theme(theme_name)
    _QSS_CACHE[theme_name] = qss
    logger.info("Solid UI compiled %s QSS: %d bytes", theme_name, len(qss))
    app.setStyleSheet(qss)

    from smart_header import render_smart_header_qss
    try:
        app.setStyleSheet(app.styleSheet() + "\n" + render_smart_header_qss())
    except Exception:
        logger.warning("SmartHeader QSS failed", exc_info=True)

    from ui_helpers import apply_app_light_chrome
    apply_app_light_chrome(app, theme_name)
    _refresh_chrome_widgets(window)


def _refresh_chrome_widgets(window=None) -> None:
    """主题切换后刷新仍带 inline 样式或需 re-polish 的壳层组件。"""
    if window is not None:
        from ui_helpers import apply_windows_light_title_bar
        apply_windows_light_title_bar(window)
    if window is None:
        return
    bar = getattr(window, "status_bar", None)
    if bar is not None and hasattr(bar, "refresh_theme"):
        try:
            bar.refresh_theme()
        except Exception:
            logger.debug("status_bar refresh_theme failed", exc_info=True)
    ws = getattr(window, "workspace", None)
    ct = getattr(ws, "checkin_tab", None) if ws is not None else None
    if ct is not None and hasattr(ct, "_on_theme_changed"):
        try:
            ct._on_theme_changed()
        except Exception:
            logger.debug("checkin_tab theme surfaces refresh failed", exc_info=True)
    sh = getattr(window, "smart_header", None)
    if sh is not None and hasattr(sh, "refresh_theme"):
        try:
            sh.refresh_theme()
        except Exception:
            logger.debug("smart_header refresh_theme failed", exc_info=True)
    ws = getattr(window, "workspace", None)
    if ws is not None and hasattr(ws, "_on_workspace_theme_changed"):
        try:
            ws._on_workspace_theme_changed()
        except Exception:
            logger.debug("workspace theme refresh failed", exc_info=True)
    tv = getattr(window, "timeline_view", None)
    if tv is not None and hasattr(tv, "refresh_theme"):
        try:
            tv.refresh_theme()
        except Exception:
            logger.debug("timeline_view refresh_theme failed", exc_info=True)
    rm = getattr(window, "room_matrix", None)
    if rm is not None:
        try:
            from ui_surface import fd_apply_scroll_area, fd_refresh_surfaces

            fd_refresh_surfaces(rm)
            scroll = getattr(rm, "scroll", None)
            if scroll is not None:
                fd_apply_scroll_area(scroll)
        except Exception:
            logger.debug("room_matrix theme surfaces refresh failed", exc_info=True)


def _shutdown_v9_bridge() -> None:
    """让 V9 发卡器 DLL 释放 USB 句柄、关掉 32 位桥子进程。

    必须在程序退出路径上调用至少一次：否则下次开机时 Windows 会把
    proUSB 发卡器识别成 VID_0000 死设备，必须靠 _rescue_v9_usb 脚本
    才能复活。bridge_client 内部对 close_usb 是幂等的，调多次无副作用。
    """
    try:
        from lock_adapters.bridge_client import shutdown_bridge
        shutdown_bridge()
    except Exception as exc:
        logger.warning("V9 bridge 关闭: %s", exc)


def _shutdown_worker_threads() -> None:
    try:
        from health_monitor import health_monitor
        health_monitor.stop()
        if not health_monitor.wait(12_000):
            health_monitor.terminate()
            health_monitor.wait(2_000)
    except Exception as exc:
        logger.warning("健康监控线程停止: %s", exc)
    try:
        from telegram_shadow import telegram_thread
        telegram_thread.request_stop()
        if not telegram_thread.wait(6_000):
            telegram_thread.terminate()
            telegram_thread.wait(1_500)
    except Exception as exc:
        logger.warning("Telegram 线程停止: %s", exc)
    try:
        from heartbeat_service import heartbeat_service
        heartbeat_service.request_stop()
    except Exception:
        pass
    try:
        from task_queue import task_queue
        task_queue.request_stop()
    except Exception:
        pass
    # 退出前必须释放 V9 USB 句柄：放在最后调用，确保即使前面线程停顿
    # 也仍然走得到。
    _shutdown_v9_bridge()


def _start_background_services() -> None:
    """主窗口显示后再启动后台，避免挡住首屏。"""
    try:
        from telegram_shadow import telegram_thread
        from heartbeat_service import heartbeat_service
        from task_queue import task_queue
        from audit_engine import AuditEngine
        from health_monitor import health_monitor

        AuditEngine()
        if not telegram_thread.isRunning():
            telegram_thread.start()
        if not heartbeat_service.is_alive():
            heartbeat_service.start()
        if not task_queue.is_alive():
            task_queue.start()
        if not health_monitor.isRunning():
            health_monitor.start()
    except Exception as exc:
        logger.warning("后台服务启动: %s", exc)

    # 门锁发卡器保活（每 30 秒调一次 initializeUSB 防止固件超时）
    # BridgeCore Injector 回放期间可暂停保活，避免干扰
    # PanicRecovery 在通信失败时自动触发软复位/断电
    try:
        from lock_adapters.bridge_client import get_bridge, RflBridge

        # ── PanicRecovery 惰性初始化 ────────────────────────────
        def _init_panic_recovery():
            global _panic_recovery_inst
            if _panic_recovery_inst is not None:
                return
            try:
                from bridgecore.panic_recovery import PanicRecovery
                bridge = get_bridge()
                _panic_recovery_inst = PanicRecovery(bridge)
                logger.info("[PanicRecovery] 已初始化，阈值=%d",
                            _panic_recovery_inst._soft_reset_threshold)
            except Exception as exc:
                logger.warning("PanicRecovery 初始化跳过: %s", exc)

        def _lock_keepalive_loop():
            while True:
                time.sleep(30)
                try:
                    if RflBridge.is_keepalive_paused():
                        continue
                    bridge = get_bridge()
                    if bridge and bridge.dll_loaded and bridge.is_running():
                        bridge.keepalive()
                except Exception:
                    # 保活失败 → 通知 PanicRecovery（由它决定是否触发恢复）
                    pr = _panic_recovery_inst
                    if pr is not None:
                        try:
                            pr.notify_failure("timeout", triggered_by="keepalive")
                        except Exception:
                            pass

        _ka = threading.Thread(target=_lock_keepalive_loop, name="lock-keepalive", daemon=True)
        _ka.start()

        # 保活线程启动后初始化 PanicRecovery（此时 get_bridge() 已可用）
        _init_panic_recovery()
    except Exception as exc:
        logger.warning("门锁保活线程启动: %s", exc)

    try:
        from power_controller_config import ensure_power_config_initialized
        ensure_power_config_initialized()
    except Exception as exc:
        logger.warning("取电配置: %s", exc)

    try:
        from local_adapter import init_cloud_connection
        init_cloud_connection()
    except Exception as exc:
        logger.warning("云端初始化: %s", exc)

    # 桌面快捷方式仅由安装包创建，避免与 Inno 重复出现两个图标

    def _refresh_live_mdb_background() -> None:
        try:
            from database import db
            if db.get_config("lock_takeover_done_at"):
                from vendor_gate import maybe_refresh_live_mdb_on_startup
                maybe_refresh_live_mdb_on_startup()
        except Exception as exc:
            logger.warning("活MDB日切刷新失败: %s", exc)

    threading.Thread(
        target=_refresh_live_mdb_background,
        name="solid-live-mdb-refresh",
        daemon=True,
    ).start()


def main():
    """Solid PMS 主入口 — 委托给 services.bootstrap 四个阶段。"""
    from services.bootstrap import preflight, init_app, gate, post_login

    preflight()

    from database import db

    app, splash, icon_path = init_app(db)

    if not gate(app, splash):
        sys.exit(0)

    post_login(app, splash, icon_path, db)

    ret = app.exec()
    _shutdown_worker_threads()
    sys.exit(ret)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        # 先尝试写日志（可能日志系统还没初始化）
        try:
            logger.critical("启动崩溃: %s\n%s", exc, tb)
        except Exception:
            pass
        # Show a message box so the user knows what happened
        try:
            from ui_helpers import show_error
            qapp = QApplication.instance() or QApplication(sys.argv)
            show_error(
                None,
                "Solid 启动失败",
                f"系统启动时发生严重错误，程序即将退出。\n\n错误信息：{exc}\n\n"
                f"请将日志文件发送给厂家技术人员。\n日志位置：{Path(sys.executable if getattr(sys, 'frozen', False) else __file__).parent}/logs/solid.log"
            )
        except Exception:
            pass
        sys.exit(1)
