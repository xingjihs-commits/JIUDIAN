"""
lock_adapters/base.py — LockAdapter 抽象基类

所有品牌适配器都继承这个基类。新增品牌只需实现这些方法，主代码不变。

——————————————————————————————————————————————————————
接口语义约定
——————————————————————————————————————————————————————

`detect(install_dir)`
类方法。给一个目录路径，如果里面有这个品牌的 DLL 特征文件，
就返回该品牌的 LockAdapter 实例；否则返回 None。
不要在 detect 里做副作用（不要打开 USB、不要写文件）。

`initialize()`
打开发卡器 USB 连接。成功返回 True；硬件未连、注册未完成等
任意原因失败都返回 False。可重入调用（即使已经 open 过）。

`get_version()`
返回 DLL 内部版本号字符串。**不需要连接发卡器硬件**，因此
可以作为最低风险的连通性测试。

`buzzer(ms)`
让发卡器嘀一声。验证 DLL → USB → 硬件 整条链路连通。
ms 是嘀的时长（毫秒），实际精度看硬件，一般 100ms 起步。

`read_card_uid()`
读取放在发卡器上的卡片的 UID（卡面唯一编号）。
如果发卡器上没卡或读失败，返回 None。

`issue_guest_card(lock_no, b_date, e_date, ...)`
发一张客人卡。lock_no 是 8 位锁号字符串（楼栋+楼层+房号 十六进制）。
b_date / e_date 都是 10 字节 "YYMMDDHHMM" 字符串（如 "2605221200"
表示 2026/05/22 12:00）。返回 CardResult。

`issue_auth_card / issue_master_card / ...`
各类卡的实现。具体参数因品牌而异，但都返回 CardResult。
某些品牌可能不支持某种卡 —— 实现时可以 raise NotImplementedError。

`close()`
关闭发卡器连接，释放 USB 句柄。应该在程序退出前调用。
"""

from __future__ import annotations

import dataclasses
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def warn_if_live_card(payload_hex: str) -> None:
    """写卡前检查：如果卡上已有有效 卡数据，打警告日志。

    不阻拦正常业务（酒店发卡本身就是覆盖），只在日志中记录，
    方便现场排查时追溯。
    """
    pl = (payload_hex or "").strip().upper()
    if not pl.startswith("C92B20B7"):
        return  # 空白卡或未初始化，安全
    if len(pl) < 20:
        return
    try:
        type_hex = pl[18:20]  # byte[9]
        type_val = int(type_hex, 16)
        high = (type_val >> 4) & 0x0F
        card_type_names = {
            0x0: "授权卡",
            0x1: "初始化卡",
            0x6: "客人卡",
            0x8: "记录/组控卡",
            0xA: "应急卡",
            0xB: "总卡",
            0xC: "楼栋卡",
            0xD: "楼层卡",
        }
        name = card_type_names.get(high, f"类型(0x{high:X})")
        logger.warning(
            "发卡器上现有有效 %s payload (type_byte=0x%02X)，即将覆盖。"
            "请确认此卡已回收或属于当前操作房间。",
            name, type_val,
        )
    except (ValueError, IndexError):
        pass


@dataclasses.dataclass
class CardResult:
    """
    发卡结果，所有适配器统一返回这个。

    success: 是否成功
    card_hex: 写到卡上的数据（十六进制 字符串），失败时为空
    error: 失败原因的人话描述
    raw_ret: 厂家 DLL 原始返回码（0 = OK，其他视品牌定义）
    """

    success: bool
    card_hex: str = ""
    error: str = ""
    raw_ret: int = 0

    def __bool__(self) -> bool:
        return self.success

    @classmethod
    def ok(cls, card_hex: str, raw_ret: int = 0) -> "CardResult":
        return cls(success=True, card_hex=card_hex, raw_ret=raw_ret)

    @classmethod
    def fail(cls, error: str, raw_ret: int = -1) -> "CardResult":
        return cls(success=False, error=error, raw_ret=raw_ret)


class LockAdapter(ABC):
    """所有门锁品牌适配器的统一接口。"""

    brand: str = "Unknown"
    version_hint: str = ""

    def __init__(self, install_dir: Path):
        self.install_dir = Path(install_dir)
        self._opened = False

    # ──────────────────────────────────────────────────────────────
    # 识别 / 生命周期
    # ──────────────────────────────────────────────────────────────

    @classmethod
    @abstractmethod
    def detect(cls, install_dir: Path) -> Optional["LockAdapter"]:
        ...

    @abstractmethod
    def initialize(self) -> bool:
        ...

    def close(self) -> None:
        self._opened = False

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    @property
    def is_open(self) -> bool:
        return self._opened

    # ──────────────────────────────────────────────────────────────
    # 信息查询（不需要发卡器在线）
    # ──────────────────────────────────────────────────────────────

    @abstractmethod
    def get_version(self) -> str:
        ...

    def get_supported_card_types(self) -> list[str]:
        """品牌支持的卡类型清单。子类按需扩展。"""
        return ["guest"]

    # ──────────────────────────────────────────────────────────────
    # 硬件交互（要求发卡器在线）
    # ──────────────────────────────────────────────────────────────

    def buzzer(self, ms: int = 200) -> bool:
        raise NotImplementedError(f"{self.brand} 不支持蜂鸣器")

    def read_card_uid(self) -> Optional[str]:
        raise NotImplementedError(f"{self.brand} 未实现读卡 UID")

    def read_card_raw(self) -> Optional[str]:
        """返回完整卡数据 十六进制字符串。"""
        raise NotImplementedError(f"{self.brand} 未实现读卡")

    # ──────────────────────────────────────────────────────────────
    # 各类卡发放 —— 子类按需实现，未实现的会自动 raise
    # ──────────────────────────────────────────────────────────────

    def issue_guest_card(
        self,
        lock_no: str,
        b_date: str,
        e_date: str,
        card_no: int = 1,
        llock: bool = True,
        pdoors: bool = False,
    ) -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现客人卡")

    def issue_auth_card(self, **kwargs) -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现授权卡")

    def issue_master_card(self, **kwargs) -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现总卡")

    def issue_floor_card(self, **kwargs) -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现楼层卡")

    def issue_building_card(self, **kwargs) -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现楼栋卡")

    def issue_clock_card(self, **kwargs) -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现时钟卡")

    def issue_room_no_card(self, **kwargs) -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现房号卡")

    def issue_loss_report_card(self, **kwargs) -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现挂失卡")

    def issue_emergency_card(self, **kwargs) -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现应急卡")

    def issue_record_card(self, **kwargs) -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现记录卡")

    def erase_card(self, card_hex: str = "") -> CardResult:
        raise NotImplementedError(f"{self.brand} 未实现擦卡")

    # ──────────────────────────────────────────────────────────────
    # 卡片分析（离线）
    # ──────────────────────────────────────────────────────────────

    def parse_card_type(self, card_hex: str) -> str:
        """识别 十六进制字符串对应的卡类型代号。子类实现。"""
        raise NotImplementedError(f"{self.brand} 未实现 parse_card_type")

    # ──────────────────────────────────────────────────────────────
    # 配置存取
    # ──────────────────────────────────────────────────────────────

    def configure(self, **kwargs) -> None:
        """品牌特定的初始化参数（如 `proUSB` 的 `dlsCoID`）。子类可覆盖。"""
        pass
