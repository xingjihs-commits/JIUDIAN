"""
backup_service.py — 数据备份与恢复

- 每日自动备份数据库（加密 + 校验和 + 保留 14 份）
- 紧急备份（数据库异常时自动触发）
- 一键恢复（预览 → 确认 → 覆盖）
- 备份校验和验证（防止备份文件损坏）
"""
from __future__ import annotations
import os
import shutil
import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 备份目录（相对应用根目录）
BACKUP_DIR = Path("backup")
MAX_BACKUPS = 14  # 保留最近 14 份（两周）
BACKUP_TIME = "04:00"  # 每日凌晨 4 点
DB_FILENAME = "solid_hotel.db"


def _derive_backup_key(hotel_id: str = "") -> bytes:
    """从酒店 ID 派生 AES 加密密钥。"""
    if not hotel_id:
        hotel_id = "solid_default"
    return hashlib.sha256(f"solid_backup::{hotel_id}::2026".encode()).digest()[:32]


def _compute_checksum(file_path: str) -> str:
    """计算文件的 SHA-256 校验和。"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_checksum_file(backup_path: str, checksum: str) -> None:
    """将校验和写入 .sha256 伴生文件。"""
    cs_path = backup_path + ".sha256"
    meta = {
        "file": os.path.basename(backup_path),
        "sha256": checksum,
        "created_at": datetime.now().isoformat(),
    }
    with open(cs_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _verify_checksum(backup_path: str) -> tuple[bool, str]:
    """验证备份文件的校验和。"""
    cs_path = backup_path + ".sha256"
    if not os.path.isfile(cs_path):
        return False, "校验和文件不存在"
    try:
        with open(cs_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        expected = meta.get("sha256", "")
        actual = _compute_checksum(backup_path)
        if expected != actual:
            return False, f"校验和不匹配: 期望={expected[:16]}... 实际={actual[:16]}..."
        return True, "ok"
    except Exception as e:
        return False, f"校验和验证异常: {e}"


def auto_backup(db_path: str, hotel_id: str = "") -> Optional[Path]:
    """执行一次自动备份（加密 + 校验和），返回备份文件路径。"""
    try:
        src = Path(db_path)
        if not src.is_file():
            logger.warning("[backup] 源数据库不存在: %s", db_path)
            return None

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = BACKUP_DIR / f"solid_backup_{ts}.enc"

        _encrypt_file(str(src), str(dst), _derive_backup_key(hotel_id))
        checksum = _compute_checksum(str(dst))
        _write_checksum_file(str(dst), checksum)
        logger.info("[backup] 备份成功: %s (SHA256=%s)", dst, checksum[:16])

        # 清理过期备份
        _purge_old_backups()
        return dst
    except Exception as e:
        logger.error("[backup] 备份失败: %s", e)
        return None


def emergency_backup(db_path: str, hotel_id: str = "", reason: str = "") -> Optional[Path]:
    """紧急备份（数据库异常时自动触发）。

    与 auto_backup 相同，但文件名加 _EMERGENCY 前缀，并记录触发原因。
    """
    try:
        src = Path(db_path)
        if not src.is_file():
            logger.warning("[backup] 紧急备份：源数据库不存在: %s", db_path)
            return None

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = BACKUP_DIR / f"solid_backup_EMERGENCY_{ts}.enc"

        _encrypt_file(str(src), str(dst), _derive_backup_key(hotel_id))
        checksum = _compute_checksum(str(dst))
        _write_checksum_file(str(dst), checksum)
        # 记录紧急备份原因
        reason_path = str(dst) + ".reason.txt"
        with open(reason_path, "w", encoding="utf-8") as f:
            f.write(f"触发时间: {datetime.now().isoformat()}\n原因: {reason}\n")
        logger.warning("[backup] 紧急备份: %s (SHA256=%s) 原因: %s", dst, checksum[:16], reason)

        # 不清理旧备份，保留所有紧急备份
        return dst
    except Exception as e:
        logger.error("[backup] 紧急备份失败: %s", e)
        return None


def list_backups() -> list[dict]:
    """列出所有备份文件（按时间倒序），包含校验和验证状态。"""
    if not BACKUP_DIR.is_dir():
        return []
    backups = []
    for f in sorted(BACKUP_DIR.glob("solid_backup_*.enc"), reverse=True):
        try:
            stat = f.stat()
            ts = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            path = str(f)
            cs_ok, cs_msg = _verify_checksum(path)
            is_emergency = "_EMERGENCY_" in f.name
            backups.append({
                "path": path,
                "size_kb": round(stat.st_size / 1024, 1),
                "time": ts,
                "checksum_ok": cs_ok,
                "checksum_detail": cs_msg,
                "is_emergency": is_emergency,
            })
        except OSError:
            pass
    return backups


def restore_from_backup(backup_path: str, db_path: str, hotel_id: str = "") -> tuple[bool, str]:
    """从加密备份恢复数据（覆盖当前数据库）。

    恢复前先验证校验和，然后创建紧急回滚快照。
    返回 (是否成功, 详情)。
    """
    try:
        src = Path(backup_path)
        if not src.is_file():
            return False, f"备份文件不存在: {backup_path}"

        # 恢复前验证校验和
        cs_ok, cs_msg = _verify_checksum(backup_path)
        if not cs_ok:
            return False, f"备份校验和验证失败: {cs_msg}"

        # 先把当前数据库备份一份（安全回滚）
        dst = Path(db_path)
        emergency = dst.with_suffix(dst.suffix + ".pre_restore")
        if dst.is_file():
            shutil.copy2(str(dst), str(emergency))
            logger.info("[backup] 已保存恢复前快照: %s", emergency)

        # 解密并覆盖
        _decrypt_file(str(src), str(dst), _derive_backup_key(hotel_id))
        logger.info("[backup] 恢复成功: %s → %s", backup_path, db_path)
        return True, "恢复成功"
    except Exception as e:
        logger.error("[backup] 恢复失败: %s", e)
        return False, f"恢复失败: {e}"


def _purge_old_backups():
    """保留最多 MAX_BACKUPS 份普通备份，删除最旧的。紧急备份不被清理。"""
    # 只清理普通备份（不含 _EMERGENCY_）
    files = sorted(
        [f for f in BACKUP_DIR.glob("solid_backup_*.enc") if "_EMERGENCY_" not in f.name],
        key=os.path.getmtime, reverse=True,
    )
    for f in files[MAX_BACKUPS:]:
        try:
            f.unlink()
            # 同时删除伴生的 .sha256 文件
            cs = Path(str(f) + ".sha256")
            if cs.exists():
                cs.unlink()
            logger.debug("[backup] 清理旧备份: %s", f.name)
        except OSError:
            pass


def _encrypt_file(src: str, dst: str, key: bytes):
    """AES-256-GCM 加密文件。"""
    import os as _os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = _os.urandom(12)
    aesgcm = AESGCM(key)

    with open(src, "rb") as f:
        plaintext = f.read()

    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    with open(dst, "wb") as f:
        f.write(nonce + ciphertext)


def _decrypt_file(src: str, dst: str, key: bytes):
    """AES-256-GCM 解密文件。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    with open(src, "rb") as f:
        data = f.read()

    nonce = data[:12]
    ciphertext = data[12:]
    aesgcm = AESGCM(key)

    plaintext = aesgcm.decrypt(nonce, ciphertext, None)

    with open(dst, "wb") as f:
        f.write(plaintext)
