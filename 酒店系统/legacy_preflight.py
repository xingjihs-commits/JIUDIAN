"""
legacy_preflight.py — 老系统或门锁迁移现场预检（酒店前台执行前）

与系统主链对齐：先确认环境与路径 → 再导入门锁数据库 → USB 密钥 → 可选嗅探。
"""
from __future__ import annotations

import os
import platform
import socket
import struct
import subprocess
import time
from typing import Any, Dict, List, Tuple

ACCESS_ENGINE_URL = (
    "https://www.microsoft.com/zh-cn/download/details.aspx?id=54920"
)


def check_solid_deploy_kit() -> Dict[str, Any]:
    """Solid 部署包内置组件是否齐全（合作酒店无需另装）。"""
    try:
        from runtime_deps import deploy_kit_status, mdbtools_bundled_ok
        st = deploy_kit_status()
        ok = (st.get("access_driver") or st.get("mdbtools_bundled")
              or st.get("ace_installer_bundled") or st.get("access_parser_available"))
        return {
            "ok": bool(ok),
            "mdbtools": mdbtools_bundled_ok(),
            "ace_bundled": st.get("ace_installer_bundled"),
            "access_driver": st.get("access_driver"),
            "access_parser": st.get("access_parser_available"),
            "detail": (
                "Solid 完整部署包已就绪（可读取 MDB）"
                if ok
                else "部署包缺少 MDB 读取组件，请用公司完整盘"
            ),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)}


def _is_64bit_os() -> bool:
    return struct.calcsize("P") * 8 == 64


def list_access_odbc_drivers() -> List[str]:
    try:
        import pyodbc
        return [d for d in pyodbc.drivers() if "Access" in d or "ACE" in d or "Jet" in d]
    except ImportError:
        return []


def check_access_driver() -> Dict[str, Any]:
    drivers = list_access_odbc_drivers()
    arch = "64" if _is_64bit_os() else "32"
    return {
        "ok": bool(drivers),
        "drivers": drivers,
        "os_arch": arch,
        "pyodbc": True,
        "install_url": ACCESS_ENGINE_URL,
        "hint": (
            f"请安装 Microsoft Access Database Engine（{arch} 位，与本系统同位数）。"
            if not drivers
            else f"已检测到驱动: {', '.join(drivers[:2])}"
        ),
    }


def probe_mdb_readable(mdb_path: str) -> Dict[str, Any]:
    """尝试只读打开 MDB，返回是否可读及表数量。"""
    if not mdb_path or not os.path.isfile(mdb_path):
        return {"ok": False, "error": "文件不存在", "table_count": 0}
    try:
        from legacy_migration import open_readonly_legacy_db, SchemaAnalyzer

        legacy, dtype, msg = open_readonly_legacy_db(mdb_path)
        if not legacy:
            return {"ok": False, "error": msg, "table_count": 0}
        try:
            tables = SchemaAnalyzer.analyze_legacy(legacy)
            return {
                "ok": bool(tables),
                "dtype": dtype,
                "msg": msg,
                "table_count": len(tables),
                "tables_sample": list(tables.keys())[:12],
            }
        finally:
            legacy.close()
    except Exception as e:
        return {"ok": False, "error": str(e), "table_count": 0}


def _classify_db_candidate(path: str) -> Dict[str, Any]:
    from legacy_migration import DatabaseCracker, DatabaseScanner

    entry = DatabaseScanner.entry_for_database_file(path)
    if not entry:
        return {"path": path, "ok": False, "detail": "不是支持的数据库文件"}
    ext = entry.get("ext", "")
    detail = entry.get("magic_type", "未知")
    ok = False
    if ext in (".db", ".sqlite", ".sqlite3"):
        opened, conn, msg = DatabaseCracker.try_open_sqlite(path)
        ok = opened
        detail = f"SQLite: {msg}"
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    elif ext in (".mdb", ".accdb"):
        probe = probe_mdb_readable(path)
        ok = bool(probe.get("ok"))
        detail = f"Access/MDB: {probe.get('msg') or probe.get('error') or '可探测'}"
    elif ext == ".dbf":
        opened, meta, msg = DatabaseCracker.try_open_dbf(path)
        ok = opened
        detail = f"DBF/FoxPro: {msg}"
    return {**entry, "ok": ok, "detail": detail}


