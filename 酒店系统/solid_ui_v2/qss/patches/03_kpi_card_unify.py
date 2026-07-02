# -*- coding: utf-8 -*-
"""03_kpi_card_unify — KPI 卡片统一高亮配方。

v1: 3 套并存配方
    - KpiCard / ReportKpiCard / OverviewSectionCard / DashKpiCard / FinanceStatCell
    - 都是 top 2px + left/right 1px 的"半框"配方
v2: 统一为 1 套：border-left: 3px solid @accent@ + 普通描边

为什么改：3 套并存让人眼"感觉不一致但说不出"，是"差点意思"来源之一。
"""
from __future__ import annotations
import re

# 匹配 KPI 类卡片的 top + left + right 半框配方
_HALF_FRAME = re.compile(
    r"(QFrame#(?:KpiCard|ReportKpiCard|OverviewSectionCard|DashKpiCard|FinanceStatCell)\s*\{[^}]*?)"
    r"border-top:\s*2px\s*solid\s*@(?:primary|accent)@;\s*"
    r"border-left:\s*1px\s*solid\s*@(?:primary|accent)@;\s*"
    r"border-right:\s*1px\s*solid\s*@(?:primary|accent)@;",
    re.MULTILINE,
)


def apply(qss: str) -> str:
    qss, _ = _HALF_FRAME.subn(r"\1border-left: 3px solid @accent@;", qss)
    return qss
