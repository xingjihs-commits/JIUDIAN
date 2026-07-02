"""
lock_deploy/importer.py — 把酒店现有 proUSB 配置导入 Solid

读取：
- `System.ini` 里的 `dlsCoID / HotelID / PCID / SN / LD / HotelName / CompanyName / ...`
- DLL 路径（`V9RFL.dll`, `d12.dll`, `Mwic_32.dll` …）
- 可选 `CardLock.mdb` 路径（交给 legacy_migration 模块去处理具体的 MDB 内容）

写入 Solid SQLite 的 `system_config` 表，键名带 `lock_takeover_` 前缀避免冲突：
- `lock_takeover_brand`       —— 'proUSB' / 'aidier' / ...
- `lock_takeover_adapter_id`  —— 对应 LockAdapter 类
- `lock_takeover_install_dir` —— 路径
- `lock_takeover_dll_path`    —— V9RFL.dll 绝对路径
- `lock_takeover_dlsCoID`     —— 整数 (proUSB 专用)
- `lock_takeover_hotel_id`    —— 128 位十六进制字符串
- `lock_takeover_pc_id`       —— 12 位十六进制（MAC）
- `lock_takeover_hotel_name`  —— 内部名
- `lock_takeover_company`     —— CompanyName
- `lock_takeover_mdb_path`    —— CardLock.mdb 路径（可空）
- `lock_takeover_done_at`     —— ISO 时间戳
- `lock_takeover_raw_ini`     —— JSON dump，留作回溯
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# System.ini 解析
# ──────────────────────────────────────────────────────────────────

def parse_system_ini(ini_path: Path) -> Dict[str, Dict[str, str]]:
    """
    一个容错 INI 解析器（proUSB 的 ini 有些非标准行，标准 configparser 经常炸）。
    返回嵌套字典 {section: {key: value}}。
    """
    ini_path = Path(ini_path)
    result: Dict[str, Dict[str, str]] = {}
    current = "_default"
    result[current] = {}

    try:
        # proUSB 的 ini 经常是 GBK 编码
        text = _read_text_best_effort(ini_path)
    except Exception as e:
        result["_error"] = {"reason": f"无法读取 {ini_path}: {e}"}
        return result

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("//") or line.startswith(";") or line.startswith("#"):
            continue
        # section
        m = re.match(r"^\[([^\]]+)\]\s*$", line)
        if m:
            current = m.group(1).strip()
            result.setdefault(current, {})
            continue
        # 跳过单独的 { } 之类
        if line in "{}":
            continue
        # key = value
        if "=" in line:
            key, _, val = line.partition("=")
            result.setdefault(current, {})[key.strip()] = val.strip()

    return result


def _read_text_best_effort(path: Path) -> str:
    """
    proUSB 系列文件编码很乱：
    - System.ini      → GBK / GB18030
    - 发卡器记录/*.TXT → UTF-16 LE (带 FF FE BOM)
    - 个别新版本       → UTF-8

    优先用 BOM 嗅探，没 BOM 再按编码挨个试。
    """
    raw = path.read_bytes()

    # 1. BOM 嗅探（最准）
    if raw.startswith(b"\xff\xfe"):
        try:
            return raw.decode("utf-16-le")
        except Exception:
            pass
    if raw.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16-be")
        except Exception:
            pass
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw.decode("utf-8-sig")
        except Exception:
            pass

    # 2. 启发式：大量 0x00 字节 → 大概率 UTF-16 但没 BOM
    sample = raw[:1024]
    zeros = sample.count(b"\x00")
    if zeros > len(sample) // 3:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc)
            except Exception:
                pass

    # 3. 兜底：常见单字节 / 多字节编码挨个试
    for enc in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue

    return raw.decode("latin-1", errors="replace")


# ──────────────────────────────────────────────────────────────────
# 字段提取
# ──────────────────────────────────────────────────────────────────

def extract_prousb_fields(ini_data: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """从解析过的 ini 里提取我们关心的字段。"""
    system = ini_data.get("SYSTEM", {}) or ini_data.get("_default", {})

    def _get(*names: str) -> str:
        for n in names:
            for sect in (system, ini_data.get("_default", {})):
                for k, v in (sect or {}).items():
                    if k.lower() == n.lower():
                        return v
        return ""

    dlsCoID_raw = _get("dlsCoID")
    try:
        dlsCoID = int(dlsCoID_raw.strip()) if dlsCoID_raw else 0
    except Exception:
        dlsCoID = 0

    return {
        "dlsCoID":      dlsCoID,
        "hotel_id":     _get("HotelID"),
        "pc_id":        _get("PCID"),
        "hotel_name":   _get("HotelName"),
        "company":      _get("CompanyName"),
        "company_tel":  _get("CompanyPhone"),
        "sn":           _get("SN"),
        "ld":           _get("LD"),
        "db_bak_path":      _get("DBBakPath"),
        "share_db_path":    _get("ShareDBPath"),
        "checkout_time":    _get("CheckOutTime"),
        "vip_checkout_time": _get("VIPCheckOutTime"),
        "flag_checkout":    _get("FlagCheckOut"),
        "guest_llock":      _get("GuestLLock"),
        "language":     _get("Language"),
        "main_title":   _get("MainTitle"),
        "logon_title":  _get("LogonTitle"),
        # 这些字段 System.ini 里不一定有，但「发卡器记录」目录里的 TXT 里有 —
        # importer 会把发卡器记录也一并解析进来再合并
        "encoder_type": _get("EncoderType"),
        "my666":        _get("MY666"),  # 发卡器内置卡 UID + 厂家校验数据
        "my777":        _get("MY777"),  # Mifare sector trailer 模板
        "sn17":         _get("SN17"),
        "ldo":          _get("LDO"),    # LD Original 备份
    }


def find_registration_snapshot(install_dir: Path) -> Optional[Path]:
    """
    proUSB 的"发卡器记录"目录里通常有一个 N<XXXX>_<TIMESTAMP_HEX>.TXT 文件，
    那是首次注册时的 ini 快照 + 发卡器硬件指纹。返回最新的一个，没有则 None。
    """
    candidates: List[Path] = []
    for sub_name in ("发卡器记录", "CardIssuerLog", "EncoderLog", "Records"):
        sub = install_dir / sub_name
        if sub.is_dir():
            for f in sub.glob("*.TXT"):
                candidates.append(f)
            for f in sub.glob("*.txt"):
                candidates.append(f)
    if not candidates:
        return None
    # 按修改时间最新返回
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def merge_registration_snapshot(
    fields: Dict[str, Any], snapshot_path: Path,
) -> Dict[str, Any]:
    """
    把发卡器记录里的字段合并到 fields，但**只补充 System.ini 里没有/为空的字段**。
    System.ini 永远优先（因为它是最新状态）。
    """
    try:
        ini = parse_system_ini(snapshot_path)
        snap = extract_prousb_fields(ini)
    except Exception:
        return fields

    for key, val in snap.items():
        if not fields.get(key) and val:
            fields[key] = val
    fields["_snapshot_path"] = str(snapshot_path)
    return fields


# ──────────────────────────────────────────────────────────────────
# 导入到 Solid SQLite
# ──────────────────────────────────────────────────────────────────

class LockTakeoverImporter:
    """
    把识别到的安装目录导入 Solid。

    用法：
        from lock_deploy import scan_for_lock_systems, LockTakeoverImporter
        cands = scan_for_lock_systems()
        if cands and cands[0].supported:
            imp = LockTakeoverImporter(cands[0])
            imp.run()
    """

    def __init__(self, candidate=None, *, dry_run: bool = False):
        from lock_deploy.scanner import InstallationCandidate
        if candidate is not None and not isinstance(candidate, InstallationCandidate):
            raise TypeError("candidate must be InstallationCandidate or None (for from_json)")
        self.candidate = candidate
        self.dry_run = dry_run
        self.fields: Dict[str, Any] = {}
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @classmethod
    def from_json(cls, json_path: Path, *, dry_run: bool = False) -> "LockTakeoverImporter":
        """
        从 Solid_Field_Box 产出的 hotel_profile.json 直接导入。

        用法：
            imp = LockTakeoverImporter.from_json("D:\\hotel_profile.json")
            imp.run()
        """
        json_path = Path(json_path)
        if not json_path.is_file():
            raise FileNotFoundError(f"hotel_profile.json 不存在: {json_path}")

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"hotel_profile.json 格式错误: {e}")

        profile_version = data.get("version", "1.0")
        logger.info("[from_json] 加载 hotel_profile.json v%s: %s", profile_version, json_path)

        # ── 构造候选对象 ──
        from lock_deploy.scanner import InstallationCandidate
        brand = data.get("brand", "未知品牌")
        adapter_id = data.get("adapter_id") or ""
        install_dir = data.get("install_dir", "")

        candidate = InstallationCandidate(
            path=Path(install_dir) if install_dir else Path("."),
            brand=brand,
            adapter_id=adapter_id,
            score=data.get("score", 200),
            matched_required=[],
            matched_optional=[],
            has_mdb=True,
            mdb_paths=[],
            system_ini=None,
            supported=True,
            dll_exports=data.get("dll_exports", {}),
            matched_exports=[],
            brand_confidence=data.get("brand_confidence", ""),
            card_magic_detected=True,
        )

        imp = cls(candidate, dry_run=dry_run)

        # ── 填充字段（来自 system_config）──
        sc = data.get("system_config", {})
        imp.fields = {
            "dlsCoID":         int(sc.get("dlsCoID", 0)),
            "hotel_id":        str(sc.get("hotel_id", "")),
            "pc_id":           str(sc.get("pc_id", "")),
            "hotel_name":      str(sc.get("hotel_name", "")),
            "company":         str(sc.get("company", "")),
            "sn":              str(sc.get("sn", "")),
            "ld":              str(sc.get("ld", "")),
            "db_bak_path":     str(sc.get("db_bak_path", "")),
            "share_db_path":   str(sc.get("share_db_path", "")),
            "checkout_time":   str(sc.get("checkout_time", "")),
            "vip_checkout_time": str(sc.get("vip_checkout_time", "")),
            "flag_checkout":   str(sc.get("flag_checkout", "")),
            "guest_llock":     str(sc.get("guest_llock", "")),
            "encoder_type":    str(sc.get("encoder_type", "")),
            "my666":           str(sc.get("my666", "")),
            "my777":           str(sc.get("my777", "")),
            "_via_json":       str(json_path.resolve()),
            "_profile_version": profile_version,
        }

        # ── v1.1+ 额外字段 ──
        if profile_version >= "1.1":
            imp.fields["_dll_exports"] = data.get("dll_exports", {})
            imp.fields["_card_samples"] = data.get("card_samples", {})
            imp.fields["_brand_confidence"] = data.get("brand_confidence", "")
            imp.fields["_adaptation_notes"] = data.get("adaptation_notes", "")

        # ── MDB 路径推断 ──
        mdb_paths = data.get("mdb_paths") or []
        if mdb_paths:
            imp.candidate.mdb_paths = [Path(p) for p in mdb_paths if Path(p).exists()]

        # ── 活 MDB 发现（如果 json 里有路径信息）──
        try:
            from lock_deploy.live_mdb import discover_live_mdb
            live = discover_live_mdb(
                install_dir=candidate.path,
                share_db_path_hint=sc.get("share_db_path") or None,
                db_bak_path_hint=sc.get("db_bak_path") or None,
            )
            imp.fields["_live_mdb"] = live
            if live.path and not live.validated:
                imp.warnings.append(f"活数据库已找到但未通过校验: {live.path}")
            elif not live.path:
                imp.warnings.append(f"未自动发现活数据库: {live.error or '将于后续诊断页手动指定'}")
        except Exception as e:
            imp.warnings.append(f"活 MDB 自动发现失败（不影响接管）：{e}")

        logger.info("[from_json] 导入完成 brand=%s adapter=%s dlsCoID=%s",
                    brand, adapter_id, imp.fields.get("dlsCoID"))
        return imp

    # ──────────── 步骤 ────────────

    def parse_ini(self) -> Dict[str, Any]:
        if self.candidate.system_ini and self.candidate.system_ini.is_file():
            ini = parse_system_ini(self.candidate.system_ini)
            self.fields = extract_prousb_fields(ini)
            self.fields["_raw_ini_sections"] = list(ini.keys())
            self.fields["_raw_ini_text_path"] = str(self.candidate.system_ini)
        else:
            self.warnings.append("没有找到 System.ini，dlsCoID/HotelID 等字段将留空，需要手填。")
            self.fields = {
                "dlsCoID": 0, "hotel_id": "", "pc_id": "",
                "hotel_name": "", "company": "",
                "sn": "", "ld": "",
            }

        # 顺手把"发卡器记录"快照里的字段补充进来（EncoderType / MY666 / MY777 等）
        snapshot = find_registration_snapshot(self.candidate.path)
        if snapshot is not None:
            try:
                merge_registration_snapshot(self.fields, snapshot)
            except Exception as e:
                self.warnings.append(f"解析发卡器记录快照失败（不影响接管）：{e}")

        # 活 MDB 自动发现（ShareDBPath > DBBakPath > 安装目录备份子文件夹 > 盘根）
        try:
            from lock_deploy.live_mdb import discover_live_mdb
            live = discover_live_mdb(
                install_dir=self.candidate.path,
                share_db_path_hint=self.fields.get("share_db_path") or None,
                db_bak_path_hint=self.fields.get("db_bak_path") or None,
            )
            self.fields["_live_mdb"] = live
            if live.path and not live.validated:
                self.warnings.append(
                    f"活数据库已找到但未通过校验: {live.path} ({live.error or '未知'})"
                )
            elif not live.path:
                self.warnings.append(
                    f"未自动发现活数据库: {live.error or '请稍后在诊断页手动指定'}"
                )
        except Exception as e:
            self.warnings.append(f"活 MDB 自动发现失败（不影响接管）：{e}")

        return dict(self.fields)

    def write_to_solid(self) -> None:
        """写入 Solid 的 system_config 表。"""
        try:
            from database import db
        except Exception as e:
            self.errors.append(f"无法导入 database 模块: {e}")
            return

        c = self.candidate
        cfg = {
            "lock_takeover_brand":        c.brand,
            "lock_takeover_adapter_id":   c.adapter_id or "",
            "lock_takeover_install_dir":  str(c.path),
            "lock_takeover_dll_path":     str(c.path / "V9RFL.dll") if (c.path / "V9RFL.dll").is_file() else "",
            "lock_takeover_dlsCoID":      str(self.fields.get("dlsCoID", 0)),
            "lock_takeover_hotel_id":     str(self.fields.get("hotel_id", "")),
            "lock_takeover_pc_id":        str(self.fields.get("pc_id", "")),
            "lock_takeover_hotel_name":   str(self.fields.get("hotel_name", "")),
            "lock_takeover_company":      str(self.fields.get("company", "")),
            "lock_takeover_sn":           str(self.fields.get("sn", "")),
            "lock_takeover_ld":           str(self.fields.get("ld", "")),
            "lock_takeover_db_bak_path":  str(self.fields.get("db_bak_path", "")),
            "lock_takeover_mdb_path":     str(c.mdb_paths[0]) if c.mdb_paths else "",
            # 来自"发卡器记录"快照的硬件指纹字段（System.ini 里通常没有）
            "lock_takeover_encoder_type": str(self.fields.get("encoder_type", "")),
            "lock_takeover_my666":        str(self.fields.get("my666", "")),
            "lock_takeover_my777":        str(self.fields.get("my777", "")),
            "lock_takeover_snapshot":     str(self.fields.get("_snapshot_path", "")),
            "lock_takeover_done_at":      _dt.datetime.now().isoformat(timespec="seconds"),
            "lock_takeover_raw_fields":   json.dumps(
                {k: v for k, v in self.fields.items() if not k.startswith("_")},
                ensure_ascii=False,
            ),
            "lock_takeover_share_db_path": str(self.fields.get("share_db_path", "")),
            "lock_takeover_checkout_time": str(self.fields.get("checkout_time", "")),
            "lock_takeover_vip_checkout_time": str(self.fields.get("vip_checkout_time", "")),
            "lock_takeover_flag_checkout": str(self.fields.get("flag_checkout", "")),
            "lock_takeover_guest_llock": str(self.fields.get("guest_llock", "")),
        }
        live = self.fields.get("_live_mdb")
        if live is not None:
            try:
                from vendor_gate import persist_live_mdb_result
                if not self.dry_run:
                    persist_live_mdb_result(live)
                cfg["lock_takeover_live_mdb_path"] = str(live.path) if live.path else ""
                cfg["lock_takeover_live_mdb_dir"] = str(live.dir) if live.dir else ""
                cfg["lock_takeover_live_mdb_source"] = live.source or ""
                cfg["lock_takeover_live_mdb_validated_at"] = (
                    _dt.datetime.now().isoformat(timespec="seconds") if live.validated else ""
                )
            except Exception as e:
                self.warnings.append(f"写入活 MDB 配置失败: {e}")
        if self.dry_run:
            self.fields["_would_write"] = cfg
            return

        try:
            for k, v in cfg.items():
                try:
                    db.set_config(k, v)
                except Exception as e:
                    self.errors.append(f"set_config({k}) 失败: {e}")
            try:
                db.log_action("LOCK_TAKEOVER", "DEPLOY_DONE",
                              f"brand={c.brand} dlsCoID={self.fields.get('dlsCoID')}")
            except Exception:
                pass
        except Exception as e:
            self.errors.append(f"写入 system_config 时整体异常: {e}")

    def run(self) -> Dict[str, Any]:
        is_from_json = bool(self.fields.get("_via_json"))
        if not is_from_json:
            self.parse_ini()
        self.write_to_solid()
        return {
            "candidate":  self.candidate.as_dict() if self.candidate else {},
            "fields":     {k: v for k, v in self.fields.items() if not k.startswith("_")},
            "errors":     list(self.errors),
            "warnings":   list(self.warnings),
            "ok":         not self.errors,
            "via_json":   bool(self.fields.get("_via_json")),
        }


def import_hotel_profile_json(json_path: Path, *, dry_run: bool = False) -> Dict[str, Any]:
    """
    便捷函数：从 hotel_profile.json 一键导入到 Solid。

    用法：
        result = import_hotel_profile_json("D:\\\\hotel_profile.json")
        if result["ok"]:
            print("导入成功")
    """
    imp = LockTakeoverImporter.from_json(Path(json_path), dry_run=dry_run)
    return imp.run()


# ──────────────────────────────────────────────────────────────────
# 还原已部署配置（给 LockAdapter 用）
# ──────────────────────────────────────────────────────────────────

def load_takeover_config() -> Dict[str, Any]:
    """从 Solid SQLite 里拿之前导入的接管配置。"""
    try:
        from database import db
    except Exception:
        return {}

    keys = (
        "lock_takeover_brand", "lock_takeover_adapter_id", "lock_takeover_install_dir",
        "lock_takeover_dll_path", "lock_takeover_dlsCoID", "lock_takeover_hotel_id",
        "lock_takeover_pc_id", "lock_takeover_hotel_name", "lock_takeover_company",
        "lock_takeover_mdb_path", "lock_takeover_done_at", "lock_takeover_db_bak_path",
        "lock_takeover_live_mdb_path", "lock_takeover_live_mdb_dir",
        "lock_takeover_live_mdb_source", "lock_takeover_live_mdb_validated_at",
        "lock_takeover_share_db_path", "lock_takeover_checkout_time",
        "lock_takeover_vip_checkout_time", "lock_takeover_flag_checkout",
        "lock_takeover_guest_llock",
    )
    out: Dict[str, Any] = {}
    for k in keys:
        try:
            v = db.get_config(k)
        except Exception:
            v = None
        if v is not None:
            out[k] = v
    # 转 dlsCoID 类型
    try:
        out["lock_takeover_dlsCoID"] = int(out.get("lock_takeover_dlsCoID") or 0)
    except Exception:
        out["lock_takeover_dlsCoID"] = 0
    return out


def get_active_adapter() -> Optional[Any]:
    """
    根据 Solid 里保存的接管配置实例化对应的 LockAdapter，并配置好。
    没接管过返回 None。
    """
    cfg = load_takeover_config()
    install_dir = cfg.get("lock_takeover_install_dir")
    if not install_dir:
        return None

    try:
        from lock_adapters import detect_adapter
    except Exception:
        return None

    adapter = detect_adapter(install_dir)
    if adapter is None:
        return None

    try:
        adapter.configure(
            dlsCoID=cfg.get("lock_takeover_dlsCoID"),
            hotel_id=cfg.get("lock_takeover_hotel_id"),
            pc_id=cfg.get("lock_takeover_pc_id"),
        )
    except Exception as e:
        logger.warning("[importer] adapter.configure error: %s", e)
    return adapter
