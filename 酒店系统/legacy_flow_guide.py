"""
legacy_flow_guide.py — 老系统对接顺序指引（防跳步）

前台门锁或整合台共用：记录已完成步骤，尝试乱序操作时给出明确提示。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from database import db

CONFIG_KEY_CARDLOCK = "cardlock_frontdesk_flow"
CONFIG_KEY_HUB = "legacy_takeover_hub_flow"


@dataclass(frozen=True)
class FlowStep:
    key: str
    num: int
    title: str
    short: str
    hint: str


CARDLOCK_STEPS: List[FlowStep] = [
    FlowStep(
        "preflight", 1, "① 预检环境",
        "预检",
        "确认已安装 Access 数据库引擎，且门锁数据库路径正确、可读。",
    ),
    FlowStep(
        "import", 2, "② 导入旧库",
        "导入",
        "从旧门锁数据库导入房间、在住客人、发卡历史（只读，不改原文件）。",
    ),
    FlowStep(
        "usb", 3, "③ USB 门锁迁移",
        "USB",
        "发卡器 U 盘插入本机，识别品牌并写入密钥配置。",
    ),
    FlowStep(
        "sniff", 4, "④ 发卡嗅探（可选）",
        "嗅探",
        "打开嗅探窗后按黄框一步一步来：放哪种卡 → 老系统点写卡 → 已读取 → 换下一张（客人卡必做，总卡建议做）。",
    ),
    FlowStep(
        "verify", 5, "⑤ 验收",
        "验收",
        "确认房间与密钥已就绪，再回前台正常入住→开卡。",
    ),
]

HUB_CARDLOCK_PATH_STEPS: List[FlowStep] = [
    FlowStep(
        "open_frontdesk", 1, "① 打开前台门锁对接",
        "对接向导",
        "换系统 + 智能门锁必须从此向导逐步完成，不要先点 USB 或嗅探。",
    ),
    FlowStep("preflight", 2, "② 在向导内完成预检", "预检", "见前台门锁对接窗口。"),
    FlowStep("import", 3, "③ 在向导内导入旧库", "导入", "见前台门锁对接窗口。"),
    FlowStep("usb", 4, "④ 在向导内 USB 迁移", "USB", "见前台门锁对接窗口。"),
    FlowStep("verify", 5, "⑤ 在向导内验收", "验收", "见前台门锁对接窗口。"),
]


class TakeoverFlowState:
    """可持久化的顺序流程状态。"""

    def __init__(self, config_key: str, steps: List[FlowStep]):
        self.config_key = config_key
        self.steps = steps
        self._keys = [s.key for s in steps]
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            raw = db.get_config(self.config_key) or "{}"
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        done = data.get("done") or {}
        if not isinstance(done, dict):
            done = {}
        data["done"] = {k: bool(done.get(k)) for k in self._keys}
        data.setdefault("usb_skipped", False)
        return data

    def save(self) -> None:
        db.set_config(self.config_key, json.dumps(self._data, ensure_ascii=False))

    def is_done(self, key: str) -> bool:
        return bool(self._data.get("done", {}).get(key))

    def mark_done(self, key: str) -> None:
        if key in self._keys:
            self._data.setdefault("done", {})[key] = True
            self.save()

    def mark_usb_skipped(self) -> None:
        self._data["usb_skipped"] = True
        self.save()

    def reset(self) -> None:
        self._data = {"done": {k: False for k in self._keys}, "usb_skipped": False}
        self.save()

    def step_index(self, key: str) -> int:
        try:
            return self._keys.index(key)
        except ValueError:
            return -1

    def current_step(self) -> FlowStep:
        """第一个未完成的步骤；若全完成则返回最后一步。"""
        for s in self.steps:
            if s.key == "sniff":
                if self.is_done("sniff"):
                    continue
                if self.is_done("usb"):
                    continue  # USB 已完成可不做嗅探
            if not self.is_done(s.key):
                return s
        return self.steps[-1]

    def _required_before(self, key: str) -> List[str]:
        """返回必须先完成的步骤键列表。"""
        if key == "preflight":
            return []
        if key == "import":
            return ["preflight"]
        if key == "usb":
            return ["preflight", "import"]
        if key == "sniff":
            if self._data.get("usb_skipped"):
                return ["preflight", "import"]
            return ["preflight", "import", "usb"]
        if key == "verify":
            return ["preflight", "import"]
        return []

    def can_execute(self, key: str) -> Tuple[bool, str]:
        if key not in self._keys:
            return False, "未知步骤"
        for req in self._required_before(key):
            if not self.is_done(req):
                return False, self._wrong_order_message(key, req)
        if key == "sniff":
            if not self._data.get("usb_skipped") and not self.is_done("usb"):
                return False, self._wrong_order_message(key, "usb")
        if key == "verify":
            if not self.is_done("import"):
                return False, self._wrong_order_message(key, "import")
            if not self.is_done("usb") and not self.is_done("sniff"):
                return (
                    False,
                    "操作顺序不对。\n\n"
                    "您点击的是【⑤ 验收】，但必须先完成：\n"
                    "  • 【③ USB 门锁迁移】或\n"
                    "  • 【④ 发卡串口嗅探】\n\n"
                    "👉 当前应操作："
                    + (self._step_by_key("usb").title if not self._data.get("usb_skipped")
                       else "④ 发卡串口嗅探（③ 已标记稍后补）")
                )
        return True, ""

    def _step_by_key(self, key: str) -> Optional[FlowStep]:
        for s in self.steps:
            if s.key == key:
                return s
        return None

    def _wrong_order_message(self, attempted: str, missing: str) -> str:
        att = self._step_by_key(attempted)
        mis = self._step_by_key(missing)
        cur = self.current_step()
        att_n = att.num if att else "?"
        mis_n = mis.num if mis else "?"
        cur_n = cur.num
        cur_title = cur.title
        return (
            f"操作顺序不对。\n\n"
            f"您点击的是【{att.title if att else attempted}】（第 {att_n} 步），\n"
            f"但必须先完成【{mis.title if mis else missing}】（第 {mis_n} 步）。\n\n"
            f"👉 当前应操作：{cur_title}（第 {cur_n} 步）\n"
            f"{cur.hint}\n\n"
            f"正确顺序：① 预检 → ② 导入 → ③ USB → ④ 嗅探（可选）→ ⑤ 验收"
        )

    def status_line(self) -> str:
        parts = []
        for s in self.steps:
            if s.key == "sniff" and (self.is_done("usb") or self._data.get("usb_skipped")):
                mark = "✅" if self.is_done(s.key) else "⏭"
            elif self.is_done(s.key):
                mark = "✅"
            elif s.key == self.current_step().key:
                mark = "👉"
            else:
                mark = "⬜"
            parts.append(f"{mark} {s.short}")
        return "  ".join(parts)

    def guide_banner(self) -> str:
        try:
            from legacy_migration_guide import cardlock_step_banner

            cur = self.current_step()
            if self.is_done("verify") or (
                self.is_done("import")
                and (self.is_done("usb") or self.is_done("sniff") or self._data.get("usb_skipped"))
            ):
                return (
                    "✅ 主线步骤已完成，可点黄框【开始刷新验收】或左侧 ⑤；通过后去前台入住→开卡。"
                )
            return cardlock_step_banner(cur.key, step_done=self.is_done(cur.key))
        except Exception:
            cur = self.current_step()
            return f"👉 当前请操作：{cur.title}\n{cur.hint}"


def cardlock_flow() -> TakeoverFlowState:
    return TakeoverFlowState(CONFIG_KEY_CARDLOCK, CARDLOCK_STEPS)


def hub_cardlock_flow() -> TakeoverFlowState:
    return TakeoverFlowState(CONFIG_KEY_HUB, HUB_CARDLOCK_PATH_STEPS)


def sync_hub_from_cardlock() -> None:
    """前台向导进度同步到整合台（便于整合台判断能否点其它按钮）。"""
    cf = cardlock_flow()
    hf = hub_cardlock_flow()
    hf.mark_done("open_frontdesk")
    for k in ("preflight", "import", "usb", "sniff", "verify"):
        if cf.is_done(k):
            hf.mark_done(k)
    if cf._data.get("usb_skipped"):
        hf._data["usb_skipped"] = True
        hf.save()
