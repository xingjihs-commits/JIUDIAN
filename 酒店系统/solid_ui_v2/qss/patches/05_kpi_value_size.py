# -*- coding: utf-8 -*-
"""05_kpi_value_size — KPI 数字字号 24 → 28px。

v1: QLabel#ReportKpiValue font-size: 24px
v2: 28px — 让 KPI 数字真正成为仪表盘主角

为什么改：24px 跟正文（13px）只差 11px，没有主角感。
28px + font-weight 700 + 配合卡片左 3px accent，KPI 终于"跳出来"。
"""
from __future__ import annotations
import re

# ReportKpiValue 块内的 font-size: 24px → 28px
_SIZE = re.compile(
    r"(QLabel#ReportKpiValue\s*\{[^}]*?font-size:\s*)24px",
    re.MULTILINE,
)


def apply(qss: str) -> str:
    qss, _ = _SIZE.subn(r"\g<1>28px", qss)
    return qss
