"""
handover_assembler.py — 采集结果 → PMS 可导入握手包 的数据组装

Collector 分析完成后调用，把 DLL 探针 / 协议学习 / MDB / UI 录制
合并为 HandoverPackager 可直接消费的 learn_result 字典。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional

from .analysis_types import ProbeResult


def probe_result_from_install(
    install_dir: str,
    loaded_dll: str = "",
) -> tuple[ProbeResult, dict]:
    """用 dll_probe 扫描安装目录，返回 ProbeResult 与原始探测 dict。"""
    from .dll_probe import probe as dll_probe_install

    raw: dict = {}
    probe = ProbeResult()
    try:
        raw = dll_probe_install(install_dir) or {}
    except Exception:
        raw = {}

    dll_path = raw.get("dll_path") or ""
    dll_name = loaded_dll or (os.path.basename(dll_path) if dll_path else "")

    if raw.get("detected"):
        probe.brand_guess = raw.get("brand_guess") or "auto_detected"
        probe.dll_name = dll_name or os.path.basename(dll_path)
        probe.dll_path = dll_path
        probe.confidence = float(raw.get("confidence") or 0.0)
        probe.can_issue = bool(raw.get("can_issue"))
        probe.classified = dict(raw.get("matched_functions") or {})
        probe.hardcoded_match = dict(raw.get("hardcoded_fallback") or {})
        probe.exports = list(raw.get("exports") or [])
    elif dll_name:
        probe.dll_name = dll_name
        probe.brand_guess = "auto_detected"
    else:
        probe.brand_guess = "auto_detected"

    return probe, raw


def enrich_profile(
    profile: dict,
    probe_result: ProbeResult,
    install_dir: str,
    probe_raw: Optional[dict] = None,
    forensic: Optional[dict] = None,
) -> dict:
    """把 DLL 探针与现场身份信息写入 profile。"""
    p = dict(profile)
    dll_name = probe_result.dll_name or p.get("dll", {}).get("path") or ""
    if dll_name:
        dll_cfg = dict(p.get("dll") or {})
        dll_cfg["path"] = dll_name
        if probe_result.classified or probe_result.hardcoded_match:
            merged = dict(probe_result.hardcoded_match or {})
            merged.update(probe_result.classified or {})
            for key in ("init", "init_usb", "read", "write", "guest"):
                if key in merged:
                    dll_cfg[key] = merged[key]
        p["dll"] = dll_cfg
        detect = dict(p.get("detect") or {})
        detect["files"] = [dll_name]
        p["detect"] = detect

    if probe_result.brand_guess and probe_result.brand_guess != "auto_detected":
        p["brand"] = probe_result.brand_guess
    elif probe_raw and probe_raw.get("candidate_profile"):
        cp = probe_raw["candidate_profile"]
        if cp.get("brand"):
            p["brand"] = cp["brand"]
        if cp.get("adapter_id") and str(p.get("adapter_id", "")).startswith("auto_"):
            p["adapter_id"] = cp["adapter_id"]

    p["install"] = {
        "main_dll": dll_name,
        "install_dir": install_dir,
    }

    if forensic:
        ini = forensic.get("system_ini")
        if ini is not None:
            dls = getattr(ini, "dls_co_id", None) or (ini.get("dls_co_id") if isinstance(ini, dict) else None)
            hotel = getattr(ini, "hotel_id", None) or (ini.get("hotel_id") if isinstance(ini, dict) else None)
            if dls:
                site = dict(p.get("site_code") or {})
                site["hint_dlsCoID"] = dls
                p["site_code"] = site
            if hotel:
                p.setdefault("probe_meta", {})["hotel_id"] = hotel

    return p


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def normalize_rooms(rooms: list) -> list[dict]:
    """统一 room_data 字段，兼容 PMS HandoverImporter。"""
    out: list[dict] = []
    for r in rooms or []:
        if not isinstance(r, dict):
            continue
        room_id = r.get("room_id") or r.get("RoomNo") or ""
        if not room_id:
            continue
        bld = r.get("bld_no") if r.get("bld_no") is not None else r.get("building_no")
        flr = r.get("flr_no") if r.get("flr_no") is not None else r.get("floor_no")
        rom = r.get("rom_id")
        floor_txt = r.get("floor") or r.get("floor_no") or flr or ""
        out.append({
            "room_id": str(room_id),
            "lock_no": str(r.get("lock_no") or ""),
            "building_no": str(
                r.get("building_no") or r.get("bld_no") or r.get("building") or ""
            ),
            "floor_no": str(floor_txt),
            "bld_no": _safe_int(bld, 1),
            "flr_no": _safe_int(flr, 0),
            "rom_id": _safe_int(rom, 0),
            "floor": str(floor_txt),
            "room_type": str(r.get("room_type") or "标准间"),
            "current_seq": int(r.get("current_seq") or 0),
        })
    return out


def normalize_guests(guests: list) -> list[dict]:
    out: list[dict] = []
    for g in guests or []:
        if not isinstance(g, dict):
            continue
        room_id = g.get("room_id") or g.get("RoomNo") or ""
        if not room_id:
            continue
        out.append({
            "room_id": str(room_id),
            "guest_name": str(g.get("guest_name") or g.get("name") or "（迁移）"),
            "checkin_time": str(g.get("checkin_time") or g.get("checkin") or ""),
            "checkout_time": str(g.get("checkout_time") or g.get("checkout") or ""),
            "phone": str(g.get("phone") or ""),
            "id_card": str(g.get("id_card") or g.get("idcard") or ""),
        })
    return out


def collect_dll_file_names(profile: dict, install_dir: str, extra: Optional[list] = None) -> list[str]:
    """收集应打入握手包的主 DLL 及常见依赖名。"""
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        n = (name or "").strip()
        if not n:
            return
        key = n.lower()
        if key in seen:
            return
        seen.add(key)
        names.append(n)

    for n in extra or []:
        _add(n)

    dll_cfg = profile.get("dll") or {}
    _add(dll_cfg.get("path", ""))
    install_cfg = profile.get("install") or {}
    _add(install_cfg.get("main_dll", ""))

    for n in profile.get("detect", {}).get("files", []) or []:
        _add(n)

    # 常见 proUSB 依赖
    for dep in ("d12.dll", "Mwic_32.dll", "USB.dll"):
        p = os.path.join(install_dir, dep)
        if os.path.isfile(p):
            _add(dep)

    return names


def ui_map_to_button_map(ui_map: Any) -> dict:
    if ui_map is None:
        return {}
    if is_dataclass(ui_map):
        return dict(getattr(ui_map, "card_type_buttons", {}) or {})
    if isinstance(ui_map, dict):
        return dict(ui_map.get("card_type_buttons") or ui_map)
    return {}


def workflow_to_dict(workflow: Any) -> dict:
    if workflow is None:
        return {}
    if is_dataclass(workflow):
        return asdict(workflow)
    if isinstance(workflow, dict):
        return workflow
    return {}


def build_workflow_bundle(
    workflow_guest: Any = None,
    workflow_master: Any = None,
    workflows_by_type: Optional[dict] = None,
) -> dict:
    bundle: dict = {}
    if workflows_by_type:
        for key, wf in workflows_by_type.items():
            if wf:
                bundle[key] = workflow_to_dict(wf)
    if workflow_guest and "guest_card" not in bundle:
        bundle["guest_card"] = workflow_to_dict(workflow_guest)
    if workflow_master and "master_card" not in bundle:
        bundle["master_card"] = workflow_to_dict(workflow_master)
    return bundle


def build_deployment_context(
    install_dir: str,
    profile: dict,
    forensic: Optional[dict] = None,
    cardlock_exe: str = "",
    loaded_dll: str = "",
    hotel_name: str = "",
) -> dict:
    ctx: dict = {
        "install_dir": install_dir,
        "cardlock_exe": cardlock_exe or "",
        "loaded_dll": loaded_dll or profile.get("dll", {}).get("path", ""),
        "hotel_name": hotel_name or "",
        "brand": profile.get("brand", ""),
        "adapter_id": profile.get("adapter_id", ""),
    }
    # 串口品牌配置
    channel = profile.get("channel", "")
    if channel == "serial":
        serial_cfg = profile.get("serial", {})
        ctx["channel"] = "serial"
        ctx["serial_port"] = serial_cfg.get("port", "")
        ctx["serial_baudrate"] = serial_cfg.get("baudrate", 9600)
    if forensic:
        ini = forensic.get("system_ini")
        if ini is not None:
            ctx["dls_co_id"] = getattr(ini, "dls_co_id", None) or (
                ini.get("dls_co_id") if isinstance(ini, dict) else None
            )
            ctx["hotel_id"] = getattr(ini, "hotel_id", None) or (
                ini.get("hotel_id") if isinstance(ini, dict) else None
            )
            ctx["pc_id"] = getattr(ini, "pc_id", None) or (
                ini.get("pc_id") if isinstance(ini, dict) else None
            )
    return ctx


def load_room_export(path: str) -> tuple[list, list]:
    if not path or not os.path.isfile(path):
        return [], []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return normalize_rooms(data.get("rooms", [])), normalize_guests(data.get("guests", []))
    except Exception:
        return [], []


def assemble_handover_payload(
    analyze_result: dict,
    install_dir: str,
    loaded_dll: str = "",
    forensic: Optional[dict] = None,
    ui_map: Any = None,
    workflow_guest: Any = None,
    workflow_master: Any = None,
    workflows_by_type: Optional[dict] = None,
    cardlock_exe: str = "",
    hotel_name: str = "",
) -> dict:
    """把分析结果 + UI 侧数据合并为 HandoverPackager 输入。"""
    profile = dict(analyze_result.get("profile") or {})
    room_data, guest_data = load_room_export(analyze_result.get("room_data_path", ""))

    button_map = ui_map_to_button_map(ui_map)
    if not button_map and forensic:
        ui_from_forensic = forensic.get("ui_map")
        button_map = ui_map_to_button_map(ui_from_forensic)

    workflow = build_workflow_bundle(
        workflow_guest, workflow_master, workflows_by_type=workflows_by_type,
    )

    dll_files = collect_dll_file_names(profile, install_dir, [loaded_dll])

    payload = {
        "success": analyze_result.get("success", True),
        "brand": profile.get("brand", "unknown"),
        "adapter_id": profile.get("adapter_id", ""),
        "profile": profile,
        "profile_path": analyze_result.get("profile_path", ""),
        "room_data": room_data,
        "guest_data": guest_data,
        "room_data_path": analyze_result.get("room_data_path", ""),
        "button_map": button_map,
        "workflow": workflow,
        "dll_files": dll_files,
        "deployment_context": build_deployment_context(
            install_dir, profile, forensic, cardlock_exe, loaded_dll, hotel_name,
        ),
        "card_types": analyze_result.get("card_types", []),
        "confidence": analyze_result.get("confidence", 0),
    }
    return payload
