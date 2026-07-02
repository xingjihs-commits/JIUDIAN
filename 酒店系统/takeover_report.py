"""
takeover_report.py — 接管完整性报告

Phase 3 §3.3.3。接管动作完成后必须给老板/厂家一个一眼能看的：
有多少间房落了 lock_no？哪些房没落？

产出可被 UI 直接渲染的 dict，也提供 open_takeover_report_dialog 一键弹窗。
"""
from __future__ import annotations

from typing import Any, Dict, List

from database import db


def generate_takeover_report() -> Dict[str, Any]:
    """生成接管完整性报告。

    返回：
      {
        "total_rooms":       房间总数,
        "with_lock_no":      已绑定锁号的房间数,
        "missing_lock_no":   缺锁号的 room_id 列表（按 room_id 升序）,
        "completeness":      0..1 的小数完整度,
      }
    """
    try:
        total = db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] or 0
    except Exception:
        total = 0
    try:
        with_lock = db.execute(
            "SELECT COUNT(*) FROM rooms WHERE lock_no IS NOT NULL AND lock_no != ''"
        ).fetchone()[0] or 0
    except Exception:
        with_lock = 0
    missing: List[str] = []
    try:
        rows = db.execute(
            "SELECT room_id FROM rooms WHERE lock_no IS NULL OR lock_no = '' ORDER BY room_id"
        ).fetchall()
        missing = [str(r[0]) for r in rows if r and r[0] is not None]
    except Exception:
        missing = []
    return {
        "total_rooms": int(total),
        "with_lock_no": int(with_lock),
        "missing_lock_no": missing,
        "completeness": (with_lock / total) if total else 0.0,
    }


def format_report_text(report: Dict[str, Any]) -> str:
    """把报告 dict 渲染成对话框可显示的纯文本。"""
    total = report.get("total_rooms", 0)
    bound = report.get("with_lock_no", 0)
    missing = report.get("missing_lock_no", []) or []
    pct = (report.get("completeness", 0.0) or 0.0) * 100.0
    head_icon = "✅" if not missing else ("⚠️" if missing else "ℹ️")
    if total == 0:
        head_icon = "ℹ️"
    lines = [
        f"{head_icon} 接管完整性：{bound} / {total}（{pct:.1f}%）",
        "",
    ]
    if not missing:
        lines.append("所有房间都已绑定锁号。")
    else:
        head = missing[:50]
        lines.append(f"以下 {len(missing)} 间房尚未绑定锁号（最多展示 50 间）：")
        # 每行 8 个房间号
        chunk = 8
        for i in range(0, len(head), chunk):
            lines.append("  " + ", ".join(head[i:i + chunk]))
        if len(missing) > 50:
            lines.append(f"  ……（其余 {len(missing) - 50} 间未列出）")
        lines.append("")
        lines.append("处理方式：进入【厂家工具 → 门锁迁移】重新扫描，或手工补录。")
    return "\n".join(lines)


def open_takeover_report_dialog(parent=None) -> None:
    """弹出接管报告对话框。仅依赖界面框架。"""
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QPlainTextEdit, QPushButton, QHBoxLayout,
    )
    from ui_helpers import style_dialog, build_dialog_header

    report = generate_takeover_report()
    txt = format_report_text(report)

    dlg = QDialog(parent)
    dlg.setWindowTitle("接管完整性报告")
    try:
        style_dialog(dlg, size="medium")
    except Exception:
        dlg.setMinimumSize(640, 480)
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    try:
        layout.addWidget(build_dialog_header(
            "接管完整性报告",
            f"完整性 {report['with_lock_no']} / {report['total_rooms']}",
        ))
    except Exception:
        pass

    box = QPlainTextEdit()
    box.setReadOnly(True)
    box.setPlainText(txt)
    layout.addWidget(box, 1)

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    btn_close = QPushButton("关闭")
    btn_close.setObjectName("FdGhostBtn")
    btn_close.clicked.connect(dlg.accept)
    btn_row.addWidget(btn_close)
    layout.addLayout(btn_row)
    dlg.exec()
