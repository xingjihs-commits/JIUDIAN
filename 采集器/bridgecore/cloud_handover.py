"""
cloud_handover.py — 采集器端握手包云端回传客户端

职责
====
把 SolidCollector 生成的 .solidhandover 文件回传到厂家云端，让 PMS 端可
以远程拉取，省去 U 盘中转。

设计约束
========
1. 采集器是 U 盘独立工具，**物理隔离 PMS**，不能 import 任何 PMS 模块。
   因此 cloud_security.py 的 HMAC 签名核心函数被**复制**到本文件，并在
   文件头标注 COPIED_FROM。
2. 采集器无 SQLite/SQLCipher 数据库，本地配置（云端 URL / hotel_id /
   client_secret）落在 work_dir/collector_cloud.json。
3. 云端 URL 全部从配置读，默认空=禁用，不硬编码。
4. 失败不阻断主流程：所有公开方法都 try/except + 日志，最差情况回退到
   "仅本地保存"。
5. 所有云端通信走 HMAC-SHA256 签名（X-Solid-Signature 头），与 PMS 端
   cloud_security.py 共用同一签名算法，worker 端用同一密钥验签。

API 端点（厂家云端 cloud-worker 需要后续配套实现）
==================================================
- POST {base}/api/collector/handover-upload
    multipart/form-data:
      file=<.solidhandover 二进制>
      meta=<JSON: hotel_id/hotel_name/brand/mode/generated_at>
    返回: {"ok": true, "task_id": "xxx", "cloud_id": "yyy"}
- GET  {base}/api/collector/upload-status?task_id=xxx
    返回: {"ok": true, "status": "queued|received|imported|failed", "detail": "..."}
- GET  {base}/api/health
    返回: {"ok": true, "service": "shadowguard-cloud"}
"""
from __future__ import annotations

# COPIED_FROM: 酒店系统/cloud_security.py
# 采集器不能 import PMS 模块（物理隔离），HMAC 签名核心逻辑被原样复制过来。
# 两边算法必须保持一致；如需修改签名算法，请同步更新 PMS 端 cloud_security.py。
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

SIGNATURE_VERSION = "solid-hmac-v1"
_UPLOAD_RETRIES = 3
_UPLOAD_RETRY_BACKOFF = 2.0  # 秒
_PING_TIMEOUT = 4  # 秒
_UPLOAD_TIMEOUT = 60  # 秒，握手包 5-50MB


# ──────────────────────────────────────────────────────────────────
#  COPIED_FROM: cloud_security.py  (HMAC 签名核心，采集器独立副本)
# ──────────────────────────────────────────────────────────────────


