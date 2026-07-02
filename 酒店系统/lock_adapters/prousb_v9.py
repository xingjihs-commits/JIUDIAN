"""
prousb_v9.py — proUSB V9 系列发卡器适配器

适配文件
========
- DLL: `V9RFL.dll`（主接口）
- DLL: `d12.dll`, `d12c.dll`（USB 通讯依赖）
- DLL: `Mwic_32.dll`（Mifare 读写底层）
- 配置: `System.ini`（含 dlsCoID / HotelID / PCID / SN / LD）
- 数据: `CardLock.mdb`（房间和卡历史）

通用安装目录特征：含上述 `V9RFL.dll` 和 `System.ini` 即视为命中。

——————————————————————————————————————————————————————
日期格式约定 (BDate / EDate, 10 字节字符串)
——————————————————————————————————————————————————————
proUSB 用 "YYMMDDHHMM" 格式：
- "2605221200" = 2026-05-22 12:00
- "9912312359" = 1999-12-31 23:59
本模块提供 `format_date()` 工具函数处理 datetime → 这个格式。

——————————————————————————————————————————————————————
锁号格式约定 (LockNo, 8 字节字符串)
——————————————————————————————————————————————————————
8 位字符（hex 或数字）：通常前 2 位楼栋号，中间 2-4 位楼层，末尾房号。
本模块不做编码假设，调用方决定格式。
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .base import CardResult, LockAdapter
from .bridge_client import RflBridge, get_bridge
from .card_corpus import log_issue_result, snapshot_mdb

logger = logging.getLogger(__name__)

_SIGS_JSON = Path(__file__).resolve().parent.parent / "tools" / "dev" / "_legacy_intel" / "special_card_sigs.json"
_WINNERS_CACHE: Optional[dict[str, Any]] = None

# 不阻塞前台的卡型（仍可调，但 UI/前台应标为 延迟）
DEFERRED_CARD_FNS = frozenset({"IniCard", "SpecialCard"})

# proUSB CardType 字符代码（来自官方接口文档）
PROUSB_CARD_TYPE_MAP = {
    "0": "控制卡",
    "1": "记录卡",
    "2": "房号设置卡",
    "3": "时钟设置卡",
    "4": "挂失卡",
    "5": "区域号设置卡",
    "6": "客人卡",
    "7": "校验卡",
    "8": "区域卡",
    "9": "未知",
    "A": "应急卡",
    "B": "调查卡",
    "C": "楼栋卡",
    "D": "楼层卡",
    "E": "未知",
    "F": "空白卡",
}

PAYLOAD_CARD_TYPE_MAP = {
    "0": "授权卡",
    "1": "记录卡",
    "2": "房号设置卡",
    "3": "时钟设置卡",
    "4": "挂失卡",
    "5": "区域号设置卡",
    "6": "客人卡",
    "7": "退房卡",
    "8": "区域卡",
    "A": "应急卡",
    "B": "总卡",
    "C": "楼栋卡",
    "D": "楼层卡",
    "F": "空白卡",
}


def format_date(when: _dt.datetime) -> str:
    """datetime → "YYMMDDHHMM" 10 字节字符串。"""
    return when.strftime("%y%m%d%H%M")


def parse_card_type_code(code: str) -> str:
    """把 GetCardTypeByCardDataStr 返回的单字符代码翻成人话。"""
    if not code:
        return "未知"
    c = code[0].upper()
    return PROUSB_CARD_TYPE_MAP.get(c, f"未知({c})")


def parse_payload_card_type(card_hex: str) -> str:
    """优先按酒店真实卡数据判定卡型，避免 DLL 类型接口把客人卡误报成楼栋卡。"""
    pl = (card_hex or "").strip().upper()
    if len(pl) < 20 or not pl.startswith("C92B20B7"):
        return ""
    return PAYLOAD_CARD_TYPE_MAP.get(pl[18], f"未知({pl[18]})")


class ProUsbV9Adapter(LockAdapter):
    """proUSB V9 实现。"""

    brand = "proUSB"
    version_hint = "V9-20171130"

    # 用于 detect 的关键文件特征
    DLL_NAME = "V9RFL.dll"
    REQUIRED_FILES = ("V9RFL.dll", "d12.dll")
    OPTIONAL_FILES = ("d12c.dll", "Mwic_32.dll", "System.ini", "CardLock.mdb")

    def __init__(self, install_dir: Path):
        super().__init__(install_dir)
        self._dll_path = self.install_dir / self.DLL_NAME
        self._bridge: Optional[RflBridge] = None
        self._dlsCoID: int = 0
        self._hotel_id: str = ""
        self._pc_id: str = ""
        self._version_cache: str = ""
        self._system_factory: Optional[Any] = None

    # ──────────────────────────────────────────────────────────────
    # 识别
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def detect(cls, install_dir: Path) -> Optional["ProUsbV9Adapter"]:
        install_dir = Path(install_dir)
        if not install_dir.is_dir():
            return None
        for name in cls.REQUIRED_FILES:
            if not (install_dir / name).is_file():
                return None
        return cls(install_dir)

    # ──────────────────────────────────────────────────────────────
    # 配置
    # ──────────────────────────────────────────────────────────────

    def configure(
        self,
        *,
        dlsCoID: Optional[int] = None,
        hotel_id: Optional[str] = None,
        pc_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        if dlsCoID is not None:
            self._dlsCoID = int(dlsCoID)
        if hotel_id is not None:
            self._hotel_id = str(hotel_id)
        if pc_id is not None:
            self._pc_id = str(pc_id)

    @property
    def dlsCoID(self) -> int:
        return self._dlsCoID

    @property
    def hotel_id(self) -> str:
        return self._hotel_id

    @property
    def dll_path(self) -> Path:
        return self._dll_path

    # ──────────────────────────────────────────────────────────────
    # 生命周期
    # ──────────────────────────────────────────────────────────────

    def _ensure_bridge(self) -> RflBridge:
        if self._bridge is None:
            self._bridge = get_bridge()
        if not self._bridge.is_running():
            self._bridge.start()
        return self._bridge

    def _ensure_dll_loaded(self) -> bool:
        bridge = self._ensure_bridge()
        if bridge.dll_loaded:
            return True
        if not self._dll_path.is_file():
            raise FileNotFoundError(f"V9RFL.dll not found at {self._dll_path}")
        resp = bridge.load_dll(
            str(self._dll_path),
            extra_paths=[str(self.install_dir)],
        )
        return bool(resp.get("ok") and resp.get("loaded"))

    def initialize(self, d12: Optional[int] = None) -> bool:
        """打开 USB 连接到发卡器。

        参数
        ----
        d12 :
            None -> 自动模式：先试 d12=0（firmware 握手），失败后自动
                    降级到 d12=1（轻量 USB 句柄）。
            0    -> 强制 firmware 握手（部分老发卡器写卡需要）。
            1    -> 轻量 USB 句柄（本机实测全部发卡操作均正常）。

        注意
        ----
        实测证明 d12=1 模式完全支持擦卡(CardErase)、写客人卡(GuestCard)
        等全部发卡操作。d12=0 失败时自动降级不会影响功能，只是在日志中
        记明信任状态以便排查。
        """
        if d12 is not None:
            # 显式指定模式
            d12_set = (d12,)
        else:
            # 自动模式：先试 d12=0，失败降级到 d12=1
            d12_set = (0, 1)

        last_error = ""
        for mode in d12_set:
            try:
                if not self._ensure_dll_loaded():
                    continue
                bridge = self._ensure_bridge()
                resp = bridge.initialize(d12=mode)
                ok = bool(resp.get("ok"))
                ret = int(resp.get("ret", -1))
                if ok and ret == 0:
                    self._opened = True
                    if mode == 0:
                        logger.info("initialize(d12=0) 成功 — firmware 信任握手通过")
                    else:
                        logger.info("initialize(d12=1) 成功 — 轻量 USB 模式")
                    return True
                last_error = f"ret={ret}"
                if mode == 0:
                    details = self._diagnose_init_failure()
                    logger.warning(
                        "initializeUSB(0) 失败 (ret=%d) — %s；"
                        "将尝试 d12=1 轻量模式", ret, details,
                    )
                else:
                    logger.error("initializeUSB(1) 也失败 (ret=%d)", ret)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.error("initialize(d12=%d) 异常: %s", mode, last_error)

        self._opened = False
        logger.error("initialize 全部模式均失败。最后错误: %s", last_error)
        return False

    def _diagnose_init_failure(self) -> str:
        """分析 initializeUSB(0) 失败的可能原因。"""
        parts = []
        install_dir = str(self.install_dir)
        d12c = Path(install_dir) / "d12c.dll"
        d12_dll = Path(install_dir) / "d12.dll"
        if not d12c.is_file():
            parts.append("d12c.dll 文件缺失（firmware 握手依赖此文件）")
        if not d12_dll.is_file():
            parts.append("d12.dll 文件缺失")
        if not parts:
            parts.append("d12c.dll 和 d12.dll 均存在，可能是本机缺 d12 驱动或发卡器未上电")
        return "；".join(parts)

    def initialize_trusted(self) -> tuple[bool, str]:
        """显式检查 firmware 握手状态（d12=0）。

        区别于 initialize() 的自动降级，本方法只试 d12=0，
        不降级到 d12=1。用于诊断而非正常业务流程。

        返回 (ok: bool, error_message: str)。
        - ok=True 时 error_message 为空
        - ok=False 时 error_message 包含具体失败原因
        - 注意：ok=False 不代表发卡器不能用，d12=1 已能完成全部发卡操作
        """
        ok = self.initialize(d12=0)
        if ok:
            return True, ""
        diag = self._diagnose_init_failure()
        msg = (
            f"firmware 握手 (d12=0) 失败：{diag}。\n\n"
            "注意：d12=1 轻量模式已能正常发卡，不影响使用。\n\n"
            "如需修复 firmware 信任（少数老门锁写卡时仍需此握手），\n"
            "可以尝试：\n"
            "  1) 安装 d12 芯片驱动（设备管理器中 VID_320F 设备）\n"
            "  2) 物理拔插 USB 60 秒后重试\n"
            "  3) 用原版 CardLock.exe 刷一张授权卡后切换回本系统"
        )
        return False, msg

    def close(self) -> None:
        if self._bridge is not None and self._bridge.is_running() and self._opened:
            try:
                self._bridge.close_usb()
            except Exception:
                pass
        self._opened = False

    def restart_reader(self, *, full: bool = True, settle_sec: float = 3.0) -> Dict[str, Any]:
        """软重启发卡器：释放 DLL/USB 句柄 -> 等 settle_sec -> 重新打开。

        当发卡器出现"一直蜂鸣 / 注销显示成功但卡没变 / Read 卡返回全 BBBB"等
        中间态卡死时，调用这个方法可以不拔 USB 也把 V9 DLL 内部状态拍回初始。

        参数
        ----
        full : bool, 默认 True
            True  -> 完整重启：close_usb() + 杀掉 32 位桥子进程 + 重新 spawn
                     桥 + initializeUSB(d12=0)。等同物理拔插（但保留驱动）。
            False -> 只调 close_usb + initializeUSB(d12=0)（轻量级，不杀桥）。
                     当 DLL 状态没问题、只是 USB 握手卡住时够用。

        settle_sec : float, 默认 3.0
            释放 USB 后等待几秒让 Windows 重新枚举。<1 容易抢回脏状态。
            建议 3-5 秒。2.0 秒在现场曾不够（2026-05-31 复盘结论）。

        返回
        ----
        dict with keys:
            ok        : bool   -- 重启后能否成功 initialize(d12=0)
            stages    : list[(name, ok, detail)]  -- 每一步的结果
            elapsed   : float  -- 总耗时（秒）
            error     : str    -- 若 ok=False 才有
            trust_ok  : bool   -- firmware 信任握手 (d12=0) 是否成功
        """
        t0 = time.monotonic()
        stages: list[tuple[str, bool, str]] = []

        try:
            self.close()
            stages.append(("close_usb", True, "已释放 USB 句柄"))
        except Exception as e:
            stages.append(("close_usb", False, f"{type(e).__name__}: {e}"))

        if full:
            try:
                if self._bridge is not None and self._bridge.is_running():
                    self._bridge.stop()
                    stages.append(("stop_bridge", True, "已结束 32 位桥子进程"))
                else:
                    stages.append(("stop_bridge", True, "桥子进程未运行"))
            except Exception as e:
                stages.append(("stop_bridge", False, f"{type(e).__name__}: {e}"))
            self._bridge = None
            self._dll_loaded = False

        try:
            time.sleep(max(0.0, float(settle_sec)))
            stages.append(("settle", True, f"已等待 {settle_sec:.1f}s"))
        except Exception:
            pass

        try:
            # 自动模式：先试 d12=0 完整握手，失败降级到 d12=1
            ok = self.initialize()
            if ok:
                stages.append((
                    "re_initialize", True,
                    "initialize 成功（d12=0→1 自动模式）",
                ))
            else:
                stages.append((
                    "re_initialize", False,
                    "初始化失败，发卡器未连接",
                ))
            return {
                "ok": ok,
                "stages": stages,
                "elapsed": round(time.monotonic() - t0, 2),
                "trust_ok": ok and self._opened,
                "error": "" if ok else "重启后初始化失败。请检查 USB 连接和驱动。",
            }
        except Exception as e:
            stages.append(("re_initialize", False, f"{type(e).__name__}: {e}"))
            return {
                "ok": False,
                "stages": stages,
                "elapsed": round(time.monotonic() - t0, 2),
                "trust_ok": False,
                "error": f"重启异常: {type(e).__name__}: {e}",
            }

    # ──────────────────────────────────────────────────────────────
    # 信息查询
    # ──────────────────────────────────────────────────────────────

    def get_version(self) -> str:
        """读 DLL 版本。不需要发卡器在线，是最低风险的连通性测试。"""
        if self._version_cache:
            return self._version_cache
        try:
            if not self._ensure_dll_loaded():
                # 别返回空串掩盖错误，直接告诉调用方 DLL 没加载上。
                return "(error: DLL 未加载，请先点 ① 加载 DLL 并查看其报错)"
            resp = self._ensure_bridge().get_version()
        except Exception as e:
            return f"(error: {e})"
        if not resp.get("ok"):
            return f"(error: {resp.get('error', 'unknown')})"
        ver = (resp.get("out") or {}).get("version", "")
        self._version_cache = ver
        return ver

    @staticmethod
    def expected_type_nibble(fn_name: str) -> Optional[int]:
        """payload[9] 高半字节期望（来自 13 张现场样本）。"""
        m = {
            "IniCard": 0x0,
            "RecordCard": 0x1,
            "RoomSetCard": 0x2,
            "TimeSetCard": 0x3,
            "LimitCard": 0x4,
            "GuestCard": 0x6,
            "CheckOutCard": 0x7,
            "GroupCard": 0x8,
            "GroupSetCard": 0x5,
            "EmergencyCard": 0xA,
            "MasterCard": 0xB,
            "BuildingCard": 0xC,
            "FloorCard": 0xD,
            "BlankCard": 0xF,
        }
        return m.get(fn_name)

    @staticmethod
    def validate_payload(payload: str, fn_name: str) -> tuple[bool, str]:
        pl = (payload or "").upper()
        if not pl.startswith("C92B20B7"):
            return False, "payload 不以 C92B20B7 开头"
        if len(pl) < 20:
            return False, "payload 长度不足"
        nib = ProUsbV9Adapter.expected_type_nibble(fn_name)
        if nib is None:
            return True, "ok"
        # 16-byte payload = 32 hex chars; byte index 9 → hex chars [18:20]
        byte9 = int(pl[18:20], 16)
        got = byte9 >> 4
        if got != nib:
            return False, f"byte9 高半字节期望 {nib:X} 实际 {got:X} (byte={byte9:02X})"
        return True, "ok"

    @classmethod
    def _load_winners(cls) -> dict[str, Any]:
        global _WINNERS_CACHE
        if _WINNERS_CACHE is not None:
            return _WINNERS_CACHE
        if not _SIGS_JSON.is_file():
            _WINNERS_CACHE = {}
            return _WINNERS_CACHE
        try:
            data = json.loads(_SIGS_JSON.read_text(encoding="utf-8"))
            _WINNERS_CACHE = data.get("winners") or {}
        except Exception:
            _WINNERS_CACHE = {}
        return _WINNERS_CACHE

    @classmethod
    def winner_signature_template(cls, fn_name: str) -> Optional[list[dict[str, Any]]]:
        w = cls._load_winners().get(fn_name) or {}
        if w.get("verdict") != "match":
            return None
        sig = w.get("signature")
        if not isinstance(sig, list) or not sig:
            return None
        out: list[dict[str, Any]] = []
        u8_slot = 0
        cstr_11 = 0
        for spec in sig:
            s = dict(spec)
            kind = s.get("kind")
            if kind == "i32":
                s["value"] = "__DLSCOID__"
            elif kind == "u8":
                if not out:
                    s["value"] = 1
                elif fn_name == "GroupSetCard":
                    if u8_slot == 0:
                        s["value"] = "__GROUP_NO__"
                    else:
                        s["value"] = "__CARD_NO__"
                    u8_slot += 1
                elif fn_name == "BuildingCard" and u8_slot == 0:
                    s["value"] = "__BUILDING_NO__"
                    u8_slot += 1
                elif fn_name == "FloorCard" and u8_slot == 0:
                    s["value"] = "__BUILDING_NO__"
                    u8_slot += 1
                elif fn_name == "FloorCard" and u8_slot == 1:
                    s["value"] = "__FLOOR_NO__"
                    u8_slot += 1
                elif fn_name == "GroupCard" and u8_slot == 0:
                    s["value"] = "__GROUP_NO__"
                    u8_slot += 1
                elif u8_slot == 0:
                    s["value"] = "__CARD_NO__"
                    u8_slot += 1
                elif u8_slot == 1:
                    s["value"] = "__DAI__"
                    u8_slot += 1
                else:
                    s["value"] = "__LLOCK__" if int(s.get("value", 0)) else 0
            elif kind == "cstr":
                if int(s.get("size", 11)) <= 9:
                    s["value"] = "__LOCK_NO__"
                elif cstr_11 == 0:
                    s["value"] = "__B_DATE__"
                    cstr_11 += 1
                else:
                    s["value"] = "__E_DATE__"
            out.append(s)
        return out

    def _corpus_after_issue(
        self,
        fn_name: str,
        before: str,
        result: CardResult,
        *,
        tag: str = "",
    ) -> None:
        snap = None
        if result.success:
            snap = snapshot_mdb(self.install_dir, tag or fn_name)
        log_issue_result(
            fn_name=fn_name,
            success=result.success,
            card_hex=result.card_hex or "",
            before_payload=before,
            after_payload=result.card_hex or "",
            raw_ret=int(result.raw_ret or 0),
            error=result.error or "",
            mdb_snapshot=snap,
        )

    def get_supported_card_types(self) -> list[str]:
        return [
            "guest", "auth", "master", "floor", "building",
            "clock", "room_no", "loss_report", "emergency", "record",
            "blank",
        ]

    # ──────────────────────────────────────────────────────────────
    # 硬件交互
    # ──────────────────────────────────────────────────────────────

    def buzzer(self, ms: int = 200) -> bool:
        try:
            if not self.is_open and not self.initialize(d12=1):
                return False
            t_units = max(1, int(ms / 10))  # 每单位 10ms
            resp = self._ensure_bridge().buzzer(d12=1, t=t_units)
        except Exception:
            return False
        return bool(resp.get("ok") and int(resp.get("ret", -1)) == 0)

    def _success_buzzer(self, fn_name: str = "") -> None:
        """Keep Solid's encoder feedback as clear as the legacy CardLock UI."""
        try:
            from database import db
            if str(db.get_config("encoder_buzzer_enabled") or "1").strip() == "0":
                return
            ms = int(db.get_config("encoder_buzzer_ms") or "200")
        except Exception:
            ms = 200
        try:
            if fn_name in {"IniCard", "RoomSetCard", "TimeSetCard", "GroupSetCard"}:
                self.buzzer(150)
                time.sleep(0.05)
                self.buzzer(150)
            else:
                self.buzzer(ms)
        except Exception:
            pass

    def keepalive(self) -> bool:
        """读卡器保活——每 30 秒调一次 initializeUSB(d12=1) 防止固件超时断开。"""
        try:
            if self.is_open:
                resp = self._ensure_bridge().keepalive()
                return bool(resp.get("ok") and int(resp.get("ret", -1)) == 0)
        except Exception:
            pass
        return False

    def encoder_ok(self) -> bool:
        """发卡前检查编码器连接状态。返回 True 才可发卡。"""
        try:
            if not self.is_open:
                self.initialize(d12=1)
            resp = self._ensure_bridge().encoder_check()
            return bool(resp.get("ok") and int(resp.get("ret", -1)) == 0)
        except Exception:
            return False

    def read_card_raw(self) -> Optional[str]:
        """读发卡器上的卡。返回完整帧 hex；卡上无数据 / 无卡时返回 None。

        ReadCard 实测有 3 种帧头：
            552101 → 有真实卡数据
            551501 → 空白卡（payload 全 FF）
            550100 → 发卡器上没卡
        """
        try:
            if not self.is_open and not self.initialize(d12=1):
                return None
            resp = self._ensure_bridge().read_card(d12=1)
        except Exception:
            return None
        if not resp.get("ok"):
            return None
        ret = int(resp.get("ret", -1))
        out = resp.get("out") or {}
        hex_str = out.get("hex", "")
        if ret != 0 or not hex_str:
            return None
        if not out.get("has_card", False):
            return None
        return hex_str

    def read_card_payload(self) -> Optional[str]:
        """只返回 16 字节卡内 payload (32 字符 hex)。无卡时 None。"""
        try:
            if not self.is_open and not self.initialize(d12=1):
                return None
            resp = self._ensure_bridge().read_card(d12=1)
        except Exception:
            return None
        if not resp.get("ok") or int(resp.get("ret", -1)) != 0:
            return None
        out = resp.get("out") or {}
        if not out.get("has_card"):
            return None
        return out.get("payload") or None

    def read_record_card_open_logs(self) -> list[dict]:
        """读记录卡，调用 GetOpenRecordByDataStr 获取开门日志。
        基于逆向证据：dll_exports.txt 记录 RVA=0x000011B8，
        v9_calling_conventions 记录 ret=8 stack=8（2 参数 c_char_p, c_char_p）。
        返回 [{"card_id":..., "room_no":..., "open_time":...}, ...]。
        """
        try:
            if not self.is_open and not self.initialize(d12=1):
                return []
            raw = self.read_card_raw()
            if not raw:
                return []
            resp = self._ensure_bridge().read_open_record(raw)
            records_str = resp.get("out", {}).get("records", "")
            if not records_str or records_str.strip() == "":
                return []
            results = []
            for line in records_str.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 3:
                    results.append({
                        "card_id": parts[0],
                        "room_no": parts[1],
                        "open_time": parts[2],
                    })
            return results
        except Exception:
            return []

    def read_card_uid(self) -> Optional[str]:
        hex_str = self.read_card_raw()
        if not hex_str or len(hex_str) < 16:
            return None
        # 文档：第 25 字符起 8 字符是 UID（这里下标是 1-based 还是 0-based 厂家文档不清，
        # 经验上是字符 8..16 那段；我们两段都返回，让上层判断）。
        # 实际接管现场以读到的为准，先暂取 8..16。
        return hex_str[8:16]

    # ──────────────────────────────────────────────────────────────
    # 发各类卡
    # ──────────────────────────────────────────────────────────────

    def _check_open(self) -> Optional[CardResult]:
        """确保发卡器已打开。

        d12=0 失败时会自动降级到 d12=1（本机实测 d12=1 支持全部发卡操作），
        仅在日志中记录信任状态。
        """
        if not self.is_open:
            ok = self.initialize()
            if not ok:
                return CardResult.fail("无法连接发卡器。请检查 USB 连接和驱动。")
        if not self._dlsCoID:
            return CardResult.fail("缺少 dlsCoID 配置 —— 请先完成「接管门锁」向导。")
        return None

    def issue_guest_card(
        self,
        lock_no: str,
        b_date: str,
        e_date: str,
        card_no: int = 1,
        llock: bool = True,
        pdoors: bool = False,
        dai: int = 0,
        seq: int = -1,
    ) -> CardResult:
        """发一张客人卡。seq 仅在通用回退路径生效，DLL 路径内部自行管理 seq。

        lock_no:
            - 8 位 hex 字符（4 字节锁号），例如 "80010301"
            - 或者用 lock_no_from_room() 由 (building, floor, room_id) 算出
        """
        # 发卡前先检查编码器状态
        if not self.encoder_ok():
            return CardResult.fail("发卡器未就绪，请检查 USB 连接和驱动。")
        err = self._check_open()
        if err is not None:
            return err
        lock_no = self._normalize_lock_no(lock_no)
        b_date = self._normalize_date(b_date)
        e_date = self._normalize_date(e_date)
        before = self.read_card_payload() or ""
        try:
            resp = self._ensure_bridge().guest_card(
                d12=1, dlsCoID=self._dlsCoID,
                CardNo=int(card_no), dai=int(dai),
                LLock=1 if llock else 0,
                pdoors=1 if pdoors else 0,
                BDate=b_date, EDate=e_date,
                LockNo=lock_no,
            )
        except Exception as e:
            return CardResult.fail(f"调用 GuestCard 失败: {e}")
        ok = bool(resp.get("ok"))
        ret = int(resp.get("ret", -1))
        hex_str = (resp.get("out") or {}).get("hex", "")
        result = CardResult.ok(hex_str, raw_ret=ret) if ok and ret == 0 and hex_str else CardResult.fail(
            f"GuestCard 失败 (ret={ret}, ok={ok})", raw_ret=ret)
        self._corpus_after_issue("GuestCard", before, result, tag="guest")
        if result.success:
            self._success_buzzer("GuestCard")
        return result

    def issue_guest_card_direct(
        self,
        lock_no: str,
        b_date: str,
        e_date: str,
        card_no: int = 1,
        llock: bool = True,
        pdoors: bool = False,
        dai: int = 0,
        seq: int = -1,
    ) -> CardResult:
        """发客人卡 — 同 issue_guest_card，直接调 GuestCard(ASCII)。"""
        return self.issue_guest_card(lock_no, b_date, e_date, card_no, llock, pdoors, dai, seq=seq)

    @staticmethod
    def lock_no_from_room(building: int, floor: int, room_id: int) -> str:
        """从 (BldNo, FlrNo, RomID) 三元组算出 8 位 hex 锁号字符串。

        编码方式 (由 CardLock.mdb 的 1336 张历史客人卡反推):
            byte 0 = 0x80 固定
            byte 1 = RomID    (RoomInfo.RomID，房间在该楼栋·楼层下的内部编号 1-99)
            byte 2 = FlrNo    (RoomInfo.FlrNo，"楼层组"号 —— 注意这是数据库逻辑分组，
                               并不一定等于 RoomNo 首位数字)
            byte 3 = BldNo    (RoomInfo.BldNo，楼栋号 1-255)

        重要：proUSB 的"楼层号"是 RoomInfo.FlrNo，不是房号开头数字。
        我们见过整间酒店所有房间都 FlrNo=3 而 RoomNo 是 8XX 的情况。
        正确做法是从 RoomInfo 表查 FlrNo / RomID，再喂给本函数。
        """
        b = max(0, min(255, int(building)))
        f = max(0, min(255, int(floor)))
        r = max(0, min(255, int(room_id)))
        return f"80{r:02X}{f:02X}{b:02X}"

    @classmethod
    def lock_no_from_roominfo_row(cls, row: dict) -> str:
        """直接从 RoomInfo 表的一行字典生成 LockNo。

        row 必须含字段 BldNo / FlrNo / RomID（字符串或数字都行）。
        """
        def _i(key: str) -> int:
            v = row.get(key, 0) if row else 0
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0
        return cls.lock_no_from_room(_i("BldNo"), _i("FlrNo"), _i("RomID"))

    @classmethod
    def lock_no_from_solid_room(cls, room: dict) -> str:
        """对接 Solid 内部 room dict（字段可能用 building / floor / room_id 命名）。"""
        def _i(*keys: str) -> int:
            for k in keys:
                v = room.get(k) if room else None
                if v is not None:
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        pass
            return 0
        return cls.lock_no_from_room(
            _i("BldNo", "building", "bld_no", "bld"),
            _i("FlrNo", "floor", "flr_no", "flr"),
            _i("RomID", "room_id", "rom_id", "rid"),
        )

    @staticmethod
    def _roomset_lock_code(lock_no: str) -> str:
        """房号设置卡 payload 里仍使用 4 字节 LockNo，例如 80050301。"""
        return ProUsbV9Adapter._normalize_lock_no(lock_no)

    def issue_loss_report_card(
        self,
        l_card_no: str,
        b_date: str,
        card_no: int = 1,
        dai: int = 0,
    ) -> CardResult:
        err = self._check_open()
        if err is not None:
            return err
        b_date = self._normalize_date(b_date)
        l_card_no = (l_card_no or "").ljust(4, "0")[:4]
        try:
            resp = self._ensure_bridge().limit_card(
                d12=1, dlsCoID=self._dlsCoID,
                CardNo=int(card_no), dai=int(dai),
                BDate=b_date, LCardNo=l_card_no,
            )
        except Exception as e:
            return CardResult.fail(f"调用 LimitCard 失败: {e}")
        result = self._parse_card_resp(resp)
        if result.success:
            self._success_buzzer("LimitCard")
        return result

    def _issue_special_by_signature(
        self,
        fn_name: str,
        signature_template: list[dict[str, Any]],
        *,
        b_date: str = "",
        e_date: str = "",
        lock_no: str = "80010301",
        card_no: int = 1,
        dai: int = 0,
        llock: bool = False,
        group_no: int = 1,
        building_no: int = 1,
        floor_no: int = 3,
    ) -> CardResult:
        """
        按盲探得到的 signature 模板调用 12 个专用 DLL 函数。

        说明：
        - signature_template 里允许占位符：
            "__B_DATE__", "__E_DATE__", "__LOCK_NO__", "__HOTEL_ID__",
            "__CARD_NO__", "__DAI__", "__GROUP_NO__", "__LLOCK__",
            "__BUILDING_NO__", "__FLOOR_NO__"
        - 回包 ret 并不总是 0（例如 Record/Room/Time 实测会写卡成功但 ret=-1）；
          统一以读卡 payload 是否变化 + 是否保持酒店前缀 C92B20B7 来判成功。
        """
        err = self._check_open()
        if err is not None:
            return err

        b_norm = self._normalize_date(b_date or format_date(_dt.datetime.now()))
        e_norm = self._normalize_date(
            e_date or format_date(_dt.datetime.now() + _dt.timedelta(days=365))
        )
        lock_norm = self._normalize_lock_no(lock_no)
        roomset_lock_norm = self._roomset_lock_code(lock_no)
        hotel_id = (self._hotel_id or "").strip().upper()

        before = self.read_card_payload() or ""
        try:
            signature: list[dict[str, Any]] = []
            for spec in signature_template:
                s = dict(spec)
                kind = s.get("kind")
                if kind == "u8":
                    v = s.get("value", 0)
                    if isinstance(v, str):
                        if v == "__CARD_NO__":
                            v = int(card_no)
                        elif v == "__DAI__":
                            v = int(dai)
                        elif v == "__GROUP_NO__":
                            v = int(group_no)
                        elif v == "__BUILDING_NO__":
                            v = int(building_no)
                        elif v == "__FLOOR_NO__":
                            v = int(floor_no)
                        elif v == "__LLOCK__":
                            v = 1 if llock else 0
                    s["value"] = int(v) & 0xFF
                elif kind == "i32":
                    v = s.get("value", 0)
                    if isinstance(v, str) and v == "__DLSCOID__":
                        v = int(self._dlsCoID)
                    s["value"] = int(v)
                elif kind == "cstr":
                    v = str(s.get("value", ""))
                    if v == "__B_DATE__":
                        v = b_norm
                    elif v == "__E_DATE__":
                        v = e_norm
                    elif v == "__LOCK_NO__":
                        v = roomset_lock_norm if fn_name == "RoomSetCard" else lock_norm
                    elif v == "__HOTEL_ID__":
                        v = hotel_id
                    s["value"] = v
                signature.append(s)

            resp = self._ensure_bridge().call_card_fn(
                fn_name=fn_name,
                signature=signature,
                # 现场故障复盘 (2026-05-30 20:14)：8.0s 在某些 V9 卡上不够，
                # IniCard/SpecialCard 真要 10-12s。8s 强切会让 bridge_client
                # 在 DLL 还没回时就 raise，进而触发 bridge 自动重启 + USB
                # 半握手脏状态（最终 d12c.dll 被踩坏）。提到 15s 给 DLL
                # 留足时间，同时不至于把 GUI 卡到用户骂街。
                timeout=15.0,
            )
        except Exception as e:
            return CardResult.fail(f"调用 {fn_name} 失败: {e}")

        if not resp.get("ok"):
            return CardResult.fail(resp.get("error", f"{fn_name} RPC 失败"))

        after = self.read_card_payload() or ""
        if after and after != before and after.upper().startswith("C92B20B7"):
            ok, _ = self.validate_payload(after, fn_name)
            result = CardResult.ok(after, raw_ret=int(resp.get("ret", 0)))
            if not ok:
                result.error = f"{fn_name} 已写卡但类型字节未匹配期望"
            self._corpus_after_issue(fn_name, before, result)
            self._success_buzzer(fn_name)
            return result
        if fn_name in DEFERRED_CARD_FNS:
            result = CardResult.fail(
                f"{fn_name} 未写出有效酒店卡（延迟：IniCard 需空白卡前置；SpecialCard 为占位串）",
                raw_ret=int(resp.get("ret", -1)),
            )
            self._corpus_after_issue(fn_name, before, result)
            return result
        if after and after.upper().startswith("C92B20B7") and fn_name in (
            "MasterCard", "BuildingCard", "FloorCard", "EmergencyCard", "GroupCard", "GroupSetCard",
        ):
            result = CardResult.fail(
                f"{fn_name} 已调用（ret={resp.get('ret')})，但 payload 未变化，参数仍待确认",
                raw_ret=int(resp.get("ret", -1)),
            )
            self._corpus_after_issue(fn_name, before, result)
            return result
        result = CardResult.fail(f"{fn_name} 调用后卡数据未变化或异常", raw_ret=int(resp.get("ret", -1)))
        self._corpus_after_issue(fn_name, before, result)
        return result

    def _debug_log(self, hypothesis_id: str, message: str, data: dict[str, Any]) -> None:
        return

    def _rewrite_payload(self, fn_name: str, before: str, original: str, patched: str, hypothesis_id: str) -> CardResult:
        try:
            resp = self._ensure_bridge().write_card(d12=1, card_hex=patched, variant="binary", timeout=6.0)
        except Exception as exc:
            self._debug_log(hypothesis_id, "系统卡补写异常", {"fn": fn_name, "error": str(exc)})
            return CardResult.fail(f"{fn_name} 补写失败：{exc}")
        after = self.read_card_payload() or ""
        write_ret = int(resp.get("ret", -1))
        patched_u = patched.upper()
        after_u = after.upper()
        exact_ok = after_u == patched_u
        type_ok = False
        if after_u.startswith("C92B20B7"):
            type_ok, _ = self.validate_payload(after_u, fn_name)
        # 部分发卡器写卡成功但即时读回窗口失败。
        # DLL 返回 0 且本地 payload 有效即视为成功，原厂软件在现场测试中保持白盒校验能力。
        ok = exact_ok or type_ok or (write_ret == 0 and self.validate_payload(patched_u, fn_name)[0])
        self._debug_log(hypothesis_id, "系统卡补写结果", {
            "fn": fn_name,
            "original": original,
            "patched": patched,
            "verify": after,
            "write_ret": write_ret,
            "exact_ok": exact_ok,
            "type_ok": type_ok,
            "ok": ok,
        })
        result_hex = after_u if (exact_ok or type_ok) else patched_u
        result = CardResult.ok(result_hex, raw_ret=write_ret) if ok else CardResult.fail(
            f"{fn_name} 补写后校验失败",
            raw_ret=write_ret,
        )
        self._corpus_after_issue(fn_name, before, result)
        if result.success:
            self._success_buzzer(fn_name)
        return result

    def _reference_time_marker(self, b_date: str, card_no: int, dai: int) -> Optional[str]:
        tpl = self.winner_signature_template("CheckOutCard") or [
            {"kind": "u8", "value": 1},
            {"kind": "i32", "value": "__DLSCOID__"},
            {"kind": "u8", "value": "__CARD_NO__"},
            {"kind": "u8", "value": "__DAI__"},
            {"kind": "cstr", "value": "__B_DATE__", "size": 11},
            {"kind": "outbuf", "size": 256},
        ]
        ref = self._issue_special_by_signature("CheckOutCard", tpl, b_date=b_date, card_no=card_no, dai=dai)
        pl = (ref.card_hex or "").upper()
        marker = pl[20:26] if len(pl) >= 26 and pl.startswith("C92B20B7") else ""
        self._debug_log("H11", "生成系统卡时间参照", {
            "success": ref.success,
            "payload": pl,
            "marker": marker,
            "error": ref.error,
        })
        return marker if len(marker) == 6 else None

    @staticmethod
    def _legacy_date_marker(date_value: str) -> str:
        """CardLock.exe 的 YYMMDDHHMM -> 3-byte 时间编码。"""
        s = ProUsbV9Adapter._normalize_date(date_value)
        yy, mm, dd, hh, minute = (
            int(s[0:2]), int(s[2:4]), int(s[4:6]), int(s[6:8]), int(s[8:10])
        )
        return f"{(((yy - 9) & 0x0F) << 4) + mm:02X}{(dd << 3) + (hh // 4):02X}{((hh & 0x03) << 6) + minute:02X}"

    @staticmethod
    def _legacy_begin_time_marker(date_value: str) -> str:
        s = ProUsbV9Adapter._normalize_date(date_value)
        hh, minute = int(s[6:8]), int(s[8:10])
        return f"{(hh << 3) + (minute // 10):02X}"

    @staticmethod
    def _legacy_full_day_range(b_date: str = "", e_date: str = "") -> tuple[str, str]:
        """系统卡应全天可刷：00:00 → 23:59。"""
        now = _dt.datetime.now()
        b_src = ProUsbV9Adapter._normalize_date(b_date or format_date(now))
        e_src = ProUsbV9Adapter._normalize_date(
            e_date or format_date(now + _dt.timedelta(days=365))
        )
        return b_src[:6] + "0000", e_src[:6] + "2359"

    def _legacy_random16(self) -> str:
        return f"{random.randint(0, 0xFFFE):04X}"

    def _legacy_random8(self) -> str:
        return f"{random.randint(0, 0xFE):02X}"

    # ── 系统卡（Master/Building/Floor/Emergency/Group）使用 PayloadFactory ──────────
    # 替换旧的 _issue_legacy_system_payload 路径

    def _system_payload_factory(self) -> Optional[Any]:
        """延迟初始化并返回 PayloadFactory 实例。"""
        if self._system_factory is None:
            try:
                from .profile.payload_factory import BrandPayloadFactory, BrandProfileLoader
                _pro = BrandProfileLoader.load("prousb_v9") or {}
                self._system_factory = BrandPayloadFactory(_pro, dlsCoID=int(self._dlsCoID))
            except Exception as exc:
                logger.warning("PayloadFactory 初始化失败 %s", exc)
        return self._system_factory

    def _issue_system_card(self, fn_name: str, card_type: str, **fields) -> CardResult:
        """通用系统卡发卡：由 PayloadFactory 构造 + DirectWriteUSB 写卡 + 读回校验。

        自动管理 seq nibble（byte[9] 低 4 位），每次发卡递增并持久化。
        不同类型独立计数（master, building, floor...）。
        """
        err = self._check_open()
        if err is not None:
            return err

        # 自动分配 seq（若调用方未显式指定）
        if "seq" not in fields:
            try:
                from .seq_manager import get_next_system_seq
                fields["seq"] = get_next_system_seq(card_type)
            except Exception:
                pass  # 降级：seq=0，由 PayloadFactory 兜底

        factory = self._system_payload_factory()
        if factory is None:
            return CardResult.fail("PayloadFactory 不可用")

        # 构建 payload
        try:
            payload_hex = factory.build(card_type, **fields)
        except (ValueError, Exception) as exc:
            return CardResult.fail(f"{fn_name} PayloadFactory 失败: {exc}")

        if len(payload_hex) != 32:
            return CardResult.fail(f"{fn_name} payload 长度异常: {len(payload_hex)}")

        before = self.read_card_payload() or ""
        return self._rewrite_payload(fn_name, before, "", payload_hex, "PAYLOAD_FACTORY")

    def issue_master_card(self, *, b_date: str = "", e_date: str = "",
                          card_no: int = 1, dai: int = 0,
                          llock: bool = True) -> CardResult:
        return self._issue_system_card(
            "MasterCard", "master",
            b_date=b_date, e_date=e_date,
            card_no=card_no, llock=llock,
            extra_hex=self._legacy_random16(),
        )

    def issue_building_card(self, *, b_date: str = "", e_date: str = "",
                            building_no: int = 1, card_no: int = 1,
                            dai: int = 0) -> CardResult:
        return self._issue_system_card(
            "BuildingCard", "building",
            b_date=b_date, e_date=e_date,
            card_no=card_no,
            extra_hex=f"FF{int(building_no) & 0xFF:02X}",
        )

    def issue_floor_card(self, *, b_date: str = "", e_date: str = "",
                         building_no: int = 1, floor_no: int = 3,
                         card_no: int = 1, dai: int = 0) -> CardResult:
        return self._issue_system_card(
            "FloorCard", "floor",
            b_date=b_date, e_date=e_date,
            card_no=card_no,
            extra_hex=f"{int(floor_no) & 0xFF:02X}{int(building_no) & 0xFF:02X}",
        )

    def issue_emergency_card(self, *, b_date: str = "", e_date: str = "",
                             card_no: int = 1, dai: int = 0) -> CardResult:
        return self._issue_system_card(
            "EmergencyCard", "emergency",
            b_date=b_date, e_date=e_date,
            card_no=card_no,
            emergency_site_bit=True,
            extra_hex=self._legacy_random16(),
        )

    def issue_group_card(self, *, b_date: str = "", e_date: str = "",
                         group_no: int = 1, card_no: int = 1,
                         dai: int = 0, llock: bool = False) -> CardResult:
        return self._issue_system_card(
            "GroupCard", "group",
            b_date=b_date, e_date=e_date,
            card_no=card_no, llock=llock,
            extra_hex=f"{int(group_no) & 0xFF:02X}00",
        )

    def issue_group_set_card(
        self, *, b_date: str = "", e_date: str = "", group_no: int = 1, card_no: int = 1,
    ) -> CardResult:
        tpl = self.winner_signature_template("GroupSetCard") or [
            {"kind": "u8", "value": 1},
            {"kind": "i32", "value": "__DLSCOID__"},
            {"kind": "u8", "value": "__GROUP_NO__"},
            {"kind": "u8", "value": "__CARD_NO__"},
            {"kind": "cstr", "value": "__B_DATE__", "size": 11},
            {"kind": "cstr", "value": "__E_DATE__", "size": 11},
            {"kind": "outbuf", "size": 256},
        ]
        return self._issue_special_by_signature(
            "GroupSetCard", tpl, b_date=b_date, e_date=e_date, group_no=group_no, card_no=card_no,
        )

    def issue_check_out_card(self, *, b_date: str = "", card_no: int = 1, dai: int = 0) -> CardResult:
        tpl = self.winner_signature_template("CheckOutCard") or [
            {"kind": "u8", "value": 1},
            {"kind": "i32", "value": "__DLSCOID__"},
            {"kind": "u8", "value": "__CARD_NO__"},
            {"kind": "u8", "value": "__DAI__"},
            {"kind": "cstr", "value": "__B_DATE__", "size": 11},
            {"kind": "outbuf", "size": 256},
        ]
        return self._issue_special_by_signature(
            "CheckOutCard", tpl, b_date=b_date, card_no=card_no, dai=dai,
        )

    def issue_record_card(self, *, b_date: str = "", card_no: int = 1, dai: int = 0) -> CardResult:
        before = self.read_card_payload() or ""
        marker = self._reference_time_marker(b_date, card_no, dai)
        tpl = self.winner_signature_template("RecordCard") or [
            {"kind": "u8", "value": 1},
            {"kind": "i32", "value": "__DLSCOID__"},
            {"kind": "u8", "value": "__CARD_NO__"},
            {"kind": "u8", "value": "__DAI__"},
            {"kind": "cstr", "value": "__B_DATE__", "size": 11},
            {"kind": "outbuf", "size": 256},
        ]
        res = self._issue_special_by_signature(
            "RecordCard", tpl, b_date=b_date, card_no=card_no, dai=dai,
        )
        pl = (res.card_hex or "").upper()
        if res.success and marker and len(pl) == 32 and pl.startswith("C92B20B7"):
            return self._rewrite_payload("RecordCard", before, pl, pl[:20] + marker + marker, "H11")
        return res

    def issue_room_no_card(self, *, lock_no: str, b_date: str = "", card_no: int = 1, dai: int = 0) -> CardResult:
        before = self.read_card_payload() or ""
        room_lock = self._roomset_lock_code(lock_no)
        tpl = self.winner_signature_template("RoomSetCard") or [
            {"kind": "u8", "value": 1},
            {"kind": "i32", "value": "__DLSCOID__"},
            {"kind": "u8", "value": "__CARD_NO__"},
            {"kind": "u8", "value": "__DAI__"},
            {"kind": "cstr", "value": "__LOCK_NO__", "size": 9},
            {"kind": "cstr", "value": "__B_DATE__", "size": 11},
            {"kind": "outbuf", "size": 256},
        ]
        res = self._issue_special_by_signature(
            "RoomSetCard", tpl, lock_no=lock_no, b_date=b_date, card_no=card_no, dai=dai,
        )
        pl = (res.card_hex or "").upper()
        if res.success and len(pl) == 32 and pl.startswith("C92B20B7"):
            marker = pl[20:26]
            patched = pl[:8] + room_lock + pl[16:20] + marker + marker
            return self._rewrite_payload("RoomSetCard", before, pl, patched, "H12")
        return res

    def issue_clock_card(self, *, b_date: str = "", card_no: int = 1, dai: int = 0) -> CardResult:
        before = self.read_card_payload() or ""
        marker = self._reference_time_marker(b_date, card_no, dai)
        tpl = self.winner_signature_template("TimeSetCard") or [
            {"kind": "u8", "value": 1},
            {"kind": "i32", "value": "__DLSCOID__"},
            {"kind": "u8", "value": "__CARD_NO__"},
            {"kind": "u8", "value": "__DAI__"},
            {"kind": "cstr", "value": "__B_DATE__", "size": 11},
            {"kind": "outbuf", "size": 256},
        ]
        res = self._issue_special_by_signature(
            "TimeSetCard", tpl, b_date=b_date, card_no=card_no, dai=dai,
        )
        pl = (res.card_hex or "").upper()
        if res.success and marker and len(pl) == 32 and pl.startswith("C92B20B7"):
            return self._rewrite_payload("TimeSetCard", before, pl, pl[:20] + marker + marker, "H11")
        return res

    def issue_auth_card(self, *, b_date: str = "") -> CardResult:
        """授权/初始化卡（IniCard）。

        功能：把发卡器的 firmware 与这台 PC 绑定，firmware 信任后
              写卡操作才能正常进行。如果发卡器初始化失败（firmware
              拒绝信任），可以用这张卡来授权 PC。

        延迟：常需空白卡 + 老软件前置流程，不阻塞 Solid 前台。

        使用前提：
          - 发卡器已通过 initializeUSB(0) 完成 firmware 握手
          - 如果 firmware 握手失败（蜂鸣器长鸣），请先刷授权卡
            让发卡器信任这台 PC，或物理拔插 USB 60 秒后重试
        """
        return self._issue_special_by_signature(
            "IniCard",
            [
                {"kind": "u8", "value": 1},
                {"kind": "i32", "value": "__DLSCOID__"},
                {"kind": "cstr", "value": "__B_DATE__", "size": 11},
                {"kind": "outbuf", "size": 256},
            ],
            b_date=b_date,
        )

    def issue_ini_card(self, *, b_date: str = "") -> CardResult:
        """同 issue_auth_card，别名。"""
        return self.issue_auth_card(b_date=b_date)

    def issue_special_card(self, *, b_date: str = "") -> CardResult:
        """特殊卡。延迟：当前易写出 HZSPECIALCARD 占位，不阻塞前台。"""
        return self._issue_special_by_signature(
            "SpecialCard",
            [
                {"kind": "u8", "value": 1},
                {"kind": "i32", "value": "__DLSCOID__"},
                {"kind": "cstr", "value": "__B_DATE__", "size": 11},
                {"kind": "outbuf", "size": 256},
            ],
            b_date=b_date,
        )

    def erase_card(self, card_hex: str = "") -> CardResult:
        err = self._check_open()
        if err is not None:
            return err
        before = self.read_card_payload() or ""
        try:
            resp = self._ensure_bridge().card_erase(
                d12=1, dlsCoID=self._dlsCoID, card_hex=card_hex or "",
            )
        except Exception as e:
            return CardResult.fail(f"调用 CardErase 失败: {e}")
        r = self._parse_card_resp(resp)
        if not r.success:
            if self._erase_says_foreign(r):
                fb = self._fallback_init_then_erase()
                if fb is not None:
                    return self._verify_erased_payload(before, fb)
            return r
        return self._verify_erased_payload(before, r)

    def _verify_erased_payload(self, before: str, result: CardResult) -> CardResult:
        """CardErase 的 ret=0 不一定代表卡面真的变干净。

        V9 脏卡/调查卡（payload 大段 BBBB）会出现“DLL 返回成功但读卡仍旧”的假成功。
        这里强制复读校验；若仍脏，走 IniCard -> CardErase 再救一次。
        """
        after = self.read_card_payload() or (result.card_hex or "")
        after_u = (after or "").upper()
        before_u = (before or "").upper()
        if self._looks_like_blank_payload(after_u):
            return CardResult.ok(after_u, raw_ret=result.raw_ret)

        # 假成功：卡面没变，或仍是调查/脏卡。尝试强制恢复一次。
        if before_u and (after_u == before_u or self._looks_like_dirty_probe_payload(after_u)):
            fb = self._fallback_init_then_erase()
            if fb is not None and fb.success:
                after2 = self.read_card_payload() or (fb.card_hex or "")
                after2_u = (after2 or "").upper()
                if self._looks_like_blank_payload(after2_u):
                    return CardResult.ok(after2_u, raw_ret=fb.raw_ret)
                return CardResult.fail(
                    f"CardErase 返回成功但卡面未复位（仍为 {after2_u[:32] or '空'}）。请换一张空白卡或用厂家工具强制格式化。",
                    raw_ret=fb.raw_ret,
                )

        return CardResult.fail(
            f"CardErase 返回成功但校验失败：当前卡面 {after_u[:32] or '空'}，不是空白卡。",
            raw_ret=result.raw_ret,
        )

    # ──────────────────────────────────────────────────────────────
    # Solid 自产空白卡
    #
    # 业务上等价于厂家卖给酒店的"预授权空白卡"：payload 前 4 字节是
    # C92B20B7 厂家头，4..7 是本酒店 dlsCoID，类型字节 (offset 13) 高半字节
    # 为 0xF（"空白卡"），其余字段无房号、无客人、无楼层信息。
    # 锁刷到这种卡时识别为"我家酒店的卡，但目前还没分配角色，不开任何门"，
    # 之后再用任意发卡函数写到同一张卡上即可立刻变成对应卡型。
    # CardLock.exe 故意不提供此功能（厂家商业模式），但我们做完全合理。
    # ──────────────────────────────────────────────────────────────

    BLANK_CARD_BATCH_MAX = 30

    def issue_blank_card(self, *, count: int = 1) -> CardResult:
        """发空白卡：CardErase 把卡复位为"已带本酒店授权码、无任何角色"。

        - count: 1..30。批量发卡时由调用方逐张换卡（每张耗时约 0.5-1 秒）。
        - 异常路径：若 CardErase 返回 ret=15「非本酒店卡」，会自动尝试
          IniCard 把酒店码注入再重试，等价于把外购未授权 RFID 卡也变成
          本酒店空白卡。IniCard 失败时返回原始 CardErase 错误，不破坏卡。
        - 返回最后一张卡的 CardResult；中途失败立即返回那张失败 result。
        """
        try:
            n = int(count)
        except (TypeError, ValueError):
            return CardResult.fail("count 必须是整数")
        if n < 1:
            return CardResult.fail("count 必须 ≥ 1")
        if n > self.BLANK_CARD_BATCH_MAX:
            return CardResult.fail(
                f"count 上限 {self.BLANK_CARD_BATCH_MAX}（防止过度自产）",
            )
        err = self._check_open()
        if err is not None:
            return err
        last: CardResult = CardResult.fail("未执行")
        for i in range(n):
            last = self._issue_one_blank_card(index=i + 1, total=n)
            if not last.success:
                return last
        return last

    def _issue_one_blank_card(self, *, index: int, total: int) -> CardResult:
        """实际写一张空白卡：CardErase → 必要时 IniCard 降级 → 校验 payload 头。"""
        bridge = self._ensure_bridge()
        before = self.read_card_payload() or ""
        prefix = f"第 {index}/{total} 张空白卡：" if total > 1 else ""

        try:
            resp = bridge.card_erase(d12=1, dlsCoID=self._dlsCoID, card_hex="")
        except Exception as e:
            return CardResult.fail(f"{prefix}调用 CardErase 异常: {e}")
        r = self._parse_card_resp(resp)

        if not r.success and self._erase_says_foreign(r):
            fb = self._fallback_init_then_erase()
            if fb is not None:
                r = fb

        after = self.read_card_payload() or (r.card_hex if r.success else "")
        after_u = (after or "").upper()
        if r.success:
            if not after_u.startswith("C92B20B7"):
                r = CardResult.fail(
                    f"{prefix}写卡后 payload 头不对（{after_u[:24] or '空'}…），疑似卡片不兼容",
                    raw_ret=r.raw_ret,
                )
            elif len(after_u) >= 28 and after_u[26] != "F":
                r = CardResult.fail(
                    f"{prefix}写卡后 type nibble = {after_u[26]}（期望 F=空白卡），可能 CardErase 已废弃",
                    raw_ret=r.raw_ret,
                )
            else:
                r = CardResult.ok(after_u, raw_ret=r.raw_ret)
        else:
            r = CardResult.fail(f"{prefix}{r.error or '未知错误'}", raw_ret=r.raw_ret)
        self._corpus_after_issue("BlankCard", before, r, tag="blank")
        if r.success:
            self._success_buzzer("BlankCard")
        return r

    @staticmethod
    def _erase_says_foreign(r: CardResult) -> bool:
        """CardErase 返回是否表示"非本酒店卡"，需要走 IniCard 降级。"""
        if r.raw_ret == 15:
            return True
        err = (r.error or "")
        return ("非本酒店" in err) or ("非本系统" in err)

    @staticmethod
    def _looks_like_blank_payload(payload: str) -> bool:
        pl = (payload or "").strip().upper()
        return bool(pl.startswith("C92B20B7") and len(pl) >= 28 and pl[26] == "F")

    @staticmethod
    def _looks_like_dirty_probe_payload(payload: str) -> bool:
        pl = (payload or "").strip().upper()
        return ("BBBBBBBB" in pl) or pl.startswith("BBBB")

    def _fallback_init_then_erase(self) -> Optional[CardResult]:
        """处理"非本酒店卡"：先用 IniCard 注入酒店码，再 CardErase 复位。

        外购的、未与本酒店绑定的 RFID 卡走这条路。
        若 IniCard 调用本身失败，返回 None（让上层保留原 CardErase 错误）。
        """
        bridge = self._ensure_bridge()
        b_date = format_date(_dt.datetime.now())
        signature = [
            {"kind": "u8", "value": 1},
            {"kind": "i32", "value": int(self._dlsCoID)},
            {"kind": "cstr", "value": b_date, "size": 11},
            {"kind": "outbuf", "size": 256},
        ]
        try:
            resp = bridge.call_card_fn(fn_name="IniCard", signature=signature, timeout=15.0)
        except Exception:
            return None
        if not resp.get("ok"):
            return None
        try:
            resp2 = bridge.card_erase(d12=1, dlsCoID=self._dlsCoID, card_hex="")
        except Exception as e:
            return CardResult.fail(f"IniCard 后重试 CardErase 异常: {e}")
        return self._parse_card_resp(resp2)

    # ──────────────────────────────────────────────────────────────
    # 卡片解析（离线，不需要发卡器）
    # ──────────────────────────────────────────────────────────────

    def parse_card_type(self, card_hex: str) -> str:
        payload_type = parse_payload_card_type(card_hex)
        try:
            if not self._ensure_dll_loaded():
                return payload_type
            resp = self._ensure_bridge().parse_card_type(card_hex)
        except Exception as e:
            return payload_type or f"(error: {e})"
        if not resp.get("ok"):
            return payload_type or f"(error: {resp.get('error', 'unknown')})"
        code = (resp.get("out") or {}).get("card_type", "")
        dll_type = parse_card_type_code(code)
        if payload_type:
            return payload_type
        return dll_type

    def parse_guest_lock_no(self, card_hex: str) -> Optional[str]:
        if not self._dlsCoID:
            return None
        try:
            if not self._ensure_dll_loaded():
                return None
            resp = self._ensure_bridge().get_guest_lock_no(self._dlsCoID, card_hex)
        except Exception:
            return None
        if not resp.get("ok") or int(resp.get("ret", -1)) != 0:
            return None
        return (resp.get("out") or {}).get("lock_no", "")

    def parse_guest_etime(self, card_hex: str) -> Optional[str]:
        if not self._dlsCoID:
            return None
        try:
            if not self._ensure_dll_loaded():
                return None
            resp = self._ensure_bridge().get_guest_etime(self._dlsCoID, card_hex)
        except Exception:
            return None
        if not resp.get("ok") or int(resp.get("ret", -1)) != 0:
            return None
        return (resp.get("out") or {}).get("e_time", "")

    # ──────────────────────────────────────────────────────────────
    # 工具
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_date(s: str) -> str:
        s = (s or "").strip()
        if len(s) == 10 and s.isdigit():
            return s
        # 尝试用常见格式转
        for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%y%m%d%H%M"):
            try:
                return _dt.datetime.strptime(s, fmt).strftime("%y%m%d%H%M")
            except Exception:
                pass
        # 兜底：右对齐补 0
        return s.ljust(10, "0")[:10]

    @staticmethod
    def _normalize_lock_no(s: str) -> str:
        """规范化为 8 位大写 hex。
        - 纯 hex 直接补齐到 8 位
        - 别的输入（极少见）做兜底补 0
        """
        s = (s or "").strip().upper()
        try:
            int(s, 16)
            return s.ljust(8, "0")[:8]
        except ValueError:
            return s.ljust(8, "0")[:8]

    @staticmethod
    def _parse_card_resp(resp: Dict[str, Any]) -> CardResult:
        if not resp.get("ok"):
            return CardResult.fail(resp.get("error", "unknown"))
        ret = int(resp.get("ret", -1))
        out = resp.get("out") or {}
        hex_str = out.get("hex", "")
        if ret == 0:
            return CardResult.ok(hex_str, raw_ret=ret)
        return CardResult.fail(_decode_ret_code(ret), raw_ret=ret)

    @staticmethod
    def _parse_compose_resp(resp: Dict[str, Any]) -> CardResult:
        """compose_guest_card 返回的多阶段结果。"""
        if not resp.get("ok"):
            return CardResult.fail(resp.get("error", "unknown"))
        ret = int(resp.get("ret", -1))
        out = resp.get("out") or {}
        if ret == 0 and out.get("match"):
            payload = out.get("new_payload") or out.get("verify_payload") or ""
            return CardResult.ok(payload, raw_ret=0)
        stage = out.get("stage", "?")
        err = out.get("error") or f"{stage} 阶段失败 (ret={ret})"
        return CardResult.fail(err, raw_ret=ret)


