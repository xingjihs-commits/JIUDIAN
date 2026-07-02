"""
step_coach.py — 9 步操作教练（UX 层）

与 graduation_coach（六维毕业证据）并行：本模块只负责「在哪操作、点什么、卡放哪」，
不改毕业判定逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

STEP_TOTAL = 9

# 每步静态 copy（target_widget_id 供 UI scroll+highlight）
_STEP_DEFS: list[dict] = [
    {
        "id": 1,
        "title": "认现场",
        "location": "tool",
        "action": "请填写原厂安装目录，点「开始扫描」",
        "hardware_hint": "",
        "why_hint": "工具会扫描 DLL 与配置，并尝试连接发卡器",
        "next_hint": "扫描成功后选择卡型「客人卡」",
        "target_widget_id": "detect_btn",
        "is_oem_pause": False,
    },
    {
        "id": 2,
        "title": "选卡型",
        "location": "tool",
        "action": "在「② 读卡采样」区选择卡型「客人卡」",
        "hardware_hint": "",
        "why_hint": "首次采集只需 1 组客人卡对照样本",
        "next_hint": "下一步：空白卡放在发卡器上，点「读空白卡（采样本）」",
        "target_widget_id": "card_type_combo",
        "is_oem_pause": False,
    },
    {
        "id": 3,
        "title": "读空白卡",
        "location": "tool",
        "action": "请点「读空白卡（采样本）」",
        "hardware_hint": "空白卡放在发卡器感应区",
        "why_hint": "工具通过 bridge32 加载已识别的原厂 DLL 读取卡数据",
        "next_hint": "读成功后，去原厂门锁软件发一张客人卡",
        "target_widget_id": "rb_btn",
        "is_oem_pause": False,
    },
    {
        "id": 4,
        "title": "原厂写卡",
        "location": "oem",
        "action": "请切换到酒店原厂门锁软件，按平时方式发一张客人卡",
        "hardware_hint": "卡通常仍放在发卡器上（按原厂习惯操作）",
        "why_hint": "采集阶段写卡只在原厂完成；工具只负责读卡与学习差分",
        "next_hint": "发完后回到本窗口，点「读已写卡（采样本）」",
        "target_widget_id": "",
        "is_oem_pause": True,
    },
    {
        "id": 5,
        "title": "读已写卡",
        "location": "tool",
        "action": "请点「读已写卡（采样本）」",
        "hardware_hint": "同一张已写卡放回发卡器",
        "why_hint": "工具再次通过原厂 DLL 读取写卡后的数据",
        "next_hint": "读成功后点「添加样本」",
        "target_widget_id": "rw_btn",
        "is_oem_pause": False,
    },
    {
        "id": 6,
        "title": "保存样本",
        "location": "tool",
        "action": "请点「添加样本」",
        "hardware_hint": "",
        "why_hint": "保存空白/已写对照，供协议差分分析",
        "next_hint": "下一步：点底栏「开始分析」",
        "target_widget_id": "add_btn",
        "is_oem_pause": False,
    },
    {
        "id": 7,
        "title": "协议分析",
        "location": "tool",
        "action": "请点底栏「开始分析」",
        "hardware_hint": "",
        "why_hint": "工具对比空白与已写 hex，学习发卡协议",
        "next_hint": "分析完成后点「核对读数（毕业验证）」",
        "target_widget_id": "analyze_btn",
        "is_oem_pause": False,
    },
    {
        "id": 8,
        "title": "核对读数",
        "location": "tool",
        "action": "请点「核对读数（毕业验证）」",
        "hardware_hint": "已写卡放在发卡器上",
        "why_hint": "验证读回数据与样本一致，才能毕业打包",
        "next_hint": "6/6 后点「生成握手包」",
        "target_widget_id": "grad_readback_btn",
        "is_oem_pause": False,
    },
    {
        "id": 9,
        "title": "生成交接包",
        "location": "tool",
        "action": "请点「手动重新生成」或等待自动生成握手包",
        "hardware_hint": "",
        "why_hint": "产出 .solidhandover，带回 PMS 导入",
        "next_hint": "导入路径：厂家控制台 → 门锁品牌 → 导入握手包",
        "target_widget_id": "handover_build_btn",
        "is_oem_pause": False,
    },
]


@dataclass
class StepCoachState:
    step_index: int
    step_total: int
    location: str
    title: str
    hardware_hint: str
    action: str
    why_hint: str
    next_hint: str
    target_widget_id: str
    is_oem_pause: bool
    bridge_blocked: bool = False


def _def(step_index: int) -> dict:
    for d in _STEP_DEFS:
        if d["id"] == step_index:
            return d
    return _STEP_DEFS[0]


def _state_from_def(step_index: int, **overrides) -> StepCoachState:
    d = _def(step_index)
    return StepCoachState(
        step_index=step_index,
        step_total=STEP_TOTAL,
        location=overrides.get("location", d["location"]),
        title=overrides.get("title", d["title"]),
        hardware_hint=overrides.get("hardware_hint", d["hardware_hint"]),
        action=overrides.get("action", d["action"]),
        why_hint=overrides.get("why_hint", d["why_hint"]),
        next_hint=overrides.get("next_hint", d["next_hint"]),
        target_widget_id=overrides.get("target_widget_id", d["target_widget_id"]),
        is_oem_pause=overrides.get("is_oem_pause", d["is_oem_pause"]),
        bridge_blocked=overrides.get("bridge_blocked", False),
    )


def _blank_written(current: Optional[dict]) -> tuple[str, str]:
    if not current:
        return "", ""
    blank = (current.get("blank_hex") or "").strip()
    written = (current.get("written_hex") or "").strip()
    return blank, written


def _readback_passed(graduation_state: Any, readback_hex: Optional[str]) -> bool:
    if not graduation_state or not readback_hex:
        return False
    for it in getattr(graduation_state, "items", []) or []:
        if getattr(it, "id", "") == "readback" and getattr(it, "passed", False):
            return True
    return False


def _bridge_block_message(identity: Any) -> str:
    hint = getattr(identity, "bridge_hint", "") or ""
    if "oem_running" in (getattr(identity, "blockers", None) or []):
        return f"发卡器被原厂软件占用。请先关闭原厂程序，再点「开始扫描」。{hint}".strip()
    if hint:
        return f"发卡器未就绪：{hint}。请检查 USB 连接后重新扫描。"
    return "发卡器未就绪。请连接发卡器并关闭原厂软件，再点「开始扫描」。"


def resolve_step_coach(
    *,
    identity: Optional[Any] = None,
    samples: Optional[list] = None,
    current: Optional[dict] = None,
    analyze_result: Optional[dict] = None,
    probe_result: Optional[dict] = None,
    readback_hex: Optional[str] = None,
    graduation_state: Optional[Any] = None,
    oem_phase_complete: bool = False,
    card_type_ready: bool = True,
    resample_requested: bool = False,
) -> StepCoachState:
    """根据 UI 状态解析当前应展示的操作步骤（1..9）。"""
    samples = samples or []
    site_ok = bool(identity and getattr(identity, "site_ok", False))
    bridge_ok = bool(identity and getattr(identity, "bridge_ok", False))
    main_dll = getattr(identity, "main_dll", "") if identity else ""

    can_graduate = bool(
        graduation_state and getattr(graduation_state, "can_graduate", False)
    )

    # Step 9 — 可毕业
    if can_graduate:
        why = _def(9)["why_hint"]
        if main_dll:
            why = f"已通过六维毕业（DLL: {main_dll}）"
        return _state_from_def(9, why_hint=why)

    blank, written = _blank_written(current)
    has_pair_on_current = bool(
        blank and written and blank.upper() != written.upper()
    )
    has_blank_only = bool(blank and not written)
    sample_count = len(samples)

    analyze_ok = bool(
        analyze_result and analyze_result.get("success")
    )

    # Step 1 — 未认现场
    if not site_ok:
        return _state_from_def(1)

    # Step 8 — 已分析，读回未过
    if analyze_ok and not _readback_passed(graduation_state, readback_hex):
        if not bridge_ok:
            return _state_from_def(
                8,
                action=_bridge_block_message(identity),
                bridge_blocked=True,
            )
        why = _def(8)["why_hint"]
        if main_dll:
            why = f"工具通过 {main_dll} 读卡验证"
        return _state_from_def(8, why_hint=why)

    # Step 7 — 有样本，未分析成功（可回退再采）
    if sample_count >= 1 and not analyze_ok:
        if resample_requested:
            return _state_from_def(
                3,
                action="请再放一张空白卡到发卡器上，点「读空白卡（采样本）」",
                why_hint=f"已采集 {sample_count} 组样本，继续补充可提高置信度",
            )
        return _state_from_def(7)

    # Step 6 — 当前有有效对照，尚未添加
    if has_pair_on_current and not (current or {}).get("done"):
        return _state_from_def(6)

    # Step 5 — 原厂写卡已完成，待读已写
    if has_blank_only and oem_phase_complete:
        if not bridge_ok:
            return _state_from_def(
                5,
                action=_bridge_block_message(identity),
                bridge_blocked=True,
            )
        why = _def(5)["why_hint"]
        if main_dll:
            why = f"工具通过 {main_dll} 读卡"
        return _state_from_def(5, why_hint=why)

    # Step 4 — 空白已读，请去原厂写卡
    if has_blank_only:
        return _state_from_def(4)

    # Step 3 — 尚未读 blank（含新一轮采样）
    if site_ok and not blank:
        if not card_type_ready:
            return _state_from_def(2)
        if not bridge_ok:
            return _state_from_def(
                3,
                action=_bridge_block_message(identity),
                bridge_blocked=True,
            )
        why = _def(3)["why_hint"]
        if main_dll:
            why = f"工具通过 {main_dll} 读卡"
        return _state_from_def(3, why_hint=why)

    # 已写与空白相同 → 重新读已写
    if blank and written and blank.upper() == written.upper():
        return _state_from_def(
            5,
            action="已写数据与空白相同，请确认原厂已写卡后重读",
        )

    return _state_from_def(2)
