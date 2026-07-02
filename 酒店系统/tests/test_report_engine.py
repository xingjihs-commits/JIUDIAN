"""报表引擎 SQL 生成测试"""
from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── 全局 mock 阻止 event_bus / PySide6 ──
@pytest.fixture(autouse=True)
def _mock_heavy_modules():
    """阻止需要 QApplication 的模块真实导入。"""
    mock_bus = MagicMock()
    mock_bus.ledger_updated = MagicMock()
    mock_event_bus_mod = MagicMock()
    mock_event_bus_mod.bus = mock_bus

    with patch.dict("sys.modules", {"event_bus": mock_event_bus_mod}):
        yield


@pytest.fixture
def mock_db():
    """Mock report_engine.db.execute 以返回可控数据。"""
    with patch("report_engine.db") as mock_db:
        mock_db.get_config.return_value = "$"
        yield mock_db


# ─────────────────────────────────────────────
#  1. 月度营收返回期望键
# ─────────────────────────────────────────────
def test_monthly_revenue_query_returns_expected_keys(mock_db):
    """测试 ReportData.monthly_revenue() 返回字典含 days/incomes/expenses/total_income 等键。"""
    from report_engine import ReportData

    mock_db.execute.return_value.fetchall.return_value = [
        ("2026-06-01", 100.0, 10.0),
        ("2026-06-02", 200.0, 20.0),
    ]
    result = ReportData.monthly_revenue(2026, 6)
    expected_keys = {"days", "incomes", "expenses", "total_income",
                     "total_expense", "net_profit"}
    for key in expected_keys:
        assert key in result, f"缺少键: {key}"
    assert result["days"] == ["2026-06-01", "2026-06-02"]
    assert isinstance(result["incomes"], list)
    assert isinstance(result["expenses"], list)


# ─────────────────────────────────────────────
#  2. 日收入和 = 合计
# ─────────────────────────────────────────────
def test_monthly_revenue_sums_to_total(mock_db):
    """测试 daily incomes 之和等于 total_income，daily expenses 之和等于 total_expense。"""
    from report_engine import ReportData

    mock_db.execute.return_value.fetchall.return_value = [
        ("2026-06-01", 150.0, 30.0),
        ("2026-06-02", 250.0, 50.0),
    ]
    result = ReportData.monthly_revenue(2026, 6)
    assert sum(result["incomes"]) == result["total_income"]
    assert sum(result["expenses"]) == result["total_expense"]
    assert result["net_profit"] == result["total_income"] - result["total_expense"]


# ─────────────────────────────────────────────
#  3. 年度营收 12 个月
# ─────────────────────────────────────────────
def test_yearly_revenue_has_12_months():
    """测试 ReportData.yearly_revenue() 返回 12 个月的数据。"""
    from report_engine import ReportData

    # Mock monthly_revenue 在每个月份返回模拟数据
    with patch.object(ReportData, "monthly_revenue") as mock_mr:
        mock_mr.return_value = {
            "total_income": 1000.0,
            "total_expense": 200.0,
            "net_profit": 800.0,
        }
        result = ReportData.yearly_revenue(2026)
    assert len(result["months"]) == 12
    assert len(result["incomes"]) == 12
    assert len(result["expenses"]) == 12
    assert len(result["net_profits"]) == 12


# ─────────────────────────────────────────────
#  4. 出租率趋势默认 30 天
# ─────────────────────────────────────────────
def test_occupancy_trend_30_days_default(mock_db):
    """测试 ReportData.occupancy_trend() 默认返回最近 30 天数据。"""
    from report_engine import ReportData

    # COUNT(*) from rooms
    mock_db.execute.return_value.fetchone.side_effect = [
        (10,),  # total_rooms
        (5,),   # occupied day 1
        (6,),   # occupied day 2
    ] + [(0,)] * 28  # remaining days

    result = ReportData.occupancy_trend(30)
    assert len(result["days"]) == 30
    assert len(result["rates"]) == 30
    assert result["total_rooms"] == 10


