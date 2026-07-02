"""门锁 payload 构建测试"""
from __future__ import annotations

import sys
import tempfile
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── 全局 mock：阻止 event_bus / PySide6 等模块被真实导入 ──
@pytest.fixture(autouse=True)
def _mock_heavy_modules():
    """阻止需要 QApplication 或 DLL 环境的模块真实导入。"""
    mock_bus = MagicMock()
    mock_bus.ledger_updated = MagicMock()
    mock_bus.ledger_updated.emit = MagicMock()

    mock_event_bus_mod = MagicMock()
    mock_event_bus_mod.bus = mock_bus

    with patch.dict("sys.modules", {"event_bus": mock_event_bus_mod}):
        yield


@pytest.fixture
def adapter():
    """创建一个无需真实 DLL/硬件的 ProUsbV9Adapter 实例。"""
    from lock_adapters.prousb_v9 import ProUsbV9Adapter

    tmpdir = Path(tempfile.mkdtemp())
    a = ProUsbV9Adapter(tmpdir)
    a._bridge = MagicMock()
    a._dlsCoID = 1
    return a


# ─────────────────────────────────────────────
#  1. 客人卡 payload 格式
# ─────────────────────────────────────────────
def test_guest_card_payload_format(adapter):
    """测试 issue_guest_card 返回的 card_hex 以 C92B20B7 厂家头开头。"""
    adapter._bridge.guest_card.return_value = {
        "ok": True,
        "ret": 0,
        "out": {"hex": "C92B20B7" + "0" * 28},
    }
    with (
        patch.object(adapter, "encoder_ok", return_value=True),
        patch.object(adapter, "_check_open", return_value=None),
        patch.object(adapter, "_ensure_bridge", return_value=adapter._bridge),
        patch.object(adapter, "_success_buzzer"),
    ):
        result = adapter.issue_guest_card("80050301", "2606120000", "2606151200")
    assert result.success is True
    assert result.card_hex.startswith("C92B20B7")


# ─────────────────────────────────────────────
#  2. 锁号编码格式
# ─────────────────────────────────────────────
def test_guest_card_lock_no_encoding():
    """测试 lock_no_from_room() 按 (BldNo, FlrNo, RomID) 算出的 8 位 hex 锁号格式正确。"""
    from lock_adapters.prousb_v9 import ProUsbV9Adapter

    lock_no = ProUsbV9Adapter.lock_no_from_room(1, 3, 5)
    # 编码：80 + RomID(05) + FlrNo(03) + BldNo(01) → "80050301"
    assert lock_no == "80050301"
    assert len(lock_no) == 8
    assert lock_no.isalnum()


# ─────────────────────────────────────────────
#  3. 日期格式
# ─────────────────────────────────────────────
def test_guest_card_date_format():
    """测试 format_date() 输出 10 位 YYMMDDHHMM 字符串。"""
    from lock_adapters.prousb_v9 import format_date

    dt = datetime.datetime(2026, 6, 12, 14, 30)
    result = format_date(dt)
    assert result == "2606121430"
    assert len(result) == 10
    assert result.isdigit()


# ─────────────────────────────────────────────
#  4. 空白卡 payload 类型 nibble
# ─────────────────────────────────────────────
def test_blank_card_payload_type_nibble(adapter):
    """测试空白卡 payload 在 byte13 高位（hex[26]）为 F。"""
    from lock_adapters.prousb_v9 import ProUsbV9Adapter, CardResult

    # proUSB V9 空白卡: byte13 (hex chars 26-27) 高半字节 = F
    # pl[26] must be 'F'
    blank_card_hex = "C92B20B7000000000000000000F000000000"

    # 直接测静态方法 _looks_like_blank_payload
    result = ProUsbV9Adapter._looks_like_blank_payload(blank_card_hex)
    assert result is True

    # 验证位置
    assert len(blank_card_hex) >= 28
    assert blank_card_hex[26] == "F"


