"""前台界面显示层级：按 config `frontdesk_display_json` 颗粒化开关，缺省与代码 DEFAULT 合并。"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List

DEFAULT_FD_LAYERS: Dict[str, Any] = {
    "matrix_page": {
        # 房态顶栏统计芯片已含房态/营业数字，默认不再叠两行看板（省纵向空间）
        "embedded_dashboard": False,
        "dashboard_kpi_row": False,
        "dashboard_room_snap": False,
        "flow_hint_strip": True,
    },
    "stats_bar": {
        "pending_cart": True,
        "chips_room_status": True,
        "chips_business": True,
        "finance_hub_menu": True,
        "timeline_toggle": True,
        "batch_mode": True,
    },
    "frontdesk_hub": {
        "checkin": True,
        "roster": True,
        "shop": True,
        "service": True,
        "shift": True,
    },
}

ROOM_CHIP_KEYS: tuple = ("total", "inhouse", "ready", "dirty", "overtime")
OPS_CHIP_KEYS: tuple = ("revenue", "fund", "occ", "ci_today", "co_today")
HUB_ORDER: tuple = ("checkin", "shop", "roster", "service", "shift")


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v) if isinstance(v, dict) else v
    return out


def get_frontdesk_layers(db) -> Dict[str, Any]:
    raw = (db.get_config("frontdesk_display_json") or "").strip()
    if not raw:
        return deepcopy(DEFAULT_FD_LAYERS)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return deepcopy(DEFAULT_FD_LAYERS)
        return _deep_merge(DEFAULT_FD_LAYERS, parsed)
    except json.JSONDecodeError:
        return deepcopy(DEFAULT_FD_LAYERS)


def layer_is_on(layers: Dict[str, Any], *path: str) -> bool:
    cur: Any = layers
    for p in path:
        if not isinstance(cur, dict):
            return True
        if p not in cur:
            return True
        cur = cur[p]
    return bool(cur)


def layers_to_json(layers: Dict[str, Any]) -> str:
    return json.dumps(layers, ensure_ascii=False, separators=(",", ":"))
