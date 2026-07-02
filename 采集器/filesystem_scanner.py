"""
filesystem_scanner.py — 文件系统法医扫描引擎

职责：
1. 递归扫描门锁安装目录，记录所有文件信息
2. 解析 System.ini 提取 dlsCoID / HotelID / PCID / Port
3. 解析 MDB/Access 数据库，导出房间/客人表结构
4. 枚举所有 DLL 的导出函数

用法：
    from collector.filesystem_scanner import FileSystemScanner
    scanner = FileSystemScanner("D:\\智能门锁管理系统")
    report = scanner.scan()          # 返回 FileSystemReport
    report_dict = scanner.scan_to_dict()  # 返回纯字典
"""

from __future__ import annotations

import configparser
import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .forensic_schema import (
    FileInfo, SystemIniContent, MdbSummary, DllExportInfo,
    FileSystemReport,
)

logger = logging.getLogger(__name__)

SCAN_SKIP_EXTENSIONS = {".tmp", ".bak", ".log", ".ldb", ".laccdb"}


class FileSystemScanner:
    """扫描门锁安装目录，产出文件系统报告。"""

    def __init__(self, install_dir: str):
        self._root = Path(install_dir)
        if not self._root.is_dir():
            raise FileNotFoundError(f"目录不存在: {install_dir}")

    def scan(self) -> FileSystemReport:
        report = FileSystemReport()
        report.install_dir = str(self._root.resolve())

        files = self._scan_files()
        report.files = files
        report.file_count = len(files)
        report.total_size_mb = round(sum(f.size for f in files) / (1024 * 1024), 2)

        ini = self._parse_system_ini()
        if ini:
            report.system_ini = ini

        mdb = self._parse_mdb()
        if mdb:
            report.mdb_summary = mdb

        dlls = self._enumerate_dll_exports()
        report.dll_exports = dlls

        logger.info(
            "文件系统扫描完成: %s — %d 文件, %.1f MB, %d DLL",
            report.install_dir, report.file_count,
            report.total_size_mb, len(dlls),
        )
        return report

    def scan_to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self.scan())

    # ── 文件扫描 ──────────────────────────────────────────

    def _scan_files(self) -> list[FileInfo]:
        results: list[FileInfo] = []
        for entry in self._root.rglob("*"):
            if not entry.is_file():
                continue
            if entry.suffix.lower() in SCAN_SKIP_EXTENSIONS:
                continue
            try:
                stat = entry.stat()
                size = stat.st_size
                md5 = ""
                if size < 50 * 1024 * 1024:  # 超过50MB 不计算 MD5
                    md5 = self._md5_file(str(entry))
                mod_time = datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                rel_path = str(entry.relative_to(self._root))
                results.append(FileInfo(
                    path=rel_path,
                    size=size,
                    md5=md5,
                    mod_time=mod_time,
                ))
            except (OSError, PermissionError) as e:
                logger.debug("跳过 %s: %s", entry, e)
                continue
        return results

    @staticmethod
    def _md5_file(filepath: str) -> str:
        try:
            h = hashlib.md5()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    # ── System.ini 解析 ───────────────────────────────────

    def _parse_system_ini(self) -> Optional[SystemIniContent]:
        """解析门锁目录下的 System.ini（或 System.ini 的变形）。"""
        candidates = ["System.ini", "system.ini", "SYSTEM.INI",
                       "System.INI", "System.Ini"]
        ini_path = None
        for name in candidates:
            p = self._root / name
            if p.is_file():
                ini_path = p
                break

        if not ini_path:
            return None

        try:
            cp = configparser.ConfigParser(strict=False)
            cp.read(str(ini_path), encoding="gbk")
        except Exception:
            try:
                cp.read(str(ini_path), encoding="utf-8")
            except Exception:
                return None

        content = SystemIniContent()
        try:
            content.dls_co_id = cp.get("System", "dlsCoID", fallback="")
        except Exception:
            content.dls_co_id = ""
        try:
            content.hotel_id = cp.get("System", "HotelID", fallback="")
        except Exception:
            content.hotel_id = ""
        try:
            content.pc_id = cp.get("System", "PCID", fallback="")
        except Exception:
            content.pc_id = ""
        try:
            content.port = cp.get("System", "Port", fallback="")
        except Exception:
            content.port = ""

        raw = {}
        for section in cp.sections():
            raw[section] = dict(cp.items(section))
        content.raw_sections = raw

        logger.info("System.ini 解析完成: dlsCoID=%s, HotelID=%s",
                     content.dls_co_id, content.hotel_id)
        return content

    # ── MDB 解析 ──────────────────────────────────────────

    def _parse_mdb(self) -> Optional[MdbSummary]:
        mdb_files = list(self._root.glob("*.mdb")) + list(self._root.glob("*.accdb"))
        if not mdb_files:
            return None

        mdb_path = str(mdb_files[0])
        summary = MdbSummary(source=Path(mdb_path).name)

        try:
            from access_parser import AccessParser
            db = AccessParser(mdb_path)
            catalog = [str(t) for t in db.catalog
                        if not str(t).startswith("MSys")]
            summary.tables = catalog

            if "RoomInfo" in catalog:
                raw = db.parse_table("RoomInfo")
                if raw:
                    n_rows = max(len(v) for v in raw.values()) if raw else 0
                    summary.room_count = n_rows

            guest_tables = [t for t in catalog if any(kw in t.lower() for kw in
                            ("guest", "inhouse", "in_house", "hotel_card",
                             "入住", "客人", "checin"))]
            if guest_tables:
                try:
                    g_raw = db.parse_table(guest_tables[0])
                    if g_raw:
                        g_rows = max(len(v) for v in g_raw.values()) if g_raw else 0
                        summary.guest_count = g_rows
                except Exception:
                    pass

            logger.info("MDB 解析完成: %s — %d 表, %d 房, %d 客",
                         summary.source, len(summary.tables),
                         summary.room_count, summary.guest_count)
        except ImportError:
            logger.debug("access_parser 未安装，跳过 MDB 解析")
        except Exception as e:
            logger.warning("MDB 解析失败: %s", e)

        return summary

    # ── DLL 导出枚举 ──────────────────────────────────────

    def _enumerate_dll_exports(self) -> list[DllExportInfo]:
        results: list[DllExportInfo] = []
        for dll_path in self._root.glob("*.dll"):
            exports = self._peek_dll_exports(str(dll_path))
            arch = self._guess_dll_arch(str(dll_path))
            results.append(DllExportInfo(
                dll_name=dll_path.name,
                arch=arch,
                exports=exports,
            ))
        return results

    @staticmethod
    def _peek_dll_exports(dll_path: str) -> list[dict]:
        """用 Python 纯解析 PE 头读取 DLL 导出表（不加载 DLL）。"""
        results: list[dict] = []
        try:
            import struct
            with open(dll_path, "rb") as f:
                # 读 DOS header
                if f.read(2) != b"MZ":
                    return results
                f.seek(60)
                pe_offset = struct.unpack("<I", f.read(4))[0]
                f.seek(pe_offset)
                if f.read(4) != b"PE\x00\x00":
                    return results

                # COFF header
                coff = f.read(20)
                machine = struct.unpack("<H", coff[0:2])[0]
                arch_label = "32bit" if machine in (0x014C, 0x8664) else "unknown"

                # Optional header
                opt_header_size = struct.unpack("<H", coff[16:18])[0]
                f.read(opt_header_size)

                # Section headers
                num_sections = struct.unpack("<H", coff[2:4])[0]
                sections = []
                for _ in range(num_sections):
                    sec = f.read(40)
                    sections.append({
                        "name": sec[:8].rstrip(b"\x00").decode("ascii", errors="replace"),
                        "virtual_address": struct.unpack("<I", sec[12:16])[0],
                        "raw_offset": struct.unpack("<I", sec[20:24])[0],
                    })

                # Data directories
                data_dir_offset = pe_offset + 24 + opt_header_size
                f.seek(data_dir_offset)
                export_rva = struct.unpack("<I", f.read(4))[0]
                export_size = struct.unpack("<I", f.read(4))[0]

                if export_rva == 0 or export_size == 0:
                    return results

                # 定位导出表
                export_offset = None
                for sec in sections:
                    va = sec["virtual_address"]
                    raw = sec["raw_offset"]
                    if raw == 0:
                        continue
                    if va <= export_rva < va + export_size + 4096:
                        export_offset = raw + (export_rva - va)
                        break

                if export_offset is None:
                    return results

                f.seek(export_offset)
                _ = f.read(40)  # Export Directory Table header
                num_functions = struct.unpack("<I", f.read(4))[0]
                num_names = struct.unpack("<I", f.read(4))[0]
                func_rva = struct.unpack("<I", f.read(4))[0]
                name_rva = struct.unpack("<I", f.read(4))[0]
                ordinal_rva = struct.unpack("<I", f.read(4))[0]

                if num_names == 0:
                    return results

                # 读名字
                name_offsets = []
                for sec in sections:
                    if sec["virtual_address"] <= name_rva:
                        base = sec["raw_offset"] + (name_rva - sec["virtual_address"])
                        f.seek(base)
                        for _ in range(min(num_names, 100)):  # 最多读100个
                            name_offsets.append(struct.unpack("<I", f.read(4))[0])
                        break

                for na in name_offsets:
                    for sec in sections:
                        if sec["virtual_address"] <= na:
                            real_offset = sec["raw_offset"] + (na - sec["virtual_address"])
                            f.seek(real_offset)
                            name_bytes = bytearray()
                            for _ in range(256):
                                b = f.read(1)
                                if b == b"\x00":
                                    break
                                name_bytes.extend(b)
                            try:
                                name = name_bytes.decode("ascii", errors="replace")
                                if name.strip():
                                    results.append({"name": name})
                            except Exception:
                                pass
                            break

        except Exception as e:
            logger.debug("PE 解析失败 %s: %s", dll_path, e)

        return results

    @staticmethod
    def _guess_dll_arch(dll_path: str) -> str:
        try:
            import struct
            with open(dll_path, "rb") as f:
                if f.read(2) != b"MZ":
                    return "unknown"
                f.seek(60)
                pe_offset = struct.unpack("<I", f.read(4))[0]
                f.seek(pe_offset + 4)
                machine = struct.unpack("<H", f.read(2))[0]
                if machine == 0x014C:
                    return "32bit"
                elif machine == 0x8664:
                    return "64bit"
                return f"0x{machine:04X}"
        except Exception:
            return "unknown"
