"""
path_prober.py — 探测门锁发卡路径

职责
====
在采集现场确定该酒店的门锁能用哪种方式发卡：

1. DLL 直调（dll_direct）：通过 bridge32.exe 直接加载原厂 DLL 读写发卡器
2. 寄生原厂（parasitic）：通过 pywinauto 模拟操作原厂 CardLock.exe
3. 串口（serial）：直接通过 COM 口与发卡器通信

输出
====
mode: "dll_direct" | "parasitic" | "serial" | "failed"
detail: 探测详情（每步结果，用于 UI 展示）
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from .serial_channel import SerialScanner

logger = logging.getLogger(__name__)

_BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SYSTEM_DLLS = frozenset({
    "msvcp100.dll", "msvcr100.dll", "msvcp120.dll", "msvcr120.dll",
    "vcruntime140.dll", "concrt140.dll", "d12.dll",
})

_MIN_DLL_CONFIDENCE = 0.35


def _bridge32_path() -> str:
    candidates = [
        os.path.join(_BUNDLE_DIR, "bridge32.exe"),
        os.path.join(os.path.dirname(_BUNDLE_DIR), "bridge32.exe"),
        os.path.join(_BUNDLE_DIR, "..", "bridge32.exe"),
        "bridge32.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return "bridge32.exe"


class PathProber:
    """探测该酒店的门锁能用哪条路发卡。"""

    def __init__(self):
        self._bridge32 = _bridge32_path()

    def probe(
        self,
        install_dir: str,
        profile: dict,
        *,
        identity_hint: Optional[dict] = None,
    ) -> Dict[str, Any]:
        detail = {"dll_direct": {}, "parasitic": {}, "serial": {}}
        hint = identity_hint or {}

        # 1. 串口优先（identity 已发现响应串口，或 profile 指定 serial）
        if profile.get("channel") == "serial" or hint.get("serial_responsive"):
            detail["serial"] = self._probe_serial(profile, hint)
            if detail["serial"].get("connected"):
                return {"mode": "serial", "detail": detail}

        # 2. DLL 直调（需探针置信度 + init 成功）
        dll_name = self._find_main_dll(install_dir, profile, hint)
        if dll_name:
            detail["dll_direct"] = self._probe_dll_direct(install_dir, dll_name)
            dd = detail["dll_direct"]
            conf = float(hint.get("dll_confidence") or profile.get("detect", {}).get("confidence") or 1.0)
            if (
                dd.get("dll_loaded")
                and dd.get("initialized")
                and conf >= _MIN_DLL_CONFIDENCE
            ):
                return {"mode": "dll_direct", "detail": detail}

        # 3. 寄生原厂
        detail["parasitic"] = self._probe_parasitic(install_dir)
        if detail["parasitic"].get("exe_found"):
            return {"mode": "parasitic", "detail": detail}

        return {
            "mode": "failed",
            "detail": detail,
            "message": "未找到可用的发卡路径（DLL 直调 / 原厂寄生 / 串口均失败）",
        }

    def _find_main_dll(
        self,
        install_dir: str,
        profile: dict,
        hint: dict,
    ) -> Optional[str]:
        main_dll = hint.get("main_dll") or ""
        install_cfg = profile.get("install", {})
        if not main_dll:
            main_dll = install_cfg.get("main_dll", "")
        dll_cfg = profile.get("dll") or {}
        if not main_dll:
            main_dll = dll_cfg.get("path") or ""

        if main_dll:
            full_path = os.path.join(install_dir, main_dll)
            if os.path.isfile(full_path):
                return os.path.basename(main_dll)

        candidates: List[str] = []
        for c in hint.get("dll_candidates") or []:
            name = c.get("dll_name") or ""
            if name and name.lower() not in _SYSTEM_DLLS:
                candidates.append(name)

        if install_dir and os.path.isdir(install_dir):
            for f in os.listdir(install_dir):
                fl = f.lower()
                if not fl.endswith(".dll") or fl in _SYSTEM_DLLS:
                    continue
                if any(k in fl for k in ("rfl", "lock", "comm", "usb", "hotel", "card")):
                    candidates.append(f)

        seen = set()
        for name in candidates:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            if os.path.isfile(os.path.join(install_dir, name)):
                return name
        return None

    def _probe_dll_direct(self, install_dir: str, dll_name: str) -> dict:
        result = {
            "dll_path": os.path.join(install_dir, dll_name),
            "dll_found": os.path.isfile(os.path.join(install_dir, dll_name)),
            "dll_loaded": None,
            "initialized": None,
            "card_read": None,
        }

        if not result["dll_found"]:
            return result

        try:
            args = [
                self._bridge32,
                "--action", "load_dll",
                "--dll", os.path.join(install_dir, dll_name),
            ]
            proc = subprocess.run(args, capture_output=True, timeout=10, text=True)
            if proc.returncode != 0:
                logger.info("bridge32 load_dll 失败: %s", proc.stderr[:200])
                return result

            result["dll_loaded"] = True

            args2 = [
                self._bridge32,
                "--action", "initialize",
                "--dll", os.path.join(install_dir, dll_name),
            ]
            proc2 = subprocess.run(args2, capture_output=True, timeout=10, text=True)
            if proc2.returncode != 0:
                logger.info("bridge32 initialize 失败: %s", proc2.stderr[:200])
                return result

            result["initialized"] = True

            args3 = [
                self._bridge32,
                "--action", "read_card",
                "--dll", os.path.join(install_dir, dll_name),
            ]
            proc3 = subprocess.run(args3, capture_output=True, timeout=15, text=True)
            if proc3.returncode == 0 and proc3.stdout.strip():
                result["card_read"] = True
            else:
                result["card_read"] = False
        except subprocess.TimeoutExpired:
            logger.warning("bridge32 操作超时")
        except FileNotFoundError:
            logger.warning("bridge32.exe 未找到")
        except Exception as exc:
            logger.warning("探测 DLL 直调异常: %s", exc)

        return result

    def _probe_parasitic(self, install_dir: str) -> dict:
        from .oem_process import find_primary_oem_exe, find_oem_exes

        result = {"cardlock_exe": "", "exe_found": False}

        primary = find_primary_oem_exe(install_dir)
        if primary:
            result["cardlock_exe"] = primary
            result["exe_found"] = True
            return result

        exes = find_oem_exes(install_dir, top_n=1)
        if exes:
            result["cardlock_exe"] = exes[0].path
            result["exe_found"] = True
        return result

    def _probe_serial(self, profile: dict, hint: dict) -> dict:
        result = {"port": "", "baudrate": 0, "connected": None}
        serial_cfg = profile.get("serial", {})
        port = serial_cfg.get("port", "")
        baudrate = serial_cfg.get("baudrate", 9600)

        responsive = hint.get("serial_responsive") or []
        if responsive and not port:
            p0 = responsive[0]
            port = getattr(p0, "port", "") or (p0.get("port") if isinstance(p0, dict) else "")
            baudrate = getattr(p0, "baudrate", baudrate) or (
                p0.get("baudrate") if isinstance(p0, dict) else baudrate
            )

        if port:
            result["port"] = port
            result["baudrate"] = baudrate
            try:
                from .serial_channel import SerialBridge
                bridge = SerialBridge(port, baudrate, profile=profile)
                bridge.start()
                resp = bridge.direct_read_usb(d12=1, timeout=3.0)
                result["connected"] = bool(resp.get("ok"))
                bridge.stop()
            except Exception:
                result["connected"] = False
        else:
            try:
                scanner = SerialScanner()
                ports = scanner.scan()
                responsive_ports = scanner.probe(ports, timeout=1.0)
                if responsive_ports:
                    p = responsive_ports[0]
                    result["port"] = p.port
                    result["baudrate"] = p.baudrate
                    result["connected"] = True
                    if "serial" not in profile:
                        profile["serial"] = {}
                    profile["serial"]["port"] = p.port
                    profile["serial"]["baudrate"] = p.baudrate
            except Exception as e:
                logger.info("串口自动扫描跳过: %s", e)
        return result


def probe(
    install_dir: str,
    profile: dict,
    *,
    identity_hint: Optional[dict] = None,
) -> Dict[str, Any]:
    prober = PathProber()
    return prober.probe(install_dir, profile, identity_hint=identity_hint)
