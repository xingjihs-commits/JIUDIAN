"""
厂家通信服务模块
=====================================
职责：
  1. 心跳轮询（每5分钟）：向云端地址上报在线状态，接收远程指令
  2. 事件镜像：将关键事件静默上报给厂家 Telegram 聊天
  3. 远程通知处理：停用开关 / 广告推送 / 远程指令 等
  4. 版本检查（占位，可扩展）
"""

import threading
import time
import requests
from database import db
from PySide6.QtCore import QThread, Signal


# ─────────────────────────────────────────────────────────────────────────────
#  常量
# ─────────────────────────────────────────────────────────────────────────────
_HEARTBEAT_INTERVAL = 300   # 5 分钟
_CLOUD_TIMEOUT      = 8
_APP_VERSION        = "1.6.0"


# ─────────────────────────────────────────────────────────────────────────────
#  ManufacturerCommService — 静态工具方法
# ─────────────────────────────────────────────────────────────────────────────
class ManufacturerCommService:

    # ── 事件镜像（静默上报给厂家 Telegram）────────────────────────────────────
    @staticmethod
    def report_event(event_type: str, details: str):
        """将关键事件静默镜像给厂家 Telegram 聊天（不影响主流程）"""
        from telegram_bot_config import get_work_bot_token
        m_chat_id = db.get_config("manufacturer_chat_id")
        token     = get_work_bot_token()
        if not m_chat_id or not token:
            return
        hotel_name = db.get_config("hotel_name") or "未命名酒店"
        hotel_id   = db.get_config("hotel_id") or "UNKNOWN"
        try:
            url  = f"https://api.telegram.org/bot{token}/sendMessage"
            text = (
                f"🕵️ <b>[厂家镜像]</b> {event_type}\n"
                f"🏨 {hotel_name} <code>({hotel_id})</code>\n"
                f"{details}"
            )
            requests.post(
                url,
                json={"chat_id": m_chat_id, "text": text, "parse_mode": "HTML"},
                timeout=5
            )
        except Exception:
            pass

    # ── 云端心跳 ──────────────────────────────────────────────────────────────
    @staticmethod
    def heartbeat() -> dict:
        """
        向云端发送心跳，返回响应数据字典
        失败时返回空字典
        """
        worker_url = db.get_config("cloud_worker_url") or ""
        if not worker_url:
            return {}
        from license_manager import LicenseManager
        hotel_id = LicenseManager.get_hotel_id()
        try:
            resp = requests.get(
                f"{worker_url.rstrip('/')}/api/hotel-poll",
                params={"hotel_id": hotel_id},
                timeout=_CLOUD_TIMEOUT
            )
            data = resp.json()
            try:
                from telegram_bot_config import apply_cloud_poll_response
                apply_cloud_poll_response(data)
            except Exception:
                pass
            return data
        except Exception:
            return {}

    # ── 远程通知处理 ──────────────────────────────────────────────────────────
    @staticmethod
    def process_notifications(notifications: list, worker_url: str):
        """
        处理云端下发的通知列表
        支持类型：停用开关 / 广告推送 / 远程指令 / 版本更新
        """
        if not notifications:
            return
        acked = []
        for n in notifications:
            ntype   = n.get("notify_type", "")
            payload = {}
            try:
                import json
                raw = n.get("payload_json", "{}")
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                pass
            notify_id = n.get("notify_id", "")

            if ntype == "KILL_SWITCH":
                kill_date = payload.get("kill_date", "2020-01-01")
                db.set_config("kill_switch_date", kill_date)
                db.log_action("MANUFACTURER", "REMOTE_KILL_SWITCH", f"kill_date={kill_date}")
                from event_bus import bus
                bus.kill_switch_triggered.emit()

            elif ntype == "AD_PUSH":
                ad_text = payload.get("ad_text", "")
                if ad_text:
                    db.set_config("pending_ad_text", ad_text)
                    db.log_action("MANUFACTURER", "AD_PUSH_RECEIVED", ad_text[:100])

            elif ntype == "SET_AD_SIGNATURE":
                # 设置被动广告签名（附加在每条机器人消息底部）
                sig = payload.get("signature", "")
                db.set_config("ad_signature", sig)
                db.log_action("MANUFACTURER", "AD_SIGNATURE_SET", sig[:100])

            elif ntype == "REMOTE_CMD":
                cmd = payload.get("cmd", "")
                db.log_action("MANUFACTURER", "REMOTE_CMD", cmd)
                from event_bus import bus
                bus.remote_command.emit(payload)

            elif ntype == "UPDATE_AVAILABLE":
                version = payload.get("version", "")
                db.set_config("pending_update_version", version)
                db.log_action("MANUFACTURER", "UPDATE_AVAILABLE", version)

            if notify_id:
                acked.append(notify_id)

        # 批量确认
        for nid in acked:
            try:
                requests.post(
                    f"{worker_url.rstrip('/')}/api/ack",
                    json={"notify_id": nid},
                    timeout=5
                )
            except Exception:
                pass

    # ── 版本检查 ──────────────────────────────────────────────────────────────
    @staticmethod
    def check_for_updates() -> dict:
        """检查云端是否有新版本（占位实现）"""
        pending = db.get_config("pending_update_version") or ""
        if pending and pending != _APP_VERSION:
            return {"has_update": True, "version": pending}
        return {"has_update": False, "version": _APP_VERSION}

    # ── 云端酒店列表（厂家后台用）────────────────────────────────────────────
    @staticmethod
    def fetch_hotel_list(worker_url: str, admin_pwd: str) -> list:
        """从云端获取所有酒店列表（需要管理员密码）"""
        try:
            resp = requests.get(
                f"{worker_url.rstrip('/')}/admin",
                params={"pwd": admin_pwd, "format": "json"},
                timeout=_CLOUD_TIMEOUT
            )
            # Worker 返回 HTML，改用专用 API 地址
            # 此处调用酒店列表接口（需在 worker.js 中添加）
            resp2 = requests.get(
                f"{worker_url.rstrip('/')}/api/hotels-list",
                params={"pwd": admin_pwd},
                timeout=_CLOUD_TIMEOUT
            )
            data = resp2.json()
            return data.get("hotels", [])
        except Exception:
            return []

    # ── 远程授权下发 ──────────────────────────────────────────────────────────
    @staticmethod
    def issue_license(worker_url: str, admin_pwd: str,
                      expire_days: int, salesperson_id: str) -> dict:
        """向云端申请生成新授权码"""
        try:
            resp = requests.post(
                f"{worker_url.rstrip('/')}/api/license-issue",
                json={
                    "pwd":            admin_pwd,
                    "expire_days":    expire_days,
                    "salesperson_id": salesperson_id,
                    "features":       {"all": True}
                },
                timeout=_CLOUD_TIMEOUT
            )
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── 远程停用/恢复酒店 ─────────────────────────────────────────────────────
    @staticmethod
    def toggle_hotel(worker_url: str, admin_pwd: str,
                     hotel_id: str, action: str) -> dict:
        """action: 'suspend' | 'resume'"""
        try:
            resp = requests.post(
                f"{worker_url.rstrip('/')}/api/hotel-suspend",
                json={"pwd": admin_pwd, "hotel_id": hotel_id, "action": action},
                timeout=_CLOUD_TIMEOUT
            )
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── 广告推送 ──────────────────────────────────────────────────────────────
    @staticmethod
    def push_ad(worker_url: str, admin_pwd: str,
                hotel_ids: list, ad_text: str) -> dict:
        """向指定酒店推送广告"""
        try:
            resp = requests.post(
                f"{worker_url.rstrip('/')}/api/ad-push",
                json={"pwd": admin_pwd, "hotel_ids": hotel_ids, "ad_text": ad_text},
                timeout=_CLOUD_TIMEOUT
            )
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  HeartbeatWorkerThread — 后台心跳线程
# ─────────────────────────────────────────────────────────────────────────────
class HeartbeatWorkerThread(QThread):
    """
    每 5 分钟向云端发送心跳，处理远程通知
    同时每次心跳同步授权状态
    """
    status_changed = Signal(bool)   # True=在线, False=离线/被停用

    def __init__(self):
        super().__init__()
        self.running = True
        self._interval = _HEARTBEAT_INTERVAL

    def run(self):
        # 启动后延迟 30 秒再首次心跳（等待主窗口加载完成）
        time.sleep(30)
        while self.running:
            self._do_heartbeat()
            # 分段睡眠，支持快速停止
            for _ in range(self._interval):
                if not self.running:
                    break
                time.sleep(1)

    def _do_heartbeat(self):
        try:
            from license_manager import LicenseManager
            active = LicenseManager.sync_cloud_status()
            self.status_changed.emit(active)
        except Exception:
            pass

    def stop(self):
        self.running = False


# ─────────────────────────────────────────────────────────────────────────────
#  全局单例
# ─────────────────────────────────────────────────────────────────────────────
heartbeat_worker = HeartbeatWorkerThread()
