"""
HeartbeatService — 云端心跳服务
每 5 分钟向云服务发送一次心跳，检查远程开关与酒店状态。

改进点（v1.1）：
  - 指数退避重试：失败后 30s → 60s → 120s → 240s → 300s（上限）
  - 连续失败计数：超过阈值（默认 5 次）通过 event_bus 发出 heartbeat_failed 告警
  - 成功后重置退避计数
  - 所有异常均有详细日志
"""
import json
import logging
import time
import threading
from pathlib import Path

import requests
from database import db

logger = logging.getLogger(__name__)

# 退避序列（秒）：第 1 次失败等 30s，第 2 次 60s，以此类推，最大 300s
_BACKOFF_SCHEDULE = [30, 60, 120, 240, 300]
# 连续失败超过此次数时触发告警信号
_FAIL_ALERT_THRESHOLD = 5
# 正常心跳间隔（秒）
_NORMAL_INTERVAL = 300


class HeartbeatService(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True
        self._fail_count = 0          # 连续失败次数
        self._alert_sent = False      # 是否已发出告警（避免重复告警）

    def run(self):
        while self.running:
            success = self._send_heartbeat()
            if success:
                self._fail_count = 0
                self._alert_sent = False
                self._sleep(_NORMAL_INTERVAL)
            else:
                self._fail_count += 1
                # 连续失败超过阈值，发出告警（只发一次，直到恢复）
                if self._fail_count >= _FAIL_ALERT_THRESHOLD and not self._alert_sent:
                    self._alert_sent = True
                    self._emit_alert()
                # 指数退避等待
                backoff_idx = min(self._fail_count - 1, len(_BACKOFF_SCHEDULE) - 1)
                wait_secs = _BACKOFF_SCHEDULE[backoff_idx]
                logger.warning("[HEARTBEAT] 连续失败 %d 次，%ds 后重试", self._fail_count, wait_secs)
                self._sleep(wait_secs)

    def _sleep(self, seconds: int):
        """可中断的 sleep，每秒检查一次 running 标志"""
        for _ in range(seconds):
            if not self.running:
                break
            time.sleep(1)

    def _send_heartbeat(self) -> bool:
        """
        发送心跳请求（POST JSON），携带运营数据。
        返回成功（HTTP 200）为真值，任何失败为假值。
        """
        try:
            hotel_id = db.get_config("hotel_id")
            if not hotel_id:
                return True

            cloud_worker_url = db.get_config("cloud_worker_url") or ""
            if not cloud_worker_url:
                return True

            url = f"{cloud_worker_url.rstrip('/')}/api/hotel-poll"
            body = self._build_heartbeat_body(hotel_id)
            # 传输加密标记（待云端实现后启用）
            res = requests.post(
                url,
                json=body,
                timeout=10,
            )

            if res.status_code == 200:
                data = res.json()
                try:
                    from telegram_bot_config import apply_cloud_poll_response
                    apply_cloud_poll_response(data)
                except Exception:
                    pass

                # 检查 kill_switch
                if data.get("kill_switch"):
                    logger.warning("[HEARTBEAT] 收到 kill_switch 指令")
                    from event_bus import bus
                    bus.kill_switch_triggered.emit()

                # 检查 hotel_status
                hotel_status = data.get("hotel_status", "active")
                if hotel_status == "suspended":
                    logger.warning("[HEARTBEAT] 酒店账号已被暂停")
                    from event_bus import bus
                    bus.hotel_suspended.emit()

                # ── 处理云端下发的通知（含广告推送）──
                notifications = data.get("notifications", [])
                if notifications:
                    try:
                        from manufacturer_comm import ManufacturerCommService
                        worker_url = db.get_config("cloud_worker_url") or ""
                        ManufacturerCommService.process_notifications(notifications, worker_url)
                    except Exception as e:
                        logger.warning("[HEARTBEAT] 处理通知失败: %s", e)

                # ── 处理批量远程指令 ──
                remote_commands = data.get("remote_commands", [])
                if remote_commands:
                    self._process_remote_commands(remote_commands)

                # ── 处理版本白名单/黑名单更新 ──
                version_policy = data.get("version_policy")
                if version_policy:
                    self._apply_version_policy(version_policy)

                # ── 处理酒店分组信息更新 ──
                hotel_group = data.get("hotel_group")
                if hotel_group:
                    self._apply_hotel_group(hotel_group)

                # ── 执行待推送广告 ──
                pending_ad = db.get_config("pending_ad_text") or ""
                if pending_ad:
                    try:
                        from telegram_shadow import telegram_thread
                        sent = telegram_thread.broadcast_ad(pending_ad)
                        db.set_config("pending_ad_text", "")
                        db.log_action("SYSTEM", "AD_BROADCAST_DONE", f"已广播给{sent}位客人")
                        logger.info("[HEARTBEAT] 广告已广播给 %d 位客人", sent)
                    except Exception as e:
                        logger.error("[HEARTBEAT] 广告广播失败: %s", e)

                return True

            else:
                logger.warning("[HEARTBEAT] 服务器返回非 200 状态码: %s", res.status_code)
                return False

        except requests.exceptions.Timeout:
            logger.warning("[HEARTBEAT] 请求超时（10s）")
            return False
        except requests.exceptions.ConnectionError as e:
            logger.warning("[HEARTBEAT] 网络连接失败: %s", e)
            return False
        except Exception as e:
            logger.error("[HEARTBEAT] 未知错误: %s", e)
            return False

    def _build_heartbeat_body(self, hotel_id: str) -> dict:
        """构造包含运营数据的心跳请求体。"""
        body = {
            "hotel_id": hotel_id,
            "hotel_name": db.get_config("hotel_name") or "",
            "region": db.get_config("region") or "",
            "app_version": db.get_config("app_version") or "1.0.0",
            "salesperson_id": db.get_config("salesperson_id") or "",
            "group_id": db.get_config("hotel_group_id") or "",
        }

        try:
            ops = _collect_ops_data()
            body["ops"] = ops
        except Exception as e:
            logger.warning("[HEARTBEAT] 运营数据采集失败: %s", e)
            body["ops"] = {}

        return body

    def _process_remote_commands(self, commands: list) -> None:
        """处理云端下发的批量远程指令。

        支持的指令类型：
          - RESTART_APP      重启应用
          - CLEAR_CACHE      清除缓存
          - SEND_ALERT       发送系统告警
          - LOCK_LEVEL       设置锁死级别
          - PUSH_AD          推送广告
          - SYNC_NOW         立即同步离线操作
          - DIAG_SNAPSHOT    请求诊断快照
        """
        import json as _json
        for cmd in commands:
            if not isinstance(cmd, dict):
                continue
            cmd_type = (cmd.get("type") or "").upper().strip()
            cmd_id = cmd.get("command_id", "")
            logger.info("[HEARTBEAT] 收到远程指令: %s (id=%s)", cmd_type, cmd_id)

            try:
                if cmd_type == "RESTART_APP":
                    db.log_action("CLOUD", "REMOTE_CMD_RESTART", f"command_id={cmd_id}")
                    from PySide6.QtCore import QTimer
                    from PySide6.QtWidgets import QApplication
                    app = QApplication.instance()
                    if app:
                        QTimer.singleShot(2000, app.quit)

                elif cmd_type == "CLEAR_CACHE":
                    db.log_action("CLOUD", "REMOTE_CMD_CLEAR_CACHE", f"command_id={cmd_id}")
                    _clear_app_cache()

                elif cmd_type == "SEND_ALERT":
                    msg = cmd.get("message", "")
                    level = cmd.get("level", "INFO")
                    db.log_action("CLOUD", "REMOTE_CMD_ALERT", msg)
                    try:
                        from event_bus import bus
                        bus.show_warning.emit("厂家通知", msg)
                    except Exception:
                        pass

                elif cmd_type == "LOCK_LEVEL":
                    level = cmd.get("lock_level", "")
                    from vendor_lockdown import sync_lock_level
                    sync_lock_level(level, source="cloud_remote_cmd")
                    db.log_action("CLOUD", "REMOTE_CMD_LOCK", f"level={level}")

                elif cmd_type == "PUSH_AD":
                    ad_text = cmd.get("ad_text", "")
                    db.set_config("pending_ad_text", ad_text)
                    db.log_action("CLOUD", "REMOTE_CMD_PUSH_AD", f"text_len={len(ad_text)}")

                elif cmd_type == "SYNC_NOW":
                    db.log_action("CLOUD", "REMOTE_CMD_SYNC", "triggered")
                    try:
                        from offline_queue import sync_offline_operations
                        sync_offline_operations()
                    except Exception as e:
                        logger.warning("[HEARTBEAT] 离线同步失败: %s", e)

                elif cmd_type == "DIAG_SNAPSHOT":
                    db.log_action("CLOUD", "REMOTE_CMD_DIAG", f"command_id={cmd_id}")
                    try:
                        from remote_diag import get_full_diagnosis
                        snapshot = get_full_diagnosis()
                        self._upload_diag_snapshot(cmd.get("return_url", ""), snapshot, cmd_id)
                    except Exception as e:
                        logger.warning("[HEARTBEAT] 诊断快照失败: %s", e)

                else:
                    logger.warning("[HEARTBEAT] 未知远程指令类型: %s", cmd_type)

            except Exception as e:
                logger.error("[HEARTBEAT] 执行远程指令失败: %s → %s", cmd_type, e)

    def _upload_diag_snapshot(self, return_url: str, snapshot: dict, command_id: str) -> None:
        if not return_url:
            return
        try:
            import json as _json
            res = requests.post(
                return_url,
                json={"command_id": command_id, "snapshot": snapshot},
                timeout=15,
            )
            logger.info("[HEARTBEAT] 诊断快照已上传: status=%s", res.status_code)
        except Exception as e:
            logger.warning("[HEARTBEAT] 诊断快照上传失败: %s", e)

    def _apply_version_policy(self, policy: dict) -> None:
        """写入版本白名单/黑名单到本地配置。"""
        whitelist = policy.get("whitelisted_versions", [])
        blacklist = policy.get("blacklisted_versions", [])
        db.set_config("version_whitelist", ",".join(whitelist))
        db.set_config("version_blacklist", ",".join(blacklist))
        current = db.get_config("app_version") or "1.0.0"
        if blacklist and current in blacklist:
            db.set_config("update_blocked", "1")
            db.set_config("update_block_reason",
                           f"版本 {current} 已被厂家拉黑")
            logger.warning("[HEARTBEAT] 当前版本 %s 在黑名单中", current)

    def _apply_hotel_group(self, group: dict) -> None:
        """接收并保存酒店分组信息。

        Cloud Worker 下发的分组数据结构：
        {
            "group_id": "group_001",
            "group_name": "金边旗舰店组",
            "hotels": ["HTL001", "HTL002"],
            "owner_telegram_chat_id": "123456",
        }
        """
        db.set_config("hotel_group_id", group.get("group_id", ""))
        db.set_config("hotel_group_name", group.get("group_name", ""))
        db.set_config("hotel_group_data", json.dumps(group, ensure_ascii=False))
        logger.info("[HEARTBEAT] 酒店分组已更新: %s", group.get("group_name", ""))

    def _emit_alert(self):
        """连续失败超过阈值时，通过 event_bus 发出告警"""
        try:
            logger.warning("[HEARTBEAT] 连续失败 %d 次，触发告警信号", self._fail_count)
            from event_bus import bus
            # 使用通用 show_warning 信号通知主窗口
            bus.show_warning.emit(
                "云端连接异常",
                f"心跳服务已连续失败 {self._fail_count} 次，无法连接到云端服务器。\n"
                "请检查网络连接或云服务地址配置。\n"
                "系统将继续重试，功能不受影响。"
            )
            # 同时写入审计日志
            try:
                db.log_action("SYSTEM", "HEARTBEAT_FAIL_ALERT", f"连续失败{self._fail_count}次")
            except Exception:
                pass
        except Exception as e:
            logger.error("[HEARTBEAT] 发出告警信号失败: %s", e)

    def request_stop(self):
        self.running = False


def _collect_ops_data() -> dict:
    """采集运营数据：出租率、单房收入、平均房价。

    从数据库查询今日实时数据，不上传明细。
    """
    import datetime as _dt
    today = _dt.date.today().isoformat()
    ops = {
        "date": today,
        "occupancy_pct": 0.0,
        "revpar": 0.0,
        "adr": 0.0,
        "total_rooms": 0,
        "inhouse_rooms": 0,
        "revenue_today": 0.0,
        "checkins_today": 0,
        "checkouts_today": 0,
    }
    try:
        total = db.execute("SELECT COUNT(*) FROM rooms").fetchone()
        if total and total[0] > 0:
            ops["total_rooms"] = total[0]
            inhouse = db.execute(
                "SELECT COUNT(*) FROM rooms WHERE status='INHOUSE'"
            ).fetchone()
            ops["inhouse_rooms"] = inhouse[0] if inhouse else 0
            if ops["total_rooms"] > 0:
                ops["occupancy_pct"] = round(
                    (ops["inhouse_rooms"] / ops["total_rooms"]) * 100, 1
                )
            if ops["inhouse_rooms"] > 0:
                ops["revpar"] = round(
                    ops.get("revenue_today", 0) / ops["total_rooms"], 2
                )
                ops["adr"] = round(
                    ops.get("revenue_today", 0) / ops["inhouse_rooms"], 2
                )

        from database import LEDGER_REVENUE_TX_TYPES, _sql_in_types
        inc = _sql_in_types(LEDGER_REVENUE_TX_TYPES)
        rev = db.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM ledger "
            f"WHERE date(created_at)=? AND tx_type IN ({inc})",
            (today,),
        ).fetchone()
        ops["revenue_today"] = round(float(rev[0] if rev else 0), 2)

        ci = db.execute(
            "SELECT COUNT(*) FROM ledger WHERE date(created_at)=? AND tx_type='ROOM_IN'",
            (today,),
        ).fetchone()
        ops["checkins_today"] = ci[0] if ci else 0

        co = db.execute(
            "SELECT COUNT(*) FROM ledger WHERE date(created_at)=? AND tx_type='ROOM_OUT'",
            (today,),
        ).fetchone()
        ops["checkouts_today"] = co[0] if co else 0

    except Exception:
        pass
    return ops


