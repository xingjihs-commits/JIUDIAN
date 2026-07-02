"""
vendor_gate.py — 厂家模式 / 酒店模式 状态与门禁

- 厂家：拥有 debug_panel 权限的账号（超级管理员 / 厂家工程师）
- 首次运行：setup_done != "1" 或 lock_takeover_done_at 为空 → 仅厂家可进主界面
- C0-alpha 激活闸门：未激活 → 全屏激活页；激活完成但未做期初盘点 → 强制期初盘点向导
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, Optional
import logging
logger = logging.getLogger(__name__)

from ui_helpers import show_warning

_CARDLOCK_DATE_RE = re.compile(r"CardLock(\d{6})\.MDB", re.IGNORECASE)


def is_setup_done() -> bool:
    try:
        from services.vendor_service import get_vendor_config
        from database import db
        return get_vendor_config(db, "setup_done") == "1"
    except Exception:
        return False


def is_takeover_done() -> bool:
    try:
        from database import db
        return bool(db.get_config("lock_takeover_done_at"))
    except Exception:
        return False


def is_first_run() -> bool:
    """单次 DB 查询判断是否为首次运行（替代两次 get_config 调用）。"""
    try:
        from database import db
        rows = db.execute(
            "SELECT key, value FROM system_config WHERE key IN ('setup_done', 'lock_takeover_done_at')"
        ).fetchall()
        cfg = dict(rows)
        setup_done = cfg.get("setup_done") == "1"
        takeover_done = bool(cfg.get("lock_takeover_done_at"))
        return not (setup_done and takeover_done)
    except Exception:
        return True


def current_is_vendor() -> bool:
    try:
        from permission_system import PermissionManager
        return PermissionManager.has_permission("debug_panel")
    except Exception:
        return False


def require_vendor_or_block(parent=None) -> bool:
    """可继续返回真；已弹窗阻止返回假。"""
    if current_is_vendor():
        return True
    try:
        show_warning(
            parent,
            "需要厂家账号",
            "此功能仅限厂家配置使用。\n"
            "请联系厂家工程师，或用厂家账号登录后再操作。",
        )
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  C0-alpha：厂家激活硬锁 + 期初盘点闸门
# ─────────────────────────────────────────────────────────────────────────────

def is_activated() -> bool:
    """判断是否已激活（委托给授权管理器）。"""
    try:
        from license_manager import LicenseManager
        return not LicenseManager.is_activation_required()
    except Exception:
        return False


def should_block_for_activation() -> bool:
    """启动闸门：是否必须先弹出全屏激活页。"""
    try:
        from license_manager import LicenseManager
        return LicenseManager.is_activation_required()
    except Exception:
        # 任何异常都按"需要激活"处理，宁可错杀不可漏放
        return True


def is_initial_stocktake_done() -> bool:
    """期初盘点是否完成（C0-beta 实装期初向导后由它写入 initial_stocktake_done_at）。

    C0-alpha 阶段先用"是否有完成时间戳"判断；老用户尚未做过期初盘点，
    期初只返回温和提示不强制阻塞。"""
    try:
        from database import db
        return bool(
            (db.get_config("initial_stocktake_done_at") or "").strip()
        )
    except Exception:
        return False


def should_block_for_initial_stocktake() -> bool:
    """C0-alpha 闸门钩子：激活后是否需要强制弹期初盘点向导。

    规则：
      - 必须已激活才考虑
      - 已祖父豁免（license_source == grandfathered）的老用户跳过强制阻塞
        （他们以前就在用，没有期初基线；C0-beta 完成后再做补盘点提醒）
      - 否则缺 initial_stocktake_done_at → 阻塞
    """
    try:
        if not is_activated():
            return False
        from database import db
        source = (db.get_config("license_source") or "").strip()
        if source == "grandfathered":
            return False
        return not is_initial_stocktake_done()
    except Exception:
        return False


def _today_yymmdd() -> str:
    return _dt.datetime.now().strftime("%y%m%d")


def _date_in_live_mdb_path(path_str: str | None) -> str:
    if not path_str:
        return ""
    m = _CARDLOCK_DATE_RE.search(path_str)
    return m.group(1) if m else ""


def persist_live_mdb_result(result, *, db=None) -> None:
    """把实时采集结果写入系统配置。"""
    if db is None:
        from database import db as _db
        db = _db

    now = _dt.datetime.now().isoformat(timespec="seconds")
    if result.path:
        db.set_config("lock_takeover_live_mdb_path", str(result.path))
        db.set_config("lock_takeover_live_mdb_dir", str(result.dir or result.path.parent))
        db.set_config("lock_takeover_live_mdb_source", result.source or "")
        db.set_config("lock_takeover_live_mdb_validated_at", now if result.validated else "")
    else:
        db.set_config("lock_takeover_live_mdb_path", "")
        db.set_config("lock_takeover_live_mdb_dir", "")
        db.set_config("lock_takeover_live_mdb_source", "")
        db.set_config("lock_takeover_live_mdb_validated_at", "")


def maybe_refresh_live_mdb_on_startup() -> Optional[Dict[str, Any]]:
    """
    已接管时：若缓存的活 MDB 文件名日期不是今天，按已存 hint 重发现并回写。
    返回发现结果或空（无需刷新时）。
    """
    if not is_takeover_done():
        return None

    try:
        from database import db
    except Exception:
        return None

    cached = db.get_config("lock_takeover_live_mdb_path") or ""
    cached_date = _date_in_live_mdb_path(cached)
    today = _today_yymmdd()
    if cached_date == today and cached:
        return None

    install_dir = db.get_config("lock_takeover_install_dir") or ""
    share = db.get_config("lock_takeover_share_db_path") or ""
    db_bak = db.get_config("lock_takeover_db_bak_path") or ""

    try:
        from lock_deploy.live_mdb import discover_live_mdb
    except ImportError:
        return None

    # 启动路径必须轻量：只用已保存的安装目录提示重新定位日切数据库。
    # 全盘浅扫 D:/C:/E: 可能卡在机械盘、坏盘或大目录上，导致首屏白屏无响应；
    # 需要人工重扫时由诊断页「重新发现活数据库」触发。
    result = discover_live_mdb(
        install_dir=Path(install_dir) if install_dir else None,
        share_db_path_hint=share or None,
        db_bak_path_hint=db_bak or None,
        probe_common_roots=False,
    )
    if result.path:
        persist_live_mdb_result(result, db=db)
        logger.info(
            "[vendor_gate] 活MDB日切刷新: %s (source=%s, validated=%s)",
            result.path, result.source, result.validated,
        )
    return {
        "path": str(result.path) if result.path else "",
        "source": result.source,
        "validated": result.validated,
        "error": result.error,
    }
