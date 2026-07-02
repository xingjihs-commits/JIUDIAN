"""
proxy_log_parser.py — V9RFL_proxy.log 解析器

将原厂 CardLock.exe 通过代理 DLL 写入的日志转为 Observer 兼容记录。
仅解析，不部署代理。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PROXY_LOG_NAMES = ("V9RFL_proxy.log", "v9rfl_proxy.log")

# DirectWriteUSB / DirectReadUSB hex
_RE_DIRECT_IO = re.compile(
    r"Direct(?:Write|Read)USB.*?hex[=:]\s*([0-9A-Fa-f]+)",
    re.IGNORECASE,
)
_RE_GUEST_CARD = re.compile(
    r"GuestCard.*?room[=:]\s*(\S+).*?(?:b_date|begin)[=:]\s*(\S+).*?(?:e_date|end)[=:]\s*(\S+)",
    re.IGNORECASE,
)
_RE_RET = re.compile(r"ret[=:]\s*(-?\d+)", re.IGNORECASE)


def find_proxy_log(install_dir: str) -> Optional[str]:
    """在安装目录查找代理日志文件。"""
    base = Path(install_dir)
    if not base.is_dir():
        return None
    for name in _PROXY_LOG_NAMES:
        p = base / name
        if p.is_file():
            return str(p)
    return None


def is_proxy_deployed(install_dir: str) -> bool:
    """检测是否已部署代理（存在 _real 备份或 proxy 日志）。"""
    base = Path(install_dir)
    if not base.is_dir():
        return False
    if find_proxy_log(install_dir):
        return True
    return (base / "V9RFL_real.dll").is_file()


def parse_proxy_log(
    log_path: str,
    *,
    offset: int = 0,
) -> list[dict]:
    """解析代理日志，返回 call_complete 风格记录列表。"""
    path = Path(log_path)
    if not path.is_file():
        return []

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("读取代理日志失败: %s", e)
        return []

    if offset > 0:
        text = text[offset:]

    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m_io = _RE_DIRECT_IO.search(line)
        if m_io:
            hex_val = m_io.group(1).upper()
            fn = "direct_write_usb" if "write" in line.lower() else "direct_read_usb"
            records.append({
                "_type": "call_complete",
                "fn_name": fn,
                "payload_hex": hex_val if fn == "direct_write_usb" else "",
                "args_in": {"source": "proxy_log"},
                "ret": {"ok": True, "hex": hex_val if fn == "direct_read_usb" else ""},
            })
            continue

        m_guest = _RE_GUEST_CARD.search(line)
        if m_guest:
            ret_m = _RE_RET.search(line)
            ret_val = int(ret_m.group(1)) if ret_m else 0
            records.append({
                "_type": "call_complete",
                "fn_name": "guest_card",
                "args_in": {
                    "room": m_guest.group(1),
                    "b_date": m_guest.group(2),
                    "e_date": m_guest.group(3),
                    "source": "proxy_log",
                },
                "ret": {"ok": ret_val == 0, "ret": ret_val},
            })

    logger.info("代理日志解析: %d 条记录 (%s)", len(records), path.name)
    return records


def records_to_session(records: list[dict], *, brand: str = "proxy") -> dict:
    """将记录列表包装为 RecordingSession 可消费的 dict。"""
    return {
        "session_id": "proxy_import",
        "brand": brand,
        "records": records,
    }
