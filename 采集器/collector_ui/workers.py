"""
workers.py — Solid 学习助手后台工作线程

所有 QThread 子类集中在此，主窗口只负责连接信号。
每个 Worker 自包含：导入依赖、执行逻辑、发射结果信号。
"""

from __future__ import annotations

import json as _json
import logging
import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


# ── 辅助：惰性导入 bridge ──────────────────────────────

def _get_bridge():
    try:
        from ..collector_bridge import get_bridge
    except ImportError:
        from collector_bridge import get_bridge
    return get_bridge()


# ── DetectWorker ─────────────────────────────────────

class DetectWorker(QThread):
    """身份引擎：现场扫描 + DLL 推断 + 冲突检测 + 发卡器验证。"""
    done = Signal(object)  # IdentityResult

    def __init__(self, install_dir: str, parent=None):
        super().__init__(parent)
        self._install_dir = install_dir

    def run(self):
        from ..bridgecore.identity_engine import analyze, IdentityResult
        bridge = _get_bridge()
        try:
            result = analyze(self._install_dir, bridge=bridge)
        except Exception as e:
            logger.exception("身份分析失败")
            result = IdentityResult(install_dir=self._install_dir)
            result.bridge_hint = str(e)
            result.blockers.append("analyze_exception")
        self.done.emit(result)


# ── ReadCardWorker ───────────────────────────────────

class ReadCardWorker(QThread):
    """后台读卡，不阻塞 UI。"""
    done = Signal(bool, str)  # ok, hex_or_error

    def __init__(
        self,
        profile: Optional[dict] = None,
        *,
        orchestrator: Any = None,
        session_tag: str = "read_card",
        parent=None,
    ):
        super().__init__(parent)
        self._profile = profile
        self._orchestrator = orchestrator
        self._session_tag = session_tag

    def _read_once(self) -> tuple[bool, str]:
        profile = self._profile or {}
        channel = profile.get("channel", "")

        if channel == "serial":
            from ..bridgecore.serial_channel import SerialBridge
            serial_cfg = profile.get("serial", {})
            port = serial_cfg.get("port", "COM1")
            baudrate = serial_cfg.get("baudrate", 9600)
            sb = SerialBridge(port, baudrate)
            try:
                sb.start()
                resp = sb.direct_read_usb(d12=1, timeout=6.0)
                if resp.get("ok"):
                    hex_str = resp.get("hex", "").upper().strip()
                    if hex_str:
                        return True, hex_str
                    return False, "未检测到卡片"
                return False, resp.get("error", "串口读卡失败")
            finally:
                sb.stop()

        bridge = _get_bridge()
        bridge.start()
        dll_cfg = profile.get("dll") or {}
        read_fn = dll_cfg.get("read")
        if read_fn and (profile.get("dll") or {}).get("path"):
            resp = bridge.generic_read(read_fn, d12=1, timeout=6.0)
        else:
            resp = bridge.read_card(d12=1, timeout=5.0)
        if not resp.get("ok") or int(resp.get("ret", -1)) != 0:
            err = resp.get("out", {}).get("error", str(resp))
            return False, err
        out = resp.get("out", {})
        payload_hex = out.get("payload", out.get("card_hex", "")).upper().strip()
        if not payload_hex or payload_hex == "0" * len(payload_hex):
            return False, "未检测到卡片"
        return True, payload_hex

    def run(self):
        profile = self._profile or {}
        channel = profile.get("channel", "")
        orch = self._orchestrator if channel != "serial" else None
        try:
            if orch is not None:
                with orch.record_session(session_tag=self._session_tag):
                    ok, msg = self._read_once()
            else:
                ok, msg = self._read_once()
            self.done.emit(ok, msg)
        except Exception as e:
            self.done.emit(False, str(e))


# ── ProbeWorker ──────────────────────────────────────