def _decode_ret_code(ret: int) -> str:
    """把 DLL 返回码翻成人话。

    返回码与门锁刷卡时的"嘀嘀"蜂鸣声次数一致，
    取自厂家 Help.txt（智能门锁管理系统新2021网络版）。
    """
    common = {
        -1: "未知错误",
        0:  "成功",
        1:  "USB 未连接或发卡器未上电",
        2:  "卡片未放置在发卡器上 / 读卡失败",
        3:  "门锁已反锁（请用能开反锁的卡或先解除反锁）",
        4:  "此卡号已经被挂失（黑名单）",
        6:  "房号不对（请先用『房号设置卡』给门锁设置房号）",
        7:  "卡已过期（请先用『时间设置卡』给门锁校时）",
        8:  "客人卡被后续客人卡覆盖 / 已被退房卡限制 "
            "(功能卡：开锁时段不在允许范围)",
        9:  "卡已被挂失（已进入黑名单）",
        10: "授权码错误（请先拧机械钥匙再刷授权卡）",
        11: "楼栋号或楼层号无效（请先设置门锁房号）",
        12: "员工卡被后卡覆盖（请重新刷授权卡）",
        15: "非本酒店的卡（请刷授权卡或重新发卡）",
        30: "非本系统的卡（请重新发卡）",
    }
    return common.get(ret, f"DLL 返回错误码 {ret}")
