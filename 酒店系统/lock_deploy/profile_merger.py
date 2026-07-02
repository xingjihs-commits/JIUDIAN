"""
lock_deploy/profile_merger.py — 诊断包导入器（厂家端）

职责：
1. 从 diagnostic.zip 中提取候选配置
2. 把配置写入 lock_adapters/profile/profiles/ 目录
3. 返回导入结果，供 UI 展示

用法：
    from lock_deploy.profile_merger import from_diag_zip, install_profile

    # 第一步：从 zip 提取
    result = from_diag_zip(r"D:\solid_lock_diag_未知品牌_20260608.zip")
    print(result["candidate_profile"]["brand"])  # "疑似 爱迪尔 Lock9200"

    # 第二步：安装到 profiles/
    ok = install_profile(result["candidate_profile"])
    print("导入成功" if ok else "导入失败")
"""

from __future__ import annotations

import json
import logging
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 路径
# ──────────────────────────────────────────────────────────────────

def _profiles_dir() -> Path:
    """返回 profiles/ 目录路径。"""
    me = Path(__file__).resolve().parent
    return me.parent / "lock_adapters" / "profile" / "profiles"


# ──────────────────────────────────────────────────────────────────
# 从诊断包提取候选配置
# ──────────────────────────────────────────────────────────────────

def from_diag_zip(zip_path: str) -> Dict[str, Any]:
    """解析诊断包，提取候选配置和关键元数据。

    支持的 zip 结构：
    - payload.json（主报告）
    - candidate_profile.json（可选，dll_probe 生成的配置）

    Returns:
        {
            "ok": bool,
            "zip_path": str,
            "candidate_profile": dict or None,
            "payload": dict,          # 原始报告
            "errors": [str, ...],     # 解析过程中的非致命异常
        }
    """
    result: Dict[str, Any] = {
        "ok": False,
        "zip_path": zip_path,
        "candidate_profile": None,
        "payload": {},
        "errors": [],
    }

    if not os.path.isfile(zip_path):
        result["errors"].append(f"文件不存在: {zip_path}")
        return result

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except Exception as e:
        result["errors"].append(f"打开 zip 失败: {e}")
        return result

    # 读取 payload.json
    try:
        with zf.open("payload.json") as f:
            result["payload"] = json.loads(f.read().decode("utf-8"))
    except Exception as e:
        result["errors"].append(f"读取 payload.json 失败: {e}")

    # 读取 candidate_profile.json（探针生成）
    try:
        with zf.open("candidate_profile.json") as f:
            raw = json.loads(f.read().decode("utf-8"))
            result["candidate_profile"] = raw
    except Exception as e:
        result["errors"].append(f"读取 candidate_profile.json 失败: {e}")

# 兼容旧版：从 payload 的 probe_result 字段找
    if result["candidate_profile"] is None:
        payload = result.get("payload", {})
        probe_result = payload.get("probe_result")
        if probe_result:
            cp = probe_result.get("candidate_profile")
            if cp:
                result["candidate_profile"] = cp

    zf.close()
    result["ok"] = result["candidate_profile"] is not None
    return result


# ──────────────────────────────────────────────────────────────────
# 安装配置到 profiles/ 目录
# ──────────────────────────────────────────────────────────────────

def install_profile(profile: Dict[str, Any], *, dry_run: bool = False) -> bool:
    """把候选配置写入 lock_adapters/profile/profiles/。

    Args:
        profile: 候选配置字典
        dry_run: 若为 True 只打印不写文件

    Returns:
        是否写入成功
    """
    errors: List[str] = []
    profile = _sanitize_profile(profile)
    profile = _bump_if_conflict(profile)

    profiles_dir = _profiles_dir()
    if not profiles_dir.is_dir():
        errors.append(f"profiles 目录不存在: {profiles_dir}")
        logger.error("[profile_merger] %s", errors[-1])
        return False

    # 输出文件名
    brand_tag = _safe_filename(profile.get("brand", "未知品牌"))
    filename = f"auto_{brand_tag}.json"
    out_path = profiles_dir / filename

    if dry_run:
        print(f"[dry-run] 将写入: {out_path}")
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return True

    try:
        json_str = json.dumps(profile, ensure_ascii=False, indent=2)
        out_path.write_text(json_str, encoding="utf-8")
        logger.info("[profile_merger] 配置已写入: %s", out_path)
        return True
    except Exception as e:
        errors.append(f"写入配置失败: {e}")
        logger.error("[profile_merger] %s", errors[-1])
        return False


