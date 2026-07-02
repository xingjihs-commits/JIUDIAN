"""
task_fetcher.py — 采集器端厂家任务拉取客户端

职责
====
厂家云端下发"今天要去哪几家酒店采集什么"的任务列表，采集器操作员到达
现场后，先在本工具点「拉取厂家任务」，看到任务清单 → 选中任务 → 任务
信息（酒店名/门锁品牌/DLL要求/现场联系人）自动填入采集向导。

设计约束
========
1. 复用 cloud_handover.py 的 HMAC 签名（同酒店同密钥）
2. URL 全部从本地配置读，默认空=禁用
3. 失败不阻断主流程，全部 try/except + 日志
4. API 端点 worker 端需要后续配套实现（诚实说）

API 端点（厂家云端 cloud-worker 需要后续配套实现）
==================================================
- GET  {base}/api/collector/tasks?hotel_id=xxx
    返回: {"ok": true, "tasks": [
        {"task_id": "T001",
         "hotel_id": "...", "hotel_name": "希尔顿酒店",
         "brand_hint": "prousb_v9", "mode_hint": "dll_direct",
         "dll_requirements": ["V9RFL.dll", "d12.dll"],
         "contact_name": "李经理", "contact_phone": "138...",
         "priority": "high",
         "created_at": "2025-06-22T10:00:00",
         "due_at": "2025-06-22T18:00:00",
         "note": "现场要求..."},
        ...
    ]}
- POST {base}/api/collector/tasks/ack
    body: {"task_id": "T001"}
    返回: {"ok": true}
- POST {base}/api/collector/tasks/submit
    multipart: file=<.solidhandover>, meta=JSON{task_id,...}
    返回: {"ok": true, "result_id": "..."}
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

# 复用同包 cloud_handover 的签名函数与本地配置
from .cloud_handover import (
    _load_cloud_config,
    _local_client_secret,
    _local_hotel_id,
    get_cloud_url,
    signature_headers,
)

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 8  # 秒
_SUBMIT_TIMEOUT = 60  # 秒，握手包 5-50MB


class TaskFetcher:
    """从厂家云端拉取该酒店的采集任务列表。

    用法（典型在采集器操作员到达现场后调用）::

        fetcher = TaskFetcher()
        if fetcher.is_enabled():
            tasks = fetcher.fetch_pending_tasks(hotel_id="HTL001")
            for t in tasks:
                print(t["hotel_name"], t["brand_hint"])
            # 操作员选中一个任务后
            fetcher.ack_task(tasks[0]["task_id"])
            # ... 走采集向导 ...
            # 采集完成后
            fetcher.submit_result(tasks[0]["task_id"], "/path/x.solidhandover")
    """

    def __init__(self, base_url: str = "", hotel_id: str = ""):
        self._base_url = (base_url or get_cloud_url()).rstrip("/")
        self._hotel_id = hotel_id or _local_hotel_id()

    # ── 公开 API ──────────────────────────────────────────────

    def is_enabled(self) -> bool:
        """是否启用：URL 配置了就算启用（不强求 ping 通，避免现场无网时弹错）。"""
        return bool(self._base_url)

    def fetch_pending_tasks(self, hotel_id: str = "") -> list[dict[str, Any]]:
        """拉取该酒店待办任务列表。

        Args:
            hotel_id: 酒店 ID（不传则用本地配置的 hotel_id）。

        Returns:
            任务列表，每项含 task_id/hotel_name/brand_hint/mode_hint/
            dll_requirements/contact_name/contact_phone/priority/due_at/note。
            失败返回空列表，不抛异常。
        """
        if not self._base_url:
            logger.info("[sub-g] 任务拉取：云端未配置，返回空列表")
            return []
        sid = hotel_id or self._hotel_id
        try:
            import requests
            url = f"{self._base_url}/api/collector/tasks"
            params = {"hotel_id": sid}
            headers = signature_headers("GET", url, b"", subject=sid)
            resp = requests.get(url, params=params, headers=headers, timeout=_FETCH_TIMEOUT)
            if resp.status_code != 200:
                logger.warning("[sub-g] 任务拉取失败 HTTP %s: %s",
                               resp.status_code, resp.text[:200])
                return []
            body = resp.json() if resp.text else {}
            if not body.get("ok"):
                logger.warning("[sub-g] 任务拉取返回 ok=false: %s",
                               body.get("error", ""))
                return []
            tasks = body.get("tasks", []) or []
            # 按优先级排序：high > normal > low，同级按 due_at 升序
            priority_order = {"high": 0, "normal": 1, "low": 2}
            tasks.sort(key=lambda t: (
                priority_order.get(t.get("priority", "normal"), 1),
                t.get("due_at", ""),
            ))
            logger.info("[sub-g] 拉取到 %d 个待办任务 (hotel_id=%s)", len(tasks), sid)
            return tasks
        except Exception as exc:
            logger.warning("[sub-g] 任务拉取异常（返回空列表）: %s", exc)
            return []

    def ack_task(self, task_id: str) -> bool:
        """确认接收任务（让云端知道操作员已经看到任务并开始执行）。

        Args:
            task_id: 任务 ID。

        Returns:
            True=确认成功；False=失败或不启用。失败不抛异常。
        """
        if not self._base_url or not task_id:
            return False
        try:
            import requests
            from .cloud_handover import _stable_json_bytes
            url = f"{self._base_url}/api/collector/tasks/ack"
            body = {"task_id": task_id, "hotel_id": self._hotel_id,
                    "acked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            body_bytes = _stable_json_bytes(body)
            headers = signature_headers("POST", url, body_bytes, subject=self._hotel_id)
            headers["Content-Type"] = "application/json"
            resp = requests.post(url, data=body_bytes, headers=headers, timeout=_FETCH_TIMEOUT)
            if resp.status_code == 200:
                ok = (resp.json() if resp.text else {}).get("ok", False)
                if ok:
                    logger.info("[sub-g] 任务 %s 已确认接收", task_id)
                return bool(ok)
            logger.warning("[sub-g] 任务确认失败 HTTP %s", resp.status_code)
            return False
        except Exception as exc:
            logger.warning("[sub-g] 任务确认异常: %s", exc)
            return False

    def submit_result(
        self,
        task_id: str,
        handover_path: str,
        extra_meta: Optional[dict[str, Any]] = None,
    ) -> bool:
        """提交采集结果（带 .solidhandover 文件）。

        与 cloud_handover.upload_handover 类似，但带 task_id 关联到厂家
        下发的任务。如果 cloud_handover.upload_handover 已经回传过，这
        里再做一次 submit_result 让云端把回传文件与任务关联起来。

        Args:
            task_id: 任务 ID。
            handover_path: .solidhandover 文件路径。
            extra_meta: 额外元数据。

        Returns:
            True=提交成功；False=失败。失败不抛异常。
        """
        if not self._base_url or not task_id:
            return False
        from pathlib import Path
        fpath = Path(handover_path)
        if not fpath.is_file():
            logger.warning("[sub-g] 提交结果：握手包不存在 %s", handover_path)
            return False
        try:
            import requests
            from .cloud_handover import _stable_json_bytes
            url = f"{self._base_url}/api/collector/tasks/submit"
            meta = {
                "task_id": task_id,
                "hotel_id": self._hotel_id,
                "filename": fpath.name,
                "size_bytes": fpath.stat().st_size,
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            if extra_meta:
                meta.update(extra_meta)
            meta_bytes = _stable_json_bytes(meta)
            headers = signature_headers("POST", url, meta_bytes, subject=self._hotel_id)
            with open(fpath, "rb") as f:
                resp = requests.post(
                    url,
                    data={"meta": meta_bytes.decode("utf-8")},
                    files={"file": (fpath.name, f, "application/octet-stream")},
                    headers=headers,
                    timeout=_SUBMIT_TIMEOUT,
                )
            if resp.status_code == 200:
                ok = (resp.json() if resp.text else {}).get("ok", False)
                if ok:
                    logger.info("[sub-g] 任务 %s 结果已提交", task_id)
                return bool(ok)
            logger.warning("[sub-g] 任务结果提交失败 HTTP %s: %s",
                           resp.status_code, resp.text[:200])
            return False
        except Exception as exc:
            logger.warning("[sub-g] 任务结果提交异常: %s", exc)
            return False
