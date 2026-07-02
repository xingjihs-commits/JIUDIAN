"""
health_monitor.py — 系统健康监控模块
职责：
  1. 磁盘空间检查
  2. 数据库文件大小监控
  3. 备份状态检查
  4. 数据库完整性校验（PRAGMA integrity_check）
  5. 定时向 Telegram 发送健康报告
  6. 异常阈值告警（磁盘 < 500MB / DB > 500MB / 完整性失败）

架构：健康监控线程 — 每小时检查一次，每天发送日报
"""

import os
import shutil
import datetime
import time
from pathlib import Path

import logging

from PySide6.QtCore import QThread, Signal
from database import db

logger = logging.getLogger(__name__)


def _active_db_file_path() -> str:
    """与当前 `db` 连接一致的数据库文件路径（勿用已废弃的 hotel.db 默认值）。"""
    return str(Path(db.db_path).resolve())


# ── 阈值常量（可通过 system_config 覆盖）──
DISK_WARN_MB   = 500    # 磁盘剩余低于此值触发告警
DB_WARN_MB     = 500    # 数据库文件超过此值触发告警
CHECK_INTERVAL = 3600   # 检查间隔（秒），默认1小时
REPORT_HOUR    = 8      # 每日健康报告发送时刻（小时）


class HealthMonitorThread(QThread):
    """后台健康监控线程，每小时检查一次，每天早上发送健康报告"""

    # 信号：(level, message)  level = 'INFO' | 'WARN' | 'CRITICAL'
    health_alert = Signal(str, str)

    # ── 指南第11项：网络授时防作弊 ──────────────────────────────
    NTP_SERVERS = ["pool.ntp.org", "time.cloudflare.com", "time.google.com"]
    # 指南第12项：断网超过此秒数触发自锁
    OFFLINE_LOCKDOWN_SECONDS = 7200  # 2小时

    offline_detected = Signal()   # 触发主窗口锁定

    def __init__(self):
        super().__init__()
        self.running = True
        self._last_report_date = None
        self._last_check_time = 0
        self._last_online_time = time.time()  # 记录最后一次联网时间
        self._lockdown_triggered = False

    def _sleep_interruptible(self, total_sec: float, step: float = 1.0) -> None:
        """可中断 sleep，便于退出时 stop() 后尽快结束线程（避免占满 30s/60s）。"""
        deadline = time.time() + float(total_sec)
        while self.running and time.time() < deadline:
            time.sleep(min(step, max(0.0, deadline - time.time())))

    def run(self):
        logger.info("[HEALTH] 健康监控线程已启动")
        # 启动后延迟再首次检查，避免与启动期其他线程竞争（可中断）
        self._sleep_interruptible(30)
        while self.running and self.isRunning():
            now = time.time()
            if now - self._last_check_time >= CHECK_INTERVAL:
                self._last_check_time = now
                self._run_checks()
                self._sync_ntp_time()        # 指南第11项：授时校验
            self._check_daily_report()
            self._check_offline_lockdown()   # 指南第12项：断网自锁
            self._sleep_interruptible(60)  # 每分钟轮询一次（检查是否到报告时间）

    # ── 指南第11项：NTP 网络授时 ──────────────────────────────
    def _sync_ntp_time(self):
        """从 NTP 服务器获取标准时间，比较本地时间偏差。
        偏差超过 60 秒则告警（员工可能篡改了系统时间逃避超时审计）。"""
        try:
            import socket
            import struct
            for srv in self.NTP_SERVERS:
                try:
                    # 简单 NTP 查询（不依赖 ntplib 库）
                    NTP_PORT = 123
                    NTP_DELTA = 2208988800  # NTP epoch to Unix epoch
                    msg = b'\x1b' + 47 * b'\0'
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                        sock.settimeout(3)
                        sock.sendto(msg, (srv, NTP_PORT))
                        data, _ = sock.recvfrom(1024)
                    if data:
                        t = struct.unpack('!12I', data)[10]
                        ntp_time = t - NTP_DELTA
                        local_time = time.time()
                        drift = abs(ntp_time - local_time)
                        # 记录最后联网时间
                        self._last_online_time = time.time()
                        self._lockdown_triggered = False
                        if drift > 60:
                            alert = (
                                f"⚠️ [时钟防作弊] 系统时间偏差 {drift:.0f} 秒！\n"
                                f"NTP时间: {datetime.datetime.utcfromtimestamp(ntp_time)}\n"
                                f"本地时间: {datetime.datetime.now()}\n"
                                f"请检查是否有人篡改系统时间以逃避超时房费审计。"
                            )
                            self._send_alert(alert)
                            self.health_alert.emit("WARN", f"时钟偏差 {drift:.0f}s，疑似篡改")
                            logger.warning("[HEALTH-NTP] 时钟偏差告警: %ss", drift)
                        else:
                            logger.info("[HEALTH-NTP] 时钟正常，偏差 %.1fs (via %s)", drift, srv)
                        return  # 成功后退出
                except Exception:
                    continue
            # 所有 NTP 服务器均超时 → 离线
            logger.warning("[HEALTH-NTP] 无法连接 NTP 服务器，可能离线")
        except Exception as e:
            logger.warning("[HEALTH-NTP] 授时检查异常: %s", e)

    # ── 指南第12项：断网超时自锁 ──────────────────────────────
    def _check_offline_lockdown(self):
        """若断网超过 OFFLINE_LOCKDOWN_SECONDS，发出离线锁定信号"""
        try:
            # 简单 TCP 探测（比 NTP 更轻量）
            import socket
            with socket.create_connection(("8.8.8.8", 53), timeout=2):
                self._last_online_time = time.time()
                self._lockdown_triggered = False
        except Exception:
            offline_duration = time.time() - self._last_online_time
            mode = (db.get_config("offline_lockdown_mode") or "warn").lower()
            if offline_duration >= self.OFFLINE_LOCKDOWN_SECONDS and not self._lockdown_triggered:
                self._lockdown_triggered = True
                hours = offline_duration / 3600
                logger.warning("[HEALTH] 断网 %.1f 小时，模式=%s", hours, mode)
                self._send_alert(
                    f"⚠️ <b>网络离线告警</b>\n酒店前台已连续离线 {hours:.1f} 小时。\n"
                    "柬埔寨现场默认只告警不锁机；如需强制锁定，请厂家云端设置 offline_lockdown_mode=lock。"
                )
                if mode == "lock":
                    self.offline_detected.emit()

    def stop(self):
        self.running = False
        try:
            self.requestInterruption()
        except Exception:
            pass

    # ── 核心检查 ──────────────────────────────────────────────

    def _run_checks(self):
        """执行全量健康检查，异常时发送 Telegram 告警"""
        results = self.get_health_status()
        alerts = [r for r in results if r["level"] in ("WARN", "CRITICAL")]
        for alert in alerts:
            msg = (
                f"{'⚠️' if alert['level'] == 'WARN' else '🚨'} "
                f"<b>系统健康告警</b>\n"
                f"📋 项目：{alert['name']}\n"
                f"📊 状态：{alert['detail']}\n"
                f"🕐 时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            self._send_alert(msg)
            self.health_alert.emit(alert["level"], alert["detail"])
            logger.warning("[HEALTH] %s: %s", alert['level'], alert['detail'])

    def _check_daily_report(self):
        """每天固定时刻发送健康日报"""
        try:
            if (db.get_config("health_report_enabled") or "1") != "1":
                return
            now = datetime.datetime.now()
            report_hour = int(db.get_config("health_report_hour") or str(REPORT_HOUR))
            today = now.date()
            if now.hour == report_hour and today != self._last_report_date:
                self._last_report_date = today
                self._send_daily_report()
        except Exception as e:
            logger.warning("[HEALTH] 日报检查异常: %s", e)

    # ── 健康状态采集 ──────────────────────────────────────────

    def get_health_status(self) -> list:
        """
        采集所有健康指标，返回列表：
        [{"name": str, "value": any, "unit": str, "level": "OK"|"WARN"|"CRITICAL", "detail": str}]
        """
        results = []

        # 1. 磁盘空间
        results.append(self._check_disk())

        # 2. 数据库文件大小
        results.append(self._check_db_size())

        # 3. 备份状态
        results.append(self._check_backup())

        # 4. 数据库连通性
        results.append(self._check_db_connectivity())

        # 5. 日志表行数（防止日志膨胀）
        results.append(self._check_log_size())

        # 6. 数据库完整性校验（PRAGMA integrity_check）
        results.append(self._check_db_integrity())

        return results

    def _check_disk(self) -> dict:
        """检查程序所在磁盘的剩余空间"""
        try:
            db_path = _active_db_file_path()
            drive = os.path.splitdrive(db_path)[0] or "."
            usage = shutil.disk_usage(drive or ".")
            free_mb = usage.free / (1024 * 1024)
            total_mb = usage.total / (1024 * 1024)
            used_pct = (usage.used / usage.total) * 100

            warn_mb = float(db.get_config("disk_warn_mb") or DISK_WARN_MB)

            if free_mb < warn_mb:
                level = "CRITICAL" if free_mb < warn_mb / 2 else "WARN"
                detail = f"磁盘剩余 {free_mb:.0f}MB（已用 {used_pct:.1f}%），低于警戒线 {warn_mb:.0f}MB"
            else:
                level = "OK"
                detail = f"磁盘剩余 {free_mb:.0f}MB / 总计 {total_mb:.0f}MB（已用 {used_pct:.1f}%）"

            return {"name": "磁盘空间", "value": free_mb, "unit": "MB",
                    "level": level, "detail": detail}
        except Exception as e:
            return {"name": "磁盘空间", "value": -1, "unit": "MB",
                    "level": "WARN", "detail": f"检查失败: {e}"}

    def _check_db_size(self) -> dict:
        """检查 SQLite 数据库文件大小"""
        try:
            db_path = _active_db_file_path()

            if not os.path.exists(db_path):
                return {"name": "数据库大小", "value": 0, "unit": "MB",
                        "level": "WARN", "detail": f"数据库文件不存在: {db_path}"}

            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            warn_mb = float(db.get_config("db_warn_mb") or DB_WARN_MB)

            if size_mb > warn_mb:
                level = "WARN"
                detail = f"数据库文件 {size_mb:.1f}MB，超过警戒线 {warn_mb:.0f}MB，建议清理历史日志"
            else:
                level = "OK"
                detail = f"数据库文件 {size_mb:.1f}MB（警戒线 {warn_mb:.0f}MB）"

            return {"name": "数据库大小", "value": size_mb, "unit": "MB",
                    "level": level, "detail": detail}
        except Exception as e:
            return {"name": "数据库大小", "value": -1, "unit": "MB",
                    "level": "WARN", "detail": f"检查失败: {e}"}

    def _check_backup(self) -> dict:
        """检查最近备份时间"""
        try:
            row = db.execute(
                "SELECT value FROM system_config WHERE key='last_backup_at'"
            ).fetchone()
            if not row or not row[0]:
                return {"name": "备份状态", "value": None, "unit": "",
                        "level": "WARN", "detail": "从未执行过备份，建议立即备份数据库"}

            last_backup = datetime.datetime.fromisoformat(str(row[0]))
            hours_ago = (datetime.datetime.now() - last_backup).total_seconds() / 3600

            if hours_ago > 48:
                level = "WARN"
                detail = f"最近备份于 {last_backup.strftime('%Y-%m-%d %H:%M')}（{hours_ago:.0f}小时前），建议尽快备份"
            else:
                level = "OK"
                detail = f"最近备份于 {last_backup.strftime('%Y-%m-%d %H:%M')}（{hours_ago:.1f}小时前）"

            return {"name": "备份状态", "value": hours_ago, "unit": "小时",
                    "level": level, "detail": detail}
        except Exception as e:
            return {"name": "备份状态", "value": -1, "unit": "",
                    "level": "WARN", "detail": f"检查失败: {e}"}

    def _check_db_connectivity(self) -> dict:
        """检查数据库读写连通性"""
        try:
            db.execute("SELECT 1").fetchone()
            # 尝试写入测试（写入后立即删除）
            db.execute(
                "INSERT OR REPLACE INTO system_config(key, value) VALUES('_health_ping', ?)",
                (datetime.datetime.now().isoformat(),)
            )
            return {"name": "数据库连通", "value": True, "unit": "",
                    "level": "OK", "detail": "数据库读写正常"}
        except Exception as e:
            return {"name": "数据库连通", "value": False, "unit": "",
                    "level": "CRITICAL", "detail": f"数据库异常: {e}"}

    def _check_db_integrity(self) -> dict:
        """运行 PRAGMA integrity_check 检查数据库文件完整性。

        如果失败，立即触发紧急备份 + CRITICAL 告警。
        """
        try:
            ok, detail = db.check_integrity()
            if ok:
                return {"name": "数据库完整性", "value": True, "unit": "",
                        "level": "OK", "detail": "数据库文件完整（integrity_check 通过）"}
            else:
                # 完整性检查失败 → 立即触发紧急备份
                try:
                    from backup_service import emergency_backup
                    db_path = _active_db_file_path()
                    hotel_id = db.get_config("hotel_id") or ""
                    emergency_backup(db_path, hotel_id, reason=f"完整性校验失败: {detail}")
                except Exception as be:
                    logger.critical("[HEALTH] 紧急备份也失败了: %s", be)
                return {"name": "数据库完整性", "value": False, "unit": "",
                        "level": "CRITICAL", "detail": f"数据库文件损坏: {detail}（已自动触发紧急备份）"}
        except Exception as e:
            return {"name": "数据库完整性", "value": False, "unit": "",
                    "level": "CRITICAL", "detail": f"完整性检查异常: {e}"}

    def _check_log_size(self) -> dict:
        """检查 audit_events 表行数（与 database.log_action 写入表一致），防止日志无限膨胀"""
        try:
            row = db.execute("SELECT COUNT(*) FROM audit_events").fetchone()
            count = row[0] if row else 0
            warn_count = int(db.get_config("log_warn_count") or "100000")

            if count > warn_count:
                level = "WARN"
                detail = f"操作日志已有 {count:,} 条，超过警戒线 {warn_count:,} 条，建议归档清理"
            else:
                level = "OK"
                detail = f"操作日志 {count:,} 条（警戒线 {warn_count:,} 条）"

            return {"name": "日志行数", "value": count, "unit": "条",
                    "level": level, "detail": detail}
        except Exception as e:
            return {"name": "日志行数", "value": -1, "unit": "条",
                    "level": "WARN", "detail": f"检查失败: {e}"}

    # ── 报告发送 ──────────────────────────────────────────────

    def _send_daily_report(self):
        """生成并发送每日健康报告"""
        try:
            results = self.get_health_status()
            hotel = db.get_config("hotel_name") or "酒店"
            today = datetime.date.today().strftime("%Y年%m月%d日")

            lines = [
                f"🏥 <b>{hotel} · 系统健康日报</b>",
                f"📅 {today}",
                "─" * 20,
            ]

            overall = "OK"
            for r in results:
                icon = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🚨"}.get(r["level"], "❓")
                lines.append(f"{icon} <b>{r['name']}</b>：{r['detail']}")
                if r["level"] == "CRITICAL":
                    overall = "CRITICAL"
                elif r["level"] == "WARN" and overall == "OK":
                    overall = "WARN"

            overall_icon = {"OK": "✅ 系统运行正常", "WARN": "⚠️ 存在警告项", "CRITICAL": "🚨 存在严重问题"}.get(overall)
            lines.append("─" * 20)
            lines.append(f"📊 总体状态：{overall_icon}")

            msg = "\n".join(lines)
            self._send_alert(msg)
            logger.info("[HEALTH] 每日健康报告已发送 (%s)", today)
        except Exception as e:
            logger.warning("[HEALTH] 发送日报失败: %s", e)

    def _send_alert(self, text: str):
        """发送 Telegram 告警（直接调用 requests，不依赖 telegram_thread 避免循环导入）"""
        try:
            import requests as _req
            from telegram_bot_config import get_work_bot_token
            token = get_work_bot_token()
            chat_id = db.get_config("telegram_chat_id")
            if not token or not chat_id:
                logger.warning("[HEALTH] Mock Alert: %s", text[:80])
                return
            _req.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=8
            )
        except Exception as e:
            logger.warning("[HEALTH] 告警发送失败: %s", e)

    # ── 公共工具方法（供 UI 调用）────────────────────────────

    def run_manual_check(self) -> str:
        """手动触发检查，返回格式化报告文本（供 debug_panel 调用）"""
        results = self.get_health_status()
        lines = []
        for r in results:
            icon = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🚨"}.get(r["level"], "❓")
            lines.append(f"{icon} {r['name']}: {r['detail']}")
        return "\n".join(lines)

    def cleanup_old_logs(self, keep_days: int = 90) -> int:
        """清理超过 keep_days 天的审计事件（audit_events），返回删除行数"""
        try:
            cutoff = (datetime.datetime.now() - datetime.timedelta(days=keep_days)).isoformat()
            cur = db.execute(
                "DELETE FROM audit_events WHERE created_at < ?", (cutoff,)
            )
            deleted = cur.rowcount if cur else 0
            try:
                cur2 = db.execute(
                    "DELETE FROM door_open_audit WHERE created_at < ?", (cutoff,)
                )
                deleted2 = cur2.rowcount if cur2 else 0
                deleted += deleted2
            except Exception:
                pass
            db.execute(
                "INSERT OR REPLACE INTO system_config(key,value) VALUES('last_log_cleanup_at',?)",
                (datetime.datetime.now().isoformat(),)
            )
            logger.info("[HEALTH] 已清理 %s 条旧日志（>%s天）", deleted, keep_days)
            return deleted
        except Exception as e:
            logger.warning("[HEALTH] 日志清理失败: %s", e)
            return 0


# ── 全局单例 ──────────────────────────────────────────────────
health_monitor = HealthMonitorThread()
