"""
bridgecore/parasitic_replay.py — 寄生回放器

当 DLL 直调走不通时（加密卡/混淆 DLL/64位 DLL），
通过 pywinauto 录制原厂 CardLock.exe 操作并自动回放。

核心能力：
1. 录制：启动 CardLock.exe → 监视控件变化 → 录制操作步骤
2. 回放：按录制的工作流自动操作 CardLock.exe 发卡
3. 提取：发卡后用 bridge32 读回卡数据，完成闭环

与 ui_workflow.py 的关系：
- ui_workflow.py 是采集器中的录制器（UI Map + 工作流录制）
- 本模块是它的引擎层封装，不依赖 PySide6 UI
- 本模块补充了 cardlock_auto.py 不具备的"自动录制"能力
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# 工作流数据结构
# ──────────────────────────────────────────────────────────────────


@dataclass
class PlaybackStep:
    index: int = 0
    action: str = ""            # click / type / select / wait / check / read_card / verify
    target: str = ""            # 控件名称/按钮文字
    value: str = ""             # 输入值/等待时间
    wait_sec: float = 0.0       # 执行后等待
    description: str = ""
    condition: str = ""         # 可选：执行条件（如 "window_exists=发卡成功"）
    retry: int = 1              # 失败重试次数


@dataclass
class ParasiticWorkflow:
    name: str = ""
    card_type: str = ""
    exe_path: str = ""
    button_map: Dict[str, str] = field(default_factory=dict)
    steps: List[PlaybackStep] = field(default_factory=list)
    created_at: str = ""
    source: str = ""            # "recorded" / "manual" / "imported"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayResult:
    ok: bool = False
    workflow_name: str = ""
    total_steps: int = 0
    completed_steps: int = 0
    failed_step: Optional[PlaybackStep] = None
    error: str = ""
    card_hex: str = ""          # 发卡后读回的数据
    duration_sec: float = 0.0
    logs: List[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────
# 按钮映射（常见品牌 CardLock.exe 界面）
# ──────────────────────────────────────────────────────────────────

DEFAULT_BUTTON_MAPS: Dict[str, Dict[str, str]] = {
    "proUSB_V9_CN": {
        "guest": "散客客人卡", "checkout": "退房卡", "loss": "挂失卡",
        "record": "记录卡", "roomset": "房号设置卡", "timeset": "时钟设置卡",
        "groupset": "组号设置卡", "master": "总卡", "building": "楼栋卡",
        "floor": "层控卡", "emergency": "应急卡", "group": "组控卡",
        "auth": "授权卡",
    },
    "proUSB_V9_EN": {
        "guest": "Guest Card", "checkout": "Check-out Card",
        "master": "Master Card", "building": "Building Card",
        "floor": "Floor Card", "emergency": "Emergency Card",
    },
    "aidier": {
        "guest": "客人卡", "master": "总管卡",
        "building": "楼栋卡", "floor": "楼层卡",
    },
}


# ──────────────────────────────────────────────────────────────────
# 录制器（引擎层，无 UI 依赖）
# ──────────────────────────────────────────────────────────────────


class ParasiticRecorder:
    """pywinauto 寄生录制引擎。"""

    def __init__(self, cardlock_exe_path: str):
        self._exe_path = Path(cardlock_exe_path)
        self._app: Any = None
        self._main_window: Any = None
        self._recording = False
        self._steps: List[PlaybackStep] = []
        self._start_time: float = 0.0
        self._stop_event = threading.Event()

    @property
    def is_running(self) -> bool:
        if self._app is None:
            return False
        try:
            return self._app.is_process_running()
        except Exception:
            return False

    def start_app(self, timeout: float = 30.0) -> bool:
        """启动 CardLock.exe 并连接 pywinauto。"""
        try:
            import pywinauto
        except ImportError:
            logger.error("pywinauto 未安装")
            return False

        if not self._exe_path.is_file():
            logger.error("CardLock.exe 不存在: %s", self._exe_path)
            return False

        try:
            self._app = pywinauto.Application().start(str(self._exe_path), timeout=timeout)
            time.sleep(3.0)
            self._main_window = self._find_window()
            if self._main_window is None:
                logger.error("找不到 CardLock 主窗口")
                return False
            return True
        except Exception as e:
            logger.error("启动失败: %s", e)
            return False

    def _find_window(self) -> Any:
        if self._app is None:
            return None
        titles = ["CardLock", "智能门锁管理系统", "智能门锁", "门锁管理系统",
                   "酒店门锁", "Card Lock", "LockCard"]
        for title in titles:
            try:
                w = self._app.window(title=title)
                if w.exists():
                    return w
            except Exception:
                continue
        try:
            for w in self._app.windows():
                try:
                    if any(kw in (w.window_text() or "") for kw in
                           ("CardLock", "门锁", "Card", "Lock")):
                        return w
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def close_app(self) -> None:
        self.stop_recording()
        if self._app:
            try:
                self._app.kill()
            except Exception:
                pass
            self._app = None
            self._main_window = None

    def start_recording(self) -> None:
        self._recording = True
        self._steps = []
        self._start_time = time.monotonic()
        self._stop_event.clear()

    def stop_recording(self) -> List[PlaybackStep]:
        self._recording = False
        self._stop_event.set()
        return list(self._steps)

    def record_click(self, button_text: str, wait_sec: float = 1.0) -> None:
        if not self._recording:
            return
        self._steps.append(PlaybackStep(
            index=len(self._steps) + 1,
            action="click", target=button_text, wait_sec=wait_sec,
            description=f"点击: {button_text}",
        ))

    def record_input(self, field_name: str, value: str, wait_sec: float = 0.5) -> None:
        if not self._recording:
            return
        self._steps.append(PlaybackStep(
            index=len(self._steps) + 1,
            action="type", target=field_name, value=value, wait_sec=wait_sec,
            description=f"输入 {field_name}={value}",
        ))

    def record_select(self, field_name: str, value: str) -> None:
        if not self._recording:
            return
        self._steps.append(PlaybackStep(
            index=len(self._steps) + 1,
            action="select", target=field_name, value=value, wait_sec=0.5,
            description=f"选择 {field_name}={value}",
        ))

    def record_wait(self, seconds: float, reason: str = "") -> None:
        if not self._recording:
            return
        self._steps.append(PlaybackStep(
            index=len(self._steps) + 1,
            action="wait", value=str(seconds), wait_sec=seconds,
            description=reason or f"等待 {seconds}s",
        ))

    def detect_button_map(self) -> Dict[str, str]:
        """自动检测 CardLock.exe 界面上的卡型按钮映射。"""
        mappings: Dict[str, str] = {}
        if self._main_window is None:
            return mappings
        known = {
            "客人": "guest", "散客": "guest", "总卡": "master", "总管": "master",
            "楼栋": "building", "楼层": "floor", "层控": "floor",
            "应急": "emergency", "退房": "checkout", "挂失": "loss",
            "记录": "record", "时钟": "timeset", "组控": "group",
            "组号": "groupset", "房号": "roomset", "授权": "auth",
            "Guest": "guest", "Master": "master", "Building": "building",
            "Floor": "floor", "Emergency": "emergency",
        }
        try:
            for child in self._main_window.descendants():
                try:
                    text = child.window_text().strip()
                    if not text:
                        continue
                    cls = child.class_name() or ""
                    if cls not in ("Button", "TButton", "BitBtn"):
                        continue
                    for kw, ct in known.items():
                        if kw in text and ct not in mappings:
                            mappings[ct] = text
                            break
                except Exception:
                    continue
        except Exception:
            pass
        return mappings


# ──────────────────────────────────────────────────────────────────
# 回放器
# ──────────────────────────────────────────────────────────────────


class ParasiticReplayer:
    """pywinauto 寄生回放引擎。按工作流步骤自动操作 CardLock.exe。"""

    def __init__(self, bridge_instance=None):
        self._bridge = bridge_instance
        self._app: Any = None
        self._main_window: Any = None

    def replay(self, workflow: ParasiticWorkflow,
               exe_path: str = "",
               readback: bool = True,
               on_step: Optional[Callable[[PlaybackStep, bool], None]] = None,
               ) -> ReplayResult:
        """回放一个工作流。

        Args:
            workflow: 要回放的工作流
            exe_path: CardLock.exe 路径（如果 workflow 中没有）
            readback: 发卡后是否用 bridge 读回卡数据
            on_step: 每步回调 (step, ok)

        Returns:
            ReplayResult
        """
        import time as _time
        t0 = _time.monotonic()
        result = ReplayResult(
            workflow_name=workflow.name,
            total_steps=len(workflow.steps),
        )

        exe = exe_path or workflow.exe_path
        if not exe or not Path(exe).is_file():
            result.error = f"CardLock.exe 不存在: {exe}"
            return result

        # 启动 CardLock.exe
        try:
            import pywinauto
            self._app = pywinauto.Application().start(exe, timeout=30.0)
            _time.sleep(3.0)
            self._main_window = self._find_window()
            if self._main_window is None:
                result.error = "找不到 CardLock 主窗口"
                return result
        except Exception as e:
            result.error = f"启动失败: {e}"
            return result

        try:
            # 逐步执行
            for step in workflow.steps:
                ok = self._exec_step(step)
                result.logs.append(f"[{'OK' if ok else 'FAIL'}] {step.description}")
                if on_step:
                    try:
                        on_step(step, ok)
                    except Exception:
                        pass
                if ok:
                    result.completed_steps += 1
                else:
                    result.failed_step = step
                    if step.retry > 1:
                        # 重试
                        for retry_i in range(step.retry):
                            _time.sleep(1.0)
                            if self._exec_step(step):
                                result.completed_steps += 1
                                result.failed_step = None
                                result.logs.append(f"  [RETRY OK] {step.description}")
                                break
                            result.logs.append(f"  [RETRY FAIL #{retry_i+1}] {step.description}")
                    if result.failed_step:
                        result.error = f"步骤 {step.index} 失败: {step.description}"
                        break

                if step.wait_sec > 0:
                    _time.sleep(step.wait_sec)

            # 回读卡数据
            if readback and result.completed_steps == result.total_steps:
                result.card_hex = self._readback_card()

        finally:
            # 关闭 CardLock.exe
            try:
                self._app.kill()
            except Exception:
                pass
            self._app = None
            self._main_window = None

        result.ok = result.completed_steps == result.total_steps and not result.error
        result.duration_sec = round(_time.monotonic() - t0, 1)
        return result

    def _find_window(self) -> Any:
        if self._app is None:
            return None
        titles = ["CardLock", "智能门锁管理系统", "智能门锁", "门锁管理系统",
                   "酒店门锁", "Card Lock", "LockCard"]
        for title in titles:
            try:
                w = self._app.window(title=title)
                if w.exists():
                    return w
            except Exception:
                continue
        try:
            visible = [w for w in self._app.windows() if w.is_visible()]
            if visible:
                return visible[0]
        except Exception:
            pass
        return None

    def _exec_step(self, step: PlaybackStep) -> bool:
        if self._main_window is None:
            return False
        try:
            if step.action == "click":
                btn = self._main_window.child_window(title=step.target)
                btn.click_input()
            elif step.action == "type":
                ctrl = self._main_window.child_window(title=step.target)
                ctrl.set_edit_text(step.value)
            elif step.action == "select":
                ctrl = self._main_window.child_window(title=step.target)
                ctrl.select(step.value)
            elif step.action == "wait":
                time.sleep(float(step.value or "1.0"))
            elif step.action == "check":
                try:
                    w = self._main_window.child_window(title=step.target)
                    if not w.exists():
                        return False
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.debug("执行步骤失败: %s - %s", step.description, e)
            return False

    def _readback_card(self) -> str:
        """用 bridge 读回发卡后的数据。"""
        if self._bridge is None:
            try:
                from ..collector_bridge import get_bridge
                bridge = get_bridge()
            except Exception:
                return ""
        else:
            bridge = self._bridge

        try:
            resp = bridge.read_card(d12=1, timeout=6.0)
            if resp.get("ok"):
                return resp.get("card_hex", "") or resp.get("payload_hex", "")
        except Exception as e:
            logger.debug("回读失败: %s", e)
        return ""


# ──────────────────────────────────────────────────────────────────
# 便捷函数
# ──────────────────────────────────────────────────────────────────


def record_and_replay(
    cardlock_exe: str,
    card_type: str = "guest",
    button_map: Optional[Dict[str, str]] = None,
    readback: bool = True,
) -> ReplayResult:
    """一键录制+回放（用于简单发卡场景）。

    启动 CardLock.exe → 录制发卡操作 → 自动回放 → 读回数据。
    """
    recorder = ParasiticRecorder(cardlock_exe)
    if not recorder.start_app():
        return ReplayResult(ok=False, error="无法启动 CardLock.exe")

    # 自动检测按钮映射
    detected = recorder.detect_button_map()
    bm = button_map or detected

    # 录制发卡工作流
    recorder.start_recording()
    btn_text = bm.get(card_type, "")
    if not btn_text:
        recorder.close_app()
        return ReplayResult(ok=False, error=f"找不到 {card_type} 按钮")

    recorder.record_click(btn_text, wait_sec=2.0)
    recorder.record_wait(1.0, "等待发卡完成")
    steps = recorder.stop_recording()
    recorder.close_app()

    # 回放
    workflow = ParasiticWorkflow(
        name=f"auto_{card_type}",
        card_type=card_type,
        exe_path=cardlock_exe,
        button_map=bm,
        steps=steps,
    )
    replayer = ParasiticReplayer()
    return replayer.replay(workflow, cardlock_exe, readback=readback)


def load_workflow(filepath: str) -> ParasiticWorkflow:
    """从 JSON 文件加载工作流。"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    wf = ParasiticWorkflow(
        name=data.get("name", ""),
        card_type=data.get("card_type", ""),
        exe_path=data.get("exe_path", ""),
        button_map=data.get("button_map", {}),
        created_at=data.get("created_at", ""),
        source=data.get("source", "imported"),
        metadata=data.get("metadata", {}),
    )
    for s in data.get("steps", []):
        wf.steps.append(PlaybackStep(
            index=s.get("index", 0), action=s.get("action", ""),
            target=s.get("target", ""), value=s.get("value", ""),
            wait_sec=s.get("wait_sec", 0.0), description=s.get("description", ""),
            condition=s.get("condition", ""), retry=s.get("retry", 1),
        ))
    return wf


def save_workflow(workflow: ParasiticWorkflow, filepath: str) -> None:
    """保存工作流为 JSON 文件。"""
    data = {
        "name": workflow.name,
        "card_type": workflow.card_type,
        "exe_path": workflow.exe_path,
        "button_map": workflow.button_map,
        "created_at": workflow.created_at or time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": workflow.source,
        "metadata": workflow.metadata,
        "steps": [],
    }
    for s in workflow.steps:
        data["steps"].append({
            "index": s.index, "action": s.action, "target": s.target,
            "value": s.value, "wait_sec": s.wait_sec,
            "description": s.description, "condition": s.condition, "retry": s.retry,
        })
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
