"""
bridgecore/takeover_wizard.py — 接管向导

编排整个品牌接管流程：
  识别现场 → 加密评估 → 通道选择 → 采样学习 → 毕业验证 → 握手打包

六阶段渐进接管，每阶段自动决策，失败自动降级。

阶段：
  1. RECON  — 侦察（clue_hunter 找线索 + identity_engine 验证）
  2. ENCRYPT — 加密评估（encryption_fingerprints 检测加密体系）
  3. CHANNEL — 通道选择（path_prober 探测 dll/serial/parasitic）
  4. SAMPLE  — 采样学习（protocol_learner 差分分析 + brute_force）
  5. GRADUATE— 毕业验证（graduation_coach 八维评估 + 读回验证）
  6. PACKAGE — 握手打包（handover_packager 打包 .solidhandover）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class WizardPhase(Enum):
    RECON = auto()
    ENCRYPT = auto()
    CHANNEL = auto()
    SAMPLE = auto()
    GRADUATE = auto()
    PACKAGE = auto()


PHASE_NAMES = {
    WizardPhase.RECON: "侦察现场",
    WizardPhase.ENCRYPT: "加密评估",
    WizardPhase.CHANNEL: "通道选择",
    WizardPhase.SAMPLE: "采样学习",
    WizardPhase.GRADUATE: "毕业验证",
    WizardPhase.PACKAGE: "握手打包",
}


@dataclass
class PhaseResult:
    phase: WizardPhase
    ok: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    next_action: str = ""
    duration_ms: float = 0.0


@dataclass
class TakeoverPlan:
    """接管计划：根据侦察+加密评估动态决定走哪条路。"""
    strategy: str = ""               # dll_direct / serial / parasitic_replay / forensic
    channel_type: str = ""           # dll / serial / parasitic
    brand_guess: str = ""
    confidence: float = 0.0
    dll_path: str = ""
    dll_functions: Dict[str, str] = field(default_factory=dict)
    serial_port: str = ""
    serial_baudrate: int = 0
    cardlock_exe: str = ""
    button_map: Dict[str, str] = field(default_factory=dict)
    encrypted: bool = False
    encryption_type: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def is_viable(self) -> bool:
        return self.strategy != "forensic"


@dataclass
class TakeoverResult:
    """接管完整结果。"""
    ok: bool = False
    plan: TakeoverPlan = field(default_factory=TakeoverPlan)
    phases: List[PhaseResult] = field(default_factory=list)
    handover_path: str = ""
    profile: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def summary(self) -> str:
        if self.ok:
            return f"接管成功: {self.plan.brand_guess}, 策略={self.plan.strategy}, 握手包={self.handover_path}"
        return f"接管失败: {'; '.join(self.errors)}"


class TakeoverWizard:
    """六阶段接管向导。每阶段失败自动降级到备选方案。"""

    def __init__(self, install_dir: str):
        self._install_dir = install_dir
        self._phases: List[PhaseResult] = []
        self._plan = TakeoverPlan()
        self._callbacks: Dict[str, Callable] = {}

    def on_phase(self, phase: WizardPhase, callback: Callable[[PhaseResult], None]) -> None:
        """注册阶段回调。"""
        self._callbacks[phase.name] = callback

    def _notify(self, result: PhaseResult) -> None:
        cb = self._callbacks.get(result.phase.name)
        if cb:
            try:
                cb(result)
            except Exception:
                pass

    def run(self, samples_dir: str = "", output_dir: str = "") -> TakeoverResult:
        """执行完整接管流程。"""
        import os as _os
        t_start = time.monotonic()
        result = TakeoverResult()

        # ── Phase 1: RECON ────────────────────────────────────
        phase1 = self._phase_recon()
        self._phases.append(phase1)
        self._notify(phase1)
        if not phase1.ok:
            result.errors.extend(phase1.errors)
            if not self._plan.brand_guess:
                result.errors.append("侦察阶段失败，无法识别品牌")
                result.total_duration_ms = round((time.monotonic() - t_start) * 1000)
                return result
        self._plan.brand_guess = phase1.data.get("brand", "")
        self._plan.confidence = phase1.data.get("confidence", 0.0)
        self._plan.dll_path = phase1.data.get("dll_path", "")
        self._plan.dll_functions = phase1.data.get("dll_functions", {})

        # ── Phase 2: ENCRYPT ──────────────────────────────────
        phase2 = self._phase_encrypt()
        self._phases.append(phase2)
        self._notify(phase2)
        self._plan.encrypted = phase2.data.get("encrypted", False)
        self._plan.encryption_type = phase2.data.get("encryption_type", "")

        # ── Phase 3: CHANNEL ──────────────────────────────────
        phase3 = self._phase_channel()
        self._phases.append(phase3)
        self._notify(phase3)
        self._plan.strategy = phase3.data.get("strategy", "forensic")
        self._plan.channel_type = phase3.data.get("channel_type", "")
        self._plan.serial_port = phase3.data.get("serial_port", "")
        self._plan.serial_baudrate = phase3.data.get("serial_baudrate", 0)
        self._plan.cardlock_exe = phase3.data.get("cardlock_exe", "")
        self._plan.button_map = phase3.data.get("button_map", {})

        if self._plan.strategy == "forensic":
            result.errors.append("所有通道均失败，只能打包法医诊断包")
        else:
            self._plan.notes.append(f"选定通道: {self._plan.strategy}")

        # ── Phase 4: SAMPLE ──────────────────────────────────
        phase4 = self._phase_sample(samples_dir)
        self._phases.append(phase4)
        self._notify(phase4)

        # ── Phase 5: GRADUATE ──────────────────────────────────
        phase5 = self._phase_graduate(phase4.data)
        self._phases.append(phase5)
        self._notify(phase5)

        if phase5.data.get("can_graduate", False):
            self._plan.notes.append("毕业验证通过")

        # ── Phase 6: PACKAGE ──────────────────────────────────
        phase6 = self._phase_package(phase5.data, output_dir)
        self._phases.append(phase6)
        self._notify(phase6)

        if phase6.ok:
            result.handover_path = phase6.data.get("handover_path", "")
            result.profile = phase6.data.get("profile", {})
            result.ok = True
        else:
            result.errors.extend(phase6.errors)

        result.phases = self._phases
        result.plan = self._plan
        result.total_duration_ms = round((time.monotonic() - t_start) * 1000)
        return result

    def _phase_recon(self) -> PhaseResult:
        """Phase 1: 侦察现场。"""
        t0 = time.monotonic()
        r = PhaseResult(phase=WizardPhase.RECON)
        data: Dict[str, Any] = {}
        try:
            from .clue_hunter import hunt
            report = hunt(self._install_dir)
            data["brand"] = report.best_brand
            data["confidence"] = report.best_confidence
            data["channel_hint"] = report.best_channel
            data["clues"] = [{"source": c.source, "brand_hint": c.brand_hint,
                               "confidence": c.confidence, "evidence": c.evidence}
                              for c in report.clues]
            data["all_dlls"] = report.all_dlls
            data["all_exes"] = report.all_exes
            data["ini_fields"] = report.ini_fields
            # 提取主 DLL 路径和探测到的函数
            if report.all_dlls:
                data["dll_path"] = str(Path(self._install_dir) / report.all_dlls[0])
                data["dll_functions"] = {}
                for clue in report.clues:
                    if clue.source == "dll_exports" and clue.extra.get("matched"):
                        data["dll_functions"] = {fn: fn for fn in clue.extra["matched"]}
                        break
            r.ok = report.has_viable_clue
            r.data = data
            if not r.ok:
                r.errors.append("未发现可识别品牌线索")
                r.next_action = "请手动选择品牌或提供更多线索"
            else:
                r.next_action = "进入加密评估阶段"
        except Exception as e:
            r.errors.append(f"侦察异常: {e}")
            r.next_action = "检查安装目录是否完整"
        r.duration_ms = round((time.monotonic() - t0) * 1000)
        return r

    def _phase_encrypt(self) -> PhaseResult:
        """Phase 2: 加密评估。"""
        t0 = time.monotonic()
        r = PhaseResult(phase=WizardPhase.ENCRYPT)
        try:
            from .encryption_fingerprints import EncryptionAnalyzer
            analyzer = EncryptionAnalyzer()
            report = analyzer.analyze(
                install_dir=self._install_dir,
                dll_list=self._plan.dll_path and [self._plan.dll_path] or None,
            )
            r.ok = True
            r.data = {
                "encrypted": report.overall_encrypted,
                "encryption_type": report.fingerprints[0].encryption_type if report.fingerprints else "",
                "strategy": report.overall_strategy,
                "confidence": report.overall_confidence,
                "summary": report.summary,
                "warnings": [],
            }
            for fp in report.fingerprints:
                r.data["warnings"].extend(fp.risk_warnings)
            if report.overall_encrypted:
                r.warnings.append(f"检测到加密: {report.overall_strategy}")
                r.next_action = "加密卡需走 DLL 代理或寄生回放"
            else:
                r.next_action = "差分学习可行"
        except Exception as e:
            r.errors.append(f"加密评估异常: {e}")
        r.duration_ms = round((time.monotonic() - t0) * 1000)
        return r

    def _phase_channel(self) -> PhaseResult:
        """Phase 3: 通道选择。"""
        t0 = time.monotonic()
        r = PhaseResult(phase=WizardPhase.CHANNEL)
        data: Dict[str, Any] = {}

        try:
            from .path_prober import PathProber
            prober = PathProber()
            probe_result = prober.probe(
                self._install_dir,
                {"channel": self._plan.channel_type or "dll"},
            )
            data["strategy"] = probe_result.get("mode", "forensic")
            data["detail"] = probe_result.get("detail", {})
            r.ok = data["strategy"] != "forensic"
        except Exception:
            pass

        # 降级逻辑
        if not r.ok or data.get("strategy") == "forensic":
            # DLL 直调失败 → 尝试串口
            try:
                from .serial_channel import SerialScanner
                scanner = SerialScanner()
                ports = scanner.scan()
                if ports:
                    # 尝试每个串口做简单探测
                    for port_info in ports[:3]:
                        try:
                            from .serial_channel import SerialChannel
                            ch = SerialChannel(port_info.port, port_info.baudrate)
                            ch.open()
                            # 发一条简单指令试探
                            ch.send(bytes.fromhex("AA5500FF00FF0100"))
                            time.sleep(0.3)
                            resp = ch.recv(32)
                            ch.close()
                            if resp:
                                data["strategy"] = "serial"
                                data["serial_port"] = port_info.port
                                data["serial_baudrate"] = port_info.baudrate
                                data["channel_type"] = "serial"
                                r.ok = True
                                break
                            ch.close()
                        except Exception:
                            continue
            except Exception:
                pass

        if not r.ok or data.get("strategy") == "forensic":
            # 串口也失败 → 尝试寄生回放
            cardlock_exe = self._find_cardlock_exe()
            if cardlock_exe:
                data["strategy"] = "parasitic_replay"
                data["cardlock_exe"] = cardlock_exe
                data["channel_type"] = "parasitic"
                data["button_map"] = self._detect_buttons(cardlock_exe)
                r.ok = True

        if not r.ok:
            data["strategy"] = "forensic"
            data["channel_type"] = ""
            r.errors.append("所有通道探测失败:dll/serial/parasitic均不可用")
            r.next_action = "打包法医诊断包,人工处理"

        r.data = data
        r.duration_ms = round((time.monotonic() - t0) * 1000)
        return r

    def _phase_sample(self, samples_dir: str) -> PhaseResult:
        """Phase 4: 采样学习。"""
        t0 = time.monotonic()
        r = PhaseResult(phase=WizardPhase.SAMPLE)
        r.data = {"samples": [], "learn_result": None}

        # 如果是寄生回放模式，先尝试自动录制+回放采样
        if self._plan.strategy == "parasitic_replay" and self._plan.cardlock_exe:
            try:
                from .parasitic_replay import record_and_replay
                replay_result = record_and_replay(
                    self._plan.cardlock_exe,
                    card_type="guest",
                    button_map=self._plan.button_map,
                    readback=True,
                )
                if replay_result.ok and replay_result.card_hex:
                    r.data["samples"] = [{"hex": replay_result.card_hex, "type": "guest",
                                          "source": "parasitic_replay"}]
                    r.data["parasitic_result"] = vars(replay_result)
            except Exception as e:
                r.data["parasitic_error"] = str(e)

        # 如果有样本目录，尝试自动学习
        if samples_dir:
            try:
                import glob, json
                samples = []
                for f in glob.glob(samples_dir + "/*.json"):
                    with open(f, 'r') as fp:
                        samples.append(json.load(fp))
                if samples:
                    from .protocol_learner import ProtocolLearner
                    learner = ProtocolLearner()
                    learn_result = learner.learn_from_payloads(samples)
                    r.data["samples"] = samples
                    r.data["learn_result"] = {
                        "has_valid_result": learn_result.has_valid_result,
                        "checksum_algorithm": learn_result.checksum_algorithm,
                        "payload_size": learn_result.payload_size,
                        "confidence": learn_result.confidence,
                    }
                    r.ok = learn_result.has_valid_result
                else:
                    r.ok = False
                    r.errors.append("无样本数据")
            except Exception as e:
                r.errors.append(f"采样学习异常: {e}")
        else:
            r.ok = False
            r.errors.append("需要操作人完成读卡采样")
            r.next_action = "请按 9 步教练完成读空白卡、原厂写卡、读已写卡"
        r.duration_ms = round((time.monotonic() - t0) * 1000)
        return r

    def _phase_graduate(self, sample_data: Dict[str, Any]) -> PhaseResult:
        """Phase 5: 毕业验证。"""
        t0 = time.monotonic()
        r = PhaseResult(phase=WizardPhase.GRADUATE)
        try:
            from .graduation_coach import evaluate
            learn = sample_data.get("learn_result", {})
            state = evaluate(
                identity=None,
                samples=sample_data.get("samples", []),
                analyze_result={
                    "success": learn.get("has_valid_result", False),
                    "confidence": learn.get("confidence", 0.0),
                    "checksum_algorithm": learn.get("checksum_algorithm", ""),
                },
                probe_result={
                    "mode": self._plan.strategy,
                    "detail": {"encrypted": self._plan.encrypted,
                               "encryption_type": self._plan.encryption_type},
                },
            )
            r.data = {
                "can_graduate": state.can_graduate,
                "passed_count": state.passed_count,
                "required_count": state.required_count,
                "blockers": state.blockers,
                "next_action": state.next_action,
            }
            r.ok = state.can_graduate
            if not state.can_graduate:
                r.errors.extend(state.blockers)
                r.next_action = state.next_action
        except Exception as e:
            r.errors.append(f"毕业验证异常: {e}")
        r.duration_ms = round((time.monotonic() - t0) * 1000)
        return r

    def _phase_package(self, graduate_data: Dict[str, Any], output_dir: str) -> PhaseResult:
        """Phase 6: 握手打包。"""
        t0 = time.monotonic()
        r = PhaseResult(phase=WizardPhase.PACKAGE)
        try:
            from .handover_packager import HandoverPackager
            packager = HandoverPackager()
            handover_path = packager.pack(
                brand=self._plan.brand_guess,
                mode=self._plan.strategy,
                profile=graduate_data.get("profile", {}),
                output_dir=output_dir,
            )
            r.data = {"handover_path": handover_path, "profile": graduate_data.get("profile", {})}
            r.ok = bool(handover_path)
            if not r.ok:
                r.errors.append("打包失败")
        except Exception as e:
            r.errors.append(f"打包异常: {e}")
        r.duration_ms = round((time.monotonic() - t0) * 1000)
        return r

    # ── 工具方法 ──────────────────────────────────────────────

    def _find_cardlock_exe(self) -> str:
        """在安装目录下递归找 CardLock.exe。"""
        known_names = ("cardlock.exe", "cardlock", "LockCard.exe",
                       "prousb.exe", "hotellock.exe", "doorlock.exe",
                       "hotelcard.exe", "cardlockv9.exe")
        for root, dirs, files in os.walk(self._install_dir):
            for f in files:
                if f.lower() in known_names:
                    return os.path.join(root, f)
        return ""

    def _detect_buttons(self, cardlock_exe: str) -> Dict[str, str]:
        """自动检测 CardLock.exe 卡型按钮。"""
        try:
            from .parasitic_replay import ParasiticRecorder
            recorder = ParasiticRecorder(cardlock_exe)
            if recorder.start_app(timeout=15.0):
                bm = recorder.detect_button_map()
                recorder.close_app()
                return bm
        except Exception:
            pass
        return {}


# ──────────────────────────────────────────────────────────────────
# 便捷函数
# ──────────────────────────────────────────────────────────────────


def run_takeover(install_dir: str, output_dir: str = "") -> TakeoverResult:
    """一键接管：自动识别品牌并生成握手包。"""
    wizard = TakeoverWizard(install_dir)
    return wizard.run(output_dir=output_dir)


def quick_takeover_plan(install_dir: str) -> Dict[str, Any]:
    """快速获取接管计划（只跑前 3 阶段，不实际采样）。"""
    wizard = TakeoverWizard(install_dir)
    wizard._phase_recon()
    wizard._phase_encrypt()
    phase3 = wizard._phase_channel()
    return {
        "brand": wizard._plan.brand_guess,
        "confidence": wizard._plan.confidence,
        "strategy": wizard._plan.strategy,
        "encrypted": wizard._plan.encrypted,
        "encryption_type": wizard._plan.encryption_type,
        "viable": wizard._plan.is_viable,
        "notes": wizard._plan.notes,
        "errors": phase3.errors,
    }
