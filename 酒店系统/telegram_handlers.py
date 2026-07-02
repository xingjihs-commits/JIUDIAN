import logging
import json
import html
import datetime
import time
from database import db
from event_bus import bus
from i18n import i18n
from telegram_bot_config import get_guest_bot_token, get_work_bot_token
# [sub-i] 图标包 + Telegram 发图能力
from shop_icon_pack import icon_pack
import telegram_notify

logger = logging.getLogger(__name__)

# 客人点单键盘：与购物下单区分；按钮仅携带 SKU（单键限 64 字符）；SKU 勿含双竖线
TG_SHOP_ORDER_PREFIX = "🛒\u2016"

# [sub-i] 超市商品发图配置：
#   PHOTO_BATCH_LIMIT  — 单次最多发图条数（超过此值的商品走文字列表兜底，避免刷屏 + Telegram 限流）
#   PHOTO_SEND_DELAY   — 每张图之间的间隔秒数（Telegram 限流：同 chat ~30 msg/s，保守 0.5s）
#   LOW_STOCK_TG_WARN  — Telegram 端库存预警阈值（≤ 此值 caption 加 ⚠️ 提示）
PHOTO_BATCH_LIMIT = 8
PHOTO_SEND_DELAY = 0.5
LOW_STOCK_TG_WARN = 3


