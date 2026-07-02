"""
handover_importer.py — PMS 端握手包导入器

职责
====
导入 SolidCollector 生成的 .solidhandover 交接包。

核心原则：
★ 不判断、不降级、不试错
★ 包里的 mode 是啥就用啥
★ 导入失败自动回滚到导入前状态

功能：
1. 解压 + 校验 MANIFEST
2. 安装 profile（写本地路径到 system_config）
3. 合并房间/客人数据（INSERT OR IGNORE）
4. 恢复序列号状态
5. 安装 DLL + bridge32 到 lock_runtime/
6. 完整回滚机制
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SUPPORTED_VERSIONS = ["1.0"]

# system_config 中与门锁接管相关的键名前缀
_TAKEOVER_PREFIX = "lock_takeover_"

# profile 安装目标目录（相对 PMS 根目录）
_PROFILE_DIR = Path(__file__).resolve().parent.parent / "lock_adapters" / "profile" / "profiles"

# 运行时文件安装目录（DLL + bridge32）
_RUNTIME_DIR = Path(__file__).resolve().parent.parent / "lock_runtime"


def _db():
    """延迟导入 database 模块，避免循环引用。"""
    from database import db
    return db


class HandoverImporter:
    """PMS 端握手包导入器。"""

    def __init__(self):
        self._tmpdir: Optional[str] = None
        self._data: Optional[dict] = None
        self._profile_path: Optional[Path] = None
        self._rooms_key = ""  # 备份用：导出前 rooms.last_seq 快照
        self._backup_takeover_keys: Dict[str, str] = {}
        self._backup_room_seqs: Dict[str, int] = {}
        self._did_import = False
        self._imported_rooms_count = 0
        self._imported_guests_count = 0

    # ────────────── 公开入口 ──────────────

    def run(self, handover_path: str) -> Dict[str, Any]:
        """导入握手包。

        Args:
            handover_path: .solidhandover 文件的完整路径。

        Returns:
            {"ok": bool, "errors": list, "mode": str, "brand": str,
             "profile_file": str, "rooms_imported": int, "guests_imported": int}
        """
        errors: List[str] = []

        try:
            # 1. 备份当前配置（在修改之前）
            self._backup_current_config()

            # 2. 加载 + 校验
            self._data = self._load_package(handover_path)
            errs = self._validate(self._data)
            if errs:
                errors.extend(errs)
                return self._result(False, errors)

            # 3. 安装 profile
            profile_path = self._install_profile(self._data.get("profile", {}))
            self._profile_path = profile_path

            # 4. 合并房间/客人数据
            self._imported_rooms_count = self._merge_rooms(self._data.get("room_data", []))
            self._imported_guests_count = self._merge_guests(self._data.get("guest_data", []))

            # 5. 安装序列号状态
            self._install_lock_state(self._data.get("lock_state", {}))

            # 6. 安装运行时文件（DLL + bridge32）
            self._install_runtime(self._data)

            # 7. 写 system_config
            mode = self._data.get("mode", "dll_direct")
            brand = self._data.get("brand", "unknown")
            adapter_id = self._data.get("adapter_id", "")
            self._write_system_config(
                brand, adapter_id, mode, handover_path, str(profile_path), self._data,
            )

            self._did_import = True
            logger.info("握手包导入成功: brand=%s mode=%s rooms=%d guests=%d",
                        brand, mode, self._imported_rooms_count, self._imported_guests_count)
            return self._result(True, errors, mode=mode, brand=brand,
                                profile_file=str(profile_path),
                                rooms_imported=self._imported_rooms_count,
                                guests_imported=self._imported_guests_count)

        except Exception as exc:
            logger.error("握手包导入异常: %s", exc, exc_info=True)
            errors.append(str(exc))
            self.rollback()
            return self._result(False, errors)

    def rollback(self) -> Dict[str, Any]:
        """回滚到导入前的状态。

        Returns:
            {"ok": bool, "errors": list}
        """
        errors: List[str] = []
        try:
            # 1. 恢复 system_config
            self._restore_system_config()

            # 2. 删除已导入的 profile
            if self._profile_path and self._profile_path.is_file():
                self._profile_path.unlink(missing_ok=True)
                logger.info("已删除 profile: %s", self._profile_path)

            # 3. 删除 lock_runtime/ 目录
            if _RUNTIME_DIR.is_dir():
                shutil.rmtree(str(_RUNTIME_DIR), ignore_errors=True)
                logger.info("已删除运行时文件: %s", _RUNTIME_DIR)

            # 4. 恢复房间 seq
            self._restore_room_seqs()

            self._did_import = False
            logger.info("握手包已回滚")
            return self._result(True, errors)

        except Exception as exc:
            logger.error("回滚异常: %s", exc, exc_info=True)
            errors.append(str(exc))
            return self._result(False, errors)

    # ────────────── 包加载与校验 ──────────────

    def _load_package(self, handover_path: str) -> dict:
        """解压并解析握手包。"""
        fpath = Path(handover_path)
        if not fpath.is_file():
            raise FileNotFoundError(f"握手包文件不存在: {handover_path}")

        self._tmpdir = tempfile.mkdtemp(prefix="handover_import_")

        data: Dict[str, Any] = {
            "handover_path": str(fpath),
        }

        with zipfile.ZipFile(str(fpath), "r") as zf:
            # 解压到临时目录
            zf.extractall(self._tmpdir)

            # 解析各个 JSON 文件
            json_files = {
                "MANIFEST.json": "manifest",
                "lock_profile.json": "profile",
                "room_data.json": "room_data",
                "guest_data.json": "guest_data",
                "lock_state.json": "lock_state",
            }
            for json_name, key in json_files.items():
                json_path = os.path.join(self._tmpdir, json_name)
                if os.path.isfile(json_path):
                    with open(json_path, "r", encoding="utf-8") as f:
                        data[key] = json.load(f)
                else:
                    data[key] = {}

            # 可选：button_map / workflow / deployment_context
            btn_map_path = os.path.join(self._tmpdir, "button_map.json")
            if os.path.isfile(btn_map_path):
                with open(btn_map_path, "r", encoding="utf-8") as f:
                    data["button_map"] = json.load(f)
            else:
                data["button_map"] = {}

            wf_path = os.path.join(self._tmpdir, "workflow.json")
            if os.path.isfile(wf_path):
                with open(wf_path, "r", encoding="utf-8") as f:
                    data["workflow"] = json.load(f)
            else:
                data["workflow"] = {}

            dep_path = os.path.join(self._tmpdir, "deployment_context.json")
            if os.path.isfile(dep_path):
                with open(dep_path, "r", encoding="utf-8") as f:
                    data["deployment_context"] = json.load(f)
            else:
                data["deployment_context"] = {}

        manifest = data.get("manifest", {})
        data["mode"] = manifest.get("mode", data.get("profile", {}).get("mode", "dll_direct"))
        data["brand"] = manifest.get("brand", data.get("profile", {}).get("brand", "unknown"))
        data["adapter_id"] = manifest.get(
            "adapter_id",
            data.get("profile", {}).get("adapter_id", ""),
        )

        return data

    def _validate(self, data: dict) -> List[str]:
        """校验握手包内容，返回错误列表。"""
        errors: List[str] = []
        manifest = data.get("manifest", {})

        hv = manifest.get("handover_version", "")
        if not hv:
            errors.append("握手包缺少 handover_version 字段")
        elif hv not in _SUPPORTED_VERSIONS:
            errors.append(f"握手包版本 {hv} 不被当前 PMS 支持（支持: {_SUPPORTED_VERSIONS}），请升级 PMS")

        mode = manifest.get("mode", data.get("profile", {}).get("mode", ""))
        if mode not in ("dll_direct", "parasitic", "serial"):
            errors.append(f"无效的发卡模式: {mode}（必须为 dll_direct / parasitic / serial）")

        profile = data.get("profile", {})
        if not profile:
            errors.append("握手包缺少 lock_profile.json（卡协议配置）")

        # 校验 DLL 依赖不缺失
        deps = manifest.get("dll_dependencies", {})
        for dll_name, dll_info in deps.items():
            missing = dll_info.get("missing", [])
            if missing:
                logger.warning("DLL 依赖缺失: %s 缺少 %s", dll_name, ", ".join(missing))

        return errors

    # ────────────── 安装各部件 ──────────────

    def _install_profile(self, profile: dict) -> Path:
        """写 profile 到 profiles/ 目录，返回文件路径。"""
        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        # 生成文件名：handover_{brand}_{timestamp}.json
        brand = profile.get("brand", "unknown").replace(" ", "_").lower()
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"handover_{brand}_{ts}.json"
        fpath = _PROFILE_DIR / fname

        fpath.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("profile 已安装: %s", fpath)
        return fpath

    @staticmethod
    def _safe_int(val: Any, default: int = 0) -> int:
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    def _merge_rooms(self, room_data: List[dict]) -> int:
        """合并房间数据（INSERT OR IGNORE + 补全锁号/楼栋楼层）。"""
        count = 0
        for room in room_data:
            room_id = room.get("room_id", "")
            if not room_id:
                continue
            lock_no = str(room.get("lock_no", "") or "")
            building = str(
                room.get("building_no", "")
                or room.get("building", "")
                or room.get("bld_no", "")
                or ""
            )
            bld_no = self._safe_int(room.get("bld_no") or room.get("building_no"), 1)
            flr_no = self._safe_int(room.get("flr_no") or room.get("floor_no"), 0)
            rom_id = self._safe_int(room.get("rom_id"), 0)
            floor = str(
                room.get("floor")
                or room.get("floor_no")
                or (str(flr_no) if flr_no else "")
            )
            room_type = str(room.get("room_type") or "标准间")
            last_seq = int(room.get("current_seq", 0) or 0)

            try:
                _db().execute(
                    """INSERT OR IGNORE INTO rooms
                       (room_id, lock_no, building, floor, room_type,
                        bld_no, flr_no, rom_id, last_seq, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'VC')""",
                    (
                        room_id, lock_no, building, floor, room_type,
                        bld_no, flr_no, rom_id, last_seq,
                    ),
                )
                _db().execute(
                    """
                    UPDATE rooms SET
                        lock_no=CASE WHEN (lock_no IS NULL OR lock_no='') AND ?<>'' THEN ? ELSE lock_no END,
                        building=CASE WHEN (building IS NULL OR building='') AND ?<>'' THEN ? ELSE building END,
                        floor=CASE WHEN (floor IS NULL OR floor='') AND ?<>'' THEN ? ELSE floor END,
                        room_type=CASE WHEN (room_type IS NULL OR room_type='') AND ?<>'' THEN ? ELSE room_type END,
                        bld_no=CASE WHEN COALESCE(bld_no,0)=0 AND ?>0 THEN ? ELSE bld_no END,
                        flr_no=CASE WHEN COALESCE(flr_no,0)=0 AND ?>0 THEN ? ELSE flr_no END,
                        rom_id=CASE WHEN COALESCE(rom_id,0)=0 AND ?>0 THEN ? ELSE rom_id END,
                        last_seq=CASE WHEN ?>0 THEN ? ELSE last_seq END
                    WHERE room_id=?
                    """,
                    (
                        lock_no, lock_no,
                        building, building,
                        floor, floor,
                        room_type, room_type,
                        bld_no, bld_no,
                        flr_no, flr_no,
                        rom_id, rom_id,
                        last_seq, last_seq,
                        room_id,
                    ),
                )
                count += 1
            except Exception as exc:
                logger.warning("合并房间 %s 失败: %s", room_id, exc)

        logger.info("合并房间: %d 条", count)
        return count

    def _merge_guests(self, guest_data: List[dict]) -> int:
        """合并在住客人：写入 checkout_time，并将对应房间标为 INHOUSE。"""
        count = 0
        for guest in guest_data:
            room_id = guest.get("room_id", "")
            if not room_id:
                continue
            name = str(guest.get("guest_name", "") or guest.get("name", "") or "未知")
            checkin = str(
                guest.get("checkin_time", "")
                or _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            )
            checkout = str(guest.get("checkout_time", "") or guest.get("checkout", "") or "")
            phone = str(guest.get("phone", "") or "")
            id_card = str(guest.get("id_card", "") or guest.get("idcard", "") or "")
            status = "INHOUSE"

            try:
                existing = _db().execute(
                    "SELECT id FROM guests WHERE room_id=? AND status='INHOUSE' "
                    "ORDER BY id DESC LIMIT 1",
                    (room_id,),
                ).fetchone()
                if existing:
                    _db().execute(
                        """UPDATE guests SET name=?, checkin_time=?, checkout_time=?,
                           phone=?, id_card=?, status=? WHERE id=?""",
                        (name, checkin, checkout or None, phone, id_card, status, existing[0]),
                    )
                else:
                    _db().execute(
                        """INSERT INTO guests
                           (room_id, name, checkin_time, checkout_time, phone, id_card, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (room_id, name, checkin, checkout or None, phone, id_card, status),
                    )
                _db().execute(
                    "UPDATE rooms SET status='INHOUSE' WHERE room_id=?",
                    (room_id,),
                )
                count += 1
            except Exception as exc:
                logger.warning("合并客人 %s 失败: %s", room_id, exc)

        logger.info("合并客人: %d 条", count)
        return count

    def _install_lock_state(self, lock_state: dict):
        """恢复序列号状态。

        lock_state 格式：
        {
            "last_seq_master": 3,
            "last_seq_guest": 7,
            "room_seqs": {
                "301": {"last_seq": 7, "last_card_hex": "C92B..."},
            }
        }
        """
        # 系统卡 seq
        for key in ("master", "building", "floor", "emergency", "group", "auth", "guest"):
            val = lock_state.get(f"last_seq_{key}")
            if val is not None:
                try:
                    _db().set_config(f"last_seq_{key}", str(val))
                except Exception as exc:
                    logger.warning("设置系统卡 seq[%s]=%d 失败: %s", key, val, exc)

        # 房间级 seq
        room_seqs = lock_state.get("room_seqs", {})
        for room_id, seq_info in room_seqs.items():
            last_seq = seq_info.get("last_seq") if isinstance(seq_info, dict) else seq_info
            if last_seq is not None:
                try:
                    _db().execute(
                        "UPDATE rooms SET last_seq=? WHERE room_id=?",
                        (int(last_seq), room_id),
                    )
                except Exception as exc:
                    logger.warning("设置房间 seq[%s]=%d 失败: %s", room_id, last_seq, exc)

        logger.info("序列号状态已恢复")

    def _install_runtime(self, data: dict):
        """安装运行时文件（DLL + bridge32）。

        从解压的临时目录复制到 lock_runtime/。
        """
        if not self._tmpdir:
            return

        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

        # 复制 native_dlls/
        dll_src = os.path.join(self._tmpdir, "native_dlls")
        if os.path.isdir(dll_src):
            for fname in os.listdir(dll_src):
                src = os.path.join(dll_src, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, str(_RUNTIME_DIR / fname))
                    logger.info("已安装 DLL: %s", fname)

        # 复制 bridge32.exe
        bridge_src = os.path.join(self._tmpdir, "rfl_bridge_32.exe")
        if os.path.isfile(bridge_src):
            shutil.copy2(bridge_src, str(_RUNTIME_DIR / "rfl_bridge_32.exe"))
            logger.info("已安装 bridge32: rfl_bridge_32.exe")

    def _resolve_install_dir(self, mode: str, data: dict) -> str:
        """dll_direct / serial 用 lock_runtime/；parasitic 用原厂安装目录。"""
        deployment = data.get("deployment_context") or {}
        factory_dir = str(deployment.get("install_dir") or "")
        if mode in ("dll_direct", "serial"):
            return str(_RUNTIME_DIR.resolve())
        return factory_dir

    def _resolve_dll_path(self, mode: str, data: dict, install_dir: str) -> str:
        # serial 模式无主 DLL，返回空
        if mode == "serial":
            return ""
        profile = data.get("profile") or {}
        dll_name = (profile.get("dll") or {}).get("path", "")
        deployment = data.get("deployment_context") or {}
        if not dll_name:
            dll_name = deployment.get("loaded_dll") or ""

        if mode == "dll_direct" and dll_name:
            candidate = _RUNTIME_DIR / dll_name
            if candidate.is_file():
                return str(candidate.resolve())
        if dll_name and install_dir:
            candidate = Path(install_dir) / dll_name
            if candidate.is_file():
                return str(candidate.resolve())
        return ""

    def _write_system_config(
        self,
        brand: str,
        adapter_id: str,
        mode: str,
        handover_path: str,
        profile_path: str,
        data: dict,
    ):
        """写 system_config（含 deployment / parasitic 回放配置）。"""
        now = _dt.datetime.now().isoformat()
        deployment = data.get("deployment_context") or {}
        install_dir = self._resolve_install_dir(mode, data)
        dll_path = self._resolve_dll_path(mode, data, install_dir)

        configs: Dict[str, str] = {
            "lock_takeover_brand": brand,
            "lock_takeover_adapter_id": adapter_id,
            "lock_takeover_mode": mode,
            "lock_takeover_profile_path": profile_path,
            "lock_takeover_handover_path": handover_path,
            "lock_takeover_done_at": now,
            "lock_takeover_install_dir": install_dir,
            "lock_takeover_dll_path": dll_path,
        }

        # serial 模式另存串口参数
        if mode == "serial":
            serial_port = deployment.get("serial_port") or ""
            serial_baudrate = deployment.get("serial_baudrate") or ""
            configs["lock_takeover_serial_port"] = str(serial_port)
            configs["lock_takeover_serial_baudrate"] = str(serial_baudrate)

        hotel_name = deployment.get("hotel_name") or ""
        if hotel_name:
            configs["lock_takeover_hotel_name"] = str(hotel_name)

        dls = deployment.get("dls_co_id")
        if dls is not None and str(dls).strip() != "":
            configs["lock_takeover_dlsCoID"] = str(dls)
        hotel_id = deployment.get("hotel_id")
        if hotel_id:
            configs["lock_takeover_hotel_id"] = str(hotel_id)
        pc_id = deployment.get("pc_id")
        if pc_id:
            configs["lock_takeover_pc_id"] = str(pc_id)

        factory_dir = str(deployment.get("install_dir") or "")
        if factory_dir:
            configs["cardlockauto_install_dir"] = factory_dir

        button_map = data.get("button_map") or {}
        if button_map:
            configs["cardlockauto_button_map"] = json.dumps(button_map, ensure_ascii=False)

        workflow = data.get("workflow") or {}
        if workflow:
            configs["cardlockauto_workflow"] = json.dumps(workflow, ensure_ascii=False)

        for key, value in configs.items():
            if value is None or value == "":
                continue
            try:
                _db().set_config(key, str(value))
            except Exception as exc:
                logger.error("写系统配置 %s=%s 失败: %s", key, value, exc)

        logger.info(
            "system_config 已更新: mode=%s brand=%s install_dir=%s",
            mode, brand, install_dir,
        )

    # ────────────── 备份与回滚 ──────────────

    def _backup_current_config(self):
        """备份当前门锁配置（用于回滚）。"""
        # 备份 system_config 中的 lock_takeover_* 键
        takeover_keys = [
            "lock_takeover_brand",
            "lock_takeover_adapter_id",
            "lock_takeover_mode",
            "lock_takeover_profile_path",
            "lock_takeover_handover_path",
            "lock_takeover_done_at",
            "lock_takeover_install_dir",
            "lock_takeover_dll_path",
            "lock_takeover_dlsCoID",
            "lock_takeover_hotel_id",
            "lock_takeover_pc_id",
            "lock_takeover_hotel_name",
            "lock_takeover_serial_port",
            "lock_takeover_serial_baudrate",
            "cardlockauto_install_dir",
            "cardlockauto_button_map",
            "cardlockauto_workflow",
        ]
        # 也包括 seq 相关键
        seq_keys = [f"last_seq_{t}" for t in ("master", "building", "floor", "emergency", "group", "auth", "guest")]

        for key in takeover_keys + seq_keys:
            try:
                val = _db().get_config(key)
                if val is not None:
                    self._backup_takeover_keys[key] = val
            except Exception:
                pass

        # 备份所有房间的 last_seq
        try:
            rows = _db().execute("SELECT room_id, last_seq FROM rooms").fetchall()
            self._backup_room_seqs = {row[0]: row[1] for row in rows if row[1] is not None}
        except Exception as exc:
            logger.warning("备份房间 seq 失败: %s", exc)

        logger.info("当前门锁配置已备份（%d 个配置项, %d 个房间 seq）",
                    len(self._backup_takeover_keys), len(self._backup_room_seqs))

    def _restore_system_config(self):
        """恢复 system_config 到备份状态。"""
        # 先删除导入时写入的键
        clear_keys = [
            "lock_takeover_brand",
            "lock_takeover_adapter_id",
            "lock_takeover_mode",
            "lock_takeover_profile_path",
            "lock_takeover_handover_path",
            "lock_takeover_done_at",
            "lock_takeover_install_dir",
            "lock_takeover_dll_path",
            "lock_takeover_dlsCoID",
            "lock_takeover_hotel_id",
            "lock_takeover_pc_id",
            "lock_takeover_hotel_name",
            "lock_takeover_serial_port",
            "lock_takeover_serial_baudrate",
            "cardlockauto_install_dir",
            "cardlockauto_button_map",
            "cardlockauto_workflow",
        ]
        for key in clear_keys:
            try:
                if key not in self._backup_takeover_keys:
                    # 导入前不存在 → 直接删除
                    _db().execute("DELETE FROM system_config WHERE key=?", (key,))
            except Exception:
                pass

        # 恢复原有的键
        for key, value in self._backup_takeover_keys.items():
            try:
                _db().set_config(key, value)
            except Exception as exc:
                logger.warning("恢复配置 %s 失败: %s", key, exc)

        logger.info("system_config 已恢复")

    def _restore_room_seqs(self):
        """恢复房间 seq 到备份状态。"""
        for room_id, last_seq in self._backup_room_seqs.items():
            try:
                _db().execute("UPDATE rooms SET last_seq=? WHERE room_id=?", (last_seq, room_id))
            except Exception as exc:
                logger.warning("恢复房间 seq[%s] 失败: %s", room_id, exc)

    # ────────────── 辅助 ──────────────

    def _result(self, ok: bool, errors: List[str], **kwargs) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "ok": ok,
            "errors": errors,
        }
        result.update(kwargs)
        return result


