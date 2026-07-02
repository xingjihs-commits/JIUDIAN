"""
identity_engine.py — Collector 第①步万能身份引擎

编排：现场扫描 → DLL 推断 → 冲突检测 → 动态验证（可选）
不绑定 V9 命名；V9 仅作为 brand_analyzer 可选快路径。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .dll_probe import probe_candidates
from .oem_process import (
    OemExeInfo,
    OemProcess,
    find_oem_exes,
    find_running_oem_processes,
    format_oem_running_hint,
)
from .serial_channel import SerialPortInfo, SerialScanner

if TYPE_CHECKING:
    from ..collector_bridge import CollectorBridge

logger = logging.getLogger(__name__)

_BRIDGE_RET_HINTS: Dict[int, str] = {
    259: "设备被占用（发卡器可能被其他程序占用）",
    3: "未检测到发卡器或驱动异常",
    1: "初始化失败（USB 未就绪）",
}


@dataclass
class IdentityResult:
    install_dir: str = ""
    site_ok: bool = False
    main_dll: str = ""
    confidence: float = 0.0
    candidate_profile: Optional[Dict[str, Any]] = None
    oem_exes: List[OemExeInfo] = field(default_factory=list)
    running_oem: List[OemProcess] = field(default_factory=list)
    bridge_ok: bool = False
    bridge_ret: int = -1
    bridge_hint: str = ""
    blockers: List[str] = field(default_factory=list)
    evidence_lines: List[str] = field(default_factory=list)
    fs_report: Any = None
    dll_candidates: List[Dict[str, Any]] = field(default_factory=list)

    # 串口信息（非 Dll 品牌）
    serial_ports: List[SerialPortInfo] = field(default_factory=list)
    serial_responsive: List[SerialPortInfo] = field(default_factory=list)

    @property
    def can_proceed_sample(self) -> bool:
        """现场识别成功即可进入采样（读卡时再要求 bridge）。"""
        return self.site_ok

    @property
    def summary_title(self) -> str:
        if self.site_ok and self.bridge_ok:
            if self.serial_responsive:
                return "现场识别完成 · 串口发卡器就绪"
            return "现场识别完成 · 发卡器就绪"
        if self.site_ok:
            return "现场识别完成 · 发卡器待就绪"
        return "现场识别未完成"


def _hint_for_ret(ret: int) -> str:
    if ret in _BRIDGE_RET_HINTS:
        return _BRIDGE_RET_HINTS[ret]
    if ret != 0:
        return f"初始化返回码 {ret}（无设备、驱动异常或被占用）"
    return ""


def _init_fn_from_profile(profile: Dict[str, Any]) -> Optional[str]:
    dll_cfg = profile.get("dll") or {}
    return dll_cfg.get("init") or dll_cfg.get("init_usb")


def _init_params_from_profile(profile: Dict[str, Any]) -> List[int]:
    dll_cfg = profile.get("dll") or {}
    params = dll_cfg.get("init_params")
    if isinstance(params, list) and params:
        return [int(p) for p in params]
    return [0, 1]


def _build_site_evidence(fs_report: Any, oem_exes: List[OemExeInfo],
                         best: Optional[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    if fs_report and getattr(fs_report, "system_ini", None):
        ini = fs_report.system_ini
        lines.append(
            f"配置: dlsCoID={getattr(ini, 'dls_co_id', '') or '?'}  "
            f"HotelID={getattr(ini, 'hotel_id', '') or '?'}"
        )
    if fs_report and getattr(fs_report, "mdb_summary", None):
        mdb = fs_report.mdb_summary
        lines.append(
            f"数据库: {getattr(mdb, 'source', '?')} "
            f"({len(getattr(mdb, 'tables', []) or [])}表, "
            f"{getattr(mdb, 'room_count', 0)}房)"
        )
    if oem_exes:
        lines.append(f"前台程序: {oem_exes[0].name}")
    if best:
        matched = best.get("matched_functions") or {}
        groups = [g for g in ("init_usb", "init", "read_card", "guest_card") if g in matched]
        if groups:
            lines.append(f"DLL 依据: 导出 {', '.join(groups)}")
    return lines


def _verify_bridge(
    install_dir: str,
    candidates: List[Dict[str, Any]],
    bridge: "CollectorBridge",
) -> tuple[bool, int, str, str, Optional[Dict[str, Any]]]:
    """按 confidence 尝试 load + bind + generic_initialize。"""
    last_ret = -1
    last_hint = ""
    resolved_dir = str(Path(install_dir).resolve())

    for cand in candidates:
        profile = cand.get("candidate_profile")
        if not profile:
            continue
        dll_name = cand.get("dll_name") or (profile.get("dll") or {}).get("path", "")
        if not dll_name:
            continue
        init_fn = _init_fn_from_profile(profile)
        if not init_fn:
            logger.info("跳过 %s：未推断 init 函数", dll_name)
            continue

        dll_path = str(Path(install_dir) / dll_name)
        if not os.path.isfile(dll_path):
            continue

        try:
            bridge.start()
            lr = bridge.load_dll(dll_path, [resolved_dir])
            if not (lr.get("ok") and lr.get("loaded")):
                continue
            br = bridge.bind_from_profile(profile)
            bound = br.get("bound") or []
            if init_fn not in bound:
                logger.info("bind 未包含 %s: %s", init_fn, br)
                continue

            params = _init_params_from_profile(profile)
            ir = bridge.generic_initialize(init_fn, params)
            ret = int(ir.get("ret", -1))
            last_ret = ret
            if ir.get("ok") and ret == 0:
                return True, 0, dll_name, "", profile

            last_hint = _hint_for_ret(ret)
            logger.info("init %s(%s) ret=%s", dll_name, init_fn, ret)
        except Exception as e:
            logger.warning("bridge 验证 %s 异常: %s", dll_name, e)
            last_hint = str(e)

    if not last_hint:
        last_hint = "未能通过任何 DLL 候选连接发卡器"
    return False, last_ret, "", last_hint, None


def analyze(
    install_dir: str,
    *,
    bridge: Optional["CollectorBridge"] = None,
    skip_bridge: bool = False,
) -> IdentityResult:
    """主入口：分析原厂安装目录，返回结构化身份结果。"""
    result = IdentityResult(install_dir=install_dir)

    # Layer 1 — 现场扫描
    fs_report = None
    try:
        from ..filesystem_scanner import FileSystemScanner
    except ImportError:
        from filesystem_scanner import FileSystemScanner  # noqa: E0611
    try:
        scanner = FileSystemScanner(install_dir)
        fs_report = scanner.scan()
        result.fs_report = fs_report
    except Exception as e:
        logger.warning("文件扫描失败: %s", e)
        result.blockers.append("fs_scan_failed")
        result.evidence_lines.append(f"文件扫描失败: {e}")

    result.oem_exes = find_oem_exes(install_dir)

    has_ini = bool(fs_report and getattr(fs_report, "system_ini", None))
    has_mdb = bool(fs_report and getattr(fs_report, "mdb_summary", None))
    has_dll_exports = bool(fs_report and getattr(fs_report, "dll_exports", None))
    result.site_ok = bool(
        os.path.isdir(install_dir)
        and (has_ini or has_mdb or has_dll_exports or result.oem_exes)
    )

    if not result.site_ok:
        result.blockers.append("no_site")
        result.bridge_hint = "目录下未识别到门锁配置或 DLL，请确认选对了原厂安装文件夹"
        return result

    # Layer 2 — DLL 推断
    try:
        result.dll_candidates = probe_candidates(install_dir, top_n=3)
    except Exception as e:
        logger.warning("DLL 探测失败: %s", e)
        result.blockers.append("dll_probe_failed")

    best = result.dll_candidates[0] if result.dll_candidates else None
    if best:
        result.main_dll = best.get("dll_name") or ""
        result.confidence = float(best.get("confidence") or 0)
        result.candidate_profile = best.get("candidate_profile")
        result.evidence_lines = _build_site_evidence(
            fs_report, result.oem_exes, best,
        )
    else:
        result.blockers.append("no_dll")
        result.evidence_lines.append("未找到带 init/read 导出的业务 DLL")

    # Layer 2.5 — 串口扫描（补充通道探测）
    _serial_available = False
    try:
        scanner = SerialScanner()
        result.serial_ports = scanner.scan()
        if result.serial_ports:
            logger.info("发现 %d 个串口: %s", len(result.serial_ports),
                        [p.port for p in result.serial_ports])
            responsive = scanner.probe(result.serial_ports, timeout=1.0)
            if responsive:
                result.serial_responsive = responsive
                _serial_available = True
                ports_desc = ", ".join(f"{p.port}@{p.baudrate}" for p in responsive)
                result.evidence_lines.append(f"串口: {ports_desc} 响应探测")
                # 如果没有 DLL 但串口有响应 → 串口品牌
                if not best:
                    p = responsive[0]
                    result.site_ok = True
                    result.candidate_profile = {
                        "channel": "serial",
                        "serial": {"port": p.port, "baudrate": p.baudrate},
                    }
                    result.evidence_lines.append(
                        f"推断为串口通信品牌: {p.port} @ {p.baudrate} bps")
    except Exception as e:
        logger.info("串口扫描跳过: %s", e)

    # Layer 3 — 冲突检测
    result.running_oem = find_running_oem_processes(install_dir)
    if result.running_oem:
        result.blockers.append("oem_running")
        result.bridge_hint = format_oem_running_hint(result.running_oem)
        result.bridge_ok = False
        return result

    # Layer 4 — 动态验证（仅 Dll 品牌）
    if _serial_available and not result.dll_candidates:
        # 串口品牌：标记 bridge 就绪（走 SerialBridge）
        result.bridge_ok = True
        result.bridge_hint = f"串口设备: {', '.join(p.port for p in result.serial_responsive)}"
        result.evidence_lines.append(result.bridge_hint)
        return result

    if skip_bridge or bridge is None:
        return result

    if not result.dll_candidates:
        result.blockers.append("no_dll")
        return result

    ok, ret, loaded_dll, hint, profile = _verify_bridge(
        install_dir, result.dll_candidates, bridge,
    )
    result.bridge_ok = ok
    result.bridge_ret = ret
    if ok:
        result.main_dll = loaded_dll or result.main_dll
        if profile:
            result.candidate_profile = profile
        result.evidence_lines.append(f"发卡器: 已通过 {result.main_dll} 连接")
    else:
        result.bridge_hint = hint or _hint_for_ret(ret)
        if ret != 0:
            result.blockers.append("usb_init_failed")
        else:
            result.blockers.append("bridge_failed")

    return result
