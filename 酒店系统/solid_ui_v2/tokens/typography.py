# -*- coding: utf-8 -*-
"""字体系统。

v2：主字体 HarmonyOS Sans SC（免费、跨平台、现代），
    fallback 链 HarmonyOS → PingFang → 微雅黑 → Noto Sans SC。
"""
from __future__ import annotations
from enum import Enum


class Fonts(str, Enum):
    """字体族。值可直接用于 CSS font-family。"""
    SANS = '"HarmonyOS Sans SC", "PingFang SC", "Microsoft YaHei UI", "Noto Sans SC", sans-serif'
    MONO = '"JetBrains Mono", "Consolas", "Microsoft YaHei UI", monospace'


class FontSize(str, Enum):
    """字号档位（7 档）。"""
    XS = "11px"   # 状态标签、tag
    SM = "12px"   # 辅助说明
    MD = "13px"   # 正文（表格、表单、卡片正文）
    LG = "14px"   # 小节标题、按钮文字
    XL = "16px"   # 区块标题
    XXL = "20px"  # 页面标题
    XXXL = "28px" # 报表大标题、KPI 数字


class FontWeight(str, Enum):
    """字重档位。"""
    REGULAR = "400"
    MEDIUM = "500"
    SEMIBOLD = "600"
    BOLD = "700"


class LetterSpacing(str, Enum):
    """字间距档位。"""
    TIGHT = "-0.5px"   # 大标题（KPI 数字）
    NORMAL = "0"       # 正文
    WIDE = "0.5px"     # 小节标题
    WIDER = "1px"      # 品牌名
    WIDEST = "2.5px"   # 分组标签（全大写）
