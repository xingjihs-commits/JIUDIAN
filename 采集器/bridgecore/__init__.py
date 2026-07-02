"""
bridgecore/__init__.py — Collector 版统一入口

新模块在首次访问时通过 __getattr__ 惰性导入。
"""

__all__ = [
    # ── 核心引擎 ──
    "OPERATOR_REGISTRY", "compute_checksum",
    "ProtocolLearner", "ProtocolLearnResult",
    "ChannelInfo", "ProbeResult",
    "generate_profile", "save_profile",
    # ── 品牌分析 ──
    "BrandAnalyzer", "probe_unknown_brand",
    "from_diag_zip", "install_profile", "batch_install", "list_available_profiles",
    # ── 握手打包 ──
    "SUPPORTED_HANDOVER_VERSIONS", "build_manifest", "validate_manifest", "file_sha256",
    "HandoverPackager",
    # ── 通道探测 ──
    "PathProber", "probe_path",
    # ── 毕业教练 ──
    "evaluate_graduation", "build_graduation_report", "GraduationState", "GraduationItem",
    # ── PMS 迁移 ──
    "BridgeCoreSettings", "get_settings", "reload_settings",
    "Observer", "RecordingSession", "load_recording", "list_sessions",
    "FaultManager", "FaultTriggered", "FaultKind",
    "Injector", "ReplaySummary", "ReplayStatus",
    "RxMonitor", "BridgeCoreOrchestrator",
    "ProtocolProcessor", "ProtocolValidationError",
    # ── 物理通道 ──
    "PhysicalChannel", "ChannelInfoPC", "ChannelDetector", "ProbeResultPC",
    "DllChannel", "SerialChannel", "UsbHidChannel",
    # ── DLL 探测 ──
    "probe_dll", "probe_dll_by_path",
    # ── 恐慌恢复 ──
    "PanicRecovery", "RecoveryRecord", "RecoverySummary",
    "PowerController", "WinUsbPowerController", "SerialRelayPowerController",
    # ── Phase 1: 核心接管模块 ──
    "ClueHunter", "ClueReport", "ClueItem", "hunt",
    "EncryptionAnalyzer", "EncryptionFingerprint", "EncryptionReport", "quick_check",
    "ParasiticRecorder", "ParasiticReplayer", "ParasiticWorkflow",
    "TakeoverWizard", "TakeoverResult", "TakeoverPlan", "run_takeover",
    # ── Phase 2: 黑客能力 ──
    "DllSandbox", "SandboxReport", "SandboxCallResult",
    "ProtocolBruteForce", "BruteForceReport", "FieldHypothesis", "brute_learn",
    "CardTypeFuzzer", "FuzzReport", "FuzzResult", "fuzz_card_types",
    "ChecksumBruteForce", "ChecksumReport", "brute_checksum",
    "WeakKeyBruteForcer",
    # ── P1-P2 辅助模块 ──
    "KeepAlive", "TaskFetcher", "TokenRecorder",
    "OemExeInfo", "OemProcess", "find_oem_exes", "find_running_oem_processes",
    "RoomExporter", "export_room_data",
]

# 惰性导入映射
_LAZY_MAP = {
    # Phase 1
    "ClueHunter": "clue_hunter", "ClueReport": "clue_hunter", "ClueItem": "clue_hunter", "hunt": "clue_hunter",
    "EncryptionAnalyzer": "encryption_fingerprints", "EncryptionFingerprint": "encryption_fingerprints",
    "EncryptionReport": "encryption_fingerprints", "quick_check": "encryption_fingerprints",
    "ParasiticRecorder": "parasitic_replay", "ParasiticReplayer": "parasitic_replay",
    "ParasiticWorkflow": "parasitic_replay",
    "TakeoverWizard": "takeover_wizard", "TakeoverResult": "takeover_wizard",
    "TakeoverPlan": "takeover_wizard", "run_takeover": "takeover_wizard",
    # Phase 2
    "DllSandbox": "dll_sandbox", "SandboxReport": "dll_sandbox", "SandboxCallResult": "dll_sandbox",
    "ProtocolBruteForce": "protocol_bruteforce", "BruteForceReport": "protocol_bruteforce",
    "FieldHypothesis": "protocol_bruteforce", "brute_learn": "protocol_bruteforce",
    "CardTypeFuzzer": "card_type_fuzzer", "FuzzReport": "card_type_fuzzer",
    "FuzzResult": "card_type_fuzzer", "fuzz_card_types": "card_type_fuzzer",
    "ChecksumBruteForce": "checksum_bruteforce", "ChecksumReport": "checksum_bruteforce",
    "brute_checksum": "checksum_bruteforce",
    "WeakKeyBruteForcer": "mifare_weak_keys",
    # P1-P2
    "KeepAlive": "keepalive", "TaskFetcher": "task_fetcher", "TokenRecorder": "token_recorder",
    "OemExeInfo": "oem_process", "OemProcess": "oem_process",
    "find_oem_exes": "oem_process", "find_running_oem_processes": "oem_process",
    "export_room_data": "room_exporter",
}

# 非惰性：这些是已有子模块中直接定义的导出
_ALREADY_IMPORTED = {
    # 核心引擎 (从 operator_lib, protocol_learner, physical_channel 等)
    "OPERATOR_REGISTRY", "compute_checksum",
    "ProtocolLearner", "ProtocolLearnResult",
    "ChannelInfo", "ProbeResult",
    "generate_profile", "save_profile",
    # 品牌分析
    "BrandAnalyzer", "probe_unknown_brand",
    "from_diag_zip", "install_profile", "batch_install", "list_available_profiles",
    # 握手打包
    "SUPPORTED_HANDOVER_VERSIONS", "build_manifest", "validate_manifest", "file_sha256",
    "HandoverPackager",
    # 通道探测
    "PathProber", "probe_path",
    # 毕业教练
    "evaluate_graduation", "build_graduation_report", "GraduationState", "GraduationItem",
    # PMS 迁移
    "BridgeCoreSettings", "get_settings", "reload_settings",
    "Observer", "RecordingSession", "load_recording", "list_sessions",
    "FaultManager", "FaultTriggered", "FaultKind",
    "Injector", "ReplaySummary", "ReplayStatus",
    "RxMonitor", "BridgeCoreOrchestrator",
    "ProtocolProcessor", "ProtocolValidationError",
    # 物理通道
    "PhysicalChannel", "ChannelInfoPC", "ChannelDetector", "ProbeResultPC",
    "DllChannel", "SerialChannel", "UsbHidChannel",
    # DLL 探测
    "probe_dll", "probe_dll_by_path",
    # 恐慌恢复
    "PanicRecovery", "RecoveryRecord", "RecoverySummary",
    "PowerController", "WinUsbPowerController", "SerialRelayPowerController",
}

import importlib as _il

def __getattr__(name: str):
    if name in _LAZY_MAP:
        mod = _il.import_module(f".{_LAZY_MAP[name]}", __package__)
        return getattr(mod, name)
    if name in _ALREADY_IMPORTED:
        # 这些在 PMS bridgecore 中的已有子模块中，delayed import
        raise AttributeError(
            f"bridgecore.{name} 请直接 import 对应子模块，"
            f"或: from bridgecore.xxx import {name}"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
