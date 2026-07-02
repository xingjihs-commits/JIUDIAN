"""
Ghidra 自动扫描脚本 — 被 Ghidra analyzeHeadless 在分析完成后调用
提取：密钥、品牌线索、文件名线索、函数交叉引用、导出表
"""

GHIDRA_SCRIPT_TEMPLATE = r"""
# Ghidra Post-Script: auto_ghidra_scan.py
# 由 SolidCollector 自动生成并注入 Ghidra headless 流程

import json
import re
import sys
from ghidra.program.model.symbol import SymbolType
from ghidra.program.model.listing import CodeUnit
from ghidra.util.task import ConsoleTaskMonitor

output_path = sys.argv[1] if len(sys.argv) > 1 else None

HEX_KEY_PATTERNS = [
    ("S50_key_6byte",  r"\b[A-Fa-f0-9]{12}\b"),
    ("S50_key_8byte",  r"\b[A-Fa-f0-9]{16}\b"),
    ("key_12byte",     r"\b[A-Fa-f0-9]{24}\b"),
    ("key_16byte",     r"\b[A-Fa-f0-9]{32}\b"),
    ("key_32byte",     r"\b[A-Fa-f0-9]{64}\b"),
    ("key_64byte",     r"\b[A-Fa-f0-9]{128}\b"),
]

BRAND_KEYWORDS = [
    "Walton", "CardLock", "爱迪尔", "必达", "力维",
    "西容", "雅迪顿", "同创新佳", "宝迅达",
    "proUSB", "V9", "V10", "V11", "RFL",
    "RFLOCK", "DIGILOCK", "ONITY", "SALTO",
]

FILE_PATTERNS = [
    r"\b\w+\.mdb\b", r"\b\w+\.ini\b", r"\b\w+\.dll\b",
    r"\b\w+\.exe\b", r"\b\w+\.dat\b", r"\b\w+\.cfg\b",
]

TARGET_FUNCS = [
    "SectorLogin", "ReadCard", "WriteCard", "GuestCard",
    "init", "Initialize", "OpenUSB", "CloseUSB",
    "Auth", "Authenticate", "CheckIn", "CheckOut",
    "MakeCard", "EraseCard", "CopyCard",
]

result = {
    "dll_path": str(currentProgram.getExecutablePath()),
    "total_functions": 0,
    "exports": [],
    "keys": [],
    "xrefs": [],
    "strings_hint": [],
    "file_clues": [],
}

string_table = currentProgram.getListing().getDataIterator(
    currentProgram.getMinAddress(), True
)

seen_strings = set()
for s in currentProgram.getListing().getDefinedData(currentProgram.getMinAddress(), True):
    try:
        val = s.getValue()
        if val is None:
            continue
        text = str(val)
        if text in seen_strings:
            continue
        seen_strings.add(text)
        addr = s.getAddress().toString()

        for key_type, pattern in HEX_KEY_PATTERNS:
            for m in re.finditer(pattern, text):
                hex_val = m.group(0).upper()
                if hex_val not in ("0" * len(hex_val), "F" * len(hex_val)):
                    result["keys"].append({
                        "type": key_type,
                        "address": addr,
                        "value": hex_val,
                        "context": text[:80],
                    })

        for brand in BRAND_KEYWORDS:
            if brand.lower() in text.lower():
                result["strings_hint"].append({
                    "keyword": brand,
                    "address": addr,
                    "context": text[:120],
                })

        for fp in FILE_PATTERNS:
            for m in re.finditer(fp, text, re.IGNORECASE):
                clue = m.group(0)
                if clue not in result["file_clues"]:
                    result["file_clues"].append(clue)
    except:
        pass

func_manager = currentProgram.getFunctionManager()
result["total_functions"] = func_manager.getFunctionCount()

for func in func_manager.getFunctions(True):
    func_name = func.getName()
    for target in TARGET_FUNCS:
        if target.lower() in func_name.lower():
            entry = func.getEntryPoint().toString()
            refs = getReferencesTo(func.getEntryPoint())
            for ref in refs:
                caller_addr = ref.getFromAddress().toString()
                params = []
                try:
                    caller_func = getFunctionContaining(ref.getFromAddress())
                    if caller_func:
                        body = caller_func.getBody()
                        code_units = currentProgram.getListing().getCodeUnits(body, True)
                        for cu in code_units:
                            disasm = cu.toString()
                            imm_match = re.search(r"0x[0-9A-Fa-f]{4,8}", disasm)
                            if imm_match:
                                params.append(imm_match.group(0))
                            if len(params) >= 5:
                                break
                except:
                    pass

                result["xrefs"].append({
                    "target": target,
                    "caller": caller_addr,
                    "params": params[:5],
                })

for sym in currentProgram.getSymbolTable().getAllSymbols(True):
    if sym.getSymbolType() == SymbolType.FUNCTION and sym.isExternal():
        result["exports"].append({
            "name": sym.getName(),
            "address": sym.getAddress().toString(),
        })

for func in func_manager.getExternalFunctions():
    result["exports"].append({
        "name": func.getName(),
        "address": func.getEntryPoint().toString(),
    })

result["keys"] = [dict(t) for t in {tuple(d.items()) for d in result["keys"]}]
result["xrefs"] = [dict(t) for t in {tuple(d.items()) for d in result["xrefs"]}]

if output_path:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("[auto_ghidra_scan] Output written to:", output_path)
else:
    print(json.dumps(result, indent=2, ensure_ascii=False))

exit(0)
""".strip()