# ────────────── [sub-g] 云端拉取导入 ──────────────


def import_from_cloud(cloud_id: str) -> Dict[str, Any]:
    """[sub-g] 从厂家云端拉取握手包并导入 PMS（一站式）。

    组合 CloudHandoverPuller.download_handover + HandoverImporter.run +
    ack_handover，让 PMS 厂家控制台只需要点一次「下载并导入」即可。

    Args:
        cloud_id: 云端握手包 ID。

    Returns:
        成功: {"ok": True, "mode": ..., "brand": ..., "rooms_imported": ...,
               "cloud_id": ..., "local_path": ...}
        失败: {"ok": False, "errors": [...], "cloud_id": ...}
    """
    from lock_deploy.cloud_handover_pull import CloudHandoverPuller  # type: ignore

    errors: List[str] = []
    puller = CloudHandoverPuller()
    if not puller.is_enabled():
        return {
            "ok": False,
            "errors": ["云端 URL 未配置（system_config.cloud_worker_url 为空）"],
            "cloud_id": cloud_id,
        }

    # 1. 下载握手包
    local_path = puller.download_handover(cloud_id)
    if local_path is None or not Path(local_path).is_file():
        errors.append(f"下载云端握手包 {cloud_id} 失败")
        return {"ok": False, "errors": errors, "cloud_id": cloud_id}

    # 2. 调用标准导入器
    importer = HandoverImporter()
    result = importer.run(str(local_path))

    # 3. ack 给云端（无论成功失败都告诉云端处理结果）
    try:
        if result.get("ok"):
            puller.ack_handover(cloud_id, status="imported",
                                detail=f"rooms={result.get('rooms_imported', 0)}")
        else:
            err_str = "; ".join(result.get("errors", []))[:200]
            puller.ack_handover(cloud_id, status="failed", detail=err_str)
    except Exception as exc:
        logger.warning("[sub-g] ack 云端握手包失败（不阻断导入）: %s", exc)

    # 把下载路径回填到结果，便于 UI 展示
    result["cloud_id"] = cloud_id
    result["local_path"] = str(local_path)
    return result