class ProbeWorker(QThread):
    """探测发卡路径，不阻塞 UI。"""
    done = Signal(bool, str, dict)

    def __init__(
        self,
        install_dir: str,
        profile: dict,
        identity_hint: Optional[dict] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._install_dir = install_dir
        self._profile = profile
        self._identity_hint = identity_hint or {}

    def run(self):
        try:
            from ..bridgecore.path_prober import PathProber
            prober = PathProber()
            result = prober.probe(
                self._install_dir,
                self._profile,
                identity_hint=self._identity_hint,
            )
            mode = result["mode"]
            detail = result.get("detail", {})
            self.done.emit(mode != "failed", mode, detail)
        except Exception as e:
            self.done.emit(False, str(e), {})


# ── BuildWorker ──────────────────────────────────────

class BuildWorker(QThread):
    """打包 .solidhandover，不阻塞 UI。"""
    progress = Signal(int)
    done = Signal(bool, str)
    cloud_status = Signal(dict)

    def __init__(self, learn_result: dict, mode: str, install_dir: str,
                 output_path: str, hotel_name: str = "", parent=None,
                 graduation_report: Optional[dict] = None):
        super().__init__(parent)
        self._learn_result = learn_result
        self._mode = mode
        self._install_dir = install_dir
        self._output_path = output_path
        self._hotel_name = hotel_name
        self._graduation_report = graduation_report

    def run(self):
        try:
            # U 盘只读防御性检查
            out_dir = os.path.dirname(os.path.abspath(self._output_path)) or self._output_path
            # S1: 用实际写测试替代不可靠的 os.access
            test_file = os.path.join(out_dir, ".write_test_solid")
            try:
                with open(test_file, 'w') as tmp:
                    tmp.write('ok')
                os.remove(test_file)
            except (IOError, OSError):
                self.done.emit(False, f"U 盘只读，无法写入: {out_dir}")
                return

            from ..bridgecore.handover_packager import HandoverPackager
            packager = HandoverPackager(self._learn_result, self._mode, self._install_dir)
            if self._graduation_report:
                packager.set_graduation_report(self._graduation_report)

            evidence = self._learn_result.get("evidence_level", "hex_only")
            packager.set_evidence_level(evidence)

            for attr, setter in [
                ("dll_traces",       packager.set_dll_traces),
                ("field_checklist",  packager.set_field_checklist),
                ("token_matrix",     packager.set_token_matrix),
            ]:
                val = self._learn_result.get(attr) or {}
                if val:
                    setter(val)

            lock_state = self._learn_result.get("lock_state") or {}
            if lock_state:
                packager.add_lock_state(lock_state)

            for attr, setter in [
                ("room_data",   packager.add_room_data),
                ("guest_data",  packager.add_guest_data),
            ]:
                val = self._learn_result.get(attr, [])
                if isinstance(val, list) and val:
                    setter(val)

            for attr, setter in [
                ("button_map", packager.add_button_map),
                ("workflow",   packager.add_workflow),
            ]:
                val = self._learn_result.get(attr, {})
                if val:
                    setter(val)

            self.progress.emit(30)

            # 收集 DLL
            if self._mode == "dll_direct":
                dll_names = list(self._learn_result.get("dll_files") or [])
                if not dll_names:
                    from ..bridgecore.handover_assembler import collect_dll_file_names
                    dll_names = collect_dll_file_names(
                        self._learn_result.get("profile", {}),
                        self._install_dir,
                    )
                packager.collect_dlls(dll_names)

            self.progress.emit(60)

            bridge32 = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(__file__)))
            bridge32_path = os.path.join(bridge32, "bridge32.exe")
            packager.set_bridge32(bridge32_path)

            self.progress.emit(80)

            final_path = packager.build(self._output_path, self._hotel_name)
            size_mb = os.path.getsize(final_path) / (1024 * 1024)
            self.progress.emit(100)

            cloud_msg = self._maybe_upload_to_cloud(final_path)
            self.done.emit(True, f"{final_path}\n{size_mb:.1f} MB\n{cloud_msg}")
        except Exception as e:
            self.done.emit(False, str(e))

    def _maybe_upload_to_cloud(self, handover_path: str) -> str:
        try:
            from ..bridgecore.cloud_handover import (
                CloudHandoverClient,
                write_cloud_meta_to_manifest,
            )
            client = CloudHandoverClient()
            if not client.is_cloud_enabled():
                self.cloud_status.emit({
                    "uploaded": False, "reason": "cloud_disabled",
                    "error": "云端未配置或不可达",
                })
                return "仅本地保存（云端未启用）"

            profile = self._learn_result.get("profile", {}) or {}
            hotel_info = {
                "hotel_name": self._hotel_name or "",
                "brand": profile.get("brand", "") or self._learn_result.get("brand", ""),
                "mode": self._mode,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            result = client.upload_handover(handover_path, hotel_info)
            if result.get("ok"):
                write_cloud_meta_to_manifest(
                    handover_path,
                    cloud_url=result.get("cloud_url", ""),
                    task_id=result.get("task_id", ""),
                    uploaded_at=result.get("uploaded_at", ""),
                )
                self.cloud_status.emit({
                    "uploaded": True,
                    "task_id": result.get("task_id", ""),
                    "cloud_id": result.get("cloud_id", ""),
                    "cloud_url": result.get("cloud_url", ""),
                    "uploaded_at": result.get("uploaded_at", ""),
                })
                return "已回传云端 (task: %s)" % result.get("task_id", "")[:8]

            self.cloud_status.emit({
                "uploaded": False, "reason": "upload_failed",
                "error": result.get("error", ""), "saved_locally": True,
            })
            return "已保存本地，可稍后手动上传（%s）" % result.get("error", "")[:40]
        except Exception as exc:
            logger.warning("[sub-g] 云端回传异常（不阻断）: %s", exc, exc_info=True)
            self.cloud_status.emit({
                "uploaded": False, "reason": "exception",
                "error": str(exc), "saved_locally": True,
            })
            return "已保存本地（云端回传异常）"


# ── AnalyzeWorker ────────────────────────────────────

class AnalyzeWorker(QThread):
    log      = Signal(str)
    progress = Signal(int)
    done     = Signal(dict)

    def __init__(self, samples: list[dict], install_dir: str,
                 loaded_dll: str = "",
                 candidate_profile: Optional[dict] = None,
                 forensic_data: Optional[dict] = None,
                 allow_destructive_verify: bool = False,
                 blank_hex: str = "",
                 recording_dir: str = "",
                 parent=None):
        super().__init__(parent)
        self._samples = list(samples)
        self._install_dir = install_dir
        self._loaded_dll = loaded_dll or ""
        self._candidate_profile = candidate_profile or {}
        self._forensic = forensic_data or {}
        self._allow_destructive_verify = allow_destructive_verify
        self._blank_hex = blank_hex or ""
        self._recording_dir = recording_dir or ""

    def run(self):
        try:
            from ..bridgecore.protocol_learner import ProtocolLearner
            from ..bridgecore.analysis_types import ChannelInfo
            from ..bridgecore.profile_generator import generate_profile, save_profile
            from ..bridgecore.handover_assembler import (
                probe_result_from_install,
                enrich_profile,
                collect_dll_file_names,
                load_room_export,
            )

            self.progress.emit(10)
            self.log.emit("分析 %d 个样本..." % len(self._samples))

            learner = ProtocolLearner()
            learn_result = learner.learn_from_payloads(self._samples)

            # 配对差分分析
            for s in self._samples:
                if isinstance(s, dict):
                    blank_hex = (s.get("blank_hex") or "").strip()
                    written_hex = (s.get("written_hex") or s.get("hex") or "").strip()
                else:
                    blank_hex = getattr(s, "blank_hex", "") or ""
                    written_hex = getattr(s, "written_hex", "") or ""
                if blank_hex and written_hex:
                    try:
                        pair_result = learner.learn_from_pair(blank_hex, written_hex)
                        if pair_result and pair_result.pair_diff:
                            self.log.emit("配对差分: %d 字节变化" %
                                len([k for k,v in pair_result.pair_diff.items() if v == 'changed']))
                            for key, val in pair_result.layout.items():
                                if val is not None:
                                    learn_result.layout[key] = val
                            if pair_result.checksum_algorithm:
                                learn_result.checksum_algorithm = pair_result.checksum_algorithm
                                learn_result.checksum_offset = pair_result.checksum_offset
                                learn_result.checksum_length = pair_result.checksum_length
                            learn_result.pair_diff = pair_result.pair_diff
                            break
                    except Exception:
                        continue

            if self._recording_dir:
                from ..bridgecore.protocol_learner import merge_recordings_into_result
                merged_n = merge_recordings_into_result(
                    learner, learn_result, self._recording_dir,
                )
                if merged_n:
                    self.log.emit("JSONL 录制合并: %d 个会话" % merged_n)

            if not learn_result.card_types:
                self.done.emit({"success": False,
                    "error": "未能识别卡型，请确保至少有一组空白卡与已写卡对照样本"})
                return

            self.log.emit("识别卡型: %s" % list(learn_result.card_types.keys()))
            self.log.emit("校验和: %s" % (learn_result.checksum_algorithm or "未识别"))
            self.progress.emit(25)

            probe_result, probe_raw = probe_result_from_install(
                self._install_dir, self._loaded_dll,
            )
            if probe_result.brand_guess and probe_result.brand_guess != "auto_detected":
                self.log.emit("品牌识别: %s" % probe_result.brand_guess)
            if probe_result.dll_name:
                self.log.emit("主 DLL: %s" % probe_result.dll_name)

            # 通道类型
            channel = self._candidate_profile.get("channel", "dll")
            if channel == "serial":
                serial_cfg = self._candidate_profile.get("serial", {})
                channel_info = ChannelInfo(
                    channel_type="serial",
                    device_name="串口发卡器",
                    port=serial_cfg.get("port", ""),
                    baudrate=serial_cfg.get("baudrate", 9600),
                )
            else:
                channel_info = ChannelInfo(
                    channel_type="dll",
                    device_name="发卡器",
                    dll_path=probe_result.dll_path or "",
                )

            profile = generate_profile(learn_result, probe_result, channel_info)
            profile = enrich_profile(
                profile, probe_result, self._install_dir, probe_raw, self._forensic,
            )

            dll_traces = self._forensic.get("dll_traces") or []
            if dll_traces:
                from ..bridgecore.protocol_learner import boost_from_dll_traces
                boost_from_dll_traces(learn_result, dll_traces)

            ini = self._forensic.get("system_ini") or {}
            if ini:
                profile["site"] = {
                    "dls_co_id": getattr(ini, "dls_co_id", "") or ini.get("dls_co_id", ""),
                    "hotel_id":  getattr(ini, "hotel_id", "")  or ini.get("hotel_id", ""),
                    "pc_id":     getattr(ini, "pc_id", "")     or ini.get("pc_id", ""),
                    "port":      getattr(ini, "port", "")      or ini.get("port", ""),
                }

            profile["encryption_hints"] = {
                "dll_layer_suspected": bool(dll_traces),
                "notes": "payload 与 DLL 入参不一致时需在 PMS 侧走 DLL 发卡",
            }
            profile["site_code"] = {
                "mask": learn_result.site_mask_hex or "0x3FFF",
                "emergency_bit": learn_result.emergency_bit_hex or "0x4000",
            }
            profile["magic"] = learn_result.magic_hex or profile.get("magic") or ""

            self.progress.emit(40)

            from .constants import collector_work_dir
            profiles_dir = collector_work_dir() / "learned_profiles"
            profiles_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%y%m%d_%H%M%S")
            fname = "learned_%s.json" % ts
            fpath = str(profiles_dir / fname)
            save_profile(profile, fpath)
            self.log.emit("Profile 已生成: %s" % fname)
            self.progress.emit(70)

            # MDB 数据
            room_data_path = ""
            try:
                from ..bridgecore.room_exporter import export_room_data
                rd = export_room_data(self._install_dir, profile=profile)
                if rd.get("rooms"):
                    rd_fname = f"room_data_{ts}.json"
                    rd_path = str(profiles_dir / rd_fname)
                    with open(rd_path, "w", encoding="utf-8") as _f:
                        _json.dump(rd, _f, ensure_ascii=False, indent=2)
                    room_data_path = rd_path
                    self.log.emit("房间数据: %d 间房, %d 在住" %
                                  (len(rd["rooms"]), len(rd["guests"])))
            except Exception as e:
                self.log.emit("房间数据跳过: %s" % e)

            # 协议验证环
            protocol_verified: Optional[bool] = None
            try:
                from ..bridgecore.protocol_verifier import safe_verify_protocol
                from ..bridgecore.keepalive import KeepAlive

                channel = profile.get("channel", "dll")
                if channel == "serial":
                    serial_cfg = profile.get("serial", {})
                    port = serial_cfg.get("port", "COM1")
                    baudrate = serial_cfg.get("baudrate", 9600)
                    from ..bridgecore.serial_channel import SerialBridge
                    sb = SerialBridge(port, baudrate)
                    sb.start()
                    try:
                        vr = safe_verify_protocol(
                            sb, learn_result, d12=1,
                            allow_destructive=self._allow_destructive_verify,
                            blank_hex=self._blank_hex,
                        )
                        if vr.passed:
                            protocol_verified = True
                            self.log.emit("协议验证环（串口）通过")
                        elif vr.error and not vr.written_hex:
                            self.log.emit("协议验证跳过（串口）: %s" % vr.error)
                        else:
                            protocol_verified = False
                            self.log.emit("协议验证环（串口）失败")
                    finally:
                        sb.stop()
                else:
                    bridge = _get_bridge()
                    bridge.start()
                    dll_name = profile.get("dll", {}).get("path") or self._loaded_dll
                    if dll_name:
                        dll_path = str(Path(self._install_dir) / dll_name)
                        lr = bridge.load_dll(dll_path, [str(Path(self._install_dir).resolve())])
                        if lr.get("ok") and lr.get("loaded"):
                            init_fn = (profile.get("dll") or {}).get("init")
                            if init_fn:
                                bridge.bind_from_profile(profile)
                                ir = bridge.generic_initialize(init_fn, [0, 1])
                                ir_ok = ir.get("ok") and int(ir.get("ret", -1)) == 0
                            else:
                                ir = bridge.initialize(d12=1)
                                ir_ok = ir.get("ok") and int(ir.get("ret", -1)) == 0

                            if ir_ok:
                                with KeepAlive(bridge, interval=15):
                                    vr = safe_verify_protocol(
                                        bridge, learn_result, d12=1,
                                        allow_destructive=self._allow_destructive_verify,
                                        blank_hex=self._blank_hex,
                                    )
                                if vr.passed:
                                    protocol_verified = True
                                    self.log.emit("协议验证环通过")
                                elif vr.error and not vr.written_hex:
                                    self.log.emit("协议验证跳过: %s" % vr.error)
                                else:
                                    protocol_verified = False
                                    self.log.emit("协议验证环失败")
                            else:
                                self.log.emit("验证跳过: 发卡器初始化失败")
                        else:
                            self.log.emit("验证跳过: DLL 加载失败")
                    else:
                        self.log.emit("验证跳过: 未识别主 DLL")
            except Exception as e:
                self.log.emit("验证跳过: %s" % e)

            # 配置文件
            forensic_path = ""
            try:
                from ..forensic_schema import ForensicConfig
                fcfg = self._build_forensic_config(profile, learn_result, ts)
                forensic_path = str(profiles_dir / f"forensic_{ts}.json")
                fcfg.save(forensic_path)
                self.log.emit("配置已导出: forensic_%s.json" % ts)
            except Exception as e:
                self.log.emit("配置导出跳过: %s" % e)

            self.progress.emit(100)
            self.log.emit("学习完成")

            rooms, guests = load_room_export(room_data_path)
            dll_files = collect_dll_file_names(profile, self._install_dir, [self._loaded_dll])

            self.done.emit({
                "success": True,
                "profile_path": fpath,
                "profile": profile,
                "brand": profile.get("brand", "unknown"),
                "adapter_id": profile.get("adapter_id", ""),
                "card_types": list(learn_result.card_types.keys()),
                "confidence": round(learn_result.confidence, 2),
                "encrypted_suspected": bool(getattr(learn_result, "encrypted_suspected", False)),
                "room_data_path": room_data_path,
                "room_data": rooms,
                "guest_data": guests,
                "dll_files": dll_files,
                "forensic_path": forensic_path,
                "protocol_verified": protocol_verified,
                "dll_traces": dll_traces,
                "evidence_level": (
                    "verified_write" if protocol_verified is True
                    else ("dll_traced" if dll_traces else "hex_only")
                ),
            })
        except Exception as e:
            self.done.emit({"success": False, "error": str(e)})

    def _build_forensic_config(self, profile: dict, learn_result: Any,
                                ts: str) -> Any:
        try:
            from ..forensic_schema import ForensicConfig, IdentityInfo
        except ImportError:
            from forensic_schema import ForensicConfig, IdentityInfo
        cfg = ForensicConfig()
        if self._forensic.get("system_ini"):
            ini = self._forensic["system_ini"]
            cfg.identity = IdentityInfo(
                dls_co_id=ini.dls_co_id,
                hotel_id=ini.hotel_id,
                pc_id=ini.pc_id,
                port=ini.port,
                source="System.ini",
            )
        for key in ("filesystem", "process_tree", "ui_map", "workflow"):
            if self._forensic.get(key):
                setattr(cfg, key, self._forensic[key])
        cfg.card_protocol.confidence = round(learn_result.confidence, 2)
        cfg.card_protocol.checksum.algorithm = learn_result.checksum_algorithm or ""
        cfg.card_protocol.magic_hex = learn_result.magic_hex or ""
        cfg.card_protocol.layout = learn_result.layout
        if self._forensic.get("apdu_traces"):
            cfg.card_protocol.apdu_trace = self._forensic["apdu_traces"]
        if self._forensic.get("registry_changes"):
            cfg.registry_changes = self._forensic["registry_changes"]
        if self._forensic.get("file_changes"):
            cfg.file_changes = self._forensic["file_changes"]
        cfg.meta.brand_guess = "auto_learned"
        cfg.meta.confidence = round(learn_result.confidence, 2)
        return cfg


# ── TokenCollectionWorker ────────────────────────────

class TokenCollectionWorker(QThread):
    log      = Signal(str)
    progress = Signal(str)
    done     = Signal(bool, str, str)  # ok, msg, path

    def __init__(self, bridge, count: int = 5, parent=None):
        super().__init__(parent)
        self._bridge = bridge
        self._count = count

    def run(self):
        try:
            from ..bridgecore.token_recorder import TokenRecorder
            self._bridge.start()
            recorder = TokenRecorder(self._bridge)
            self.progress.emit("发卡中...")

            samples = recorder.collect_sequence(count=self._count, d12=1)
            if not samples:
                self.done.emit(False, "未采集到任何样本", "")
                return

            ok_count = sum(1 for s in samples if not s["error"])
            self.progress.emit(f"保存 {ok_count}/{len(samples)} 条样本...")
            path = recorder.save_samples(samples, tag="auth_token_matrix")
            if path:
                msg = f"采集 {ok_count}/{len(samples)} 张，已保存到 {path}"
                self.log.emit(msg)
                self.done.emit(True, msg, path)
            else:
                self.done.emit(False, "保存样本失败", "")
        except Exception as e:
            self.done.emit(False, str(e), "")


# ── EraseWorker ─────────────────────────────────────

class EraseWorker(QThread):
    done = Signal(bool, str)

    def run(self):
        bridge = _get_bridge()
        try:
            bridge.start()
            resp = bridge.direct_write_usb(d12=1, card_hex="0" * 32)
            if not resp.get("ok"):
                err = resp.get("out", {}).get("error", str(resp))
                self.done.emit(False, err)
                return
            self.done.emit(True, "擦卡成功")
        except Exception as e:
            self.done.emit(False, str(e))


# ── TaskFetchWorker ─────────────────────────────────

class TaskFetchWorker(QThread):
    done = Signal(object)

    def __init__(self, hotel_id: str = ""):
        super().__init__()
        self._hotel_id = hotel_id

    def run(self):
        try:
            from ..bridgecore.task_fetcher import TaskFetcher
            fetcher = TaskFetcher(hotel_id=self._hotel_id)
            if not fetcher.is_enabled():
                self.done.emit([])
                return
            tasks = fetcher.fetch_pending_tasks(self._hotel_id)
            self.done.emit(tasks)
        except Exception as exc:
            self.done.emit(exc)
