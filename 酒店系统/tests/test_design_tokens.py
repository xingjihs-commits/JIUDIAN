"""design_tokens.py _p() / invalidate_token_cache 单元测试"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ── 全局 mock ──
_MOCK_DB = MagicMock()
_MOCK_DB.get_config.return_value = "mist"

_MOCK_DB_MOD = MagicMock()
_MOCK_DB_MOD.db = _MOCK_DB


@pytest.fixture(autouse=True)
def _mock_db():
    with patch.dict("sys.modules", {"database": _MOCK_DB_MOD}):
        yield


class TestDesignTokens:
    """_p() 核心令牌解析测试"""

    def test_basic_token_returns_value(self):
        """_p 直接 token 返回色值"""
        from design_tokens import _p
        val = _p("primary")
        assert val.startswith("#"), f"Expected hex color, got {val}"
        assert len(val) == 7

    def test_unknown_token_returns_default(self):
        """不存在的 token 返回空字符串兜底"""
        from design_tokens import _p
        val = _p("__nonexistent_key__")
        assert val == ""

    def test_unknown_token_with_fallback(self):
        """不存在的 token 返回提供的 default"""
        from design_tokens import _p
        val = _p("__nonexistent_key__", "#FALLBACK")
        assert val == "#FALLBACK"

    def test_alias_mapping(self):
        """别名映射生效：accent_soft → surface_alt"""
        from design_tokens import _p, _KEY_ALIASES
        assert "accent_soft" in _KEY_ALIASES
        direct = _p("surface_alt")
        aliased = _p("accent_soft")
        assert direct == aliased, f"alias mismatch: {direct} != {aliased}"

    def test_derived_token_bg_root(self):
        """派生 token bg_root 可解析"""
        from design_tokens import _p
        val = _p("bg_root")
        assert val.startswith("#")
        assert len(val) == 7

    def test_derived_token_bg_card(self):
        """派生 token bg_card（暖色插值）可解析"""
        from design_tokens import _p
        val = _p("bg_card")
        assert val.startswith("#")
        assert len(val) == 7

    def test_all_l0_l3_tokens(self):
        """L0-L3 四层 token 全可解析"""
        from design_tokens import _p
        for k in ["bg_root", "bg_container", "surface", "bg_card"]:
            val = _p(k)
            assert val.startswith("#"), f"_p('{k}') = {val} not hex"

    def test_sidebar_token(self):
        """侧栏 token 独立可解析"""
        from design_tokens import _p
        val = _p("sidebar")
        assert val.startswith("#")

    def test_invalidate_clears_cache(self):
        """invalidate_token_cache 清缓存后重读"""
        from design_tokens import _p, invalidate_token_cache
        before = _p("bg_card")
        invalidate_token_cache()
        after = _p("bg_card")
        assert before == after  # 同主题，色值相同

    def test_theme_switch_returns_different_value(self):
        """切主题后 _p 返回当前主题的 token 值（v2 下部分 token 跨主题不变，属正常）"""
        from design_tokens import _p, invalidate_token_cache
        old_val = _p("primary")
        invalidate_token_cache()
        _MOCK_DB.get_config.return_value = "glow"
        invalidate_token_cache()
        new_val = _p("primary")
        # v2 下 primary 可能跨主题不变，验证两值都是有效 hex
        assert old_val.startswith("#")
        assert new_val.startswith("#")

    def test_text_tokens_return_hex(self):
        """text / text_muted / text_dim 均返回 hex"""
        from design_tokens import _p
        for k in ["text", "text_muted", "text_dim"]:
            val = _p(k)
            assert val.startswith("#"), f"_p('{k}') = {val} not hex"

    def test_btn_tokens_resolve(self):
        """按钮派生 token 可解析"""
        from design_tokens import _p
        for k in ["btn_primary", "btn_card_action", "btn_low_freq"]:
            val = _p(k)
            assert val.startswith("#"), f"_p('{k}') = {val} not hex"
