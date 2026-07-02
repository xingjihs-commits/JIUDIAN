# ota_connector.py — OTA 在线预订对接模块
# 支持：美团/携程/订房平台 网络钩子接收
# 自动创建预订、冲突检测、消息通知老板确认/拒绝
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import show_warning, show_info
from design_tokens import _p
from PySide6.QtGui import QColor
import logging
logger = logging.getLogger(__name__)

# ─── 常量 ────────────────────────────────────────────────────────────────────
OTA_SOURCES = {
    "meituan": "美团",
    "ctrip": "携程",
    "booking": "Booking.com",
    "agoda": "Agoda",
    "manual": "手动录入",
}

STATUS_PENDING = "PENDING"      # 待确认
STATUS_CONFIRMED = "CONFIRMED"  # 已确认
STATUS_REJECTED = "REJECTED"    # 已拒绝
STATUS_CANCELLED = "CANCELLED"  # 已取消
STATUS_CHECKEDIN = "CHECKEDIN"  # 已入住
STATUS_WAITING = "WAITING"      # 等房（无可用房间）

# OTA 平台状态回传网络钩子地址（按来源区分）
OTA_CALLBACK_URLS = {
    "meituan": "https://openapi.meituan.com/v1/order/status",
    "ctrip": "https://open.ctrip.com/api/hotel/order/status",
    "booking": "https://api.booking.com/v1/reservations/status",
}

# 库存同步定时器间隔（秒）
INVENTORY_SYNC_INTERVAL = 30 * 60  # 30 分钟