def _stable_json_bytes(data: Any) -> bytes:
    """[COPIED_FROM cloud_security.py] 生成稳定 JSON 字节序列用于签名。"""
    return json.dumps(
        data or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _path_for_signature(url: str) -> str:
    """[COPIED_FROM cloud_security.py] 取 URL 的 path 部分参与签名。"""
    parsed = urlparse(url)
    return parsed.path or "/"


def signature_headers(
    method: str,
    url: str,
    body: bytes = b"",
    *,
    subject: Optional[str] = None,
    secret: Optional[str] = None,
) -> dict[str, str]:
    """[COPIED_FROM cloud_security.py] 计算 HMAC-SHA256 签名头。

    Args:
        method: HTTP 方法（GET/POST/...）
        url: 完整 URL（用于取 path 段）
        body: 请求体字节（GET 传 b""）
        subject: 签名主体（采集器场景 = hotel_id），不传则从配置读
        secret: HMAC 共享密钥，不传则从配置读

    Returns:
        5 个 X-Solid-* 头的字典，可直接放进 requests 调用。
    """
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    sid = (subject or _local_hotel_id() or "UNKNOWN").strip()
    key = (secret or _local_client_secret()).encode("utf-8")
    msg = "\n".join([
        method.upper(),
        _path_for_signature(url),
        sid,
        ts,
        nonce,
        body.decode("utf-8", errors="ignore"),
    ]).encode("utf-8")
    sig = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return {
        "X-Solid-Signature-Version": SIGNATURE_VERSION,
        "X-Solid-Hotel-Id": sid,
        "X-Solid-Timestamp": ts,
        "X-Solid-Nonce": nonce,
        "X-Solid-Signature": sig,
    }


# ──────────────────────────────────────────────────────────────────
#  采集器本地配置（替代 PMS 端的 database.system_config）
# ──────────────────────────────────────────────────────────────────


def _cloud_config_path() -> Path:
    """采集器云端配置文件路径：work_dir/collector_cloud.json。

    打包版写 EXE 旁，源码版写采集器目录。与 learned_profiles/ 同级，
    便于 U 盘带走时一并带走。
    """
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "collector_cloud.json"
    return Path(__file__).resolve().parent.parent / "collector_cloud.json"


def _load_cloud_config() -> dict[str, Any]:
    """读取本地云端配置，失败返回空 dict。"""
    p = _cloud_config_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as exc:
        logger.warning("读取采集器云端配置失败 %s: %s", p, exc)
        return {}


def _save_cloud_config(cfg: dict[str, Any]) -> None:
    """落盘本地云端配置，失败不抛。"""
    p = _cloud_config_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("写采集器云端配置失败 %s: %s", p, exc)


def _local_hotel_id() -> str:
    """从本地配置读 hotel_id；没有就用 hotel_name；都没有返回 UNKNOWN。"""
    cfg = _load_cloud_config()
    return (cfg.get("hotel_id") or cfg.get("hotel_name") or "UNKNOWN").strip()


def _local_client_secret() -> str:
    """本机与厂家云端共享的 HMAC 密钥。

    首次调用时生成（CS_ 前缀 + sha256(uuid+mac+time)），落盘到本地 JSON。
    后续读取沿用，保证同一台机器签名可被云端验签。
    """
    cfg = _load_cloud_config()
    cached = (cfg.get("cloud_client_secret") or "").strip()
    if cached:
        return cached
    raw = f"{uuid.uuid4().hex}:{uuid.getnode()}:{time.time_ns()}"
    secret = "CS_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    cfg["cloud_client_secret"] = secret
    _save_cloud_config(cfg)
    return secret


def set_cloud_url(url: str) -> None:
    """设置云端 URL（空字符串=禁用）。供 UI 配置入口调用。"""
    cfg = _load_cloud_config()
    cfg["cloud_worker_url"] = (url or "").strip()
    _save_cloud_config(cfg)


def set_hotel_info(hotel_id: str = "", hotel_name: str = "") -> None:
    """设置酒店标识，供签名主体使用。"""
    cfg = _load_cloud_config()
    if hotel_id:
        cfg["hotel_id"] = hotel_id.strip()
    if hotel_name:
        cfg["hotel_name"] = hotel_name.strip()
    _save_cloud_config(cfg)


def get_cloud_url() -> str:
    """读取云端 URL，未配置返回空字符串。"""
    cfg = _load_cloud_config()
    return (cfg.get("cloud_worker_url") or "").strip().rstrip("/")


# ──────────────────────────────────────────────────────────────────
#  CloudHandoverClient — 握手包云端回传主类
# ──────────────────────────────────────────────────────────────────


class CloudHandoverClient:
    """采集器 → 厂家云端的握手包回传客户端。

    用法（典型在 BuildWorker.run() 打包成功后调用）::

        client = CloudHandoverClient()
        if client.is_cloud_enabled():
            result = client.upload_handover("/path/to/x.solidhandover", {
                "hotel_name": "希尔顿",
                "brand": "prousb_v9",
                "mode": "dll_direct",
            })
            if result.get("ok"):
                # 把 cloud_task_id 回写进 MANIFEST.json
                ...
    """

    def __init__(self, base_url: str = "", hotel_id: str = ""):
        """
        Args:
            base_url: 厂家云端基础 URL（如 https://cloud.example.com）。
                      空字符串则从本地配置读。
            hotel_id: 签名主体（酒店 ID），空字符串则从本地配置读。
        """
        self._base_url = (base_url or get_cloud_url()).rstrip("/")
        self._hotel_id = hotel_id or _local_hotel_id()

    # ── 公开 API ──────────────────────────────────────────────

    def is_cloud_enabled(self) -> bool:
        """云端是否可用：URL 配置了 + 能 ping 通 /api/health。

        失败/超时一律返回 False，主流程自然回退到本地保存。
        """
        if not self._base_url:
            return False
        try:
            import requests
            # 用签名头 ping，让云端同时验签；不验签也至少要求 200。
            url = f"{self._base_url}/api/health"
            headers = signature_headers("GET", url, b"", subject=self._hotel_id)
            resp = requests.get(url, headers=headers, timeout=_PING_TIMEOUT)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("云端 ping 失败（%s）: %s", self._base_url, exc)
            return False

    def upload_handover(
        self,
        path: str,
        hotel_info: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """把 .solidhandover 文件回传到厂家云端。

        Args:
            path: .solidhandover 文件本地路径。
            hotel_info: 附加元数据（hotel_name/brand/mode/generated_at/...），
                        会作为 multipart 的 meta 字段提交。

        Returns:
            成功: {"ok": True, "task_id": "...", "cloud_id": "...",
                   "cloud_url": "...", "uploaded_at": "ISO 时间"}
            失败: {"ok": False, "error": "...", "saved_locally": True}
        失败不抛异常，全部 catch 后返回失败 dict，调用方继续走本地保存流程。
        """
        if not self._base_url:
            return {"ok": False, "error": "云端 URL 未配置", "saved_locally": True}

        fpath = Path(path)
        if not fpath.is_file():
            return {"ok": False, "error": f"握手包文件不存在: {path}", "saved_locally": True}

        meta = {
            "hotel_id": self._hotel_id,
            "hotel_name": (hotel_info or {}).get("hotel_name", ""),
            "brand": (hotel_info or {}).get("brand", ""),
            "mode": (hotel_info or {}).get("mode", ""),
            "generated_at": (hotel_info or {}).get("generated_at", ""),
            "filename": fpath.name,
            "size_bytes": fpath.stat().st_size,
        }

        # 重试 3 次，断网/5xx 才重试，4xx 直接放弃（签名错就别瞎试）
        url = f"{self._base_url}/api/collector/handover-upload"
        last_err = ""
        for attempt in range(1, _UPLOAD_RETRIES + 1):
            try:
                result = self._do_upload(url, fpath, meta, attempt)
                if result.get("ok"):
                    return result
                last_err = result.get("error", "未知错误")
                # 4xx 不重试
                if result.get("status_code", 0) and 400 <= result["status_code"] < 500:
                    logger.warning("握手包回传被云端拒绝（%s），不再重试: %s",
                                   result["status_code"], last_err)
                    break
                logger.warning("握手包回传第 %d/%d 次失败: %s",
                               attempt, _UPLOAD_RETRIES, last_err)
            except Exception as exc:
                last_err = str(exc)
                logger.warning("握手包回传第 %d/%d 次异常: %s",
                               attempt, _UPLOAD_RETRIES, exc)

            if attempt < _UPLOAD_RETRIES:
                time.sleep(_UPLOAD_RETRY_BACKOFF * attempt)

        logger.error("握手包回传最终失败（已保存本地）: %s", last_err)
        return {
            "ok": False,
            "error": last_err,
            "saved_locally": True,
            "local_path": str(fpath),
        }

    def get_upload_status(self, task_id: str) -> dict[str, Any]:
        """查询上传任务的云端处理状态。

        Args:
            task_id: upload_handover 返回的 task_id。

        Returns:
            成功: {"ok": True, "status": "queued|received|imported|failed",
                   "detail": "..."}
            失败: {"ok": False, "error": "..."}
        """
        if not self._base_url or not task_id:
            return {"ok": False, "error": "参数缺失"}
        try:
            import requests
            url = f"{self._base_url}/api/collector/upload-status"
            params = {"task_id": task_id}
            headers = signature_headers("GET", url, b"", subject=self._hotel_id)
            resp = requests.get(url, params=params, headers=headers, timeout=_PING_TIMEOUT)
            if resp.status_code == 200:
                return resp.json() if resp.text else {"ok": True}
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code}",
                "status_code": resp.status_code,
            }
        except Exception as exc:
            logger.warning("查询上传状态失败: %s", exc)
            return {"ok": False, "error": str(exc)}

    # ── 内部 ──────────────────────────────────────────────────

    def _do_upload(
        self,
        url: str,
        fpath: Path,
        meta: dict[str, Any],
        attempt: int,
    ) -> dict[str, Any]:
        """单次上传尝试。

        用 requests-toolbelt 不一定可用，回退到手工拼 multipart。这里直接
        用 requests 的 files= 参数 + data= 传 meta，最简单可靠。
        """
        import requests

        meta_bytes = _stable_json_bytes(meta)
        # 签名只签 meta（meta 是 JSON 体能稳定序列化），二进制文件作为附件
        # 不参与签名；云端验签时只验 meta 部分。
        headers = signature_headers(
            "POST", url, meta_bytes, subject=self._hotel_id,
        )
        # 注：requests 用 files= 时会自动设 Content-Type: multipart/form-data
        # 带边界，所以这里不能再手动设 Content-Type，但签名头要保留。
        with open(fpath, "rb") as f:
            resp = requests.post(
                url,
                data={"meta": meta_bytes.decode("utf-8")},
                files={"file": (fpath.name, f, "application/octet-stream")},
                headers=headers,
                timeout=_UPLOAD_TIMEOUT,
            )

        if resp.status_code == 200:
            try:
                body = resp.json() if resp.text else {}
            except Exception:
                body = {"raw": resp.text[:500]}
            if body.get("ok"):
                return {
                    "ok": True,
                    "task_id": body.get("task_id", ""),
                    "cloud_id": body.get("cloud_id", ""),
                    "cloud_url": self._base_url,
                    "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "attempt": attempt,
                }
            return {
                "ok": False,
                "error": body.get("error", "云端返回 ok=false"),
                "status_code": resp.status_code,
            }
        return {
            "ok": False,
            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            "status_code": resp.status_code,
        }


