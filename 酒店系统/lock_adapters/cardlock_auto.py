"""
cardlock_auto.py — pywinauto CardLock.exe 寄生发卡适配器

路线
====
方向 A（寄生套壳）：不逆向 DLL，不碰固件。Solid PMS 后台静默运行
CardLock.exe，用 pywinauto 按原来的按钮发卡，发完后读回 payload
给 Solid 完成制卡流程。

支持的品牌
==========
- proUSB V9 / V8 / V7（作为 DLL 直调路径的降级，或 DLL 不可用时）
- 任何有标准 CardLock.exe 界面的门锁品牌（需提供 button_map）

使用
====
    from lock_adapters.cardlock_auto import CardLockAutoAdapter

    ad = CardLockAutoAdapter(install_dir)
    ad.configure(dlsCoID=2826423)
    ad.initialize()                       # 启动 CardLock.exe
    res = ad.issue_guest_card(...)        # pywinauto 点按钮 → 发卡 → 读回
    ad.close()                            # 关闭 CardLock.exe

依赖
====
- pywinauto >= 0.6.8
- CardLock.exe 可执行（需在 install_dir 下）
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .base import CardResult, LockAdapter

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# 默认卡型 → 按钮名映射（proUSB V9 CardLock.exe 中文界面）
# 不同的品牌/版本可能有不同的按钮文字，通过 configure 覆盖。
# ──────────────────────────────────────────────────────────────────

DEFAULT_BUTTON_MAP: Dict[str, str] = {
    "guest":       "散客客人卡",
    "checkout":    "退房卡",
    "loss":        "挂失卡",
    "record":      "记录卡",
    "roomset":     "房号设置卡",
    "timeset":     "时钟设置卡",
    "groupset":    "组号设置卡",
    "master":      "总卡",
    "building":    "楼栋卡",
    "floor":       "层控卡",
    "emergency":   "应急卡",
    "group":       "组控卡",
    "auth":        "授权卡",
    "blank":       "空白卡",  # CardLock.exe 通常不直接提供，走 CardErase
}


class CardLockAutoError(RuntimeError):
    """pywinauto 控制 CardLock.exe 失败。"""


# ──────────────────────────────────────────────────────────────────
# CardLockAutoController — pywinauto 会话管理 + 按钮自动化核心
# ──────────────────────────────────────────────────────────────────

class CardLockAutoController:
    """管理单个 CardLock.exe pywinauto 会话。

    核心职责：
    1. 启动 / 关闭 CardLock.exe 进程
    2. 查找主窗口，查找按钮
    3. 点击按钮，等待操作完成
    4. 处理弹窗（错误 / 确认 / 进度提示）
    """

    # 已知的 CardLock 窗口标题模式（按优先级）
    KNOWN_WINDOW_TITLES = [
        "CardLock",
        "智能门锁管理系统",
        "智能门锁",
        "门锁管理系统",
        "酒店门锁",
        "Card Lock",
        "LockCard",
        "CardLock.*",
    ]

    # 发多少秒等 CardLock.exe 启动完成
    STARTUP_TIMEOUT = 30.0
    # 点完按钮后最多等多久操作完成
    ACTION_TIMEOUT = 60.0
    # 按钮点击后轮询间隔
    POLL_INTERVAL = 0.5

    def __init__(self, exe_path: Path):
        self._exe_path = Path(exe_path)
        if not self._exe_path.is_file():
            raise CardLockAutoError(f"CardLock.exe not found: {self._exe_path}")
        self._app: Any = None
        self._main_window: Any = None
        self._button_map: Dict[str, str] = dict(DEFAULT_BUTTON_MAP)
        self._pid: Optional[int] = None

    # ── 生命周期 ──────────────────────────────────────────────────

    def start(self) -> bool:
        """启动 CardLock.exe 并通过 pywinauto 连接。"""
        try:
            import pywinauto
        except ImportError:
            raise CardLockAutoError(
                "pywinauto 未安装。请运行: pip install pywinauto"
            )

        if self._app is not None and self.is_running():
            return True

        logger.info("启动 CardLock.exe: %s", self._exe_path)
        try:
            # Application.start 返回 Application 对象
            app_cls = pywinauto.Application  # type: ignore
            self._app = app_cls().start(str(self._exe_path), timeout=self.STARTUP_TIMEOUT)
        except Exception as e:
            raise CardLockAutoError(f"无法启动 CardLock.exe: {e}")

        time.sleep(2.0)

        # 连接到已启动的窗口
        main = self._find_main_window()
        if main is None:
            # 也许窗口标题不是已知的，尝试 blind connect
            try:
                self._app.connect(path=str(self._exe_path), timeout=5)
                main = self._find_main_window()
            except Exception:
                pass

        if main is None:
            raise CardLockAutoError(
                f"CardLock.exe 已启动但找不到主窗口。"
                f"已知标题: {self.KNOWN_WINDOW_TITLES}"
            )

        self._main_window = main
        self._main_window.set_focus()
        logger.info("CardLock.exe 连接成功 (PID=%s)", self._get_pid())
        return True

    def _get_pid(self) -> Optional[int]:
        try:
            return self._app.process
        except Exception:
            return None

    def _find_main_window(self) -> Any:
        """在已连接的 app 中查找 CardLock 主窗口。"""
        if self._app is None:
            return None

        # 方法 1：按已知标题匹配
        for title in self.KNOWN_WINDOW_TITLES:
            try:
                w = self._app.window(title=title)
                if w.exists():
                    return w
            except Exception:
                continue

        # 方法 2：遍历顶层窗口找匹配标题
        try:
            for w in self._app.windows():
                try:
                    text = w.window_text()
                    if any(kw in text for kw in ("CardLock", "门锁", "Card", "Lock")):
                        return w
                except Exception:
                    continue
        except Exception:
            pass

        # 方法 3：拿第一个可见的顶层窗口
        try:
            tops = [w for w in self._app.windows() if w.is_visible()]
            if tops:
                return tops[0]
        except Exception:
            pass

        return None

    def stop(self) -> None:
        """关闭 CardLock.exe。"""
        if self._app is not None:
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

    # ── 按钮映射配置 ───────────────────────────────────────────────

    def set_button_map(self, mapping: Dict[str, str]) -> None:
        """覆盖卡型 → 按钮名映射。"""
        self._button_map = dict(mapping)

    def get_button_name(self, card_type: str) -> str:
        """逻辑卡型名 → CardLock.exe 上的按钮文字。"""
        name = self._button_map.get(card_type)
        if not name:
            raise CardLockAutoError(
                f"未知卡型 '{card_type}'，button_map={self._button_map}"
            )
        return name

    # ── 按钮查找与点击 ─────────────────────────────────────────────

    def _find_button(self, text: str) -> Any:
        """在 CardLock 主窗口中寻找文字匹配的按钮。

        Delphi 应用通常用 win32 backend，按钮是 "Button" class。
        尝试多种策略：
        1. 精确文本匹配
        2. 模糊文本匹配 (best_match)
        3. 遍历所有 Button 控件匹配
        """
        win = self._main_window
        if win is None:
            raise CardLockAutoError("CardLock 主窗口未连接")

        strategies: List[Callable[[], Any]] = [
            # 策略 1：wanted_text 精确匹配
            lambda: win.child_window(title=text, control_type="Button"),
            # 策略 2：best_match 模糊匹配
            lambda: win.child_window(best_match=text),
            # 策略 3：标题包含匹配
            lambda: win.child_window(title_re=f".*{text}.*", control_type="Button"),
        ]

        for strategy in strategies:
            try:
                btn = strategy()
                if btn.exists():
                    return btn
            except Exception:
                continue

        # 策略 4：暴力遍历所有可见按钮
        try:
            for child in win.children():
                try:
                    child_text = child.window_text()
                    if text in child_text:
                        return child
                except Exception:
                    continue
        except Exception:
            pass

        raise CardLockAutoError(
            f"在 CardLock 窗口中找不到按钮: '{text}'。\n"
            f"请确认 CardLock.exe 界面和 button_map 的一致性。\n"
            f"当前主窗口标题: {self._safe_window_title()}"
        )

    def _safe_window_title(self) -> str:
        try:
            return self._main_window.window_text() if self._main_window else "(无窗口)"
        except Exception:
            return "(读取失败)"

    def click_button(self, text: str) -> bool:
        """找到并点击 CardLock.exe 中的按钮。返回是否成功。"""
        btn = self._find_button(text)
        if btn is None:
            return False

        logger.info("点击按钮: %s", text)
        try:
            btn.click_input()
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.error("点击按钮 '%s' 失败: %s", text, e)
            return False

    # ── 等待操作完成 ───────────────────────────────────────────────

    def wait_for_action_complete(self, timeout: float = 0) -> float:
        """等待 CardLock.exe 完成当前操作。

        策略：等待进度/状态窗口消失，或等待主窗口重新可操作。
        返回实际等待秒数。
        """
        timeout = timeout or self.ACTION_TIMEOUT
        deadline = time.monotonic() + timeout

        # CardLock.exe 的"发卡完成"信号：
        # 1. 弹出的小窗口消失（"请放卡" / "写卡成功" 等）
        # 2. 主窗口恢复焦点
        # 3. 进度条 / 状态文本消失

        # 先给一个固定等待让 Delphi 消化点击
        time.sleep(1.0)

        while time.monotonic() < deadline:
            # 检查是否有弹窗
            popup = self._find_popup()
            if popup:
                self._dismiss_popup(popup)
                time.sleep(0.3)
                continue

            # 检查是否有进度指示器
            if self._has_progress_indicator():
                time.sleep(self.POLL_INTERVAL)
                continue

            # 无弹窗、无进度 → 认为完成
            break

        elapsed = time.monotonic() - (deadline - timeout)
        return elapsed

    def _find_popup(self) -> Any:
        """查找可能正在显示的弹窗。"""
        popup_titles = [
            "提示", "确认", "警告", "错误", "信息",
            "Please", "Confirm", "Warning", "Error",
            "请放卡", "写卡", "读卡", "发卡",
            "成功", "失败", "操作", "系统",
        ]
        try:
            for w in self._app.windows():
                try:
                    title = w.window_text()
                    if any(t in title for t in popup_titles):
                        return w
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _dismiss_popup(self, popup: Any) -> None:
        """尝试关闭弹窗（点确定/是/OK）。"""
        try:
            # 先试点"确定"
            for btn_text in ("确定", "是(&Y)", "OK", "Yes", "确认", "关闭"):
                try:
                    btn = popup.child_window(title=btn_text, control_type="Button")
                    if btn.exists():
                        btn.click_input()
                        return
                except Exception:
                    continue
            # 兜底：关掉窗口
            popup.close()
        except Exception:
            pass

    def _has_progress_indicator(self) -> bool:
        """检查是否有进度指示器在显示。"""
        try:
            for child in self._main_window.children():
                try:
                    cls = child.class_name()
                    if cls in ("msctls_progress32", "ProgressBar", "Static"):
                        text = child.window_text()
                        if any(kw in text for kw in ("正在", "请稍", "Loading", "处理")):
                            return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    # ── 错误诊断 ───────────────────────────────────────────────────

    def dump_window_tree(self) -> str:
        """打印当前窗口树，用于调试。"""
        try:
            return self._main_window.print_control_identifiers()
        except Exception as e:
            return f"无法打印窗口树: {e}"


# ──────────────────────────────────────────────────────────────────
# CardLockAutoAdapter — LockAdapter 实现
# ──────────────────────────────────────────────────────────────────

class CardLockAutoAdapter(LockAdapter):
    """pywinauto 寄生发卡适配器。

    特征检测：install_dir 下存在 CardLock.exe。
    通过 pywinauto 自动化 CardLock.exe 完成发卡，发完后用桥接读回验证。
    不做任何 DLL 逆向或固件修改。
    """

    brand = "CardLockAuto"
    version_hint = "pywinauto"

    # 用于 detect 的关键文件特征
    REQUIRED_FILES = ("CardLock.exe",)

    def __init__(self, install_dir: Path):
        super().__init__(install_dir)
        self._exe_path = self.install_dir / "CardLock.exe"
        self._controller: Optional[CardLockAutoController] = None
        self._dlsCoID: int = 0
        self._hotel_id: str = ""
        self._pc_id: str = ""
        self._reader_adapter: Any = None  # 用来读卡回验证的 V9/桥接适配器

    # ──────────────────────────────────────────────────────────────
    # 识别
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def detect(cls, install_dir: Path) -> Optional["CardLockAutoAdapter"]:
        install_dir = Path(install_dir)
        if not install_dir.is_dir():
            return None
        exe = install_dir / "CardLock.exe"
        if not exe.is_file():
            return None
        return cls(install_dir)

    # ──────────────────────────────────────────────────────────────
    # 配置
    # ──────────────────────────────────────────────────────────────

    def configure(
        self,
        *,
        dlsCoID: Optional[int] = None,
        hotel_id: Optional[str] = None,
        pc_id: Optional[str] = None,
        button_map: Optional[Dict[str, str]] = None,
        reader_adapter: Any = None,
        **kwargs: Any,
    ) -> None:
        if dlsCoID is not None:
            self._dlsCoID = int(dlsCoID)
        if hotel_id is not None:
            self._hotel_id = str(hotel_id)
        if pc_id is not None:
            self._pc_id = str(pc_id)
        if button_map is not None:
            if self._controller:
                self._controller.set_button_map(button_map)
            self._button_map_override = button_map
        if reader_adapter is not None:
            self._reader_adapter = reader_adapter

    @property
    def dlsCoID(self) -> int:
        return self._dlsCoID

    def auto_configure(self) -> bool:
        """从 PMS 数据库自动读取法医级配置，填充 dlsCoID / button_map 等。

        读取的键（由 vendor_console_tab._lk_import_collected 写入）：
        - lock_takeover_dlsCoID        → dlsCoID
        - lock_takeover_hotel_id       → hotel_id
        - lock_takeover_pc_id          → pc_id
        - cardlockauto_button_map      → button_map (JSON)
        - cardlockauto_workflow        → 工作流 (JSON, 备用)
        - cardlockauto_install_dir     → 安装目录（已由 __init__ 传入）

        返回 True 表示至少读到了 dlsCoID（发卡必需）。
        """
        try:
            from database import db
        except ImportError:
            logger.warning("数据库不可用，跳过自动配置")
            return False

        dls = db.get_config("lock_takeover_dlsCoID")
        if dls:
            self._dlsCoID = int(dls)

        hid = db.get_config("lock_takeover_hotel_id")
        if hid:
            self._hotel_id = str(hid)

        pid = db.get_config("lock_takeover_pc_id")
        if pid:
            self._pc_id = str(pid)

        btn_map_json = db.get_config("cardlockauto_button_map")
        if btn_map_json:
            try:
                import json
                btn_map = json.loads(btn_map_json)
                if isinstance(btn_map, dict) and btn_map:
                    self._button_map_override = btn_map
                    if self._controller:
                        self._controller.set_button_map(btn_map)
                    logger.info("自动加载按钮映射: %d 键", len(btn_map))
            except Exception as e:
                logger.warning("按钮映射解析失败: %s", e)

        return bool(self._dlsCoID)

    # ──────────────────────────────────────────────────────────────
    # 生命周期
    # ──────────────────────────────────────────────────────────────

    def initialize(self) -> bool:
        """启动 CardLock.exe 并通过 pywinauto 连接。"""
        try:
            self._controller = CardLockAutoController(self._exe_path)
            if hasattr(self, '_button_map_override'):
                self._controller.set_button_map(self._button_map_override)
            self._controller.start()
            self._opened = True
            logger.info("CardLockAutoAdapter 初始化完成")
            return True
        except CardLockAutoError as e:
            logger.error("CardLockAutoAdapter 初始化失败: %s", e)
            self._opened = False
            return False
        except Exception as e:
            logger.error("CardLockAutoAdapter 初始化异常: %s", e, exc_info=True)
            self._opened = False
            return False

    def close(self) -> None:
        """关闭 CardLock.exe。"""
        if self._controller:
            try:
                self._controller.stop()
            except Exception:
                pass
            self._controller = None
        self._opened = False

    @property
    def is_open(self) -> bool:
        return self._opened and (self._controller is not None) and self._controller.is_running()

    # ──────────────────────────────────────────────────────────────
    # 信息查询
    # ──────────────────────────────────────────────────────────────

    def get_version(self) -> str:
        return f"CardLockAuto (pywinauto) @ {self._exe_path}"

    def get_supported_card_types(self) -> list[str]:
        if self._controller:
            return list(self._controller._button_map.keys())
        return list(DEFAULT_BUTTON_MAP.keys())

    # ──────────────────────────────────────────────────────────────
    # 核心发卡流程
    # ──────────────────────────────────────────────────────────────

    def _ensure_ready(self) -> Optional[CardResult]:
        if not self.is_open:
            ok = self.initialize()
            if not ok:
                return CardResult.fail("CardLock.exe 无法启动或连接")
        if not self._dlsCoID:
            return CardResult.fail("缺少 dlsCoID 配置")
        return None

    def _issue_card_by_button(
        self,
        card_type: str,
        *,
        pre_click_params: Optional[Dict[str, Any]] = None,
        post_verify: bool = True,
    ) -> CardResult:
        """通用发卡：点按钮 → 等待完成 → 读回验证。

        Args:
            card_type: 逻辑卡型名（"guest", "master", ...）
            pre_click_params: 点按钮前需要设置的参数（如房号、日期）
                CardLock.exe 的 UI 中这些参数通常在弹出框中输入。
                暂时不支持自动填写 → 需要人工介入，或留到品牌适配时解决。
            post_verify: 是否在写完后读卡验证
        """
        err = self._ensure_ready()
        if err is not None:
            return err

        ctrl = self._controller
        btn_name = ctrl.get_button_name(card_type)

        # 1. 读卡前 payload（用于对比）
        before = self._read_card_payload_via_bridge()

        # 2. 点击按钮
        if not ctrl.click_button(btn_name):
            return CardResult.fail(f"无法点击按钮 '{btn_name}'。窗口树: {ctrl.dump_window_tree()}")

        # 3. 等待 CardLock.exe 完成写卡
        elapsed = ctrl.wait_for_action_complete()
        logger.info("CardLock.exe '%s' 操作完成，等待 %.1f 秒", btn_name, elapsed)

        # 4. 读回验证
        if not post_verify:
            return CardResult.ok(before or "", raw_ret=0)

        after = self._read_card_payload_via_bridge()
        if after and after != before:
            return CardResult.ok(after, raw_ret=0)
        elif after:
            return CardResult.ok(after, raw_ret=0)  # 即使没变化也返回（可能同类型卡）
        else:
            return CardResult.fail(
                f"CardLock.exe '{btn_name}' 点击完成但无法读卡。\n"
                "请确认发卡器上已放卡，并且通过 bridge 可读。"
            )

    def _read_card_payload_via_bridge(self) -> Optional[str]:
        """通过桥接/读卡适配器读当前卡上的 payload。"""
        # 优先用注入的 reader_adapter（如 ProUsbV9Adapter 实例）
        if self._reader_adapter is not None:
            try:
                return self._reader_adapter.read_card_payload()
            except Exception:
                pass

        # 尝试用全局桥接直接读
        try:
            from .bridge_client import get_bridge
            bridge = get_bridge()
            if bridge.dll_loaded and bridge.is_running():
                resp = bridge.read_card(d12=1, timeout=3.0)
                if resp.get("ok") and int(resp.get("ret", -1)) == 0:
                    out = resp.get("out") or {}
                    if out.get("has_card"):
                        return out.get("payload") or None
        except Exception:
            pass

        return None

    # ──────────────────────────────────────────────────────────────
    # 14 种卡实现
    # ──────────────────────────────────────────────────────────────

    def issue_guest_card(
        self,
        lock_no: str,
        b_date: str,
        e_date: str,
        card_no: int = 1,
        llock: bool = True,
        pdoors: bool = False,
        dai: int = 0,
    ) -> CardResult:
        """发客人卡 — 点 CardLock.exe 的「散客客人卡」按钮。

        注意：CardLock.exe 发客人卡时，房号/日期等参数通过弹窗输入。
        pywinauto 目前的实现只负责点按钮，参数输入需要人工干预或进一步
        的 UI 自动化（定位弹窗中的输入框并填写）。

        生产环境中建议：
        1. 优先使用 ProUsbV9Adapter 直调 DLL（已有完整支持）
        2. 本适配器用于品牌无 DLL 接口时的降级
        """
        logger.info(
            "CardLockAuto issue_guest_card: lock_no=%s, b=%s, e=%s, card_no=%d",
            lock_no, b_date, e_date, card_no,
        )
        # TODO: 在 CardLock.exe 弹窗中自动填写房号、日期
        # 当前实现：点击按钮后需要操作人在 CardLock.exe 中完成输入
        return self._issue_card_by_button("guest")

    def issue_master_card(
        self, *, b_date: str = "", e_date: str = "", card_no: int = 1, **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("master")

    def issue_building_card(
        self, *, b_date: str = "", e_date: str = "", building_no: int = 1, **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("building")

    def issue_floor_card(
        self, *, b_date: str = "", e_date: str = "",
        building_no: int = 1, floor_no: int = 3, **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("floor")

    def issue_emergency_card(
        self, *, b_date: str = "", e_date: str = "", card_no: int = 1, **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("emergency")

    def issue_clock_card(
        self, *, b_date: str = "", card_no: int = 1, **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("timeset")

    def issue_room_no_card(
        self, *, lock_no: str = "", b_date: str = "", **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("roomset")

    def issue_loss_report_card(
        self, *, l_card_no: str = "", b_date: str = "", **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("loss")

    def issue_record_card(
        self, *, b_date: str = "", card_no: int = 1, **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("record")

    def issue_check_out_card(
        self, *, b_date: str = "", card_no: int = 1, **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("checkout")

    def issue_auth_card(self, *, b_date: str = "", **kwargs) -> CardResult:
        return self._issue_card_by_button("auth")

    def issue_group_card(
        self, *, b_date: str = "", e_date: str = "",
        group_no: int = 1, **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("group")

    def issue_group_set_card(
        self, *, b_date: str = "", e_date: str = "",
        group_no: int = 1, **kwargs
    ) -> CardResult:
        return self._issue_card_by_button("groupset")

    def erase_card(self, card_hex: str = "") -> CardResult:
        """擦卡 — 大多数 CardLock.exe 没有独立的擦卡按钮。
        通过 V9 桥接的 CardErase 实现。
        """
        try:
            from .bridge_client import get_bridge
            bridge = get_bridge()
            if bridge.dll_loaded and bridge.is_running():
                resp = bridge.card_erase(d12=1, dlsCoID=self._dlsCoID, card_hex=card_hex or "")
                if resp.get("ok") and int(resp.get("ret", -1)) == 0:
                    return CardResult.ok(card_hex, raw_ret=0)
                return CardResult.fail(
                    f"CardErase 失败 (ret={resp.get('ret')})",
                    raw_ret=int(resp.get("ret", -1)),
                )
        except Exception as e:
            pass
        return CardResult.fail(f"擦卡不可用: 需要 bridge 已加载 DLL")

    def buzzer(self, ms: int = 200) -> bool:
        try:
            from .bridge_client import get_bridge
            bridge = get_bridge()
            resp = bridge.buzzer(d12=1, t=int(ms / 10))
            return bool(resp.get("ok") and int(resp.get("ret", -1)) == 0)
        except Exception:
            return False

    def read_card_raw(self) -> Optional[str]:
        return self._read_card_payload_via_bridge()

    # ──────────────────────────────────────────────────────────────
    # 静态工具
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def scan_for_cardlock_exes(search_root: Path) -> List[Path]:
        """在指定目录下递归搜索所有 CardLock.exe。"""
        results = []
        try:
            for root, dirs, files in os.walk(str(search_root)):
                for f in files:
                    if f.lower() == "cardlock.exe":
                        results.append(Path(root) / f)
        except Exception:
            pass
        return results


# ──────────────────────────────────────────────────────────────────
# 一键接管入口
# ──────────────────────────────────────────────────────────────────

def auto_takeover(
    install_dir: Path,
    *,
    dlsCoID: int = 0,
    hotel_id: str = "",
    reader_adapter: Any = None,
    button_map: Optional[Dict[str, str]] = None,
) -> Optional[CardLockAutoAdapter]:
    """一键接管：检测 → 配置 → 初始化。

    返回就绪的 CardLockAutoAdapter，失败返回 None。
    调用方应检查 adapter.is_open 后再发卡。

    可以传入一个已初始化的 ProUsbV9Adapter 作为 reader_adapter，
    发卡完成后用 V9 桥接读回验证。
    """
    ad = CardLockAutoAdapter.detect(install_dir)
    if ad is None:
        logger.warning("目录 %s 中未找到 CardLock.exe，跳过 pywinauto 接管", install_dir)
        return None

    ad.configure(
        dlsCoID=dlsCoID,
        hotel_id=hotel_id,
        reader_adapter=reader_adapter,
        button_map=button_map,
    )

    if not ad.initialize():
        logger.error("CardLockAutoAdapter 初始化失败")
        return None

    logger.info(
        "pywinauto 接管成功: %s (dlsCoID=%d, buttons=%d)",
        install_dir, dlsCoID, len(ad._controller._button_map) if ad._controller else 0,
    )
    return ad
