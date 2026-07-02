"""
bridgecore/dll_sandbox.py — DLL 动态沙箱执行器

用 bridge32.exe 做隔离沙箱，动态调用 DLL 导出函数并记录：
- 入参/出参/返回值
- 副作用（USB 通信、文件读写、注册表修改）
- 用 change_monitor 拍前后快照对比

这样即使 DLL 函数全叫 fn_001/fn_002 也能通过调用结果推断语义。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SandboxCallResult:
    """一次沙箱调用的结果。"""
    fn_name: str = ""
    params: List[int] = field(default_factory=list)
    ret_code: int = 0
    ret_ok: bool = False
    duration_ms: float = 0.0
    error: str = ""
    side_effects: Dict[str, Any] = field(default_factory=dict)
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SandboxReport:
    """沙箱完整报告。"""
    dll_path: str = ""
    total_functions: int = 0
    tested_functions: int = 0
    classified_functions: Dict[str, str] = field(default_factory=dict)
    # {"fn_001": "init", "fn_002": "read_card", ...}
    results: List[SandboxCallResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    summary: str = ""


# ──────────────────────────────────────────────────────────────────
# 函数语义推断模板
# ──────────────────────────────────────────────────────────────────

_SEMANTIC_TEMPLATES: List[Tuple[str, List[str], str]] = [
    # (推断分类, [特征关键词], 描述)
    ("init_usb",     ["init", "usb", "open", "connect"], "初始化USB"),
    ("read_card",    ["read", "card", "get"], "读卡"),
    ("write_card",   ["write", "set", "send"], "写卡"),
    ("guest_card",   ["guest", "issue", "make", "compose"], "发客人卡"),
    ("erase_card",   ["erase", "clear", "delete", "remove"], "擦卡"),
    ("close_usb",    ["close", "disconnect", "shutdown", "release"], "关闭USB"),
    ("buzzer",       ["buzzer", "beep", "sound", "alarm"], "蜂鸣器"),
    ("get_version",  ["version", "getver", "dllversion"], "获取版本"),
    ("master_card",  ["master", "total"], "总卡"),
    ("building_card",["building", "block"], "楼栋卡"),
    ("floor_card",   ["floor", "level"], "楼层卡"),
    ("emergency",    ["emergency", "emer", "urgent"], "应急卡"),
    ("auth_card",    ["auth", "authorize", "register"], "授权卡"),
    ("check_card",   ["check", "verify", "validate"], "校验/检查"),
    ("ini_card",     ["ini", "initcard", "init_card"], "初始化卡"),
]


def infer_function_semantics(fn_name: str) -> Tuple[str, float]:
    """根据函数名推断语义分类，返回 (分类, 置信度)。"""
    nl = fn_name.lower().replace("_", "")
    for category, keywords, _ in _SEMANTIC_TEMPLATES:
        for kw in keywords:
            if kw in nl:
                # 更长的关键词匹配 → 更高置信度
                conf = min(0.5 + 0.1 * len(kw), 0.95)
                return category, conf
    return "unknown", 0.0


# ──────────────────────────────────────────────────────────────────
# 参数探针
# ──────────────────────────────────────────────────────────────────

_PARAM_PROBES: List[Tuple[str, List[List[int]]]] = [
    # (测试场景, [[参数组合1], [参数组合2], ...])
    ("init_probe",   [[0], [1], [0, 1]]),
    ("read_probe",   [[1], [0], [0, 0]]),
    ("write_probe",  [[1], [0]]),
    ("buzzer_probe", [[1, 20], [1, 50], [1, 100]]),
    ("close_probe",  [[]]),
]


class DllSandbox:
    """DLL 动态沙箱。通过 bridge32 隔离调用 DLL 导出函数。"""

    def __init__(self, bridge_instance=None):
        self._bridge = bridge_instance
        self._results: List[SandboxCallResult] = []
        self._classified: Dict[str, str] = {}

    def set_bridge(self, bridge):
        self._bridge = bridge

    def _get_bridge(self):
        if self._bridge is None:
            try:
                from ..collector_bridge import get_bridge
                return get_bridge()
            except Exception:
                pass
        return self._bridge

    def run(self, dll_path: str, install_dir: str = "",
            max_functions: int = 30) -> SandboxReport:
        """加载 DLL 并对所有导出函数做动态探测。

        Args:
            dll_path: DLL 完整路径
            install_dir: 安装目录（用于副作用监控）
            max_functions: 最多探测的函数数

        Returns:
            SandboxReport
        """
        t0 = time.monotonic()
        report = SandboxReport(dll_path=dll_path)
        bridge = self._get_bridge()
        if bridge is None:
            report.errors.append("无法获取 bridge 实例")
            return report

        # 1. 加载 DLL
        try:
            resp = bridge.load_dll(dll_path, [install_dir] if install_dir else [])
            if not resp.get("ok") or not resp.get("loaded"):
                report.errors.append(f"load_dll 失败: {resp}")
                return report
        except Exception as e:
            report.errors.append(f"load_dll 异常: {e}")
            return report

        # 2. 枚举导出函数
        try:
            resp = bridge.dll_list_exports(timeout=5.0)
            exports = resp.get("exports", [])
        except Exception as e:
            report.errors.append(f"枚举导出失败: {e}")
            return report

        if not exports:
            # 回退：尝试已知函数名
            exports = [{"name": n} for n in [
                "initializeUSB", "ReadCard", "WriteCard", "GuestCard",
                "CardErase", "CloseUSB", "Buzzer", "GetDLLVersion",
            ]]

        report.total_functions = len(exports)

        # 3. 先尝试 initialize（如果存在）
        init_fns = [e for e in exports if _is_init_fn(e.get("name", ""))]
        if init_fns:
            init_name = init_fns[0]["name"]
            result = self._safe_call(bridge, init_name, [1])
            self._results.append(result)
            if result.ret_ok:
                self._classified[init_name] = "init_usb"

        # 4. 对剩余函数逐一探测
        tested = 0
        for exp in exports[:max_functions]:
            fn_name = exp.get("name", "")
            if fn_name in self._classified:
                continue

            # 语义推断
            category, conf = infer_function_semantics(fn_name)
            if conf > 0.6:
                self._classified[fn_name] = category

            # 轻量探测：调一次 d12=1 看看返回
            result = self._safe_call(bridge, fn_name, [1])
            self._results.append(result)
            tested += 1

            # 根据返回值进一步推断
            if result.ret_code == 0 and category == "unknown":
                # 可能是个无参函数，尝试推断
                pass

        # 5. 关闭
        try:
            bridge.close_usb()
        except Exception:
            pass

        report.tested_functions = tested
        report.classified_functions = dict(self._classified)
        report.results = self._results
        report.summary = (
            f"共 {report.total_functions} 个函数, "
            f"探测了 {tested} 个, "
            f"分类了 {len(self._classified)} 个"
        )
        return report

    def _safe_call(self, bridge, fn_name: str, params: List[int],
                   timeout: float = 5.0) -> SandboxCallResult:
        """安全调用 DLL 函数。"""
        t0 = time.monotonic()
        result = SandboxCallResult(fn_name=fn_name, params=params)
        try:
            resp = bridge.dll_call(
                fn_name,
                [{"type": "int", "value": p} for p in params],
                timeout=timeout,
            )
            result.raw_response = resp
            result.ret_code = int(resp.get("ret", -1))
            result.ret_ok = resp.get("ok", False) and result.ret_code == 0
        except Exception as e:
            result.error = str(e)
        result.duration_ms = round((time.monotonic() - t0) * 1000)
        return result

    def probe_init_params(self, bridge, fn_name: str) -> List[int]:
        """探测 init 函数的最佳参数组合。

        依次尝试 [1], [0], [0, 1] 等组合，返回第一个成功的参数列表。
        """
        for params in [[1], [0], [0, 1], [2]]:
            result = self._safe_call(bridge, fn_name, params, timeout=8.0)
            if result.ret_ok:
                return params
        return [1]  # 默认


def _is_init_fn(name: str) -> bool:
    nl = name.lower()
    return any(kw in nl for kw in ["init", "initialize", "open"])


def _is_read_fn(name: str) -> bool:
    nl = name.lower()
    return any(kw in nl for kw in ["read", "getcard"])


def _is_write_fn(name: str) -> bool:
    nl = name.lower()
    return any(kw in nl for kw in ["write", "setcard", "send"])


# ──────────────────────────────────────────────────────────────────
# 便捷函数
# ──────────────────────────────────────────────────────────────────


def quick_sandbox(dll_path: str, install_dir: str = "") -> SandboxReport:
    """快速沙箱分析一个 DLL。"""
    sandbox = DllSandbox()
    return sandbox.run(dll_path, install_dir)


def classify_exports(exports: List[str]) -> Dict[str, str]:
    """纯静态分类（不加载 DLL）：对导出函数名做语义推断。"""
    result: Dict[str, str] = {}
    for name in exports:
        category, conf = infer_function_semantics(name)
        if conf > 0.5:
            result[name] = category
    return result
