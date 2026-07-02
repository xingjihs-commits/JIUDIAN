"""
Ghidra 结果注入器 — 将 Ghidra 深度分析输出整合到 candidate_profile
提升品牌识别置信度，补全导出表，注入密钥与线索
"""

from typing import Dict, Any, Optional


def enrich_profile(
    candidate_profile: Dict[str, Any],
    ghidra_result: Dict[str, Any],
    ghidra_enriched: bool = True,
) -> Dict[str, Any]:
    """
    把 Ghidra 分析结果注入 candidate_profile。

    规则：
    - 找到密钥 → 写入 detect.sector_keys
    - 找到品牌线索 → 写入 detect.string_brand_hints
    - 导出表补全（pefile 漏掉的）
    - 每个密钥 +0.1 置信度，每个 xref +0.05，上限 1.0
    - Ghidra 没跑 → probe_meta.ghidra_enriched = False
    """
    if "detect" not in candidate_profile:
        candidate_profile["detect"] = {}
    if "probe_meta" not in candidate_profile:
        candidate_profile["probe_meta"] = {}

    candidate_profile["probe_meta"]["ghidra_enriched"] = ghidra_enriched

    if not ghidra_enriched or not ghidra_result:
        return candidate_profile

    keys = ghidra_result.get("keys", [])
    if keys:
        existing = candidate_profile["detect"].get("sector_keys", [])
        seen_vals = {k.get("value") for k in existing}
        for k in keys:
            if k.get("value") not in seen_vals:
                existing.append(k)
                seen_vals.add(k["value"])
        candidate_profile["detect"]["sector_keys"] = existing

    hints = ghidra_result.get("strings_hint", [])
    if hints:
        existing_hints = candidate_profile["detect"].get("string_brand_hints", [])
        seen_keywords = {h.get("keyword") for h in existing_hints}
        for h in hints:
            if h.get("keyword") not in seen_keywords:
                existing_hints.append(h)
                seen_keywords.add(h["keyword"])
        candidate_profile["detect"]["string_brand_hints"] = existing_hints

    exports = ghidra_result.get("exports", [])
    if exports:
        existing_exports = candidate_profile.get("exports", [])
        seen_names = {e.get("name") for e in existing_exports}
        for e in exports:
            if e.get("name") not in seen_names:
                existing_exports.append(e)
                seen_names.add(e["name"])
        candidate_profile["exports"] = existing_exports

    confidence = candidate_profile.get("confidence", 0.0)
    key_boost = min(len(keys) * 0.1, 0.4)
    xref_boost = min(len(ghidra_result.get("xrefs", [])) * 0.05, 0.3)
    candidate_profile["confidence"] = min(confidence + key_boost + xref_boost, 1.0)

    file_clues = ghidra_result.get("file_clues", [])
    if file_clues:
        candidate_profile["probe_meta"]["file_clues"] = file_clues

    candidate_profile["probe_meta"]["ghidra_keys_found"] = len(keys)
    candidate_profile["probe_meta"]["ghidra_xrefs_found"] = len(ghidra_result.get("xrefs", []))
    candidate_profile["probe_meta"]["ghidra_total_functions"] = ghidra_result.get("total_functions", 0)

    return candidate_profile