# ──────────────────────────────────────────────────────────────────
#  MANIFEST 回写工具
# ──────────────────────────────────────────────────────────────────


def write_cloud_meta_to_manifest(
    handover_path: str,
    cloud_url: str,
    task_id: str,
    uploaded_at: str,
) -> bool:
    """把云端回传信息回写到 .solidhandover 包内的 MANIFEST.json。

    采集器 BuildWorker 在 upload_handover 成功后调用本函数，让 PMS 端
    拉取后能在 MANIFEST 看到回传时间与云端任务 ID，方便审计。

    实现：zip 内 MANIFEST.json 单独重写，其他文件原地保留。
    失败不抛异常，只返回 False。
    """
    try:
        import zipfile
        import tempfile
        import shutil

        if not Path(handover_path).is_file():
            return False

        # 读出原 MANIFEST
        with zipfile.ZipFile(handover_path, "r") as zf:
            manifest_bytes = zf.read("MANIFEST.json")
            manifest = json.loads(manifest_bytes.decode("utf-8"))

        manifest["cloud_upload_url"] = cloud_url
        manifest["cloud_uploaded_at"] = uploaded_at
        manifest["cloud_task_id"] = task_id

        # 重写 zip：临时文件 → 替换原文件
        tmp_path = handover_path + ".cloudtmp"
        with zipfile.ZipFile(handover_path, "r") as zin:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if item.filename == "MANIFEST.json":
                        zout.writestr(
                            item,
                            json.dumps(manifest, ensure_ascii=False, indent=2),
                        )
                    else:
                        zout.writestr(item, zin.read(item.filename))
        shutil.move(tmp_path, handover_path)
        logger.info("MANIFEST 已回写云端回传信息: task_id=%s", task_id)
        return True
    except Exception as exc:
        logger.warning("回写 MANIFEST 云端信息失败: %s", exc)
        return False
