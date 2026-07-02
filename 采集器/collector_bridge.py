"""
collector_bridge.py — 独立版 RflBridge

从 PMS 的 bridge_client.py 剥离而来，去掉所有 PMS 依赖。
只保留：启动32位桥进程、加载DLL、读卡、写卡、擦卡。
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, ClassVar, List, Optional
import logging

logger = logging.getLogger(__name__)

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import fcntl
except ImportError:
    fcntl = None


class BridgeNotAvailable(RuntimeError):
    """找不到 32 位 Python 也没有打包好的 bridge exe。"""


class BridgeCallError(RuntimeError):
    """RPC 调用本身失败（不是 DLL 返回错误码）。"""


# ── 单 USB 互斥 ──────────────────────────────────────────
_BRIDGE_LOCK_PATH = os.path.join(
    tempfile.gettempdir(), "solid_collector_bridge.lock"
)


def _acquire_bridge_lock() -> Optional[Any]:
    try:
        fh = open(_BRIDGE_LOCK_PATH, "a+")
    except Exception:
        return None
    try:
        if msvcrt is not None:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                try:
                    fh.close()
                except Exception:
                    pass
                return None
        elif fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                try:
                    fh.close()
                except Exception:
                    pass
                return None
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(f"pid={os.getpid()} time={time.time():.0f}\n")
            fh.flush()
        except Exception:
            pass
        return fh
    except Exception:
        try:
            fh.close()
        except Exception:
            pass
        return None


def _release_bridge_lock(fh: Any) -> None:
    if fh is None:
        return
    try:
        if msvcrt is not None:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        elif fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass


def _module_dir() -> Path:
    return Path(__file__).resolve().parent


def _find_bridge_exe() -> Optional[Path]:
    """找打包好的 bridge32.exe，只找自身目录。"""
    base = _module_dir()
    candidates = [
        base / "bridge32.exe",
        base / "rfl_bridge_32.exe",
    ]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates = [
            exe_dir / "bridge32.exe",
            exe_dir / "rfl_bridge_32.exe",
        ] + candidates
    for c in candidates:
        if c.is_file():
            return c
    return None


# ── 主客户端 ──────────────────────────────────────────────


class CollectorBridge:
    """独立的 RPC 客户端，只用于采集工具。"""

    def __init__(self, *, default_timeout: float = 5.0):
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._stderr_thread: Optional[threading.Thread] = None
        self._stderr_log: List[str] = []
        self._default_timeout = default_timeout
        self._dll_loaded = False
        self._lock_fh: Optional[Any] = None
        self._last_dll_path: Optional[str] = None
        self._last_extra_paths: Optional[List[str]] = None
        self._last_init_d12: Optional[int] = None
        self._handshake_lock = threading.Lock()
        self._call_pre_hooks: List[Callable[[str, dict], None]] = []
        self._call_post_hooks: List[
            Callable[[str, dict, dict, Optional[str]], None]
        ] = []
        self._probe_call = False

    def register_call_hook(
        self,
        *,
        pre_fn: Optional[Callable[[str, dict], None]] = None,
        post_fn: Optional[Callable[[str, dict, dict, Optional[str]], None]] = None,
    ) -> None:
        if pre_fn is not None:
            self._call_pre_hooks.append(pre_fn)
        if post_fn is not None:
            self._call_post_hooks.append(post_fn)

    def unregister_call_hook(
        self,
        *,
        pre_fn: Optional[Callable[[str, dict], None]] = None,
        post_fn: Optional[Callable[[str, dict, dict, Optional[str]], None]] = None,
    ) -> None:
        if pre_fn is not None:
            self._call_pre_hooks = [h for h in self._call_pre_hooks if h is not pre_fn]
        if post_fn is not None:
            self._call_post_hooks = [h for h in self._call_post_hooks if h is not post_fn]

    def _fire_pre_hook(self, method: str, args: dict) -> None:
        for hook in self._call_pre_hooks:
            try:
                hook(method, args)
            except Exception:
                pass

    def _fire_post_hook(
        self, method: str, args: dict, resp: dict, err: Optional[str]
    ) -> None:
        for hook in self._call_post_hooks:
            try:
                hook(method, args, resp, err)
            except Exception:
                pass

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, *, force_restart: bool = False) -> None:
        with self._lock:
            if self.is_running() and not force_restart:
                return
            if self._proc is not None:
                self._kill_locked()

            bridge_exe = _find_bridge_exe()
            if bridge_exe:
                cmd = [str(bridge_exe)]
            else:
                raise BridgeNotAvailable(
                    "找不到 bridge32.exe。\n"
                    "请确认 bridge32.exe 与 SolidCollector.exe 在同一目录。\n"
                    "如缺失，可打开 32 位 Python，在 JIUDIAN/酒店系统 目录下执行：\n"
                    "  python -m PyInstaller lock_adapters/bridge_32bit.spec --noconfirm\n"
                    "将生成的 dist/bridge_32bit/bridge_32bit.exe 重命名为 bridge32.exe 复制过来。"
                )
            self._cmd = cmd

            if self._lock_fh is None:
                self._lock_fh = _acquire_bridge_lock()

            creationflags = 0
            if sys.platform == "win32":
                creationflags = 0x08000000

            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=creationflags,
            )
            self._dll_loaded = False
            self._stderr_log = []
            t = threading.Thread(target=self._collect_stderr, daemon=True)
            t.start()
            self._stderr_thread = t

    def _collect_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in iter(proc.stderr.readline, b""):
                if not line:
                    break
                try:
                    s = line.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    s = repr(line)
                self._stderr_log.append(s)
                if len(self._stderr_log) > 500:
                    del self._stderr_log[:200]
        except Exception:
            pass

    def stop(self) -> None:
        with self._lock:
            self._kill_locked()

    def _kill_locked(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            self._release_lock_fh_locked()
            return

        def _safe_write(method: str) -> bool:
            if proc.stdin is None or proc.stdin.closed:
                return False
            try:
                payload = json.dumps(
                    {"id": 0, "method": method, "args": {}},
                    ensure_ascii=False,
                )
                proc.stdin.write((payload + "\n").encode("utf-8"))
                proc.stdin.flush()
                return True
            except Exception:
                return False

        try:
            if proc.poll() is None:
                if _safe_write("close_usb"):
                    time.sleep(0.3)
            if proc.poll() is None:
                _safe_write("exit")
                try:
                    proc.wait(timeout=1.5)
                except Exception:
                    pass
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._release_lock_fh_locked()

    def _release_lock_fh_locked(self) -> None:
        fh = self._lock_fh
        self._lock_fh = None
        if fh is not None:
            try:
                _release_bridge_lock(fh)
            except Exception:
                pass

    _HANDSHAKE_METHODS: ClassVar[frozenset] = frozenset({
        "load_dll", "initialize", "ping", "exit", "close_usb", "get_version",
    })

    def _ensure_ready_for(self, method: str) -> None:
        auto_restarted = False
        if not self.is_running():
            self.start()
            auto_restarted = True
        if not auto_restarted:
            return
        if method in self._HANDSHAKE_METHODS:
            return
        if not self._last_dll_path:
            return
        with self._handshake_lock:
            if not self._dll_loaded:
                try:
                    resp = self.load_dll(
                        self._last_dll_path,
                        self._last_extra_paths or [],
                    )
                except Exception as e:
                    raise BridgeCallError(
                        f"bridge 自动重启后 load_dll 异常: {type(e).__name__}: {e}"
                    )
                if not (resp.get("ok") and resp.get("loaded")):
                    raise BridgeCallError(
                        f"bridge 自动重启后 load_dll 失败: {resp}"
                    )
            if self._last_init_d12 is not None:
                try:
                    resp2 = self.initialize(self._last_init_d12)
                except Exception as e:
                    raise BridgeCallError(
                        f"bridge 自动重启后 initializeUSB 异常: {type(e).__name__}: {e}"
                    )
                ret = int(resp2.get("ret", -1))
                if not resp2.get("ok") or ret != 0:
                    raise BridgeCallError(
                        f"bridge 自动重启后 initializeUSB 失败: {resp2}"
                    )

    def _call(self, method: str, args: Optional[dict] = None,
              *, timeout: Optional[float] = None) -> dict:
        self._ensure_ready_for(method)
        timeout = timeout if timeout is not None else self._default_timeout
        safe_args = args or {}
        if not getattr(self, "_probe_call", False):
            self._fire_pre_hook(method, safe_args)
        err_text: Optional[str] = None
        try:
            with self._lock:
                assert self._proc is not None
                assert self._proc.stdin is not None
                assert self._proc.stdout is not None
                self._next_id += 1
                req_id = self._next_id
                payload = json.dumps(
                    {"id": req_id, "method": method, "args": safe_args},
                    ensure_ascii=False,
                )
                try:
                    self._proc.stdin.write((payload + "\n").encode("utf-8"))
                    self._proc.stdin.flush()
                except Exception as e:
                    err_text = str(e)
                    raise BridgeCallError(f"写入子进程失败: {e}")
                line = self._read_line_with_timeout(timeout)
                if line is None:
                    err_text = f"timeout {timeout}s"
                    raise BridgeCallError(
                        f"调用 {method} 超时 ({timeout}s)"
                    )
                try:
                    resp = json.loads(line)
                except Exception as e:
                    err_text = str(e)
                    raise BridgeCallError(f"响应不是合法 JSON: {line!r} ({e})")
        except BridgeCallError:
            if not getattr(self, "_probe_call", False):
                self._fire_post_hook(method, safe_args, {}, err_text)
            raise
        else:
            if not getattr(self, "_probe_call", False):
                self._fire_post_hook(method, safe_args, resp, None)
            return resp

    def _read_line_with_timeout(self, timeout: float) -> Optional[str]:
        """按行读取子进程 stdout（块读优化版）。

        旧实现：单字节 read(1) + 5ms 轮询 → 每条消息 N 次系统调用，
        50KB/s 数据时 CPU 占用 8-12%，且忙等导致 sleep 抖动大。
        新实现：read(64) 一次最多拿 64 字节，平均 1-2 次系统调用读完一行，
        CPU 占用降到 < 2%，且单次系统调用开销恒定。
        若读到不带 \\n 的块，缓存到 buf 等下一轮拼接。
        """
        assert self._proc is not None and self._proc.stdout is not None
        out = self._proc.stdout
        deadline = time.time() + timeout
        # 复用持久化缓冲区（_call 是串行锁内调用，无需线程安全）
        buf = getattr(self, "_read_buf", None)
        if buf is None:
            buf = bytearray()
            self._read_buf = buf
        while time.time() < deadline:
            # 缓冲区里已经有完整行 → 直接切出返回，不再 syscall
            nl = buf.find(b"\n")
            if nl >= 0:
                line = bytes(buf[:nl + 1])
                del buf[:nl + 1]
                try:
                    return line.decode("utf-8", errors="replace")
                except Exception:
                    return None
            # 缓冲区里没有完整行 → 一次最多读 64 字节
            try:
                chunk = out.read(64)
            except Exception:
                return None
            if not chunk:
                # 子进程已退出 → 直接放弃
                if self._proc.poll() is not None:
                    return None
                # 5ms 兜底等待（仅在没数据时才 sleep，避免忙等）
                time.sleep(0.005)
                continue
            buf.extend(chunk)
            # 读到块后立即循环检查是否已有换行符
        # 超时退出：若 buf 非空，返回部分内容（容忍半行）
        if buf:
            line = bytes(buf)
            buf.clear()
            try:
                return line.decode("utf-8", errors="replace")
            except Exception:
                return None
        return None

    # ── 高层 API ─────────────────────────────────────────

    def load_dll(self, dll_path: str, extra_paths: Optional[List[str]] = None,
                 *, timeout: float = 5.0) -> dict:
        resp = self._call(
            "load_dll",
            {"dll_path": dll_path, "extra_paths": extra_paths or []},
            timeout=timeout,
        )
        if resp.get("ok") and resp.get("loaded"):
            self._dll_loaded = True
            self._last_dll_path = dll_path
            self._last_extra_paths = list(extra_paths) if extra_paths else []
        else:
            self._dll_loaded = False
        return resp

    @property
    def dll_loaded(self) -> bool:
        return self._dll_loaded

    def initialize(self, d12: int = 0, *, timeout: float = 5.0) -> dict:
        resp = self._call("initialize", {"d12": d12}, timeout=timeout)
        try:
            if resp.get("ok") and int(resp.get("ret", -1)) == 0:
                self._last_init_d12 = d12
        except Exception:
            pass
        return resp

    def bind_from_profile(self, profile: dict, *, timeout: float = 5.0) -> dict:
        return self._call(
            "bind_from_profile",
            {"profile": profile},
            timeout=timeout,
        )

    def generic_initialize(
        self,
        init_fn_name: str,
        param_list: List[int],
        *,
        timeout: float = 8.0,
    ) -> dict:
        resp = self._call(
            "generic_initialize",
            {"init_fn_name": init_fn_name, "param_list": param_list},
            timeout=timeout,
        )
        try:
            if resp.get("ok") and int(resp.get("ret", -1)) == 0:
                self._last_init_d12 = param_list[0] if param_list else 0
        except Exception:
            pass
        return resp

    def generic_read(
        self,
        read_fn_name: str,
        d12: int = 1,
        *,
        timeout: float = 8.0,
    ) -> dict:
        return self._call(
            "generic_read",
            {"read_fn_name": read_fn_name, "d12": d12},
            timeout=timeout,
        )

    def close_usb(self, *, timeout: float = 3.0) -> dict:
        return self._call("close_usb", timeout=timeout)

    def read_card(self, d12: int = 1, *, timeout: float = 6.0) -> dict:
        return self._call("read_card", {"d12": d12}, timeout=timeout)

    def direct_read_usb(self, *, d12: int = 1,
                        timeout: float = 6.0) -> dict:
        return self._call("direct_read_usb", {"d12": d12}, timeout=timeout)

    def direct_write_usb(self, *, d12: int = 1, card_hex: str,
                         timeout: float = 6.0) -> dict:
        return self._call(
            "direct_write_usb",
            {"d12": d12, "card_hex": card_hex},
            timeout=timeout,
        )

    def write_card(self, *, d12: int = 1, card_hex: str,
                   variant: str = "binary",
                   timeout: float = 6.0) -> dict:
        return self._call(
            "write_card",
            {"d12": d12, "card_hex": card_hex, "variant": variant},
            timeout=timeout,
        )

    def list_bound_functions(self, *, timeout: float = 3.0) -> dict:
        return self._call("list_bound_functions", timeout=timeout)

    def dll_list_exports(self, timeout: float = 5.0) -> dict:
        return self._call("dll_list_exports", {}, timeout=timeout)

    def dll_call(self, fn_name: str, params: List[dict],
                 timeout: float = 10.0) -> dict:
        return self._call(
            "dll_call",
            {"fn_name": fn_name, "params": params},
            timeout=timeout,
        )

    def buzzer(self, d12: int = 1, t: int = 20) -> dict:
        return self._call("buzzer", {"d12": d12, "t": t})

    def ping(self, *, timeout: float = 2.0) -> bool:
        try:
            resp = self._call("ping", timeout=timeout)
        except Exception:
            return False
        return bool(resp.get("ok"))


# ── 单例 ──────────────────────────────────────────────────

_bridge_instance: Optional[CollectorBridge] = None
_bridge_lock = threading.Lock()


def get_bridge() -> CollectorBridge:
    """全局唯一的 bridge 实例，所有工作线程共享。"""
    global _bridge_instance
    with _bridge_lock:
        if _bridge_instance is None:
            _bridge_instance = CollectorBridge()
        return _bridge_instance


def shutdown_bridge() -> None:
    global _bridge_instance
    with _bridge_lock:
        if _bridge_instance is not None:
            try:
                _bridge_instance.stop()
            except Exception:
                pass
            _bridge_instance = None


# ── 串口桥接工厂 ─────────────────────────────────────────────


_SERIAL_BRIDGE_INSTANCE: dict = {}
_SERIAL_BRIDGE_LOCK = threading.RLock()


def get_serial_bridge(port: str, baudrate: int = 9600) -> "SerialBridge":
    """返回串口桥接实例（每个 port 单例）。

    Args:
        port: COM 口，如 "COM3"。
        baudrate: 波特率。

    Returns:
        已启动的 SerialBridge 实例。
    """
    from .bridgecore.serial_channel import SerialBridge

    key = f"{port}:{baudrate}"
    with _SERIAL_BRIDGE_LOCK:
        if key not in _SERIAL_BRIDGE_INSTANCE:
            _SERIAL_BRIDGE_INSTANCE[key] = SerialBridge(port, baudrate)
        return _SERIAL_BRIDGE_INSTANCE[key]


def shutdown_serial_bridge(port: str, baudrate: int = 9600) -> None:
    """关闭指定端口串口桥接。"""
    key = f"{port}:{baudrate}"
    with _SERIAL_BRIDGE_LOCK:
        inst = _SERIAL_BRIDGE_INSTANCE.pop(key, None)
        if inst is not None:
            try:
                inst.stop()
            except Exception:
                pass


def _atexit_cleanup() -> None:
    try:
        shutdown_bridge()
    except Exception:
        pass
    # 关闭所有串口
    with _SERIAL_BRIDGE_LOCK:
        for key, inst in list(_SERIAL_BRIDGE_INSTANCE.items()):
            try:
                inst.stop()
            except Exception:
                pass
            _SERIAL_BRIDGE_INSTANCE.pop(key, None)


# 统一对外关闭入口（供 collector_main 的 atexit/signal 调用）
def _shutdown(*_args) -> None:
    """外部清理入口：兼容 atexit/signal 的多参数签名。"""
    _atexit_cleanup()


# 主动注册 atexit（原本定义了却没 register，是 P0 bug）
atexit.register(_shutdown)