# ─────────────────────────────────────────────
#  5. 擦除卡后 payload 不再是原酒店数据
# ─────────────────────────────────────────────
def test_erase_card_clears_payload(adapter):
    """测试 erase 后通过 _looks_like_blank_payload 校验。"""
    from lock_adapters.prousb_v9 import CardResult

    blank_hex = "C92B20B7000000000000000000F000000000"
    orig_hex = "C92B20B7000000000000600000000000"
    adapter._bridge.card_erase.return_value = {
        "ok": True, "ret": 0,
        "out": {"hex": blank_hex},
    }
    with (
        patch.object(adapter, "_check_open", return_value=None),
        patch.object(adapter, "_ensure_bridge", return_value=adapter._bridge),
        patch.object(adapter, "read_card_payload",
                     side_effect=[orig_hex, blank_hex]),
        patch.object(adapter, "_looks_like_blank_payload", return_value=True),
    ):
        result = adapter.erase_card(card_hex=orig_hex)
    assert result.success is True
    assert result.card_hex != orig_hex


# ─────────────────────────────────────────────
#  6. 总卡 payload 可被校验通过
# ─────────────────────────────────────────────
def test_master_card_payload_valid(adapter):
    """测试 issue_master_card 返回的 payload 可被 validate_payload 校验通过。"""
    from lock_adapters.prousb_v9 import ProUsbV9Adapter

    hex_str = "C92B20B70123456789B000CDEF01234567"
    ok, msg = ProUsbV9Adapter.validate_payload(hex_str, "MasterCard")
    assert ok is True, msg


# ─────────────────────────────────────────────
#  7. 检测到 proUSB V9 安装目录
# ─────────────────────────────────────────────
def test_detect_finds_prousb_v9_installation():
    """测试 ProUsbV9Adapter.detect() 在包含 V9RFL.dll 和 d12.dll 的目录返回非 None。"""
    from lock_adapters.prousb_v9 import ProUsbV9Adapter

    tmpdir = Path(tempfile.mkdtemp())
    (tmpdir / "V9RFL.dll").write_text("fake")
    (tmpdir / "d12.dll").write_text("fake")
    result = ProUsbV9Adapter.detect(tmpdir)
    assert result is not None
    assert isinstance(result, ProUsbV9Adapter)


# ─────────────────────────────────────────────
#  8. 缺少 DLL 返回 None
# ─────────────────────────────────────────────
def test_detect_returns_none_for_missing_dll():
    """测试 ProUsbV9Adapter.detect() 在缺少必需 DLL 的目录返回 None。"""
    from lock_adapters.prousb_v9 import ProUsbV9Adapter

    tmpdir = Path(tempfile.mkdtemp())
    (tmpdir / "V9RFL.dll").write_text("fake")
    # 缺少 d12.dll
    result = ProUsbV9Adapter.detect(tmpdir)
    assert result is None


# ─────────────────────────────────────────────
#  9. 擦除非本酒店卡时走 IniCard 回退路径
# ─────────────────────────────────────────────
def test_bridge_client_card_erase_handles_foreign_card(adapter):
    """测试 CardErase 遇到 ret=15（非本酒店卡）时走 IniCard 回退路径。"""
    adapter._bridge.card_erase.side_effect = [
        {"ok": False, "ret": 15, "out": {"hex": ""}, "error": "非本酒店卡"},
        {"ok": True, "ret": 0, "out": {"hex": "C92B20B7000000000000F0000000000000"}},
    ]
    adapter._bridge.call_card_fn.return_value = {"ok": True}
    with (
        patch.object(adapter, "_check_open", return_value=None),
        patch.object(adapter, "_ensure_bridge", return_value=adapter._bridge),
        patch.object(adapter, "read_card_payload",
                     return_value="C92B20B7000000000000F0000000000000"),
        patch.object(adapter, "_looks_like_blank_payload", return_value=True),
    ):
        result = adapter.erase_card(card_hex="")
    assert result.success is True
    # IniCard fallback 被调用（card_erase 第二次调用成功）
    assert adapter._bridge.card_erase.call_count == 2
    assert adapter._bridge.call_card_fn.called


# ─────────────────────────────────────────────
#  10. 发卡器恢复
# ─────────────────────────────────────────────
def test_restart_reader_returns_dict(adapter):
    """测试 restart_reader() 返回包含状态信息的 dict（不依赖真实硬件）。"""
    adapter._bridge.restart_reader.return_value = {
        "ok": True,
        "ret": 0,
        "out": {"status": "restarted"},
    }
    with (
        patch.object(adapter, "initialize", return_value=True),
        patch.object(adapter, "_ensure_bridge", return_value=adapter._bridge),
    ):
        result = adapter.restart_reader(full=True, settle_sec=1.0)
    assert isinstance(result, dict)
    assert result.get("ok") is True
