"""
vendor_lockdown.py — C0-lockdown 厂家远程锁死与断网硬锁（sub-h 强化版）

锁死级别：
- RECONNECT_OK：恢复正常
- WARNING_BANNER：只显示警告横幅
- LOCK_GUEST_BOT：锁客人机器人 / 云端订单
- LOCK_REPORTS：锁报表和核心审计页面
- LOCK_ALL：全功能锁死（仅留紧急延期码入口 + 备份导出）

离线锁死阶梯（sub-h 2026-06-22 强化，从原 D5/D6/D7 改为 D3/D7/D14）：
  D3  → 黄色 toast 告警 "离线 X 天，请尽快联网"（LOCK_WARNING_BANNER 级别）
  D7  → 红色弹窗告警 "即将锁机，请立即联网或输入紧急延期码"（LOCK_GUEST_BOT 级别）
  D14 → 自动 LOCK_ALL（仅留紧急延期码入口）

紧急延期码（72 小时一次性）：
  generate_emergency_code(hotel_id) → HMAC-SHA256(hotel_id, secret) 取前 8 位 hex
  apply_emergency_code(hotel_id, code) → 验证并延期 72 小时，同码一次性防重放

旧常量 OFFLINE_LOCK_DAYS / OFFLINE_WARN_DAY1 / OFFLINE_WARN_DAY2 保留为别名
以避免破坏旧调用方；新代码请使用 OFFLINE_ALERT_*_DAYS 常量。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import logging
from typing import Optional

logger = logging.getLogger(__name__)

LOCK_RECONNECT_OK = "RECONNECT_OK"
LOCK_WARNING_BANNER = "WARNING_BANNER"
LOCK_GUEST_BOT = "LOCK_GUEST_BOT"
LOCK_REPORTS = "LOCK_REPORTS"
LOCK_ALL = "LOCK_ALL"

LOCK_LEVELS: list[str] = [
    "",
    LOCK_RECONNECT_OK,
    LOCK_WARNING_BANNER,
    LOCK_GUEST_BOT,
    LOCK_REPORTS,
    LOCK_ALL,
]

LOCK_LEVELS_MAP: dict[str, str] = {v: v for v in LOCK_LEVELS}

# ── sub-h 强化版离线阶梯阈值（2026-06-22） ──
# 新版三级阶梯：D3 黄色 / D7 红色 / D14 锁机
# 旧版 D5/D6/D7 阶梯过急，断网超过 5 天就发警告，但实际酒店网络故障恢复
# 经常需要 7-10 天，新版把硬锁阈值放宽到 14 天，给现场更多缓冲。
OFFLINE_ALERT_YELLOW_DAYS = 3   # D3：黄色 toast 状态栏告警
OFFLINE_ALERT_RED_DAYS    = 7   # D7：红色弹窗告警 + LOCK_GUEST_BOT
OFFLINE_HARD_LOCK_DAYS    = 14  # D14：自动 LOCK_ALL

# ── 旧常量别名（向后兼容，新代码勿用） ──
OFFLINE_LOCK_DAYS = OFFLINE_HARD_LOCK_DAYS   # 原值 7 → 现值 14
OFFLINE_WARN_DAY1 = OFFLINE_ALERT_YELLOW_DAYS  # 原值 5 → 现值 3
OFFLINE_WARN_DAY2 = OFFLINE_ALERT_RED_DAYS     # 原值 6 → 现值 7

# 紧急延期码有效期（72 小时）
EMERGENCY_EXTEND_SECONDS = 72 * 3600
# 密钥种子（生产环境应替换）
_EMERGENCY_SECRET = b"j1k9e52h_JIUDIAN_SOLID_TSL"


def normalize_lock_level(level: object) -> str:
    val = str(level or "").strip().upper()
    if val == LOCK_RECONNECT_OK:
        return ""
    return val if val in LOCK_LEVELS else ""


def _parse_iso(value: Optional[str]) -> Optional[_dt.datetime]:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            pass
    return None


def sync_lock_level(level: object, *, source: str = "cloud") -> str:
    """写入厂家锁死级别并通知 UI。"""
    from database import db
    from event_bus import bus

    normalized = normalize_lock_level(level)
    db.set_config("lock_level", normalized)
    if normalized:
        db.set_config("lock_level_source", source)
        db.set_config("lock_level_updated_at", _dt.datetime.now().isoformat(timespec="seconds"))
    else:
        db.set_config("lock_level_source", "")
    try:
        bus.lock_level_changed.emit(normalized)
    except Exception:
        pass
    return normalized


def mark_cloud_seen() -> None:
    from database import db

    db.set_config("last_cloud_seen_at", _dt.datetime.now().isoformat(timespec="seconds"))


def offline_days() -> Optional[float]:
    from database import db

    last = _parse_iso(db.get_config("last_cloud_seen_at"))
    if not last:
        return None
    return (_dt.datetime.now() - last).total_seconds() / 86400.0


def current_lock_level() -> str:
    """厂家远程级别优先；断网时阶梯升级：D3 黄告警 → D7 红告警 → D14 LOCK_ALL。

    sub-h 强化版（2026-06-22）：阈值从 D5/D6/D7 调整为 D3/D7/D14。
    紧急延期码生效中（emergency_extend_until 未过期）一律返回 ""（不锁）。
    """
    from database import db

    remote = normalize_lock_level(db.get_config("lock_level"))
    if remote:
        return remote

    days = offline_days()
    if days is None:
        return ""

    # 检查紧急延期码是否生效
    emergency_until = _parse_iso(db.get_config("emergency_extend_until"))
    if emergency_until and _dt.datetime.now() < emergency_until:
        return ""  # 延期码生效中，不锁

    # 阶梯锁死（sub-h 新版阈值）
    if days >= OFFLINE_HARD_LOCK_DAYS:
        return LOCK_ALL
    if days >= OFFLINE_ALERT_RED_DAYS:
        return LOCK_GUEST_BOT
    if days >= OFFLINE_ALERT_YELLOW_DAYS:
        return LOCK_WARNING_BANNER

    return ""


def get_lockdown_phase() -> dict:
    """返回锁死阶段详情（供 UI 展示和 Telegram 通知）。

    sub-h 强化版（2026-06-22）：阶段从 warn_day5/warn_day6/locked
    调整为 yellow_warn/red_warn/locked，与新阈值 D3/D7/D14 对齐。
    """
    days = offline_days()
    emergency_until = None
    try:
        from database import db
        emergency_until = _parse_iso(db.get_config("emergency_extend_until"))
    except Exception:
        pass

    phase = {
        "offline_days": round(days, 1) if days else 0,
        "lock_level": current_lock_level(),
        "will_lock_at_day": OFFLINE_HARD_LOCK_DAYS,
        "emergency_active": bool(emergency_until and _dt.datetime.now() < emergency_until),
        "emergency_expires_at": emergency_until.isoformat() if emergency_until else None,
    }

    if phase["offline_days"] >= OFFLINE_HARD_LOCK_DAYS:
        phase["stage"] = "locked"
        phase["message"] = "系统已锁定，联网或使用紧急延期码恢复。"
    elif phase["offline_days"] >= OFFLINE_ALERT_RED_DAYS:
        phase["stage"] = "red_warn"
        phase["message"] = (
            f"离线 {phase['offline_days']:.0f} 天！即将锁机，"
            f"请立即联网或输入紧急延期码。"
        )
    elif phase["offline_days"] >= OFFLINE_ALERT_YELLOW_DAYS:
        phase["stage"] = "yellow_warn"
        phase["message"] = (
            f"离线 {phase['offline_days']:.0f} 天，请尽快联网。"
            f"第 {OFFLINE_HARD_LOCK_DAYS} 天将自动锁机。"
        )
    else:
        phase["stage"] = "normal"
        phase["message"] = ""

    return phase


def get_offline_alert() -> dict:
    """返回离线告警详情（供 app_main 定时检查触发 toast）。

    sub-h 新增（2026-06-22）。返回结构：
        {
            "level":   "normal" | "yellow" | "red" | "locked",
            "days":    float | None,
            "title":   str,   # toast 标题（短）
            "message": str,   # toast 主体
            "toast_level": "info" | "warning" | "error",  # 对应 toast_widget 四态
        }

    紧急延期码生效中或无离线数据时返回 normal。
    """
    days = offline_days()
    if days is None:
        return {
            "level": "normal", "days": None,
            "title": "离线监控",
            "message": "从未联网，无法计算离线天数",
            "toast_level": "info",
        }

    # 紧急延期码生效中 → 视为 normal
    try:
        from database import db
        emergency_until = _parse_iso(db.get_config("emergency_extend_until"))
        if emergency_until and _dt.datetime.now() < emergency_until:
            return {
                "level": "normal", "days": round(days, 1),
                "title": "紧急延期生效中",
                "message": f"离线 {days:.1f} 天，但延期码生效中，剩余 {(emergency_until - _dt.datetime.now()).total_seconds()/3600:.1f} 小时",
                "toast_level": "info",
            }
    except Exception:
        pass

    if days >= OFFLINE_HARD_LOCK_DAYS:
        return {
            "level": "locked", "days": round(days, 1),
            "title": "系统已自动锁定",
            "message": (
                f"离线 {days:.0f} 天，系统已自动 LOCKED。"
                f"仅留紧急延期码入口 + 备份导出。请联网或输入延期码。"
            ),
            "toast_level": "error",
        }
    if days >= OFFLINE_ALERT_RED_DAYS:
        return {
            "level": "red", "days": round(days, 1),
            "title": "即将锁机",
            "message": (
                f"离线 {days:.0f} 天，第 {OFFLINE_HARD_LOCK_DAYS} 天将自动锁机。"
                f"请立即联网或输入紧急延期码。"
            ),
            "toast_level": "error",
        }
    if days >= OFFLINE_ALERT_YELLOW_DAYS:
        return {
            "level": "yellow", "days": round(days, 1),
            "title": "离线告警",
            "message": (
                f"离线 {days:.0f} 天，请尽快联网。"
                f"第 {OFFLINE_HARD_LOCK_DAYS} 天将自动锁机。"
            ),
            "toast_level": "warning",
        }
    return {
        "level": "normal", "days": round(days, 1),
        "title": "在线",
        "message": f"离线 {days:.1f} 天，状态正常",
        "toast_level": "info",
    }


def is_locked(area: str = "all") -> bool:
    level = current_lock_level()
    area = str(area or "all").lower()
    if level == LOCK_ALL:
        return True
    if area in ("reports", "report", "audit"):
        return level == LOCK_REPORTS
    if area in ("guest_bot", "bot", "cloud_order"):
        return level in (LOCK_GUEST_BOT, LOCK_ALL)
    return False


def should_show_warning_banner() -> bool:
    return current_lock_level() in {
        LOCK_WARNING_BANNER,
        LOCK_GUEST_BOT,
        LOCK_REPORTS,
        LOCK_ALL,
    }


def lock_message(level: Optional[str] = None) -> str:
    lv = normalize_lock_level(level) or current_lock_level()
    if lv == LOCK_ALL:
        return "系统已被厂家全功能锁定，请联系厂家恢复连接或处理授权。"
    if lv == LOCK_REPORTS:
        return "核心报表与审计功能已被厂家暂时锁定，请联系厂家处理。"
    if lv == LOCK_GUEST_BOT:
        return "客人机器人与云端订单服务已被厂家暂时锁定。"
    if lv == LOCK_WARNING_BANNER:
        return "厂家提醒：此酒店需要尽快联网或处理授权。"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
#  厂家服务码（vendor_password_hash）— 设置中心厂家分组解锁等场景共用
# ─────────────────────────────────────────────────────────────────────────────

def vendor_code_configured() -> bool:
    """本机是否已设置厂家服务码（vendor_password_hash）。"""
    try:
        from database import db
        return bool((db.get_config("vendor_password_hash") or "").strip())
    except Exception:
        return False


def verify_vendor_code(code: str) -> bool:
    """校验输入的厂家服务码是否匹配本机已存储的 hash。

    存储格式与 vendor_activation_screen / first_run_dialog 中保持一致：
    sha256(code.strip().encode()).hexdigest()。

    未配置 vendor_password_hash 时一律返回 False —— 厂家解锁路径必须
    要求厂家在本机预先通过激活流程或首次配置写入口令。
    """
    if not code:
        return False
    try:
        from database import db
        stored = (db.get_config("vendor_password_hash") or "").strip()
    except Exception:
        return False
    if not stored:
        return False
    h = hashlib.sha256(code.strip().encode()).hexdigest()
    return h == stored


# ─────────────────────────────────────────────────────────────────────────────
#  紧急延期码（72 小时）
# ─────────────────────────────────────────────────────────────────────────────

def generate_emergency_code(hotel_id: str) -> str:
    """生成一次性 72 小时紧急延期码。

    算法：HMAC-SHA256(hotel_id, secret) → 取前 8 位 hex + 校验位。
    输出格式：XXXX-XXXX（大写，便于酒店前台电话口述）。

    厂家支持人员在云端工作器调用此函数生成码，然后通过电话告知酒店。

    注意：此函数仅限厂家端执行，客户端使用 apply_emergency_code() 验证。
    """
    if not hotel_id:
        return ""
    msg = hotel_id.strip().encode("utf-8")
    sig = hmac.new(_EMERGENCY_SECRET, msg, hashlib.sha256).hexdigest()
    code = sig[:8].upper()
    return f"{code[:4]}-{code[4:]}"


def apply_emergency_code(hotel_id: str, code: str) -> bool:
    """酒店端验证并应用紧急延期码。

    验证成功后：
      - 设置 emergency_extend_until = now + 72 小时
      - 清除当前锁死级别
      - 写入审计日志

    Args:
        hotel_id: 酒店 ID（必须与本地存储一致）
        code: 厂家提供的延期码（接受带或不带短横线）

    Returns:
        True 表示延期成功。
    """
    from database import db

    if not hotel_id or not code:
        return False

    # 验证本地 hotel_id 是否匹配
    local_hotel = (db.get_config("hotel_id") or "").strip()
    if hotel_id.strip() != local_hotel:
        return False

    # 标准化输入码
    normalized = code.strip().upper().replace("-", "")
    if len(normalized) != 8:
        return False

    # 验证
    expected = generate_emergency_code(hotel_id).upper().replace("-", "")
    if normalized != expected:
        logger.warning("[vendor_lockdown] 延期码验证失败: hotel=%s", hotel_id)
        return False

    # 检查是否已使用（一次性）
    used_marker = db.get_config("emergency_code_used")
    if used_marker == normalized:
        return False  # 同一码不能重复使用

    # 应用延期
    expire_time = _dt.datetime.now() + _dt.timedelta(seconds=EMERGENCY_EXTEND_SECONDS)
    db.set_config("emergency_extend_until", expire_time.isoformat(timespec="seconds"))
    db.set_config("emergency_code_used", normalized)

    # 清除锁死
    sync_lock_level("", source="emergency_code")

    # 审计
    db.log_action("SYSTEM", "EMERGENCY_EXTEND",
                   f"72h延期码已应用, hotel={hotel_id}, 失效={expire_time.isoformat()}")
    return True


def clear_emergency_extension() -> None:
    """手动清除紧急延期状态（恢复联网后调用）。"""
    from database import db
    db.set_config("emergency_extend_until", "")
    db.set_config("emergency_code_used", "")
    mark_cloud_seen()


def current_lock_status() -> dict:
    """返回当前锁死状态详情（供厂家控制台诊断页/锁死控制页调用）。
    
    与 current_lock_level() 区别：返回完整 dict，含 level 和阶段信息。
    与 get_lockdown_phase() 区别：key 命名更简洁（level 而非 lock_level）。
    """
    return {
        "level": current_lock_level(),
        "phase": get_lockdown_phase(),
        "locked": is_locked(),
    }


def check_and_notify_offline_warnings() -> dict | None:
    """检查离线天数，按阶梯发送 Telegram 预警。

    由 health_monitor 或 heartbeat 定时调用。
    每阶段只发送一次（通过 db 标记防重复）。

    Returns:
        如果触发了告警，返回 {"stage": str, "days": float, "sent": bool}
        否则返回 None
    """
    from database import db

    days = offline_days()
    if days is None:
        return None

    stage = None
    marker_key = None

    # sub-h 强化版：阈值改为 D3/D7/D14，marker 也更新
    if days >= OFFLINE_ALERT_YELLOW_DAYS and days < OFFLINE_ALERT_RED_DAYS:
        stage = "yellow_warn"
        marker_key = "offline_alert_yellow_sent"
    elif days >= OFFLINE_ALERT_RED_DAYS and days < OFFLINE_HARD_LOCK_DAYS:
        stage = "red_warn"
        marker_key = "offline_alert_red_sent"
    elif days >= OFFLINE_HARD_LOCK_DAYS:
        stage = "locked"
        marker_key = "offline_hard_lock_sent"

    if not stage:
        return None

    # 检查是否已经发送过
    if marker_key and db.get_config(marker_key) == "1":
        return {"stage": stage, "days": round(days, 1), "sent": False}

    # 构造告警消息
    hotel_name = db.get_config("hotel_name") or "未命名酒店"
    hotel_id = db.get_config("hotel_id") or "N/A"

    if stage == "yellow_warn":
        msg = (
            f"⚠️ [离线告警 D3] {hotel_name}\n"
            f"已离线 {days:.0f} 天，请尽快恢复网络连接。\n"
            f"第 {OFFLINE_HARD_LOCK_DAYS} 天将自动锁定系统。\n"
            f"Hotel ID: {hotel_id}"
        )
    elif stage == "red_warn":
        msg = (
            f"🚨 [离线告警 D7] {hotel_name}\n"
            f"已离线 {days:.0f} 天！即将自动锁机。\n"
            f"请立即恢复网络，或联系厂家获取紧急延期码。\n"
            f"Hotel ID: {hotel_id}"
        )
    elif stage == "locked":
        msg = (
            f"🔒 [系统已锁定 D14] {hotel_name}\n"
            f"离线 {days:.0f} 天，前台操作已禁用。\n"
            f"后台可正常备份/导出数据。\n"
            f"恢复网络或使用延期码解锁。\n"
            f"Hotel ID: {hotel_id}"
        )
    else:
        return None

    # 通过 Telegram 发送告警
    sent = False
    try:
        from telegram_notify import send_alert_sync
        send_alert_sync(msg)
        sent = True
    except Exception as e:
        logger.warning("[vendor_lockdown] Telegram 告警发送失败: %s", e)

    # 标记已发送
    if marker_key:
        db.set_config(marker_key, "1")

    return {"stage": stage, "days": round(days, 1), "sent": sent}  # 刷新 last_cloud_seen_at