def _clear_app_cache() -> None:
    """清除应用缓存目录。"""
    import sys as _sys
    try:
        app_dir = Path(sys.executable).parent if getattr(_sys, 'frozen', False) else Path(__file__).parent
        cache_dirs = ["__pycache__", ".mypy_cache", "logs/cache"]
        for cdir in cache_dirs:
            p = app_dir / cdir
            if p.exists() and p.is_dir():
                import shutil as _shutil
                _shutil.rmtree(p, ignore_errors=True)
        logger.info("[HEARTBEAT] 缓存已清除")
    except Exception as e:
        logger.warning("[HEARTBEAT] 清除缓存失败: %s", e)




def heartbeat_once() -> str:
    """厂家控制台手动触发一次云端心跳（不启动后台线程）。"""
    worker = (db.get_config("cloud_worker_url") or "").strip()
    if not worker:
        return "未配置云端 Worker 地址"
    hotel_id = (db.get_config("hotel_id") or "").strip()
    if not hotel_id:
        return "未配置 hotel_id（请先完成激活/装机）"
    if (db.get_config("cloud_enabled") or "0") != "1":
        return "云端对接未启用（设置里勾选启用云端）"

    svc = HeartbeatService()
    if svc._send_heartbeat():
        return "心跳成功"
    return "心跳失败（请检查网络与 Worker 地址）"


heartbeat_service = HeartbeatService()
