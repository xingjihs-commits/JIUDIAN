"""
offline_queue.py — 离线操作队列

网络断开时暂存操作到本地，恢复后批量同步到云端。
策略：last-write-wins + 时间戳比较。
"""
from __future__ import annotations
import json
import sys
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

logger = logging.getLogger(__name__)


from deploy_paths import get_deploy_root as _get_app_dir


QUEUE_FILE = _get_app_dir() / "data" / "offline_ops.jsonl"
MAX_QUEUE_SIZE = 500


def enqueue_offline_operation(op_type: str, data: dict) -> bool:
    """将离线操作写入本地队列。成功返回 True，队列满返回 False。"""
    if not QUEUE_FILE.parent.exists():
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    entry = json.dumps({
        "op_type": op_type,
        "data": data,
        "enqueued_at": datetime.now().isoformat(),
        "timestamp": time.time(),
    })
    
    try:
        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
        logger.debug("[offline] 入队操作: %s", op_type)
        
        # 限制最大队列
        _trim_queue()
        return True
    except Exception as e:
        logger.error("[offline] 入队失败: %s", e)
        return False


def sync_offline_operations(upload_fn: Callable | None = None) -> int:
    """批量同步离线操作到云。

    - 不传处理函数时：自动通过云服务同步地址发送。
    - 传 upload_fn(item) → bool 时：回调决定成功/失败。

    返回成功同步的条数。
    """
    if not QUEUE_FILE.is_file():
        return 0

    if upload_fn is None:
        upload_fn = _cloud_upload_fn

    synced = 0
    remaining = []

    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if upload_fn(item):
                        synced += 1
                    else:
                        remaining.append(line)
                except json.JSONDecodeError:
                    continue

        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            for line in remaining:
                f.write(line + "\n")

        logger.info("[offline] 同步完成: %d/%d", synced, synced + len(remaining))
    except Exception as e:
        logger.error("[offline] 同步失败: %s", e)

    return synced


def _cloud_upload_fn(item: dict) -> bool:
    """通过云服务上传单条离线操作。"""
    try:
        from database import db
        import urllib.request
        worker = (db.get_config("cloud_worker_url") or "").strip().rstrip("/")
        if not worker:
            return False
        hotel_id = db.get_config("hotel_id") or "UNKNOWN"
        body = json.dumps({
            "hotel_id": hotel_id,
            "operation": item,
            "synced_at": datetime.now().isoformat(),
        }).encode("utf-8")
        url = f"{worker}/api/offline-sync"
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        res = urllib.request.urlopen(req, timeout=10)
        return res.status == 200
    except Exception as e:
        logger.warning("[offline] 上传失败: %s", e)
        return False


def pending_count() -> int:
    """返回等待同步的操作数。"""
    if not QUEUE_FILE.is_file():
        return 0
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def resolve_conflict(local: dict, remote: dict, strategy: str = "last_write_wins") -> dict:
    """冲突解决策略。
    
    - "last_write_wins": 比较时间戳，取最新的
    - "local_first": 优先保留本地数据
    - "remote_first": 优先保留远程数据
    """
    if strategy == "local_first":
        return local
    if strategy == "remote_first":
        return remote
    
    # 默认 last_write_wins
    local_ts = local.get("updated_at", local.get("timestamp", 0))
    remote_ts = remote.get("updated_at", remote.get("timestamp", 0))
    
    try:
        lt = float(local_ts)
        rt = float(remote_ts)
        return local if lt >= rt else remote
    except (TypeError, ValueError):
        return local


def clear_queue() -> None:
    """清空离线操作队列。"""
    if QUEUE_FILE.is_file():
        QUEUE_FILE.write_text("", encoding="utf-8")


def _trim_queue():
    """超过 MAX_QUEUE_SIZE 条时裁剪最旧的记录。"""
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        if len(lines) > MAX_QUEUE_SIZE:
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                for line in lines[-MAX_QUEUE_SIZE:]:
                    f.write(line)
    except Exception:
        pass
