import asyncio
import logging
import json
import time
import datetime
import html
import io
from PySide6.QtCore import QThread, QBuffer, QIODevice
from database import db
from event_bus import bus
from i18n import i18n
from telegram_bot_config import get_guest_bot_token, get_work_bot_token
import requests

from telegram_handlers import TelegramHandlersMixin, TG_SHOP_ORDER_PREFIX
from telegram_messages import TelegramMessagesMixin

logger = logging.getLogger(__name__)


class TelegramShadowThread(TelegramHandlersMixin, TelegramMessagesMixin, QThread):
    """Telegram Bot 核心：主循环、轮询、连接管理。"""

    def __init__(self):
        super().__init__()
        self.offset = 0
        self.chat_to_room = {}
        self.running = True
        self._last_report_date = None
        bus.screenshot_ready.connect(self._on_screenshot)
        # 客人超市：内联 callback 用序号映射 SKU；品类列表用 shopc: 序号
        self._guest_shop_pick = {}  # chat_id str -> (expires_mono: float, [sku, ...])
        self._guest_shop_cats = {}  # chat_id str -> (expires_mono: float, [category_key, ...])
        # 客人购物车（in-memory，30 分钟过期）。
        self._guest_shop_cart = {}
        # 常用付现面额
        self._cash_quick_amounts = (5, 10, 20, 50, 100, 200, 500, 1000)

    # ── 生命周期 ──

    def run(self):
        logger.info("Telegram thread started.")
        while self.running and self.isRunning():
            self._poll()
            self._check_daily_report()
            for _ in range(20):
                if not self.running:
                    break
                time.sleep(0.1)

    def request_stop(self):
        """退出应用前调用：结束轮询循环，配合 wait() 避免 QThread 析构告警。"""
        self.running = False

    # ── 轮询 ──

    def _poll(self):
        token = get_guest_bot_token()
        if not token:
            return

        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            res = requests.get(url, params={"offset": self.offset, "timeout": 5}, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get("ok"):
                    for update in data["result"]:
                        self.offset = update["update_id"] + 1
                        self._handle_update(update, token)
        except Exception as e:
            pass

    # ── 日报检查 ──

    def _check_daily_report(self):
        """检查是否需要发送每日日报"""
        try:
            if (db.get_config("daily_report_enabled") or "1") != "1":
                return
            token = get_work_bot_token()
            chat_id = db.get_config("telegram_chat_id")
            if not token or not chat_id:
                return
            now = datetime.datetime.now()
            report_hour = int(db.get_config("daily_report_hour") or "23")
            today = now.date()
            if now.hour == report_hour and today != self._last_report_date:
                self._last_report_date = today
                report = db.build_daily_risk_report()
                hotel = db.get_config("hotel_name") or "酒店"
                text = (
                    f"📊 <b>{hotel} · 每日运营日报</b>\n"
                    f"📅 {today.strftime('%Y年%m月%d日')}\n"
                    f"{'─'*20}\n"
                    f"{report['report_text']}"
                )
                self.send_alert_sync(text)
                logger.info("每日日报已发送 (%s)", today)
        except Exception as e:
            logger.warning("日报发送失败: %s", e)

    # ── 消息发送 ──

    def _send_reply(self, token, chat_id, text, reply_markup=None, parse_mode=None, ad=False):
        """向客人发送消息。ad=True 时在消息底部附带厂家信息。"""
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        if ad:
            sig = self._get_ad_signature()
            if sig:
                text = text + "\n\n" + sig
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            if isinstance(reply_markup, str):
                try:
                    payload["reply_markup"] = json.loads(reply_markup)
                except json.JSONDecodeError:
                    payload["reply_markup"] = reply_markup
            else:
                payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.warning("_send_reply error: %s", e)

    def _send_to_target(self, token, target, text, buttons=None):
        """发送消息到指定目标（支持群组ID）"""
        try:
            payload = {"chat_id": target, "text": text, "parse_mode": "HTML"}
            if buttons:
                payload["reply_markup"] = {"inline_keyboard": buttons}
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=5)
        except Exception as e:
            logger.warning("_send_to_target error: %s", e)

    def _answer_callback_query(self, token, cq_id, text=None, show_alert=False):
        """关闭内联按钮 loading；可选弹出短提示。"""
        if not cq_id:
            return
        body = {"callback_query_id": cq_id}
        if text:
            body["text"] = str(text)[:200]
            body["show_alert"] = bool(show_alert)
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                json=body,
                timeout=5,
            )
        except Exception as e:
            logger.warning("answerCallbackQuery: %s", e)

    # ── 截图 ──

    def _on_screenshot(self, pixmap):
        token = get_work_bot_token()
        if not token or not hasattr(self, 'last_shot_chat'):
            return

        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        img_bytes = buffer.data().data()

        try:
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            files = {'photo': ('shot.png', img_bytes, 'image/png')}
            payload = {'chat_id': self.last_shot_chat, 'caption': f"📸 前台实时截图\n时间: {time.strftime('%Y-%m-%d %H:%M:%S')}"}
            requests.post(url, data=payload, files=files, timeout=15)
        except Exception as e:
            logger.warning("Error sending shot: %s", e)

    # ── 工具方法 ──

    def _now(self):
        return datetime.datetime.now().strftime("%H:%M")

    def _get_guest_keyboard(self):
        """客人主菜单 6大按钮"""
        return json.dumps({
            "keyboard": [
                [{"text": "📶 WiFi 密码"}, {"text": "💰 查房费余额"}],
                [{"text": "🛒 超市下单"}, {"text": "📦 呼叫送物"}],
                [{"text": "🧹 呼叫保洁"}, {"text": "🛎 呼叫前台"}]
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False
        })

    def _get_back_keyboard(self):
        """返回主菜单键盘"""
        return json.dumps({
            "keyboard": [[{"text": "🔙 返回主菜单"}]],
            "resize_keyboard": True
        })

    def _get_ad_signature(self) -> str:
        """获取厂家广告签名（被动广告位）。"""
        sig = db.get_config("ad_signature") or ""
        if (db.get_config("ad_signature_enabled") or "1") != "1":
            return ""
        return f"\n─────────────\n{sig}" if sig else ""

    # ── 广播广告 ──

    def broadcast_ad(self, text, photo_url=None):
        """广播广告给所有订阅客人（同步版本）"""
        try:
            from vendor_lockdown import is_locked
            if is_locked("guest_bot"):
                return 0
        except Exception:
            pass
        if (db.get_config("ad_broadcast_enabled") or "1") != "1":
            return 0
        subscribers = db.execute("SELECT chat_id, COALESCE(last_active, subscribed_at, datetime('now')) FROM bot_subscribers").fetchall()
        token = get_guest_bot_token()
        if not token:
            return 0
        cap = int(db.get_config("ad_broadcast_daily_cap") or "2")
        today = datetime.date.today().isoformat()
        count = 0
        ad_sig = self._get_ad_signature()
        body_text = (text or "") + (ad_sig if ad_sig and ad_sig not in (text or "") else "")
        for (sid, _last_active) in subscribers:
            try:
                key = f"ad_sent_{today}_{sid}"
                sent_today = int(db.get_config(key) or "0")
                if sent_today >= cap:
                    continue
                if photo_url:
                    url = f"https://api.telegram.org/bot{token}/sendPhoto"
                    requests.post(url, json={
                        "chat_id": sid,
                        "photo": photo_url,
                        "caption": body_text,
                        "parse_mode": "HTML"
                    }, timeout=5)
                else:
                    url = f"https://api.telegram.org/bot{token}/sendMessage"
                    requests.post(url, json={
                        "chat_id": sid,
                        "text": body_text,
                        "parse_mode": "HTML"
                    }, timeout=5)
                db.set_config(key, str(sent_today + 1))
                count += 1
            except Exception:
                pass
        db.set_config(f"ad_last_broadcast_{int(time.time())}", json.dumps({"sent": count, "text": text[:120] if text else ""}, ensure_ascii=False))
        return count

    # ── 告警发送 ──

    async def send_alert(self, text, buttons=None):
        """Async wrapper - kept for compatibility, internally calls sync version."""
        self.send_alert_sync(text, buttons)

    def send_alert_sync(self, text, buttons=None):
        """Synchronous alert sender - safe to call from any thread."""
        token = get_work_bot_token()
        chat_id = db.get_config("telegram_chat_id")
        m_chat_id = db.get_config("manufacturer_chat_id")

        if not token or not chat_id:
            logger.warning("🚨 [MOCK ALERT] %s", text)
            return

        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if buttons:
            payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})

        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, json=payload, timeout=5)
            manufacturer_copy_enabled = db.get_config("manufacturer_copy_enabled") or "0"
            if m_chat_id and manufacturer_copy_enabled == "1":
                payload2 = dict(payload)
                payload2["chat_id"] = m_chat_id
                requests.post(url, json=payload2, timeout=5)
                db.log_action("SYSTEM", "MANUFACTURER_COPY_SENT",
                              f"已向制造商发送副本（chat_id:{m_chat_id}）")
        except Exception as e:
            logger.warning("send_alert_sync error: %s", e)


telegram_thread = TelegramShadowThread()
