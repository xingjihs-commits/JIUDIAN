"""
bridgecore/token_recorder.py — 授权卡 Token 现场采集器

从 PMS 的 algo_study_recorder.py 搬运核心逻辑，剥离 PMS 依赖。

用途：遇到 auth_token_repeat 品牌时采集授权卡 Token 差分样本。
采集器桥接层（CollectorBridge）没有 guest_card() 方法，所以本模块
改用 direct_read_usb / direct_write_usb 实现被动采样 + 主动构造测试卡。

两种采样模式：
1. collect_guided() — 引导用户用原厂软件发卡，采集器负责前后读卡配对
2. collect_sequence() — 用 direct_write_usb 构造多样本 payload（需 profile）

用法：
    recorder = TokenRecorder(bridge)
    samples = recorder.collect_guided(count=3, d12=1)
    recorder.save_samples(samples, tag="auth_token_matrix")
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# 采样目标：发卡方法列表（用于 wrap 模式，桥接层实际可能没有）
COLLECT_METHODS = (
    "write_card",
    "direct_write_usb",
)


def _samples_dir() -> Path:
    """采集器工作目录下的 token_samples 子目录。"""
    here = Path(__file__).resolve().parent.parent  # 采集器/
    d = here / "output" / "token_samples"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TokenRecorder:
    """授权卡 Token 现场采集器。

    线程安全。非单例（每次现场采集新建一个）。

    Args:
        bridge: CollectorBridge 实例（需有 direct_read_usb / direct_write_usb）。
        static_context: 可选的静态上下文。
    """

    def __init__(
        self,
        bridge: Any,
        *,
        static_context: Optional[dict] = None,
    ):
        self._bridge = bridge
        self._lock = threading.RLock()
        self._seq = 0
        self._static_ctx: dict = dict(static_context or {})

    # ───────────────────────── 引导式采集 ─────────────────────────

    def collect_guided(
        self,
        count: int = 3,
        d12: int = 1,
    ) -> list[dict]:
        """引导式采集：读空白 → 用户用原厂发卡 → 读已写，每次配对。

        由外部循环调用，每轮调用一次。返回单条样本。

        Args:
            count: 采集张数（仅用于日志）。
            d12: 发卡器端口。

        Returns:
            [{"seq": 1, "before_hex": "...", "after_hex": "...",
              "before_raw": "...", "after_raw": "...", "error": ""}, ...]
        """
        results: list[dict] = []
        if not self._bridge:
            logger.error("[TokenRecorder] bridge 未就绪")
            return results

        bridge = self._bridge

        for i in range(count):
            seq = i + 1
            t_local = _dt.datetime.now().isoformat(timespec="microseconds")
            before_hex = ""
            before_raw = ""
            after_hex = ""
            after_raw = ""
            err_text = ""

            try:
                # 读空白/操作前
                rr = bridge.direct_read_usb(d12=d12)
                if isinstance(rr, dict) and rr.get("ok"):
                    before_hex = str(rr.get("hex") or "")
                    before_raw = str(rr.get("data") or "")
                else:
                    err_text = f"第 {seq} 张读空白失败: {rr.get('error', '?')}"
                    logger.warning("[TokenRecorder] %s", err_text)
                    results.append({
                        "seq": seq, "tag": f"auth_token_seq_{seq}",
                        "timestamp_local": t_local, "fn_name": "guided",
                        "before_hex": before_hex, "before_raw": before_raw,
                        "after_hex": after_hex, "after_raw": after_raw,
                        "error": err_text, "collect_type": "auth_token_guided",
                    })
                    continue
            except Exception as e:
                err_text = f"第 {seq} 张读空白异常: {e}"
                logger.error("[TokenRecorder] %s", err_text)
                results.append({
                    "seq": seq, "tag": f"auth_token_seq_{seq}",
                    "timestamp_local": t_local, "fn_name": "guided",
                    "before_hex": before_hex, "before_raw": before_raw,
                    "after_hex": after_hex, "after_raw": after_raw,
                    "error": err_text, "collect_type": "auth_token_guided",
                })
                continue

            # 等待 UI 层引导用户操作原厂软件（外部控制，这里不阻塞）
            # 外部循环在每次 collect_guided 调用后弹窗提示用户

            try:
                # 读已写（用户操作原厂软件后）
                wr = bridge.direct_read_usb(d12=d12)
                if isinstance(wr, dict) and wr.get("ok"):
                    after_hex = str(wr.get("hex") or "")
                    after_raw = str(wr.get("data") or "")
                else:
                    err_text = f"第 {seq} 张读已写失败: {wr.get('error', '?')}"
            except Exception as e:
                err_text = f"第 {seq} 张读已写异常: {e}"

            results.append({
                "seq": seq, "tag": f"auth_token_seq_{seq}",
                "timestamp_local": t_local, "fn_name": "guided",
                "before_hex": before_hex, "before_raw": before_raw,
                "after_hex": after_hex, "after_raw": after_raw,
                "error": err_text, "collect_type": "auth_token_guided",
            })

        ok = sum(1 for s in results if not s["error"] and s["after_hex"])
        logger.info("[TokenRecorder] 引导采集完成: %d/%d 张配对", ok, count)
        return results

    # ───────────────────────── 自动测试卡采集（有 profile 时） ──

    def collect_sequence(
        self,
        count: int = 5,
        d12: int = 1,
    ) -> list[dict]:
        """用 direct_write_usb 构造多样本测试卡。

        适用于已有协议分析结果时，自动写多张不同内容的卡做差分。

        Args:
            count: 写卡张数。
            d12: 发卡器端口。

        Returns:
            [{"seq": 1, "written_hex": "...", "readback_hex": "...", ...}]
        """
        results: list[dict] = []
        if not self._bridge:
            logger.error("[TokenRecorder] bridge 未就绪")
            return results

        bridge = self._bridge

        for i in range(count):
            seq = i + 1
            t_local = _dt.datetime.now().isoformat(timespec="microseconds")
            written_hex = ""
            readback_hex = ""
            err_text = ""

            # 构造一个变化的测试 payload（每张卡不同）
            # 16 字节：4 magic + 2 site + 2 lock_no + 1 salt + 1 type + 4 body + 2 chk
            payload = bytearray(16)
            payload[0:4] = bytes.fromhex("C92B20B7")  # placeholder magic
            payload[4] = 0x3F
            payload[5] = 0xFF
            payload[6] = seq & 0xFF          # lock_no low
            payload[7] = (seq >> 8) & 0xFF   # lock_no high
            payload[8] = 0x00                # salt
            payload[9] = 0x60                # type: guest byte
            # body: 不同的日期
            day_offset = seq * 7
            yymm = 0x26  # 2026
            dd = 0x10 + day_offset
            payload[10] = yymm
            payload[11] = dd
            payload[12] = yymm
            payload[13] = dd + 1
            # checksum 留 0x0000（验证时不校验 chk，只比较写入 vs 读回）

            test_hex = payload.hex().upper()

            try:
                w_resp = bridge.direct_write_usb(d12=d12, card_hex=test_hex)
                if not w_resp.get("ok"):
                    err_text = f"第 {seq} 张写卡失败: {w_resp.get('error', '?')}"
                    logger.warning("[TokenRecorder] %s", err_text)
                else:
                    written_hex = test_hex
                    # 跟读
                    r_resp = bridge.direct_read_usb(d12=d12)
                    if isinstance(r_resp, dict) and r_resp.get("ok"):
                        readback_hex = str(r_resp.get("hex") or "")
            except Exception as e:
                err_text = f"第 {seq} 张异常: {e}"

            results.append({
                "seq": seq,
                "tag": f"auth_token_seq_{seq}",
                "timestamp_local": t_local,
                "fn_name": "direct_write_usb",
                "written_hex": written_hex,
                "readback_hex": readback_hex,
                "error": err_text,
                "collect_type": "auth_token_auto",
            })

            if i < count - 1:
                time.sleep(1.0)

        ok = sum(1 for s in results if not s["error"])
        logger.info("[TokenRecorder] 自动采集完成: %d/%d 张", ok, count)
        return results

    # ───────────────────────── 落盘 ─────────────────────────

    def save_samples(self, samples: list[dict], *, tag: str = "") -> str:
        """将采集的样本追加到 JSONL 文件。

        Args:
            samples: collect_sequence/collect_guided 返回的样本列表。
            tag: 实验标签（可选），会写入每条记录。

        Returns:
            写入的文件路径。
        """
        if not samples:
            logger.warning("[TokenRecorder] 无样本可保存")
            return ""

        today = _dt.datetime.now().strftime("%y%m%d")
        samples_dir = _samples_dir()
        samples_dir.mkdir(parents=True, exist_ok=True)
        path = samples_dir / f"token_samples_{today}.jsonl"
        err_path = samples_dir / f"token_samples_{today}.errors.log"

        written = 0
        with self._lock:
            for s in samples:
                self._seq += 1
                collect_type = s.get("collect_type", "auth_token")
                if collect_type == "auth_token_guided":
                    # 引导式配对样本
                    record = {
                        "seq": self._seq,
                        "timestamp_local": s.get("timestamp_local", ""),
                        "hotel_id": self._static_ctx.get("hotel_id", ""),
                        "dls_co_id": self._static_ctx.get("dls_co_id", ""),
                        "pcid": self._static_ctx.get("pcid", ""),
                        "fn_name": "guided",
                        "before_hex": s.get("before_hex", ""),
                        "before_raw": s.get("before_raw", ""),
                        "after_hex": s.get("after_hex", ""),
                        "after_raw": s.get("after_raw", ""),
                        "experiment_tag": tag or s.get("tag", ""),
                        "collect_type": "auth_token_guided",
                        "error": s.get("error", ""),
                    }
                else:
                    # 自动构造样本
                    record = {
                        "seq": self._seq,
                        "timestamp_local": s.get("timestamp_local", ""),
                        "hotel_id": self._static_ctx.get("hotel_id", ""),
                        "dls_co_id": self._static_ctx.get("dls_co_id", ""),
                        "pcid": self._static_ctx.get("pcid", ""),
                        "fn_name": s.get("fn_name", "direct_write_usb"),
                        "written_hex": s.get("written_hex", ""),
                        "readback_hex": s.get("readback_hex", ""),
                        "experiment_tag": tag or s.get("tag", ""),
                        "collect_type": "auth_token_auto",
                        "error": s.get("error", ""),
                    }
                try:
                    line = json.dumps(record, ensure_ascii=False)
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                    written += 1
                except Exception as e:
                    try:
                        err_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(err_path, "a", encoding="utf-8") as f:
                            f.write(
                                f"[{_dt.datetime.now().isoformat(timespec='seconds')}] "
                                f"record_failed seq={self._seq}: {type(e).__name__}: {e}\n"
                            )
                    except Exception:
                        pass

        logger.info("[TokenRecorder] 保存 %d/%d 条样本到 %s", written, len(samples), path)
        return str(path)

    # ───────────────────────── 包装桥接方法（监控模式）─────────

    def wrap_bridge(self) -> None:
        """装饰桥接的 direct_write_usb 方法，自动 + 跟读 + 落盘。

        装饰后每次写卡自动触发 direct_read_usb 跟读并落盘。
        重复调用安全。
        """
        bridge = self._bridge
        if bridge is None:
            return
        if getattr(bridge, "_token_recorder_wrapped", False):
            return

        for method_name in COLLECT_METHODS:
            original = getattr(bridge, method_name, None)
            if original is None or not callable(original):
                continue
            wrapped = self._make_wrapper(bridge, method_name, original)
            try:
                setattr(bridge, method_name, wrapped)
            except Exception:
                continue

        try:
            setattr(bridge, "_token_recorder_wrapped", True)
        except Exception:
            pass

    def _make_wrapper(self, bridge: Any, method_name: str,
                      original: Callable) -> Callable:
        recorder = self

        def wrapped(*args, **kwargs):
            t_local = _dt.datetime.now().isoformat(timespec="microseconds")
            ret = original(*args, **kwargs)

            readback_hex = ""
            try:
                read_fn = getattr(bridge, "direct_read_usb", None)
                if callable(read_fn):
                    rr = read_fn(d12=kwargs.get("d12", 1))
                    if isinstance(rr, dict) and rr.get("ok"):
                        readback_hex = str(rr.get("hex") or "")
            except Exception:
                pass

            try:
                with recorder._lock:
                    recorder._seq += 1
                    seq = recorder._seq
                record = {
                    "seq": seq,
                    "timestamp_local": t_local,
                    "fn_name": method_name,
                    "written_hex": "",
                    "readback_hex": readback_hex,
                    "collect_type": "wrapped_auto",
                }
                _append_record_failsafe(record)
            except Exception:
                pass

            return ret

        wrapped.__name__ = f"{method_name}__token_wrapped"
        wrapped.__qualname__ = wrapped.__name__
        return wrapped


# ───────────────────────── 底层落盘 ─────────────────────────


def _append_record_failsafe(record: dict) -> None:
    """安全追加一条记录到当日日志文件（不抛异常）。"""
    today = _dt.datetime.now().strftime("%y%m%d")
    d = _samples_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"token_samples_{today}.jsonl"
    try:
        line = json.dumps(record, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
