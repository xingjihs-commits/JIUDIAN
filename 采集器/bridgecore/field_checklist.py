"""
field_checklist.py — 现场开门验证清单生成器

根据毕业状态生成工程师带回酒店实测的检查项（PMS 暂不消费）。
"""

from __future__ import annotations

from typing import Any, Optional


def build_field_checklist(
    *,
    graduation_state: Optional[Any] = None,
    analyze_result: Optional[dict] = None,
    probe_result: Optional[dict] = None,
    samples: Optional[list[dict]] = None,
) -> dict:
    """生成 field_checklist.json 内容。"""
    mode = (probe_result or {}).get("mode", "unknown")
    card_types: list[str] = []
    if analyze_result:
        ct = analyze_result.get("card_types") or []
        if isinstance(ct, dict):
            card_types = list(ct.keys())
        elif isinstance(ct, (list, tuple)):
            card_types = list(ct)

    sample_types = set()
    for s in samples or []:
        if isinstance(s, dict) and s.get("type"):
            sample_types.add(str(s["type"]))

    items: list[dict] = []

    def _add(check_id: str, title: str, required: bool, note: str = ""):
        items.append({
            "id": check_id,
            "title": title,
            "required": required,
            "verified_by_collector": False,
            "note": note,
        })

    _add("guest_open_door", "客人卡刷指定房门能开门", True,
         "采集器仅验证 hex 一致，开门需现场确认")
    if "master" in card_types or "master" in sample_types:
        _add("master_open", "总卡刷门验证", True)
    if "building" in card_types or "building" in sample_types:
        _add("building_open", "楼栋卡刷门验证", False)
    if "floor" in card_types or "floor" in sample_types:
        _add("floor_open", "楼层卡刷门验证", False)
    _add("checkout_erase", "退房/擦卡后旧卡不能开门", True)
    if mode == "parasitic":
        _add("parasitic_replay", "PMS 寄生原厂工作流可完整发卡", True,
             "需 PMS 导入后实测")
    if mode == "serial":
        _add("serial_issue", "串口路径独立发卡", True,
             "PMS 串口发卡尚未接线，现场人工验证")

    profile = (analyze_result or {}).get("profile") or {}
    auth = (profile.get("card_types") or {}).get("auth") or {}
    if auth.get("auth_token_repeat"):
        _add("auth_token", "授权卡 Token 有效性", True)

    protocol_verified = (analyze_result or {}).get("protocol_verified")
    evidence_level = "hex_only"
    if protocol_verified is True:
        evidence_level = "verified_write"
    elif analyze_result and analyze_result.get("dll_traces"):
        evidence_level = "dll_traced"

    passed = []
    if graduation_state and hasattr(graduation_state, "items"):
        passed = [it.id for it in graduation_state.items if it.passed]

    return {
        "version": "1.0",
        "mode": mode,
        "evidence_level": evidence_level,
        "graduation_passed": passed,
        "items": items,
        "disclaimer": "以下项目需在现场实门锁上人工确认；采集器未验证开门。",
    }