# ─────────────────────────────────────────────
#  5. 出租率在 0-100 范围
# ─────────────────────────────────────────────
def test_occupancy_trend_rate_between_0_and_100(mock_db):
    """测试出租率数组所有值都在 0-100 范围内。"""
    from report_engine import ReportData

    results = [(10,)]  # total_rooms = 10
    for i in range(30):
        results.append((i % 10,))  # 0-9 inhouse per day
    mock_db.execute.return_value.fetchone.side_effect = results

    result = ReportData.occupancy_trend(30)
    for rate in result["rates"]:
        assert 0 <= rate <= 100, f"出租率 {rate} 不在 [0,100]"


# ─────────────────────────────────────────────
#  6. 房型收益分组
# ─────────────────────────────────────────────
def test_room_type_revenue_groups_correctly(mock_db):
    """测试 ReportData.room_type_revenue() 按房型分组返回营收。"""
    from report_engine import ReportData

    mock_db.execute.return_value.fetchall.return_value = [
        ("标准间", 5, 1500.0),
        ("大床房", 3, 900.0),
        ("套房", 1, 600.0),
    ]
    result = ReportData.room_type_revenue(2026, 6)
    assert result["types"] == ["标准间", "大床房", "套房"]
    assert result["checkins"] == [5, 3, 1]
    assert result["revenues"] == [1500.0, 900.0, 600.0]


# ─────────────────────────────────────────────
#  7. 支付方式占比 ≈100%
# ─────────────────────────────────────────────
def test_payment_method_breakdown_percentages_sum_to_100(mock_db):
    """测试支付方式分布 percentages 之和约等于 100。"""
    from report_engine import ReportData

    mock_db.execute.return_value.fetchall.return_value = [
        ("CASH_USD", 600.0),
        ("ABA", 300.0),
        ("WECHAT", 100.0),
    ]
    result = ReportData.payment_method_breakdown(2026, 6)
    total_pct = sum(result["percentages"])
    # 允许浮点舍入误差
    assert abs(total_pct - 100.0) < 0.1
    assert result["total"] == 1000.0


# ─────────────────────────────────────────────
#  8. top_guests 不超过 limit
# ─────────────────────────────────────────────
def test_top_guests_returns_at_most_limit(mock_db):
    """测试 ReportData.top_guests(limit=N) 返回记录数不超过 N。"""
    from report_engine import ReportData

    mock_db.execute.return_value.fetchall.return_value = [
        ("张三", "13800000001", 3, 5000.0),
        ("李四", "13800000002", 2, 3000.0),
    ]
    result = ReportData.top_guests(limit=5)
    assert len(result) <= 5
    assert len(result) == 2  # 实际只有 2 条数据


# ─────────────────────────────────────────────
#  9. 渠道分析返回 channels 列表
# ─────────────────────────────────────────────
def test_channel_analysis_returns_channels_list(mock_db):
    """测试 ReportData.channel_analysis() 返回含 channels 键的字典。"""
    from report_engine import ReportData

    mock_db.execute.return_value.fetchall.return_value = [
        ("WALK_IN", 10, 2000.0),
        ("OTA", 5, 1500.0),
    ]
    result = ReportData.channel_analysis(2026, 6)
    assert "channels" in result
    assert "period" in result
    assert len(result["channels"]) == 2
    assert result["channels"][0]["channel"] == "WALK_IN"


# ─────────────────────────────────────────────
#  10. 导出 CSV 为有效 UTF-8
# ─────────────────────────────────────────────
def test_export_monthly_csv_writes_valid_utf8(mock_db):
    """测试 ReportExporter.export_monthly_csv() 生成有效的 UTF-8 CSV 文件。"""
    from report_engine import ReportExporter

    mock_db.execute.return_value.fetchall.return_value = [
        ("2026-06-01", 100.0, 10.0),
        ("2026-06-02", 200.0, 20.0),
    ]
    tmpfile = Path(tempfile.mkdtemp()) / "test_report.csv"
    ok, path = ReportExporter.export_monthly_csv(2026, 6, str(tmpfile))
    assert ok is True
    assert Path(path).exists()
    # 验证是有效 UTF-8 CSV
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)
    assert len(rows) >= 3  # 标题行 + 空行 + 表头 + 数据行 + 合计
    assert "营收报表" in rows[0][0]
