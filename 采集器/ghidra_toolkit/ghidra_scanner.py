"""
Ghidra 扫描器 — 启动 Ghidra 命令行分析 DLL，读回 JSON 结果
支持超时控制、进程清理、错误降级
"""

import os
import sys
import json
import time
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass

from .ghidra_finder import (
    get_ghidra_analyze_headless,
    get_ghidra_project_dir,
    get_portable_java_home,
)


@dataclass
class GhidraScanResult:
    """Ghidra 扫描结果包装器"""
    success: bool
    data: Dict[str, Any]
    error: Optional[str] = None
    elapsed_sec: float = 0.0


def _write_ghidra_script(script_path: Path) -> None:
    """将 auto_ghidra_scan.py 脚本写入临时目录"""
    from .scripts import auto_ghidra_scan
    script_path.write_text(auto_ghidra_scan.GHIDRA_SCRIPT_TEMPLATE, encoding='utf-8')


def run_ghidra_scan(
    dll_path: str,
    install_dir: str,
    timeout: int = 300,
) -> GhidraScanResult | None:
    """
    启动 Ghidra headless 分析指定 DLL。

    Args:
        dll_path: 目标 DLL 的完整路径
        install_dir: 原厂软件安装目录（用于线索追踪上下文）
        timeout: 最大等待秒数，默认 5 分钟

    Returns:
        GhidraScanResult 对象，或 None（Ghidra 未找到）
    """
    headless = get_ghidra_analyze_headless()
    if not headless:
        return GhidraScanResult(
            success=False,
            data={},
            error="Ghidra not found on USB drive",
        )

    proj_dir = get_ghidra_project_dir()
    java_home = get_portable_java_home()

    tmpdir = Path(tempfile.gettempdir()) / "solidcollector_ghidra"
    tmpdir.mkdir(exist_ok=True)

    script_file = tmpdir / "auto_ghidra_scan.py"
    output_json = tmpdir / f"ghidra_out_{int(time.time())}.json"

    _write_ghidra_script(script_file)

    proj_name = f"temp_{Path(dll_path).stem}"
    cmd = [
        str(headless),
        str(proj_dir),
        proj_name,
        "-import", dll_path,
        "-postScript", str(script_file), str(output_json),
        "-scriptPath", str(script_file.parent),
    ]

    env = os.environ.copy()
    if java_home:
        env["JAVA_HOME"] = str(java_home)
        env["PATH"] = f"{java_home / 'bin'};{env.get('PATH', '')}"

    start = time.time()
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(proj_dir),
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return GhidraScanResult(
                success=False,
                data={},
                error=f"Ghidra timeout after {timeout}s",
                elapsed_sec=time.time() - start,
            )

        elapsed = time.time() - start

        if output_json.exists():
            data = json.loads(output_json.read_text(encoding='utf-8'))
            data["_meta"] = {
                "elapsed_sec": round(elapsed, 2),
                "ghidra_stdout_snippet": stdout[-500:] if stdout else "",
                "ghidra_stderr_snippet": stderr[-500:] if stderr else "",
            }
            return GhidraScanResult(success=True, data=data, elapsed_sec=elapsed)
        else:
            return GhidraScanResult(
                success=False,
                data={},
                error="Ghidra finished but output JSON not found",
                elapsed_sec=elapsed,
            )

    except Exception as e:
        if proc and proc.poll() is None:
            proc.kill()
        return GhidraScanResult(
            success=False,
            data={},
            error=f"Ghidra exception: {str(e)}",
            elapsed_sec=time.time() - start,
        )
