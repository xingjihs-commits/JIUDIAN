"""
法医诊断打包器 — 什么都走不通时打包现场所有资料
生成 forensic_diagnosis_<日期>_<酒店>.zip + ANALYSIS_REPORT.md
"""

import os
import json
import zipfile
import time
from pathlib import Path
from typing import Dict, Any, List, Optional


def _get_collector_root() -> Path:
    import sys
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _generate_report(context: Dict[str, Any]) -> str:
    """生成 ANALYSIS_REPORT.md 内容"""
    lines = [
        "# SolidCollector 法医诊断报告",
        "",
        f"**生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**酒店**: {context.get('hotel_name', 'unknown')}",
        f"**失败原因**: {context.get('failure_reason', 'unknown')}",
        "",
        "## 1. 已尝试的方法",
    ]

    tried = context.get("tried_methods", [])
    for i, method in enumerate(tried, 1):
        lines.append(f"{i}. {method}")
    if not tried:
        lines.append("（无记录）")

    lines.extend(["", "## 2. 卡在哪一步", f"{context.get('stuck_at', 'unknown')}", "", "## 3. Ghidra 分析结果"])

    ghidra = context.get("ghidra_result")
    if ghidra:
        lines.append(f"- 总函数数: {ghidra.get('total_functions', 0)}")
        lines.append(f"- 发现密钥: {len(ghidra.get('keys', []))} 条")
        lines.append(f"- 发现交叉引用: {len(ghidra.get('xrefs', []))} 条")
        lines.append(f"- 品牌线索: {len(ghidra.get('strings_hint', []))} 条")
    else:
        lines.append("- Ghidra 未运行或超时")

    lines.extend(["", "## 4. 加密检测结果", f"{context.get('encryption_match', '未检测')}", "", "## 5. 发现的线索"])

    clues = context.get("clues", [])
    for clue in clues:
        lines.append(f"- {clue}")
    if not clues:
        lines.append("（无线索）")

    lines.extend(["", "## 6. 文件清单"])
    files = context.get("file_list", [])
    for f in files:
        lines.append(f"- `{f}`")

    lines.extend(["", "## 7. 下一步建议",
        "1. **增加样本**: 收集更多空白卡/已写卡对比，提升差分学习精度",
        "2. **手动逆向**: 用 Ghidra GUI 模式打开 DLL，人工分析关键函数",
        "3. **联系支持**: 将本诊断包发送给 Solid PMS 技术团队，获取定制 Profile",
        "", "---", "*本报告由 SolidCollector 自动生成*"])

    return "\n".join(lines)


def package_forensic(
    install_dir: str,
    context: Dict[str, Any],
    output_dir: Optional[str] = None,
) -> Path:
    """打包现场所有资料"""
    root = _get_collector_root()
    out_dir = Path(output_dir) if output_dir else root
    out_dir.mkdir(parents=True, exist_ok=True)

    hotel = context.get("hotel_name", "unknown").replace(" ", "_")
    date_str = time.strftime("%Y%m%d_%H%M%S")
    zip_name = f"forensic_diagnosis_{date_str}_{hotel}.zip"
    zip_path = out_dir / zip_name

    collected_files = []
    install_path = Path(install_dir)
    if install_path.exists():
        for dll in install_path.rglob("*.dll"):
            collected_files.append(dll)
        for exe in install_path.rglob("*.exe"):
            if exe.stat().st_size < 50 * 1024 * 1024:
                collected_files.append(exe)
        for ext in ["*.ini", "*.cfg", "*.dat", "*.xml", "*.json"]:
            for f in install_path.rglob(ext):
                collected_files.append(f)

    collected_files = list(dict.fromkeys(collected_files))
    total_size = sum(f.stat().st_size for f in collected_files)
    max_size = 100 * 1024 * 1024
    if total_size > max_size:
        collected_files.sort(key=lambda f: f.stat().st_size)
        running = 0
        filtered = []
        for f in collected_files:
            if running + f.stat().st_size <= max_size:
                filtered.append(f)
                running += f.stat().st_size
        collected_files = filtered

    context["file_list"] = [str(f.relative_to(install_path)) for f in collected_files]
    report_md = _generate_report(context)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ANALYSIS_REPORT.md", report_md)
        logs = context.get("process_logs", "")
        zf.writestr("process_logs.txt", logs)
        registry = context.get("registry_snapshot", "")
        if registry:
            zf.writestr("registry_snapshot.reg", registry)
        sig_file = root / "known_signatures.json"
        if sig_file.exists():
            zf.write(sig_file, "known_signatures.json")
        for f in collected_files:
            arcname = f"files/{f.relative_to(install_path)}"
            try:
                zf.write(f, arcname)
            except Exception:
                pass

    return zip_path
