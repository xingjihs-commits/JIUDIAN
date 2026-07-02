import logging
import datetime
from database import db
from telegram_bot_config import get_work_bot_token

logger = logging.getLogger(__name__)


class TelegramMessagesMixin:
    """通知消息模板 — 所有 notify_* 方法（含多语言格式化字符串）。"""

    def notify_rate_override(self, room_id, guest_name, tier_label, expected_total, actual_total, reason):
        """房费行与当前档位系统参考价不一致时通知老板（需在设置中开启）。"""
        if (db.get_config("notify_rate_override") or "0") != "1":
            return
        hotel = db.get_config("hotel_name") or "酒店"
        cur = db.get_config("currency_symbol") or "¥"
        msg = (
            f"📌 <b>房费改价</b> — {hotel}\n"
            f"🚪 房间：{room_id}\n"
            f"👤 住客：{guest_name}\n"
            f"📎 档位：{tier_label}\n"
            f"💡 系统参考合计：{cur}{float(expected_total):.2f} → 账单房费行：{cur}{float(actual_total):.2f}\n"
            f"📝 原因：{reason}\n"
            f"🕐 时间：{datetime.datetime.now().strftime('%H:%M')}"
        )
        self.send_alert_sync(msg)

    def notify_checkin(self, room_id, guest_name, amount):
        """入住通知"""
        if (db.get_config("notify_checkin") or "1") != "1":
            return
        hotel = db.get_config("hotel_name") or "酒店"
        msg = (
            f"🏨 <b>新客入住</b> — {hotel}\n"
            f"🚪 房间：{room_id}\n"
            f"👤 住客：{guest_name}\n"
            f"💰 金额：{db.get_config('currency_symbol') or '¥'}{amount:.2f}\n"
            f"🕐 时间：{datetime.datetime.now().strftime('%H:%M')}"
        )
        self.send_alert_sync(msg)

    def notify_checkout(self, room_id, guest_name):
        """退房通知"""
        if (db.get_config("notify_checkout") or "1") != "1":
            return
        hotel = db.get_config("hotel_name") or "酒店"
        msg = (
            f"🚪 <b>客人退房</b> — {hotel}\n"
            f"🏠 房间：{room_id}\n"
            f"👤 住客：{guest_name}\n"
            f"🕐 时间：{datetime.datetime.now().strftime('%H:%M')}"
        )
        self.send_alert_sync(msg)

    def notify_reservation(self, reservation_id, guest_name, room_type, room_id, checkin_dt, checkout_dt, deposit=0):
        """本地预订通知老板/前台（OTA 不参与当前主线）。"""
        hotel = db.get_config("hotel_name") or "酒店"
        sym = db.get_config("currency_symbol") or "$"
        target = room_id or room_type or "待分配"
        msg = (
            f"📌 <b>本地预订</b> — {hotel}\n"
            f"📋 单号：<code>{reservation_id}</code>\n"
            f"👤 客人：{guest_name}\n"
            f"🏠 房间/房型：{target}\n"
            f"📅 入住：{checkin_dt}\n"
            f"📅 离店：{checkout_dt}\n"
            f"💰 订金：{sym}{float(deposit or 0):.2f}\n"
            f"🕐 时间：{datetime.datetime.now().strftime('%H:%M')}"
        )
        self.send_alert_sync(msg)

    def notify_today_reservation_digest(self):
        """今日预抵/预离摘要，适合老板或前台群早班查看。"""
        data = db.list_today_reservation_alerts()
        arrivals = data.get("arrivals") or []
        departures = data.get("departures") or []
        if not arrivals and not departures:
            return
        lines = ["📋 <b>今日预抵/预离提醒</b>"]
        if arrivals:
            lines.append("\n<b>今日预抵</b>")
            for r in arrivals[:12]:
                _rid, room_id, room_type, name, phone, ci = r
                lines.append(f"• {name}｜{room_id or room_type or '待分配'}｜{str(ci)[11:16]}｜{phone or '-'}")
        if departures:
            lines.append("\n<b>今日预离</b>")
            for room_id, name, phone, co in departures[:12]:
                lines.append(f"• {room_id}｜{name}｜{str(co)[11:16] if co else '-'}｜{phone or '-'}")
        self.send_alert_sync("\n".join(lines))

    def notify_cashier_shift_summary(self, since=""):
        """收银/交班摘要通过工作机器人通知老板。"""
        s = db.build_cashier_shift_summary(since)
        sym = s.get("currency") or "$"
        sym2 = s.get("secondary_currency") or "៛"
        lines = [
            "💵 <b>收银交班摘要</b>",
            f"现金净额：{sym}{float(s.get('cash_net') or 0):.2f} / {sym2}{float(s.get('cash_net_secondary') or 0):.0f}",
            "",
            "<b>按支付方式</b>",
        ]
        for k, v in sorted((s.get("by_pay") or {}).items()):
            lines.append(f"• {k}: {sym}{float(v or 0):.2f}")
        lines.append("")
        lines.append("<b>按流水类型</b>")
        for k, v in sorted((s.get("by_type") or {}).items()):
            lines.append(f"• {k}: {sym}{float(v or 0):.2f}")
        self.send_alert_sync("\n".join(lines))

    def notify_night_audit_closed(self, business_date="", operator_id="night_audit"):
        ok, msg = db.close_business_day(business_date, operator_id)
        s = db.build_cashier_shift_summary(f"{business_date} 00:00:00" if business_date else "")
        sym = s.get("currency") or "$"
        text = (
            f"🌙 <b>夜审{'完成' if ok else '提示'}</b>\n"
            f"📅 营业日：{business_date or datetime.date.today().isoformat()}\n"
            f"📌 状态：{msg}\n"
            f"💵 现金净额：{sym}{float(s.get('cash_net') or 0):.2f}\n"
            "未处理服务/预抵预离已写入夜审快照。"
        )
        self.send_alert_sync(text)

    def notify_payout(self, payout_type, amount, note):
        """支出通知"""
        if (db.get_config("notify_payout") or "1") != "1":
            return
        hotel = db.get_config("hotel_name") or "酒店"
        msg = (
            f"💸 <b>支出登记</b> — {hotel}\n"
            f"📋 类型：{payout_type}\n"
            f"💰 金额：{db.get_config('currency_symbol') or '¥'}{amount:.2f}\n"
            f"📝 备注：{note}\n"
            f"🕐 时间：{datetime.datetime.now().strftime('%H:%M')}"
        )
        self.send_alert_sync(msg)

    def notify_hk_done(self, room_id, staff_name):
        """保洁完成通知"""
        if (db.get_config("notify_hk") or "0") != "1":
            return
        msg = (
            f"🧹 <b>保洁完成</b>\n"
            f"🏠 房间：{room_id} 已清洁完毕\n"
            f"👷 员工：{staff_name}\n"
            f"🕐 时间：{datetime.datetime.now().strftime('%H:%M')}"
        )
        self.send_alert_sync(msg)
        token = get_work_bot_token()
        if not token:
            return
        boss = str(db.get_config("telegram_chat_id") or "").strip()
        seen = {boss} if boss else set()
        try:
            for (sid,) in db.execute(
                "SELECT staff_id FROM staff_roster WHERE is_active=1 AND ("
                "role='保洁' OR LOWER(role) IN ('housekeeping','cleaner','cleaning')"
                ")"
            ).fetchall():
                for cid in db.resolve_staff_notify_chats(sid[0]):
                    c = str(cid).strip()
                    if not c or c in seen:
                        continue
                    seen.add(c)
                    self._send_to_target(token, c, msg)
        except Exception as e:
            logger.warning("notify_hk_done extra targets: %s", e)