# ─── 数据库初始化 ─────────────────────────────────────────────────────────────
def _ensure_ota_table():
    """确保 ota_bookings 表存在"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS ota_bookings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_no  TEXT UNIQUE NOT NULL,
            ota_source  TEXT NOT NULL DEFAULT 'manual',
            ota_order_id TEXT,
            guest_name  TEXT NOT NULL,
            guest_phone TEXT,
            room_type   TEXT,
            room_id     TEXT,
            checkin_dt  TEXT NOT NULL,
            checkout_dt TEXT NOT NULL,
            nights      INTEGER DEFAULT 1,
            total_price REAL DEFAULT 0,
            status      TEXT DEFAULT 'PENDING',
            raw_payload TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            updated_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS ota_config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # 默认配置
    for k, v in [
        ("webhook_port", "8765"),
        ("webhook_enabled", "0"),
        ("meituan_secret", ""),
        ("ctrip_secret", ""),
        ("booking_secret", ""),
        ("auto_confirm", "0"),
    ]:
        db.execute("INSERT OR IGNORE INTO ota_config(key,value) VALUES(?,?)", (k, v))


def _get_ota_config(key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM ota_config WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_ota_config(key: str, value: str):
    db.execute("INSERT OR REPLACE INTO ota_config(key,value) VALUES(?,?)", (key, value))


# ─── 预订业务逻辑 ─────────────────────────────────────────────────────────────
class OTABookingService:
    """OTA 预订业务逻辑层（静态方法）"""

    @staticmethod
    def create_booking(
        guest_name: str,
        checkin_dt: str,
        checkout_dt: str,
        ota_source: str = "manual",
        guest_phone: str = "",
        room_type: str = "",
        total_price: float = 0.0,
        ota_order_id: str = "",
        raw_payload: str = "",
    ) -> tuple[bool, str, Optional[int]]:
        """
        创建 OTA 预订
        返回 (success, message, booking_id)
        """
        _ensure_ota_table()
        if (db.get_config("ota_enabled") or "0") != "1" and ota_source != "manual":
            return False, "OTA 当前是未来占位功能，尚未开通；请先在前台手工登记。", None

        # 生成预订号
        booking_no = f"OTA{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"

        # 计算住宿天数
        try:
            ci = datetime.strptime(checkin_dt[:16], "%Y-%m-%d %H:%M")
            co = datetime.strptime(checkout_dt[:16], "%Y-%m-%d %H:%M")
            nights = max(1, (co - ci).days)
        except Exception:
            nights = 1

        # 冲突检测
        conflict = OTABookingService.detect_conflict(room_type, checkin_dt, checkout_dt)

        db.execute("""
            INSERT INTO ota_bookings
                (booking_no, ota_source, ota_order_id, guest_name, guest_phone,
                 room_type, checkin_dt, checkout_dt, nights, total_price,
                 status, raw_payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            booking_no, ota_source, ota_order_id, guest_name, guest_phone,
            room_type, checkin_dt, checkout_dt, nights, total_price,
            STATUS_PENDING, raw_payload,
        ))

        row = db.execute("SELECT last_insert_rowid()").fetchone()
        booking_id = row[0] if row else None

        # 同时写入 guests 表作为预订记录
        guest_id = f"OTA_{booking_no}"
        db.execute("""
            INSERT OR IGNORE INTO guests
                (room_id, name, phone, checkin_time, checkout_time, status, id_card)
            VALUES (?, ?, ?, ?, ?, 'RESERVED', ?)
        """, (
            room_type or "待分配",
            guest_name,
            guest_phone,
            checkin_dt,
            checkout_dt,
            guest_id,
        ))

        # 发送通知
        OTABookingService._notify_new_booking(booking_no, booking_id, guest_name,
                                               checkin_dt, checkout_dt, ota_source,
                                               total_price, conflict)

        # 自动分配房间
        if booking_id and room_type:
            OTABookingService._auto_assign_room(booking_id, room_type)

        msg = f"预订创建成功: {booking_no}"
        if conflict:
            msg += f" ⚠️ 检测到冲突: {conflict}"

        return True, msg, booking_id

    @staticmethod
    def detect_conflict(room_type: str, checkin_dt: str, checkout_dt: str) -> str:
        """检测时间段内是否有冲突预订，返回冲突描述或空字符串"""
        if not room_type:
            return ""
        rows = db.execute("""
            SELECT booking_no, guest_name, checkin_dt, checkout_dt
            FROM ota_bookings
            WHERE room_type = ?
              AND status IN ('PENDING','CONFIRMED')
              AND checkin_dt < ?
              AND checkout_dt > ?
        """, (room_type, checkout_dt, checkin_dt)).fetchall()
        if rows:
            r = rows[0]
            return f"与预订 {r[0]}({r[1]}) {r[2][:10]}~{r[3][:10]} 冲突"
        # 检查 guests 表在住
        inhouse = db.execute("""
            SELECT name FROM guests
            WHERE room_id = ? AND status = 'INHOUSE'
              AND checkout_time > ?
        """, (room_type, checkin_dt)).fetchone()
        if inhouse:
            return f"房型 {room_type} 在 {checkin_dt[:10]} 已有在住客人 {inhouse[0]}"
        return ""

    @staticmethod
    def confirm_booking(booking_id: int) -> tuple[bool, str]:
        """确认预订"""
        row = db.execute("SELECT booking_no, guest_name, room_type, checkin_dt, checkout_dt "
                         "FROM ota_bookings WHERE id=?", (booking_id,)).fetchone()
        if not row:
            return False, "预订不存在"
        booking_no, guest_name, room_type, ci, co = row
        db.execute("UPDATE ota_bookings SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                   (STATUS_CONFIRMED, booking_id))
        db.log_action("OTA", "CONFIRM_BOOKING", f"确认预订 {booking_no}")
        OTABookingService._send_tg(
            f"✅ [OTA预订已确认]\n预订号: {booking_no}\n客人: {guest_name}\n"
            f"房型: {room_type}\n入住: {ci[:10]} → {co[:10]}"
        )
        # 自动发卡
        try:
            from lock_issue_service import issue_guest_for_room
            from lock_adapters.cardlock_auto import CardLockAuto
            auto = CardLockAuto()
            if not auto.auto_configure():
                raise RuntimeError("门锁未配置")
            def auto_issue_card(bd):
                result = issue_guest_for_room({"room_no": bd.get("room_no", ""), "guest_name": bd.get("guest_name", "")})
                return {"success": result.ok, "card_id": result.card_id or ""}
            booking_data = {
                "room_no": room_type,
                "guest_name": guest_name,
                "check_in": ci,
                "check_out": co,
            }
            result = auto_issue_card(booking_data)
            if result.get("success"):
                db.execute(
                    "INSERT INTO card_records(room_id, status, issued_at) VALUES (?, 'ACTIVE', datetime('now','localtime'))",
                    (room_type,),
                )
        except Exception as e:
            bus.audit_alert.emit("ota_issue_failed", f"房{room_type}自动发卡失败: {e}")
        return True, f"预订 {booking_no} 已确认"

    @staticmethod
    def reject_booking(booking_id: int, reason: str = "") -> tuple[bool, str]:
        """拒绝预订"""
        row = db.execute("SELECT booking_no, guest_name FROM ota_bookings WHERE id=?",
                         (booking_id,)).fetchone()
        if not row:
            return False, "预订不存在"
        booking_no, guest_name = row
        db.execute("UPDATE ota_bookings SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                   (STATUS_REJECTED, booking_id))
        # 同步删除 guests 表中的 RESERVED 记录
        db.execute("DELETE FROM guests WHERE id_card=? AND status='RESERVED'",
                   (f"OTA_{booking_no}",))
        db.log_action("OTA", "REJECT_BOOKING", f"拒绝预订 {booking_no}: {reason}")
        OTABookingService._send_tg(
            f"❌ [OTA预订已拒绝]\n预订号: {booking_no}\n客人: {guest_name}\n原因: {reason or '无'}"
        )
        return True, f"预订 {booking_no} 已拒绝"

    @staticmethod
    def cancel_booking(booking_id: int) -> tuple[bool, str]:
        """取消预订（客人主动取消）"""
        row = db.execute("SELECT booking_no, guest_name FROM ota_bookings WHERE id=?",
                         (booking_id,)).fetchone()
        if not row:
            return False, "预订不存在"
        booking_no, guest_name = row
        db.execute("UPDATE ota_bookings SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                   (STATUS_CANCELLED, booking_id))
        db.execute("DELETE FROM guests WHERE id_card=? AND status='RESERVED'",
                   (f"OTA_{booking_no}",))
        OTABookingService._send_tg(
            f"🚫 [OTA预订已取消]\n预订号: {booking_no}\n客人: {guest_name}"
        )
        return True, f"预订 {booking_no} 已取消"

    @staticmethod
    def get_bookings(status_filter: str = "ALL", limit: int = 100) -> list:
        _ensure_ota_table()
        if status_filter == "ALL":
            return db.execute(
                "SELECT id,booking_no,ota_source,guest_name,guest_phone,"
                "room_type,checkin_dt,checkout_dt,nights,total_price,status,created_at "
                "FROM ota_bookings ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return db.execute(
            "SELECT id,booking_no,ota_source,guest_name,guest_phone,"
            "room_type,checkin_dt,checkout_dt,nights,total_price,status,created_at "
            "FROM ota_bookings WHERE status=? ORDER BY id DESC LIMIT ?",
            (status_filter, limit)
        ).fetchall()

    @staticmethod
    def get_today_arrivals() -> list:
        today = datetime.now().strftime("%Y-%m-%d")
        _ensure_ota_table()
        return db.execute(
            "SELECT id,booking_no,ota_source,guest_name,guest_phone,room_type,"
            "checkin_dt,checkout_dt,total_price,status "
            "FROM ota_bookings WHERE checkin_dt LIKE ? AND status IN ('PENDING','CONFIRMED') "
            "ORDER BY checkin_dt",
            (f"{today}%",)
        ).fetchall()

    @staticmethod
    def _auto_assign_room(booking_id: int, room_type: str):
        """根据房型 type_id 自动分配房间：查找 rooms 表该房型 + status='READY' 的第一间 → 写入 room_id。
        无空房则标记为'等房'状态。"""
        try:
            row = db.execute(
                "SELECT room_id FROM rooms WHERE room_type=? AND status='READY' LIMIT 1",
                (room_type,),
            ).fetchone()
            if row:
                assigned_room = row[0]
                db.execute(
                    "UPDATE ota_bookings SET room_id=?, updated_at=datetime('now','localtime') WHERE id=?",
                    (assigned_room, booking_id),
                )
                db.execute(
                    "UPDATE rooms SET status='RESERVED' WHERE room_id=?",
                    (assigned_room,),
                )
                db.log_action("OTA", "AUTO_ASSIGN",
                              f"预订 {booking_id} 自动分配房间 {assigned_room} (房型 {room_type})")
                logger.info("[OTA] 预订 %s 自动分配房间 %s (房型 %s)", booking_id, assigned_room, room_type)
            else:
                db.execute(
                    "UPDATE ota_bookings SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                    (STATUS_WAITING, booking_id),
                )
                db.log_action("OTA", "WAITING_ROOM",
                              f"预订 {booking_id} 房型 {room_type} 无可用房间，标记为等房")
                logger.warning("[OTA] 预订 %s 房型 %s 无可用房间，标记为等房", booking_id, room_type)
                OTABookingService._send_tg(
                    f"⚠️ [OTA 等房提醒]\n预订 #{booking_id} 房型 {room_type} 暂无可用房间，已标记为等房状态。请尽快调配。"
                )
        except Exception as e:
            logger.error("[OTA] 自动分配房间失败: %s", e)

    @staticmethod
    def _notify_new_booking(booking_no, booking_id, guest_name, ci, co,
                             source, price, conflict):
        source_name = OTA_SOURCES.get(source, source)
        conflict_line = f"\n⚠️ 冲突警告: {conflict}" if conflict else ""
        msg = (
            f"📥 [新OTA预订]\n"
            f"来源: {source_name}\n"
            f"预订号: {booking_no}\n"
            f"客人: {guest_name}\n"
            f"入住: {ci[:10]} → {co[:10]}\n"
            f"金额: ¥{price:.0f}"
            f"{conflict_line}"
        )
        buttons = None
        if booking_id:
            buttons = [[
                {"text": "✅ 确认", "callback_data": f"ota_confirm:{booking_id}"},
                {"text": "❌ 拒绝", "callback_data": f"ota_reject:{booking_id}"},
            ]]
        OTABookingService._send_tg(msg, buttons)

    @staticmethod
    def _send_tg(msg: str, buttons=None):
        try:
            from telegram_shadow import telegram_thread
            if telegram_thread and telegram_thread.isRunning():
                telegram_thread.send_alert_sync(msg, buttons)
        except Exception as e:
            logger.warning("[OTA] Telegram 发送失败: %s", e)

    @staticmethod
    def _push_booking_status(booking_id: int, status: str) -> bool:
        """向 OTA 平台网络钩子回传订单状态"""
        try:
            row = db.execute(
                "SELECT ota_source, ota_order_id, booking_no FROM ota_bookings WHERE id=?",
                (booking_id,),
            ).fetchone()
            if not row:
                logger.warning("[OTA] 回传状态失败: 预订 %s 不存在", booking_id)
                return False
            ota_source, ota_order_id, booking_no = row
            if not ota_order_id:
                logger.warning("[OTA] 回传状态失败: 预订 %s 无 OTA 订单号", booking_id)
                return False
            callback_url = OTA_CALLBACK_URLS.get(ota_source)
            if not callback_url:
                logger.warning("[OTA] 回传状态失败: 未知 OTA 来源 %s", ota_source)
                return False
            payload = json.dumps({
                "order_id": ota_order_id,
                "booking_no": booking_no,
                "status": status,
                "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }).encode("utf-8")
            req = urllib.request.Request(
                callback_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info("[OTA] 订单 %s 状态已回传 %s → %s (HTTP %s)",
                            booking_no, status, ota_source, resp.status)
            db.log_action("OTA", "PUSH_STATUS",
                          f"预订 {booking_no} 回传状态 {status} 至 {ota_source}")
            return True
        except urllib.error.URLError as e:
            logger.error("[OTA] 回传状态网络错误: %s", e)
        except Exception as e:
            logger.error("[OTA] 回传状态异常: %s", e)
        return False

    @staticmethod
    def _sync_room_inventory():
        """查询各房型剩余 READY 房间数 → POST 到 OTA 平台"""
        try:
            rows = db.execute(
                "SELECT room_type, COUNT(*) AS cnt FROM rooms WHERE status='READY' GROUP BY room_type"
            ).fetchall()
            inventory = {r[0]: r[1] for r in rows}
            payload = json.dumps({
                "hotel_id": _get_ota_config("hotel_external_id", ""),
                "inventory": inventory,
                "synced_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }).encode("utf-8")
            for source in ("meituan", "ctrip", "booking"):
                url = OTA_CALLBACK_URLS.get(source, "").replace("/order/status", "/inventory")
                if not url:
                    continue
                try:
                    req = urllib.request.Request(
                        url,
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        logger.info("[OTA] 库存已同步至 %s: %s (HTTP %s)", source, inventory, resp.status)
                except urllib.error.URLError as e:
                    logger.warning("[OTA] 库存同步 %s 网络错误: %s", source, e)
                except Exception as e:
                    logger.warning("[OTA] 库存同步 %s 异常: %s", source, e)
            db.log_action("OTA", "INVENTORY_SYNC",
                          f"库存同步完成: {json.dumps(inventory, ensure_ascii=False)}")
        except Exception as e:
            logger.error("[OTA] 库存同步失败: %s", e)

    @staticmethod
    def check_rate_parity(room_type: str):
        """比较同一房型在不同 OTA 中的价格差异 > 10% → 写入 audit_events 告警"""
        try:
            rows = db.execute(
                "SELECT ota_source, total_price FROM ota_bookings "
                "WHERE room_type=? AND status IN ('PENDING','CONFIRMED') "
                "ORDER BY created_at DESC LIMIT 50",
                (room_type,),
            ).fetchall()
            if len(rows) < 2:
                return
            prices_by_source = {}
            for src, price in rows:
                prices_by_source.setdefault(src, []).append(float(price))
            avg_by_source = {
                src: sum(ps) / len(ps) for src, ps in prices_by_source.items() if ps
            }
            sources = list(avg_by_source.keys())
            for i in range(len(sources)):
                for j in range(i + 1, len(sources)):
                    s1, s2 = sources[i], sources[j]
                    p1, p2 = avg_by_source[s1], avg_by_source[s2]
                    if p1 > 0 and p2 > 0:
                        diff = abs(p1 - p2) / max(p1, p2)
                        if diff > 0.10:
                            event_id = f"RATE_PARITY_{uuid.uuid4().hex[:8].upper()}"
                            db.execute(
                                "INSERT INTO audit_events (event_id, event_type, severity, actor_id, reason, metadata_json) "
                                "VALUES (?,?,?,?,?,?)",
                                (
                                    event_id,
                                    "RATE_PARITY_ALERT",
                                    "WARNING",
                                    "OTA_SYSTEM",
                                    f"房型 {room_type} 价格不一致",
                                    json.dumps({
                                        "room_type": room_type,
                                        "source_a": s1,
                                        "price_a": round(p1, 2),
                                        "source_b": s2,
                                        "price_b": round(p2, 2),
                                        "diff_pct": round(diff * 100, 1),
                                    }, ensure_ascii=False),
                                ),
                            )
                            logger.warning(
                                "[OTA] 价格不一致告警: 房型 %s, %s=¥%.0f vs %s=¥%.0f (差异 %.1f%%)",
                                room_type, s1, p1, s2, p2, diff * 100,
                            )
        except Exception as e:
            logger.error("[OTA] 价格一致性检查失败: %s", e)


# ─── Webhook HTTP 服务器 ──────────────────────────────────────────────────────
class _WebhookHandler(BaseHTTPRequestHandler):
    """处理来自 OTA 平台的网络钩子请求"""

    def log_message(self, format, *args):
        pass  # 静默日志

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        # 路由分发
        if path == "/webhook/meituan":
            self._handle_meituan(body)
        elif path == "/webhook/ctrip":
            self._handle_ctrip(body)
        elif path == "/webhook/booking":
            self._handle_booking(body)
        else:
            self.send_response(404)
            self.end_headers()
            return

    def _respond(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_meituan(self, body: bytes):
        """美团网络钩子解析"""
        try:
            secret = _get_ota_config("meituan_secret")
            if secret:
                sig = self.headers.get("X-Meituan-Signature", "")
                expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(sig.lower(), expected.lower()):
                    self._respond(401, {"error": "invalid signature"})
                    return

            payload = json.loads(body)
            order = payload.get("order", payload)
            guest_name = order.get("guestName", order.get("guest_name", "美团客人"))
            guest_phone = order.get("guestPhone", order.get("phone", ""))
            room_type = order.get("roomType", order.get("room_type", ""))
            checkin = order.get("checkInDate", order.get("checkin_date", ""))
            checkout = order.get("checkOutDate", order.get("checkout_date", ""))
            price = float(order.get("totalPrice", order.get("total_price", 0)))
            ota_order_id = str(order.get("orderId", order.get("order_id", "")))

            # 标准化日期格式
            checkin = _normalize_date(checkin)
            checkout = _normalize_date(checkout)

            ok, msg, bid = OTABookingService.create_booking(
                guest_name=guest_name, checkin_dt=checkin, checkout_dt=checkout,
                ota_source="meituan", guest_phone=guest_phone, room_type=room_type,
                total_price=price, ota_order_id=ota_order_id, raw_payload=body.decode()
            )
            self._respond(200, {"success": ok, "message": msg})
        except Exception as e:
            logger.warning("[OTA/Meituan] 解析失败: %s", e)
            self._respond(400, {"error": str(e)})

    def _handle_ctrip(self, body: bytes):
        """携程网络钩子解析"""
        try:
            secret = _get_ota_config("ctrip_secret")
            if secret:
                sig = self.headers.get("X-Ctrip-Signature", "")
                expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(sig.lower(), expected.lower()):
                    self._respond(401, {"error": "invalid signature"})
                    return

            payload = json.loads(body)
            order = payload.get("orderInfo", payload)
            guest_name = order.get("contactName", "携程客人")
            guest_phone = order.get("contactPhone", "")
            room_type = order.get("roomTypeName", "")
            checkin = _normalize_date(order.get("arrivalDate", ""))
            checkout = _normalize_date(order.get("departureDate", ""))
            price = float(order.get("totalAmount", 0))
            ota_order_id = str(order.get("orderId", ""))

            ok, msg, bid = OTABookingService.create_booking(
                guest_name=guest_name, checkin_dt=checkin, checkout_dt=checkout,
                ota_source="ctrip", guest_phone=guest_phone, room_type=room_type,
                total_price=price, ota_order_id=ota_order_id, raw_payload=body.decode()
            )
            self._respond(200, {"success": ok, "message": msg})
        except Exception as e:
            self._respond(400, {"error": str(e)})

    def _handle_booking(self, body: bytes):
        """Booking.com Webhook 解析"""
        try:
            secret = _get_ota_config("booking_secret")
            if secret:
                sig = self.headers.get("X-Booking-Signature", "")
                expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(sig.lower(), expected.lower()):
                    self._respond(401, {"error": "invalid signature"})
                    return

            payload = json.loads(body)
            reservation = payload.get("reservation", payload)
            guest = reservation.get("guest", {})
            guest_name = f"{guest.get('first_name','')} {guest.get('last_name','')}".strip() or "Booking客人"
            guest_phone = guest.get("telephone", "")
            room_type = reservation.get("room_name", "")
            checkin = _normalize_date(reservation.get("arrival_date", ""))
            checkout = _normalize_date(reservation.get("departure_date", ""))
            price = float(reservation.get("total_price", 0))
            ota_order_id = str(reservation.get("id", ""))

            ok, msg, bid = OTABookingService.create_booking(
                guest_name=guest_name, checkin_dt=checkin, checkout_dt=checkout,
                ota_source="booking", guest_phone=guest_phone, room_type=room_type,
                total_price=price, ota_order_id=ota_order_id, raw_payload=body.decode()
            )
            self._respond(200, {"success": ok, "message": msg})
        except Exception as e:
            self._respond(400, {"error": str(e)})


def _normalize_date(s: str) -> str:
    """将各种日期格式统一为 YYYY-MM-DD HH:MM"""
    if not s:
        return datetime.now().strftime("%Y-%m-%d 14:00")
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s[:len(fmt)], fmt)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            continue
    return datetime.now().strftime("%Y-%m-%d 14:00")


# ─── Webhook 服务线程 ─────────────────────────────────────────────────────────
class WebhookServerThread(QThread):
    status_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self._server: Optional[HTTPServer] = None
        self._running = False

    def run(self):
        port = int(_get_ota_config("webhook_port", "8765"))
        try:
            self._server = HTTPServer(("0.0.0.0", port), _WebhookHandler)
            self._running = True
            self.status_changed.emit(f"✅ Webhook 监听中 (端口 {port})")
            self._server.serve_forever()
        except Exception as e:
            self.status_changed.emit(f"❌ 启动失败: {e}")

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None
        self._running = False
        self.status_changed.emit("⏹ Webhook 已停止")


# 全局网络钩子线程单例
_webhook_thread: Optional[WebhookServerThread] = None


def get_webhook_thread() -> WebhookServerThread:
    global _webhook_thread
    if _webhook_thread is None:
        _webhook_thread = WebhookServerThread()
    return _webhook_thread


# ─── 库存同步定时器 ───────────────────────────────────────────────────────────
_inventory_timer: Optional[threading.Timer] = None
_inventory_timer_lock = threading.Lock()


def start_inventory_sync_timer():
    """启动库存同步定时器，每 30 分钟同步一次"""
    global _inventory_timer

    def _run():
        try:
            OTABookingService._sync_room_inventory()
        except Exception as e:
            logger.error("[OTA] 库存同步定时任务异常: %s", e)
        finally:
            _schedule_next()

    def _schedule_next():
        global _inventory_timer
        with _inventory_timer_lock:
            _inventory_timer = threading.Timer(INVENTORY_SYNC_INTERVAL, _run)
            _inventory_timer.daemon = True
            _inventory_timer.start()
            logger.info("[OTA] 下次库存同步将在 %d 分钟后执行", INVENTORY_SYNC_INTERVAL // 60)

    _schedule_next()


def stop_inventory_sync_timer():
    """停止库存同步定时器"""
    global _inventory_timer
    with _inventory_timer_lock:
        if _inventory_timer is not None:
            _inventory_timer.cancel()
            _inventory_timer = None
            logger.info("[OTA] 库存同步定时器已停止")


# ─── Telegram 回调处理（注册到 telegram_shadow） ─────────────────────────────
def handle_ota_callback(callback_data: str) -> str:
    """处理回按钮回调，返回回复文本"""
    try:
        if callback_data.startswith("ota_confirm:"):
            bid = int(callback_data.split(":")[1])
            ok, msg = OTABookingService.confirm_booking(bid)
            return msg
        elif callback_data.startswith("ota_reject:"):
            bid = int(callback_data.split(":")[1])
            ok, msg = OTABookingService.reject_booking(bid, "老板拒绝")
            return msg
    except Exception as e:
        return f"操作失败: {e}"
    return ""


# ─── UI：手动录入预订对话框 ───────────────────────────────────────────────────
class ManualBookingDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手动录入 OTA 预订")
        from ui_helpers import style_dialog
        style_dialog(self, size="medium")
        self._booking_id = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        from ui_helpers import build_dialog_header
        layout.addWidget(build_dialog_header("📥 手动录入预订", "将 OTA 平台的预订信息录入系统。"))

        form = QFormLayout()
        form.setSpacing(10)

        self.src_combo = QComboBox()
        for k, v in OTA_SOURCES.items():
            self.src_combo.addItem(v, k)
        form.addRow("来源渠道:", self.src_combo)

        self.name_edit = QLineEdit(placeholderText="客人姓名")
        form.addRow("客人姓名:", self.name_edit)

        self.phone_edit = QLineEdit(placeholderText="联系电话（选填）")
        form.addRow("联系电话:", self.phone_edit)

        # 房型下拉
        self.room_type_combo = QComboBox()
        self.room_type_combo.addItem("待分配", "")
        types = db.execute("SELECT type_id, type_name FROM room_type_templates").fetchall()
        for t in types:
            self.room_type_combo.addItem(f"{t[1]} ({t[0]})", t[0])
        form.addRow("房型:", self.room_type_combo)

        self.ci_edit = QDateTimeEdit()
        self.ci_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.ci_edit.setDateTime(
            __import__("PySide6.QtCore", fromlist=["QDateTime"]).QDateTime.currentDateTime()
        )
        form.addRow("入住时间:", self.ci_edit)

        self.co_edit = QDateTimeEdit()
        self.co_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        from PySide6.QtCore import QDateTime
        self.co_edit.setDateTime(QDateTime.currentDateTime().addDays(1))
        form.addRow("退房时间:", self.co_edit)

        self.price_spin = QSpinBox()
        self.price_spin.setRange(0, 99999)
        self.price_spin.setSuffix(" 元")
        form.addRow("预订金额:", self.price_spin)

        self.order_id_edit = QLineEdit(placeholderText="OTA 订单号（选填）")
        form.addRow("OTA订单号:", self.order_id_edit)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton("✅ 创建预订")
        btn_ok.setObjectName("SolidPrimaryBtn")
        btn_ok.clicked.connect(self._submit)
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _submit(self):
        name = self.name_edit.text().strip()
        if not name:
            show_warning(self, "提示", "请填写客人姓名")
            return
        ci = self.ci_edit.dateTime().toString("yyyy-MM-dd HH:mm")
        co = self.co_edit.dateTime().toString("yyyy-MM-dd HH:mm")
        if ci >= co:
            show_warning(self, "提示", "退房时间必须晚于入住时间")
            return

        ok, msg, bid = OTABookingService.create_booking(
            guest_name=name,
            checkin_dt=ci,
            checkout_dt=co,
            ota_source=self.src_combo.currentData(),
            guest_phone=self.phone_edit.text().strip(),
            room_type=self.room_type_combo.currentData(),
            total_price=float(self.price_spin.value()),
            ota_order_id=self.order_id_edit.text().strip(),
        )
        if ok:
            self._booking_id = bid
            show_info(self, "成功", msg)
            self.accept()
        else:
            show_warning(self, "失败", msg)


# ─── UI：OTA 设置对话框 ───────────────────────────────────────────────────────
class OTASettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OTA 对接设置")
        from ui_helpers import style_dialog
        style_dialog(self, size="medium")
        self._build_ui()

    def _build_ui(self):
        _ensure_ota_table()
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        from ui_helpers import build_dialog_header
        layout.addWidget(build_dialog_header("⚙️ OTA Webhook 设置", "配置在线旅游平台的密钥与接入端口。"))

        form = QFormLayout()
        form.setSpacing(10)

        self.port_edit = QLineEdit(_get_ota_config("webhook_port", "8765"))
        form.addRow("监听端口:", self.port_edit)

        self.meituan_secret = QLineEdit(_get_ota_config("meituan_secret"))
        self.meituan_secret.setEchoMode(QLineEdit.Password)
        form.addRow("美团密钥:", self.meituan_secret)

        self.ctrip_secret = QLineEdit(_get_ota_config("ctrip_secret"))
        self.ctrip_secret.setEchoMode(QLineEdit.Password)
        form.addRow("携程密钥:", self.ctrip_secret)

        self.booking_secret = QLineEdit(_get_ota_config("booking_secret"))
        self.booking_secret.setEchoMode(QLineEdit.Password)
        form.addRow("Booking密钥:", self.booking_secret)

        layout.addLayout(form)

        # Webhook 地址提示
        port = _get_ota_config("webhook_port", "8765")
        hint = QLabel(
            f"📡 Webhook 地址:\n"
            f"  美团: http://YOUR_IP:{port}/webhook/meituan\n"
            f"  携程: http://YOUR_IP:{port}/webhook/ctrip\n"
            f"  Booking: http://YOUR_IP:{port}/webhook/booking"
        )
        hint.setObjectName("OtaWebhookHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾 保存")
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_save.clicked.connect(self._save)
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

    def _save(self):
        _set_ota_config("webhook_port", self.port_edit.text().strip() or "8765")
        _set_ota_config("meituan_secret", self.meituan_secret.text().strip())
        _set_ota_config("ctrip_secret", self.ctrip_secret.text().strip())
        _set_ota_config("booking_secret", self.booking_secret.text().strip())
        show_info(self, "成功", "设置已保存，重启服务后生效")
        self.accept()


# ─── UI：OTA 预订管理页面 ─────────────────────────────────────────────────────
class OTATab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        _ensure_ota_table()
        self._webhook_thread: Optional[WebhookServerThread] = None
        self._build_ui()
        QTimer.singleShot(0, self.refresh)

    def _build_ui(self):
        self.setObjectName("OTATab")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 标题行
        title_row = QHBoxLayout()
        title = QLabel("📥 OTA 在线预订管理")
        self._title_lbl = title
        title.setObjectName("H2Title")
        title_row.addWidget(title)
        title_row.addStretch()

        self.webhook_status = QLabel("⏹ Webhook 未启动")
        self.webhook_status.setObjectName("Small")
        title_row.addWidget(self.webhook_status)
        layout.addLayout(title_row)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._btn_add = QPushButton("➕ 手动录入")
        self._btn_add.setObjectName("SolidPrimaryBtn")
        self._btn_add.clicked.connect(self._manual_add)
        toolbar.addWidget(self._btn_add)

        self._btn_today = QPushButton("📅 今日到店")
        self._btn_today.setObjectName("FdGhostBtn")
        self._btn_today.setProperty("primary", True)
        self._btn_today.clicked.connect(self._show_today)
        toolbar.addWidget(self._btn_today)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["全部", "待确认", "已确认", "已拒绝", "已取消"])
        self.filter_combo.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.filter_combo)

        toolbar.addStretch()

        self.btn_webhook = QPushButton("▶ 启动服务")
        self.btn_webhook.setObjectName("OtaWebhookBtn")
        self.btn_webhook.setProperty("running", False)
        self.btn_webhook.clicked.connect(self._toggle_webhook)
        toolbar.addWidget(self.btn_webhook)

        self._btn_settings = QPushButton("⚙️ 设置")
        self._btn_settings.setObjectName("FdGhostBtn")
        self._btn_settings.clicked.connect(self._open_settings)
        toolbar.addWidget(self._btn_settings)

        self._btn_refresh = QPushButton("🔄")
        self._btn_refresh.setObjectName("FdGhostBtn")
        self._btn_refresh.clicked.connect(self.refresh)
        toolbar.addWidget(self._btn_refresh)

        layout.addLayout(toolbar)

        # 今日到店提示条
        self.today_bar = QLabel()
        self.today_bar.setObjectName("OtaTodayBar")
        self.today_bar.hide()
        layout.addWidget(self.today_bar)

        # 预订列表
        table_box = QFrame()
        table_box.setObjectName("ContentBox")
        from ui_surface import fd_apply_content_box, fd_apply_table_palette
        fd_apply_content_box(table_box)
        tb_lay = QVBoxLayout(table_box)
        tb_lay.setContentsMargins(10, 10, 10, 10)
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "ID", "预订号", "来源", "客人", "电话",
            "房型", "入住", "退房", "金额", "状态"
        ])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(2, 70)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 80)
        self.table.setColumnWidth(6, 110)
        self.table.setColumnWidth(7, 110)
        self.table.setColumnWidth(8, 70)
        self.table.setColumnWidth(9, 70)
        fd_apply_table_palette(self.table)
        tb_lay.addWidget(self.table)
        layout.addWidget(table_box)

        # 操作按钮行
        action_row = QHBoxLayout()
        self._btn_confirm = QPushButton("✅ 确认选中")
        self._btn_confirm.setObjectName("SolidPrimaryBtn")
        self._btn_confirm.clicked.connect(self._confirm_selected)
        action_row.addWidget(self._btn_confirm)

        self._btn_reject = QPushButton("❌ 拒绝选中")
        self._btn_reject.setObjectName("FdDangerBtn")
        self._btn_reject.clicked.connect(self._reject_selected)
        action_row.addWidget(self._btn_reject)

        self._btn_cancel_sel = QPushButton("🚫 取消选中")
        self._btn_cancel_sel.setObjectName("FdActSecondary")
        self._btn_cancel_sel.clicked.connect(self._cancel_selected)
        action_row.addWidget(self._btn_cancel_sel)

        action_row.addStretch()
        layout.addLayout(action_row)

    def _sync_webhook_btn(self, running: bool) -> None:
        self.btn_webhook.setProperty("running", running)
        self.btn_webhook.style().unpolish(self.btn_webhook)
        self.btn_webhook.style().polish(self.btn_webhook)

    def _refresh_theme_styles(self, _theme: str = "") -> None:
        """换主题后重刷表格色块；按钮/容器走 base.qss。"""
        wt = get_webhook_thread()
        self._sync_webhook_btn(wt.isRunning())
        self.refresh()

    def refresh(self):
        _ensure_ota_table()
        filter_map = {0: "ALL", 1: "PENDING", 2: "CONFIRMED", 3: "REJECTED", 4: "CANCELLED"}
        idx = self.filter_combo.currentIndex() if hasattr(self, "filter_combo") else 0
        status_filter = filter_map.get(idx, "ALL")
        rows = OTABookingService.get_bookings(status_filter)

        self.table.setRowCount(len(rows))
        status_colors = {
            "PENDING": QColor(_p('accent')).lighter(180),
            "CONFIRMED": QColor(_p('amount_positive')).lighter(180),
            "REJECTED": QColor(_p('danger')).lighter(180),
            "CANCELLED": QColor(_p('surface_alt')).lighter(180),
            "CHECKEDIN": QColor(_p('primary')).lighter(180),
        }
        status_labels = {
            "PENDING": "待确认",
            "CONFIRMED": "已确认",
            "REJECTED": "已拒绝",
            "CANCELLED": "已取消",
            "CHECKEDIN": "已入住",
        }
        for r, row in enumerate(rows):
            bid, bno, src, name, phone, rtype, ci, co, nights, price, status, created = row
            vals = [
                str(bid), bno, OTA_SOURCES.get(src, src), name, phone or "-",
                rtype or "待分配", ci[:16] if ci else "-", co[:16] if co else "-",
                f"¥{price:.0f}", status_labels.get(status, status)
            ]
            bg = status_colors.get(status, QColor(_p('bg_card')))
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setBackground(bg)
                self.table.setItem(r, c, item)

        # 今日到店提示
        today_arrivals = OTABookingService.get_today_arrivals()
        if today_arrivals:
            self.today_bar.setText(f"📅 今日到店 {len(today_arrivals)} 组客人，请提前准备房间")
            self.today_bar.show()
        else:
            self.today_bar.hide()

    def _manual_add(self):
        dlg = ManualBookingDialog(self)
        if dlg.exec():
            self.refresh()

    def _show_today(self):
        rows = OTABookingService.get_today_arrivals()
        if not rows:
            show_info(self, "今日到店", "今日暂无预订到店")
            return
        lines = [f"今日到店 {len(rows)} 组："]
        for r in rows:
            lines.append(f"  • {r[3]} | {r[5] or '待分配'} | {r[6][:16]} → {r[7][:16]} | ¥{r[8]:.0f}")
        show_info(self, "今日到店", "\n".join(lines))

    def _get_selected_id(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return int(item.text()) if item else None

    def _confirm_selected(self):
        bid = self._get_selected_id()
        if not bid:
            show_warning(self, "提示", "请先选择一条预订记录")
            return
        ok, msg = OTABookingService.confirm_booking(bid)
        show_info(self, "结果", msg)
        self.refresh()

    def _reject_selected(self):
        bid = self._get_selected_id()
        if not bid:
            show_warning(self, "提示", "请先选择一条预订记录")
            return
        ok, msg = OTABookingService.reject_booking(bid, "前台拒绝")
        show_info(self, "结果", msg)
        self.refresh()

    def _cancel_selected(self):
        bid = self._get_selected_id()
        if not bid:
            show_warning(self, "提示", "请先选择一条预订记录")
            return
        ok, msg = OTABookingService.cancel_booking(bid)
        show_info(self, "结果", msg)
        self.refresh()

    def _toggle_webhook(self):
        wt = get_webhook_thread()
        if wt.isRunning():
            wt.stop()
            wt.wait(2000)
            self.btn_webhook.setText("▶ 启动服务")
            self._sync_webhook_btn(False)
        else:
            wt.status_changed.connect(self.webhook_status.setText)
            wt.start()
            self.btn_webhook.setText("⏹ 停止服务")
            self._sync_webhook_btn(True)

    def _open_settings(self):
        dlg = OTASettingsDialog(self)
        dlg.exec()