class TelegramHandlersMixin:
    """所有命令处理器（/start, /menu, /help, etc.）、内联回调、菜单处理。"""

    # ── 员工操作鉴权 ──

    def _staff_ops_chat_allowed(self, chat_id) -> bool:
        """仅允许老板主 chat、花名册已绑定个人号、或配置的工作群使用员工指令。"""
        cid = str(chat_id).strip()
        if not cid:
            return False
        boss = str(db.get_config("telegram_chat_id") or "").strip()
        if boss and cid == boss:
            return True
        try:
            row = db.execute(
                "SELECT 1 FROM staff_roster WHERE telegram_chat_id=? AND COALESCE(is_active,1)=1",
                (cid,),
            ).fetchone()
            if row:
                return True
        except Exception:
            pass
        for key in (
            "housekeeping_group_id",
            "housekeeping_chat_id",
            "front_desk_group_id",
            "front_desk_chat_id",
        ):
            v = (db.get_config(key) or "").strip()
            if v and v == cid:
                return True
        return False

    # ── 消息入口 ──

    def _handle_update(self, update, token):
        # 1. Handle Callback Query (Buttons)
        if "callback_query" in update:
            self._handle_callback(update["callback_query"], token)
            return

        if "message" not in update:
            return
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            parts = text.split(" ", 1)
            if len(parts) > 1:
                param = parts[1].strip()
                if param.startswith("room_"):
                    self._handle_room_deeplink(token, chat_id, param)
                else:
                    room_id = param
                    self.chat_to_room[chat_id] = room_id
                    try:
                        db.execute(
                            "INSERT OR REPLACE INTO bot_subscribers(chat_id, room_id, subscribed_at, last_active) VALUES(?,?,COALESCE((SELECT subscribed_at FROM bot_subscribers WHERE chat_id=?),datetime('now')),datetime('now'))",
                            (str(chat_id), room_id, str(chat_id)),
                        )
                    except Exception:
                        pass
                    hotel_name = db.get_config("hotel_name") or "酒店"
                    now_h = datetime.datetime.now().hour
                    greeting = "早上好" if now_h < 12 else "下午好" if now_h < 18 else "晚上好"
                    welcome_text = (
                        f"🏨 {greeting}，欢迎入住 <b>{hotel_name}</b>！\n"
                        f"🚪 您的房间：<b>{room_id}</b>\n\n"
                        "有需要随时叫我，看看我能帮您做些什么："
                    )
                    self._send_reply(token, chat_id, welcome_text, self._get_guest_keyboard(), parse_mode="HTML", ad=True)
            else:
                self._send_reply(token, chat_id, "您好，我是酒店管家 🏨 想用手机点超市、叫保洁、查无线网络？扫房间桌上的二维码就能找到我啦。暂时找不到的话，也可以告诉前台帮您操作。")

        elif text == "/shot":
            allowed_ids = [
                str(db.get_config("telegram_chat_id") or ""),
                str(db.get_config("manufacturer_chat_id") or ""),
            ]
            if str(chat_id) in [x for x in allowed_ids if x]:
                self.last_shot_chat = chat_id
                bus.request_screenshot.emit()
                self._send_reply(token, chat_id, "正在截取前台画面，请稍候...")
            else:
                self._send_reply(token, chat_id, "⛔ 无权限执行此操作。")

        elif text.startswith(("/clean", "/meter", "/clockin", "/clockout", "/nightaudit")):
            if not self._staff_ops_chat_allowed(chat_id):
                self._send_reply(token, chat_id, i18n.t("tg_staff_cmd_denied"))
                return
            self._handle_staff_command(token, chat_id, text)

        elif text == "📶 WiFi 密码":
            self._handle_wifi(token, chat_id)

        elif text == "💰 查房费余额":
            self._handle_balance_check(token, chat_id)

        elif text == "🛒 超市下单":
            self._handle_shop_menu(token, chat_id)

        elif text == "📦 呼叫送物":
            self._handle_delivery_menu(token, chat_id)

        elif text == "🧹 呼叫保洁":
            self._handle_hk_menu(token, chat_id)

        elif text == "🛎 呼叫前台":
            self._handle_front_menu(token, chat_id)

        elif text in ["🧺 换毛巾/床单", "💧 补充饮用水", "🧹 打扫房间", "🧴 补充洗漱用品"]:
            room_id = self.chat_to_room.get(chat_id, "未知房间")
            req_id = ""
            try:
                req_id = db.record_guest_service_request(room_id, "HOUSEKEEPING", text, chat_id=str(chat_id))
                task_id = db.create_housekeeping_task(room_id, "GUEST_REQUEST", req_id, source="guest_bot", note=text)
            except Exception:
                task_id = ""
            hk_target = db.get_config("housekeeping_group_id") or db.get_config("housekeeping_chat_id") or db.get_config("telegram_chat_id")
            if hk_target:
                buttons = None
                if task_id:
                    buttons = [[
                        {"text": "🧹 接单", "callback_data": f"hk_accept:{task_id}:GROUP"},
                        {"text": "✅ 完成保洁", "callback_data": f"hk_done:{task_id}:GROUP"},
                    ]]
                self._send_to_target(
                    token, hk_target,
                    f"🧹 <b>保洁群任务</b>\n🚪 房间：{room_id}\n📋 需求：{text}\n"
                    f"🧾 任务：<code>{task_id or req_id or '-'}</code>\n🕐 时间：{self._now()}",
                    buttons=buttons,
                )
            bus.room_status_changed.emit(room_id, "DIRTY")
            self._send_reply(token, chat_id, f"✅ 已通知保洁，{text}请求已发送！\n保洁员将尽快为您服务。", self._get_guest_keyboard())

        elif text in ["📦 送物品到房间", "🔄 申请换房", "⏰ 续住", "🚪 申请退房", "❓ 其他咨询"]:
            room_id = self.chat_to_room.get(chat_id, "未知房间")
            try:
                db.record_guest_service_request(room_id, "FRONTDESK", text, chat_id=str(chat_id))
            except Exception:
                pass
            front_target = db.get_config("front_desk_group_id") or db.get_config("front_desk_chat_id") or db.get_config("telegram_chat_id")
            if front_target:
                self._send_to_target(token, front_target,
                    f"🛎 <b>前台请求</b>\n🚪 房间：{room_id}\n📋 需求：{text}\n🕐 时间：{self._now()}")
            bus.guest_called.emit(room_id, text)
            self._send_reply(token, chat_id, f"✅ 已通知前台，{text}请求已发送！\n前台将尽快为您服务。", self._get_guest_keyboard())

        elif text.startswith(TG_SHOP_ORDER_PREFIX):
            self._handle_shop_order_by_sku(token, chat_id, text[len(TG_SHOP_ORDER_PREFIX):].strip())

        elif text.startswith("🛒 购买:"):
            raw = text[len("🛒 购买:"):]
            sku_legacy = raw.split(":", 1)[0].strip() if raw else ""
            self._handle_shop_order_by_sku(token, chat_id, sku_legacy)

        elif text == "🔙 返回主菜单":
            hotel_name = db.get_config("hotel_name") or "酒店"
            self._send_reply(token, chat_id, f"🏨 {hotel_name} 客房服务", self._get_guest_keyboard())

        elif text == "🛎️ 呼叫前台":
            room_id = self.chat_to_room.get(chat_id, "未知房间")
            bus.guest_called.emit(room_id, "呼叫前台")
            self._send_reply(token, chat_id, "已通知前台，请稍候。")

        elif text == "🧹 需要打扫":
            room_id = self.chat_to_room.get(chat_id, "未知房间")
            bus.room_status_changed.emit(room_id, "DIRTY")
            self._send_reply(token, chat_id, "保洁请求已发送。")

    # ── 深链接 ──

    def _handle_room_deeplink(self, token, chat_id, param):
        try:
            parts = param.split("_")
            if len(parts) < 3:
                self._send_reply(token, chat_id, "❌ 二维码无效，请联系前台重新获取。")
                return

            qr_token = parts[-1]
            room_id = "_".join(parts[1:-1])

            valid = False
            try:
                row = db.execute(
                    "SELECT room_id, expires_at FROM qr_tokens WHERE token=? AND room_id=?",
                    (qr_token, room_id)
                ).fetchone()
                if row:
                    expires_at = row[1]
                    if expires_at:
                        import datetime as _dt
                        exp = _dt.datetime.fromisoformat(str(expires_at))
                        if _dt.datetime.now() <= exp:
                            valid = True
                    else:
                        valid = True
            except Exception:
                valid = True

            if not valid:
                self._send_reply(token, chat_id, "⏰ 此二维码已过期，请联系前台重新生成。")
                return

            self.chat_to_room[chat_id] = room_id

            try:
                db.execute(
                    "INSERT OR REPLACE INTO bot_subscribers(chat_id, room_id, subscribed_at, last_active) VALUES(?,?,COALESCE((SELECT subscribed_at FROM bot_subscribers WHERE chat_id=?),datetime('now')),datetime('now'))",
                    (str(chat_id), room_id, str(chat_id))
                )
            except Exception:
                pass

            hotel_name = db.get_config("hotel_name") or "酒店"
            now_h = datetime.datetime.now().hour
            greeting = "早上好" if now_h < 12 else "下午好" if now_h < 18 else "晚上好"
            welcome_text = (
                f"🏨 {greeting}，欢迎入住 <b>{hotel_name}</b>！\n"
                f"🚪 您的房间：<b>{room_id}</b>\n\n"
                "有需要随时叫我，看看我能帮您做些什么："
            )
            self._send_reply(token, chat_id, welcome_text,
                             self._get_guest_keyboard(), parse_mode="HTML", ad=True)

            boss_chat_id = db.get_config("telegram_chat_id")
            if boss_chat_id:
                self._send_to_target(
                    token, boss_chat_id,
                    f"📲 <b>客人已连上</b>\n"
                    f"🏠 {room_id} 的客人正在用手机使用客房服务，有需求会通知大家。\n"
                    f"🕐 时间：{self._now()}"
                )
            logger.info("客房深链接绑定成功: room=%s, chat_id=%s", room_id, chat_id)

        except Exception as e:
            logger.warning("_handle_room_deeplink 异常: %s", e)
            self._send_reply(token, chat_id, "❌ 服务暂时不可用，请联系前台。")

    # ── 客人服务：查房费余额 ──

    def _handle_balance_check(self, token, chat_id):
        """客人查询当前房间费用余额。"""
        room_id = self.chat_to_room.get(chat_id, "")
        if not room_id:
            self._send_reply(token, chat_id, "⚠️ 暂时无法确定您的房间，请联系前台或重新扫码。")
            return

        sym = db.get_config("currency_symbol") or "฿"
        try:
            # 获取当前在住客人入住时设置的房价
            guest_row = db.execute(
                "SELECT checkin_time, price FROM guests WHERE room_id=? AND status='INHOUSE' ORDER BY checkin_time DESC LIMIT 1",
                (room_id,),
            ).fetchone()
            if not guest_row:
                self._send_reply(token, chat_id, "⚠️ 您的房间暂无入住记录，请确认已办理入住。")
                return

            from datetime import datetime
            cin = str(guest_row[0] or "").replace("T", " ").strip()[:19]
            price_per_night = float(guest_row[1] or 0)

            # 计算已住天数
            try:
                cin_dt = datetime.strptime(cin[:10], "%Y-%m-%d")
                nights = max(1, (datetime.now() - cin_dt).days + 1)
            except Exception:
                nights = 1

            total_charged = price_per_night * nights
            deposit_net = self._get_room_deposit_balance(room_id)

            # 已付房费（从 ledger）
            paid_row = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE room_id=? AND tx_type IN ('ROOM_IN','DEPOSIT_IN')",
                (room_id,),
            ).fetchone()
            paid = float(paid_row[0]) if paid_row else 0.0
            balance = deposit_net  # 押金余额

            lines = [
                f"💰 <b>房间费用明细</b>",
                f"🚪 房间：<b>{room_id}</b>",
                f"📅 已住：{nights} 晚",
                f"💵 房价：{sym}{price_per_night:.0f}/晚",
                f"📊 预估房费：{sym}{total_charged:.0f}",
                f"💳 押金余额：{sym}{balance:.0f}",
            ]
            if balance < total_charged:
                lines.append(f"⚠️ 押金余额不足，请联系前台续费。")
            self._send_reply(token, chat_id, "\n".join(lines), parse_mode="HTML")

        except Exception as e:
            self._send_reply(token, chat_id, f"❌ 查询失败，请联系前台。")
            logger.warning("balance check error: %s", e)

    # ── 客人服务：呼叫送物 ──

    def _handle_delivery_menu(self, token, chat_id):
        """客人呼叫送物到房间。"""
        room_id = self.chat_to_room.get(chat_id, "未知房间")
        req_text = "📦 客人呼叫送物到房间"
        try:
            db.record_guest_service_request(room_id, "DELIVERY", req_text, chat_id=str(chat_id))
        except Exception:
            pass
        front_target = db.get_config("front_desk_group_id") or db.get_config("front_desk_chat_id") or db.get_config("telegram_chat_id")
        if front_target:
            self._send_to_target(token, front_target,
                f"📦 <b>送物请求</b>\n🚪 房间：{room_id}\n📋 客人需要送物品到房间\n🕐 时间：{self._now()}")
        bus.guest_called.emit(room_id, "客人呼叫送物")
        self._send_reply(token, chat_id, "✅ 已通知前台，工作人员将尽快将物品送到您的房间。", self._get_guest_keyboard())

    # ── 员工命令 ──

    def _handle_staff_command(self, token, chat_id, text):
        """处理员工机器人命令（同步）。"""
        parts = text.split()
        cmd = parts[0].lower()

        # 保洁确认：/clean [room_id] [staff_id]
        if cmd == "/clean" and len(parts) >= 3:
            rid, sid = parts[1], parts[2]
            try:
                task_id = ""
                row = db.execute(
                    "SELECT task_id FROM housekeeping_tasks WHERE room_id=? AND status IN ('PENDING','ACCEPTED') ORDER BY created_at DESC LIMIT 1",
                    (rid,),
                ).fetchone()
                if row:
                    task_id = row[0]
                    db.complete_housekeeping_task(task_id, str(chat_id), sid)
                else:
                    db.execute("UPDATE rooms SET status='READY' WHERE room_id=?", (rid,))
                db.log_inventory_change(rid, "CLEAN", "SVC", 0, sid, "Via Bot /clean")
                bus.room_status_changed.emit(rid, "READY")
                self._send_reply(token, chat_id, f"✅ 房号 {rid} 已设为备好 (保洁:{sid})")
            except Exception as e:
                self._send_reply(token, chat_id, f"❌ 操作失败: {e}")
            return

        # 电表录入：/meter [room_id] [reading] [staff_id]
        if cmd == "/meter" and len(parts) >= 4:
            rid, sid = parts[1], parts[3]
            try:
                val = float(parts[2])
                hrs_def = db.get_config_float("energy_default_sold_hours", 24.0)
                db.log_energy_reading(rid, val, hrs_def, sid, note="telegram /meter", reading_mode="telegram")
                try:
                    from energy_audit_engine import record_meter_reading
                    record_meter_reading("MAIN", val, sid, source="telegram", note=f"room={rid}")
                except Exception:
                    pass
                self._send_reply(token, chat_id, f"⚡ 房号 {rid} 电表读数 {val} 已录入 (电工:{sid})")
            except ValueError:
                self._send_reply(token, chat_id, "❌ 读数格式错误，请输入数字。")
            except Exception as e:
                self._send_reply(token, chat_id, f"❌ 操作失败: {e}")
            return

        # 考勤打卡：/clockin (上班) 或 /clockout (下班)
        if cmd in ["/clockin", "/clockout"]:
            is_in = (cmd == "/clockin")
            ok, msg = db.log_attendance(str(chat_id), is_in)
            self._send_reply(token, chat_id, msg)
            return

        # 夜审报告：/nightaudit
        if cmd == "/nightaudit":
            self._handle_night_audit_report(token, chat_id)
            return

        self._send_reply(token, chat_id, "🤔 没看懂这条消息，需要帮忙吗？\n\n🧹 保洁完成：/clean 101 张三\n🔌 电表读数：/meter 101 1234 张三\n✅ 打卡上班：/clockin\n🚶 打卡下班：/clockout\n🌙 夜审报告：/nightaudit")

    # ── 夜审报告 ──

    def _handle_night_audit_report(self, token, chat_id):
        """生成夜审报告：今日收入、入住/退房数、能耗异常、待处理任务。"""
        from datetime import date
        today = date.today().isoformat()
        sym = db.get_config("currency_symbol") or "¥"

        overview = db.get_daily_overview()
        inhouse = db.execute("SELECT COUNT(*) FROM guests WHERE status='INHOUSE'").fetchone()[0] or 0
        total = db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] or 0

        try:
            checkins = db.execute(
                "SELECT COUNT(*) FROM guests WHERE date(checkin_time)=?", (today,)
            ).fetchone()[0] or 0
        except Exception:
            checkins = 0

        try:
            checkouts = db.execute(
                "SELECT COUNT(*) FROM guests WHERE date(checkout_time)=?", (today,)
            ).fetchone()[0] or 0
        except Exception:
            checkouts = 0

        dirty = db.execute("SELECT COUNT(*) FROM rooms WHERE status='DIRTY'").fetchone()[0] or 0

        try:
            anoms = db.execute(
                "SELECT COUNT(*) FROM energy_audit WHERE date(reading_time)=? AND is_anomaly=1",
                (today,),
            ).fetchone()[0] or 0
        except Exception:
            anoms = 0

        lines = [
            f"🌙 <b>夜审报告</b> — {today}",
            "",
            f"💰 今日营业额：<b>{sym}{float(overview.get('revenue', 0)):.0f}</b>",
            f"🏨 入住率：{inhouse}/{total} ({overview.get('occupancy', 0):.0f}%)",
            f"✅ 今日入住：{checkins}  |  🚪 今日退房：{checkouts}",
            f"🧹 待打扫：{dirty} 间",
            f"⚡ 能耗异常：{anoms} 条",
            f"📋 待处理：{overview.get('pending_tasks', 0)} 项",
        ]

        self._send_reply(token, chat_id, "\n".join(lines), parse_mode="HTML")

    # ── Callback 路由 ──

    def _handle_callback(self, query, token):
        chat_id = query["message"]["chat"]["id"]
        data = query.get("data") or ""
        cq_id = query.get("id")

        if data.startswith("shopc:"):
            self._handle_shop_category_callback(token, cq_id, chat_id, data)
            return

        if data.startswith("shopi:"):
            self._handle_shop_pick_callback(token, cq_id, chat_id, data)
            return

        if data.startswith("cart:"):
            self._handle_cart_callback(token, cq_id, chat_id, data)
            return

        if data.startswith("pay:"):
            self._handle_pay_callback(token, cq_id, chat_id, data)
            return

        if data.startswith("cash:"):
            self._handle_cash_callback(token, cq_id, chat_id, data)
            return

        if data.startswith("cfm:"):
            self._handle_confirm_callback(token, cq_id, chat_id, data)
            return

        if data.startswith("deliver:"):
            actor_id = ""
            try:
                actor_id = str((query.get("from") or {}).get("id") or "")
            except Exception:
                actor_id = ""
            self._handle_deliver_callback(token, cq_id, chat_id, data, actor_id)
            return

        # e.g. "payout_approve:TX_123"
        if data.startswith("payout_approve:"):
            boss_chat_id = str(db.get_config("telegram_chat_id") or "")
            if not boss_chat_id or str(chat_id) != boss_chat_id:
                self._send_reply(token, chat_id, "⛔ 无权限：只有授权账号可以审批资金申请。")
                return
            tx_id = data.split(":", 1)[1]
            bus.payout_approved.emit(tx_id)
            self._send_reply(token, chat_id, f"✅ 已成功审批资金下发请求: {tx_id}")
        elif data.startswith("payout_reject:"):
            boss_chat_id = str(db.get_config("telegram_chat_id") or "")
            if not boss_chat_id or str(chat_id) != boss_chat_id:
                self._send_reply(token, chat_id, "⛔ 无权限：只有授权账号可以操作资金申请。")
                return
            self._send_reply(token, chat_id, "❌ 已拒绝该资金申请。")
        elif data.startswith("hk_done:"):
            parts = data.split(":")
            if len(parts) >= 3:
                target, sid = parts[1], parts[2]
                try:
                    if target.startswith("HK_"):
                        rid = db.complete_housekeeping_task(target, str(chat_id), sid)
                        if not rid:
                            self._answer_callback_query(token, cq_id, "任务已处理或不存在", show_alert=True)
                            return
                    else:
                        rid = target
                        db.execute("UPDATE rooms SET status='READY' WHERE room_id=?", (rid,))
                    rt_row = db.execute("SELECT room_type FROM rooms WHERE room_id=?", (rid,)).fetchone()
                    rt = (rt_row[0] if rt_row else None) or "STANDARD"
                    bus.housekeeping_done.emit(rid, rt, sid, "standard")
                    bus.room_status_changed.emit(rid, "READY")
                    self._answer_callback_query(token, cq_id, f"房间 {rid} 已完成")
                    self._send_reply(token, chat_id, f"✅ 房间 {rid} 已打扫干净，辛苦了！")
                    front_token = get_work_bot_token()
                    front_chat = db.get_config("telegram_chat_id")
                    if front_token and front_chat:
                        import requests as _req
                        _req.post(
                            f"https://api.telegram.org/bot{front_token}/sendMessage",
                            json={"chat_id": front_chat,
                                  "text": f"🧹 房间 <b>{rid}</b> 保洁已完成（员工:{sid}），房态已更新为空净。",
                                  "parse_mode": "HTML"},
                            timeout=5
                        )
                except Exception as e:
                    self._send_reply(token, chat_id, f"❌ 更新失败: {e}")

        elif data.startswith("hk_accept:"):
            parts = data.split(":")
            if len(parts) >= 3:
                task_id, sid = parts[1], parts[2]
                try:
                    ok = db.accept_housekeeping_task(task_id, str(chat_id), sid)
                    row = db.execute("SELECT room_id FROM housekeeping_tasks WHERE task_id=?", (task_id,)).fetchone()
                    rid = row[0] if row else ""
                    self._answer_callback_query(token, cq_id, "已接单，辛苦了" if ok else "任务已被接单或不存在", show_alert=not ok)
                    if ok:
                        self._send_reply(token, chat_id, f"🧹 收到，房间 {rid} 交给您了，忙完告诉我一声！")
                except Exception as e:
                    self._answer_callback_query(token, cq_id, f"接单失败: {e}", show_alert=True)

        elif data.startswith("ota_confirm:"):
            if (db.get_config("ota_enabled") or "0") != "1":
                self._send_reply(token, chat_id, "OTA 当前是未来功能占位，尚未开通；请在前台手工登记预订。")
                return
            boss_chat_id = str(db.get_config("telegram_chat_id") or "")
            if not boss_chat_id or str(chat_id) != boss_chat_id:
                self._send_reply(token, chat_id, "⛔ 无权限：只有授权账号可以操作OTA订单。")
                return
            booking_id = data.split(":", 1)[1]
            try:
                db.execute(
                    "UPDATE ota_bookings SET status='CONFIRMED', confirmed_at=datetime('now') WHERE booking_id=?",
                    (booking_id,)
                )
                row = db.execute(
                    "SELECT guest_name, room_type, checkin_date, checkout_date, amount FROM ota_bookings WHERE booking_id=?",
                    (booking_id,)
                ).fetchone()
                if row:
                    gname, rtype, cin, cout, amt = row
                    sym = db.get_config("currency_symbol") or "¥"
                    self._send_reply(token, chat_id,
                        f"✅ <b>OTA订单已确认</b>\n"
                        f"📋 订单号：{booking_id}\n"
                        f"👤 客人：{gname}\n"
                        f"🏠 房型：{rtype}\n"
                        f"📅 入住：{cin} → {cout}\n"
                        f"💰 金额：{sym}{amt:.2f}"
                    )
                else:
                    self._send_reply(token, chat_id, f"✅ OTA订单 {booking_id} 已确认。")
                bus.ota_booking_confirmed.emit(booking_id)
            except Exception as e:
                self._send_reply(token, chat_id, f"❌ 确认失败: {e}")

        elif data.startswith("ota_reject:"):
            if (db.get_config("ota_enabled") or "0") != "1":
                self._send_reply(token, chat_id, "OTA 当前是未来功能占位，尚未开通；请在前台手工处理该消息。")
                return
            boss_chat_id = str(db.get_config("telegram_chat_id") or "")
            if not boss_chat_id or str(chat_id) != boss_chat_id:
                self._send_reply(token, chat_id, "⛔ 无权限：只有授权账号可以操作OTA订单。")
                return
            booking_id = data.split(":", 1)[1]
            try:
                db.execute(
                    "UPDATE ota_bookings SET status='REJECTED', confirmed_at=datetime('now') WHERE booking_id=?",
                    (booking_id,)
                )
                self._send_reply(token, chat_id,
                    f"❌ <b>OTA订单已拒绝</b>\n📋 订单号：{booking_id}\n\n请及时通知客人并安排退款。")
                bus.ota_booking_rejected.emit(booking_id)
            except Exception as e:
                self._send_reply(token, chat_id, f"❌ 拒绝操作失败: {e}")

    # ── WiFi ──

    def _handle_wifi(self, token, chat_id):
        """📶 WiFi 一键查看"""
        wifi_name = db.get_config("wifi_name") or "未配置"
        wifi_pwd = db.get_config("wifi_password") or "未配置"
        hotel_name = db.get_config("hotel_name") or "酒店"
        text = (
            f"📶 <b>{hotel_name} WiFi 信息</b>\n\n"
            f"🌐 网络名称：<code>{wifi_name}</code>\n"
            f"🔑 WiFi 密码：<code>{wifi_pwd}</code>\n\n"
            "💡 点击密码可直接复制"
        )
        self._send_reply(token, chat_id, text, self._get_guest_keyboard(), parse_mode="HTML", ad=True)

    # ── 超市 / 品类 ──

    def _shop_cat_label(self, cat_key: str) -> str:
        if not (cat_key or "").strip():
            return i18n.t("shop_cat_uncategorized")
        return (cat_key or "").strip()

    def _prune_shop_guest_state(self, now: float) -> None:
        for d in (self._guest_shop_pick, self._guest_shop_cats):
            for k, v in list(d.items()):
                if now > float(v[0]):
                    try:
                        del d[k]
                    except KeyError:
                        pass

    def _load_shop_in_stock_grouped(self):
        try:
            rows = db.execute(
                """
                SELECT sku, COALESCE(emoji,''), name, price, stock,
                       TRIM(COALESCE(category,'')),
                       COALESCE(NULLIF(TRIM(telegram_label), ''), name)
                FROM shop_items
                WHERE COALESCE(listed,0)=1 AND COALESCE(stock,0) > 0
                ORDER BY COALESCE(category,''), COALESCE(sort_order,9999), name
                LIMIT 300
                """
            ).fetchall()
        except Exception:
            return {}, []
        groups = {}
        for sku, emoji, name, price, stock, cat, tg_name in rows:
            ck = (cat or "").strip()
            groups.setdefault(ck, []).append((sku, emoji, tg_name or name, price, stock))
        keys = sorted(groups.keys(), key=lambda k: (k == "", (k or "").lower()))
        return groups, keys

    def _send_shop_category_menu(self, token, chat_id):
        now = time.monotonic()
        self._prune_shop_guest_state(now)
        groups, keys = self._load_shop_in_stock_grouped()
        if not keys:
            self._send_reply(token, chat_id, i18n.t("tg_shop_empty"), self._get_back_keyboard())
            return
        self._guest_shop_cats[str(chat_id)] = (now + 600.0, list(keys))
        lines = [
            "🛒 <b>" + html.escape(i18n.t("tg_shop_menu_title")) + "</b>",
            "",
            i18n.t("tg_shop_pick_category_hint"),
            "",
        ]
        rows_inline = []
        for i, ck in enumerate(keys):
            n = len(groups[ck])
            lab = self._shop_cat_label(ck)
            label = f"{lab} · {n}{i18n.t('tg_shop_cat_items_suffix')}"
            if len(label) > 120:
                label = label[:119] + "…"
            lines.append(f"{i + 1}. <b>{html.escape(lab)}</b> — {n}{i18n.t('tg_shop_cat_items_suffix')}")
            rows_inline.append([{"text": label, "callback_data": f"shopc:{i}"}])
        cart_qty = self._cart_total_qty(chat_id)
        cart_total = self._cart_total(chat_id)
        sym = db.get_config("currency_symbol") or "฿"
        if cart_qty > 0:
            rows_inline.append([{
                "text": f"🛒 查看购物车（{cart_qty} 件 · {sym}{cart_total:.0f}）",
                "callback_data": "cart:view",
            }])
        rows_inline.append([{"text": i18n.t("tg_shop_inline_back"), "callback_data": "shopc:main"}])
        body = "\n".join(lines)
        footer = "\n\n" + i18n.t("tg_shop_tap_category_btn")
        markup = {"inline_keyboard": rows_inline}
        self._send_reply(token, chat_id, body + footer, markup, parse_mode="HTML")

    def _send_shop_product_list(self, token, chat_id, items, *, cat_key: str, show_cat_back: bool):
        """[sub-i] 改造：每个商品先 send_photo 发图 + caption + inline"加入购物车"按钮。

        行为：
          • 前 PHOTO_BATCH_LIMIT 件商品逐个发图（PNG/_tg.jpg 优先，无图走 emoji 大字号兜底）
          • 超出 PHOTO_BATCH_LIMIT 的商品合并为一条文字列表 + inline 按钮（兼容旧行为）
          • 批量发图加 PHOTO_SEND_DELAY 秒间隔避免 Telegram 限流
          • sku_order 仍按显示顺序记录所有 SKU，保证 callback_data=f"shopi:{i}" 索引一致
          • 发图失败自动回退文字（telegram_notify.send_photo 内置兜底）
        """
        now = time.monotonic()
        self._prune_shop_guest_state(now)
        if not items:
            self._send_reply(token, chat_id, i18n.t("tg_shop_empty"), self._get_back_keyboard())
            return
        max_items = 40
        total = len(items)
        truncated = total > max_items
        items = items[:max_items]
        sym = db.get_config("currency_symbol") or "฿"
        cat_title = self._shop_cat_label(cat_key)

        # sku_order 必须按显示顺序记录所有 SKU（发图区 + 文字区连续编号），
        # 这样 callback_data=f"shopi:{i}" 才能正确映射回 SKU。
        sku_order: list[str] = []

        # ── 头部消息：分类标题 + 商品数提示 ──
        header_lines = [
            f"🛒 <b>{html.escape(cat_title)}</b>",
            f"📦 共 {total} 件商品" + (f"（仅展示前 {max_items} 件）" if truncated else ""),
        ]
        if total > PHOTO_BATCH_LIMIT:
            header_lines.append(
                f"📸 前 {PHOTO_BATCH_LIMIT} 件已发实图，余 {total - PHOTO_BATCH_LIMIT} 件见下方文字列表"
            )
        self._send_reply(token, chat_id, "\n".join(header_lines), parse_mode="HTML")

        # ── 第一段：逐个发图（PHOTO_BATCH_LIMIT 件以内） ──
        photo_items = items[:PHOTO_BATCH_LIMIT]
        for i, (sku, emoji, name, price, stock) in enumerate(photo_items):
            sk = str(sku).strip()
            sku_order.append(sk)
            name_s = " ".join(str(name or "").split()).strip() or sk
            try:
                pf = float(price or 0)
            except (TypeError, ValueError):
                pf = 0.0
            try:
                st = int(stock or 0)
            except (TypeError, ValueError):
                st = 0
            # [sub-i] emoji 兜底：DB emoji 空 → icon_pack 按 SKU/category 解析
            em = (emoji or "").strip() or icon_pack.get_emoji(sk, category=cat_key)

            # caption：商品名/价格/库存 + 库存预警
            warn_tag = ""
            if st <= 0:
                warn_tag = " · ⚠️ 缺货"
            elif st <= LOW_STOCK_TG_WARN:
                warn_tag = f" · ⚠️ 仅剩 {st}"
            caption = (
                f"{em} <b>{html.escape(name_s)}</b>\n"
                f"💰 {sym}{pf:.0f}  ·  库存: {st}{warn_tag}"
            )
            # inline"加入购物车"按钮
            markup = {"inline_keyboard": [[
                {"text": f"🛒 加入购物车 · {sym}{pf:.0f}", "callback_data": f"shopi:{i}"}
            ]]}

            # [sub-i] 优先发实图（items/{SKU}_tg.jpg 或 items/{SKU}.png）
            payload = icon_pack.get_telegram_payload(sk, category=cat_key)
            photo_path = payload.get("photo_path")
            if photo_path is not None:
                # 有图：sendPhoto + caption + 按钮；失败自动回退 send_message
                telegram_notify.send_photo(
                    token, chat_id, photo_path, caption,
                    reply_markup=markup, parse_mode="HTML",
                )
            else:
                # 无图：emoji 大字号 caption 兜底（emoji 单独一行放大视觉权重）
                big_caption = f"<b>{em}</b>\n\n{caption}"
                self._send_reply(token, chat_id, big_caption, markup, parse_mode="HTML")

            # 批量发图加间隔避免 Telegram 限流（最后一张不延迟）
            if i < len(photo_items) - 1:
                time.sleep(PHOTO_SEND_DELAY)

        # ── 第二段：超出 PHOTO_BATCH_LIMIT 的商品合并为文字列表（兼容旧行为） ──
        remaining = items[PHOTO_BATCH_LIMIT:]
        if remaining:
            lines = [f"📋 <b>更多商品</b>（{len(remaining)} 件）", ""]
            rows_inline = []
            for j, (sku, emoji, name, price, stock) in enumerate(remaining):
                idx = PHOTO_BATCH_LIMIT + j  # sku_order 全局索引
                em = (emoji or "").strip() or icon_pack.get_emoji(str(sku), category=cat_key)
                sk = str(sku).strip()
                sku_order.append(sk)
                name_s = " ".join(str(name or "").split()).strip() or sk
                esc = html.escape(name_s)
                try:
                    pf = float(price or 0)
                except (TypeError, ValueError):
                    pf = 0.0
                try:
                    st = int(stock or 0)
                except (TypeError, ValueError):
                    st = 0
                lines.append(
                    f"{idx + 1}. {em} <b>{esc}</b>\n"
                    f"   💰 {sym}{pf:.0f}  ·  {i18n.t('tg_shop_stock_label')}: {st}"
                )
                label = f"{em} {name_s} · {sym}{pf:.0f}"
                if len(label) > 120:
                    label = label[:119] + "…"
                rows_inline.append([{"text": label, "callback_data": f"shopi:{idx}"}])
            # 购物车 + 返回按钮
            cart_qty = self._cart_total_qty(chat_id)
            cart_total = self._cart_total(chat_id)
            if cart_qty > 0:
                rows_inline.append([{
                    "text": f"🛒 查看购物车（{cart_qty} 件 · {sym}{cart_total:.0f}）",
                    "callback_data": "cart:view",
                }])
            if show_cat_back:
                rows_inline.append([{"text": i18n.t("tg_shop_back_categories"), "callback_data": "shopc:up"}])
            rows_inline.append([{"text": i18n.t("tg_shop_inline_back"), "callback_data": "shopc:main"}])
            markup = {"inline_keyboard": rows_inline}
            self._send_reply(token, chat_id, "\n".join(lines), markup, parse_mode="HTML")
        else:
            # 没有剩余商品：单独发一条购物车 + 返回按钮
            cart_qty = self._cart_total_qty(chat_id)
            cart_total = self._cart_total(chat_id)
            rows_inline = []
            if cart_qty > 0:
                rows_inline.append([{
                    "text": f"🛒 查看购物车（{cart_qty} 件 · {sym}{cart_total:.0f}）",
                    "callback_data": "cart:view",
                }])
            if show_cat_back:
                rows_inline.append([{"text": i18n.t("tg_shop_back_categories"), "callback_data": "shopc:up"}])
            rows_inline.append([{"text": i18n.t("tg_shop_inline_back"), "callback_data": "shopc:main"}])
            footer = i18n.t("tg_shop_pick_hint")
            if cart_qty == 0:
                footer += "\n💡 点商品图下方的「加入购物车」按钮，可多件多品类一起结账。"
            else:
                footer += f"\n🛒 购物车已有 {cart_qty} 件，结账请点上方「查看购物车」。"
            markup = {"inline_keyboard": rows_inline}
            self._send_reply(token, chat_id, footer, markup, parse_mode="HTML")

        # [sub-i] 记录 sku_order（含发图区 + 文字区全部 SKU，顺序与 callback_data 索引一致）
        self._guest_shop_pick[str(chat_id)] = (now + 600.0, sku_order)

    def _handle_shop_category_callback(self, token, cq_id, chat_id, data: str):
        key = str(chat_id)
        suffix = data[6:] if data.startswith("shopc:") and len(data) > 6 else ""
        if suffix == "main":
            self._answer_callback_query(token, cq_id)
            self._guest_shop_cats.pop(key, None)
            self._guest_shop_pick.pop(key, None)
            self._send_reply(token, chat_id, i18n.t("tg_shop_back_to_menu"), self._get_guest_keyboard())
            return
        if suffix == "up":
            self._answer_callback_query(token, cq_id)
            self._guest_shop_pick.pop(key, None)
            self._send_shop_category_menu(token, chat_id)
            return
        try:
            idx = int(suffix)
        except ValueError:
            self._answer_callback_query(token, cq_id, i18n.t("tg_shop_unknown_sku"), show_alert=True)
            return
        now = time.monotonic()
        ent = self._guest_shop_cats.get(key)
        if not ent or now > float(ent[0]):
            self._answer_callback_query(token, cq_id, i18n.t("tg_shop_menu_expired"), show_alert=True)
            return
        cat_keys = ent[1]
        if idx < 0 or idx >= len(cat_keys):
            self._answer_callback_query(token, cq_id, i18n.t("tg_shop_unknown_sku"), show_alert=True)
            return
        ck = cat_keys[idx]
        groups, _ = self._load_shop_in_stock_grouped()
        prod = groups.get(ck, [])
        self._answer_callback_query(token, cq_id)
        self._send_shop_product_list(token, chat_id, prod, cat_key=ck, show_cat_back=True)

    def _handle_shop_menu(self, token, chat_id):
        """🛒 超市：多品类时先选品类；仅一类时直接进入商品列表。"""
        groups, keys = self._load_shop_in_stock_grouped()
        if not keys:
            self._send_reply(token, chat_id, i18n.t("tg_shop_empty"), self._get_back_keyboard())
            return
        if len(keys) == 1:
            ck = keys[0]
            self._send_shop_product_list(token, chat_id, groups.get(ck, []), cat_key=ck, show_cat_back=False)
        else:
            self._send_shop_category_menu(token, chat_id)

    def _handle_shop_pick_callback(self, token, cq_id, chat_id, data: str):
        """商品列表内联按钮：shopi:<idx> 加入购物车。"""
        key = str(chat_id)
        suffix = data.split(":", 1)[1] if ":" in data else ""
        if suffix == "back":
            self._answer_callback_query(token, cq_id)
            self._send_reply(token, chat_id, i18n.t("tg_shop_back_to_menu"), self._get_guest_keyboard())
            return
        try:
            idx = int(suffix)
        except ValueError:
            self._answer_callback_query(token, cq_id, i18n.t("tg_shop_unknown_sku"), show_alert=True)
            return
        ent = self._guest_shop_pick.get(key)
        now = time.monotonic()
        if not ent or now > float(ent[0]):
            self._answer_callback_query(token, cq_id, i18n.t("tg_shop_menu_expired"), show_alert=True)
            return
        skus = ent[1]
        if idx < 0 or idx >= len(skus):
            self._answer_callback_query(token, cq_id, i18n.t("tg_shop_unknown_sku"), show_alert=True)
            return
        sku = skus[idx]
        new_qty = self._add_sku_to_cart(chat_id, sku)
        if new_qty is None:
            self._answer_callback_query(token, cq_id, i18n.t("tg_shop_unknown_sku"), show_alert=True)
            return
        if new_qty == 0:
            self._answer_callback_query(token, cq_id, i18n.t("tg_shop_out_of_stock"), show_alert=True)
            return
        sym = db.get_config("currency_symbol") or "฿"
        cart_total = self._cart_total(chat_id)
        cart_count = self._cart_total_qty(chat_id)
        self._answer_callback_query(
            token, cq_id,
            f"✅ 已加入购物车（{cart_count} 件 · {sym}{cart_total:.0f}）",
        )

    def _handle_shop_order_by_sku(self, token, chat_id, sku: str):
        """旧版兼容：旧客户端文本形式 → 加入购物车并提示。"""
        sku = (sku or "").strip()
        if not sku:
            self._send_reply(token, chat_id, i18n.t("tg_shop_unknown_sku"), self._get_guest_keyboard())
            return
        new_qty = self._add_sku_to_cart(chat_id, sku)
        if new_qty is None:
            self._send_reply(token, chat_id, i18n.t("tg_shop_unknown_sku"), self._get_back_keyboard())
            return
        if new_qty == 0:
            self._send_reply(token, chat_id, i18n.t("tg_shop_out_of_stock"), self._get_back_keyboard())
            return
        self._send_cart_view(token, chat_id)

    # ── 保洁 / 前台菜单 ──

    def _handle_hk_menu(self, token, chat_id):
        """🧹 呼叫保洁菜单"""
        markup = json.dumps({
            "keyboard": [
                [{"text": "🧺 换毛巾/床单"}, {"text": "💧 补充饮用水"}],
                [{"text": "🧹 打扫房间"}, {"text": "🧴 补充洗漱用品"}],
                [{"text": "🔙 返回主菜单"}]
            ],
            "resize_keyboard": True
        })
        self._send_reply(token, chat_id, "🧹 <b>呼叫保洁</b>\n请选择您需要的保洁服务：", markup, parse_mode="HTML")

    def _handle_front_menu(self, token, chat_id):
        """🛎 呼叫前台菜单"""
        markup = json.dumps({
            "keyboard": [
                [{"text": "📦 送物品到房间"}, {"text": "🔄 申请换房"}],
                [{"text": "⏰ 续住"}, {"text": "🚪 申请退房"}],
                [{"text": "❓ 其他咨询"}, {"text": "🔙 返回主菜单"}]
            ],
            "resize_keyboard": True
        })
        self._send_reply(token, chat_id, "🛎 <b>呼叫前台</b>\n请选择您需要的服务：", markup, parse_mode="HTML")

    # ── 购物车助手 ──

    _CART_TTL = 1800.0

    def _get_cart(self, chat_id) -> dict:
        key = str(chat_id)
        now = time.monotonic()
        ent = self._guest_shop_cart.get(key)
        if not ent or now > float(ent.get("expires", 0)):
            ent = {
                "expires": now + self._CART_TTL,
                "items": [],
                "stage": "browsing",
                "pay_method": None,
                "cash_received": None,
            }
            self._guest_shop_cart[key] = ent
        else:
            ent["expires"] = now + self._CART_TTL
        return ent

    def _drop_cart(self, chat_id) -> None:
        self._guest_shop_cart.pop(str(chat_id), None)

    def _cart_total(self, chat_id) -> float:
        c = self._guest_shop_cart.get(str(chat_id))
        if not c:
            return 0.0
        return sum(float(it.get("price", 0)) * int(it.get("qty", 0)) for it in c.get("items", []))

    def _cart_total_qty(self, chat_id) -> int:
        c = self._guest_shop_cart.get(str(chat_id))
        if not c:
            return 0
        return sum(int(it.get("qty", 0)) for it in c.get("items", []))

    def _is_sku_locked(self, sku: str) -> bool:
        try:
            from inventory_audit_engine import is_sku_locked
            from inventory_baseline import make_item_id, CATEGORY_SHOP
            return bool(is_sku_locked(make_item_id(CATEGORY_SHOP, sku)))
        except Exception:
            return False

    def _add_sku_to_cart(self, chat_id, sku: str) -> int | None:
        sku = (sku or "").strip()
        if not sku:
            return None
        row = db.execute(
            "SELECT COALESCE(emoji,''), name, price, COALESCE(stock,0) "
            "FROM shop_items WHERE sku=? AND COALESCE(listed,0)=1",
            (sku,),
        ).fetchone()
        if not row:
            return None
        if self._is_sku_locked(sku):
            return None
        emoji, name, price, stock = row[0], row[1], float(row[2] or 0), int(row[3] or 0)
        em = (emoji or "").strip() or "📦"
        cart = self._get_cart(chat_id)
        items = cart["items"]
        line = next((it for it in items if it.get("sku") == sku), None)
        already = int(line.get("qty", 0)) if line else 0
        if already + 1 > stock:
            return 0
        if line is None:
            items.append({
                "sku": sku, "name": name, "emoji": em,
                "price": float(price or 0), "qty": 1,
            })
            return 1
        line["qty"] = already + 1
        return line["qty"]

    def _adjust_cart_line(self, chat_id, idx: int, delta: int) -> str:
        cart = self._get_cart(chat_id)
        items = cart["items"]
        if idx < 0 or idx >= len(items):
            return "oob"
        line = items[idx]
        new_qty = int(line.get("qty", 0)) + int(delta)
        if new_qty <= 0:
            items.pop(idx)
            return "empty" if not items else "removed"
        row = db.execute("SELECT COALESCE(stock,0) FROM shop_items WHERE sku=?", (line["sku"],)).fetchone()
        stock = int(row[0] or 0) if row else 0
        if new_qty > stock:
            return "oos"
        line["qty"] = new_qty
        return "ok"

    def _get_room_deposit_balance(self, room_id: str) -> float:
        rid = (room_id or "").strip()
        if not rid:
            return 0.0
        row = db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM ledger "
            "WHERE room_id=? AND is_deposit=1 AND tx_type IN ('DEPOSIT_IN','DEPOSIT_OUT')",
            (rid,),
        ).fetchone()
        return float(row[0] if row else 0)

    # ── 购物车视图 ──

    def _send_cart_view(self, token, chat_id) -> None:
        cart = self._get_cart(chat_id)
        items = cart["items"]
        sym = db.get_config("currency_symbol") or "฿"
        if not items:
            self._send_reply(
                token, chat_id,
                "🛒 您的购物车是空的。\n👇 点「🛒 超市下单」回到商品列表选购。",
                self._get_guest_keyboard(),
            )
            return
        lines = ["🛒 <b>您的购物车</b>", ""]
        rows_inline = []
        for i, it in enumerate(items):
            em = it.get("emoji") or "📦"
            name = it.get("name") or it.get("sku") or "?"
            qty = int(it.get("qty", 0))
            price = float(it.get("price", 0))
            sub = price * qty
            lines.append(
                f"{i + 1}. {em} <b>{html.escape(str(name))}</b>\n"
                f"   {sym}{price:.0f} × {qty} = <b>{sym}{sub:.0f}</b>"
            )
            rows_inline.append([
                {"text": "➖", "callback_data": f"cart:dec:{i}"},
                {"text": f"{qty}", "callback_data": "cart:noop"},
                {"text": "➕", "callback_data": f"cart:inc:{i}"},
                {"text": "🗑", "callback_data": f"cart:del:{i}"},
            ])
        total = self._cart_total(chat_id)
        lines.append("")
        lines.append(f"💰 <b>合计：{sym}{total:.0f}</b>")
        rows_inline.append([
            {"text": "💵 去结账", "callback_data": "cart:pay"},
        ])
        rows_inline.append([
            {"text": "🛒 继续选购", "callback_data": "cart:goon"},
            {"text": "🗑 清空", "callback_data": "cart:clear"},
        ])
        rows_inline.append([
            {"text": i18n.t("tg_shop_inline_back"), "callback_data": "shopc:main"},
        ])
        body = "\n".join(lines)
        self._send_reply(
            token, chat_id, body,
            {"inline_keyboard": rows_inline},
            parse_mode="HTML",
        )

    def _send_payment_menu(self, token, chat_id) -> None:
        cart = self._get_cart(chat_id)
        if not cart["items"]:
            self._send_cart_view(token, chat_id)
            return
        cart["stage"] = "checkout"
        sym = db.get_config("currency_symbol") or "฿"
        total = self._cart_total(chat_id)
        room_id = self.chat_to_room.get(chat_id, "")
        bal = self._get_room_deposit_balance(room_id)
        dep_ok = bal + 0.009 >= total and room_id
        lines = [
            "💵 <b>请选择付款方式</b>",
            "",
            f"🚪 房间：{room_id or '未绑定'}",
            f"💰 合计：<b>{sym}{total:.0f}</b>",
            "",
        ]
        rows_inline = [[{"text": "💵 现金（送达时支付）", "callback_data": "pay:cash"}]]
        if dep_ok:
            lines.append(f"💳 当前剩余押金：{sym}{bal:.0f}")
            rows_inline.append([{"text": f"💳 抵扣押金（余 {sym}{bal:.0f}）", "callback_data": "pay:dep"}])
        else:
            lines.append(f"💳 押金余额不足或未入住：{sym}{bal:.0f}（需 {sym}{total:.0f}）")
        rows_inline.append([{"text": "🔙 返回购物车", "callback_data": "cart:view"}])
        self._send_reply(
            token, chat_id, "\n".join(lines),
            {"inline_keyboard": rows_inline},
            parse_mode="HTML",
        )

    def _send_cash_amounts_menu(self, token, chat_id) -> None:
        cart = self._get_cart(chat_id)
        if not cart["items"]:
            self._send_cart_view(token, chat_id)
            return
        cart["stage"] = "cash_input"
        cart["pay_method"] = "CASH"
        sym = db.get_config("currency_symbol") or "฿"
        total = self._cart_total(chat_id)
        eligible = [a for a in self._cash_quick_amounts if a >= total]
        if not eligible:
            eligible = [self._cash_quick_amounts[-1]]
        rows_inline = []
        row = []
        for amt in eligible[:6]:
            row.append({"text": f"{sym}{amt}", "callback_data": f"cash:{amt}"})
            if len(row) == 3:
                rows_inline.append(row)
                row = []
        if row:
            rows_inline.append(row)
        rows_inline.append([
            {"text": f"✏ 正好 {sym}{total:.0f}", "callback_data": "cash:exact"},
        ])
        rows_inline.append([{"text": "🔙 返回付款方式", "callback_data": "cart:pay"}])
        text = (
            "💵 <b>客人打算给多少现金？</b>\n"
            f"💰 应付：<b>{sym}{total:.0f}</b>\n\n"
            "选好后送货员上门时按此金额收钱并准备找零。\n"
            "（点「正好」表示不需找零。）"
        )
        self._send_reply(
            token, chat_id, text,
            {"inline_keyboard": rows_inline},
            parse_mode="HTML",
        )

    def _send_confirm_summary(self, token, chat_id) -> None:
        cart = self._get_cart(chat_id)
        if not cart["items"]:
            self._send_cart_view(token, chat_id)
            return
        cart["stage"] = "confirming"
        sym = db.get_config("currency_symbol") or "฿"
        total = self._cart_total(chat_id)
        room_id = self.chat_to_room.get(chat_id, "未知房间")
        pay = cart.get("pay_method") or "CASH"
        cash_r = float(cart.get("cash_received") or 0)
        change = max(0.0, cash_r - total) if pay == "CASH" else 0.0
        lines = [
            "✅ <b>确认下单</b>",
            "",
            f"🚪 房间：<b>{room_id}</b>",
            "📦 商品：",
        ]
        for it in cart["items"]:
            em = it.get("emoji") or "📦"
            lines.append(
                f"  · {em} {html.escape(str(it.get('name', '?')))}"
                f" × {int(it.get('qty', 0))} = {sym}{float(it.get('price', 0)) * int(it.get('qty', 0)):.0f}"
            )
        lines.append("")
        lines.append(f"💰 合计：<b>{sym}{total:.0f}</b>")
        if pay == "CASH":
            lines.append(f"💵 付款：现金 {sym}{cash_r:.0f}")
            if change > 0.005:
                lines.append(f"💸 找零：<b>{sym}{change:.0f}</b>")
            else:
                lines.append("💸 找零：无（正好）")
        else:
            bal = self._get_room_deposit_balance(room_id)
            lines.append(f"💳 付款：抵扣押金 {sym}{total:.0f}")
            lines.append(f"💳 押金余额：{sym}{bal:.0f} → {sym}{(bal - total):.0f}")
        lines.append("")
        lines.append("点「确认下单」后将通知前台尽快送货上门。")
        rows_inline = [[
            {"text": "✅ 确认下单", "callback_data": "cfm:yes"},
            {"text": "🔙 返回", "callback_data": "cart:pay"},
        ]]
        self._send_reply(
            token, chat_id, "\n".join(lines),
            {"inline_keyboard": rows_inline},
            parse_mode="HTML",
        )

    # ── 购物车 callback 路由 ──

    def _handle_cart_callback(self, token, cq_id, chat_id, data: str) -> None:
        parts = data.split(":")
        action = parts[1] if len(parts) >= 2 else ""
        if action == "noop":
            self._answer_callback_query(token, cq_id)
            return
        if action == "view":
            self._answer_callback_query(token, cq_id)
            self._send_cart_view(token, chat_id)
            return
        if action == "goon":
            self._answer_callback_query(token, cq_id)
            self._send_shop_category_menu(token, chat_id)
            return
        if action == "clear":
            self._drop_cart(chat_id)
            self._answer_callback_query(token, cq_id, "已清空购物车")
            self._send_cart_view(token, chat_id)
            return
        if action == "pay":
            self._answer_callback_query(token, cq_id)
            self._send_payment_menu(token, chat_id)
            return
        if action in ("inc", "dec", "del"):
            try:
                idx = int(parts[2])
            except (IndexError, ValueError):
                self._answer_callback_query(token, cq_id, "操作失败", show_alert=True)
                return
            if action == "del":
                cart = self._get_cart(chat_id)
                if 0 <= idx < len(cart["items"]):
                    cart["items"].pop(idx)
                self._answer_callback_query(token, cq_id, "已删除")
                self._send_cart_view(token, chat_id)
                return
            delta = 1 if action == "inc" else -1
            status = self._adjust_cart_line(chat_id, idx, delta)
            if status == "oos":
                self._answer_callback_query(token, cq_id, "库存不足", show_alert=True)
            elif status == "oob":
                self._answer_callback_query(token, cq_id, "操作失败", show_alert=True)
            else:
                self._answer_callback_query(token, cq_id)
            self._send_cart_view(token, chat_id)
            return
        self._answer_callback_query(token, cq_id)

    def _handle_pay_callback(self, token, cq_id, chat_id, data: str) -> None:
        parts = data.split(":")
        action = parts[1] if len(parts) >= 2 else ""
        cart = self._get_cart(chat_id)
        if not cart["items"]:
            self._answer_callback_query(token, cq_id, "购物车空", show_alert=True)
            self._send_cart_view(token, chat_id)
            return
        if action == "cash":
            self._answer_callback_query(token, cq_id)
            self._send_cash_amounts_menu(token, chat_id)
            return
        if action == "dep":
            room_id = self.chat_to_room.get(chat_id, "")
            total = self._cart_total(chat_id)
            bal = self._get_room_deposit_balance(room_id)
            if bal + 0.009 < total:
                self._answer_callback_query(token, cq_id, "押金余额不足", show_alert=True)
                self._send_payment_menu(token, chat_id)
                return
            cart["pay_method"] = "DEPOSIT"
            cart["cash_received"] = 0.0
            self._answer_callback_query(token, cq_id)
            self._send_confirm_summary(token, chat_id)
            return
        self._answer_callback_query(token, cq_id)

    def _handle_cash_callback(self, token, cq_id, chat_id, data: str) -> None:
        parts = data.split(":")
        action = parts[1] if len(parts) >= 2 else ""
        cart = self._get_cart(chat_id)
        if not cart["items"]:
            self._answer_callback_query(token, cq_id, "购物车空", show_alert=True)
            self._send_cart_view(token, chat_id)
            return
        total = self._cart_total(chat_id)
        if action == "exact":
            cart["pay_method"] = "CASH"
            cart["cash_received"] = total
            self._answer_callback_query(token, cq_id)
            self._send_confirm_summary(token, chat_id)
            return
        try:
            amt = float(action)
        except ValueError:
            self._answer_callback_query(token, cq_id, "金额无效", show_alert=True)
            return
        if amt + 0.009 < total:
            self._answer_callback_query(token, cq_id, "面额不足", show_alert=True)
            return
        cart["pay_method"] = "CASH"
        cart["cash_received"] = amt
        self._answer_callback_query(token, cq_id)
        self._send_confirm_summary(token, chat_id)

    def _handle_confirm_callback(self, token, cq_id, chat_id, data: str) -> None:
        parts = data.split(":")
        action = parts[1] if len(parts) >= 2 else ""
        if action == "yes":
            self._answer_callback_query(token, cq_id, "正在提交…")
            self._finalize_order(token, chat_id)
            return
        self._answer_callback_query(token, cq_id)
        self._send_cart_view(token, chat_id)

    # ── 派送 callback ──

    def _handle_deliver_callback(self, token, cq_id, chat_id, data: str, actor_id: str = "") -> None:
        if not (self._staff_ops_chat_allowed(chat_id) or (actor_id and self._staff_ops_chat_allowed(actor_id))):
            self._answer_callback_query(token, cq_id, "⛔ 无权限", show_alert=True)
            return
        deliverer_tag = actor_id or str(chat_id)
        parts = data.split(":")
        if len(parts) < 3:
            self._answer_callback_query(token, cq_id, "数据错误", show_alert=True)
            return
        action, cart_id = parts[1], parts[2]
        row = db.execute(
            "SELECT room_id, status, items_json, total_amount, payment_method, cash_received, cash_change "
            "FROM pending_carts WHERE cart_id=?",
            (cart_id,),
        ).fetchone()
        if not row:
            self._answer_callback_query(token, cq_id, "订单不存在", show_alert=True)
            return
        room_id, status, items_json, total, pay, cash_r, change = row
        sym = db.get_config("currency_symbol") or "฿"
        if action == "take":
            if status not in ("PENDING",):
                self._answer_callback_query(token, cq_id, f"订单已是 {status} 状态", show_alert=True)
                return
            db.execute(
                "UPDATE pending_carts SET delivery_status='TAKING', deliverer_id=? WHERE cart_id=?",
                (deliverer_tag, cart_id),
            )
            self._answer_callback_query(token, cq_id, "✅ 已接单，请尽快送达")
            self._send_reply(
                token, chat_id,
                f"🚚 已接单：房间 {room_id}，单号 <code>{cart_id}</code>\n"
                f"💰 应收：{sym}{float(total or 0):.0f}"
                + (f"\n💵 现金 {sym}{float(cash_r or 0):.0f}，找零 {sym}{float(change or 0):.0f}" if (pay or 'CASH') == 'CASH' else f"\n💳 抵扣押金 {sym}{float(total or 0):.0f}"),
                parse_mode="HTML",
            )
            return
        if action == "done":
            if status == "FULFILLED":
                self._answer_callback_query(token, cq_id, "订单已结算过", show_alert=True)
                return
            if status == "CANCELLED":
                self._answer_callback_query(token, cq_id, "订单已作废", show_alert=True)
                return
            try:
                db.append_ledger("SHOP", float(total or 0), "CASH", 1, room_id, "TG 客房点单送达")
                if (pay or "CASH") == "DEPOSIT":
                    db.append_ledger(
                        "DEPOSIT_OUT", -float(total or 0), "CASH", 1, room_id,
                        f"客房点单抵押金 {sym}{float(total or 0):.0f}",
                        is_deposit=1,
                    )
            except Exception as e:
                logger.warning("deliver done append_ledger: %s", e)
            db.execute(
                "UPDATE pending_carts SET status='FULFILLED', delivery_status='DONE', deliverer_id=COALESCE(NULLIF(deliverer_id,''),?) "
                "WHERE cart_id=?",
                (deliverer_tag, cart_id),
            )
            try:
                bus.cart_received.emit({"cart_id": cart_id, "room_id": room_id, "fulfilled": True})
            except Exception:
                pass
            self._answer_callback_query(token, cq_id, "✅ 已送达并入账")
            try:
                guest_chat = db.execute(
                    "SELECT chat_id FROM pending_carts WHERE cart_id=?", (cart_id,)
                ).fetchone()
                if guest_chat and guest_chat[0]:
                    guest_token = get_guest_bot_token()
                    if guest_token:
                        ad_text = f"📦 您的订单 <code>{cart_id}</code> 已经送到房间啦，有什么问题随时找我！"
                        ad_sig = self._get_ad_signature()
                        if ad_sig:
                            ad_text = ad_text + "\n\n" + ad_sig
                        self._send_to_target(
                            guest_token, guest_chat[0],
                            ad_text,
                        )
            except Exception:
                pass
            return
        if action == "cancel":
            if status == "FULFILLED":
                self._answer_callback_query(token, cq_id, "订单已送达，无法作废", show_alert=True)
                return
            try:
                items = json.loads(items_json or "[]")
            except json.JSONDecodeError:
                items = []
            for it in items:
                try:
                    db.adjust_shop_stock(it.get("sku") or "", int(it.get("qty") or 0))
                except Exception:
                    pass
            db.execute(
                "UPDATE pending_carts SET status='CANCELLED', delivery_status='CANCELLED' WHERE cart_id=?",
                (cart_id,),
            )
            try:
                bus.cart_received.emit({"cart_id": cart_id, "room_id": room_id, "cancelled": True})
            except Exception:
                pass
            self._answer_callback_query(token, cq_id, "❌ 已作废，库存已回退")
            try:
                guest_chat = db.execute(
                    "SELECT chat_id FROM pending_carts WHERE cart_id=?", (cart_id,)
                ).fetchone()
                if guest_chat and guest_chat[0]:
                    guest_token = get_guest_bot_token()
                    if guest_token:
                        self._send_to_target(
                            guest_token, guest_chat[0],
                            f"😔 抱歉，订单 <code>{cart_id}</code> 暂时送不了了，已经给您取消。方便时再点一次，或联系前台帮您。",
                        )
            except Exception:
                pass
            return
        self._answer_callback_query(token, cq_id)

    # ── 最终下单 ──

    def _finalize_order(self, token, chat_id) -> None:
        cart = self._get_cart(chat_id)
        items = list(cart.get("items") or [])
        if not items:
            self._send_cart_view(token, chat_id)
            return
        sym = db.get_config("currency_symbol") or "฿"
        room_id = self.chat_to_room.get(chat_id, "未知房间")
        pay = cart.get("pay_method") or "CASH"
        total = self._cart_total(chat_id)
        cash_r = float(cart.get("cash_received") or 0)
        change = max(0.0, cash_r - total) if pay == "CASH" else 0.0

        for it in items:
            if self._is_sku_locked(it["sku"]):
                self._send_reply(
                    token, chat_id,
                    f"❗ 商品「{it.get('name')}」已被前台暂时下架（账实差异审核），订单未提交。\n"
                    "请回到购物车移除该商品后再试。",
                    self._get_guest_keyboard(),
                )
                return

        reserved: list[tuple[str, int]] = []
        out_of_stock: tuple[str, str] | None = None
        for it in items:
            sku = it["sku"]
            qty = int(it.get("qty", 0))
            if qty <= 0:
                continue
            if db.reserve_shop_stock(sku, qty):
                reserved.append((sku, qty))
            else:
                out_of_stock = (sku, it.get("name", sku))
                break
        if out_of_stock is not None:
            for sku, qty in reserved:
                try:
                    db.adjust_shop_stock(sku, qty)
                except Exception:
                    pass
            self._send_reply(
                token, chat_id,
                f"❌ 商品「{out_of_stock[1]}」库存不足，整单未提交。\n"
                "请回到购物车减少数量或换商品再试。",
                self._get_guest_keyboard(),
            )
            return

        import uuid as _uuid
        cart_id = str(_uuid.uuid4())[:8]
        items_json_str = json.dumps(items, ensure_ascii=False)
        try:
            db.execute(
                "INSERT INTO pending_carts ("
                " cart_id, room_id, items_json, total_amount, status,"
                " payment_method, cash_received, cash_change, delivery_status,"
                " chat_id, notified_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (
                    cart_id, room_id, items_json_str, total, "PENDING",
                    pay, cash_r, change, "NEW",
                    str(chat_id),
                ),
            )
        except Exception as e:
            logger.warning("pending_carts 写入失败: %s", e)
            for sku, qty in reserved:
                try:
                    db.adjust_shop_stock(sku, qty)
                except Exception:
                    pass
            self._send_reply(
                token, chat_id,
                "❌ 系统繁忙，订单未提交，请稍后再试或联系前台。",
                self._get_guest_keyboard(),
            )
            return

        for it in items:
            sku = it["sku"]
            qty = int(it.get("qty", 0))
            try:
                db.log_inventory_change(
                    room_id, "SHOP_ORDER", sku, -qty,
                    f"tg:{chat_id}", f"telegram guest order {cart_id}",
                )
            except Exception as le:
                logger.warning("inventory log: %s", le)
            try:
                from inventory_baseline import record_shop_movement, MOVE_SALE
                record_shop_movement(
                    db,
                    sku=sku,
                    move_type=MOVE_SALE,
                    qty_change=-qty,
                    unit_cost=float(it.get("price") or 0),
                    related_room=str(room_id or ""),
                    related_order=cart_id,
                    operator_id=f"tg:{chat_id}",
                    note=f"客人 Bot 下单 {it.get('name', sku)}",
                )
            except Exception as ce:
                logger.warning("销售入链失败: %s", ce)

        self._send_order_to_front_desk(
            token, cart_id, room_id, items, total,
            pay, cash_r, change, chat_id,
        )

        try:
            bus.cart_received.emit({
                "cart_id": cart_id,
                "room_id": room_id,
                "amount": float(total),
                "items": items,
                "payment_method": pay,
                "cash_received": cash_r,
                "cash_change": change,
            })
        except Exception:
            pass

        lines = [
            "✅ <b>订单已提交！</b>",
            "",
            f"🚪 房间：{room_id}",
            "📦 商品：",
        ]
        for it in items:
            lines.append(
                f"  · {it.get('emoji', '📦')} {html.escape(str(it.get('name', '?')))} × {int(it.get('qty', 0))}"
            )
        lines.append(f"💰 合计：{sym}{total:.0f}")
        if pay == "CASH":
            if change > 0.005:
                lines.append(f"💵 送货员到房间收 {sym}{cash_r:.0f}，找零 {sym}{change:.0f}。")
            else:
                lines.append(f"💵 送货员到房间收现金 {sym}{cash_r:.0f}（正好）。")
        else:
            lines.append(f"💳 已从押金扣 {sym}{total:.0f}。")
        lines.append("")
        lines.append("⏰ 前台已收到订单，将尽快送达您的房间。")
        self._send_reply(
            token, chat_id,
            "\n".join(lines),
            self._get_guest_keyboard(),
            parse_mode="HTML",
        )

        self._drop_cart(chat_id)

    def _send_order_to_front_desk(
        self, token, cart_id, room_id, items, total,
        pay, cash_r, change, chat_id,
    ) -> None:
        front_target = (
            db.get_config("front_desk_group_id")
            or db.get_config("front_desk_chat_id")
            or db.get_config("telegram_chat_id")
        )
        if not front_target:
            return
        sym = db.get_config("currency_symbol") or "฿"
        lines = [
            "🛒 <b>客房点单 · 待派送</b>",
            f"🚪 房间：<b>{room_id}</b>",
            f"🆔 单号：<code>{cart_id}</code>",
            "",
            "📦 商品：",
        ]
        for it in items:
            lines.append(
                f"  · {it.get('emoji', '📦')} {html.escape(str(it.get('name', '?')))}"
                f" × {int(it.get('qty', 0))} = {sym}{float(it.get('price', 0)) * int(it.get('qty', 0)):.0f}"
            )
        lines.append("")
        lines.append(f"💰 合计：<b>{sym}{total:.0f}</b>")
        if pay == "CASH":
            lines.append(f"💵 付款：<b>现金</b>")
            lines.append(f"   收：{sym}{cash_r:.0f}    找零：<b>{sym}{change:.0f}</b>")
        else:
            lines.append(f"💳 付款：<b>抵扣押金 {sym}{total:.0f}</b>")
        lines.append(f"🕐 下单：{self._now()}")
        lines.append("")
        lines.append("⚠️ 请尽快出货并配送")
        buttons = [[
            {"text": "🚚 我接单送", "callback_data": f"deliver:take:{cart_id}"},
            {"text": "✅ 已送达", "callback_data": f"deliver:done:{cart_id}"},
        ], [
            {"text": "❌ 作废订单", "callback_data": f"deliver:cancel:{cart_id}"},
        ]]
        self._send_to_target(token, front_target, "\n".join(lines), buttons=buttons)
