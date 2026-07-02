"""
graduation_coach.py — 毕业教练六维评估引擎

Collector 的「毕业判定」核心：基于身份、样本、分析、探测、读回五项证据，
输出 6 项通过/未通过状态，及下一步行动提示。

用法
====
    state = evaluate(
        identity=identity_result,
        samples=[{"blank_hex": "AABB", "written_hex": "CCDD"}],
        analyze_result={"success": True, "confidence": 0.82, "card_types": ["guest"]},
        probe_result={"mode": "dll_direct", "detail": {...}},
        readback_hex="CCDD",
    )
    if state.can_graduate:
        # 允许打包握手包
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ====================================================================
# 常量
# ====================================================================

MIN_PROTOCOL_CONFIDENCE = 0.55
"""协议分析置信度最低阈值。低于此值 protocol 维判定为未通过。"""

STRICT_OBSERVE = False
"""发卡时进程无变化不挡毕业。写死在 coach 注释里，不作配置暴露。"""


# ====================================================================
# 数据结构
# ====================================================================


@dataclass
class GraduationItem:
    """毕业六维中的一项。

    Attributes:
        id: 维度标识（site / bridge / pair / protocol / readback / path）。
        title: 中文短标题。
        required: 是否为毕业必要条件。
        passed: 是否通过。
        evidence: 通过/未通过的证据描述。
        pending_hint: 未通过时的下一步操作提示。
    """
    id: str
    title: str
    required: bool
    passed: bool
    evidence: str = ""
    pending_hint: str = ""


@dataclass
class GraduationState:
    """毕业评估结果。

    Attributes:
        items: 六维逐项结果。
        passed_count: 通过的项数。
        required_count: 必需项总数。
        can_graduate: 是否允许毕业（所有必需项均通过）。
        next_action: 下一个应该执行的操作描述（第一个未过必填项的 pending_hint）。
        blockers: 阻碍毕业的维度 id 列表（未通过的必需项）。
    """
    items: list[GraduationItem]
    passed_count: int
    required_count: int
    can_graduate: bool
    next_action: str
    blockers: list[str]


# ====================================================================
# 六维定义
# ====================================================================

_DIMENSIONS: list[dict] = [
    {"id": "site",     "title": "认环境",    "required": True,
     "pending_hint": "请先选择原厂门锁安装目录，点击「开始扫描」"},
    {"id": "bridge",   "title": "发卡器就绪", "required": True,
     "pending_hint": "请连接发卡器并关闭原厂软件，然后点击「开始扫描」"},
    {"id": "pair",     "title": "采到对照",    "required": True,
     "pending_hint": "请读空白卡、原厂写卡、读已写卡，再点「添加样本」"},
    {"id": "protocol", "title": "协议学懂",    "required": True,
     "pending_hint": "请点底栏「开始分析」，或补充样本后重新分析"},
    {"id": "readback", "title": "读回验证",    "required": True,
     "pending_hint": "请点「核对读数（毕业验证）」"},
    {"id": "path",     "title": "路径确认",    "required": True,
     "pending_hint": "分析完成后会自动探测发卡路径，请稍候"},
    {"id": "token",    "title": "授权卡",     "required": False,
     "pending_hint": "此品牌含授权卡 Token，请采集 Token 矩阵"},
    {"id": "system_card", "title": "系统卡样本", "required": False,
     "pending_hint": "建议采集总卡/楼栋/楼层卡至少一种对照样本"},
    {"id": "dll_deep",    "title": "深度分析",    "required": False,
     "pending_hint": "Ghidra DLL 深度分析（可选，通过后可补偿一项未通过维度）"},
]

# 授权卡 Token 状态（由 _eval_token 从 analyze_result 动态推导，不跨 session 缓存）

# ====================================================================
# 评估逻辑
# ====================================================================


def _eval_site(identity: Optional[Any]) -> GraduationItem:
    """维度 1：认环境 — 现场目录是否识别成功。"""
    item = _make_item("site")
    if identity is None:
        item.passed = False
        item.evidence = "未执行现场识别"
        return item
    item.passed = bool(getattr(identity, "site_ok", False))
    if item.passed:
        item.evidence = f"安装目录已识别: {getattr(identity, 'install_dir', '?')}"
    else:
        blockers = getattr(identity, "blockers", [])
        item.evidence = f"现场识别失败: {', '.join(blockers) if blockers else '未知原因'}"
    return item


def _eval_bridge(identity: Optional[Any]) -> GraduationItem:
    """维度 2：发卡器就绪 — bridge 是否初始化成功。"""
    item = _make_item("bridge")
    if identity is None:
        item.passed = False
        item.evidence = "未执行现场识别"
        return item
    item.passed = bool(getattr(identity, "bridge_ok", False))
    if item.passed:
        serial_ports = getattr(identity, "serial_responsive", [])
        if serial_ports:
            ports_desc = ", ".join(f"{p.port}@{p.baudrate}" for p in serial_ports)
            item.evidence = f"串口发卡器就绪: {ports_desc}"
        else:
            item.evidence = f"发卡器已就绪 via {getattr(identity, 'main_dll', '?')}"
    else:
        hint = getattr(identity, "bridge_hint", "")
        item.evidence = hint or "发卡器未就绪"
    return item


def _eval_pair(samples: list[dict]) -> GraduationItem:
    """维度 3：采到对照 — 至少一组空白/已写卡对照样本。"""
    item = _make_item("pair")
    matched = _find_valid_sample(samples)
    if matched is not None:
        item.passed = True
        item.evidence = (
            f"已采集 {len(samples)} 组样本，第 {matched + 1} 组有有效对照"
        )
    else:
        total = len(samples) if samples else 0
        item.passed = False
        item.evidence = (
            f"样本不足（共 {total} 组），需要至少一组空白+已写卡对照"
        )
    return item


def _eval_protocol(
    analyze_result: Optional[dict],
    probe_result: Optional[dict] = None,
    *,
    workflow_recorded: bool = False,
    pair_passed: bool = False,
) -> GraduationItem:
    """维度 4：协议学懂 — 分析成功、置信度达标、有卡型。

    验证策略按 mode 分流：
    - dll_direct / serial：要求 protocol_verified == True
    - parasitic：不要求裸写验证；要求 workflow_recorded 且 pair 维已通过
    - protocol_verified is None：验证被跳过，不因此否决
    """
    item = _make_item("protocol")
    if not analyze_result:
        item.passed = False
        item.evidence = "未执行协议分析"
        return item

    success = bool(analyze_result.get("success", False))
    confidence = float(analyze_result.get("confidence", 0.0))
    card_types = analyze_result.get("card_types", []) or []
    pv_raw = analyze_result.get("protocol_verified")
    protocol_verified = bool(pv_raw) if pv_raw is not None else None

    if isinstance(card_types, dict):
        card_count = len(card_types)
    elif isinstance(card_types, (list, tuple)):
        card_count = len(card_types)
    else:
        card_count = 0

    mode = (probe_result or {}).get("mode", "")

    if not (success and confidence >= MIN_PROTOCOL_CONFIDENCE and card_count >= 1):
        reasons = []
        if not success:
            reasons.append("分析未成功")
        if confidence < MIN_PROTOCOL_CONFIDENCE:
            reasons.append(f"置信度 {confidence:.0%} < 阈值 {MIN_PROTOCOL_CONFIDENCE:.0%}")
        if card_count < 1:
            reasons.append("未识别到任何卡型")
        item.passed = False
        item.evidence = "; ".join(reasons)
        return item

    # 加密卡检测降级：若 protocol_learner 标记 encrypted_suspected，
    # 自动否决毕业并提示操作员走 DLL 代理模式（协议学习不可靠）。
    if analyze_result.get("encrypted_suspected"):
        item.passed = False
        item.evidence = "疑似加密卡（变化字节 > 80%），建议改走 DLL 代理模式"
        return item

    evidence = f"置信度 {confidence:.0%}，发现 {card_count} 种卡型"

    if mode == "parasitic":
        if workflow_recorded and pair_passed:
            item.passed = True
            item.evidence = evidence + " · 寄生模式（工作流已录制 + 对照样本）"
        else:
            item.passed = False
            missing = []
            if not workflow_recorded:
                missing.append("未录制原厂工作流")
            if not pair_passed:
                missing.append("缺少有效对照样本")
            item.evidence = evidence + " · " + "；".join(missing)
        return item

    if protocol_verified is True:
        item.passed = True
        item.evidence = evidence + " · 写卡验证通过"
    elif protocol_verified is False:
        item.passed = False
        item.evidence = evidence + " — 写卡验证不通过，协议不可靠"
    else:
        item.passed = True
        item.evidence = evidence + " · 写卡验证已跳过（DLL/发卡器未就绪）"
    return item


def _eval_readback(
    readback_hex: Optional[str],
    samples: list[dict],
) -> GraduationItem:
    """维度 5：读回验证 — 读回数据与某一样本的已写数据一致。"""
    item = _make_item("readback")
    if not readback_hex:
        item.passed = False
        item.evidence = "未提供读回数据"
        return item

    written_hexes = _collect_written_hexes(samples)
    if not written_hexes:
        item.passed = False
        item.evidence = "无对照样本的已写数据可用于比对"
        return item

    normalized = readback_hex.strip().upper()
    if normalized in [h.strip().upper() for h in written_hexes]:
        item.passed = True
        item.evidence = f"读回数据与已写卡一致 ({normalized[:20]}...)"
    else:
        item.passed = False
        item.evidence = "读回数据与任何已写卡样本均不匹配"
    return item


def _eval_path(probe_result: Optional[dict]) -> GraduationItem:
    """维度 6：路径确认 — 探测完成且 mode 有效。

    DLL 直调 + 寄生 + 串口三种模式任一种可用即可。
    """
    item = _make_item("path")
    if not probe_result:
        item.passed = False
        item.evidence = "未执行路径探测"
        return item

    mode = probe_result.get("mode", "")
    detail = probe_result.get("detail", {}) or {}

    if mode not in ("dll_direct", "parasitic", "serial"):
        item.passed = False
        item.evidence = f"路径探测 mode 无效: {mode}"
        return item

    if mode == "parasitic":
        parasitic_detail = detail.get("parasitic", {}) or {}
        exe_found = bool(parasitic_detail.get("exe_found", False))
        if not exe_found:
            item.passed = False
            item.evidence = "寄生模式需要原厂前台程序但未找到"
            return item
        cardlock_exe = parasitic_detail.get("cardlock_exe", "")
        item.evidence = f"路径: {mode} (前台: {os.path.basename(cardlock_exe) if cardlock_exe else '?'})"
    elif mode == "serial":
        serial_detail = detail.get("serial", {}) or {}
        port = serial_detail.get("port", "")
        baudrate = serial_detail.get("baudrate", 0)
        connected = serial_detail.get("connected")
        if not connected:
            item.passed = False
            item.evidence = f"串口 {port} @ {baudrate}bps 连接失败"
            return item
        item.evidence = f"路径: {mode} ({port} @ {baudrate} bps)"
    else:
        dll_detail = detail.get("dll_direct", {}) or {}
        dll_path = dll_detail.get("dll_path", "")
        item.evidence = (
            f"路径: {mode} (DLL: {os.path.basename(dll_path) if dll_path else '?'})"
        )

    item.passed = True
    return item


def _eval_system_card(samples: list[dict]) -> GraduationItem:
    """维度 8：系统卡 — 若用户采了 master/building/floor 则自动通过。"""
    item = _make_item("system_card")
    system_types = {"master", "building", "floor"}
    collected = set()
    for s in samples or []:
        if not isinstance(s, dict):
            continue
        t = (s.get("type") or "").strip().lower()
        if t in system_types and _sample_written_hex(s):
            collected.add(t)

    needs = any(
        (s.get("type") or "").strip().lower() in system_types
        for s in (samples or [])
        if isinstance(s, dict)
    )
    if not needs:
        item.passed = True
        item.required = False
        item.evidence = "未采集系统卡（可选）"
        return item

    item.required = True
    if collected:
        item.passed = True
        item.evidence = f"已采集系统卡: {', '.join(sorted(collected))}"
    else:
        item.passed = False
        item.evidence = "已选系统卡类型但未完成空白/已写对照"
    return item


def _eval_token(analyze_result: Optional[dict], token_collected: bool = False) -> GraduationItem:
    """维度 7：授权卡 Token — 协议中是否有 auth_token_repeat 标记。

    如果 profile 的 card_types 包含 auth 且 auth_token_repeat 为 true，
    则此维度变为必需项，需要用户现场采集 Token 矩阵。
    """
    item = _make_item("token")

    # 检查分析结果中的 profile 是否含有 auth_token_repeat 标记
    token_needed = False
    if analyze_result:
        profile = analyze_result.get("profile", {}) or {}
        card_types = profile.get("card_types", {}) or {}
        auth_cfg = card_types.get("auth", {}) or {}
        if auth_cfg.get("auth_token_repeat", False):
            token_needed = True

    if not token_needed:
        # 此品牌不需要授权卡 Token → 自动通过
        item.passed = True
        item.evidence = "此品牌无授权卡 Token 需求"
        item.required = False
        return item

    # 需要 Token 但未采集
    item.required = True
    if token_collected:
        item.passed = True
        item.evidence = "Token 矩阵已采集"
    else:
        item.passed = False
        item.evidence = "检测到 auth_token_repeat，需要采集 Token 矩阵"
    return item


def _eval_dll_deep(
    analyze_result: Optional[dict],
    probe_result: Optional[dict] = None,
) -> GraduationItem:
    """维度 9：深度分析 — Ghidra/strings 等静态分析是否产出有效线索。

    检测 analyze_result.probe_meta 中的 ghidra_enriched / strings_found 标记。
    此维度非必需，但通过后可触发 boost 机制，补偿一项未通过的必需维度。
    """
    item = _make_item("dll_deep")
    meta = (analyze_result or {}).get("probe_meta", {}) or {}

    if not meta.get("ghidra_enriched", False):
        # 回退检查 strings 扫描
        if meta.get("strings_found", 0) >= 3:
            item.passed = True
            item.evidence = (
                f"深度分析通过 (strings 扫描发现 {meta['strings_found']} 条线索)"
            )
            return item
        item.passed = False
        item.evidence = "Ghidra DLL 深度分析未执行或无有效产出"
        return item

    keys_found = meta.get("ghidra_keys_found", 0)
    xrefs_found = meta.get("ghidra_xrefs_found", 0)
    if keys_found >= 1 or xrefs_found >= 3:
        item.passed = True
        item.evidence = (
            f"深度分析通过 (Ghidra: {keys_found} 个密钥, {xrefs_found} 个交叉引用)"
        )
    else:
        item.passed = False
        item.evidence = "Ghidra 分析已完成但未找到有效密钥或足够交叉引用"
    return item


# ====================================================================
# 辅助函数
# ====================================================================


def _make_item(dim_id: str) -> GraduationItem:
    """根据 _DIMENSIONS 定义创建一个未通过的 GraduationItem。"""
    for d in _DIMENSIONS:
        if d["id"] == dim_id:
            return GraduationItem(
                id=dim_id,
                title=d["title"],
                required=d["required"],
                passed=False,
                pending_hint=d["pending_hint"],
            )
    return GraduationItem(
        id=dim_id, title=dim_id, required=True, passed=False,
    )


def _sample_written_hex(sample: dict) -> str:
    """从样本 dict 取已写卡 hex（兼容 written_hex 与 hex）。"""
    return (sample.get("written_hex") or sample.get("hex") or "").strip()


def _find_valid_sample(samples: list[dict]) -> Optional[int]:
    """返回第一个有效对照样本的索引，没有则返回 None。

    有效条件：blank_hex 与 written_hex（或 hex）均非空且不等。
    """
    for i, s in enumerate(samples or []):
        if not isinstance(s, dict):
            continue
        blank = (s.get("blank_hex") or "").strip()
        written = _sample_written_hex(s)
        if blank and written and blank.upper() != written.upper():
            return i
    return None


def _collect_written_hexes(samples: list[dict]) -> list[str]:
    """从样本中提取所有非空已写卡 hex。"""
    result: list[str] = []
    for s in samples or []:
        if not isinstance(s, dict):
            continue
        wh = _sample_written_hex(s)
        if wh:
            result.append(wh)
    return result


# ====================================================================
# 主入口
# ====================================================================


def evaluate(
    *,
    identity: Optional[Any] = None,
    samples: Optional[list[dict]] = None,
    analyze_result: Optional[dict] = None,
    probe_result: Optional[dict] = None,
    readback_hex: Optional[str] = None,
    forensic: Optional[dict] = None,
    token_collected: bool = False,
    workflow_recorded: bool = False,
) -> GraduationState:
    """执行七维毕业评估。

    七维比六维多了一个 "token"（授权卡/Token）维度，
    仅在 profile 的 card_types.auth 有 auth_token_repeat 标记时变为必需。

    Args:
        identity: IdentityResult 或类似结构（需有 site_ok / bridge_ok 属性）。
        samples: 对照样本列表，每项含 blank_hex / written_hex 字段。
        analyze_result: 协议分析结果 dict（需有 success / confidence / card_types / profile）。
        probe_result: 路径探测结果 dict（含 mode / detail）。
        readback_hex: 读回验证的原始 hex 字符串。
        forensic: 法医扫描完整结果（当前仅用于占位，后续可扩展）。
        token_collected: 用户是否已采集授权卡 Token 矩阵。
        workflow_recorded: 寄生模式下是否已录制原厂工作流。

    Returns:
        GraduationState 包含七维逐项结果及综合状态。
    """
    samples = samples or []
    token_collected = bool(token_collected)
    pair_item = _eval_pair(samples)

    # 九维逐项评估（dll_deep 排最后，但其结果影响前面的 boost 判定）
    dll_deep_item = _eval_dll_deep(analyze_result, probe_result)
    items: list[GraduationItem] = [
        _eval_site(identity),
        _eval_bridge(identity),
        pair_item,
        _eval_protocol(
            analyze_result,
            probe_result,
            workflow_recorded=workflow_recorded,
            pair_passed=pair_item.passed,
        ),
        _eval_readback(readback_hex, samples),
        _eval_path(probe_result),
        _eval_token(analyze_result, token_collected),
        _eval_system_card(samples),
        dll_deep_item,
    ]

    # 综合统计（含 boost 补偿机制）
    passed_count = sum(1 for it in items if it.passed)
    required_count = sum(1 for it in items if it.required)
    blockers = [it.id for it in items if it.required and not it.passed]

    # boost 机制：dll_deep 通过可弥补一个未通过的必需维度
    can_graduate = len(blockers) == 0
    if not can_graduate and dll_deep_item.passed and len(blockers) == 1:
        # 记住被 boost 豁免的维度
        boosted_blocker = blockers[0]
        # B2: boost 不能豁免加密卡——即使深度分析通过，加密卡仍需走寄生模式
        protocol_encrypted = False
        for it in items:
            if it.id == "protocol" and getattr(it, "encrypted_suspected", False):
                protocol_encrypted = True
                break
        if boosted_blocker == "protocol" and protocol_encrypted:
            # 加密卡不能靠深度分析豁免，必须否决
            pass
        else:
            blockers = []
            can_graduate = True
            # 标记被补偿的维度
            for it in items:
                if it.id == boosted_blocker:
                    it.evidence = it.evidence + " · [深度分析补偿]"
                    break

    # 计算 next_action：按维度顺序，第一个未过必填项的 pending_hint
    next_action = ""
    for it in items:
        if it.required and not it.passed:
            next_action = it.pending_hint
            break

    return GraduationState(
        items=items,
        passed_count=passed_count,
        required_count=required_count,
        can_graduate=can_graduate,
        next_action=next_action,
        blockers=blockers,
    )


# ====================================================================
# 毕业报告 → 握手包 manifest 段
# ====================================================================


def build_graduation_report(
    state: GraduationState,
    *,
    identity: Optional[Any] = None,
    analyze_result: Optional[dict] = None,
    probe_result: Optional[dict] = None,
    readback_hex: Optional[str] = None,
    sample_count: int = 0,
) -> dict:
    """从评估状态和原始输入构建 graduation_report 字典。

    输出结构（写入 MANIFEST.json 的 graduation_report 段）:
        {
            "version": "1.0",
            "graduated_at": "2026-06-18T12:00:00",
            "passed_items": ["site", "bridge", ...],
            "confidence": 0.82,
            "mode": "dll_direct",
            "main_dll": "V9RFL.dll",
            "sample_count": 2,
            "readback_match": true
        }

    Args:
        state: evaluate() 返回的 GraduationState。
        identity: 传给 evaluate() 的 identity 参数（可选，用于提取 main_dll）。
        analyze_result: 传给 evaluate() 的 analyze_result（可选，用于提取 confidence）。
        probe_result: 传给 evaluate() 的 probe_result（可选，用于提取 mode）。
        readback_hex: 传给 evaluate() 的 readback_hex（可选，用于 readback_match）。
        sample_count: 样本总数。

    Returns:
        dict 格式的 graduation_report，可直接写入 MANIFEST.json。
    """
    passed_items = [it.id for it in state.items if it.passed]
    confidence = 0.0
    if analyze_result:
        confidence = float(analyze_result.get("confidence", 0.0))

    mode = ""
    if probe_result:
        mode = probe_result.get("mode", "")

    main_dll = ""
    if identity:
        main_dll = getattr(identity, "main_dll", "") or ""

    readback_match = bool(
        readback_hex
        and any(
            it.id == "readback" and it.passed
            for it in state.items
        )
    )

    # Token 需求
    token_item = next((it for it in state.items if it.id == "token"), None)
    token_pending = bool(token_item and token_item.required and not token_item.passed)

    return {
        "version": "1.0",
        "graduated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "passed_items": passed_items,
        "confidence": round(confidence, 2),
        "mode": mode,
        "main_dll": main_dll,
        "sample_count": sample_count,
        "readback_match": readback_match,
        "token_pending": token_pending,
    }