def scan_nearby_database_candidates(seed_path: str = "", limit: int = 12) -> List[Dict[str, Any]]:
    """围绕用户选择路径和常见安装目录找候选旧库，供现场失败时换路径。"""
    from legacy_migration import DatabaseScanner, find_cardlock_mdb_paths, scan_cardlock_candidates

    roots: List[str] = []
    seed = (seed_path or "").strip()
    if seed:
        roots.append(seed if os.path.isdir(seed) else os.path.dirname(seed))
    import deploy_paths
    roots.extend([
        r"C:\CardLock",
        r"C:\Program Files\CardLock",
        r"C:\Program Files (x86)\CardLock",
        r"D:\智能门锁管理系统新2021网络版",
        deploy_paths.cardlock_install_dir(),
        deploy_paths.cardlock_backup_dir(),
    ])
    roots.extend(os.path.dirname(p) for p in find_cardlock_mdb_paths())
    out: List[Dict[str, Any]] = []
    seen = set()
    for ent in scan_cardlock_candidates(seed, limit=limit):
        p = ent.get("path")
        if p and p not in seen:
            seen.add(p)
            out.append(_classify_db_candidate(p))
            out[-1]["cardlock_score"] = ent.get("cardlock_score", 0)
            out[-1]["detail"] = f"{out[-1].get('detail', '')}；匹配分 {ent.get('cardlock_score', 0)}"
        if len(out) >= limit:
            return sorted(out, key=lambda x: (-int(x.get("cardlock_score") or 0), not x.get("ok"), -int(x.get("size") or 0)))
    for root in roots:
        if not root or root in seen or not os.path.exists(root):
            continue
        seen.add(root)
        for ent in DatabaseScanner.scan_path_input(root, max_depth=2):
            if not ent:
                continue
            p = ent.get("path")
            if p and p not in seen:
                seen.add(p)
                out.append(_classify_db_candidate(p))
            if len(out) >= limit:
                return sorted(out, key=lambda x: (-int(x.get("cardlock_score") or 0), not x.get("ok"), -int(x.get("size") or 0)))
    return sorted(out, key=lambda x: (-int(x.get("cardlock_score") or 0), not x.get("ok"), -int(x.get("size") or 0)))


