"""
sound_helper.py — Solid 酒店系统的统一声音反馈底座
====================================================

设计目标
--------
- 给前台/工程操作一种"听得到"的反馈，方便老板隔着柜台也能判断结果。
- Windows 下优先用 `winsound`，零依赖、零安装。
- 永远静音兜底：任何异常都吞掉，绝对不影响业务交易。
- 后续可在设置页加"声音开关"，第一版默认开启，开关由
  `database.db.get_config("ui_sound_enabled")` 控制（缺省=开启）。

对外 5 个动作（所有调用方应使用这些函数，不要直接 import winsound）
- play_success：✓ 成功（入住、退房、写卡成功、收款成功）
- play_fail   ：✗ 失败（写卡失败、操作失败）
- play_warn   ：⚠ 警告（操作前确认、删除/注销）
- play_alert  ：⚠⚠ 严重告警（差异审计报警、断网锁死）
- play_notify ：🔔 普通通知（toast 提示）

线程模型
--------
- 所有发声都用后台线程异步播放，不阻塞 UI 主线程。
- 同一时刻多次调用会顺序排队（winsound 自己排队）。
- 若环境变量 `SOLID_DISABLE_SOUND=1` 则全程静音，便于自动化测试。
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Optional


# ---------------------------------------------------------------------------
# 配置：是否启用声音
# ---------------------------------------------------------------------------

_ENV_DISABLE = "SOLID_DISABLE_SOUND"
_CONFIG_KEY = "ui_sound_enabled"

# 进程内缓存，避免每次播放都查 db
_cached_enabled: Optional[bool] = None


def is_sound_enabled() -> bool:
    """声音开关：环境变量 > db 配置 > 默认开启。"""
    global _cached_enabled
    if os.environ.get(_ENV_DISABLE, "").strip() in ("1", "true", "yes", "on"):
        return False
    if _cached_enabled is not None:
        return _cached_enabled
    try:
        from database import db

        val = db.get_config(_CONFIG_KEY)
        if val is None or str(val).strip() == "":
            _cached_enabled = True
        else:
            _cached_enabled = str(val).strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        _cached_enabled = True
    return _cached_enabled


def set_sound_enabled(enabled: bool) -> None:
    """设置页调用：写 db + 刷新进程内缓存。失败不抛出。"""
    global _cached_enabled
    _cached_enabled = bool(enabled)
    try:
        from database import db

        db.set_config(_CONFIG_KEY, "1" if enabled else "0")
    except Exception:
        pass


def invalidate_cache() -> None:
    """配置外部被改时调用，下次播放会重新读取。"""
    global _cached_enabled
    _cached_enabled = None


# ---------------------------------------------------------------------------
# 平台层：winsound 优先，其他平台静默
# ---------------------------------------------------------------------------

_IS_WINDOWS = sys.platform.startswith("win")

try:  # pragma: no cover - 平台相关
    if _IS_WINDOWS:
        import winsound  # type: ignore
    else:
        winsound = None  # type: ignore
except Exception:  # pragma: no cover - 防御性
    winsound = None  # type: ignore


# Windows 内置事件音名（缺资源时 winsound 自身会静默失败，绝不抛错）
_EVENT_SUCCESS = "SystemAsterisk"     # ✓ 提示
_EVENT_FAIL = "SystemHand"            # ✗ 错误
_EVENT_WARN = "SystemExclamation"     # ⚠ 警告
_EVENT_NOTIFY = "SystemNotification"  # 🔔 通知
_EVENT_ALERT = "SystemHand"           # ⚠⚠ 严重

# Beep 兜底参数（频率/时长 ms）— 不依赖任何资源，保证一定有声
_BEEP_SUCCESS = [(880, 90), (1175, 110)]
_BEEP_FAIL = [(330, 380)]
_BEEP_WARN = [(660, 180)]
_BEEP_ALERT = [(740, 200), (440, 240), (740, 200)]
_BEEP_NOTIFY = [(988, 90)]


def _play_event_then_beep(event_name: str, beep_pattern) -> None:
    """先尝试播放系统事件音；如果失败/无效，退回纯 Beep 兜底。"""
    if winsound is None:
        return
    try:
        # SND_ASYNC：异步播放；SND_NODEFAULT：找不到事件不播默认 ding
        flags = winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        ok = winsound.PlaySound(event_name, flags)
        # 部分系统 PlaySound 返回 None/True/False 不一，统一兜底再发 Beep
        if ok in (False, 0):
            raise RuntimeError("event sound returned false")
        return
    except Exception:
        pass
    try:
        for freq, dur in beep_pattern:
            winsound.Beep(int(freq), int(dur))
    except Exception:
        # 绝对静默兜底
        pass


def _async(fn, *args, **kwargs) -> None:
    """所有发声跑后台线程，业务调用立即返回。"""
    if not is_sound_enabled():
        return
    try:
        t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
        t.start()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------

def play_success() -> None:
    """成功：双滴上扬。写卡/入住/退房/收款成功调用。"""
    _async(_play_event_then_beep, _EVENT_SUCCESS, _BEEP_SUCCESS)


def play_fail() -> None:
    """失败：低频长音。写卡/操作失败调用。"""
    _async(_play_event_then_beep, _EVENT_FAIL, _BEEP_FAIL)


def play_warn() -> None:
    """警告：中频单音。删除/注销/危险确认调用。"""
    _async(_play_event_then_beep, _EVENT_WARN, _BEEP_WARN)


def play_alert() -> None:
    """严重告警：三连音。账实差异报警/锁死调用。"""
    _async(_play_event_then_beep, _EVENT_ALERT, _BEEP_ALERT)


def play_notify() -> None:
    """普通通知：短促单音。Toast/新消息调用。"""
    _async(_play_event_then_beep, _EVENT_NOTIFY, _BEEP_NOTIFY)


__all__ = [
    "play_success",
    "play_fail",
    "play_warn",
    "play_alert",
    "play_notify",
    "is_sound_enabled",
    "set_sound_enabled",
    "invalidate_cache",
]
