"""
bridgecore/analysis_types.py — 纯数据类，无外部依赖
"""

from dataclasses import dataclass, field


@dataclass
class ChannelInfo:
    channel_type: str = "dll"  # "dll" | "serial"
    device_name: str = "发卡器"
    vid: str = ""
    pid: str = ""
    dll_path: str = ""
    port: str = ""        # serial: COM口
    baudrate: int = 0     # serial: 波特率


@dataclass
class ProbeResult:
    dll_name: str = ""
    dll_path: str = ""
    is_32bit: bool = False
    exports: list = field(default_factory=list)
    classified: dict = field(default_factory=dict)
    hardcoded_match: dict = field(default_factory=dict)
    brand_guess: str = "auto_learned"
    confidence: float = 0.0
    can_issue: bool = False

    def has_function(self, group: str) -> bool:
        return group in self.classified or group in self.hardcoded_match

    def get_function(self, group: str) -> str:
        return self.classified.get(group) or self.hardcoded_match.get(group) or ""
