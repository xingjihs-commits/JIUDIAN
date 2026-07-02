"""
rfl_bridge_32.py — 32 位 Python 子进程，加载 V9RFL.dll

为什么需要这个文件？
====================
V9RFL.dll 是厂家发的 32 位 Win32 PE。64 位的 Python (Solid 主进程) 不能
直接 ctypes.WinDLL 它。所以我们起一个 32 位 Python 子进程，让它持有 DLL
句柄，主进程用 JSON-over-stdio 协议给它下命令。

子进程崩溃也不会拖垮 Solid 主进程，是个额外的好处。

协议
====
每条消息一行 JSON，stdout 是回包，stderr 是日志/异常。

请求:
{"id": 1, "method": "load_dll", "args": {"dll_path": "...", "extra_paths": [...]}}
{"id": 2, "method": "get_version", "args": {}}
{"id": 3, "method": "initialize", "args": {"d12": 1}}
{"id": 4, "method": "buzzer", "args": {"d12": 1, "t": 20}}
{"id": 5, "method": "guest_card", "args": {...}}
{"id": 6, "method": "ping", "args": {}} ← 主进程心跳
{"id": 7, "method": "exit", "args": {}}

回包:
{"id": 1, "ok": true, "ret": 0, "out": {...}} ← out 含函数返回的输出参数
{"id": 1, "ok": false, "error": "DLL not found"}

启动参数
========
本脚本可独立调试：
python rfl_bridge_32.py
然后从 stdin 输入一行 JSON 即可。

打包注意
========
PyInstaller 时本文件要单独打成 32 位 exe（rfl_bridge_32.exe），不能跟主程序
合并。打包命令大致：
pyinstaller --onefile --console rfl_bridge_32.py # 需要 32 位 Python 环境
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import traceback
from ctypes import (
    c_char_p,
    c_int,
    c_uint8,
    c_ubyte,
    c_void_p,
    create_string_buffer,
)


def _force_utf8_streams() -> None:
    """
    强制把 stdin/stdout/stderr 切到 UTF-8。

    背景：主进程发的 JSON 是 UTF-8 字节，但 Windows 中文版默认 sys.stdin
    走 cp936(GBK)。一旦请求带中文路径（比如 D:\\AI\\智能门锁管理系统...），
    GBK 解码会把 UTF-8 多字节序列错位，凭空多出 0x5C(`\\`)，json.loads
    报 "Invalid \\escape" 而我们却完全没碰过那些反斜杠。

    Python 3.7+ 的 reconfigure 是无副作用的，重复调用也安全。
    """
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        except Exception:
            pass


_force_utf8_streams()


def _log(msg: str) -> None:
    """日志到 stderr，主进程会收集。"""
    try:
        sys.stderr.write(f"[bridge32] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


class DllSession:
    """持有一个 V9RFL.dll 句柄 + 它的所有函数原型。"""

    def __init__(self):
        self.dll = None
        self.dll_path = ""
        self.fn = {}

    # ──────────────────────────────────────────────────────────────
    # 加载 DLL
    # ──────────────────────────────────────────────────────────────

    def load_dll(self, dll_path: str, extra_paths: list[str] | None = None) -> dict:
        """加载 V9RFL.dll，并把相关函数原型设置好。

        注意：不在此处显式调用 d12.IniUsb()。实测表明显式调用返回 259
        会搞乱 AVR-USB 底层状态机，导致后续 initializeUSB(0/1) 都受影响。
        让 initializeUSB(d12=0) 自己内部调 d12.IniUsb() 就行了。
        """
        extra_paths = extra_paths or []
        dll_dir = os.path.dirname(dll_path)
        try:
            if dll_dir:
                os.chdir(dll_dir)
        except Exception as e:
            _log(f"chdir to {dll_dir} failed: {e}")
        # Windows 8+ 需要明确把额外目录加入搜索路径，d12.dll 等依赖才能找到
        added_dirs = []
        try:
            for p in [dll_dir] + extra_paths:
                if not p:
                    continue
                if hasattr(os, "add_dll_directory"):
                    try:
                        h = os.add_dll_directory(p)
                        added_dirs.append(h)
                    except Exception as e:
                        _log(f"add_dll_directory failed for {p}: {e}")
        except Exception as e:
            _log(f"add_dll_directory setup error: {e}")

        # === 加载 V9RFL.dll ===
        try:
            self.dll = ctypes.WinDLL(dll_path)
            self.dll_path = dll_path
        except Exception as e:
            # 把真实原因同时回执 + 打到 stderr，便于 UI 上看到。
            try:
                import sys as _sys
                exists = os.path.isfile(dll_path)
                size = os.path.getsize(dll_path) if exists else -1
                _log(
                    f"WinDLL load failed: {type(e).__name__}: {e} "
                    f"| path={dll_path} | exists={exists} | size={size} "
                    f"| extra_paths={extra_paths} | py={_sys.version_info[:3]} "
                    f"| 32bit={_sys.maxsize <= 2**32}"
                )
            except Exception:
                pass
            return {
                "loaded": False,
                "error": f"WinDLL load failed: {type(e).__name__}: {e}",
                "dll_path": dll_path,
                "extra_paths": extra_paths,
            }

        self._setup_prototypes()
        return {
            "loaded": True,
            "dll_path": dll_path,
            "functions_bound": sorted(self.fn.keys()),
        }

    def _try_bind(self, name: str, restype, argtypes) -> bool:
        """尝试把 name 函数挂到 self.fn，失败不抛错。"""
        try:
            f = getattr(self.dll, name)
            f.restype = restype
            f.argtypes = argtypes
            self.fn[name] = f
            return True
        except Exception as e:
            _log(f"bind {name} failed: {e}")
            return False

    def _setup_prototypes(self) -> None:
        """按公开文档绑定函数原型。绑不到的会被记录但不影响其它。"""
        if self.dll is None:
            return

        # 基础生命周期
        self._try_bind("GetDLLVersion", c_int, [c_char_p])
        self._try_bind("initializeUSB", c_int, [c_ubyte])
        self._try_bind("CloseUSB", None, [])
        self._try_bind("Buzzer", c_int, [c_ubyte, c_ubyte])

        # 读卡
        self._try_bind("ReadCard", c_int, [c_ubyte, c_char_p])

        # 客人卡
        # int GuestCard(uchar d12, int dlsCoID, uchar CardNo, uchar dai,
        # uchar LLock, uchar pdoors,
        # uchar BDate[10], uchar EDate[10],
        # uchar LockNo[8], uchar* cardHexStr)
        self._try_bind(
            "GuestCard",
            c_int,
            [c_ubyte, c_int, c_ubyte, c_ubyte, c_ubyte, c_ubyte,
             c_char_p, c_char_p, c_char_p, c_char_p],
        )

        # 挂失卡
        self._try_bind(
            "LimitCard",
            c_int,
            [c_ubyte, c_int, c_ubyte, c_ubyte,
             c_char_p, c_char_p, c_char_p],
        )

        # 擦卡
        self._try_bind("CardErase", c_int, [c_ubyte, c_int, c_char_p])

        # 解析卡
        self._try_bind("GetCardTypeByCardDataStr", c_int, [c_char_p, c_char_p])
        self._try_bind("GetGuestLockNoByCardDataStr", c_int, [c_int, c_char_p, c_char_p])
        self._try_bind("GetGuestETimeByCardDataStr", c_int, [c_int, c_char_p, c_char_p])

        # 低层写卡 / 读卡 / 工具函数（用于克隆测试和参数反推）
        # WriteCard 签名未公开，多种猜测都试一下
        self._try_bind("WriteCard", c_int, None)
        self._try_bind("ReadGusetCard", c_int, None)
        self._try_bind("a_hex", c_int, None)
        self._try_bind("hex_a", c_int, None)
        self._try_bind("GetGuestSTimeByCardDataStr", c_int, [c_int, c_char_p, c_char_p])
        self._try_bind("GetOpenRecordByDataStr", c_int, None)

        # CardLock.exe 实际使用的底层 I/O 函数（v3.0 反汇编证实）
        # DirectReadUSB / DirectWriteUSB / ReadRecord 是 CardLock 的 8 个
        # DLL调用之一，未经签名公开，用 argtypes=None 松散绑定。
        self._try_bind("DirectReadUSB", c_int, None)
        self._try_bind("DirectWriteUSB", c_int, None)
        self._try_bind("ReadRecord", c_int, None)

        # ── 已确认 标准调用签名的专用卡函数 ──────────────────
        # 证据：dll_exports.txt 中 _CheckOutCard@24 等 @N 后缀 = stdcall
        # 探针（special_card_sigs.json）verified "match" 含有效 payload
        #
        # int __stdcall CheckOutCard(uchar d12, int dlsCoID, uchar CardNo,
        # uchar dai, char BDate[11], char* outBuf)
        self._try_bind(
            "CheckOutCard", c_int,
            [c_ubyte, c_int, c_ubyte, c_ubyte, c_char_p, c_char_p],
        )
        # int __stdcall RecordCard(uchar d12, int dlsCoID, uchar CardNo,
        # uchar dai, char BDate[11], char* outBuf)
        self._try_bind(
            "RecordCard", c_int,
            [c_ubyte, c_int, c_ubyte, c_ubyte, c_char_p, c_char_p],
        )
        # int __stdcall RoomSetCard(uchar d12, int dlsCoID, uchar CardNo,
        # uchar dai, char LockNo[9], char BDate[11],
        # char* outBuf)
        self._try_bind(
            "RoomSetCard", c_int,
            [c_ubyte, c_int, c_ubyte, c_ubyte, c_char_p, c_char_p, c_char_p],
        )
        # int __stdcall TimeSetCard(uchar d12, int dlsCoID, uchar CardNo,
        # uchar dai, char BDate[11], char* outBuf)
        self._try_bind(
            "TimeSetCard", c_int,
            [c_ubyte, c_int, c_ubyte, c_ubyte, c_char_p, c_char_p],
        )

        # 各种卡型（按厂家命名习惯尝试，绑不到就算）
        # 注意：已绑定的 CheckOutCard/RecordCard/RoomSetCard/TimeSetCard
        # 不在此循环中重复绑定。余下的9个函数参数惯例未确认（推测为
        # Delphi register 惯例），保持 argtypes=None 松散绑定。
        for name in (
            "MasterCard", "FloorCard", "BuildingCard", "GroupCard",
            "GroupSetCard", "EmergencyCard",
            "IniCard", "SpecialCard",
        ):
            self._try_bind(name, c_int, None)  # argtypes=None 表示不严格检查

    # ──────────────────────────────────────────────────────────────
    # 具体方法（每个对应一种 RPC）
    # ──────────────────────────────────────────────────────────────

    def _need_fn(self, name: str):
        if name not in self.fn:
            raise RuntimeError(f"DLL 未导出或绑定失败: {name}")
        return self.fn[name]

    def get_version(self) -> dict:
        f = self._need_fn("GetDLLVersion")
        buf = create_string_buffer(256)
        ret = f(buf)
        return {"ret": int(ret), "out": {"version": buf.value.decode("latin-1", errors="replace")}}

    def initialize(self, d12: int = 0) -> dict:
        f = self._need_fn("initializeUSB")
        ret = f(c_ubyte(d12))
        return {"ret": int(ret), "out": {}}

    def encoder_check(self) -> dict:
        """发卡前编码器连接检查——调 initializeUSB(d12=1) 轻量检测"""
        return self.initialize(d12=1)

    def keepalive(self) -> dict:
        """读卡器keepalive——调 initializeUSB(d12=1) 防止固件超时断开"""
        return self.initialize(d12=1)

    def close_usb(self) -> dict:
        if "CloseUSB" in self.fn:
            try:
                self.fn["CloseUSB"]()
            except Exception as e:
                _log(f"CloseUSB error: {e}")
        return {"ret": 0, "out": {}}

    def buzzer(self, d12: int = 1, t: int = 20) -> dict:
        """t 单位 10ms，所以 t=20 = 200ms 嘀一声。"""
        f = self._need_fn("Buzzer")
        ret = f(c_ubyte(d12), c_ubyte(t))
        return {"ret": int(ret), "out": {}}

    @staticmethod
    def _read_card_parse(buf_value: bytes) -> tuple[str, str, bool, str]:
        """统一处理 DLL ReadCard / DirectReadUSB 输出的二进制帧。

        V9RFL.dll 的 ReadCard 在 out_buf 写入**二进制字节**，并非
        ASCII 十六进制。当前数据被 create_string_buffer 的 .value 截到 \0。
        常见帧头（3 字节 / 6 十六进制 字符）：
        55 21 01 → 卡上有真实数据
        55 15 01 → 空白卡（卡数据 全 FF）
        55 01 00 → 发卡器上无卡

        返回 (hex_str, header, has_card, 卡数据)。
        """
        raw_bytes = buf_value
        # DLL可能写 ASCII 十六进制 也可能写二进制——自动判断：
        # 方式 A：首字节是 0x55 → 二进制帧，十六进制编码
        # 方式 B：首字节是 0x35（'5'）→ 已经是 ASCII 十六进制，latin-1 解码
        if raw_bytes and raw_bytes[0] in (0x55,):  # 二进制帧
            raw_hex = raw_bytes.hex().upper()
        else:
            raw_hex = raw_bytes.decode("latin-1", errors="replace").upper()
        payload = ""
        header = raw_hex[:6] if len(raw_hex) >= 6 else ""
        has_card = header in ("552101", "551501")
        if has_card and len(raw_hex) >= 6 + 32:
            payload = raw_hex[6:6 + 32]
        return raw_hex, header, has_card, payload

    def read_card(self, d12: int = 1) -> dict:
        """读卡。
        返回 十六进制 是 auto-detected 的整帧；同时单独给出 16 字节 卡数据 (32 字符 十六进制)。

        帧头约定（实测）：
        552101 → 卡上有真实数据
        551501 → 卡为空白（卡数据 仅含酒店前缀 + 0xFF）
        550100 → 发卡器上无卡
        """
        f = self._need_fn("ReadCard")
        buf = create_string_buffer(256)
        ret = f(c_ubyte(d12), buf)
        raw_hex, header, has_card, payload = self._read_card_parse(buf.value)
        return {
            "ret": int(ret),
            "out": {
                "hex": raw_hex,
                "header": header,
                "has_card": has_card,
                "payload": payload,
            },
        }

    def guest_card(
        self,
        d12: int,
        dlsCoID: int,
        CardNo: int,
        dai: int,
        LLock: int,
        pdoors: int,
        BDate: str,
        EDate: str,
        LockNo: str,
    ) -> dict:
        f = self._need_fn("GuestCard")
        bdate_buf = create_string_buffer(BDate.encode("latin-1"), size=11)
        edate_buf = create_string_buffer(EDate.encode("latin-1"), size=11)
        lockno_buf = create_string_buffer(LockNo.encode("latin-1"), size=9)
        out_buf = create_string_buffer(256)
        ret = f(
            c_ubyte(d12),
            c_int(dlsCoID),
            c_ubyte(CardNo),
            c_ubyte(dai),
            c_ubyte(LLock),
            c_ubyte(pdoors),
            bdate_buf,
            edate_buf,
            lockno_buf,
            out_buf,
        )
        return {
            "ret": int(ret),
            "out": {"hex": out_buf.value.decode("latin-1", errors="replace")},
        }

    def limit_card(
        self,
        d12: int,
        dlsCoID: int,
        CardNo: int,
        dai: int,
        BDate: str,
        LCardNo: str,
    ) -> dict:
        f = self._need_fn("LimitCard")
        bdate_buf = create_string_buffer(BDate.encode("latin-1"), size=11)
        lcardno_buf = create_string_buffer(LCardNo.encode("latin-1"), size=5)
        out_buf = create_string_buffer(256)
        ret = f(
            c_ubyte(d12),
            c_int(dlsCoID),
            c_ubyte(CardNo),
            c_ubyte(dai),
            bdate_buf,
            lcardno_buf,
            out_buf,
        )
        return {
            "ret": int(ret),
            "out": {"hex": out_buf.value.decode("latin-1", errors="replace")},
        }

    def card_erase(self, d12: int, dlsCoID: int, card_hex: str) -> dict:
        f = self._need_fn("CardErase")
        buf = create_string_buffer(card_hex.encode("latin-1"), size=max(256, len(card_hex) + 1))
        ret = f(c_ubyte(d12), c_int(dlsCoID), buf)
        return {
            "ret": int(ret),
            "out": {"hex": buf.value.decode("latin-1", errors="replace")},
        }

    def parse_card_type(self, card_hex: str) -> dict:
        f = self._need_fn("GetCardTypeByCardDataStr")
        in_buf = create_string_buffer(card_hex.encode("latin-1"))
        out_buf = create_string_buffer(8)
        ret = f(in_buf, out_buf)
        return {
            "ret": int(ret),
            "out": {"card_type": out_buf.value.decode("latin-1", errors="replace")},
        }

    def read_open_record(self, card_hex: str) -> dict:
        """读记录卡，返回开门日志原文。基于逆向：GetOpenRecordByDataStr 2 参数，
        ret=8 stack=8，入参 card_hex, 出参 out_buf。"""
        f = self._need_fn("GetOpenRecordByDataStr")
        in_buf = create_string_buffer(card_hex.encode("latin-1"))
        out_buf = create_string_buffer(4096)
        ret = f(in_buf, out_buf)
        return {
            "ret": int(ret),
            "out": {"records": out_buf.value.decode("latin-1", errors="replace")},
        }

    def get_guest_lock_no(self, dlsCoID: int, card_hex: str) -> dict:
        f = self._need_fn("GetGuestLockNoByCardDataStr")
        in_buf = create_string_buffer(card_hex.encode("latin-1"))
        out_buf = create_string_buffer(16)
        ret = f(c_int(dlsCoID), in_buf, out_buf)
        return {
            "ret": int(ret),
            "out": {"lock_no": out_buf.value.decode("latin-1", errors="replace")},
        }

    # ──────────────────────────────────────────────────────────────
    # 低层写卡 / 克隆实验
    # ──────────────────────────────────────────────────────────────

    def write_card(self, d12: int, card_hex: str, variant: str = "binary") -> dict:
        """
        把任意 十六进制 数据写到卡上。试两种 buffer 编码：

        variant="ascii" : WriteCard(uchar d12, uchar* ascii_hex_string)
        buf 内容是 32 字符 ASCII "C92B..." 共 33 bytes (含 null)
        variant="binary" : WriteCard(uchar d12, uchar* binary_16bytes)
        buf 内容是 16 字节 二进制 \xC9\x2B... 共 16 bytes
        variant="bin64" : 二进制，但 buffer 长度 64 字节（防止 DLL 越界读）
        """
        f = self._need_fn("WriteCard")
        try:
            if variant == "ascii":
                buf = create_string_buffer(card_hex.encode("latin-1"),
                                           size=max(64, len(card_hex) + 1))
            elif variant == "binary":
                data = bytes.fromhex(card_hex)
                buf = create_string_buffer(data, size=len(data))
            elif variant == "bin64":
                data = bytes.fromhex(card_hex)
                # buffer 拉长到 64 字节，剩余填 0
                buf = create_string_buffer(data + b"\x00" * (64 - len(data)), size=64)
            else:
                return {"ret": -1, "out": {"error": f"unknown variant: {variant}"}}

            ret = f(c_ubyte(d12), buf)
            return {
                "ret": int(ret),
                "out": {
                    "hex_in": card_hex,
                    "buf_back_hex": buf.raw[:max(32, len(card_hex))].hex().upper() if hasattr(buf, "raw") else buf.value.hex().upper(),
                    "variant": variant,
                }
            }
        except Exception as e:
            return {"ret": -1, "out": {"error": f"{type(e).__name__}: {e}", "variant": variant}}

    def checkout_card(
        self,
        d12: int,
        dlsCoID: int,
        CardNo: int,
        dai: int,
        BDate: str,
    ) -> dict:
        """退房卡 — 直调 V9RFL.dll._CheckOutCard@24 (stdcall, 6 params)。

        探针已验证 (special_card_sigs.json verdict="match", ret=0)：
        输入: C92B20B747DEB312007115DB464DC843 (byte9=71, type_nibble=7)
        签名: u8(d12), i32(dlsCoID), u8(CardNo), u8(dai), cstr(BDate), out(256)
        """
        f = self._need_fn("CheckOutCard")
        bdate_buf = create_string_buffer(BDate.encode("latin-1"), size=11)
        out_buf = create_string_buffer(256)
        ret = f(
            c_ubyte(d12), c_int(dlsCoID),
            c_ubyte(CardNo), c_ubyte(dai),
            bdate_buf, out_buf,
        )
        return {
            "ret": int(ret),
            "out": {"hex": out_buf.value.decode("latin-1", errors="replace")},
        }

    def record_card(
        self,
        d12: int,
        dlsCoID: int,
        CardNo: int,
        dai: int,
        BDate: str,
    ) -> dict:
        """记录卡 — 直调 V9RFL.dll._RecordCard@24 (stdcall, 6 params)。

        探针已验证 (special_card_sigs.json verdict="match")：
        输入: C92B20B7D4CBFCF0001160BCF096F545 (byte9=11, type_nibble=1)
        """
        f = self._need_fn("RecordCard")
        bdate_buf = create_string_buffer(BDate.encode("latin-1"), size=11)
        out_buf = create_string_buffer(256)
        ret = f(
            c_ubyte(d12), c_int(dlsCoID),
            c_ubyte(CardNo), c_ubyte(dai),
            bdate_buf, out_buf,
        )
        return {
            "ret": int(ret),
            "out": {"hex": out_buf.value.decode("latin-1", errors="replace")},
        }

    def room_set_card(
        self,
        d12: int,
        dlsCoID: int,
        CardNo: int,
        dai: int,
        LockNo: str,
        BDate: str,
    ) -> dict:
        """房号设置卡 — 直调 V9RFL.dll._RoomSetCard@28 (stdcall, 7 params)。

        探针已验证 (special_card_sigs.json verdict="match")：
        输入: C92B20B7F0F0F000002115DB46209A50 (byte9=21, type_nibble=2)
        """
        f = self._need_fn("RoomSetCard")
        lockno_buf = create_string_buffer(LockNo.encode("latin-1"), size=9)
        bdate_buf = create_string_buffer(BDate.encode("latin-1"), size=11)
        out_buf = create_string_buffer(256)
        ret = f(
            c_ubyte(d12), c_int(dlsCoID),
            c_ubyte(CardNo), c_ubyte(dai),
            lockno_buf, bdate_buf, out_buf,
        )
        return {
            "ret": int(ret),
            "out": {"hex": out_buf.value.decode("latin-1", errors="replace")},
        }

    def time_set_card(
        self,
        d12: int,
        dlsCoID: int,
        CardNo: int,
        dai: int,
        BDate: str,
    ) -> dict:
        """时钟设置卡 — 直调 V9RFL.dll._TimeSetCard@24 (stdcall, 6 params)。

        探针已验证 (special_card_sigs.json verdict="match")：
        输入: C92B20B7DCAD4F14003160BCF000F244 (byte9=31, type_nibble=3)
        """
        f = self._need_fn("TimeSetCard")
        bdate_buf = create_string_buffer(BDate.encode("latin-1"), size=11)
        out_buf = create_string_buffer(256)
        ret = f(
            c_ubyte(d12), c_int(dlsCoID),
            c_ubyte(CardNo), c_ubyte(dai),
            bdate_buf, out_buf,
        )
        return {
            "ret": int(ret),
            "out": {"hex": out_buf.value.decode("latin-1", errors="replace")},
        }

    def guest_card_v2(
        self,
        d12: int,
        dlsCoID: int,
        CardNo: int,
        dai: int,
        LLock: int,
        pdoors: int,
        BDate: str,
        EDate: str,
        LockNo: str,
        lockno_mode: str = "ascii",
    ) -> dict:
        """
        实验版 GuestCard：可以选择 LockNo 用 ASCII 还是 二进制 传入。

        lockno_mode:
        "ascii" → LockNo 当 ASCII 8 字符传 (原来的方式)
        "binary" → "801C0301" 先转 二进制 \\x80\\x1c\\x03\\x01\\x00\\x00\\x00\\x00 再传
        "binary4"→ 只传前 4 字节 binary（短 buffer）
        """
        f = self._need_fn("GuestCard")
        bdate_buf = create_string_buffer(BDate.encode("latin-1"), size=11)
        edate_buf = create_string_buffer(EDate.encode("latin-1"), size=11)
        if lockno_mode == "binary":
            data = bytes.fromhex(LockNo.ljust(8, "0")[:8])
            lockno_buf = create_string_buffer(data + b"\x00\x00\x00\x00", size=9)
        elif lockno_mode == "binary4":
            data = bytes.fromhex(LockNo.ljust(8, "0")[:8])
            lockno_buf = create_string_buffer(data, size=5)
        else:
            lockno_buf = create_string_buffer(LockNo.encode("latin-1"), size=9)
        out_buf = create_string_buffer(256)
        ret = f(
            c_ubyte(d12), c_int(dlsCoID),
            c_ubyte(CardNo), c_ubyte(dai),
            c_ubyte(LLock), c_ubyte(pdoors),
            bdate_buf, edate_buf, lockno_buf, out_buf,
        )
        return {
            "ret": int(ret),
            "out": {"hex": out_buf.value.decode("latin-1", errors="replace"),
                    "lockno_mode": lockno_mode}
        }

    def list_bound_functions(self) -> dict:
        return {"ret": 0, "out": {"functions": sorted(self.fn.keys())}}

    def compose_guest_card(
        self,
        d12: int,
        dlsCoID: int,
        CardNo: int,
        dai: int,
        LLock: int,
        pdoors: int,
        BDate: str,
        EDate: str,
        LockNoHex: str,
    ) -> dict:
        """实测验证的发卡流水线：
        1) GuestCard(binary4 LockNo) → DLL 写入卡片，时间字段正确，锁号字段会错
        2) ReadCard → 取回 16 字节 payload
        3) payload[4:8] = 正确 二进制 LockNo
        4) WriteCard(二进制) → 覆盖写出正确 payload
        5) ReadCard → 校验

        LockNoHex: 8 个 十六进制 字符 (4 字节锁号)，例如 "801C0301"
        """
        # 1) GuestCard, binary4 LockNo
        guest_fn = self._need_fn("GuestCard")
        bdate_buf = create_string_buffer(BDate.encode("latin-1"), size=11)
        edate_buf = create_string_buffer(EDate.encode("latin-1"), size=11)
        try:
            lock_bin = bytes.fromhex(LockNoHex.ljust(8, "0")[:8])
        except ValueError as e:
            return {"ret": -1, "out": {"error": f"bad LockNoHex: {LockNoHex} ({e})"}}
        lockno_buf = create_string_buffer(lock_bin, size=5)
        out_buf = create_string_buffer(256)

        gret = guest_fn(
            c_ubyte(d12), c_int(dlsCoID),
            c_ubyte(CardNo), c_ubyte(dai),
            c_ubyte(LLock), c_ubyte(pdoors),
            bdate_buf, edate_buf, lockno_buf, out_buf,
        )
        guest_hex = out_buf.value.decode("latin-1", errors="replace")
        if int(gret) != 0:
            return {
                "ret": int(gret),
                "out": {
                    "stage": "GuestCard",
                    "guest_hex": guest_hex,
                    "error": f"GuestCard returned {gret}",
                },
            }

        # 2) ReadCard，拿到 16 字节 payload
        read_resp = self.read_card(d12=d12)
        payload = (read_resp.get("out") or {}).get("payload", "")
        if not payload or len(payload) != 32:
            return {
                "ret": -1,
                "out": {
                    "stage": "ReadAfterGuest",
                    "read": read_resp,
                    "error": "未从卡读到 16 字节 payload",
                },
            }

        # 3) 替换 byte 4..7 (字符 8..16) 为正确 二进制 LockNo
        new_payload = payload[:8] + LockNoHex.upper().ljust(8, "0")[:8] + payload[16:]

        # 4) WriteCard binary
        wfn = self._need_fn("WriteCard")
        try:
            data = bytes.fromhex(new_payload)
        except ValueError as e:
            return {"ret": -1, "out": {"error": f"compose payload bad hex: {new_payload} ({e})"}}
        wbuf = create_string_buffer(data, size=len(data))
        wret = wfn(c_ubyte(d12), wbuf)
        if int(wret) != 0:
            return {
                "ret": int(wret),
                "out": {
                    "stage": "WriteCard",
                    "guest_hex": guest_hex,
                    "guest_payload": payload,
                    "new_payload": new_payload,
                    "error": f"WriteCard returned {wret}",
                },
            }

        # 5) 校验
        verify = self.read_card(d12=d12)
        verify_payload = (verify.get("out") or {}).get("payload", "")
        ok = verify_payload.upper() == new_payload.upper()
        return {
            "ret": 0 if ok else -1,
            "out": {
                "stage": "OK" if ok else "VerifyMismatch",
                "guest_hex": guest_hex,
                "guest_payload": payload,
                "new_payload": new_payload,
                "verify_payload": verify_payload,
                "match": ok,
            },
        }

    def get_guest_etime(self, dlsCoID: int, card_hex: str) -> dict:
        f = self._need_fn("GetGuestETimeByCardDataStr")
        in_buf = create_string_buffer(card_hex.encode("latin-1"))
        out_buf = create_string_buffer(16)
        ret = f(c_int(dlsCoID), in_buf, out_buf)
        return {
            "ret": int(ret),
            "out": {"e_time": out_buf.value.decode("latin-1", errors="replace")},
        }

    # ──────────────────────────────────────────────────────────────
    # CardLock.exe 原生 I/O 函数（v3.0 反汇编证实的 8 函数之三）
    #
    # DirectReadUSB / DirectWriteUSB / ReadRecord 是 CardLock 实际
    # 使用的底层读写函数。签名未公开，以 argtypes=None 松散调用。
    # 固件 解锁后用这些函数替换 ReadCard / WriteCard。
    # ──────────────────────────────────────────────────────────────

    def direct_read_usb(self, d12: int = 1) -> dict:
        """CardLock.exe 读取卡片的底层函数。

        返回结构与 read_card() 一致：
        ret → int (0=成功)
        out.hex/header/has_card/payload → 帧头解析
        """
        f = self._need_fn("DirectReadUSB")
        buf = create_string_buffer(256)
        ret = f(c_ubyte(d12), buf)
        raw_hex, header, has_card, payload = self._read_card_parse(buf.value)
        return {
            "ret": int(ret),
            "out": {
                "hex": raw_hex,
                "header": header,
                "has_card": has_card,
                "payload": payload,
            },
        }

    def direct_write_usb(self, d12: int, card_hex: str) -> dict:
        """CardLock.exe 写入卡片的底层函数。

        card_hex: 32 字符 十六进制 字符串（16 字节 卡数据）。
        """
        f = self._need_fn("DirectWriteUSB")
        try:
            data = bytes.fromhex(card_hex)
        except ValueError as e:
            return {"ret": -1, "out": {"error": f"bad hex: {e}"}}
        buf = create_string_buffer(data, size=len(data))
        ret = f(c_ubyte(d12), buf)
        return {
            "ret": int(ret),
            "out": {"hex_in": card_hex},
        }

    def read_record(self, d12: int = 1) -> dict:
        """CardLock.exe 读取发卡记录的函数（ReadRecord）。"""
        f = self._need_fn("ReadRecord")
        buf = create_string_buffer(4096)
        ret = f(c_ubyte(d12), buf)
        return {
            "ret": int(ret),
            "out": {"hex": buf.value.decode("latin-1", errors="replace")},
        }

    def direct_compose_guest_card(
        self,
        d12: int,
        dlsCoID: int,
        CardNo: int,
        dai: int,
        LLock: int,
        pdoors: int,
        BDate: str,
        EDate: str,
        LockNoHex: str,
    ) -> dict:
        """发客人卡 — 使用 CardLock 原生路径 DirectWriteUSB。

        与 compose_guest_card 相同逻辑（5 步管线），但步骤 2/4/5 用
        DirectReadUSB / DirectWriteUSB 替代 ReadCard / WriteCard。
        固件 解锁后优先走此方法。
        """
        # 1) GuestCard(二进制四字节 LockNo) — DLL 写时间/授权，锁号会错
        guest_fn = self._need_fn("GuestCard")
        bdate_buf = create_string_buffer(BDate.encode("latin-1"), size=11)
        edate_buf = create_string_buffer(EDate.encode("latin-1"), size=11)
        try:
            lock_bin = bytes.fromhex(LockNoHex.ljust(8, "0")[:8])
        except ValueError as e:
            return {"ret": -1, "out": {"error": f"bad LockNoHex: {LockNoHex} ({e})"}}
        lockno_buf = create_string_buffer(lock_bin, size=5)
        out_buf = create_string_buffer(256)

        gret = guest_fn(
            c_ubyte(d12), c_int(dlsCoID),
            c_ubyte(CardNo), c_ubyte(dai),
            c_ubyte(LLock), c_ubyte(pdoors),
            bdate_buf, edate_buf, lockno_buf, out_buf,
        )
        guest_hex = out_buf.value.decode("latin-1", errors="replace")
        if int(gret) != 0:
            return {
                "ret": int(gret),
                "out": {
                    "stage": "GuestCard",
                    "guest_hex": guest_hex,
                    "error": f"GuestCard returned {gret}",
                },
            }

        # 2) DirectReadUSB — 取回 16 字节 payload
        read_resp = self.direct_read_usb(d12=d12)
        payload = (read_resp.get("out") or {}).get("payload", "")
        if not payload or len(payload) != 32:
            return {
                "ret": -1,
                "out": {
                    "stage": "DirectReadAfterGuest",
                    "read": read_resp,
                    "error": "DirectReadUSB 未从卡读到 16 字节 payload",
                },
            }

        # 3) 替换 byte 4..7 为正确 二进制 LockNo
        new_payload = payload[:8] + LockNoHex.upper().ljust(8, "0")[:8] + payload[16:]

        # 4) DirectWriteUSB binary
        wresp = self.direct_write_usb(d12=d12, card_hex=new_payload)
        if int(wresp.get("ret", -1)) != 0:
            return {
                "ret": int(wresp.get("ret", -1)),
                "out": {
                    "stage": "DirectWriteUSB",
                    "guest_hex": guest_hex,
                    "guest_payload": payload,
                    "new_payload": new_payload,
                    "error": f"DirectWriteUSB returned {wresp.get('ret')}",
                },
            }

        # 5) DirectReadUSB 校验
        verify = self.direct_read_usb(d12=d12)
        verify_payload = (verify.get("out") or {}).get("payload", "")
        ok = verify_payload.upper() == new_payload.upper()
        return {
            "ret": 0 if ok else -1,
            "out": {
                "stage": "OK" if ok else "VerifyMismatch",
                "guest_hex": guest_hex,
                "guest_payload": payload,
                "new_payload": new_payload,
                "verify_payload": verify_payload,
                "match": ok,
                "path": "direct",
            },
        }

    # ──────────────────────────────────────────────────────────────
    # 通用 DLL函数盲探调用
    #
    # 用于 12 个特殊卡函数（MasterCard / BuildingCard / FloorCard /
    # EmergencyCard / GroupCard / GroupSetCard / CheckOutCard / RecordCard /
    # RoomSetCard / TimeSetCard / IniCard / SpecialCard）的盲探发现
    # 参数顺序与类型。
    # ──────────────────────────────────────────────────────────────

    def call_card_fn(self, fn_name: str, signature: list) -> dict:
        """
        signature 是一串 {"kind": str, "value": ...} 字典：

        {"kind": "u8", "value": 1} → c_ubyte(1)
        {"kind": "i32", "value": 2826423} → c_int(2826423)
        {"kind": "cstr", "value": "abc",
         "size": 11} → create_string_buffer 长度 size
        {"kind": "outbuf", "size": 256} → create_string_buffer(size)，事后回读

        返回:
        ret + 每个 outbuf 的内容（十六进制 大写）
        """
        if fn_name not in self.fn:
            return {"ret": -1, "out": {"error": f"function not bound: {fn_name}"}}
        f = self.fn[fn_name]

        ctypes_args = []
        out_handles = []  # (index, buffer)
        try:
            for idx, spec in enumerate(signature):
                kind = (spec or {}).get("kind", "")
                if kind == "u8":
                    ctypes_args.append(c_ubyte(int(spec.get("value", 0)) & 0xFF))
                elif kind == "i32":
                    ctypes_args.append(c_int(int(spec.get("value", 0))))
                elif kind == "cstr":
                    raw = str(spec.get("value", ""))
                    size = int(spec.get("size", len(raw) + 1))
                    buf = create_string_buffer(raw.encode("latin-1"), size=max(1, size))
                    ctypes_args.append(buf)
                elif kind == "outbuf":
                    size = int(spec.get("size", 256))
                    buf = create_string_buffer(size)
                    ctypes_args.append(buf)
                    out_handles.append((idx, buf))
                else:
                    return {"ret": -1, "out": {"error": f"unknown kind at idx {idx}: {kind!r}"}}
        except Exception as e:
            return {"ret": -1, "out": {"error": f"build args failed: {type(e).__name__}: {e}"}}

        # argtypes 已设为 None：允许默认转换，缺点是出错就直接进程崩溃。
        # 用 try/except 保护，让上层失败时能重启桥再试下一组。
        try:
            ret = f(*ctypes_args)
        except Exception as e:
            return {"ret": -1, "out": {"error": f"call failed: {type(e).__name__}: {e}"}}

        outs = {}
        for idx, buf in out_handles:
            try:
                raw_bytes = bytes(buf.raw)  # 完整尺寸的原始字节
            except Exception:
                raw_bytes = b""
            # value 是到 \0 截断的内容，对 ASCII 十六进制 输出适用
            try:
                ascii_value = buf.value.decode("latin-1", errors="replace")
            except Exception:
                ascii_value = ""
            outs[f"out{idx}"] = {
                "ascii": ascii_value,
                "raw_hex": raw_bytes.hex().upper(),
                "size": len(raw_bytes),
            }

        return {"ret": int(ret), "out": {"outs": outs, "fn": fn_name}}

    # ──────────────────────────────────────────────────────────────
    # 桥泛化：从 配置动态绑定任意品牌 DLL的函数
    # ──────────────────────────────────────────────────────────────

    def bind_from_profile(self, profile: dict) -> dict:
        """从 品牌配置 配置动态绑定 DLL 函数。

        品牌配置 格式：
        {
            "dll": {
                "path": "Lock9200.dll",
                "init": "init",  // 初始化函数名
                "read": "readcard",  // 读卡函数名
                "write": "writecard",  // 写卡函数名
                "guest": "guestcard",  // 发客人卡函数名
                "erase": "erasedata",  // 擦卡函数名（可选）
                "buzzer": "buzzer",  // 蜂鸣函数名（可选）
                "close": "closeusb",  // 关闭函数名（可选）
                "init_params": [1],  // init 参数候选值
            }
        }

        绑定策略：
        - 所有 品牌配置 中指定的函数都用 argtypes=None（松散绑定）尝试绑定
        - 绑成功的记入 self.fn，绑不成功的只打日志不报错
        - 返回 {bound: [...], missing: [...]}
        """
        if self.dll is None:
            return {"bound": [], "missing": ["DLL not loaded"]}

        dll_cfg = profile.get("dll", {})
        fn_names: list[str] = []
        for key in ("init", "read", "write", "guest", "erase", "buzzer", "close"):
            fn = dll_cfg.get(key)
            if fn:
                fn_names.append(fn)

        # 如果有 hardcoded 的 detect.exports，也加入绑定列表
        detect = profile.get("detect", {})
        exports = detect.get("exports", {})
        if isinstance(exports, dict):
            for _, fn in exports.items():
                if fn not in fn_names:
                    fn_names.append(fn)

        bound: list[str] = []
        missing: list[str] = []
        for name in fn_names:
            try:
                f = getattr(self.dll, name)
                # 松散绑定，不限制参数类型
                f.restype = c_int
                self.fn[name] = f
                bound.append(name)
            except Exception as e:
                _log(f"bind_from_profile: {name} failed: {e}")
                missing.append(name)

        return {"bound": bound, "missing": missing}

    def generic_call(self, fn_name: str, args: list) -> dict:
        """通用调用任何已绑定的 DLL 函数。

        args 的格式（与 call_card_fn 的 signature 格式兼容）：
        [{"kind": "u8", "value": 1},
         {"kind": "i32", "value": 2826423},
         {"kind": "cstr", "value": "abc", "size": 11},
         {"kind": "outbuf", "size": 256}]

        返回：
        {"ret": int, "out": {"outs": {...}, "fn": fn_name}}
        """
        if fn_name not in self.fn:
            return {"ret": -1, "out": {"error": f"function not bound: {fn_name}"}}

        return self.call_card_fn(fn_name, args)

    def generic_initialize(self, init_fn_name: str, param_list: list[int]) -> dict:
        """通用初始化：逐个尝试参数值，返回第一个成功的。"""
        results: list[dict] = []
        for param in param_list:
            try:
                fn = self.fn.get(init_fn_name)
                if fn is None:
                    return {"ret": -1, "out": {"error": f"init function '{init_fn_name}' not bound"}}

                ret = fn(c_ubyte(param & 0xFF))
                results.append({"param": param, "ret": int(ret)})
                if int(ret) == 0:
                    return {"ret": 0, "out": {"working_param": param, "tries": results}}
            except Exception as e:
                results.append({"param": param, "error": str(e)})

        return {"ret": -1, "out": {"error": "all init params failed", "tries": results}}

    # ────────────── 通用 DLL调用（一键绑定 + 调用）──────────────

    def dll_call(self, fn_name: str, params: list) -> dict:
        """通用 DLL 函数调用：自动绑定 + 调用任意导出函数。

        如果函数尚未绑定，尝试用 getattr 绑定（argtypes=None 松散绑定）。
        如果已绑定，直接调 call_card_fn 执行。

        params 格式同 call_card_fn 的 signature：
        [{"kind": "u8", "value": 1},
         {"kind": "i32", "value": 2826423},
         {"kind": "cstr", "value": "abc", "size": 11},
         {"kind": "outbuf", "size": 256}]
        """
        if self.dll is None:
            return {"ret": -1, "out": {"error": "DLL not loaded"}}
        # 自动绑定（如果尚未绑定）
        if fn_name not in self.fn:
            try:
                f = getattr(self.dll, fn_name)
                f.restype = c_int
                self.fn[fn_name] = f
            except Exception as e:
                return {"ret": -1, "out": {"error": f"bind failed: {type(e).__name__}: {e}"}}
        return self.call_card_fn(fn_name, params)

    def dll_list_exports(self) -> dict:
        """枚举 DLL的所有导出函数（返回已绑定的函数名列表）。"""
        if self.dll is None:
            return {"exports": [], "bound": []}
        bound = sorted(self.fn.keys())
        return {"exports": bound, "bound": bound}

    def generic_read(self, read_fn_name: str, d12: int = 1) -> dict:
        """通用读卡：使用指定的 read 函数名，返回与 read_card() 兼容的格式。"""
        fn = self.fn.get(read_fn_name)
        if fn is None:
            return {"ret": -1, "out": {"error": f"read function '{read_fn_name}' not bound"}}

        buf = create_string_buffer(256)
        try:
            ret = fn(c_ubyte(d12 & 0xFF), buf)
        except Exception as e:
            # 试只传 buf
            try:
                ret = fn(buf)
            except Exception as e2:
                return {"ret": -1, "out": {"error": f"read call failed: {e2}"}}

        raw_hex, header, has_card, payload = self._read_card_parse(buf.value)
        return {
            "ret": int(ret),
            "out": {
                "hex": raw_hex,
                "header": header,
                "has_card": has_card,
                "payload": payload,
            },
        }


# ──────────────────────────────────────────────────────────────────
# Dispatcher
# ──────────────────────────────────────────────────────────────────

def dispatch(session: DllSession, method: str, args: dict) -> dict:
    if method == "ping":
        return {"ret": 0, "out": {"pong": True}}
    if method == "load_dll":
        return session.load_dll(args.get("dll_path", ""), args.get("extra_paths", []))
    if method == "bind_from_profile":
        return session.bind_from_profile(args.get("profile", {}))
    if method == "generic_call":
        return session.generic_call(str(args.get("fn_name", "")), list(args.get("args", [])))
    if method == "generic_initialize":
        return session.generic_initialize(
            str(args.get("init_fn_name", "")),
            list(args.get("param_list", [0, 1])),
        )
    if method == "generic_read":
        return session.generic_read(
            str(args.get("read_fn_name", "")),
            int(args.get("d12", 1)),
        )
    if method == "get_version":
        return session.get_version()
    if method == "initialize":
        return session.initialize(int(args.get("d12", 1)))
    if method == "encoder_check":
        return session.encoder_check()
    if method == "keepalive":
        return session.keepalive()
    if method == "close_usb":
        return session.close_usb()
    if method == "buzzer":
        return session.buzzer(int(args.get("d12", 1)), int(args.get("t", 20)))
    if method == "read_card":
        return session.read_card(int(args.get("d12", 1)))
    if method == "guest_card":
        return session.guest_card(
            int(args["d12"]),
            int(args["dlsCoID"]),
            int(args["CardNo"]),
            int(args["dai"]),
            int(args["LLock"]),
            int(args["pdoors"]),
            str(args["BDate"]),
            str(args["EDate"]),
            str(args["LockNo"]),
        )
    if method == "limit_card":
        return session.limit_card(
            int(args["d12"]),
            int(args["dlsCoID"]),
            int(args["CardNo"]),
            int(args["dai"]),
            str(args["BDate"]),
            str(args["LCardNo"]),
        )
    if method == "card_erase":
        return session.card_erase(
            int(args["d12"]), int(args["dlsCoID"]), str(args["card_hex"])
        )
    if method == "checkout_card":
        return session.checkout_card(
            int(args["d12"]), int(args["dlsCoID"]),
            int(args["CardNo"]), int(args["dai"]),
            str(args["BDate"]),
        )
    if method == "record_card":
        return session.record_card(
            int(args["d12"]), int(args["dlsCoID"]),
            int(args["CardNo"]), int(args["dai"]),
            str(args["BDate"]),
        )
    if method == "room_set_card":
        return session.room_set_card(
            int(args["d12"]), int(args["dlsCoID"]),
            int(args["CardNo"]), int(args["dai"]),
            str(args["LockNo"]), str(args["BDate"]),
        )
    if method == "time_set_card":
        return session.time_set_card(
            int(args["d12"]), int(args["dlsCoID"]),
            int(args["CardNo"]), int(args["dai"]),
            str(args["BDate"]),
        )
    if method == "parse_card_type":
        return session.parse_card_type(str(args["card_hex"]))
    if method == "read_open_record":
        return session.read_open_record(str(args["card_hex"]))
    if method == "get_guest_lock_no":
        return session.get_guest_lock_no(int(args["dlsCoID"]), str(args["card_hex"]))
    if method == "get_guest_etime":
        return session.get_guest_etime(int(args["dlsCoID"]), str(args["card_hex"]))
    if method == "write_card":
        return session.write_card(
            int(args.get("d12", 1)),
            str(args["card_hex"]),
            str(args.get("variant", "v2")),
        )
    if method == "guest_card_v2":
        return session.guest_card_v2(
            int(args["d12"]), int(args["dlsCoID"]),
            int(args["CardNo"]), int(args["dai"]),
            int(args["LLock"]), int(args["pdoors"]),
            str(args["BDate"]), str(args["EDate"]),
            str(args["LockNo"]),
            str(args.get("lockno_mode", "ascii")),
        )
    if method == "list_bound_functions":
        return session.list_bound_functions()
    if method == "dll_call":
        return session.dll_call(str(args.get("fn_name", "")), list(args.get("params", [])))
    if method == "dll_list_exports":
        return session.dll_list_exports()
    if method == "call_card_fn":
        return session.call_card_fn(
            str(args["fn_name"]),
            list(args.get("signature") or []),
        )
    if method == "compose_guest_card":
        return session.compose_guest_card(
            int(args.get("d12", 1)),
            int(args["dlsCoID"]),
            int(args.get("CardNo", 1)),
            int(args.get("dai", 0)),
            int(args.get("LLock", 1)),
            int(args.get("pdoors", 0)),
            str(args["BDate"]),
            str(args["EDate"]),
            str(args["LockNoHex"]),
        )
    if method == "direct_read_usb":
        return session.direct_read_usb(int(args.get("d12", 1)))
    if method == "direct_write_usb":
        return session.direct_write_usb(
            int(args.get("d12", 1)),
            str(args["card_hex"]),
        )
    if method == "read_record":
        return session.read_record(int(args.get("d12", 1)))
    if method == "direct_compose_guest_card":
        return session.direct_compose_guest_card(
            int(args.get("d12", 1)),
            int(args["dlsCoID"]),
            int(args.get("CardNo", 1)),
            int(args.get("dai", 0)),
            int(args.get("LLock", 1)),
            int(args.get("pdoors", 0)),
            str(args["BDate"]),
            str(args["EDate"]),
            str(args["LockNoHex"]),
        )
    if method == "exit":
        # 关键：sys.exit 前一定要把 DLL的 USB句柄释放，否则下次开机时
        # V9 USB状态机会卡在"已 open 未 close"的中间态，Windows 直接
        # 把发卡器识别成 VID_0000 / "未知 USB设备（设定地址失败）"。
        try:
            session.close_usb()
        except Exception as e:
            _log(f"exit-time close_usb failed: {e}")
        sys.exit(0)
    raise ValueError(f"unknown method: {method}")


def _safe_close_usb(session: "DllSession", reason: str) -> None:
    """无论走 dispatch("exit") / stdin EOF / 异常退出，都尽量调用 CloseUSB。

    这是 V9 USB 状态机的最后保险：DLL 在内部维护"USB 是否 open"的标志，
    没 close 就退出 → 下次重启 Solid 会被认为"USB 已被占用"，进而把
    设备打成 VID_0000 死设备。
    """
    try:
        session.close_usb()
        _log(f"final close_usb ok (reason={reason})")
    except Exception as e:
        _log(f"final close_usb failed (reason={reason}): {e}")


def main() -> None:
    session = DllSession()
    # 报上来 stdin 的实际编码，便于排查跨系统编码问题
    in_enc = getattr(sys.stdin, "encoding", "?")
    out_enc = getattr(sys.stdout, "encoding", "?")
    _log(
        f"started, python={sys.version}, pid={os.getpid()}, 32bit={sys.maxsize <= 2**32}, "
        f"stdin={in_enc}, stdout={out_enc}"
    )

    exit_reason = "stdin_eof"
    try:
        # 主循环：一行一个 JSON 请求
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except Exception as e:
                # 把原始行的前 120 个字符 + 各字符的 unicode 码点都丢到 stderr，
                # 万一 reconfigure 没生效，可以从这里看出是不是又被 GBK 解码搞坏了。
                try:
                    codes = " ".join(f"{ord(c):04x}" for c in line[:60])
                    _log(f"bad json from stdin: {e!r}; head={line[:120]!r}; codes={codes}")
                except Exception:
                    pass
                sys.stdout.write(json.dumps({"id": None, "ok": False, "error": f"bad json: {e}"}) + "\n")
                sys.stdout.flush()
                continue

            req_id = req.get("id")
            method = req.get("method", "")
            args = req.get("args", {}) or {}

            try:
                result = dispatch(session, method, args)
                resp = {"id": req_id, "ok": True, **result}
            except SystemExit:
                # dispatch("exit") 已经在内部 close_usb 过了；这里只标个状态
                exit_reason = "exit_method"
                raise
            except Exception as e:
                resp = {
                    "id": req_id,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "trace": traceback.format_exc(limit=4),
                }

            try:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except Exception as e:
                _log(f"write resp failed: {e}")
                exit_reason = "stdout_broken"
                break
    except KeyboardInterrupt:
        exit_reason = "ctrl_c"
    finally:
        # 兜底再 close 一次（idempotent）。dispatch("exit") 已经 close 过，
        # 但 stdin EOF / Ctrl-C / 64 位主进程 terminate stdin 后等场景，
        # 我们仍然有最后一次机会让 DLL 释放 USB。
        _safe_close_usb(session, exit_reason)


if __name__ == "__main__":
    main()
