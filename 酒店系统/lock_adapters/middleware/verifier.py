"""
verifier.py — 品牌 品牌配置 验证器

职责
====
ProfileVerifier 验证提取/生成的 卡数据 品牌配置 能否正常写卡/读卡。

验证流程
========
1. 启动bridge → 打开发卡器
2. 读卡 → 确认卡为空或可覆写
3. 写测试 卡数据 → 读回确认
4. 写原始 payload（还原）→ 完成
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..base import CardResult
from ..bridge_client import RflBridge, get_bridge
from ..generic_adapter import GenericLockAdapter

logger = logging.getLogger(__name__)


class ProfileVerifier:
    """品牌 配置验证器：验证 卡数据 能否正常读写。"""

    VERIFY_RESULTS = {
    "pass": "验证通过 ✅",
    "fail_read": "读卡失败 ❌",
    "fail_write":"写卡验证不匹配 ❌",
    "no_card": "未检测到卡 ❌",
    "skip": "跳过验证（bridge不可用）",
    }

    @classmethod
    def verify(cls, adapter: GenericLockAdapter) -> Dict[str, Any]:
        """验证adapter是否可正常发卡。

        返回:
        dict: {"ok": bool, "result": str, "detail": str}
        """
        result: Dict[str, Any] = {
        "ok": False,
        "result": "skip",
        "detail": "",
        "before_payload": "",
        "after_payload": "",
        }

        # 1. 初始化发卡器
        if not adapter._opened:
            ok = adapter.initialize()
            if not ok:
                result["result"] = "fail_read"
                result["detail"] = "发卡器初始化失败"
                return result

                bridge = adapter._ensure_bridge()
                if bridge is None:
                    result["detail"] = "RFL bridge未初始化"
                    return result

                    # 2. 读卡确认有卡
                    try:
                        resp = bridge.direct_read_usb(d12=1, timeout=4.0)
                        if not resp.get("ok") or int(resp.get("ret", -1)) != 0:
                            result["result"] = "no_card"
                            result["detail"] = "请将空白卡放在发卡器上"
                            return result
                            before = (resp.get("out") or {}).get("payload", "")
                            if not before or before == "0" * 32:
                                before = ""
                    except Exception as exc:
                                result["result"] = "fail_read"
                                result["detail"] = f"读卡异常: {exc}"
                                return result

                                result["before_payload"] = before or "(空)"

                                # 3. 写一个测试 卡数据(授权卡，不修改门锁状态）
                                test_payload = cls._make_test_payload(adapter)
                                if not test_payload:
                                    result["result"] = "skip"
                                    result["detail"] = "无法构造测试 payload（profile 缺失）"
                                    return result

                                    try:
                                        resp = bridge.direct_write_usb(d12=1, card_hex=test_payload, timeout=6.0)
                                        if not resp.get("ok") or int(resp.get("ret", -1)) != 0:
                                            result["result"] = "fail_write"
                                            result["detail"] = f"写测试 payload 失败 (ret={resp.get('ret')})"
                                            return result
                                    except Exception as exc:
                                            result["result"] = "fail_write"
                                            result["detail"] = f"写卡异常: {exc}"
                                            return result

                                            # 4. 读回确认
                                            try:
                                                resp = bridge.direct_read_usb(d12=1, timeout=4.0)
                                                after = (resp.get("out") or {}).get("payload", "")
                                                if after and after.upper() == test_payload.upper():
                                                    result["ok"] = True
                                                    result["result"] = "pass"
                                                    result["detail"] = "写卡验证通过 — 已写测试数据并读回确认"
                                                else:
                                                    result["result"] = "fail_write"
                                                    result["detail"] = (
                                                    f"写后读回不匹配: 写={test_payload[:16]}…, 读={after[:16] if after else '空'}"
                                                    )
                                            except Exception as exc:
                                                    result["result"] = "fail_write"
                                                    result["detail"] = f"验证读卡异常: {exc}"

                                                    result["after_payload"] = test_payload
                                                    return result

                                                    @classmethod
                                                    def _make_test_payload(cls, adapter: GenericLockAdapter) -> Optional[str]:
                                                        """构造一个安全的测试 卡数据(授权卡/擦除卡，不影响门锁）。"""
                                                        # 如果有 品牌配置，用 PayloadFactory
                                                        factory = adapter._get_payload_factory()
                                                        if factory:
                                                            try:
                                                                return factory.build("auth", card_no=0, seq=0)
                                                            except Exception:
                                                                pass

                                                                # 回退：直接用 DirectWriteUSB 模式写入最小 payload
                                                                return None

                                                                @classmethod
                                                                def restore_card(cls, adapter: GenericLockAdapter,
                                                                original_payload: str) -> bool:
                                                                    """还原卡上原始 卡数据。"""
                                                                bridge = adapter._ensure_bridge()
                                                                if bridge is None:
                                                                    return False
                                                                    try:
                                                                        bridge.direct_write_usb(d12=1, card_hex=original_payload, timeout=6.0)
                                                                        return True
                                                                    except Exception:
                                                                        return False
