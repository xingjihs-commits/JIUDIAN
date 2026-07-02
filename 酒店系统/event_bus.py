from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    # ──────────────────────────────────────────────
    # 原有信号（保持不动）
    # ──────────────────────────────────────────────
    ledger_updated = Signal(str, dict)
    room_status_changed = Signal(str, str)
    cart_received = Signal(dict)
    housekeeping_done = Signal(str, str, str, str)
    # room_id, kwh, sold_hours, electrician_id, note, reading_mode (custom|daily_default|inhouse_hours|telegram)
    energy_reading_submitted = Signal(str, float, float, str, str, str)
    show_success_overlay = Signal(str)
    boss_approved = Signal(str)
    remote_command = Signal(dict)
    audit_alert = Signal(str, str)
    theme_changed = Signal(str)
    language_changed = Signal(str)       # 语言切换
    show_celebration = Signal()
    layout_changed = Signal(str)
    frontdesk_layers_changed = Signal()  # 前台显示层级（frontdesk_display_json）已更新
    user_logged_in = Signal(str, str)  # username, role
    guest_called = Signal(str, str)
    request_screenshot = Signal()
    screenshot_ready = Signal(object)
    payout_approved = Signal(str)
    heartbeat = Signal()

    # ──────────────────────────────────────────────
    # 云端对接信号（本地适配器 / 云端回调用）
    # DEPLOY_GUIDE.md 中标注为"必须添加"的信号
    # ──────────────────────────────────────────────
    cloud_order_received = Signal(dict)   # 云端新订单 {order_id, room_id, total, items}
    cloud_service_request = Signal(dict)  # 云端服务请求 {room_id, request_type, type_name}
    kill_switch_triggered = Signal()      # 云端下发远程锁机已触发
    hotel_suspended = Signal()            # 酒店账号被暂停
    lock_level_changed = Signal(str)      # 厂家锁死级别变化
    vendor_toast = Signal(dict)           # 厂家 IM toast {notify_id,title,body,level}
    show_warning = Signal(str, str)       # 弹出非阻塞警告 (title, message)
    inventory_deduct = Signal(dict)  # {product_id, quantity, room_id, tx_id}
    toast_requested = Signal(str)         # 轻量操作结果提示（文字）


bus = EventBus()

# v7.6 主题切换时清除缓存
def _clear_theme_caches(theme: str = ""):
    """主题切换后清除所有样式缓存。"""
    try:
        from theme_palette import clear_token_cache
        clear_token_cache()
    except Exception:
        pass
    try:
        from ui_surface import clear_surface_cache
        clear_surface_cache()
    except Exception:
        pass

# 自动挂钩 theme_changed 信号
try:
    theme_changed.connect(_clear_theme_caches)
except Exception:
    pass
