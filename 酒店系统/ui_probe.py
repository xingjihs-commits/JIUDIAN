"""全站 UI 探针 — 自动扫描布局/实底/表格空撑，输出到 memory/visual_baseline/。

老板不用口述问题：切页自动记、按 F12 手动扫，AI 读 ui_probe_latest.md 即可。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLayout,
    QScrollArea,
    QTableWidget,
    QWidget,
)

from design_tokens import _p

_MAX_WIDGETS = 2500
_MIN_AREA = 120 * 80
_MIN_AREA_CRITICAL = 200 * 120
_TABLE_SLACK_PX = 56

# 允许 L0 的具名壳层（设计如此，探针不报 L0_BLEED / SCROLL_FILL）
_L0_SHELL_OK = frozenset({
    "AppRoot", "RightContent", "BodyContainer", "CommandSplitter",
    "WorkspaceDockPanel", "PageScrollWrap", "MatrixPage", "RoomMatrixRoot",
    "MatrixScroll", "MatrixScrollContainer", "WorkspaceDock", "MainWindow",
    "EnhancedStatusBar", "NightAuditTab", "PricingTab", "InventoryTab",
    "FinanceTab", "StaffTab", "MemberTab", "RefundsTab", "AuditTab",
    "WorkspaceSplit",
})

_SCROLL_INNER_OK = frozenset({
    "MatrixScrollContainer", "MiniTabContainer", "SmartHeaderCtxPanel",
    "NightAuditTab", "PricingTab", "InventoryTab", "FinanceTab",
    "SystemConsolePage", "ConsoleSettingsPage", "VendorConsolePage",
})

# 应铺 L1/L2/L3 实底的容器；其内大面积透明 = 无背景 bug
_SURFACE_HOSTS = frozenset({
    "ContentBox",
    "DataTableShell",
    "ConsoleSettingsPage",
    "OverviewSectionCard",
    "FdCheckinPanel",
    "FdBillFolioShell",
    "FinanceLedgerPanel",
})

# 允许透明的小控件 /  chrome
_TRANSPARENT_OK = frozenset({
    "QLabel",
    "QPushButton",
    "QLineEdit",
    "QComboBox",
    "QCheckBox",
    "QSpinBox",
    "QDoubleSpinBox",
    "QHeaderView",
    "QScrollBar",
    "QToolButton",
    "QTabBar",
})

_probe_timer: QTimer | None = None


@dataclass
class UiFinding:
    code: str
    severity: str  # critical | warn | info
    path: str
    detail: str
    geom: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def probe_dir() -> Path:
    d = Path(__file__).resolve().parent.parent / "memory" / "visual_baseline"
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_report_path() -> Path:
    return probe_dir() / "ui_probe_latest.md"


def jsonl_path() -> Path:
    return probe_dir() / "ui_probe.jsonl"


def _hex(c: QColor) -> str:
    return c.name(QColor.NameFormat.HexRgb).upper()


def _norm_hex(h: str) -> str:
    h = (h or "").strip().upper()
    if h.startswith("#") and len(h) == 9:
        return h[:7]
    return h


def _widget_path(w: QWidget) -> str:
    parts: list[str] = []
    cur: QWidget | None = w
    while cur is not None:
        name = cur.objectName() or cur.__class__.__name__
        parts.append(name)
        cur = cur.parentWidget()
    return " > ".join(reversed(parts))


def _visible(w: QWidget) -> bool:
    if not w.isVisible():
        return False
    if w.width() < 8 or w.height() < 8:
        return False
    return True


def _area(w: QWidget) -> int:
    return max(0, w.width()) * max(0, w.height())


def _effective_bg(w: QWidget) -> str:
    ss = w.styleSheet() or ""
    for token in ("background-color:", "background:"):
        if token in ss:
            chunk = ss.split(token, 1)[1].split(";", 1)[0].strip()
            if chunk and not chunk.startswith("transparent"):
                return _norm_hex(chunk)
    if w.autoFillBackground():
        pal = w.palette()
        c = pal.color(QPalette.ColorRole.Window)
        if c.alpha() > 0:
            return _norm_hex(_hex(c))
    pal = w.palette()
    c = pal.color(w.backgroundRole())
    if c.alpha() > 0 and not w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
        return _norm_hex(_hex(c))
    return ""


def _surface_host(w: QWidget) -> str:
    p = w.parentWidget()
    while p is not None:
        name = p.objectName()
        if name in _SURFACE_HOSTS:
            return name
        p = p.parentWidget()
    return ""


def _has_layer_fill(w: QWidget, tokens: dict[str, str]) -> bool:
    bg = _effective_bg(w)
    if not bg:
        return False
    allowed = {_norm_hex(tokens[k]) for k in ("surface", "bg_card", "bg_container", "surface_alt") if tokens.get(k)}
    return bg in allowed


def _predict_showthrough(w: QWidget, tokens: dict[str, str]) -> str:
    """沿父链推断透明控件最终露出的底色（无需截图）。"""
    cur: QWidget | None = w
    while cur is not None:
        bg = _effective_bg(cur)
        if bg:
            return bg
        cur = cur.parentWidget()
    return _norm_hex(tokens.get("bg_root", ""))


def _is_whiteish(c: str) -> bool:
    if not c.startswith("#") or len(c) < 7:
        return False
    try:
        r = int(c[1:3], 16)
        g = int(c[3:5], 16)
        b = int(c[5:7], 16)
        return r > 235 and g > 235 and b > 235
    except ValueError:
        return False


def _layout_has_trailing_stretch(layout: QLayout | None) -> bool:
    if layout is None:
        return False
    count = layout.count()
    if count < 2:
        return False
    last = layout.itemAt(count - 1)
    if last is None:
        return False
    if last.spacerItem() is not None:
        return True
    return last.widget() is None


def _scan_widget(w: QWidget, findings: list[UiFinding], tokens: dict[str, str]) -> None:
    if not _visible(w):
        return
    path = _widget_path(w)
    geom = f"{w.width()}x{w.height()}"
    area = _area(w)

    if isinstance(w, QTableWidget):
        rows = w.rowCount()
        hdr = w.horizontalHeader()
        vh = w.verticalHeader()
        row_h = vh.defaultSectionSize() or 36
        hdr_h = max(hdr.minimumHeight(), hdr.height() or 34)
        expect = hdr_h + row_h * max(rows, 1) + 4
        if w.height() > expect + _TABLE_SLACK_PX:
            findings.append(
                UiFinding(
                    "TABLE_OVERSIZE",
                    "warn" if rows else "critical",
                    path,
                    f"行数={rows} 高度={w.height()}px 预期≈{expect}px",
                    geom,
                    {"rows": rows, "height": w.height(), "expect": expect},
                )
            )
        base = _hex(w.palette().color(w.backgroundRole()))
        if _is_whiteish(base):
            findings.append(
                UiFinding(
                    "TABLE_WHITE_BASE",
                    "critical",
                    path,
                    f"表格 Base 色={base}（Qt 默认白底/未 fd_apply_table_palette）",
                    geom,
                )
            )
        alt = _hex(w.palette().color(QPalette.ColorRole.AlternateBase))
        if _is_whiteish(alt) and w.alternatingRowColors():
            findings.append(
                UiFinding(
                    "TABLE_WHITE_ALT",
                    "critical",
                    path,
                    f"AlternateBase={alt} 斑马线可能露白条",
                    geom,
                )
            )

    if area >= _MIN_AREA and not w.objectName():
        skip_types = (QScrollArea,)
        if not isinstance(w, skip_types):
            findings.append(
                UiFinding(
                    "UNNAMED_LARGE",
                    "info",
                    path,
                    "大面积控件无 objectName，难定位 QSS/探针",
                    geom,
                )
            )

    bg = _effective_bg(w)
    root = _norm_hex(tokens.get("bg_root", ""))
    show = _predict_showthrough(w, tokens)
    host = _surface_host(w)
    cls = w.__class__.__name__

    # 卡片/面板内大面积无 L1+ 实底 —— 代码可判，不必肉眼
    if area >= _MIN_AREA and host and not _has_layer_fill(w, tokens):
        if cls not in _TRANSPARENT_OK and cls != "QHeaderView":
            if area >= _MIN_AREA_CRITICAL or cls not in ("QWidget", "QFrame"):
                sev = "critical" if area >= _MIN_AREA_CRITICAL else "warn"
                findings.append(
                    UiFinding(
                        "NO_BG_IN_SURFACE",
                        sev,
                        path,
                        f"在 {host} 内无 surface/bg_card 实底，透底≈{show or '透明'}",
                        geom,
                        {"host": host, "showthrough": show},
                    )
                )

    if isinstance(w, QScrollArea) and w.widgetResizable():
        inner = w.widget()
        vp = w.viewport()
        if inner is not None and vp is not None and _visible(inner):
            iname = inner.objectName() or ""
            inner_h = inner.height()
            vp_h = max(vp.height(), 1)
            if inner_h >= int(vp_h * 0.85) and inner.width() >= int(vp.width() * 0.85):
                if iname not in _SCROLL_INNER_OK and not _has_layer_fill(inner, tokens):
                    in_show = _predict_showthrough(inner, tokens)
                    if in_show == root or not _effective_bg(inner):
                        findings.append(
                            UiFinding(
                                "SCROLL_FILL_NO_SURFACE",
                                "critical",
                                _widget_path(inner),
                                f"ScrollArea 内页撑满视口({inner.width()}x{inner_h})但无实底，露 L0={root}",
                                f"{inner.width()}x{inner_h}",
                            )
                        )

    if area >= _MIN_AREA and root and bg == root:
        oname = w.objectName()
        if oname not in _L0_SHELL_OK and oname not in (
            "WorkspaceDock", "RoomMatrix", "MatrixScroll", "OverviewScrollBody",
        ):
            findings.append(
                UiFinding(
                    "L0_BLEED",
                    "warn",
                    path,
                    f"大面积实底≈L0 bg_root ({root})，可能空撑露绿",
                    geom,
                )
            )

    if area >= _MIN_AREA and show == root and not bg and host:
        findings.append(
            UiFinding(
                "L0_SHOWTHROUGH",
                "critical" if area >= _MIN_AREA_CRITICAL else "warn",
                path,
                f"大面积透明，父链透出的底色=L0 bg_root ({root})",
                geom,
            )
        )

    if area >= _MIN_AREA and not w.autoFillBackground() and not bg:
        if not isinstance(w, (QScrollArea, QFrame)) or w.objectName() not in (
            "ContentBox",
            "DataTableShell",
            "ConsoleSettingsPage",
            "OverviewSectionCard",
        ):
            findings.append(
                UiFinding(
                    "LARGE_NO_FILL",
                    "warn",
                    path,
                    "大面积未 autoFillBackground 且无 background，可能透明继承",
                    geom,
                )
            )

    ss = (w.styleSheet() or "").strip()
    if ss and area >= 60 * 40:
        mod = w.__class__.__module__ or ""
        _ui_surface_only = ss.startswith("QWidget#") and "background-color:" in ss and ss.count("{") <= 2
        if (
            (mod.startswith("tabs.") or mod.startswith("frontdesk") or mod.endswith("_tab"))
            and not _ui_surface_only
            and "Manufacturer" not in path
            and "Debug" not in path
        ):
            findings.append(
                UiFinding(
                    "INLINE_STYLESHEET",
                    "warn",
                    path,
                    "业务页大面积 inline setStyleSheet（应走 ui_surface fd_apply_*）",
                    geom,
                    {"snippet": ss[:120]},
                )
            )

    lay = w.layout()
    if lay is not None and _layout_has_trailing_stretch(lay) and area >= _MIN_AREA:
        findings.append(
            UiFinding(
                "LAYOUT_TRAILING_STRETCH",
                "info",
                path,
                "布局末尾 addStretch，内容少时易留空带",
                geom,
            )
        )


def scan_ui(root: QWidget | None, *, context: str = "") -> dict[str, Any]:
    """扫描 widget 树，返回结构化报告。"""
    if root is None:
        return {"ok": False, "error": "no root"}
    tokens = {
        "bg": _p("bg"),
        "surface": _p("surface"),
        "elevated": _p("elevated"),
        "bg_card": _p("surface"),
        "bg_container": _p("bg"),
        "surface_alt": _p("bg"),
    }
    findings: list[UiFinding] = []
    seen = 0
    for w in root.findChildren(QWidget):
        seen += 1
        if seen > _MAX_WIDGETS:
            break
        _scan_widget(w, findings, tokens)
    _scan_widget(root, findings, tokens)

    order = {"critical": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f.severity, 9), f.code, f.path))

    by_code: dict[str, int] = {}
    for f in findings:
        by_code[f.code] = by_code.get(f.code, 0) + 1

    return {
        "ok": True,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "context": context or root.__class__.__name__,
        "theme_bg_root": tokens.get("bg_root", ""),
        "widget_count": seen,
        "finding_count": len(findings),
        "by_code": by_code,
        "findings": [asdict(f) for f in findings],
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# UI 探针报告",
        "",
        f"- 时间: {report.get('ts', '')}",
        f"- 上下文: {report.get('context', '')}",
        f"- 扫描控件: {report.get('widget_count', 0)}",
        f"- 问题数: {report.get('finding_count', 0)}",
        f"- 分类: {report.get('by_code', {})}",
        "",
        "> **说明**：`NO_BG_IN_SURFACE` / `L0_SHOWTHROUGH` / `SCROLL_FILL_NO_SURFACE` 为代码推断的无背景/露底，不依赖截图。",
        "",
    ]
    for f in report.get("findings", []):
        lines.append(
            f"## [{f.get('severity', '').upper()}] {f.get('code')} — {f.get('geom', '')}"
        )
        lines.append(f"- 路径: `{f.get('path', '')}`")
        lines.append(f"- {f.get('detail', '')}")
        lines.append("")
    if not report.get("findings"):
        lines.append("_未发现已知 UI 模式问题。_")
    return "\n".join(lines)


def save_report(report: dict[str, Any]) -> Path:
    md_path = latest_report_path()
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    with jsonl_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(report, ensure_ascii=False) + "\n")
    return md_path


def run_ui_probe(root: QWidget | None, *, context: str = "") -> Path | None:
    report = scan_ui(root, context=context)
    if not report.get("ok"):
        return None
    return save_report(report)


def schedule_ui_probe(root: QWidget | None, *, context: str = "", delay_ms: int = 900) -> None:
    """防抖：切页后延迟扫描，避免 refresh 中途误报。"""
    global _probe_timer
    if root is None:
        return
    if _probe_timer is not None:
        try:
            _probe_timer.stop()
        except RuntimeError:
            pass

    def _fire() -> None:
        QApplication.processEvents()
        run_ui_probe(root, context=context)

    _probe_timer = QTimer()
    _probe_timer.setSingleShot(True)
    _probe_timer.timeout.connect(_fire)
    _probe_timer.start(max(200, delay_ms))


def probe_and_toast(root: QWidget | None, *, context: str = "manual") -> Path | None:
    """F12 手动触发：写报告 + 返回路径。"""
    path = run_ui_probe(root, context=context)
    if path is None:
        return None
    try:
        from ui.components.toast import ToastManager, ToastType

        win = root.window() if root else None
        if win is not None and hasattr(win, "toast"):
            n = scan_ui(root, context=context).get("finding_count", 0)
            ToastManager.instance().show(f"UI探针 {n} 项 → {path.name}", ToastType.INFO)
    except Exception:
        pass
    return path