def _tcp_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def run_front_desk_preflight(mdb_path: str = "") -> Dict[str, Any]:
    """
    前台一键预检清单（可全部通过后再点「导入」）。
    返回 items: [{id, label, ok, detail}]
    """
    from legacy_migration import find_cardlock_mdb_paths, scan_cardlock_candidates

    items: List[Dict[str, Any]] = []
    auto_candidates = scan_cardlock_candidates(mdb_path, limit=8, time_budget_s=6.0)
    paths = [str(c.get("path")) for c in auto_candidates if c.get("path")] or find_cardlock_mdb_paths()
    mdb = (mdb_path or "").strip() or (paths[0] if paths else "")
    candidates = auto_candidates or scan_nearby_database_candidates(mdb, limit=10)

    scan_detail = (
        f"自动扫描到 {len(auto_candidates)} 个候选，已选择：{mdb}"
        if mdb else
        "未自动找到门锁数据库；请确认旧系统文件夹已复制到本机"
    )
    items.append({
        "id": "auto_scan",
        "label": "自动扫描门锁旧库",
        "ok": bool(mdb),
        "detail": scan_detail,
    })

    kit = check_solid_deploy_kit()
    items.append({
        "id": "deploy_kit",
        "label": "Solid 内置现场组件（无需酒店另装）",
        "ok": kit.get("ok", False),
        "detail": kit.get("detail", ""),
    })

    drv = check_access_driver()
    mdbtools_ok = kit.get("mdbtools", False)
    parser_ok = kit.get("access_parser", False)
    access_ok = drv["ok"] or mdbtools_ok or parser_ok
    items.append({
        "id": "access_driver",
        "label": "读取门锁数据库",
        "ok": access_ok,
        "detail": (
            drv["hint"]
            if drv["ok"]
            else (
                "将使用 Solid 内置 mdbtools 导入（免安装）"
                if mdbtools_ok
                else (
                    "将使用 Python access_parser 导入（纯 Python 免安装）"
                    if parser_ok
                    else "将尝试安装部署包内置驱动或 mdbtools，或请补全 _deploy_deps/mdbtools"
                )
            )
        ),
    })

    items.append({
        "id": "mdb_found",
        "label": "门锁数据库路径",
        "ok": bool(mdb and os.path.isfile(mdb)),
        "detail": mdb or "未找到；请浏览选择旧系统目录下的门锁数据库",
    })

    if mdb and os.path.isfile(mdb):
        probe = probe_mdb_readable(mdb)
        items.append({
            "id": "mdb_readable",
            "label": "数据库可读取",
            "ok": probe.get("ok", False),
            "detail": (
                f"{probe.get('table_count', 0)} 张表 — {probe.get('msg', probe.get('error', ''))}"
            ),
        })
    else:
        items.append({
            "id": "mdb_readable",
            "label": "数据库可读取",
            "ok": False,
            "detail": "需先指定有效 MDB 路径",
        })

    # USB 发卡器：仅检查常见盘符下是否有可移动盘（不保证已插密钥盘）
    usb_hint = "请在本步完成后插入发卡器 U 盘，再点 USB 门锁迁移"
    try:
        if platform.system() == "Windows":
            import ctypes
            import string as s

            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            removable = []
            for letter in s.ascii_uppercase:
                if bitmask & 1:
                    drive = f"{letter}:\\"
                    if ctypes.windll.kernel32.GetDriveTypeW(drive) == 2:
                        removable.append(drive)
                bitmask >>= 1
            items.append({
                "id": "usb_ready",
                "label": "USB 可移动盘（发卡器或密钥盘）",
                "ok": len(removable) > 0,
                "detail": (
                    f"已检测到: {', '.join(removable)}" if removable else usb_hint
                ),
            })
        else:
            items.append({
                "id": "usb_ready",
                "label": "USB 可移动盘",
                "ok": True,
                "detail": usb_hint,
            })
    except Exception:
        items.append({
            "id": "usb_ready",
            "label": "USB 可移动盘",
            "ok": True,
            "detail": usb_hint,
        })

    bak = deploy_paths.cardlock_backup_dir()
    items.append({
        "id": "proUSB_bak",
        "label": "proUSB 备份目录（可选）",
        "ok": os.path.isdir(bak),
        "detail": bak if os.path.isdir(bak) else "未找到；若旧电脑有备份请复制到前台机",
    })

    usb_candidates = [c for c in candidates if str(c.get("ext", "")).lower() in (".mdb", ".accdb")]
    items.append({
        "id": "fallback_candidates",
        "label": "可替换旧库候选",
        "ok": bool(candidates),
        "detail": (
            "；".join(f"{os.path.basename(c.get('path',''))}({c.get('detail','')})" for c in candidates[:4])
            if candidates else "未找到其它 .db/.mdb/.dbf 候选；建议选择旧系统安装目录做深度扫描"
        ),
    })

    items.append({
        "id": "remote_assist",
        "label": "厂家远程协助通道",
        "ok": _tcp_probe("api.telegram.org", 443) or _tcp_probe("1.1.1.1", 443),
        "detail": "网络可用，可发送诊断包" if (_tcp_probe("api.telegram.org", 443) or _tcp_probe("1.1.1.1", 443)) else "当前网络不可达；请先准备手机热点或离线拷贝诊断报告",
    })

    all_critical_ok = all(
        i["ok"]
        for i in items
        if i["id"] in ("deploy_kit", "access_driver", "mdb_found", "mdb_readable")
    )
    return {
        "ok": all_critical_ok,
        "mdb_path": mdb,
        "items": items,
        "drivers": drv,
        "candidates": candidates,
        "candidate_mdb_count": len(usb_candidates),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def format_preflight_report(report: Dict[str, Any]) -> str:
    lines = ["── 前台预检 ──"]
    for it in report.get("items", []):
        mark = "✓" if it.get("ok") else "○"
        lines.append(f"  [{mark}] {it.get('label', '')}")
        if it.get("detail"):
            lines.append(f"      {it['detail']}")
    lines.append("")
    if report.get("ok"):
        lines.append("✅ 关键项已通过，可执行「导入旧库数据」。")
    else:
        lines.append("⚠️ 请先解决标 ○ 的关键项；可点「启用内置组件」或换候选旧库。")
        lines.append("现场兜底顺序：1) 换门锁数据库或主业务库  2) 启用内置 MDB 工具  3) 插 USB 密钥盘  4) 发卡嗅探  5) 发诊断包给厂家。")
    cands = report.get("candidates") or []
    if cands:
        lines.append("")
        lines.append("── 候选旧库（失败时按顺序尝试）──")
        for c in cands[:6]:
            mark = "✓" if c.get("ok") else "○"
            lines.append(f"  [{mark}] {c.get('path')} — {c.get('detail', '')}")
    return "\n".join(lines)


def open_access_engine_download() -> None:
    import webbrowser
    webbrowser.open(ACCESS_ENGINE_URL)
