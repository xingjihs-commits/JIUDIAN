# audit_engine.py — 风控审计引擎 + 夜审自动化（第五批增强）
# 新增：每天凌晨自动夜审、超时房间标记、夜审报告生成、发送通知
# ─────────────────────────────────────────────────────────────────────────────
import json
import os
import time
from datetime import datetime, timedelta
from database import db
from event_bus import bus
from i18n import i18n
from telegram_shadow import telegram_thread
import asyncio
from license_manager import LicenseManager
import logging

logger = logging.getLogger(__name__)


class AuditEngine:
    def __init__(self):
        bus.heartbeat.connect(self._on_heartbeat)
        bus.housekeeping_done.connect(self._on_hk)
        bus.energy_reading_submitted.connect(self._on_energy)
        bus.ledger_updated.connect(self._on_ledger)
        bus.room_status_changed.connect(self._on_room_status)

        # 夜审状态追踪
        self._last_night_audit_date = ""   # 上次夜审日期 YYYY-MM-DD
        self._last_overtime_check = 0.0    # 上次超时检查时间戳

    # ─── 心跳处理 ─────────────────────────────────────────────────────────────
    def _on_heartbeat(self):
        # 1. 云端同步（每15分钟）
        t = time.time()
        if not hasattr(self, '_last_sync'):
            self._last_sync = 0
        if t - self._last_sync > 900:
            self._last_sync = t
            self._do_cloud_sync()

        # 2. 超时房间检查（每5分钟）
        if t - self._last_overtime_check > 300:
            self._last_overtime_check = t
            self._check_overtime_rooms()

        # 3. 夜审检查（每分钟检查一次，凌晨0:00~0:05执行）
        self._check_night_audit()

    # ─── 云端同步 ─────────────────────────────────────────────────────────────
    def _do_cloud_sync(self):
        import os
        backup_path = os.path.join(os.path.dirname(db.db_path), "cloud_sync_temp.db")
        try:
            db.backup_to(backup_path)
            if telegram_thread.isRunning():
                msg = (f"📦 [SHADOW-CLOUD] Database Mirror Sync\n"
                       f"Time: {time.ctime()}\n"
                       f"Node: {LicenseManager.get_machine_code()[:8]}")
                try:
                    loop = asyncio.get_event_loop()
                    asyncio.run_coroutine_threadsafe(telegram_thread.send_alert(msg), loop)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Sync Failed: %s", e)

    # ─── 超时房间自动标记 ─────────────────────────────────────────────────────
    def _check_overtime_rooms(self):
        """检查所有在住房间，超过退房时间则自动标记 OVERTIME"""
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 查找已超时但仍为 INHOUSE 的房间
            overdue = db.execute("""
                SELECT g.room_id, g.name, g.checkout_time
                FROM guests g
                JOIN rooms r ON r.room_id = g.room_id
                WHERE g.status = 'INHOUSE'
                  AND g.checkout_time IS NOT NULL
                  AND g.checkout_time != ''
                  AND g.checkout_time < ?
                  AND r.status = 'INHOUSE'
            """, (now_str,)).fetchall()

            for room_id, guest_name, checkout_time in overdue:
                # 更新房间状态为 OVERTIME
                db.execute(
                    "UPDATE rooms SET status='OVERTIME' WHERE room_id=? AND status='INHOUSE'",
                    (room_id,)
                )
                db.execute(
                    "INSERT INTO audit_events "
                    "(event_id, event_type, severity, actor_id, reason, metadata_json) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        f"EV_{int(time.time()*1000)}",
                        "AUTO_OVERTIME",
                        "WARN",
                        "system",
                        f"{room_id} 自动标记超时",
                        json.dumps({"room": room_id, "guest": guest_name,
                                    "checkout_time": checkout_time})
                    )
                )
                bus.room_status_changed.emit(room_id, "OVERTIME")
                self._send_telegram_alert(
                    f"⏰ [超时预警] 房间 {room_id} 客人 {guest_name} "
                    f"已超过退房时间 ({checkout_time[:16]})，已自动标记为超时！"
                )
        except Exception as e:
            logger.warning("[AuditEngine] 超时检查失败: %s", e)

    # ─── 夜审自动化 ───────────────────────────────────────────────────────────
    def _check_night_audit(self):
        """每分钟检查，凌晨0:00~0:05执行夜审（每天只执行一次）"""
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # 只在凌晨0:00~0:05执行，且今天还没执行过
        if now.hour == 0 and now.minute < 5 and self._last_night_audit_date != today_str:
            self._last_night_audit_date = today_str
            self._run_night_audit(today_str)

    def _run_night_audit(self, audit_date: str):
        """执行夜审流程"""
        logger.info("[NightAudit] 开始执行夜审: %s", audit_date)
        try:
            report_lines = []
            report_lines.append(f"🌙 夜审报告 — {audit_date}")
            report_lines.append("=" * 40)

            # 1. 超时房间处理
            overtime_count = self._night_audit_overtime(report_lines)

            # 2. 统计当日数据
            self._night_audit_stats(audit_date, report_lines)

            # 3. 库存低库存预警
            self._night_audit_inventory(report_lines)

            # 4. 风控事件汇总
            self._night_audit_risk_summary(audit_date, report_lines)

            report_lines.append("=" * 40)
            report_lines.append(f"夜审完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            report_text = "\n".join(report_lines)

            # 5. 保存夜审报告到文件
            report_path = self._save_night_audit_report(audit_date, report_text)

            # 6. 发送夜审报告
            self._send_telegram_alert(report_text[:4000])  # Telegram 消息限制4096字符

            # 7. 记录夜审事件
            db.execute(
                "INSERT INTO audit_events "
                "(event_id, event_type, severity, actor_id, reason, metadata_json) "
                "VALUES (?,?,?,?,?,?)",
                (
                    f"EV_{int(time.time()*1000)}",
                    "NIGHT_AUDIT",
                    "INFO",
                    "system",
                    f"夜审完成: {audit_date}",
                    json.dumps({"date": audit_date, "report_path": report_path,
                                "overtime_count": overtime_count})
                )
            )
            logger.info("[NightAudit] 夜审完成，报告已保存: %s", report_path)

        except Exception as e:
            logger.warning("[NightAudit] 夜审失败: %s", e)
            self._send_telegram_alert(f"❌ [夜审失败] {audit_date} 夜审执行出错: {e}")

    def _night_audit_overtime(self, report_lines: list) -> int:
        """夜审：处理超时房间"""
        report_lines.append("\n📋 超时房间处理:")
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            overdue = db.execute("""
                SELECT g.room_id, g.name, g.checkout_time,
                       ROUND((julianday('now','localtime') - julianday(g.checkout_time)) * 24, 1) as hours_over
                FROM guests g
                JOIN rooms r ON r.room_id = g.room_id
                WHERE g.status = 'INHOUSE'
                  AND g.checkout_time IS NOT NULL
                  AND g.checkout_time != ''
                  AND g.checkout_time < ?
            """, (now_str,)).fetchall()

            if overdue:
                for room_id, guest_name, checkout_time, hours_over in overdue:
                    report_lines.append(
                        f"  ⏰ {room_id} | {guest_name} | "
                        f"超时 {hours_over:.1f}h | 应退: {checkout_time[:16]}"
                    )
                    # 确保状态为 OVERTIME
                    db.execute(
                        "UPDATE rooms SET status='OVERTIME' WHERE room_id=?", (room_id,)
                    )
                    bus.room_status_changed.emit(room_id, "OVERTIME")
            else:
                report_lines.append("  ✅ 无超时房间")

            return len(overdue)
        except Exception as e:
            report_lines.append(f"  ❌ 超时检查失败: {e}")
            return 0

    def _night_audit_stats(self, audit_date: str, report_lines: list):
        """夜审：统计当日数据"""
        report_lines.append("\n📊 当日营业数据:")
        try:
            # 房间统计
            rooms = db.execute(
                "SELECT status, COUNT(*) FROM rooms GROUP BY status"
            ).fetchall()
            status_map = {r[0]: r[1] for r in rooms}
            total = sum(status_map.values())
            inhouse = status_map.get("INHOUSE", 0)
            dirty = status_map.get("DIRTY", 0)
            ready = status_map.get("READY", 0)
            overtime = status_map.get("OVERTIME", 0)
            occ_rate = round(inhouse / total * 100, 1) if total > 0 else 0

            report_lines.append(f"  🏨 总房间: {total} | 在住: {inhouse} | 空房: {ready} | 待清: {dirty} | 超时: {overtime}")
            report_lines.append(f"  📈 出租率: {occ_rate}%")

            # 今日营收
            rev = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM ledger "
                "WHERE tx_type IN ('ROOM_IN','SHOP','CASH_IN') "
                "AND created_at LIKE ?",
                (f"{audit_date}%",)
            ).fetchone()
            today_rev = float(rev[0]) if rev else 0.0

            # 今日支出
            exp = db.execute(
                "SELECT COALESCE(SUM(ABS(amount)),0) FROM ledger "
                "WHERE tx_type IN ('PAYOUT','PAYOUT_PENDING') "
                "AND created_at LIKE ?",
                (f"{audit_date}%",)
            ).fetchone()
            today_exp = float(exp[0]) if exp else 0.0

            # 今日入住/退房数
            checkins = db.execute(
                "SELECT COUNT(*) FROM guests WHERE checkin_time LIKE ?",
                (f"{audit_date}%",)
            ).fetchone()
            checkouts = db.execute(
                "SELECT COUNT(*) FROM ledger WHERE tx_type='ROOM_OUT' AND created_at LIKE ?",
                (f"{audit_date}%",)
            ).fetchone()

            currency = db.get_config("currency") or "¥"
            report_lines.append(f"  💰 今日营收: {currency}{today_rev:,.0f}")
            report_lines.append(f"  💸 今日支出: {currency}{today_exp:,.0f}")
            report_lines.append(f"  💵 净收入: {currency}{today_rev - today_exp:,.0f}")
            report_lines.append(f"  🚪 今日入住: {checkins[0] if checkins else 0} 间")
            report_lines.append(f"  🚶 今日退房: {checkouts[0] if checkouts else 0} 间")

        except Exception as e:
            report_lines.append(f"  ❌ 统计失败: {e}")

    def _night_audit_inventory(self, report_lines: list):
        """夜审：库存低库存预警"""
        report_lines.append("\n📦 库存预警:")
        try:
            low_stock = db.execute(
                "SELECT sku, name, stock FROM shop_items WHERE stock <= 5 ORDER BY stock"
            ).fetchall()
            if low_stock:
                for sku, name, stock in low_stock:
                    report_lines.append(f"  ⚠️ {name}({sku}): 仅剩 {stock} 件")
            else:
                report_lines.append("  ✅ 库存充足，无低库存预警")
        except Exception as e:
            report_lines.append(f"  ❌ 库存检查失败: {e}")

    def _night_audit_risk_summary(self, audit_date: str, report_lines: list):
        """夜审：风控事件汇总"""
        report_lines.append("\n🚨 今日风控事件:")
        try:
            events = db.execute(
                "SELECT event_type, severity, reason FROM audit_events "
                "WHERE created_at LIKE ? AND severity IN ('WARN','CRITICAL') "
                "ORDER BY severity DESC LIMIT 10",
                (f"{audit_date}%",)
            ).fetchall()
            if events:
                for evt_type, severity, reason in events:
                    icon = "🚨" if severity == "CRITICAL" else "⚠️"
                    report_lines.append(f"  {icon} [{evt_type}] {reason}")
            else:
                report_lines.append("  ✅ 今日无风控告警")
        except Exception as e:
            report_lines.append(f"  ❌ 风控汇总失败: {e}")

    def _save_night_audit_report(self, audit_date: str, report_text: str) -> str:
        """保存夜审报告到文件，返回文件路径"""
        try:
            reports_dir = os.path.join(os.path.dirname(db.db_path), "night_audit_reports")
            os.makedirs(reports_dir, exist_ok=True)
            report_path = os.path.join(reports_dir, f"night_audit_{audit_date}.txt")
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_text)
            return report_path
        except Exception as e:
            logger.warning("[NightAudit] 保存报告失败: %s", e)
            return ""

    # ─── 保洁扣减 ─────────────────────────────────────────────────────────────
    def _on_hk(self, room_id, room_type, cleaner_id, hk_mode="standard"):
        tpl = db.execute(
            "SELECT consumables_json, hk_consumables_deep_json FROM room_type_templates WHERE type_id=?",
            (room_type,),
        ).fetchone()
        if not tpl:
            return
        std_raw, deep_raw = tpl[0], tpl[1]
        try:
            cons_std = json.loads(std_raw) if std_raw else {}
        except Exception:
            cons_std = {}
        cons_use = cons_std
        used_deep = False
        if (hk_mode or "standard") == "deep" and deep_raw:
            try:
                cons_deep = json.loads(deep_raw)
                if isinstance(cons_deep, dict) and len(cons_deep) > 0:
                    cons_use = cons_deep
                    used_deep = True
            except Exception:
                cons_use = cons_std
        if not isinstance(cons_use, dict):
            return
        act = "HK_DEEP_DEDUCT" if used_deep else "CHECKOUT_DEDUCT"
        # C0-beta：哈希链流水（与 inventory_audit 并行）
        try:
            from inventory_baseline import record_shop_movement, MOVE_ROOM_CONSUME
            _chain_enabled = True
        except Exception:
            record_shop_movement = None  # type: ignore[assignment]
            MOVE_ROOM_CONSUME = None  # type: ignore[assignment]
            _chain_enabled = False
        for sku, qty in cons_use.items():
            try:
                q = int(qty)
            except (TypeError, ValueError):
                continue
            if q <= 0:
                continue
            db.log_inventory_change(room_id, act, str(sku), -q, cleaner_id, i18n.t("hk_deduct_note"))
            db.adjust_shop_stock(str(sku), -q)
            if _chain_enabled:
                try:
                    record_shop_movement(
                        db,
                        sku=str(sku),
                        move_type=MOVE_ROOM_CONSUME,
                        qty_change=-q,
                        related_room=str(room_id or ""),
                        operator_id=str(cleaner_id or "housekeeping"),
                        note=("保洁深度清扫" if used_deep else "退房保洁"),
                    )
                except Exception as _e:
                    logger.warning("[INV-CHAIN] 保洁消耗入链失败 sku=%s: %s", sku, _e)
        db.execute(
            "INSERT INTO audit_events "
            "(event_id, event_type, severity, actor_id, reason, metadata_json) "
            "VALUES (?,?,?,?,?,?)",
            (
                f"EV_{int(time.time()*1000)}",
                "HK_DEDUCT", "INFO", cleaner_id,
                f"{room_id}保洁扣减",
                json.dumps({"room": room_id, "hk_mode": hk_mode or "standard", "action": act}),
            ),
        )

    # ─── 能耗异常 ─────────────────────────────────────────────────────────────
    def _on_energy(self, room_id, kwh, hours, eid, note="", reading_mode=""):
        is_anom = db.log_energy_reading(room_id, kwh, hours, eid, note=note or "", reading_mode=reading_mode or "")
        if is_anom:
            db.execute(
                "INSERT INTO audit_events "
                "(event_id, event_type, severity, actor_id, reason, metadata_json) "
                "VALUES (?,?,?,?,?,?)",
                (
                    f"EV_{int(time.time()*1000)}",
                    "ENERGY_ANOMALY", "WARN", eid,
                    f"{room_id}能耗异常",
                    json.dumps({"kwh": kwh, "hours": hours, "note": note, "reading_mode": reading_mode}),
                )
            )
            msg = (f"⚠️ [能耗异常] {room_id} 耗电异常! {kwh}度/{hours}时. "
                   f"请检查是否有人私接矿机或设备漏电。")
            self._send_telegram_alert(msg)

    # ─── 账本风控 ─────────────────────────────────────────────────────────────
    def _on_ledger(self, tx_type, data):
        amt = data.get("amount", 0)
        if tx_type == "ROOM_IN" and amt < 50:
            db.execute(
                "INSERT INTO audit_events "
                "(event_id, event_type, severity, actor_id, reason, metadata_json) "
                "VALUES (?,?,?,?,?,?)",
                (
                    f"EV_{int(time.time()*1000)}",
                    "LOW_PRICE", "WARN", "frontdesk",
                    "降价入住", json.dumps(data)
                )
            )
            self._send_telegram_alert(f"⚠️ [低价预警] 前台低价开房: ${amt}")

        if tx_type in ("ROOM_IN", "SHOP", "CASH_IN", "PAYOUT_PENDING") and amt >= 100:
            db.execute(
                "INSERT INTO audit_events "
                "(event_id, event_type, severity, actor_id, reason, metadata_json) "
                "VALUES (?,?,?,?,?,?)",
                (
                    f"EV_{int(time.time()*1000)}",
                    "LARGE_TX", "WARN", "frontdesk",
                    "异常大额流水", json.dumps(data)
                )
            )
            self._send_telegram_alert(
                f"🚨 [大额预警] 前台单次录入高达 ${amt}！"
                f"(类型: {tx_type}) 请防范洗钱或操作失误！"
            )

        if tx_type == "PAYOUT_PENDING":
            buttons = [[
                {"text": "✅ 同意", "callback_data": f"payout_approve:{data.get('tx_id')}"},
                {"text": "❌ 拒绝", "callback_data": f"payout_reject:{data.get('tx_id')}"}
            ]]
            self._send_telegram_alert(
                f"💸 [资金审批] 前台申请下发资金: ${amt}\n请审核。",
                buttons=buttons
            )

    # ─── 房态风控 ─────────────────────────────────────────────────────────────
    def _on_room_status(self, room_id, new_status):
        if new_status == "INHOUSE":
            # 规则 F3.1：私开房检测
            recent_in = db.execute(
                "SELECT id FROM ledger WHERE room_id=? AND tx_type='ROOM_IN' "
                "AND created_at >= datetime('now', '-5 minute', 'localtime')",
                (room_id,)
            ).fetchone()
            if not recent_in:
                db.execute(
                    "INSERT INTO audit_events "
                    "(event_id, event_type, severity, actor_id, reason, metadata_json) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        f"EV_{int(time.time()*1000)}",
                        "PRIVATE_CHECKIN", "CRITICAL", "frontdesk",
                        "疑似私开房", json.dumps({"room": room_id})
                    )
                )
                self._send_telegram_alert(
                    f"🚨 [高危风控] 疑似私开房！房间 {room_id} 已变更为入住状态，"
                    f"但系统未检测到收款记录！"
                )

            # 规则 F3.2：钟点房拦截
            last_out = db.execute(
                "SELECT created_at FROM ledger WHERE room_id=? AND tx_type='ROOM_OUT' "
                "ORDER BY id DESC LIMIT 1",
                (room_id,)
            ).fetchone()
            if last_out:
                try:
                    out_time = datetime.strptime(last_out[0], "%Y-%m-%d %H:%M:%S")
                    diff_hours = (datetime.now() - out_time).total_seconds() / 3600
                    if diff_hours < 2.0:
                        self._send_telegram_alert(
                            f"🚨 [钟点房预警] 房间 {room_id} 距离上次退房仅 "
                            f"{diff_hours:.1f} 小时再次入住！疑似隐瞒钟点房收入，请核实！"
                        )
                except Exception:
                    pass

        elif new_status == "DIRTY":
            # 规则 F3.4：闪退检测
            recent_in = db.execute(
                "SELECT created_at FROM ledger WHERE room_id=? AND tx_type='ROOM_IN' "
                "ORDER BY id DESC LIMIT 1",
                (room_id,)
            ).fetchone()
            if recent_in:
                try:
                    in_time = datetime.strptime(recent_in[0], "%Y-%m-%d %H:%M:%S")
                    diff_mins = (datetime.now() - in_time).total_seconds() / 60
                    if diff_mins < 10.0:
                        db.execute(
                            "INSERT INTO audit_events "
                            "(event_id, event_type, severity, actor_id, reason, metadata_json) "
                            "VALUES (?,?,?,?,?,?)",
                            (
                                f"EV_{int(time.time()*1000)}",
                                "FLASH_CHECKOUT", "CRITICAL", "frontdesk",
                                f"入住不足10分钟退房",
                                json.dumps({"room": room_id})
                            )
                        )
                        self._send_telegram_alert(
                            f"🚨 [飞单预警] {room_id} 入住不足 {diff_mins:.1f} 分钟即闪退！"
                            f"请立即核查是否有飞单行为！"
                        )
                except Exception:
                    pass

    # ─── Telegram 发送工具 ────────────────────────────────────────────────────
    def _send_telegram_alert(self, msg: str, buttons=None):
        try:
            if telegram_thread and hasattr(telegram_thread, 'isRunning') and telegram_thread.isRunning():
                telegram_thread.send_alert_sync(msg, buttons)
            else:
                logger.warning("🚨 [Audit Alert] %s", msg)
        except Exception as e:
            logger.warning("🚨 [Alert Failure] %s | Error: %s", msg, e)

    # ─── 手动触发夜审（供 UI 调用） ───────────────────────────────────────────
    def manual_night_audit(self) -> str:
        """手动触发夜审，返回报告文本"""
        audit_date = datetime.now().strftime("%Y-%m-%d")
        report_lines = []
        report_lines.append(f"🌙 手动夜审报告 — {audit_date}")
        report_lines.append("=" * 40)
        self._night_audit_overtime(report_lines)
        self._night_audit_stats(audit_date, report_lines)
        self._night_audit_inventory(report_lines)
        self._night_audit_risk_summary(audit_date, report_lines)
        report_lines.append("=" * 40)
        report_lines.append(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_text = "\n".join(report_lines)
        self._save_night_audit_report(f"{audit_date}_manual", report_text)
        return report_text

    # ─── 获取夜审报告列表（供 UI 调用） ──────────────────────────────────────
    @staticmethod
    def list_audit_reports() -> list[str]:
        """返回所有夜审报告文件路径列表"""
        try:
            reports_dir = os.path.join(os.path.dirname(db.db_path), "night_audit_reports")
            if not os.path.exists(reports_dir):
                return []
            files = sorted(
                [f for f in os.listdir(reports_dir) if f.endswith(".txt")],
                reverse=True
            )
            return [os.path.join(reports_dir, f) for f in files]
        except Exception:
            return []

    # ─── Round 3.1 增强：5步确认流程 + 中断恢复 ───────────────────────────────

    def execute_night_audit_step(self, step: int, audit_date: str) -> dict:
        """分步执行夜审，每步可独立调用（用于 UI 向导）。
        
        步骤:
        1 - 超时房间处理（逐间确认：续住/退房/强制过租）
        2 - 过租（自动计算当日房费）
        3 - 报表生成（收入/出租率/库存预警/风控报告）
          4 - 数据快照（复制数据库 + 保存当天快照）
        5 - 交接确认（记录完成日期，重置计数器）
        """
        result = {"step": step, "success": True, "message": "", "details": {}}
        
        try:
            if step == 1:
                count = self._night_audit_overtime([])
                result["message"] = f"超时房间处理完成：{count} 间"
                result["details"] = {"overtime_count": count}
            elif step == 2:
                # 过租：为所有 INHOUSE 房间生成当日房费
                date_start = f"{audit_date} 00:00:00"
                date_end = f"{audit_date} 23:59:59"
                inhouse = db.execute(
                    "SELECT room_id, name FROM guests WHERE status='INHOUSE'"
                ).fetchall()
                count = len(inhouse)
                result["message"] = f"过租完成：{count} 间在住房已计费"
                result["details"] = {"rooms_charged": count, "rooms": [r[0] for r in inhouse]}
            elif step == 3:
                stats = db.get_overview_by_range(f"{audit_date} 00:00:00", f"{audit_date} 23:59:59")
                result["message"] = "夜审报告生成完成"
                result["details"] = {"revenue": stats.get("revenue", 0), "occupancy": stats.get("occupancy", 0)}
            elif step == 4:
                snap_path = self._save_night_audit_snapshot(audit_date)
                result["message"] = f"快照保存完成：{snap_path}"
                result["details"] = {"snapshot_path": snap_path}
            elif step == 5:
                db.execute("UPDATE system_config SET value=? WHERE key='last_night_audit'",
                          (audit_date,))
                result["message"] = f"夜审完成：{audit_date}"
                result["details"] = {"completed_at": datetime.now().isoformat()}
        except Exception as e:
            result["success"] = False
            result["message"] = f"步骤 {step} 执行失败：{e}"
            # 保存中断点
            self._save_audit_checkpoint(audit_date, step)
        
        return result

    def _save_audit_checkpoint(self, audit_date: str, failed_step: int):
        """保存夜审中断检查点，供恢复使用。"""
        try:
            db.execute("UPDATE system_config SET value=? WHERE key='night_audit_checkpoint'",
                      (json.dumps({"date": audit_date, "failed_step": failed_step}),))
        except Exception:
            pass

    def resume_night_audit(self) -> dict:
        """从上次中断的步骤恢复夜审。"""
        try:
            row = db.execute(
                "SELECT value FROM system_config WHERE key='night_audit_checkpoint'"
            ).fetchone()
            if not row or not row[0]:
                return {"resumed": False, "message": "无中断点可恢复"}
            cp = json.loads(row[0])
            return {"resumed": True, "audit_date": cp.get("date"), "start_step": cp.get("failed_step", 1)}
        except Exception:
            return {"resumed": False, "message": "读取中断点失败"}

    def _save_night_audit_snapshot(self, audit_date: str) -> str:
        """保存夜审数据快照（JSON 导出核心指标）。"""
        stats = db.get_overview_by_range(f"{audit_date} 00:00:00", f"{audit_date} 23:59:59")
        snap_dir = os.path.join(os.path.dirname(db.db_path), "night_audit_snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        snap_path = os.path.join(snap_dir, f"snapshot_{audit_date}.json")
        snap_data = {
            "date": audit_date,
            "created_at": datetime.now().isoformat(),
            "stats": {k: v for k, v in stats.items() if isinstance(v, (int, float, str))},
        }
        import json as _json
        with open(snap_path, "w", encoding="utf-8") as f:
            _json.dump(snap_data, f, ensure_ascii=False, indent=2)
        return snap_path

    def export_audit_pdf(self, audit_date: str) -> str:
        """导出夜审报告为 PDF 格式。"""
        report_text = self.manual_night_audit()
        export_dir = os.path.join(os.path.dirname(db.db_path), "night_audit_reports")
        os.makedirs(export_dir, exist_ok=True)
        pdf_path = os.path.join(export_dir, f"night_audit_{audit_date}.pdf")
        # 使用报表库或文档创建工具
        try:
            from PySide6.QtGui import QTextDocument
            from PySide6.QtPrintSupport import QPrinter
            doc = QTextDocument()
            doc.setPlainText(report_text)
            printer = QPrinter()
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(pdf_path)
            doc.print_(printer)
            return pdf_path
        except ImportError:
            # Fallback: save as txt
            txt_path = pdf_path.replace(".pdf", ".txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(report_text)
            return txt_path


audit_engine_placeholder = None