def _sanitize_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """清理和补全候选配置，确保兼容 profiles/ 格式。"""
    p = dict(profile)

    # 确保必填字段
    p.setdefault("brand", "未知品牌")
    p.setdefault("detect", {"files": []})
    p.setdefault("dll", {"path": ""})
    p.setdefault("payload", {"magic": "C92B20B7", "size": 16})
    p.setdefault("supported", False)

    # 如果 detect.files 为空，从 dll.path 补充
    detect = p.get("detect", {})
    if not detect.get("files"):
        dll_path = p.get("dll", {}).get("path", "")
        if dll_path:
            detect["files"] = [dll_path]

    # 删除 probe_meta 中的 install_dir（可能含敏感路径信息）
    meta = p.get("probe_meta", {})
    if "install_dir" in meta:
        del meta["install_dir"]

    return p


def _bump_if_conflict(profile: Dict[str, Any]) -> Dict[str, Any]:
    """如果 profiles/ 目录已有同名配置，自动后缀 +1。"""
    profiles_dir = _profiles_dir()
    brand_tag = _safe_filename(profile.get("brand", "未知品牌"))
    base = f"auto_{brand_tag}"

    # 检查 adapter_id 冲突
    adapter_id = profile.get("adapter_id", "")
    for fpath in profiles_dir.glob("*.json"):
        try:
            existing = json.loads(fpath.read_text(encoding="utf-8"))
            if existing.get("adapter_id") == adapter_id:
                # 内容相同不重复导入
                if existing.get("dll") == profile.get("dll"):
                    logger.info("[profile_merger] 配置已存在（内容相同），跳过: %s", fpath.name)
                    profile["_skipped"] = True
                    return profile
                # adapter_id 冲突 → 加后缀
                suffix = 1
                while (profiles_dir / f"{base}_{suffix}.json").exists():
                    suffix += 1
                profile["adapter_id"] = f"{adapter_id}_{suffix}"
                logger.info("[profile_merger] adapter_id 冲突，重命名为: %s", profile["adapter_id"])
                break
        except Exception:
            continue

    return profile


# ──────────────────────────────────────────────────────────────────
# 批量导入
# ──────────────────────────────────────────────────────────────────

def batch_install(zipped_diags: List[str]) -> List[Dict[str, Any]]:
    """批量导入多个诊断包。返回每个 zip 的导入结果。"""
    results: List[Dict[str, Any]] = []
    for zip_path in zipped_diags:
        res = {"zip_path": zip_path, "ok": False, "profile": None, "errors": []}
        try:
            parsed = from_diag_zip(zip_path)
            if not parsed["ok"] or not parsed["candidate_profile"]:
                res["errors"] = parsed.get("errors", []) + ["没有找到候选配置"]
                continue

            cp = parsed["candidate_profile"]
            inst_ok = install_profile(cp)
            res["ok"] = inst_ok
            res["profile"] = cp.get("brand", "(未知)")
            res["errors"] = parsed.get("errors", [])
        except Exception as e:
            res["errors"].append(f"批量导入异常: {e}")

        results.append(res)
    return results


# ──────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    """把品牌名转成安全的文件名片段。"""
    safe = ""
    for ch in name:
        if ch.isalnum() or ch in " -_":
            safe += ch
        else:
            safe += "_"
    safe = safe.strip().lower().replace(" ", "_")
    return safe[:60] or "unknown"


def list_available_profiles() -> List[Dict[str, Any]]:
    """列出 profiles/ 目录中所有 auto_ 前缀的配置文件。"""
    profiles: List[Dict[str, Any]] = []
    profiles_dir = _profiles_dir()
    if not profiles_dir.is_dir():
        return profiles

    for fpath in sorted(profiles_dir.glob("auto_*.json")):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            profiles.append({
                "filename": fpath.name,
                "brand": data.get("brand", "?"),
                "confidence": data.get("confidence", 0),
            })
        except Exception:
            continue

    return profiles


# ──────────────────────────────────────────────────────────────────
# 独立使用
# ──────────────────────────────────────────────────────────────────

def main():
    """命令行入口：python profile_merger.py <diagnostic.zip>"""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s [%(name)s] %(message)s",
    )

    if len(sys.argv) < 2:
        # 没有参数 → 列出所有 auto_ 配置
        profiles = list_available_profiles()
        if profiles:
            print(f"已安装 {len(profiles)} 个自动探测的配置：")
            for p in profiles:
                print(f"  {p['filename']:40s} {p['brand']:20s} 置信度: {p['confidence']:.0%}")
        else:
            print("没有自动探测的配置（profiles/ 目录下无 auto_*.json）")
        return

    zip_path = sys.argv[1]
    print(f"解析: {zip_path}")
    result = from_diag_zip(zip_path)

    if result["ok"]:
        print(f"✅ 找到候选配置: {result['candidate_profile']['brand']}")
        ok = install_profile(result["candidate_profile"])
        print(f"   {'✅ 导入成功' if ok else '❌ 导入失败'}")
    else:
        print(f"❌ 解析失败: {result.get('errors', ['原因未知'])}")

    if result.get("errors"):
        for err in result["errors"]:
            print(f"  ⚠ {err}")


if __name__ == "__main__":
    main()
