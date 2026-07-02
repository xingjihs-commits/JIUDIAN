"""
forensic_schema.py — 法医级 learned_config.json v2.0 数据模型

定义 Collector 采集完成后输出的完整数据结构。
PMS 导入时按此 schema 解析入库。
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


# ── 顶层结构 ──────────────────────────────────────────────

@dataclass
class ForensicConfig:
    """法医级采集报告，Collector 的最终输出。"""
    version: str = "2.0"
    generated_by: str = "SolidCollector"
    generated_at: str = ""

    meta: ForensicMeta = field(default_factory=lambda: ForensicMeta())
    identity: IdentityInfo = field(default_factory=lambda: IdentityInfo())
    filesystem: FileSystemReport = field(default_factory=lambda: FileSystemReport())
    process_tree: ProcessTreeReport = field(default_factory=lambda: ProcessTreeReport())
    ui_map: UIMapReport = field(default_factory=lambda: UIMapReport())
    workflow: WorkflowReport = field(default_factory=lambda: WorkflowReport())
    card_protocol: CardProtocolReport = field(default_factory=lambda: CardProtocolReport())
    registry_changes: RegistryChangeReport = field(default_factory=lambda: RegistryChangeReport())
    file_changes: FileChangeReport = field(default_factory=lambda: FileChangeReport())
    verification: VerificationReport = field(default_factory=lambda: VerificationReport())

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> str:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.generated_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data = self.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        return str(path)

    @classmethod
    def load(cls, path: str | Path) -> "ForensicConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "ForensicConfig":
        cfg = cls()
        cfg.version = data.get("version", "2.0")
        cfg.generated_by = data.get("generated_by", "SolidCollector")
        cfg.generated_at = data.get("generated_at", "")
        cfg.meta = ForensicMeta(**data.get("meta", {}))
        cfg.identity = IdentityInfo(**data.get("identity", {}))
        cfg.filesystem = FileSystemReport(**data.get("filesystem", {}))
        cfg.process_tree = _deserialize_process_tree(data.get("process_tree", {}))
        cfg.ui_map = UIMapReport(**data.get("ui_map", {}))
        cfg.workflow = WorkflowReport.from_dict(data.get("workflow", {}))
        cfg.card_protocol = CardProtocolReport.from_dict(data.get("card_protocol", {}))
        cfg.registry_changes = _deserialize_registry_changes(data.get("registry_changes", {}))
        cfg.file_changes = _deserialize_file_changes(data.get("file_changes", {}))
        cfg.verification = VerificationReport(**data.get("verification", {}))
        return cfg


# ── 序列化辅助函数 ────────────────────────────────────────

def _deserialize_process_tree(data: dict) -> ProcessTreeReport:
    r = ProcessTreeReport(**{k: v for k, v in data.items() if k not in (
        "before_issue", "during_issue", "after_issue",
        "guardian_processes", "new_processes",
    )})
    for attr in ("before_issue", "during_issue", "after_issue",
                 "guardian_processes", "new_processes"):
        items = data.get(attr, [])
        if items:
            setattr(r, attr, [ProcessSnapshot(**i) for i in items])
    return r


def _deserialize_registry_changes(data: dict) -> RegistryChangeReport:
    r = RegistryChangeReport(**{k: v for k, v in data.items() if k != "changes"})
    items = data.get("changes", [])
    if items:
        r.changes = [RegChange(**i) for i in items]
    return r


def _deserialize_file_changes(data: dict) -> FileChangeReport:
    r = FileChangeReport(**{k: v for k, v in data.items() if k != "changes"})
    items = data.get("changes", [])
    if items:
        r.changes = [FileChangeEntry(**i) for i in items]
    return r


# ── Meta ──────────────────────────────────────────────────

@dataclass
class ForensicMeta:
    brand_guess: str = ""
    confidence: float = 0.0
    collection_duration_sec: int = 0
    os_version: str = ""
    hostname: str = ""


# ── Identity ──────────────────────────────────────────────

@dataclass
class IdentityInfo:
    dls_co_id: str = ""
    hotel_id: str = ""
    pc_id: str = ""
    port: str = ""
    source: str = ""           # 来源文件，如 "System.ini"


# ── FileSystem ────────────────────────────────────────────

@dataclass
class FileInfo:
    path: str = ""              # 相对安装目录
    size: int = 0
    md5: str = ""
    mod_time: str = ""

@dataclass
class SystemIniContent:
    dls_co_id: str = ""
    hotel_id: str = ""
    pc_id: str = ""
    port: str = ""
    raw_sections: dict = field(default_factory=dict)

@dataclass
class MdbSummary:
    source: str = ""
    tables: list[str] = field(default_factory=list)
    room_count: int = 0
    guest_count: int = 0

@dataclass
class DllExportInfo:
    dll_name: str = ""
    arch: str = ""              # "32bit" / "64bit"
    exports: list[dict] = field(default_factory=list)  # [{name, ordinal, address}]

@dataclass
class FileSystemReport:
    install_dir: str = ""
    files: list[FileInfo] = field(default_factory=list)
    file_count: int = 0
    total_size_mb: float = 0.0
    system_ini: Optional[SystemIniContent] = None
    mdb_summary: Optional[MdbSummary] = None
    dll_exports: list[DllExportInfo] = field(default_factory=list)


# ── ProcessTree ───────────────────────────────────────────

@dataclass
class ProcessSnapshot:
    pid: int = 0
    name: str = ""
    exe_path: str = ""
    cmdline: str = ""
    parent_pid: int = 0
    loaded_dlls: list[str] = field(default_factory=list)

@dataclass
class ProcessTreeReport:
    before_issue: list[ProcessSnapshot] = field(default_factory=list)
    during_issue: list[ProcessSnapshot] = field(default_factory=list)
    after_issue: list[ProcessSnapshot] = field(default_factory=list)
    guardian_processes: list[ProcessSnapshot] = field(default_factory=list)
    new_processes: list[ProcessSnapshot] = field(default_factory=list)


# ── UIMap ────────────────────────────────────────────────

@dataclass
class UIElement:
    control_type: str = ""      # "Button", "Edit", "ComboBox", ...
    text: str = ""
    automation_id: str = ""
    class_name: str = ""
    rect: dict = field(default_factory=dict)  # {left, top, right, bottom}
    visible: bool = True
    enabled: bool = True
    children: list['UIElement'] = field(default_factory=list)

@dataclass
class DialogStructure:
    title: str = ""
    elements: list[UIElement] = field(default_factory=list)

@dataclass
class UIMapReport:
    main_window_title: str = ""
    card_type_buttons: dict[str, str] = field(default_factory=dict)  # {card_type: button_text}
    dialog_structures: dict[str, DialogStructure] = field(default_factory=dict)
    control_tree_json: str = ""  # 完整控件树的 JSON 序列化


# ── Workflow ──────────────────────────────────────────────

@dataclass
class WorkflowStep:
    index: int = 0
    action: str = ""            # "click", "type", "select", "wait", "check"
    target: str = ""            # 按钮名 / 输入框名
    value: str = ""             # 键入的值 / 选项值
    wait_sec: float = 0.0       # 操作后等待秒数
    description: str = ""       # 中文描述

@dataclass
class CardIssueWorkflow:
    card_type: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)
    duration_sec: float = 0.0
    result: str = ""            # "ok" / "fail" / "unknown"

@dataclass
class WorkflowReport:
    guest_card: Optional[CardIssueWorkflow] = None
    master_card: Optional[CardIssueWorkflow] = None
    other_cards: list[CardIssueWorkflow] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowReport":
        r = cls()
        if data.get("guest_card"):
            r.guest_card = CardIssueWorkflow(
                **{k: v for k, v in data["guest_card"].items() if k != "steps"}
            )
            steps = data["guest_card"].get("steps", [])
            r.guest_card.steps = [WorkflowStep(**s) for s in steps]
        if data.get("master_card"):
            r.master_card = CardIssueWorkflow(
                **{k: v for k, v in data["master_card"].items() if k != "steps"}
            )
            steps = data["master_card"].get("steps", [])
            r.master_card.steps = [WorkflowStep(**s) for s in steps]
        for oc in data.get("other_cards", []):
            wf = CardIssueWorkflow(
                **{k: v for k, v in oc.items() if k != "steps"}
            )
            steps = oc.get("steps", [])
            wf.steps = [WorkflowStep(**s) for s in steps]
            r.other_cards.append(wf)
        return r


# ── CardProtocol ──────────────────────────────────────────

@dataclass
class ChecksumInfo:
    algorithm: str = ""
    offset: int = 0
    length: int = 0

@dataclass
class CardTypeInfo:
    type_byte_high: int = 0
    body_len: int = 4

@dataclass
class ApduTrace:
    direction: str = ""         # "send" / "recv"
    raw_hex: str = ""
    timestamp: str = ""

@dataclass
class CardProtocolReport:
    payload_size: int = 16
    checksum: ChecksumInfo = field(default_factory=ChecksumInfo)
    layout: dict = field(default_factory=dict)
    card_types: dict[str, CardTypeInfo] = field(default_factory=dict)
    magic_hex: str = ""
    site_mask_hex: str = ""
    confidence: float = 0.0
    apdu_trace: list[ApduTrace] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "CardProtocolReport":
        r = cls()
        r.payload_size = data.get("payload_size", 16)
        r.checksum = ChecksumInfo(**data.get("checksum", {}))
        r.layout = data.get("layout", {})
        ct = {}
        for k, v in data.get("card_types", {}).items():
            ct[k] = CardTypeInfo(**v)
        r.card_types = ct
        r.magic_hex = data.get("magic_hex", "")
        r.site_mask_hex = data.get("site_mask_hex", "")
        r.confidence = data.get("confidence", 0.0)
        for at in data.get("apdu_trace", []):
            r.apdu_trace.append(ApduTrace(**at))
        return r


# ── RegistryChange ───────────────────────────────────────

@dataclass
class RegChange:
    key: str = ""
    value_name: str = ""
    old_value: str = ""
    new_value: str = ""
    change_type: str = ""       # "added" / "modified" / "deleted"

@dataclass
class RegistryChangeReport:
    before_snapshot: dict = field(default_factory=dict)
    after_snapshot: dict = field(default_factory=dict)
    changes: list[RegChange] = field(default_factory=list)


# ── FileChange ───────────────────────────────────────────

@dataclass
class FileChangeEntry:
    path: str = ""
    change_type: str = ""       # "added" / "modified" / "deleted"
    before_size: int = 0
    after_size: int = 0
    before_md5: str = ""
    after_md5: str = ""

@dataclass
class FileChangeReport:
    before_snapshot: list[str] = field(default_factory=list)  # 文件路径列表
    after_snapshot: list[str] = field(default_factory=list)
    changes: list[FileChangeEntry] = field(default_factory=list)


# ── Verification ──────────────────────────────────────────

@dataclass
class VerificationReport:
    """门锁刷卡验证报告，分析完成后可选执行。"""
    performed: bool = False
    result: str = ""              # "passed" / "failed" / "skipped"
    verified_at: str = ""
    card_type: str = ""
    card_hex: str = ""
    notes: str = ""
