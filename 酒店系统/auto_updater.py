"""
auto_updater.py — 自动更新系统

与云服务配合实现：
  1. check_version() — 查询最新版本及白/黑名单
  2. download_update(version) — 增量下载变更文件
  3. install_update() — 替换文件 + 重启应用
  4. rollback() — 回滚到上一版本

版本管理逻辑（云端）：
  - /api/version/check?hotel_id=X&current=1.0.0  返回最新版本号 + 下载链接
  - /api/version/whitelist  查看版本白名单
  - /api/version/blacklist  查看版本黑名单
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from database import db

logger = logging.getLogger(__name__)


def _app_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


# ── 版本文件路径 ──
BACKUP_DIR = _app_dir() / "_updates" / "backups"
MANIFEST_FILE = _app_dir() / "_updates" / "manifest.json"


def _get_worker_url() -> str:
    return (db.get_config("cloud_worker_url") or "").strip().rstrip("/")


def _current_version() -> str:
    return (db.get_config("app_version") or "1.0.0").strip()


def check_version() -> dict:
    """从云端检查最新版本。

    Returns:
        {
            "current": "1.0.0",
            "latest": "1.1.0",
            "update_available": true,
            "download_url": "https://...",
            "release_notes": "...",
            "blocked": false,
            "block_reason": null,
            "whitelisted_versions": ["1.0.0", "1.1.0"],
            "blacklisted_versions": ["0.9.0"],
        }
    """
    import urllib.request
    import urllib.error

    result = {
        "current": _current_version(),
        "latest": _current_version(),
        "update_available": False,
        "download_url": None,
        "release_notes": "",
        "blocked": False,
        "block_reason": None,
        "whitelisted_versions": [],
        "blacklisted_versions": [],
    }

    worker = _get_worker_url()
    if not worker:
        result["block_reason"] = "未配置云服务地址"
        return result

    hotel_id = db.get_config("hotel_id") or "UNKNOWN"
    url = (
        f"{worker}/api/version/check"
        f"?hotel_id={hotel_id}&current={_current_version()}"
    )

    try:
        req = urllib.request.Request(url, method="GET")
        res = urllib.request.urlopen(req, timeout=10)
        data = json.loads(res.read().decode("utf-8"))

        result["latest"] = data.get("latest_version", _current_version())
        result["update_available"] = data.get("update_available", False)
        result["download_url"] = data.get("download_url")
        result["release_notes"] = data.get("release_notes", "")
        result["blocked"] = data.get("blocked", False)
        result["block_reason"] = data.get("block_reason")
        result["whitelisted_versions"] = data.get("whitelisted_versions", [])
        result["blacklisted_versions"] = data.get("blacklisted_versions", [])

        # 如果当前版本被拉黑，记录到配置
        if result["blocked"]:
            logger.warning("[AUTO_UPDATE] 当前版本 %s 已被拉黑: %s",
                           _current_version(), result["block_reason"])
            db.set_config("update_blocked", "1")
            db.set_config("update_block_reason", result["block_reason"] or "")
        else:
            db.set_config("update_blocked", "")

    except urllib.error.URLError as e:
        logger.warning("[AUTO_UPDATE] 版本检查网络失败: %s", e)
        result["block_reason"] = f"网络错误: {e}"
    except Exception as e:
        logger.error("[AUTO_UPDATE] 版本检查异常: %s", e)
        result["block_reason"] = str(e)

    return result


def download_update(version: str) -> Optional[Path]:
    """增量下载指定版本的变更文件。

    Cloud Worker 返回一个 update.zip（包含变更文件 + manifest.json）。

    Returns:
        Path to downloaded zip, or None on failure.
    """
    import urllib.request
    import urllib.error

    worker = _get_worker_url()
    if not worker:
        logger.warning("[AUTO_UPDATE] 未配置云服务地址")
        return None

    hotel_id = db.get_config("hotel_id") or "UNKNOWN"
    url = f"{worker}/api/version/download?hotel_id={hotel_id}&version={version}"

    temp_dir = _app_dir() / "_updates"
    temp_dir.mkdir(exist_ok=True)
    dest = temp_dir / f"update_{version}.zip"

    try:
        logger.info("[AUTO_UPDATE] 开始下载版本 %s ...", version)
        req = urllib.request.Request(url, method="GET")
        res = urllib.request.urlopen(req, timeout=120)
        content = res.read()

        if not content or len(content) < 100:
            logger.warning("[AUTO_UPDATE] 下载内容过短 (%d bytes)", len(content))
            return None

        with open(dest, "wb") as f:
            f.write(content)

        # 校验文件完整性（通过响应头中的 md5）
        expected_md5 = res.headers.get("X-Content-MD5") or res.headers.get("Content-MD5")
        if expected_md5:
            actual_md5 = hashlib.md5(content).hexdigest()
            if actual_md5 != expected_md5:
                logger.error("[AUTO_UPDATE] MD5 校验失败: expected=%s actual=%s",
                             expected_md5, actual_md5)
                dest.unlink()
                return None

        logger.info("[AUTO_UPDATE] 下载完成: %s (%d bytes)", dest.name, len(content))
        return dest

    except urllib.error.URLError as e:
        logger.error("[AUTO_UPDATE] 下载失败: %s", e)
    except Exception as e:
        logger.error("[AUTO_UPDATE] 下载异常: %s", e)

    return None


def install_update(zip_path: Path) -> bool:
    """解压更新包、备份旧文件、替换文件、写入更新清单。

    zip_path 内部结构：
      update_1.1.0.zip
      ├── files/           ← 变更文件（保持相对目录结构）
      │   ├── Solid.exe
      │   ├── database.py
      │   └── ...
      └── manifest.json    ← 文件清单 + 校验

    Returns:
        True if installation prepared successfully.
    """
    if not zip_path or not zip_path.exists():
        logger.error("[AUTO_UPDATE] 更新包不存在: %s", zip_path)
        return False

    version = _extract_version_from_zipname(zip_path)
    backup_dir = BACKUP_DIR / f"v{_current_version()}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    try:
        manifest_data = {}
        files_to_replace = []

        with zipfile.ZipFile(zip_path, "r") as zf:
            # 读取 manifest
            if "manifest.json" in zf.namelist():
                manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
                files_to_replace = manifest_data.get("files", [])

            # 提取 files/ 目录下的变更文件
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # 跳过 manifest 本身
                if info.filename == "manifest.json":
                    continue

                # 计算目标路径（去掉 files/ 前缀）
                rel_path = info.filename
                if rel_path.startswith("files/"):
                    rel_path = rel_path[len("files/"):]

                target_path = _app_dir() / rel_path
                target_path.parent.mkdir(parents=True, exist_ok=True)

                # 先备份旧文件
                if target_path.exists():
                    backup_path = backup_dir / rel_path
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target_path, backup_path)

                # 写入新文件
                with zf.open(info) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)

                logger.info("[AUTO_UPDATE] 已更新: %s", rel_path)

        # 写入更新后的版本号
        if version:
            db.set_config("app_version", version)

        # 保存更新清单（用于 rollback）
        _save_manifest(version or "unknown", {
            "from_version": _current_version(),
            "to_version": version,
            "backup_dir": str(backup_dir),
            "files": files_to_replace,
            "installed_at": _now_iso(),
        })

        db.log_action("SYSTEM", "UPDATE_INSTALLED",
                       f"更新到 {version}" if version else "更新完成")
        logger.info("[AUTO_UPDATE] 更新安装完成")
        return True

    except Exception as e:
        logger.error("[AUTO_UPDATE] 安装失败: %s", e)
        return False


def rollback() -> bool:
    """回滚到上一版本。

    从 MANIFEST_FILE 读取上次更新的信息，反向恢复备份文件。
    只在文件级操作，不涉及数据库回滚。
    """
    if not MANIFEST_FILE.exists():
        logger.warning("[AUTO_UPDATE] 没有可用的回滚点")
        return False

    try:
        manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        from_version = manifest.get("to_version", "unknown")
        to_version = manifest.get("from_version", _current_version())
        backup_dir = Path(manifest.get("backup_dir", ""))
        files = manifest.get("files", [])

        if not backup_dir.exists():
            logger.warning("[AUTO_UPDATE] 备份目录已不存在: %s", backup_dir)
            return False

        restored = 0
        for rel_path in files:
            backup_path = backup_dir / rel_path
            target_path = _app_dir() / rel_path
            if backup_path.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, target_path)
                restored += 1
                logger.info("[AUTO_UPDATE] 已回滚: %s", rel_path)
            else:
                logger.warning("[AUTO_UPDATE] 备份文件缺失: %s", rel_path)

        # 恢复版本号
        if to_version:
            db.set_config("app_version", to_version)

        db.log_action("SYSTEM", "UPDATE_ROLLBACK",
                       f"从 {from_version} 回滚到 {to_version}, 恢复 {restored} 个文件")
        logger.info("[AUTO_UPDATE] 回滚完成: %s → %s (%d 文件)",
                     from_version, to_version, restored)

        # 归档旧 manifest
        archived = MANIFEST_FILE.with_suffix(".json.prev")
        shutil.move(str(MANIFEST_FILE), str(archived))
        return True

    except Exception as e:
        logger.error("[AUTO_UPDATE] 回滚失败: %s", e)
        return False


def _save_manifest(version_to: str, data: dict) -> None:
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_version_from_zipname(zip_path: Path) -> str:
    name = zip_path.stem
    if name.startswith("update_"):
        return name[len("update_"):]
    return "unknown"


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
