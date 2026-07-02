"""
constants.py — Solid 学习助手全局常量

卡型定义、颜色方案、步骤编号、工具函数。
与 UI 框架无关，纯数据层。
"""

from __future__ import annotations

# ── 构建标签 ──────────────────────────────────────────────
BUILD_TAG = "两栏v4"

# ── 颜色方案 ──────────────────────────────────────────────
# 统一调色板：所有面板引用此处，改色只改一个地方
PALETTE = {
    "primary":      "#2563EB",
    "primary_hover":"#1D4ED8",
    "primary_light":"#93C5FD",
    "green":        "#16A34A",
    "green_bg":     "#ECFDF5",
    "green_border": "#6EE7B7",
    "text":         "#0F172A",
    "muted":        "#475569",
    "bg":           "#FFFFFF",
    "bg_alt":       "#F8FAFC",
    "danger":       "#DC2626",
    "danger_bg":    "#FEF2F2",
    "danger_border":"#FCA5A5",
    "warn":         "#D97706",
    "warn_bg":      "#FFFBEB",
    "warn_border":  "#FCD34D",
    "warn_text_bg": "#FEF3C7",
    "border":       "#E2E8F0",
    "border_strong":"#CBD5E1",
    "info_bg":      "#EFF6FF",
    "info_border":  "#BFDBFE",
}

# 兼容旧代码的快捷变量（逐步废弃，新代码一律用 PALETTE）
C_PRIMARY    = PALETTE["primary"]
C_GREEN      = PALETTE["green"]
C_TEXT       = PALETTE["text"]
C_MUTED      = PALETTE["muted"]
C_BG         = PALETTE["bg"]
C_BG_ALT     = PALETTE["bg_alt"]
C_DANGER     = PALETTE["danger"]
C_WARN       = PALETTE["warn"]

# ── 卡型定义 ──────────────────────────────────────────────
# (中文名, 内部key, 所需字段集合)
CARD_TYPES = [
    ("客人卡",     "guest",     {"room", "b_date", "e_date"}),
    ("总卡",       "master",    {"b_date", "e_date"}),
    ("授权卡",     "auth",      set()),
    ("楼层卡",     "floor",     {"building_no", "floor_no"}),
    ("楼栋卡",     "building",  {"building_no"}),
    ("应急卡",     "emergency", set()),
    ("组控卡",     "group",     {"group_no"}),
    ("组号设置卡", "groupset",  {"group_no"}),
    ("房号设置卡", "roomset",   {"room"}),
    ("时钟设置卡", "timeset",   set()),
    ("退房卡",     "checkout",  set()),
    ("挂失卡",     "loss",      set()),
    ("记录卡",     "record",    set()),
    ("空白卡",     "blank",     set()),
]

CARD_NAMES   = [c[0] for c in CARD_TYPES]
CARD_KEY_MAP = {c[0]: c[1] for c in CARD_TYPES}   # 中文名 → key
CARD_FIELDS  = {c[0]: c[2] for c in CARD_TYPES}   # 中文名 → 所需字段 set
CARD_KEY_TO_NAME = {c[1]: c[0] for c in CARD_TYPES}  # key → 中文名（新增）

# ── 步骤定义 ──────────────────────────────────────────────
STEPS      = ["identify", "sample", "analyze", "export"]
STEP_NAMES = ["识别", "采样", "分析", "交接"]

# ── 9 步操作教练步骤编号 ──────────────────────────────────
COACH_STEPS = [
    {"idx": 1, "title": "开始扫描",   "location": "tool"},
    {"idx": 2, "title": "选客人卡",   "location": "tool"},
    {"idx": 3, "title": "读空白卡",   "location": "tool"},
    {"idx": 4, "title": "原厂发客人卡","location": "oem"},
    {"idx": 5, "title": "读已写卡",   "location": "tool"},
    {"idx": 6, "title": "添加样本",   "location": "tool"},
    {"idx": 7, "title": "开始分析",   "location": "tool"},
    {"idx": 8, "title": "核对读数",   "location": "tool"},
    {"idx": 9, "title": "生成握手包", "location": "tool"},
]

# ── 毕业维度 ──────────────────────────────────────────────
GRADUATION_DIMS = [
    ("site",      "认环境"),
    ("bridge",    "发卡器"),
    ("pair",      "采对照"),
    ("protocol",  "协议"),
    ("readback",  "读回"),
    ("path",      "路径"),
    ("token",     "授权卡"),
]

# ── 卡型操作描述 ──────────────────────────────────────────
CARD_DESC_MAP = {
    "guest":     "【客人卡】发给住店客人用，最常用的卡型。\n流程：读空白卡 → 原厂发客人卡 → 读已写卡 → 添加样本",
    "master":    "【总卡】能开全酒店所有房间，权限最高。\n流程：读空白卡 → 原厂发总卡 → 读已写卡 → 添加样本",
    "auth":      "【授权卡】系统授权用。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "floor":     "【楼层卡】只开指定楼层的房间。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "building":  "【楼栋卡】只开指定楼栋的房间。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "emergency": "【应急卡】紧急情况下开全酒店。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "group":     "【组控卡】开指定组号的房间。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "groupset":  "【组号设置卡】设置门锁的组号。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "roomset":   "【房号设置卡】设置门锁的房号。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "timeset":   "【时钟设置卡】校准门锁的时间。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "checkout":  "【退房卡】清除房间的卡片权限。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "loss":      "【挂失卡】挂失已丢失的卡片。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "record":    "【记录卡】读取门锁的开门记录。\n流程：读空白卡 → 原厂发卡 → 读已写卡 → 添加样本",
    "blank":     "【空白卡】直接读空白卡获取样本。\n流程：放空白卡 → 读空白卡 → 添加样本",
}


# ── 工具函数 ──────────────────────────────────────────────

def trunc(s: str, n: int = 32) -> str:
    """截断长字符串。"""
    return s[:n] + "..." if len(s) > n else s


def collector_work_dir() -> "Path":
    """运行时产出目录：打包版写 EXE 旁，源码版写采集器目录。"""
    import sys
    from pathlib import Path
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
