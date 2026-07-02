"""
ui_workflow.py — UI 地图 + 工作流录制引擎

职责：
1. pywinauto 遍历 CardLock.exe 窗口控件树，生成 UI Map
2. 录制人工发卡操作步骤（点击/输入/等待），生成可回放的工作流 JSON
3. 支持录制客人卡和管理卡两种流程

用法：
    from collector.ui_workflow import UIRecorder
    recorder = UIRecorder(cardlock_exe_path="D:\\CardLock\\CardLock.exe")
    recorder.start()                    # 启动 CardLock.exe + 连接 pywinauto
    ui_map = recorder.capture_ui_map()  # 遍历控件树
    recorder.start_recording()          # 开始录制
    # ... 操作人在 CardLock.exe 里发卡 ...
    workflow = recorder.stop_recording()  # 结束录制
    recorder.close()
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .forensic_schema import (
    UIElement, DialogStructure, UIMapReport,
    WorkflowStep, CardIssueWorkflow, WorkflowReport,
)

logger = logging.getLogger(__name__)


class UIRecorder:
    """pywinauto UI 地图采集 + 工作流录制器。"""

    def __init__(self, cardlock_exe_path: str):
        self._exe_path = Path(cardlock_exe_path)
        self._app: Any = None
        self._main_window: Any = None
        self._recording: bool = False
        self._recorded_steps: list[WorkflowStep] = []
        self._record_start_time: float = 0.0
        self._last_action_time: float = 0.0
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()

    # ── 生命周期 ──────────────────────────────────────────

    def start(self, timeout: float = 30.0) -> bool:
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
            self._app = pywinauto.Application().start(
                str(self._exe_path), timeout=timeout
            )
            time.sleep(3.0)
            self._main_window = self._find_window()
            if self._main_window is None:
                logger.error("找不到 CardLock 主窗口")
                return False
            logger.info("CardLock.exe 连接成功")
            return True
        except Exception as e:
            logger.error("启动 CardLock.exe 失败: %s", e)
            return False

    def close(self):
        self.stop_recording()
        if self._app:
            try:
                self._app.kill()
            except Exception:
                pass
            self._app = None
            self._main_window = None

    def is_running(self) -> bool:
        if self._app is None:
            return False
        try:
            return self._app.is_process_running()
        except Exception:
            return False

    # ── UI Map ────────────────────────────────────────────

    def capture_ui_map(self) -> UIMapReport:
        """遍历 CardLock.exe 窗口树，生成 UI Map。"""
        report = UIMapReport()
        if self._main_window is None:
            return report

        try:
            report.main_window_title = self._main_window.window_text()
        except Exception:
            report.main_window_title = "(未知)"

        # 采集主窗口控件树
        root = self._traverse_control(self._main_window)
        if root:
            report.control_tree_json = json.dumps(
                root, ensure_ascii=False, indent=2, default=str
            )

        # 采集卡型按钮映射
        report.card_type_buttons = self._detect_card_type_buttons()

        # 采集弹窗结构（发客人卡时）
        guest_dialog = self._probe_guest_dialog()
        if guest_dialog:
            report.dialog_structures["guest_card_issue"] = guest_dialog

        logger.info("UI Map 采集完成: %d 按钮, %d 弹窗结构",
                     len(report.card_type_buttons),
                     len(report.dialog_structures))
        return report

    def _find_window(self) -> Any:
        if self._app is None:
            return None
        known_titles = [
            "CardLock", "智能门锁管理系统", "智能门锁",
            "门锁管理系统", "酒店门锁", "Card Lock", "LockCard",
        ]
        for title in known_titles:
            try:
                w = self._app.window(title=title)
                if w.exists():
                    return w
            except Exception:
                continue

        try:
            for w in self._app.windows():
                try:
                    text = w.window_text()
                    if any(kw in text for kw in
                           ("CardLock", "门锁", "Card", "Lock")):
                        return w
                except Exception:
                    continue
        except Exception:
            pass

        try:
            visible = [w for w in self._app.windows() if w.is_visible()]
            if visible:
                return visible[0]
        except Exception:
            pass
        return None

    def _traverse_control(self, control: Any, depth: int = 0) -> Optional[dict]:
        if depth > 10:
            return None
        try:
            el = {
                "control_type": control.class_name() or "Unknown",
                "text": control.window_text() or "",
                "automation_id": "",
                "visible": control.is_visible(),
                "enabled": control.is_enabled(),
                "children": [],
            }
            try:
                r = control.rectangle()
                el["rect"] = {
                    "left": r.left, "top": r.top,
                    "right": r.right, "bottom": r.bottom,
                }
            except Exception:
                el["rect"] = {}

            for child in control.children():
                child_el = self._traverse_control(child, depth + 1)
                if child_el:
                    el["children"].append(child_el)
            return el
        except Exception:
            return None

    def _detect_card_type_buttons(self) -> dict[str, str]:
        """自动检测 CardLock.exe 界面上的卡型按钮。"""
        mappings: dict[str, str] = {}
        known = {
            "客人": "guest", "散客": "guest",
            "总卡": "master", "总管": "master",
            "楼栋": "building", "楼层": "floor",
            "层控": "floor", "应急": "emergency",
            "退房": "checkout", "挂失": "loss",
            "记录": "record", "时钟": "timeset",
            "组控": "group", "组号": "groupset",
            "房号": "roomset", "授权": "auth",
            "空白": "blank",
        }
        try:
            for child in self._main_window.descendants():
                try:
                    text = child.window_text().strip()
                    if not text:
                        continue
                    cls = child.class_name()
                    if cls not in ("Button", "TButton", "BitBtn"):
                        continue
                    for kw, card_type in known.items():
                        if kw in text and card_type not in mappings:
                            mappings[card_type] = text
                            break
                except Exception:
                    continue
        except Exception:
            pass
        return mappings

    def _probe_guest_dialog(self) -> Optional[DialogStructure]:
        """尝试触发客人卡弹窗并采集结构（非破坏性探测）。"""
        structure = DialogStructure()
        try:
            btn_text = None
            for child in self._main_window.descendants():
                try:
                    t = child.window_text()
                    if "客人" in t or "散客" in t:
                        btn_text = t
                        break
                except Exception:
                    continue
            if not btn_text:
                return None

            # 点按钮触发弹窗
            self._main_window.child_window(title=btn_text).click_input()
            time.sleep(1.0)

            # 查找弹窗
            popup = None
            for w in self._app.windows():
                try:
                    if w.window_text() and w.window_text() != self._main_window.window_text():
                        popup = w
                        break
                except Exception:
                    continue

            if popup:
                structure.title = popup.window_text() or ""
                for child in popup.children():
                    try:
                        el = {
                            "control_type": child.class_name(),
                            "text": child.window_text() or "",
                            "visible": child.is_visible(),
                            "enabled": child.is_enabled(),
                        }
                        structure.elements.append(el)
                    except Exception:
                        continue

                # 关闭弹窗
                try:
                    popup.close()
                except Exception:
                    pass
        except Exception as e:
            logger.debug("探测弹窗失败: %s", e)
        return structure

    # ── 工作流录制 ────────────────────────────────────────

    def start_recording(self, card_type: str = ""):
        """开始录制工作流。"""
        self._recording = True
        self._recorded_steps = []
        self._record_start_time = time.monotonic()
        self._last_action_time = self._record_start_time
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self._monitor_thread.start()
        logger.info("开始录制工作流 (card_type=%s)", card_type)

    def stop_recording(self) -> CardIssueWorkflow:
        """停止录制，返回工作流。"""
        self._recording = False
        self._stop_monitor.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)

        duration = time.monotonic() - self._record_start_time
        workflow = CardIssueWorkflow(
            card_type="",
            steps=list(self._recorded_steps),
            duration_sec=round(duration, 1),
            result="unknown",
        )
        logger.info("工作流录制完成: %d 步, %.1f 秒",
                     len(workflow.steps), duration)
        return workflow

    def _monitor_loop(self):
        """后台线程：监视 CardLock.exe 界面变化，自动记录操作。"""
        prev_children: set[str] = set()
        while not self._stop_monitor.is_set():
            time.sleep(0.5)
            if not self._recording:
                continue

            try:
                if not self.is_running():
                    self._recorded_steps.append(WorkflowStep(
                        index=len(self._recorded_steps) + 1,
                        action="check",
                        target="",
                        value="",
                        description="CardLock.exe 已退出",
                    ))
                    break

                # 获取当前可见控件快照
                current_children = self._get_visible_control_texts()

                # 检测新弹窗
                new_windows = current_children - prev_children
                if new_windows:
                    for w_text in new_windows:
                        if w_text.strip():
                            self._recorded_steps.append(WorkflowStep(
                                index=len(self._recorded_steps) + 1,
                                action="check",
                                target="",
                                value=w_text,
                                wait_sec=0.0,
                                description=f"弹窗出现: {w_text}",
                            ))

                # 检测窗口消失
                disappeared = prev_children - current_children
                if disappeared:
                    for d_text in disappeared:
                        if d_text.strip():
                            self._recorded_steps[-1].wait_sec = round(
                                time.monotonic() - self._last_action_time, 1
                            )

                prev_children = current_children
                self._last_action_time = time.monotonic()

            except Exception:
                continue

    def _get_visible_control_texts(self) -> set[str]:
        texts: set[str] = set()
        try:
            if self._app:
                for w in self._app.windows():
                    try:
                        if w.is_visible():
                            t = w.window_text()
                            if t:
                                texts.add(t)
                    except Exception:
                        continue
        except Exception:
            pass
        return texts

    # ── 手动记录步骤 ──────────────────────────────────────

    def record_click(self, button_text: str):
        """手动记录一次点击。"""
        if not self._recording:
            return
        self._recorded_steps.append(WorkflowStep(
            index=len(self._recorded_steps) + 1,
            action="click",
            target=button_text,
            value="",
            wait_sec=0.0,
            description=f"点击按钮: {button_text}",
        ))
        self._last_action_time = time.monotonic()

    def record_input(self, field_name: str, value: str):
        """手动记录一次输入。"""
        if not self._recording:
            return
        self._recorded_steps.append(WorkflowStep(
            index=len(self._recorded_steps) + 1,
            action="type",
            target=field_name,
            value=value,
            wait_sec=0.0,
            description=f"在 {field_name} 输入: {value}",
        ))
        self._last_action_time = time.monotonic()

    def record_select(self, field_name: str, value: str):
        """手动记录一次选择。"""
        if not self._recording:
            return
        self._recorded_steps.append(WorkflowStep(
            index=len(self._recorded_steps) + 1,
            action="select",
            target=field_name,
            value=value,
            wait_sec=0.0,
            description=f"在 {field_name} 选择: {value}",
        ))
        self._last_action_time = time.monotonic()

    def record_wait(self, seconds: float, reason: str = ""):
        """手动记录等待。"""
        if not self._recording:
            return
        self._recorded_steps.append(WorkflowStep(
            index=len(self._recorded_steps) + 1,
            action="wait",
            target="",
            value=str(seconds),
            wait_sec=seconds,
            description=reason or f"等待 {seconds} 秒",
        ))
        self._last_action_time = time.monotonic()
