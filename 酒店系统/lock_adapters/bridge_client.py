"""
bridge_client.py — 64 位主进程到 32 位 rfl_bridge 的 RPC 客户端

使用流程
========
    from lock_adapters.bridge_client import RflBridge

    bridge = RflBridge()
    bridge.start()                              # 启动 32 位子进程

    bridge.load_dll(r"D:\\...\\V9RFL.dll")       # 加载 DLL
    print(bridge.get_version())                  # {"ret": 0, "out": {"version": "V9..."}}
    bridge.initialize(d12=1)
    bridge.buzzer(d12=1, t=20)
    bridge.close()                               # 关闭子进程

如何寻找 32 位 Python？
=======================
1. 优先：同目录或 ../bridge/ 下有 `rfl_bridge_32.exe` (PyInstaller 打包好的)
2. 次选：环境变量 `PYTHON_32` 指向 32 位 python.exe
3. 兜底：扫描 `C:\Python3*-32`, `%LOCALAPPDATA%\Programs\Python\Python3*-32`
4. 都没有：抛 BridgeNotAvailable

设计原则
========
- **不阻塞主线程超过 5 秒**：每个 RPC 调用有超时。
- **子进程崩溃自愈**：检测到子进程退出，下一次调用自动重启。
- **零依赖**：只用 subprocess + json + threading，不引入第三方库。
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional
import logging
logger = logging.getLogger(__name__)

try:  # Windows
    import msvcrt  # type: ignore
except ImportError:  # POSIX
    msvcrt = None  # type: ignore

try:  # POSIX
    import fcntl  # type: ignore
except ImportError:  # Windows
    fcntl = None  # type: ignore


class BridgeNotAvailable(RuntimeError):
    """找不到 32 位 Python 也没有打包好的桥接 exe。"""


class BridgeCallError(RuntimeError):
    """RPC 调用本身失败（不是 DLL 返回错误码）。"""


# ──────────────────────────────────────────────────────────────────
# 单 USB 互斥：文件锁
#
# 背景：开发机上同时存在 Solid 主进程 + tools/dev/_probe_v9_*.py 等探测
# 脚本。两个 Python 进程都会去生成 32 位桥接 → 两个桥接同时调用
# initializeUSB(V9RFL.dll) → V9 USB 状态机被踩坏（典型现象：VID_0000、
# “未知 USB 设备 - 设定地址失败”）。
#
# 这里用一个 OS 级文件锁（Windows: msvcrt.locking；POSIX: fcntl.flock）
# 保证同一时刻最多只有一个 RflBridge 在跑。lock 失败不会硬阻塞，只在
# stderr_log 留一条 WARNING，方便事后排查；这样 USB 救援脚本即使
# 在 Solid 主进程崩溃 / 锁文件残留的情况下也还能跑。
# ──────────────────────────────────────────────────────────────────

_BRIDGE_LOCK_PATH = os.path.join(tempfile.gettempdir(), "solid_v9_bridge.lock")


def _acquire_bridge_lock() -> Optional[Any]:
    """尝试拿独占锁；拿到返回打开的 file handle，拿不到返回 None。"""
    try:
        fh = open(_BRIDGE_LOCK_PATH, "a+")
    except Exception:
        return None
    try:
        if msvcrt is not None:
            try:
                # 非阻塞锁 1 字节；冲突会抛 OSError
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
        # 写一行便于事后排查“是谁锁住了 USB”
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


def _bridge_script() -> Path:
    return _module_dir() / "rfl_bridge_32.py"


def _candidate_bridge_exes() -> List[Path]:
    """打包后的 32 位桥接 EXE 可能放在的位置。"""
    base = _module_dir().parent
    out = [
        base / "rfl_bridge_32.exe",
        base / "bridge" / "rfl_bridge_32.exe",
        base / "tools" / "rfl_bridge_32.exe",
        base / "lock_adapters" / "rfl_bridge_32.exe",
    ]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        out.extend([
            exe_dir / "rfl_bridge_32.exe",
            exe_dir / "bridge" / "rfl_bridge_32.exe",
        ])
    return out


def _candidate_python32_paths() -> List[Path]:
    """常见的 32 位 Python 安装路径。"""
    candidates: List[Path] = []

    # 1. 环境变量
    env = os.environ.get("PYTHON_32") or os.environ.get("PYTHON32")
    if env:
        candidates.append(Path(env))

    # 2. 跟随 Solid 自带的 embeddable Python 32（推荐部署方式）
    base = _module_dir().parent
    for sub in ("python32", "lock_adapters/python32", "bridge/python32", "_deploy_deps/python32"):
        candidates.append(base / sub / "python.exe")
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        for sub in ("python32", "bridge/python32", "lock_adapters/python32", "_deploy_deps/python32"):
            candidates.append(exe_dir / sub / "python.exe")

    # 3. 系统标准安装路径
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    for ver in ("313", "312", "311", "310", "39", "38"):
        if local_appdata:
            candidates.append(Path(local_appdata) / "Programs" / "Python" / f"Python{ver}-32" / "python.exe")
        candidates.append(Path(f"C:/Python{ver}-32/python.exe"))
        candidates.append(Path(f"C:/Program Files (x86)/Python{ver}/python.exe"))

    return candidates


def find_bridge_command() -> List[str]:
    """
    返回启动桥接的命令行。优先用打包好的 32 位 exe，否则用 32 位 Python 跑脚本。

    返回值是 subprocess.Popen 用的 list（带参数）。
    """
    # 优先：打包好的 EXE
    for exe in _candidate_bridge_exes():
        if exe.is_file():
            return [str(exe)]

    # 次选：32 位 Python + 脚本
    script = _bridge_script()
    if not script.is_file():
        raise BridgeNotAvailable(f"rfl_bridge_32.py not found at {script}")

    for py in _candidate_python32_paths():
        if py.is_file():
            return [str(py), str(script)]

    raise BridgeNotAvailable(
        "找不到 32 位 Python 或打包好的 rfl_bridge_32.exe。"
        "请安装 32 位 Python (https://www.python.org/downloads/windows/) "
        "或设置环境变量 PYTHON_32 指向 32 位 python.exe。"
    )


# ──────────────────────────────────────────────────────────────────
# 主客户端
# ──────────────────────────────────────────────────────────────────

class RflBridge:
    """单实例的 RPC 客户端。线程安全（内部加锁）。"""

    def __init__(self, *, default_timeout: float = 5.0):
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._stderr_thread: Optional[threading.Thread] = None
        self._stderr_log: List[str] = []
        self._default_timeout = default_timeout
        self._cmd: Optional[List[str]] = None
        self._dll_loaded = False
        # 单 USB 互斥锁的句柄；start() 时尝试获取，stop() 时释放
        self._lock_fh: Optional[Any] = None

        # ── 自动重新握手所需的"上一次成功握手"参数缓存 ────────────
        # 现象修复：当桥接子进程因 DLL 卡死/被杀/崩溃而需要重启时，
        # _call 会自动生成新桥接，但新桥接的 self.dll 是 None。
        # 如果不补 load_dll + initializeUSB，下一个调用就会拿到
        # "DLL 未导出或绑定失败: CardErase" 之类的误导性错误，而且
        # USB 会留在半握手状态，把 d12c.dll 踩坏。
        # 这三个字段就是自动重新握手用的"上次成功"快照。
        self._last_dll_path: Optional[str] = None
        self._last_extra_paths: Optional[List[str]] = None
        self._last_init_d12: Optional[int] = None
        # 重新握手期间的串行锁（与 _lock 分开，避免和 RPC 锁互相等待）。
        self._handshake_lock = threading.Lock()

    # ──────────────── 进程生命周期 ────────────────

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, *, force_restart: bool = False) -> None:
        """启动 32 位桥接子进程。如果已经在跑且不强制重启，直接返回。"""
        with self._lock:
            if self.is_running() and not force_restart:
                return
            if self._proc is not None:
                self._kill_locked()

            cmd = find_bridge_command()
            self._cmd = cmd

            # 尝试拿单 USB 互斥锁；拿不到不阻塞启动（救援场景仍可用），
            # 只在 stderr_log 里留 WARNING，便于事后看“是不是俩桥接抢”。
            if self._lock_fh is None:
                self._lock_fh = _acquire_bridge_lock()
                if self._lock_fh is None:
                    msg = (
                        f"[bridge_client] WARNING: 无法获取 V9 bridge 单实例锁 "
                        f"({_BRIDGE_LOCK_PATH})；可能已有另一个进程占用发卡器，"
                        "此次启动可能踩坏 USB 状态机。"
                    )
                    self._stderr_log.append(msg)
                    try:
                        sys.stderr.write(msg + "\n")
                        sys.stderr.flush()
                    except Exception:
                        pass

            # CREATE_NO_WINDOW = 0x08000000 (Windows-only, 隐藏控制台)
            creationflags = 0
            if sys.platform == "win32":
                creationflags = 0x08000000  # CREATE_NO_WINDOW

            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,                   # 不缓冲，行级延迟最低
                creationflags=creationflags,
                cwd=str(_module_dir()),
            )
            self._dll_loaded = False
            # 启 stderr 收集线程
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
                # 防止无限堆积
                if len(self._stderr_log) > 500:
                    del self._stderr_log[:200]
        except Exception:
            pass

    def stop(self) -> None:
        with self._lock:
            self._kill_locked()

    def _kill_locked(self) -> None:
        """优雅停止桥接子进程，按 close_usb → exit → terminate → kill 顺序。

        关键点：必须在子进程退出之前给 DLL 一次 CloseUSB 的机会。否则
        V9RFL.dll 内部仍持有 USB 句柄，进程被 terminate 后 Windows 收
        不到 close，下次启动会看到“未知 USB 设备（设定地址失败）”。
        """
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
            # ── 阶段 1：让 DLL 先 close USB（DLL 仍在进程内，能正常调用）
            if proc.poll() is None:
                if _safe_write("close_usb"):
                    # 给 DLL ~300ms 把 USB 句柄交还给 Windows
                    time.sleep(0.3)

            # ── 阶段 2：让子进程优雅 exit（exit 路径还会再 close 一次，幂等）
            if proc.poll() is None:
                _safe_write("exit")
                try:
                    proc.wait(timeout=1.5)
                except Exception:
                    pass

            # ── 阶段 3：terminate
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass

            # ── 阶段 4：kill（兜底；此时 USB 几乎一定脏，但已尽力）
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
        """释放单 USB 互斥锁；调用者必须已持有 self._lock。"""
        fh = self._lock_fh
        self._lock_fh = None
        if fh is not None:
            try:
                _release_bridge_lock(fh)
            except Exception:
                pass

    def get_stderr_log(self) -> List[str]:
        return list(self._stderr_log)

    # ──────────────── 基础 RPC ────────────────

    # 这些方法本身就是"握手 / 进程控制"，自动重新握手必须跳过它们，
    # 否则会出现 _call(load_dll) → _ensure_ready_for → _rehandshake → _call(load_dll) 的死循环。
    _HANDSHAKE_METHODS: ClassVar[frozenset] = frozenset({
        "load_dll", "initialize", "ping", "exit", "close_usb", "get_version",
    })

    def _ensure_ready_for(self, method: str) -> None:
        """确保桥接在跑；如果是自动重启且需要握手就先补握手。

        必须在 self._lock **外**调用 —— 内部的 load_dll / initialize 也走
        _call → with self._lock，重入锁会自死。
        """
        auto_restarted = False
        if not self.is_running():
            self.start()
            auto_restarted = True

        if not auto_restarted:
            return
        if method in self._HANDSHAKE_METHODS:
            return
        if not self._last_dll_path:
            # 第一次还没握过手就崩，没什么可恢复的，让原 method 自己报错。
            return

        with self._handshake_lock:
            # double-check：可能另一个并发调用刚补完
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
                # initialize 的成功判据：ok 且 ret==0
                ret = -1
                try:
                    ret = int(resp2.get("ret", -1))
                except Exception:
                    pass
                if not resp2.get("ok") or ret != 0:
                    raise BridgeCallError(
                        f"bridge 自动重启后 initializeUSB 失败: ret={ret}, resp={resp2}"
                    )

    def _call(self, method: str, args: Optional[Dict[str, Any]] = None,
              *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """执行一次同步 RPC。返回完整响应字典。"""
        self._ensure_ready_for(method)

        timeout = timeout if timeout is not None else self._default_timeout

        with self._lock:
            assert self._proc is not None
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None

            self._next_id += 1
            req_id = self._next_id
            payload = json.dumps(
                {"id": req_id, "method": method, "args": args or {}},
                ensure_ascii=False,
            )
            try:
                self._proc.stdin.write((payload + "\n").encode("utf-8"))
                self._proc.stdin.flush()
            except Exception as e:
                raise BridgeCallError(f"写入子进程失败: {e}")

            # 读响应（阻塞，但有超时由内核 IO 控制；这里加个粗暴守护）
            line = self._read_line_with_timeout(timeout)
            if line is None:
                raise BridgeCallError(f"调用 {method} 超时 ({timeout}s)，stderr={self._stderr_log[-3:]}")

            try:
                resp = json.loads(line)
            except Exception as e:
                raise BridgeCallError(f"响应不是合法 JSON: {line!r} ({e})")

            return resp

    def _read_line_with_timeout(self, timeout: float) -> Optional[str]:
        """带超时地从子进程 stdout 读一行。"""
        assert self._proc is not None and self._proc.stdout is not None
        out = self._proc.stdout
        deadline = time.time() + timeout
        buf = bytearray()
        while time.time() < deadline:
            try:
                ch = out.read(1)
            except Exception:
                return None
            if not ch:
                if self._proc.poll() is not None:
                    return None
                time.sleep(0.005)
                continue
            buf.extend(ch)
            if ch == b"\n":
                break
        if not buf:
            return None
        try:
            return buf.decode("utf-8", errors="replace")
        except Exception:
            return None

    # ──────────────── 高层 API ────────────────

    def ping(self, *, timeout: float = 2.0) -> bool:
        try:
            resp = self._call("ping", timeout=timeout)
        except Exception:
            return False
        return bool(resp.get("ok"))

    def load_dll(self, dll_path: str, extra_paths: Optional[List[str]] = None,
                 *, timeout: float = 5.0) -> Dict[str, Any]:
        resp = self._call(
            "load_dll",
            {"dll_path": dll_path, "extra_paths": extra_paths or []},
            timeout=timeout,
        )
        # 只有 RPC 成功 *且* DLL 真的加载上（loaded=True）才置位。
        # 否则 get_version() 会在没加载的状态下被绕过，返回空串掩盖真实错误。
        if resp.get("ok") and resp.get("loaded"):
            self._dll_loaded = True
            # 记下"上次成功 load_dll"的参数，供桥接自动重启时重新握手。
            self._last_dll_path = dll_path
            self._last_extra_paths = list(extra_paths) if extra_paths else []
        else:
            self._dll_loaded = False
        return resp

    @property
    def dll_loaded(self) -> bool:
        return self._dll_loaded

    def get_version(self) -> Dict[str, Any]:
        return self._call("get_version")

    def initialize(self, d12: int = 0, *, timeout: float = 5.0) -> Dict[str, Any]:
        resp = self._call("initialize", {"d12": d12}, timeout=timeout)
        # 只在 RPC 成功且 ret==0 时缓存 d12，供桥接自动重启时重新握手。
        try:
            if resp.get("ok") and int(resp.get("ret", -1)) == 0:
                self._last_init_d12 = d12
        except Exception:
            pass
        return resp

    def close_usb(self, *, timeout: float = 3.0) -> Dict[str, Any]:
        """同步通知 DLL 释放 USB 句柄。

        允许调用方传入更短的 timeout：探测脚本 / 程序退出时希望立刻
        让出 USB，不愿意被 5s 默认 timeout 卡住。
        """
        return self._call("close_usb", timeout=timeout)

    def encoder_check(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        """发卡前编码器连接检查"""
        return self._call("encoder_check", {}, timeout=timeout)

    def keepalive(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        """读卡器保活，定期调 initializeUSB 防止固件超时"""
        return self._call("keepalive", {}, timeout=timeout)

    def buzzer(self, d12: int = 1, t: int = 20) -> Dict[str, Any]:
        return self._call("buzzer", {"d12": d12, "t": t})

    def read_card(self, d12: int = 1, *, timeout: float = 6.0) -> Dict[str, Any]:
        return self._call("read_card", {"d12": d12}, timeout=timeout)

    def guest_card(self, *, d12: int, dlsCoID: int, CardNo: int, dai: int,
                   LLock: int, pdoors: int,
                   BDate: str, EDate: str, LockNo: str,
                   timeout: float = 6.0) -> Dict[str, Any]:
        return self._call(
            "guest_card",
            {
                "d12": d12, "dlsCoID": dlsCoID, "CardNo": CardNo, "dai": dai,
                "LLock": LLock, "pdoors": pdoors,
                "BDate": BDate, "EDate": EDate, "LockNo": LockNo,
            },
            timeout=timeout,
        )

    def limit_card(self, *, d12: int, dlsCoID: int, CardNo: int, dai: int,
                   BDate: str, LCardNo: str,
                   timeout: float = 6.0) -> Dict[str, Any]:
        return self._call(
            "limit_card",
            {
                "d12": d12, "dlsCoID": dlsCoID, "CardNo": CardNo, "dai": dai,
                "BDate": BDate, "LCardNo": LCardNo,
            },
            timeout=timeout,
        )

    def card_erase(self, *, d12: int, dlsCoID: int, card_hex: str,
                   timeout: float = 6.0) -> Dict[str, Any]:
        return self._call(
            "card_erase",
            {"d12": d12, "dlsCoID": dlsCoID, "card_hex": card_hex},
            timeout=timeout,
        )

    def parse_card_type(self, card_hex: str) -> Dict[str, Any]:
        return self._call("parse_card_type", {"card_hex": card_hex})

    def read_open_record(self, card_hex: str) -> Dict[str, Any]:
        return self._call("read_open_record", {"card_hex": card_hex})

    def get_guest_lock_no(self, dlsCoID: int, card_hex: str) -> Dict[str, Any]:
        return self._call(
            "get_guest_lock_no",
            {"dlsCoID": dlsCoID, "card_hex": card_hex},
        )

    def get_guest_etime(self, dlsCoID: int, card_hex: str) -> Dict[str, Any]:
        return self._call(
            "get_guest_etime",
            {"dlsCoID": dlsCoID, "card_hex": card_hex},
        )

    def write_card(self, *, d12: int = 1, card_hex: str,
                   variant: str = "binary",
                   timeout: float = 6.0) -> Dict[str, Any]:
        return self._call(
            "write_card",
            {"d12": d12, "card_hex": card_hex, "variant": variant},
            timeout=timeout,
        )

    def guest_card_v2(self, *, d12: int, dlsCoID: int, CardNo: int, dai: int,
                      LLock: int, pdoors: int,
                      BDate: str, EDate: str, LockNo: str,
                      lockno_mode: str = "ascii",
                      timeout: float = 6.0) -> Dict[str, Any]:
        return self._call(
            "guest_card_v2",
            {
                "d12": d12, "dlsCoID": dlsCoID, "CardNo": CardNo, "dai": dai,
                "LLock": LLock, "pdoors": pdoors,
                "BDate": BDate, "EDate": EDate, "LockNo": LockNo,
                "lockno_mode": lockno_mode,
            },
            timeout=timeout,
        )

    def list_bound_functions(self, *, timeout: float = 3.0) -> Dict[str, Any]:
        return self._call("list_bound_functions", timeout=timeout)

    def call_card_fn(self, *, fn_name: str, signature: list,
                     timeout: float = 8.0) -> Dict[str, Any]:
        """通用调用任意已绑定的 DLL 函数。signature 见桥接端定义。"""
        return self._call(
            "call_card_fn",
            {"fn_name": fn_name, "signature": signature},
            timeout=timeout,
        )

    def compose_guest_card(self, *, d12: int = 1, dlsCoID: int,
                           BDate: str, EDate: str, LockNoHex: str,
                           CardNo: int = 1, dai: int = 0,
                           LLock: int = 1, pdoors: int = 0,
                           timeout: float = 10.0) -> Dict[str, Any]:
        """一站式发客人卡（动态时间 + 强制正确锁号 + binary 写卡 + 读卡校验）。"""
        return self._call(
            "compose_guest_card",
            {
                "d12": d12, "dlsCoID": dlsCoID,
                "CardNo": CardNo, "dai": dai,
                "LLock": LLock, "pdoors": pdoors,
                "BDate": BDate, "EDate": EDate,
                "LockNoHex": LockNoHex,
            },
            timeout=timeout,
        )

    def direct_read_usb(self, *, d12: int = 1,
                        timeout: float = 6.0) -> Dict[str, Any]:
        """CardLock 原生读卡：DirectReadUSB。"""
        return self._call("direct_read_usb", {"d12": d12}, timeout=timeout)

    def direct_write_usb(self, *, d12: int = 1, card_hex: str,
                         timeout: float = 6.0) -> Dict[str, Any]:
        """CardLock 原生写卡：DirectWriteUSB（16 字节 hex）。"""
        return self._call(
            "direct_write_usb",
            {"d12": d12, "card_hex": card_hex},
            timeout=timeout,
        )

    def read_record(self, *, d12: int = 1,
                    timeout: float = 6.0) -> Dict[str, Any]:
        """CardLock 原生读取发卡记录：ReadRecord。"""
        return self._call("read_record", {"d12": d12}, timeout=timeout)

    def direct_compose_guest_card(self, *, d12: int = 1, dlsCoID: int,
                                  BDate: str, EDate: str, LockNoHex: str,
                                  CardNo: int = 1, dai: int = 0,
                                  LLock: int = 1, pdoors: int = 0,
                                  timeout: float = 10.0) -> Dict[str, Any]:
        """发客人卡 — CardLock 原生 DirectWriteUSB 路径（备选）。
        firmware 解锁后优先走此方法与 CardLock 保持一致的 I/O 路径。"""
        return self._call(
            "direct_compose_guest_card",
            {
                "d12": d12, "dlsCoID": dlsCoID,
                "CardNo": CardNo, "dai": dai,
                "LLock": LLock, "pdoors": pdoors,
                "BDate": BDate, "EDate": EDate,
                "LockNoHex": LockNoHex,
            },
            timeout=timeout,
        )

    # ──────────────── 桥泛化：profile 动态绑定 ────────────────

    def bind_from_profile(self, profile: Dict[str, Any], *,
                          timeout: float = 5.0) -> Dict[str, Any]:
        """从 profile 动态绑定 DLL 函数（无需预知 V9 函数名）。
        profile 格式见 rfl_bridge_32.py 的 bind_from_profile 文档。
        """
        return self._call("bind_from_profile", {"profile": profile}, timeout=timeout)

    def generic_initialize(self, *, init_fn_name: str,
                           param_list: Optional[List[int]] = None,
                           timeout: float = 6.0) -> Dict[str, Any]:
        """通用初始化尝试：自动试多个参数值。"""
        return self._call(
            "generic_initialize",
            {"init_fn_name": init_fn_name, "param_list": param_list or [0, 1]},
            timeout=timeout,
        )

    def generic_read(self, *, read_fn_name: str, d12: int = 1,
                     timeout: float = 6.0) -> Dict[str, Any]:
        """通用读卡：使用指定函数名。"""
        return self._call(
            "generic_read",
            {"read_fn_name": read_fn_name, "d12": d12},
            timeout=timeout,
        )

    def generic_call(self, *, fn_name: str, args: Optional[List[Dict]] = None,
                     timeout: float = 8.0) -> Dict[str, Any]:
        """通用调用任意已绑定的 DLL 函数。"""
        return self._call(
            "generic_call",
            {"fn_name": fn_name, "args": args or []},
            timeout=timeout,
        )


# ──────────────────────────────────────────────────────────────────
# 单例工厂
# ──────────────────────────────────────────────────────────────────

_singleton: Optional[RflBridge] = None
_singleton_lock = threading.Lock()


def get_bridge() -> RflBridge:
    """全局唯一的桥接实例。多个 LockAdapter 共享。"""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = RflBridge()
        return _singleton


def shutdown_bridge() -> None:
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            try:
                _singleton.stop()
            except Exception:
                pass
            _singleton = None


# ──────────────────────────────────────────────────────────────────
# atexit 兜底
#
# 任何导入了 bridge_client 的进程（主程序 / 探测脚本 / 单元测试），
# 在 Python 解释器正常退出时都会触发这里：保证至少给 DLL 一次
# CloseUSB 机会，把 32 位桥接子进程优雅停掉，释放单 USB 互斥锁。
#
# 注意：硬 kill（pkill -9 / 任务管理器强结束）不会触发 atexit，
# 那种场景就只能靠 _rescue_v9_usb.bat / .ps1 救援脚本来兜底。
# ──────────────────────────────────────────────────────────────────

def _atexit_shutdown_bridge() -> None:
    try:
        shutdown_bridge()
    except Exception:
        pass


atexit.register(_atexit_shutdown_bridge)
