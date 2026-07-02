"""
integrity_report.py — C0-epsilon 月度诚信 PDF
"""
from __future__ import annotations

import csv
import datetime as _dt
from pathlib import Path

from database import db


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = _dt.datetime(year, month, 1)
    end = _dt.datetime(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def collect_monthly_integrity_data(year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)
    hotel = db.get_config("hotel_name") or "酒店"
    currency = db.get_config("currency_symbol") or db.get_config("currency") or "¥"
    sessions = db.execute(
        """SELECT session_id,started_at,finished_at,status,total_items,items_with_diff,items_critical
           FROM inventory_stocktake_sessions
           WHERE session_type='PERIODIC' AND started_at>=? AND started_at<?
           ORDER BY started_at""",
        (start, end),
    ).fetchall()
    diff_rows = db.execute(
        """SELECT s.session_id,s.started_at,l.item_id,COALESCE(i.name,l.item_id),COALESCE(i.unit,'件'),
                  COALESCE(i.cost_price,0),l.book_qty,l.counted_qty,l.diff_qty,l.diff_rate,
                  COALESCE(l.explanation,''),l.resolved_at,l.is_critical
           FROM inventory_stocktake_lines l
           JOIN inventory_stocktake_sessions s ON s.session_id=l.session_id
           LEFT JOIN inventory_items i ON i.item_id=l.item_id
           WHERE s.session_type='PERIODIC' AND s.started_at>=? AND s.started_at<?
             AND l.diff_qty<>0
           ORDER BY l.is_critical DESC,l.diff_rate DESC""",
        (start, end),
    ).fetchall()
    diffs = []
    recovered = 0.0
    pending = 0.0
    for r in diff_rows:
        amount = abs(float(r[5] or 0) * int(r[8] or 0))
        resolved = bool(r[11])
        if resolved:
            recovered += amount
        else:
            pending += amount
        diffs.append({
            "session_id": r[0],
            "started_at": r[1] or "",
            "item_id": r[2],
            "name": r[3],
            "unit": r[4],
            "cost_price": float(r[5] or 0),
            "book_qty": int(r[6] or 0),
            "counted_qty": int(r[7] or 0),
            "diff_qty": int(r[8] or 0),
            "diff_rate": float(r[9] or 0),
            "explanation": r[10] or "",
            "resolved": resolved,
            "is_critical": bool(int(r[12] or 0)),
            "amount": amount,
        })
    baseline = db.execute(
        """SELECT snapshot_time,snapshot_hash,items_count,monitored_count,skipped_count
           FROM inventory_baseline_snapshots ORDER BY snapshot_time DESC LIMIT 1"""
    ).fetchone()
    try:
        from inventory_baseline import verify_chain
        chain = verify_chain(db)
    except Exception as exc:
        chain = {"ok": False, "total": 0, "broken_at": str(exc)}
    energy = []
    try:
        energy = db.execute(
            """SELECT started_at,finished_at,actual_kwh,theoretical_kwh,diff_rate,is_anomaly
               FROM energy_periods WHERE started_at>=? AND started_at<? ORDER BY started_at""",
            (start, end),
        ).fetchall()
    except Exception:
        energy = []
    return {
        "hotel": hotel,
        "year": year,
        "month": month,
        "currency": currency,
        "sessions": sessions,
        "diffs": diffs,
        "recovered_amount": recovered,
        "pending_amount": pending,
        "baseline": baseline,
        "chain": chain,
        "energy": energy,
    }


def _font_name():
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import os

        import deploy_paths
        _fonts = deploy_paths.fonts_dir()
        for fp in (os.path.join(_fonts, "msyh.ttc"), os.path.join(_fonts, "simhei.ttf"), os.path.join(_fonts, "simsun.ttc")):
            if os.path.exists(fp):
                pdfmetrics.registerFont(TTFont("CJK", fp))
                return "CJK"
    except Exception:
        pass
    return "Helvetica"


def export_integrity_pdf(year: int, month: int, filepath: str) -> tuple[bool, str]:
    data = collect_monthly_integrity_data(year, month)
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        font = _font_name()
        styles = getSampleStyleSheet()
        title = ParagraphStyle("TitleCJK", parent=styles["Title"], fontName=font, fontSize=18)
        h2 = ParagraphStyle("H2CJK", parent=styles["Heading2"], fontName=font, fontSize=12)
        body = ParagraphStyle("BodyCJK", parent=styles["BodyText"], fontName=font, fontSize=9)
        story = [
            Paragraph(f"{data['hotel']} · {year}年{month}月 经营诚信报告", title),
            Paragraph(f"生成时间：{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}", body),
            Spacer(1, 6 * mm),
        ]
        cur = data["currency"]
        kpi = [
            ["指标", "数值"],
            ["本月盘点次数", str(len(data["sessions"]))],
            ["差异条目", str(len(data["diffs"]))],
            ["已追回/已解释金额", f"{cur}{data['recovered_amount']:.2f}"],
            ["待处理金额", f"{cur}{data['pending_amount']:.2f}"],
            ["哈希链", "完整" if data["chain"].get("ok") else f"异常 {data['chain'].get('broken_at')}"],
        ]
        story.append(Paragraph("一、本月可信摘要", h2))
        story.append(_table(kpi, font, [60 * mm, 90 * mm]))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph("二、账实差异明细", h2))
        rows = [["商品", "账面", "实物", "差异率", "金额", "状态"]]
        for d in data["diffs"][:40]:
            rows.append([
                d["name"][:18],
                str(d["book_qty"]),
                str(d["counted_qty"]),
                f"{d['diff_rate'] * 100:.1f}%",
                f"{cur}{d['amount']:.2f}",
                "已处理" if d["resolved"] else "待处理",
            ])
        if len(rows) == 1:
            rows.append(["本月无差异", "-", "-", "-", "-", "正常"])
        story.append(_table(rows, font, [45 * mm, 20 * mm, 20 * mm, 25 * mm, 30 * mm, 30 * mm]))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph("三、期初快照与能耗", h2))
        base = data["baseline"]
        base_rows = [["项目", "结果"]]
        if base:
            base_rows.append(["期初快照", f"{base[0]} / {str(base[1])[:16]}"])
            base_rows.append(["监控 SKU", f"{base[3]} / 跳过 {base[4]}"])
        else:
            base_rows.append(["期初快照", "未找到"])
        anomalies = sum(1 for e in data["energy"] if int(e[5] or 0))
        base_rows.append(["能耗对账", f"{len(data['energy'])} 次 / 异常 {anomalies} 次"])
        story.append(_table(base_rows, font, [50 * mm, 120 * mm]))

        doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm)
        doc.build(story)
        return True, filepath
    except ImportError:
        txt = filepath.replace(".pdf", "_诚信报告.csv")
        return _export_integrity_csv(data, txt)
    except Exception as exc:
        return False, str(exc)


def _table(rows, font: str, widths):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    from design_tokens import _p

    table = Table(rows, colWidths=widths)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_p("primary"))),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_p("border"))),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(_p("bg"))]),
    ]))
    return table


def _export_integrity_csv(data: dict, filepath: str) -> tuple[bool, str]:
    try:
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([f"{data['hotel']} {data['year']}年{data['month']}月经营诚信报告"])
            w.writerow(["已追回/已解释金额", data["recovered_amount"]])
            w.writerow(["待处理金额", data["pending_amount"]])
            w.writerow(["商品", "账面", "实物", "差异率", "金额", "状态"])
            for d in data["diffs"]:
                w.writerow([d["name"], d["book_qty"], d["counted_qty"], d["diff_rate"], d["amount"], "已处理" if d["resolved"] else "待处理"])
        return True, filepath
    except Exception as exc:
        return False, str(exc)


def default_output_path(year: int, month: int) -> str:
    out = Path(__file__).resolve().parent / "reports"
    out.mkdir(exist_ok=True)
    return str(out / f"integrity_{year:04d}{month:02d}.pdf")

