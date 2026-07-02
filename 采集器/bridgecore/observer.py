"""
bridgecore/observer.py — 观察核

通过 RflBridge 的回调钩子录制所有远程调用流量。
不再使用 setattr 猴补丁，钩子直接在 _call 的调用前后触发。

设计要点：
- pre_hook 在 _call 开始前触发（锁外），快照入参
- post_hook 在 _call 返回后触发，记录响应
- 写卡操作自动追加读卡读回数据包
- 录制异常绝不传播到业务代码
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 写卡方法名列表（需要读回数据包）
_WRITE_METHODS = frozenset({
    "guest_card", "guest_card_v2", "compose_guest_card",
    "master_card", "building_card", "floor_card",
    "emergency_card", "group_card", "auth_card", "ini_card",
    "limit_card", "card_erase", "write_card",
})


# ──────────────────────────────────────────────────────────────────
# 录制会话
# ──────────────────────────────────────────────────────────────────

class RecordingSession:
    """一次录制会话。线程安全。"""

    def __init__(
        self,
        *,
        hotel_id: str = "",
        brand: str = "",
        dll_version: str = "",
        dll_path: str = "",
        session_tag: str = "",
    ):
        self.session_id = uuid.uuid4().hex[:12]
        self.start_time = time.time()
        self.start_iso = _dt.datetime.now().isoformat(timespec="microseconds")
        self.hotel_id = hotel_id
        self.brand = brand
        self.dll_version = dll_version
        self.dll_path = dll_path
        self.session_tag = session_tag
        self.records: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._seq = 0

    def add_record(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            record["session_seq"] = self._seq
            self.records.append(record)

    @property
    def record_count(self) -> int:
        return len(self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "start_time": self.start_iso,
            "hotel_id": self.hotel_id,
            "brand": self.brand,
            "dll_version": self.dll_version,
            "dll_path": self.dll_path,
            "session_tag": self.session_tag,
            "record_count": self.record_count,
            "records": self.records,
        }

    def to_jsonl_lines(self) -> list[str]:
        """返回 JSONL 行（不含换行符）。"""
        lines: list[str] = []
        header = {
            "_type": "session_header",
            "session_id": self.session_id,
            "start_time": self.start_iso,
            "hotel_id": self.hotel_id,
            "brand": self.brand,
            "dll_version": self.dll_version,
            "dll_path": self.dll_path,
            "session_tag": self.session_tag,
            "record_count": self.record_count,
        }
        lines.append(json.dumps(header, ensure_ascii=False))
        for rec in self.records:
            lines.append(json.dumps(rec, ensure_ascii=False))
        return lines

    def save(self, filepath: str | Path) -> str:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for line in self.to_jsonl_lines():
                f.write(line + "\n")
        logger.info("[Observer] 会话 %s 已保存到 %s (%d 条)",
                     self.session_id, path, self.record_count)
        return str(path)

    @classmethod
    def load(cls, filepath: str | Path) -> "RecordingSession":
        path = Path(filepath)
        records: list[dict[str, Any]] = []
        header: dict[str, Any] = {}
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("[Observer] 跳过第 %d 行", i + 1)
                    continue
                if i == 0 and obj.get("_type") == "session_header":
                    header = obj
                else:
                    records.append(obj)
        session = cls(
            hotel_id=header.get("hotel_id", ""),
            brand=header.get("brand", ""),
            dll_version=header.get("dll_version", ""),
            dll_path=header.get("dll_path", ""),
            session_tag=header.get("session_tag", ""),
        )
        session.session_id = header.get("session_id", session.session_id)
        session.records = records
        return session


# ──────────────────────────────────────────────────────────────────
# 观察核
# ──────────────────────────────────────────────────────────────────

class Observer:
    """
    观察核 — 通过 _call 回调钩子录制流量。

    使用方式：
        observer = Observer()
        observer.attach(bridge)

        # 正常发卡，Observer 自动通过 _call 钩子录制
        bridge.guest_card(lock_no="0101")

        # 提取录制结果
        observer.detach()
        session = observer.get_session()
        session.save("recording.jsonl")
    """

    def __init__(self):
        self._session: Optional[RecordingSession] = None
        self._attached_bridge: Any = None
        self._pre_hook_id: Optional[Callable] = None
        self._post_hook_id: Optional[Callable] = None

        # 缓冲系统：写卡操作的回读在 post_hook 中标记，
        # 在单独的 flush 线程中执行读卡
        self._pending_readbacks: list[dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self._readback_thread: Optional[threading.Thread] = None
        self._readback_running = False

    # ── 属性 ────────────────────────────────────────────────

    @property
    def attached(self) -> bool:
        return self._pre_hook_id is not None

    @property
    def session(self) -> Optional[RecordingSession]:
        return self._session

    def get_session(self) -> RecordingSession:
        if self._session is None:
            raise RuntimeError("没有录制会话")
        return self._session

    def get_records(self) -> list[dict[str, Any]]:
        s = self._session
        return list(s.records) if s else []

    # ── 会话管理 ────────────────────────────────────────────

    def new_session(
        self,
        *,
        hotel_id: str = "",
        brand: str = "",
        dll_version: str = "",
        dll_path: str = "",
        session_tag: str = "",
    ) -> RecordingSession:
        self._session = RecordingSession(
            hotel_id=hotel_id, brand=brand,
            dll_version=dll_version, dll_path=dll_path,
            session_tag=session_tag,
        )
        return self._session

    # ── attach / detach ─────────────────────────────────────

    def attach(self, bridge: Any, *, auto_session: bool = True) -> None:
        """通过 bridge 的 register_call_hook 挂接录制器。

        相较于 setattr 猴补丁：
        - 所有 RPC 调用一网打尽，不遗漏
        - 不需要配置方法名列表
        - 不污染 bridge 对象
        """
        if bridge is None:
            return
        if self.attached:
            logger.warning("[Observer] 已挂接，先 detach")
            self.detach()

        self._attached_bridge = bridge
        self._pre_hook_id = self._make_pre_hook()
        self._post_hook_id = self._make_post_hook()

        bridge.register_call_hook(pre_fn=self._pre_hook_id, post_fn=self._post_hook_id)

        if auto_session:
            self.new_session()

        # 启动回读缓冲线程
        self._start_readback_worker()

        logger.info("[Observer] 已挂接到 bridge (_call 回调模式)")

    def detach(self) -> Optional[RecordingSession]:
        """解除钩子，返回会话。"""
        self._stop_readback_worker()
        bridge = self._attached_bridge
        if bridge is not None:
            if self._pre_hook_id is not None:
                bridge.unregister_call_hook(pre_fn=self._pre_hook_id)
            if self._post_hook_id is not None:
                bridge.unregister_call_hook(post_fn=self._post_hook_id)
        self._pre_hook_id = None
        self._post_hook_id = None
        self._attached_bridge = None
        logger.info("[Observer] 已从 bridge 解除")
        return self._session

    # ── 回调钩子 ────────────────────────────────────────────

    def _make_pre_hook(self) -> Callable[[str, dict], None]:
        observer = self

        def pre_hook(method: str, args: dict) -> None:
            if observer._session is None:
                return
            # 跳过 RxMonitor 的探针调用，避免污染录制
            if getattr(observer._attached_bridge, "_probe_call", False):
                return
            try:
                record = {
                    "_type": "call_start",
                    "fn_name": method,
                    "args_in": _sanitize_args(args),
                    "timestamp_local": _dt.datetime.now().isoformat(timespec="microseconds"),
                    "timestamp_monotonic_ns": time.monotonic_ns(),
                }
                observer._session.add_record(record)
            except Exception:
                pass

        return pre_hook

    def _make_post_hook(self) -> Callable[[str, dict, dict, Optional[str]], None]:
        observer = self

        def post_hook(method: str, args: dict, response: dict, error: Optional[str]) -> None:
            if observer._session is None:
                return
            try:
                # 找到对应的 pre record
                records = observer._session.records
                target = None
                for r in reversed(records):
                    if r.get("_type") == "call_start" and r.get("fn_name") == method:
                        target = r
                        break
                if target is None:
                    return

                # 补充出参和结果
                target["_type"] = "call_complete"
                target["timestamp_end"] = _dt.datetime.now().isoformat(timespec="microseconds")
                target["error"] = error

                if response:
                    target["ret"] = {
                        "ok": response.get("ok"),
                        "ret": response.get("ret"),
                        "out": response.get("out") if isinstance(response.get("out"), dict) else None,
                    }
                else:
                    target["ret"] = {}

                # 写卡操作：标记需要回读数据包
                if method in _WRITE_METHODS and not error:
                    target["_need_readback"] = True
                    with observer._pending_lock:
                        observer._pending_readbacks.append(target)
            except Exception:
                pass

        return post_hook

    # ── 回读缓冲线程 ────────────────────────────────────────

    def _start_readback_worker(self) -> None:
        if self._readback_running:
            return
        self._readback_running = True
        self._readback_thread = threading.Thread(
            target=self._readback_worker,
            daemon=True,
            name="bridgecore-readback",
        )
        self._readback_thread.start()

    def _stop_readback_worker(self) -> None:
        self._readback_running = False
        if self._readback_thread is not None:
            self._readback_thread.join(timeout=1.0)
            self._readback_thread = None

    def _readback_worker(self) -> None:
        """后台线程：批量执行读卡回读数据包，不阻塞主流程。"""
        batch: list[dict[str, Any]] = []
        while self._readback_running:
            time.sleep(0.05)  # 50ms 轮询
            with self._pending_lock:
                if self._pending_readbacks:
                    batch = list(self._pending_readbacks)
                    self._pending_readbacks.clear()
                else:
                    batch = []
            for rec in batch:
                try:
                    if not self._attached_bridge:
                        continue
                    read_fn = getattr(self._attached_bridge, "read_card", None)
                    if not callable(read_fn):
                        continue
                    rr = read_fn(d12=1)
                    if isinstance(rr, dict) and rr.get("ok"):
                        out = rr.get("out") or {}
                        rec["payload_hex"] = str(out.get("payload") or "")
                        rec["raw_read_hex"] = str(out.get("hex") or "")
                    rec["_need_readback"] = False
                except Exception:
                    rec["_need_readback"] = False
                    rec["readback_error"] = True

    # ── 保存录制 ────────────────────────────────────────────

    def save(self, filepath: str | Path) -> str:
        if self._session is None:
            raise RuntimeError("没有录制会话可保存")
        return self._session.save(filepath)


# ──────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────

def _sanitize_args(args: dict) -> dict[str, Any]:
    """清理参数：去除 timeout 避免干扰回放，序列化不可 JSON 化的值。"""
    safe = {}
    for k, v in args.items():
        if k in ("timeout",):
            continue
        if isinstance(v, (int, float, str, bool, type(None))):
            safe[k] = v
        elif isinstance(v, (list, tuple)):
            safe[k] = [x if isinstance(x, (int, float, str, bool, type(None))) else repr(x) for x in v]
        elif isinstance(v, dict):
            safe[k] = _sanitize_args(v)
        else:
            safe[k] = repr(v)
    return safe


def load_recording(filepath: str | Path) -> list[dict[str, Any]]:
    """从 JSONL 加载录制记录，跳过会话头。"""
    path = Path(filepath)
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if i == 0 and obj.get("_type") == "session_header":
                continue
            records.append(obj)
    return records


def list_sessions(directory: str | Path) -> list[dict[str, Any]]:
    """列出录制目录下所有 JSONL 文件的元信息。

    Args:
        directory: 录制文件目录路径

    Returns:
        每个元素的 dict: {path, filename, session_id, record_count, brand, session_tag, mtime}
    """
    path = Path(directory)
    if not path.is_dir():
        return []
    sessions: list[dict[str, Any]] = []
    for fp in sorted(path.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            header = None
            with open(fp, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line:
                    obj = json.loads(first_line)
                    if obj.get("_type") == "session_header":
                        header = obj
            st = fp.stat()
            sessions.append({
                "path": str(fp),
                "filename": fp.name,
                "session_id": header.get("session_id", "") if header else "",
                "record_count": header.get("record_count", 0) if header else 0,
                "brand": header.get("brand", "") if header else "",
                "session_tag": header.get("session_tag", "") if header else "",
                "mtime": st.st_mtime,
            })
        except Exception:
            sessions.append({
                "path": str(fp),
                "filename": fp.name,
                "session_id": "",
                "record_count": 0,
                "brand": "",
                "session_tag": "",
                "mtime": fp.stat().st_mtime,
            })
    return sessions
