"""
handover_packager.py — Collector 端打包器

职责
====
采集完成后，将所有学习到的数据打包为 .solidhandover 交接包：

1. 组装元数据 + profile + 房间/客人数据 + 序列号状态
2. 收集原厂 DLL（dll_direct 模式需要）
3. 递归扫描 DLL 依赖 + 记录缺失
4. 写入 MANIFEST.json（含文件校验和）
5. 打包为 zip → 重命名为 .solidhandover
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .handover_package import (
    HANDOVER_BRIDGE_EXE,
    HANDOVER_DLL_DIR,
    HANDOVER_INTERNAL_FILES,
    build_manifest,
    file_sha256,
)

logger = logging.getLogger(__name__)


# ====================================================================
# DLL 依赖递归扫描
# ====================================================================


def _scan_dll_imports(dll_path: str, search_dirs: List[str]) -> Dict[str, Any]:
    """用 pefile 扫描 DLL 的 IMAGE_IMPORT_DESCRIPTOR，递归分析依赖。

    Args:
        dll_path: 主 DLL 绝对路径。
        search_dirs: 搜索依赖 DLL 的目录列表。

    Returns:
        {
            "missing": ["dll_a.dll", "dll_b.dll"],
            "found": {"dll_c.dll": "/path/to/dll_c.dll"}
        }
    """
    result: Dict[str, Any] = {"found": {}, "missing": []}
    scanned: set = set()

    def _scan_one(dll_abs_path: str, depth: int = 0):
        if depth > 5:  # 限制递归深度
            return
        dll_name = os.path.basename(dll_abs_path)
        if dll_name.lower() in scanned:
            return
        scanned.add(dll_name.lower())

        try:
            import pefile
        except ImportError:
            logger.warning("pefile 未安装，跳过 DLL 依赖扫描")
            return

        try:
            pe = pefile.PE(dll_abs_path)
            if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
                for entry in pe.DIRECTORY_ENTRY_IMPORT:
                    dep_name = entry.dll.decode("utf-8", errors="ignore").lower()
                    if dep_name in scanned:
                        continue
                    # 在搜索目录中找依赖
                    dep_path = None
                    for sd in search_dirs:
                        candidate = os.path.join(sd, dep_name)
                        if os.path.isfile(candidate):
                            dep_path = candidate
                            break
                    if dep_path:
                        result["found"][dep_name] = dep_path
                        _scan_one(dep_path, depth + 1)
                    else:
                        # 跳过系统 DLL
                        system_prefixes = ("api-", "ext-", "kernel32", "user32", "gdi32",
                                           "advapi32", "shell32", "ole32", "oleaut32",
                                           "comdlg32", "comctl32", "ws2_32", "msvcrt",
                                           "ucrtbase", "vcruntime", "winmm")
                        if not any(dep_name.startswith(p) for p in system_prefixes):
                            if dep_name not in scanned:
                                result["missing"].append(dep_name)
            pe.close()
        except Exception as exc:
            logger.debug("扫描 DLL 依赖 %s 异常: %s", os.path.basename(dll_abs_path), exc)

    _scan_one(dll_path)
    return result


# ====================================================================
# HandoverPackager
# ====================================================================


class HandoverPackager:
    """采集结果的 .solidhandover 打包器。"""

    def __init__(self, learn_result: dict, mode: str, install_dir: str = ""):
        """
        Args:
            learn_result: 采集学习结果（含 profile、品牌等信息）。
            mode: 发卡模式，来自 PathProber。
            install_dir: 原厂门锁软件安装目录（用于收集 DLL）。
        """
        self._learn = learn_result
        self._mode = mode
        self._install_dir = install_dir
        self._profile: dict = learn_result.get("profile", {})
        self._brand = (
            learn_result.get("brand")
            or self._profile.get("brand")
            or "unknown"
        )
        self._adapter_id = (
            learn_result.get("adapter_id")
            or self._profile.get("adapter_id")
            or ""
        )
        self._deployment: dict = learn_result.get("deployment_context", {})

        # 各数据部分
        self._room_data: List[dict] = []
        self._guest_data: List[dict] = []
        self._lock_state: dict = {}
        self._button_map: dict = learn_result.get("button_map", {})
        self._workflow: dict = learn_result.get("workflow", {})

        # 运行时文件
        self._dll_sources: List[str] = []  # 源路径
        self._bridge32_source: str = ""    # bridge32.exe 源路径

        # 毕业报告（可选，由 graduation_coach 生成）
        self._graduation_report: Optional[Dict[str, Any]] = None
        self._dll_traces: list = learn_result.get("dll_traces", [])
        self._token_matrix: dict = learn_result.get("token_matrix", {})
        self._field_checklist: dict = learn_result.get("field_checklist", {})
        self._evidence_level: str = learn_result.get("evidence_level", "hex_only")

        # 打包缓存
        self._tmpdir: Optional[str] = None

    def add_room_data(self, rooms: List[dict]):
        """添加房间数据（含 current_seq）。"""
        self._room_data = rooms

    def add_guest_data(self, guests: List[dict]):
        """添加在住客人数据。"""
        self._guest_data = guests

    def add_lock_state(self, seq_states: dict):
        """添加序列号状态。

        seq_states 格式：
        {
            "last_seq_master": 3,
            "last_seq_guest": 7,
            "room_seqs": {
                "301": {"last_seq": 7, "last_card_hex": "C92B..."},
                ...
            }
        }
        """
        self._lock_state = seq_states

    def add_button_map(self, btn_map: dict):
        """添加 parasitic 模式的按钮映射。"""
        self._button_map = btn_map

    def add_workflow(self, workflow: dict):
        """添加 parasitic 模式的发卡工作流。"""
        self._workflow = workflow

    def collect_dlls(self, dll_names: List[str]):
        """收集原厂 DLL。

        dll_direct 模式需要。从安装目录复制 DLL 文件。

        Args:
            dll_names: 需要收集的 DLL 文件名列表（如 ["V9RFL.dll", "d12.dll"]）。
        """
        if self._mode != "dll_direct":
            return

        if not self._install_dir or not os.path.isdir(self._install_dir):
            logger.warning("安装目录无效，无法收集 DLL: %s", self._install_dir)
            return

        for name in dll_names:
            src = os.path.join(self._install_dir, name)
            if os.path.isfile(src):
                self._dll_sources.append(src)
            else:
                logger.warning("DLL 不存在: %s", src)

    def set_bridge32(self, bridge32_path: str):
        """设置 bridge32.exe 源路径。"""
        if os.path.isfile(bridge32_path):
            self._bridge32_source = bridge32_path
        else:
            logger.warning("bridge32.exe 不存在: %s", bridge32_path)

    def set_field_checklist(self, checklist: dict):
        self._field_checklist = checklist

    def set_dll_traces(self, traces: list):
        self._dll_traces = traces

    def set_token_matrix(self, matrix: dict):
        self._token_matrix = matrix

    def set_evidence_level(self, level: str):
        self._evidence_level = level

    def set_graduation_report(self, report: Dict[str, Any]):
        """设置毕业评估报告（写入 MANIFEST.json 的 graduation_report 段）。

        Args:
            report: build_graduation_report() 返回的字典。
        """
        self._graduation_report = report

    def build(self, output_path: str, hotel_name: str = "") -> str:
        """打包并写入 .solidhandover 文件。

        Args:
            output_path: 目标路径（目录或完整文件路径）。
            hotel_name: 酒店名（用于文件名）。

        Returns:
            生成的 .solidhandover 文件完整路径。

        注意：MANIFEST.json 的 cloud_upload_url / cloud_uploaded_at /
        cloud_task_id 三个字段不在打包时填，而是由 BuildWorker 在云端
        回传成功后通过 cloud_handover.write_cloud_meta_to_manifest()
        回写到 zip 内。打包阶段先留空字段（占位 None），让 PMS 端拉取
        后能识别"这个包来自云端通道"。
        """
        # 确定输出文件名
        if output_path.lower().endswith(".solidhandover"):
            final_path = output_path
        else:
            name = hotel_name.replace(" ", "_").strip() or "handover"
            if not name.endswith(".solidhandover"):
                name += ".solidhandover"
            final_path = os.path.join(output_path, name)

        # 创建临时目录
        self._tmpdir = tempfile.mkdtemp(prefix="handover_")
        try:
            # 1. 写入内部 JSON 文件
            self._write_json_files()

            # 2. 收集 DLL
            dll_deps: Dict[str, Any] = {}
            if self._mode == "dll_direct" and self._dll_sources:
                dll_dir = os.path.join(self._tmpdir, HANDOVER_DLL_DIR)
                os.makedirs(dll_dir, exist_ok=True)
                for src in self._dll_sources:
                    dst = os.path.join(dll_dir, os.path.basename(src))
                    shutil.copy2(src, dst)

                    # 扫描 DLL 依赖
                    search_dirs = [os.path.dirname(src), self._install_dir]
                    for dep_name, dep_path in _scan_dll_imports(src, search_dirs).get("found", {}).items():
                        dep_dst = os.path.join(dll_dir, os.path.basename(dep_path))
                        if not os.path.isfile(dep_dst):
                            shutil.copy2(dep_path, dep_dst)
                    deps_info = _scan_dll_imports(src, search_dirs)
                    dll_deps[os.path.basename(src)] = deps_info

            # 3. 复制 bridge32.exe
            if self._bridge32_source:
                shutil.copy2(self._bridge32_source, os.path.join(self._tmpdir, HANDOVER_BRIDGE_EXE))
            else:
                logger.error("bridge32.exe 源文件缺失，握手包将无法独立桥接")
                manifest["bridge32_missing"] = True

            # 4. 计算校验和 + 生成 MANIFEST
            checksums: Dict[str, str] = {}
            for root, _dirs, files in os.walk(self._tmpdir):
                for f in files:
                    fpath = os.path.join(root, f)
                    rel = os.path.relpath(fpath, self._tmpdir)
                    checksums[rel] = file_sha256(fpath)

            manifest = build_manifest(
                brand=self._brand,
                mode=self._mode,
                file_checksums=checksums,
                dll_dependencies=dll_deps,
                graduation_report=self._graduation_report,
                evidence_level=self._evidence_level,
            )
            if self._adapter_id:
                manifest["adapter_id"] = self._adapter_id
            # [sub-g] 云端通道占位字段：打包阶段留空，云端回传成功后由
            # cloud_handover.write_cloud_meta_to_manifest() 回写。让 PMS 端
            # 端拉取时能区分"该包来自云端通道"vs"该包来自 U 盘导入"。
            manifest["cloud_upload_url"] = None
            manifest["cloud_uploaded_at"] = None
            manifest["cloud_task_id"] = None
            manifest_path = os.path.join(self._tmpdir, "MANIFEST.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            # 5. 打包为 zip
            with zipfile.ZipFile(final_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(self._tmpdir):
                    for f in files:
                        fpath = os.path.join(root, f)
                        rel = os.path.relpath(fpath, self._tmpdir)
                        zf.write(fpath, rel)

            logger.info("握手包已生成: %s", final_path)
            return final_path

        finally:
            # 清理临时目录
            if self._tmpdir and os.path.isdir(self._tmpdir):
                shutil.rmtree(self._tmpdir, ignore_errors=True)
                self._tmpdir = None

    def _write_json_files(self):
        """将所有数据部分写入临时目录的 JSON 文件。"""
        # lock_profile 必需 — PMS 导入校验
        profile_path = os.path.join(self._tmpdir, "lock_profile.json")
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(self._profile or {}, f, ensure_ascii=False, indent=2)

        optional_files = {
            "room_data.json": self._room_data,
            "guest_data.json": self._guest_data,
            "lock_state.json": self._lock_state,
        }
        for name, data in optional_files.items():
            if data:
                fpath = os.path.join(self._tmpdir, name)
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

        if self._button_map:
            fpath = os.path.join(self._tmpdir, "button_map.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(self._button_map, f, ensure_ascii=False, indent=2)

        if self._workflow:
            fpath = os.path.join(self._tmpdir, "workflow.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(self._workflow, f, ensure_ascii=False, indent=2)

        if self._deployment:
            fpath = os.path.join(self._tmpdir, "deployment_context.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(self._deployment, f, ensure_ascii=False, indent=2)

        if self._dll_traces:
            traces_path = os.path.join(self._tmpdir, "dll_traces.jsonl")
            with open(traces_path, "w", encoding="utf-8") as f:
                for rec in self._dll_traces:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        if self._token_matrix:
            fpath = os.path.join(self._tmpdir, "token_matrix.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(self._token_matrix, f, ensure_ascii=False, indent=2)

        if self._field_checklist:
            fpath = os.path.join(self._tmpdir, "field_checklist.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(self._field_checklist, f, ensure_ascii=False, indent=2)

    def preview(self) -> dict:
        """生成包内容预览（用于 UI 展示）。"""
        result = {
            "brand": self._brand,
            "mode": self._mode,
            "rooms_count": len(self._room_data),
            "guests_count": len(self._guest_data),
            "card_types": list(self._profile.get("card_types", {}).keys()),
            "dll_count": len(self._dll_sources),
            "has_button_map": bool(self._button_map),
            "has_workflow": bool(self._workflow),
        }
        if self._graduation_report:
            result["graduation_report"] = self._graduation_report
        return result
