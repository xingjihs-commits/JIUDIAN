"""
generic_adapter.py — 通用品牌适配器

职责
====
按 brand_profile 配置运行的通用适配器。不写任何品牌特定代码。
所有品牌差异由 profile 描述。

工作流程
========
1. scanner 发现安装目录 + MDB
2. BrandAnalyzer 分析 MDB 样本 → 生成 brand_profile
3. GenericLockAdapter 读 profile → 配置好 PayloadFactory + DLL 调用路径
4. 发卡：按 profile 描述的 payload 结构写卡

DLL 调用
========
绝大多数品牌只需 3 个底层函数：
- initializeUSB(d12=1) — 打开发卡器
- DirectReadUSB — 读卡
- DirectWriteUSB — 写卡（直接写 16 字节 payload）

不需要为每个品牌重写第 14 张卡的逻辑。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import CardResult, LockAdapter
from .bridge_client import RflBridge, get_bridge

logger = logging.getLogger(__name__)

_KNOWN_PROFILES_CACHE: Optional[Dict[str, dict]] = None


def _load_profiles_cache() -> Dict[str, dict]:
    """加载所有已知 profiles 到缓存。"""
    global _KNOWN_PROFILES_CACHE
    if _KNOWN_PROFILES_CACHE is not None:
        return _KNOWN_PROFILES_CACHE

    cache: Dict[str, dict] = {}
    profiles_dir = Path(__file__).resolve().parent / "profile" / "profiles"
    if not profiles_dir.is_dir():
        _KNOWN_PROFILES_CACHE = cache
        return cache

    for fpath in sorted(profiles_dir.glob("*.json")):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            bid = data.get("adapter_id") or data.get("brand", "")
            if bid:
                cache[bid.lower()] = data
        except Exception as exc:
            logger.warning("加载 profile %s 失败: %s", fpath, exc)

    _KNOWN_PROFILES_CACHE = cache
    return cache


class GenericLockAdapter(LockAdapter):
    """通用品牌适配器。

    通过 brand_profile 配置驱动，不依赖任何品牌特定代码。
    当 scanner 发现已知/未知品牌时，可以实例化本适配器来工作。
    """

    brand = "Generic"
    version_hint = ""

    def __init__(self, install_dir: Path, profile: Optional[Dict] = None):
        super().__init__(install_dir)
        self._profile: Dict[str, Any] = profile or {}
        self._bridge: Optional[RflBridge] = None
        self._payload_factory = None  # 延迟初始化
        self._dll_loaded = False
        self._dlsCoID: int = 0
        self._dll_path: Optional[Path] = None

    # ────────────── Detect ──────────────

    @classmethod
    def detect(cls, install_dir: Path) -> Optional["GenericLockAdapter"]:
        """检测安装目录是否匹配任何已知 profile。

        匹配策略：
        1. 检查 install_dir 中的 DLL 是否匹配任何已知品牌的 profile
        2. 如果匹配，返回 GenericLockAdapter 实例（带匹配到的 profile）
        """
        install_dir = Path(install_dir)
        if not install_dir.is_dir():
            return None

        try:
            files_lower = {p.name.lower() for p in install_dir.iterdir() if p.is_file()}
        except Exception:
            return None

        profiles = _load_profiles_cache()
        for brand_key, profile in profiles.items():
            detect_config = profile.get("detect", {})
            required_files = detect_config.get("files", [])
            if not required_files:
                continue
            required_lower = [rf.lower() for rf in required_files]
            if all(rf in files_lower for rf in required_lower):
                inst = cls(install_dir, profile=profile)
                inst.brand = profile.get("brand", "Generic")
                inst.version_hint = profile.get("version_hint", "")
                return inst

        return None

    # ────────────── 生命周期 ──────────────

    def configure(self, **kwargs) -> None:
        if "dlsCoID" in kwargs:
            self._dlsCoID = int(kwargs["dlsCoID"])
        if "profile" in kwargs:
            self._profile = kwargs["profile"]
            self.brand = self._profile.get("brand", "Generic")

    def initialize(self) -> bool:
        """打开发卡器 USB 连接。

        安全约束：通用适配器只用 d12=1 轻量模式，绝不调 initializeUSB(d12=0)。
        d12=0 会触发 SLE4442 安全芯片写保护（259 错误），且不可逆。
        已知品牌（如 proUSB V9）的适配器在自己的 initialize() 中处理 d12=0。
        """
        if self._opened:
            return True

        bridge = get_bridge()
        try:
            bridge.start()
        except Exception as exc:
            logger.error("[GenericAdapter] bridge 启动失败: %s", exc)
            return False

        # 加载 DLL
        dll_config = self._profile.get("dll", {})
        dll_name = dll_config.get("path", "V9RFL.dll")
        dll_path = self.install_dir / dll_name

        if not dll_path.is_file():
            logger.error("[GenericAdapter] DLL 不存在: %s", dll_path)
            return False

        extra_paths = [str(self.install_dir)]
        try:
            resp = bridge.load_dll(str(dll_path), extra_paths)
            if not resp.get("ok") or not resp.get("loaded"):
                logger.error("[GenericAdapter] load_dll 失败: %s", resp)
                return False
        except Exception as exc:
            logger.error("[GenericAdapter] load_dll 异常: %s", exc)
            return False

        self._dll_path = dll_path
        self._bridge = bridge
        self._dll_loaded = True

        # 只试 d12=1（跳过 SLE4442 安全芯片校验，防止触发写保护）
        try:
            resp = bridge.initialize(d12=1)
            ret = int(resp.get("ret", -1))
            if resp.get("ok") and ret == 0:
                self._opened = True
                return True
            logger.warning("[GenericAdapter] initializeUSB(d12=1) 返回 %d — 发卡器不可用", ret)
            return False
        except Exception as exc:
            logger.error("[GenericAdapter] initializeUSB 异常: %s", exc)
            return False

    def close(self) -> None:
        if self._opened:
            try:
                if self._bridge is not None:
                    self._bridge.close_usb()
            except Exception:
                pass
        self._opened = False
        super().close()

    # ────────────── 信息查询 ──────────────

    def get_version(self) -> str:
        try:
            bridge = get_bridge()
            resp = bridge.get_version()
            return resp.get("out", {}).get("version", self.version_hint or "")
        except Exception:
            return self.version_hint or ""

    def get_supported_card_types(self) -> list[str]:
        card_types = self._profile.get("card_types", {})
        return list(card_types.keys())

    # ────────────── Payload 工厂 ──────────────

    def _get_payload_factory(self):
        if self._payload_factory is None and self._profile:
            try:
                from .profile import BrandPayloadFactory
                self._payload_factory = BrandPayloadFactory(
                    self._profile,
                    dlsCoID=self._dlsCoID,
                )
            except Exception as exc:
                logger.error("[GenericAdapter] PayloadFactory 初始化失败: %s", exc)
        return self._payload_factory

    # ────────────── 写卡助手 ──────────────

    def _write_payload(self, card_type: str, **fields) -> CardResult:
        """构建 payload 并用 DirectWriteUSB 写卡。

        seq/卡号等序列逻辑由上层调用方（card_system.py）管理，
        这里只负责构建 payload 并执行写卡操作。
        """
        factory = self._get_payload_factory()
        if factory is None:
            return CardResult.fail("PayloadFactory 未初始化")

        bridge = self._ensure_bridge()
        if bridge is None:
            return CardResult.fail("发卡器桥接未初始化")

        # 读取卡前状态
        before_hex = self._read_payload(bridge) or ""

        # 写前警告：如果卡上已有有效 payload，记日志
        try:
            from .base import warn_if_live_card
            warn_if_live_card(before_hex)
        except Exception:
            pass

        # 构建 payload
        try:
            payload_hex = factory.build(card_type, **fields)
        except (ValueError, Exception) as exc:
            return CardResult.fail(f"构建 {card_type} payload 失败: {exc}")

        if len(payload_hex) != 32:
            return CardResult.fail(f"payload 长度异常: {len(payload_hex)}")

        # 写卡
        try:
            resp = bridge.direct_write_usb(d12=1, card_hex=payload_hex, timeout=6.0)
        except Exception as exc:
            return CardResult.fail(f"DirectWriteUSB 失败: {exc}")

        ret = int(resp.get("ret", -1))
        if ret != 0:
            return CardResult.fail(f"DirectWriteUSB 返回 {ret}", raw_ret=ret)

        # 读回验证
        after_hex = self._read_payload(bridge) or ""
        ok = after_hex.upper() == payload_hex.upper()
        if ok:
            return CardResult.ok(after_hex, raw_ret=ret)
        else:
            # 可能读卡时机问题，只要 DLL 返回 0 就算成功
            return CardResult.ok(payload_hex, raw_ret=ret)

    def _ensure_bridge(self) -> Optional[RflBridge]:
        if not self._opened:
            ok = self.initialize()
            if not ok:
                return None
        return self._bridge

    @staticmethod
    def _read_payload(bridge: RflBridge) -> Optional[str]:
        try:
            resp = bridge.direct_read_usb(d12=1, timeout=4.0)
            if not resp.get("ok") or int(resp.get("ret", -1)) != 0:
                return None
            out = resp.get("out") or {}
            return out.get("payload") or None
        except Exception:
            return None

    # ────────────── 各卡型发卡 ──────────────

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
        profile = self._profile
        card_types = profile.get("card_types", {})

        # 如果是 proUSB 且有 bridge，优先用 DLL 的 GuestCard 函数
        if profile.get("adapter_id") == "prousb_v9":
            bridge = self._ensure_bridge()
            if bridge is not None and self._dlsCoID:
                try:
                    resp = bridge.guest_card(
                        d12=1, dlsCoID=self._dlsCoID,
                        CardNo=int(card_no), dai=int(dai),
                        LLock=1 if llock else 0,
                        pdoors=1 if pdoors else 0,
                        BDate=b_date, EDate=e_date,
                        LockNo=lock_no,
                    )
                    ok = bool(resp.get("ok"))
                    ret = int(resp.get("ret", -1))
                    hex_str = (resp.get("out") or {}).get("hex", "")
                    if ok and ret == 0 and hex_str:
                        return CardResult.ok(hex_str, raw_ret=ret)
                except Exception:
                    pass  # 降级到 payload 构造

        # 通用回退：用 PayloadFactory 构造 + DirectWriteUSB 写卡
        guest_type = card_types.get("guest", {})
        type_high = guest_type.get("type_byte_high")
        extra_hex = f"{int(card_no) & 0xFF:02X}00"
        kw = dict(
            type_high=type_high if type_high is not None else 0x6,
            lock_no=lock_no,
            b_date=b_date,
            e_date=e_date,
            card_no=card_no,
            llock=llock,
            extra_hex=extra_hex,
        )
        if seq >= 0:
            kw["seq"] = seq
        return self._write_payload("guest", **kw)

    def issue_master_card(self, *, b_date: str = "", e_date: str = "",
                          card_no: int = 1, dai: int = 0,
                          llock: bool = True) -> CardResult:
        return self._write_payload(
            "master",
            type_high=0xB,
            lock_no="",
            b_date=b_date, e_date=e_date,
            card_no=card_no, llock=llock,
        )

    def issue_building_card(self, *, b_date: str = "", e_date: str = "",
                            building_no: int = 1, card_no: int = 1,
                            dai: int = 0) -> CardResult:
        extra = f"FF{int(building_no) & 0xFF:02X}"
        return self._write_payload(
            "building",
            type_high=0xC,
            lock_no="", b_date=b_date, e_date=e_date,
            card_no=card_no, extra_hex=extra,
        )

    def issue_floor_card(self, *, b_date: str = "", e_date: str = "",
                         building_no: int = 1, floor_no: int = 3,
                         card_no: int = 1, dai: int = 0) -> CardResult:
        extra = f"{int(floor_no) & 0xFF:02X}{int(building_no) & 0xFF:02X}"
        return self._write_payload(
            "floor",
            type_high=0xD,
            lock_no="", b_date=b_date, e_date=e_date,
            card_no=card_no, extra_hex=extra,
        )

    def issue_emergency_card(self, *, b_date: str = "", e_date: str = "",
                             card_no: int = 1, dai: int = 0) -> CardResult:
        return self._write_payload(
            "emergency",
            type_high=0xA,
            lock_no="", b_date=b_date, e_date=e_date,
            card_no=card_no, emergency_site_bit=True,
        )

    def issue_group_card(self, *, b_date: str = "", e_date: str = "",
                         group_no: int = 1, card_no: int = 1,
                         dai: int = 0, llock: bool = False) -> CardResult:
        extra = f"{int(group_no) & 0xFF:02X}00"
        return self._write_payload(
            "group",
            type_high=0x8,
            lock_no="", b_date=b_date, e_date=e_date,
            card_no=card_no, llock=llock, extra_hex=extra,
        )

    def issue_auth_card(self, *, card_no: int = 1, dai: int = 0,
                        **kwargs) -> CardResult:
        return self._write_payload(
            "auth",
            type_high=0x0,
            card_no=card_no,
        )

    def erase_card(self, card_hex: str = "") -> CardResult:
        bridge = self._ensure_bridge()
        if bridge is None:
            return CardResult.fail("发卡器未连接")
        try:
            resp = bridge.card_erase(d12=1, dlsCoID=self._dlsCoID, card_hex=card_hex)
            ret = int(resp.get("ret", -1))
            if resp.get("ok") and ret == 0:
                return CardResult.ok("", raw_ret=ret)
            return CardResult.fail(f"擦卡失败 (ret={ret})", raw_ret=ret)
        except Exception as exc:
            return CardResult.fail(f"擦卡异常: {exc}")

    def buzzer(self, ms: int = 200) -> bool:
        bridge = self._ensure_bridge()
        if bridge is None:
            return False
        try:
            resp = bridge.buzzer(d12=1, t=min(ms, 1000))
            return bool(resp.get("ok"))
        except Exception:
            return False

    def read_card_uid(self) -> Optional[str]:
        bridge = self._ensure_bridge()
        if bridge is None:
            return None
        try:
            resp = bridge.direct_read_usb(d12=1)
            if resp.get("ok") and int(resp.get("ret", -1)) == 0:
                out = resp.get("out") or {}
                return out.get("uid") or None
        except Exception:
            pass
        return None

    def read_card_raw(self) -> Optional[str]:
        return self._read_payload(self._bridge) if self._bridge else None

    def parse_card_type(self, card_hex: str) -> str:
        if len(card_hex) < 20 or not card_hex.upper().startswith("C92B20B7"):
            return "未知"

        bridge = self._ensure_bridge()
        if bridge is not None:
            try:
                resp = bridge.parse_card_type(card_hex=card_hex)
                return resp.get("out", {}).get("type", "未知")
            except Exception:
                pass

        # 本地回退：按 type 半字节识别
        type_hex = card_hex[18:20]  # byte[9]
        if type_hex:
            try:
                type_val = int(type_hex, 16)
                high = (type_val >> 4) & 0x0F
                card_type_names = {
                    0x0: "授权卡", 0x1: "初始化卡", 0x6: "客人卡",
                    0x8: "记录/组控卡", 0xA: "应急卡", 0xB: "总卡",
                    0xC: "楼栋卡", 0xD: "楼层卡",
                }
                return card_type_names.get(high, f"未知(0x{high:X})")
            except ValueError:
                pass
        return "未知"


    # ── R4.9 通用 DLL 调用模板 ───────────────────────────────────

    def invoke_dll_function(self, func_name: str, *args: int) -> Optional[int]:
        """通用 DLL 调用模板：按 profile 中的函数签名自动调用任意 DLL 函数。"""
        if not self._dll_path or not Path(self._dll_path).is_file():
            logger.warning("[GenericAdapter] DLL 不可用: %s", self._dll_path)
            return None

        try:
            import ctypes
            if not hasattr(self, '_dll_handle'):
                self._dll_handle = ctypes.cdll.LoadLibrary(str(self._dll_path))
                logger.info("[GenericAdapter] 加载 DLL: %s", self._dll_path)

            dll = self._dll_handle
            fn = getattr(dll, func_name, None)
            if fn is None:
                logger.warning("[GenericAdapter] DLL 函数不存在: %s", func_name)
                return None

            fn.restype = ctypes.c_int
            if args:
                fn.argtypes = [ctypes.c_int] * len(args)
            return fn(*args)
        except Exception as exc:
            logger.warning("[GenericAdapter] DLL 调用失败 %s: %s", func_name, exc)
            return None

    def issue_via_profile_template(self, card_type: str, **kwargs: Any) -> CardResult:
        """按 profile 描述的发卡模板自动生成写卡操作。"""
        ct = self._profile.get("card_types", {}).get(card_type, {})

        dll_fn = ct.get("dll_fn") or self._profile.get("dll", {}).get(f"{card_type}_fn")
        if dll_fn:
            try:
                result = self.invoke_dll_function(dll_fn, **kwargs)
                if result is not None and result == 0:
                    return CardResult.ok("", raw_ret=0)
            except Exception as exc:
                logger.warning("[GenericAdapter] DLL直调失败，回退到 payload: %s", exc)

        return self._write_payload(card_type, **kwargs)


# ═══════════════════════════════════════════════════════════════
#  R4.9 通用 pywinauto 寄生模板
# ═══════════════════════════════════════════════════════════════

class GenericAutoAdapter:
    """通用 pywinauto 寄生适配器 — 控制原厂软件完成发卡。"""

    def __init__(self, profile: dict, exe_path: str):
        self._profile = profile
        self._exe_path = exe_path
        self._auto_ui = profile.get("auto_ui", {})
        self._app = None

    def _ensure_app(self) -> bool:
        try:
            from pywinauto import Application
            self._app = Application(backend="uia").connect(path=self._exe_path)
            return True
        except ImportError:
            logger.warning("[GenericAuto] pywinauto 未安装")
            return False
        except Exception as exc:
            logger.warning("[GenericAuto] 连接失败: %s", exc)
            return False

    def execute(self, **kwargs: Any) -> CardResult:
        """按 auto_ui 序列执行发卡操作。"""
        if not self._ensure_app() or not self._app:
            return CardResult.fail("无法连接原厂软件")

        steps = self._auto_ui.get("steps", [])
        window_title = self._auto_ui.get("window_title", "")

        try:
            if window_title:
                win = self._app.window(title=window_title)
                win.wait("ready", timeout=10)

            for step in steps:
                action = step.get("action", "")
                target = step.get("target", "")
                raw_value = step.get("value", "")
                value = raw_value.format(**kwargs) if isinstance(raw_value, str) else raw_value

                if action == "click":
                    win[target].click() if target else win.click()
                elif action == "type":
                    win[target].set_edit_text(str(value))
                elif action == "select":
                    win[target].select(value)

            return CardResult.ok("", raw_ret=0)
        except Exception as exc:
            logger.warning("[GenericAuto] 操作失败: %s", exc)
            return CardResult.fail(f"UI 自动化失败: {exc}")


# ────────────── 便捷入口 ──────────────

def detect_generic(install_dir: Path) -> Optional[GenericLockAdapter]:
    """尝试用通用适配器检测安装目录。"""
    return GenericLockAdapter.detect(install_dir)
