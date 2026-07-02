"""
cloud_handover_pull.py — PMS 端握手包云端拉取客户端

职责
====
采集器把 .solidhandover 回传到厂家云端后，PMS 端通过本模块从云端拉取
待导入的握手包，省去 U 盘中转。

设计约束
========
1. 复用 PMS 端 cloud_security.py 的 HMAC 签名（同模块直接 import）
2. URL 从 system_config.cloud_worker_url 读，默认空=禁用
3. 失败不阻断主流程，全部 try/except + 日志
4. 拉取的握手包存到 lock_runtime/cloud_handovers/ 临时目录，再交给
   handover_importer 导入
5. cloud-worker 端的 API 需要后续配套实现（诚实说）

API 端点（厂家云端 cloud-worker 需要后续配套实现）
==================================================
- GET  {base}/api/pms/handovers/pending
    返回: {"ok": true, "handovers": [
        {"cloud_id": "C001",
         "task_id": "T001",
         "hotel_id": "...", "hotel_name": "...",
         "brand": "prousb_v9", "mode": "dll_direct",
         "size_bytes": 12345678,
         "uploaded_at": "2025-06-22T10:30:00",
         "filename": "希尔顿.solidhandover",
         "status": "received"},
        ...
    ]}
- GET  {base}/api/pms/handovers/download?cloud_id=xxx
    返回: 二进制流（application/octet-stream），即 .solidhandover 文件
- POST {base}/api/pms/handovers/ack
    body: {"cloud_id": "C001", "status": "imported|failed", "detail": "..."}
    返回: {"ok": true}
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 8  # 秒
_DOWNLOAD_TIMEOUT = 120  # 秒，握手包 5-50MB


def _get_cloud_url() -> str:
    """从 system_config 读 cloud_worker_url，空=禁用。"""
    try:
        from database import db
        url = (db.get_config("cloud_worker_url") or "").strip()
        return url.rstrip("/")
    except Exception as exc:
        logger.warning("读取 cloud_worker_url 失败: %s", exc)
        return ""


def _get_hotel_id() -> str:
    """签名主体：用 license_manager 的 hotel_id，回退到 system_config。"""
    try:
        from license_manager import LicenseManager
        return LicenseManager.get_hotel_id()
    except Exception:
        try:
            from database import db
            return (db.get_config("hotel_id")
                    or db.get_config("hotel_name")
                    or "UNKNOWN").strip()
        except Exception:
            return "UNKNOWN"


class CloudHandoverPuller:
    """PMS 端从厂家云端拉取已回传的握手包。

    用法（典型在 vendor_console_tab 的「云端握手包」区块调用）::

        puller = CloudHandoverPuller()
        if puller.is_enabled():
            handovers = puller.list_pending_handovers()
            for h in handovers:
                # 在 UI 列表显示，操作员点击「下载并导入」
                ...
            # 选中一条后
            local_path = puller.download_handover("C001")
            # 然后调用 HandoverImporter().run(local_path)
    """

    def __init__(self, base_url: str = ""):
        self._base_url = (base_url or _get_cloud_url()).rstrip("/")

    # ── 公开 API ──────────────────────────────────────────────

    def is_enabled(self) -> bool:
        """是否启用：URL 配置了就算启用（不强求 ping 通，避免无网时弹错）。"""
        return bool(self._base_url)

    def list_pending_handovers(self) -> list[dict[str, Any]]:
        """列出云端待导入的握手包。

        Returns:
            握手包元数据列表，每项含 cloud_id/task_id/hotel_name/brand/
            mode/size_bytes/uploaded_at/filename/status。
            失败返回空列表，不抛异常。
        """
        if not self._base_url:
            logger.info("[sub-g] 云端未配置，返回空握手包列表")
            return []
        try:
            from cloud_security import signed_get_json
            url = f"{self._base_url}/api/pms/handovers/pending"
            resp = signed_get_json(url, timeout=_FETCH_TIMEOUT)
            if resp.status_code != 200:
                logger.warning("[sub-g] 拉取云端握手包列表失败 HTTP %s: %s",
                               resp.status_code, resp.text[:200])
                return []
            body = resp.json() if resp.text else {}
            if not body.get("ok"):
                logger.warning("[sub-g] 云端返回 ok=false: %s",
                               body.get("error", ""))
                return []
            handovers = body.get("handovers", []) or []
            # 按 uploaded_at 升序（旧的优先导入）
            handovers.sort(key=lambda h: h.get("uploaded_at", ""))
            logger.info("[sub-g] 拉取到 %d 个待导入握手包", len(handovers))
            return handovers
        except Exception as exc:
            logger.warning("[sub-g] 拉取云端握手包列表异常（返回空）: %s", exc)
            return []

    def download_handover(self, cloud_id: str) -> Optional[Path]:
        """下载云端握手包到本地临时目录。

        Args:
            cloud_id: 云端握手包 ID。

        Returns:
            本地 .solidhandover 文件路径；失败返回 None。
        """
        if not self._base_url or not cloud_id:
            return None
        try:
            from cloud_security import signed_get_json
            url = f"{self._base_url}/api/pms/handovers/download"
            resp = signed_get_json(url, params={"cloud_id": cloud_id},
                                   timeout=_DOWNLOAD_TIMEOUT)
            if resp.status_code != 200:
                logger.warning("[sub-g] 下载云端握手包 %s 失败 HTTP %s",
                               cloud_id, resp.status_code)
                return None

            # 落盘到 lock_runtime/cloud_handovers/
            local_dir = self._cloud_handover_dir()
            local_dir.mkdir(parents=True, exist_ok=True)
            # 从 Content-Disposition 取文件名，没有就用 cloud_id
            fname = self._filename_from_response(resp, cloud_id)
            local_path = local_dir / fname
            with open(local_path, "wb") as f:
                f.write(resp.content)
            logger.info("[sub-g] 云端握手包 %s 已下载到 %s (%d bytes)",
                        cloud_id, local_path, local_path.stat().st_size)
            return local_path
        except Exception as exc:
            logger.warning("[sub-g] 下载云端握手包 %s 异常: %s",
                           cloud_id, exc)
            return None

    def ack_handover(
        self,
        cloud_id: str,
        status: str = "imported",
        detail: str = "",
    ) -> bool:
        """告诉云端握手包已被 PMS 处理（导入成功 / 失败）。

        Args:
            cloud_id: 云端握手包 ID。
            status: "imported" | "failed"
            detail: 详细信息（如失败原因）。

        Returns:
            True=已确认；False=失败。失败不抛异常。
        """
        if not self._base_url or not cloud_id:
            return False
        try:
            from cloud_security import signed_post_json
            url = f"{self._base_url}/api/pms/handovers/ack"
            body = {
                "cloud_id": cloud_id,
                "status": status,
                "detail": detail,
                "acked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            resp = signed_post_json(url, body, timeout=_FETCH_TIMEOUT)
            if resp.status_code == 200:
                ok = (resp.json() if resp.text else {}).get("ok", False)
                if ok:
                    logger.info("[sub-g] 云端握手包 %s 已 ack: %s",
                                cloud_id, status)
                return bool(ok)
            logger.warning("[sub-g] 云端握手包 ack 失败 HTTP %s",
                           resp.status_code)
            return False
        except Exception as exc:
            logger.warning("[sub-g] 云端握手包 ack 异常: %s", exc)
            return False

    # ── 内部 ──────────────────────────────────────────────────

    @staticmethod
    def _cloud_handover_dir() -> Path:
        """本地暂存目录：lock_runtime/cloud_handovers/。"""
        # lock_deploy/ 的父目录是 酒店系统/
        return Path(__file__).resolve().parent.parent / "lock_runtime" / "cloud_handovers"

    @staticmethod
    def _filename_from_response(resp, fallback: str) -> str:
        """从 Content-Disposition 头取文件名，没有就用 fallback.solidhandover。"""
        cd = resp.headers.get("Content-Disposition", "") or ""
        # 简单解析 filename=xxx
        if "filename=" in cd:
            for part in cd.split(";"):
                part = part.strip()
                if part.lower().startswith("filename="):
                    name = part.split("=", 1)[1].strip().strip('"').strip("'")
                    if name:
                        # 防路径穿越：只取 basename
                        return os.path.basename(name)
        return f"{fallback}.solidhandover"
