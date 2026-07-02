"""
======================================================
ShadowGuard - 本地端云端对接适配器 (v2.0)
取代原有的轮询模式，
本地端主动每 N 秒轮询云 API，
拉取远程新订单 / 服务请求 / 远程锁机指令。

使用方法:
    1. 将本文件放在项目目录下
    2. 在入口程序前调用:
       from local_adapter import init_cloud_connection
       init_cloud_connection()
    3. 在设置页面填入 Worker 地址

可配置项 (在 config 表中):
    cloud_worker_url            - Cloudflare Worker 地址 (必填，否则离线模式)
    cloud_enabled               - 1=启用云端 / 0=仅本地运行
    cloud_poll_interval         - 轮询间隔秒数，默认3，可调1~60
    cloud_max_consecutive_fail  - 连续失败多少次后降级，默认10
    cloud_degraded_interval     - 降级后的长轮询间隔秒数，默认30
    cloud_idle_after_empty_polls - 连续空轮询多少次后拉长间隔，默认8
    cloud_idle_max_interval     - 无通知时最长轮询间隔秒数，默认45
======================================================
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
import threading
import time
import uuid as _uuid
from typing import Any, Dict, Optional

import requests

from cloud_security import (
    get_client_secret,
    signed_get_json,
    signed_post_json,
    verify_notification_signature,
)
import logging

from database import db
from event_bus import bus

logger = logging.getLogger(__name__)

# ================================================================
# 可调默认值 (优先从 config 表读取，读不到用此默认)
# ================================================================
_DEFAULT_POLL_INTERVAL = 3          # 秒
_DEFAULT_MAX_FAIL = 10              # 连续失败多少次后降级
_DEFAULT_DEGRADED_INTERVAL = 30     # 降级后长轮询间隔
_DEFAULT_IDLE_AFTER_EMPTY = 8       # 连续无通知轮询次数后进入闲时拉长
_DEFAULT_IDLE_MAX_INTERVAL = 45     # 闲时最长间隔（秒）
_DEFAULT_CLOUD_ENABLED = 0          # 默认离线，需手动开

# ================================================================
# 安全白名单配置
# ================================================================
# 允许远程修改的配置项白名单（防止恶意配置覆盖）
CONFIG_WHITELIST = {
    "language_code",           # 语言设置
    "remote_boss_dashboard_url",  # 老板看板URL
    "api_camera_endpoint",     # 摄像头API
    "hotel_name",              # 酒店名称
    "cloud_poll_interval",     # 轮询间隔
    "cloud_enabled",           # 云端开关
    "lock_level",              # 厂家锁死级别
    # 危险配置项不在白名单中：kill_switch_date, cloud_worker_url, license_key
}

# 允许的远程指令类型白名单
COMMAND_WHITELIST = {
    "remote_order",      # 远程订单
    "kill_switch",       # 锁机指令（需特殊验证）
    "hotel_config",      # 配置更新
    "status_check",      # 状态检查
    "ad_push",           # 厂家广告投放
    "set_ad_signature",  # 广告签名更新
    "lock_level",        # 5级锁死/恢复
    "remote_pull_logs",
    "remote_diagnose",
    "remote_reload",
    "remote_ota_check",
    "remote_vendor_msg",
}

# ================================================================
# 运行时全局变量
# ================================================================
CLOUD_WORKER_URL: str = ""
HOTEL_ID: str = ""


# ================================================================
# 配置读取辅助
# ================================================================
def _cfg(key: str, default: Any = "") -> str:
    """从本地 db config 表读取配置，失败返回默认值"""
    try:
        val = db.get_config(key)
        if val is None:
            return str(default)
        return str(val)
    except Exception:
        return str(default)


def _cfg_int(key: str, default: int = 0) -> int:
    try:
        return int(_cfg(key, str(default)))
    except (ValueError, TypeError):
        return default


# ================================================================
# CloudPollThread — 增强版轮询线程
# ================================================================
class CloudPollThread(threading.Thread):
    """
    每 poll_interval 秒轮询 Worker API，拉取待处理通知。

    容错策略:
      - 连续失败 < max_consecutive_fail 次 → 正常间隔
      - 连续失败 >= max_consecutive_fail 次 → 自动降级为长间隔
      - 一旦成功一次 → 恢复为正常间隔
      - 网络不可用时静默运行，不崩溃，不停前台
    """

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.running = True
        self._registered = False
        self._consecutive_fail_count = 0
        self._empty_poll_streak = 0

    # ── 属性: 当前应该用哪个间隔 ──
    @property
    def poll_interval(self) -> int:
        normal = _cfg_int("cloud_poll_interval", _DEFAULT_POLL_INTERVAL)
        if normal < 1:
            normal = 1
        if normal > 60:
            normal = 60
        return normal

    @property
    def max_fail(self) -> int:
        return _cfg_int("cloud_max_consecutive_fail", _DEFAULT_MAX_FAIL)

    @property
    def degraded_interval(self) -> int:
        return _cfg_int("cloud_degraded_interval", _DEFAULT_DEGRADED_INTERVAL)

    @property
    def idle_after_empty(self) -> int:
        return max(3, _cfg_int("cloud_idle_after_empty_polls", _DEFAULT_IDLE_AFTER_EMPTY))

    @property
    def idle_max_interval(self) -> int:
        v = _cfg_int("cloud_idle_max_interval", _DEFAULT_IDLE_MAX_INTERVAL)
        return max(self.poll_interval, min(v, 120))

    def _current_interval(self) -> int:
        if self._consecutive_fail_count >= self.max_fail:
            return self.degraded_interval
        base = self.poll_interval
        if self._empty_poll_streak >= self.idle_after_empty:
            steps = self._empty_poll_streak // self.idle_after_empty
            stretched = min(self.idle_max_interval, base + steps * 5)
            return max(base, stretched)
        return base

    # ── 主循环 ──
    def run(self) -> None:
        global CLOUD_WORKER_URL, HOTEL_ID

        logger.info("[CLOUD] CloudPollThread starting ...")
        time.sleep(2)  # 等主窗口初始化完

        if not CLOUD_WORKER_URL:
            logger.debug("[CLOUD] 未配置 Worker 地址，线程进入离线待命模式（后续可通过设置页面开启）。")
            while self.running:
                if CLOUD_WORKER_URL:
                    break
                time.sleep(5)
            if not self.running:
                return

        # 注册 + 首次状态检查
        self._ensure_registered()
        self._check_hotel_status()

        while self.running:
            try:
                if CLOUD_WORKER_URL and HOTEL_ID:
                    self._poll_notifications()
                else:
                    time.sleep(5)
                    continue
            except Exception as exc:
                logger.warning("[CLOUD] Poll loop error: %s", exc)
                self._fail_tick()
                time.sleep(max(self._current_interval(), 1))
            else:
                time.sleep(self._current_interval())

    # ── 拉取通知 ──
    def _poll_notifications(self) -> None:
        global CLOUD_WORKER_URL, HOTEL_ID
        if not CLOUD_WORKER_URL or not HOTEL_ID:
            return

        url = f"{CLOUD_WORKER_URL.rstrip('/')}/api/hotel-poll"
        try:
            res = signed_get_json(
                url,
                params={"hotel_id": HOTEL_ID},
                timeout=10,
            )
        except requests.RequestException:
            self._fail_tick()
            return

        if res.status_code != 200:
            self._fail_tick()
            return

        self._consecutive_fail_count = 0  # 成功一次就复位
        data = res.json()
        try:
            from vendor_lockdown import mark_cloud_seen, sync_lock_level
            mark_cloud_seen()
            sync_lock_level(data.get("lock_level", ""), source="cloud_poll")
        except Exception:
            pass
        try:
            from telegram_bot_config import apply_cloud_poll_response
            apply_cloud_poll_response(data)
        except Exception:
            pass
        notifications = data.get("notifications", [])

        if notifications:
            self._empty_poll_streak = 0
        else:
            self._empty_poll_streak += 1
            if self._empty_poll_streak == self.idle_after_empty:
                logger.info(
                    "[CLOUD] 连续%s次无新通知，"
                    "轮询间隔拉长至约%ss（传话模式降频）。",
                    self.idle_after_empty,
                    self._current_interval(),
                )

        for notif in notifications:
            self._handle_notification(notif)
            ack_url = f"{CLOUD_WORKER_URL.rstrip('/')}/api/ack"
            try:
                signed_post_json(ack_url, {"notify_id": notif.get("notify_id", "")}, timeout=5)
            except requests.RequestException:
                pass

    # ── 失败计数 ──
    def _fail_tick(self) -> None:
        self._consecutive_fail_count += 1
        if self._consecutive_fail_count == self.max_fail:
            logger.warning("[CLOUD] 连续%s次失败，自动降级为%ss长轮询。", self.max_fail, self.degraded_interval)
        elif self._consecutive_fail_count > self.max_fail and self._consecutive_fail_count % 50 == 0:
            logger.warning("[CLOUD] 仍在长轮询模式下运行，已累计%s次失败。", self._consecutive_fail_count)

    # ── 分类处理各类型通知 ──
    def _handle_notification(self, notif: Dict[str, Any]) -> None:
        if not verify_notification_signature(notif):
            logger.warning("[CLOUD] 拒绝签名无效的云端通知。")
            return
        notif_type = notif.get("notify_type", "")
        notify_id = str(notif.get("notify_id") or notif.get("id") or "").strip()
        if notify_id and db.notification_processed(notify_id):
            logger.info("[CLOUD] 跳过已处理通知: %s", notify_id)
            return
        payload: Dict[str, Any] = {}
        payload_str = notif.get("payload_json", "{}")
        if isinstance(payload_str, str):
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                payload = {}
        elif isinstance(payload_str, dict):
            payload = payload_str

        handler_map = {
            "NEW_ORDER": self._handle_new_order,
            "SERVICE_REQUEST": self._handle_service_request,
            "KILL_SWITCH": self._handle_kill_switch,
            "LOCK_LEVEL": self._handle_lock_level,
            "HOTEL_CONFIG": self._handle_hotel_config,
            "AD_PUSH": self._handle_ad_push,
            "SET_AD_SIGNATURE": self._handle_ad_signature,
            "REMOTE_PULL_LOGS": self._handle_remote_pull_logs,
            "REMOTE_DIAGNOSE": self._handle_remote_diagnose,
            "REMOTE_RELOAD": self._handle_remote_reload,
            "REMOTE_OTA_CHECK": self._handle_remote_ota_check,
            "REMOTE_VENDOR_MSG": self._handle_remote_vendor_msg,
            "VENDOR_TOAST": self._handle_vendor_toast,
        }
        handler = handler_map.get(notif_type)
        if handler:
            payload["_notify_id"] = notify_id
            handler(payload)
            if notify_id:
                db.mark_notification_processed(notify_id, notif_type)

    # ── 新订单 → 通知前台 ──
    def _handle_new_order(self, payload: Dict[str, Any]) -> None:
        try:
            try:
                from vendor_lockdown import is_locked
                if is_locked("guest_bot"):
                    logger.warning("[CLOUD] 客人机器人已被厂家锁定，跳过云端订单。")
                    return
            except Exception:
                pass
            order_id = payload.get("order_id", "")
            room_id = payload.get("room_id", "")
            total = payload.get("total", 0)
            items = payload.get("items", [])

            def _line(it: Dict[str, Any]) -> str:
                sku = (it.get("sku") or "").strip()
                qty = it.get("qty", 0)
                name_fb = it.get("name") or "?"
                if not sku:
                    return f"{name_fb} x{qty}"
                try:
                    row = db.execute(
                        "SELECT COALESCE(emoji,''), name FROM shop_items WHERE sku=?",
                        (sku,),
                    ).fetchone()
                    if row:
                        em = (row[0] or "").strip() or "📦"
                        nm = (row[1] or "").strip() or name_fb
                        return f"{em} {nm} x{qty}"
                except Exception:
                    pass
                return f"{name_fb} x{qty}"

            items_summary = ", ".join(_line(it) for it in items) if items else ""

            logger.info("[CLOUD] 新订单 %s | 房间:%s | %s | 总价:%s", order_id, room_id, items_summary, total)

            bus.cloud_order_received.emit({
                "order_id": order_id,
                "room_id": room_id,
                "total": total,
                "items": items_summary,
            })
        except Exception as exc:
            logger.warning("[CLOUD] 处理订单通知失败: %s", exc)

    # ── 服务请求 → 通知前台 ──
    def _handle_service_request(self, payload: Dict[str, Any]) -> None:
        try:
            req_type = payload.get("type", "")
            room_id = payload.get("room_id", "")

            type_names: Dict[str, str] = {
                "CALL_FRONT": "呼叫前台",
                "NEED_CLEAN": "需要打扫",
                "NEED_TOWEL": "加毛巾/洗漱",
                "COMPLAINT": "投诉",
                "MAINTENANCE": "报修设备",
            }
            type_name = type_names.get(req_type, req_type)

            logger.info("[CLOUD] 服务请求 | 房间:%s | %s", room_id, type_name)
            try:
                req_id = db.record_guest_service_request(
                    room_id,
                    req_type,
                    payload.get("message") or type_name,
                    chat_id=str(payload.get("chat_id") or ""),
                    source="cloud",
                )
                if req_type in ("NEED_CLEAN", "NEED_TOWEL", "HOUSEKEEPING"):
                    task_id = db.create_housekeeping_task(room_id, req_type, req_id, source="cloud", note=type_name)
                    try:
                        from telegram_shadow import telegram_thread
                        if telegram_thread.isRunning():
                            from telegram_bot_config import get_work_bot_token
                            token = get_work_bot_token()
                            target = db.get_config("housekeeping_group_id") or db.get_config("housekeeping_chat_id")
                            if token and target:
                                telegram_thread._send_to_target(
                                    token,
                                    target,
                                    f"🧹 <b>保洁群任务</b>\n🚪 房间：{room_id}\n📋 需求：{type_name}\n🧾 任务：<code>{task_id}</code>",
                                    buttons=[[
                                        {"text": "🧹 接单", "callback_data": f"hk_accept:{task_id}:GROUP"},
                                        {"text": "✅ 完成保洁", "callback_data": f"hk_done:{task_id}:GROUP"},
                                    ]],
                                )
                    except Exception as te:
                        logger.warning("[CLOUD] 保洁群通知失败: %s", te)
            except Exception as exc:
                logger.warning("[CLOUD] 服务请求落库失败: %s", exc)

            bus.cloud_service_request.emit({
                "room_id": room_id,
                "request_type": req_type,
                "type_name": type_name,
            })

            # 自动联动房间状态
            if req_type == "NEED_CLEAN":
                db.execute("UPDATE rooms SET status='DIRTY' WHERE room_id=?", (room_id,))
                bus.room_status_changed.emit(room_id, "DIRTY")
        except Exception as exc:
            logger.warning("[CLOUD] 处理服务请求失败: %s", exc)

    def _handle_ad_push(self, payload: Dict[str, Any]) -> None:
        try:
            text = payload.get("text") or payload.get("message") or ""
            photo_url = payload.get("photo_url") or payload.get("image_url")
            if not text and not photo_url:
                return
            from telegram_shadow import telegram_thread
            sent = telegram_thread.broadcast_ad(text, photo_url=photo_url)
            db.log_action("CLOUD", "AD_PUSH", f"sent={sent} text={text[:80]}")
            logger.info("[CLOUD] 广告投放完成 sent=%s", sent)
        except Exception as exc:
            logger.warning("[CLOUD] 处理广告投放失败: %s", exc)

    def _handle_ad_signature(self, payload: Dict[str, Any]) -> None:
        try:
            signature = payload.get("signature") or payload.get("text") or ""
            enabled = str(payload.get("enabled", "1"))
            db.set_config("ad_signature", signature)
            db.set_config("ad_signature_enabled", "1" if enabled not in ("0", "false", "False") else "0")
            db.log_action("CLOUD", "SET_AD_SIGNATURE", signature[:120])
            logger.info("[CLOUD] 广告签名已更新")
        except Exception as exc:
            logger.warning("[CLOUD] 处理广告签名失败: %s", exc)

    def _upload_remote_result(self, kind: str, text: str) -> None:
        try:
            if not CLOUD_WORKER_URL or not HOTEL_ID:
                return
            signed_post_json(
                f"{CLOUD_WORKER_URL.rstrip('/')}/api/remote-log-upload",
                {"hotel_id": HOTEL_ID, "kind": kind, "snippet": text[:12000]},
                timeout=8,
            )
        except Exception as exc:
            logger.warning("[CLOUD] 远程结果回传失败: %s", exc)

    def _handle_remote_pull_logs(self, payload: Dict[str, Any]) -> None:
        try:
            limit = int(payload.get("tail_lines") or 500)
            base = Path(__file__).resolve().parent
            logs = sorted(base.glob("debug-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                self._upload_remote_result("logs", "未找到 debug 日志。")
                return
            text = logs[0].read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
            self._upload_remote_result("logs", "\n".join(text))
        except Exception as exc:
            self._upload_remote_result("logs_error", str(exc))

    def _handle_remote_diagnose(self, payload: Dict[str, Any]) -> None:
        try:
            summary = {
                "hotel_id": HOTEL_ID,
                "cloud_url": bool(CLOUD_WORKER_URL),
                "lock_level": db.get_config("lock_level") or "",
                "last_cloud_seen_at": db.get_config("last_cloud_seen_at") or "",
                "initial_stocktake_done_at": db.get_config("initial_stocktake_done_at") or "",
                "last_energy_audit_at": db.get_config("last_energy_audit_at") or "",
            }
            self._upload_remote_result("diagnose", json.dumps(summary, ensure_ascii=False, indent=2))
        except Exception as exc:
            self._upload_remote_result("diagnose_error", str(exc))

    def _handle_remote_reload(self, payload: Dict[str, Any]) -> None:
        try:
            bus.show_warning.emit("厂家远程支援", "厂家已下发配置重载指令，本地配置已重新读取。")
            self._check_hotel_status()
            db.log_action("CLOUD", "REMOTE_RELOAD", "ok")
        except Exception as exc:
            self._upload_remote_result("reload_error", str(exc))

    def _handle_remote_ota_check(self, payload: Dict[str, Any]) -> None:
        try:
            from manufacturer_comm import ManufacturerCommService
            self._upload_remote_result("ota_check", json.dumps(ManufacturerCommService.check_for_updates(), ensure_ascii=False))
        except Exception as exc:
            self._upload_remote_result("ota_error", str(exc))

    def _handle_remote_vendor_msg(self, payload: Dict[str, Any]) -> None:
        title = payload.get("title") or "厂家消息"
        body = payload.get("body") or payload.get("message") or ""
        bus.show_warning.emit(title, body)
        db.log_action("CLOUD", "REMOTE_VENDOR_MSG", f"{title} {body[:100]}")

    def _handle_vendor_toast(self, payload: Dict[str, Any]) -> None:
        bus.vendor_toast.emit({
            "notify_id": payload.get("_notify_id") or "",
            "title": payload.get("title") or "厂家消息",
            "body": payload.get("body") or payload.get("message") or "",
            "level": payload.get("level") or "info",
        })

    # ── 远程锁机 ──
    def _handle_kill_switch(self, payload: Dict[str, Any]) -> None:
        try:
            kill_date = payload.get("kill_date", "")
            if kill_date:
                db.set_config("kill_switch_date", kill_date)
                logger.warning("[CLOUD] !! 收到远程锁机指令，锁机日期 %s", kill_date)
                bus.show_warning.emit("系统通知", f"授权到期日已更新为 {kill_date}")
                bus.kill_switch_triggered.emit()
        except Exception as exc:
            logger.warning("[CLOUD] 处理锁机指令失败: %s", exc)

    def _handle_lock_level(self, payload: Dict[str, Any]) -> None:
        try:
            level = payload.get("level") or payload.get("lock_level") or ""
            from vendor_lockdown import sync_lock_level
            applied = sync_lock_level(level, source="notification")
            db.log_action("CLOUD", "LOCK_LEVEL", applied or "RECONNECT_OK")
            logger.info("[CLOUD] 厂家锁死级别已更新: %s", applied or "RECONNECT_OK")
        except Exception as exc:
            logger.warning("[CLOUD] 处理锁死级别失败: %s", exc)

    # ── 远程配置热更新 ──
    def _handle_hotel_config(self, payload: Dict[str, Any]) -> None:
        try:
            updated_keys = []
            rejected_keys = []
            
            for key, value in payload.items():
                clean_key = key
                if clean_key.startswith("config_"):
                    clean_key = clean_key.replace("config_", "", 1)
                
                # 白名单验证：只允许修改白名单中的配置项
                if clean_key in CONFIG_WHITELIST:
                    db.set_config(clean_key, str(value))
                    updated_keys.append(clean_key)
                else:
                    rejected_keys.append(clean_key)
                    logger.warning("[CLOUD] ⚠️ 拒绝远程配置 '%s' (不在白名单中)", clean_key)
            
            if updated_keys:
                logger.info("[CLOUD] 远程配置已更新: %s", updated_keys)
            if rejected_keys:
                logger.warning("[CLOUD] 已拒绝 %s 个危险配置项: %s", len(rejected_keys), rejected_keys)
                db.log_action("CLOUD_SECURITY", "CONFIG_REJECTED", f"拒绝配置: {', '.join(rejected_keys)}")
        except Exception as exc:
            logger.warning("[CLOUD] 处理远程配置失败: %s", exc)

    # ── 酒店自动注册 ──
    def _ensure_registered(self) -> None:
        global HOTEL_ID, CLOUD_WORKER_URL

        # 获取或生成 hotel_id
        existing = _cfg("hotel_id")
        if existing:
            HOTEL_ID = existing
        else:
            HOTEL_ID = f"HTL_{_uuid.uuid4().hex[:12].upper()}"
            db.set_config("hotel_id", HOTEL_ID)

        if not CLOUD_WORKER_URL:
            return

        try:
            machine_code = '-'.join(
                ('%012X' % _uuid.getnode())[i:i + 2] for i in range(0, 12, 2)
            )

            hotel_name = _cfg("hotel_name") or "未命名酒店"
            hotel_data = {
                "hotel_id": HOTEL_ID,
                "hotel_name": hotel_name,
                "machine_code": machine_code,
                "license_key": _cfg("license_key") or "",
                "owner_chat_id": _cfg("telegram_chat_id") or "",
                "salesperson_id": _cfg("salesperson_id") or "",
                "region": _cfg("region") or "",
                "status": "ACTIVE",
                "kill_date": _cfg("kill_switch_date") or "2099-12-31",
                "client_secret": get_client_secret(),
            }

            url = f"{CLOUD_WORKER_URL.rstrip('/')}/api/hotel-register"
            res = signed_post_json(url, hotel_data, timeout=10)
            if res.status_code == 200:
                self._registered = True
                logger.info("[CLOUD] OK 酒店已注册到云端: %s (%s)", HOTEL_ID, hotel_name)
            else:
                logger.warning("[CLOUD] !! 酒店注册失败, HTTP %s: %s", res.status_code, res.text[:200])
        except Exception as exc:
            logger.warning("[CLOUD] !! 注册异常 (网络不通?): %s", exc)

    # ── 检查云端酒店状态 (停用/锁机) ──
    def _check_hotel_status(self) -> None:
        global HOTEL_ID, CLOUD_WORKER_URL
        if not CLOUD_WORKER_URL or not HOTEL_ID:
            return

        try:
            # 使用 hotel-poll 接口（同时更新 last_seen 并返回状态）
            url = f"{CLOUD_WORKER_URL.rstrip('/')}/api/hotel-poll"
            res = signed_get_json(url, params={"hotel_id": HOTEL_ID}, timeout=10)
            if res.status_code == 200:
                data = res.json()
                try:
                    from vendor_lockdown import mark_cloud_seen, sync_lock_level
                    mark_cloud_seen()
                    sync_lock_level(data.get("lock_level", ""), source="status_check")
                except Exception:
                    pass
                try:
                    from telegram_bot_config import apply_cloud_poll_response
                    apply_cloud_poll_response(data)
                except Exception:
                    pass
                remote_status = data.get("hotel_status", "ACTIVE")
                kill_switch = data.get("kill_switch")

                # 检查 kill_switch
                if kill_switch and isinstance(kill_switch, dict):
                    remote_kill = kill_switch.get("kill_date", "")
                    if remote_kill:
                        db.set_config("kill_switch_date", remote_kill)
                        logger.warning("[CLOUD] !! 酒店授权已到期 (kill_date=%s)", remote_kill)
                        bus.kill_switch_triggered.emit()

                # 检查被厂家停用
                if remote_status == "SUSPENDED":
                    logger.warning("[CLOUD] !! 酒店已被厂家停用")
                    bus.show_warning.emit("系统通知", "您的酒店已被管理员停用，请联系厂家。")
        except Exception:
            pass  # 网络不通就不检查，不阻塞本地运行


# ================================================================
# 单例
# ================================================================
cloud_poll_thread: Optional[CloudPollThread] = None


# ================================================================
# 初始化入口 (app_main.py 调用)
# ================================================================
def init_cloud_connection(worker_url: str = "") -> str:
    """
    初始化云端连接，自动从 config 表读取 cloud_worker_url,
    如果手动传入 worker_url 则覆盖写入 config 表。

    返回当前的 CLOUD_WORKER_URL
    """
    global CLOUD_WORKER_URL, cloud_poll_thread

    if worker_url:
        CLOUD_WORKER_URL = worker_url.rstrip("/")
        db.set_config("cloud_worker_url", CLOUD_WORKER_URL)
    else:
        CLOUD_WORKER_URL = _cfg("cloud_worker_url", "")

    cloud_enabled = _cfg_int("cloud_enabled", _DEFAULT_CLOUD_ENABLED)

    if CLOUD_WORKER_URL and cloud_enabled:
        if cloud_poll_thread is None or not cloud_poll_thread.is_alive():
            cloud_poll_thread = CloudPollThread()
            cloud_poll_thread.start()
            logger.info("[CLOUD] OK 已连接到云端: %s", CLOUD_WORKER_URL)
        else:
            logger.info("[CLOUD] 轮询线程已在运行中。")
    else:
        if CLOUD_WORKER_URL and not cloud_enabled:
            logger.warning("[CLOUD] !! Worker URL 已配置但 cloud_enabled=0，离线运行。")
        elif not CLOUD_WORKER_URL:
            logger.info("[CLOUD] 未配置 Worker 地址，完全离线模式。")

    return CLOUD_WORKER_URL