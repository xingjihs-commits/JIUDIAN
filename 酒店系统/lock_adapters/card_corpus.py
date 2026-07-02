"""A-line: preserve MDB snapshots and issue results (no encoding reverse)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import deploy_paths
_CORPUS_DIR = Path(deploy_paths.cardlock_backup_dir())
_LOG = Path(__file__).resolve().parent.parent / "tools" / "dev" / "_legacy_intel" / "issue_corpus.jsonl"


def snapshot_mdb(install_dir: Path, tag: str) -> Optional[Path]:
    """Copy CardLock.mdb to D:\\proUSB_DBBak\\CardLock<tag>.MDB if source exists."""
    src = Path(install_dir) / "CardLock.mdb"
    if not src.is_file():
        return None
    _CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%y%m%d%H%M%S")
    safe_tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)[:40]
    dst = _CORPUS_DIR / f"CardLock_{ts}_{safe_tag}.MDB"
    try:
        shutil.copy2(src, dst)
        return dst
    except OSError:
        return None


def log_issue_result(
    *,
    fn_name: str,
    success: bool,
    card_hex: str = "",
    before_payload: str = "",
    after_payload: str = "",
    raw_ret: int = 0,
    error: str = "",
    mdb_snapshot: Optional[Path] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "fn": fn_name,
        "success": success,
        "card_hex": (card_hex or "").upper(),
        "before_payload": (before_payload or "").upper(),
        "after_payload": (after_payload or "").upper(),
        "raw_ret": raw_ret,
        "error": error,
        "mdb_snapshot": str(mdb_snapshot) if mdb_snapshot else "",
        "extra": extra or {},
    }
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
