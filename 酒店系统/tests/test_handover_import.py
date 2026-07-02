"""握手包导入 — 最小 fixture 冒烟测试。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "minimal_handover"


def _build_handover_zip(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in (
            "MANIFEST.json",
            "lock_profile.json",
            "room_data.json",
            "guest_data.json",
            "lock_state.json",
        ):
            zf.write(_FIXTURE_DIR / name, arcname=name)
    return target


@pytest.fixture
def handover_zip(tmp_path: Path) -> Path:
    return _build_handover_zip(tmp_path / "minimal.solidhandover")


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_config.return_value = None
    db.execute.return_value = None
    db.set_config.return_value = None
    return db


@pytest.fixture
def importer_env(tmp_path: Path, mock_db):
    profile_dir = tmp_path / "profiles"
    runtime_dir = tmp_path / "lock_runtime"
    profile_dir.mkdir()
    runtime_dir.mkdir()
    with patch("lock_deploy.handover_importer._db", return_value=mock_db):
        with patch("lock_deploy.handover_importer._PROFILE_DIR", profile_dir):
            with patch("lock_deploy.handover_importer._RUNTIME_DIR", runtime_dir):
                from lock_deploy.handover_importer import HandoverImporter

                yield HandoverImporter(), profile_dir, runtime_dir, mock_db


def test_handover_import_success(handover_zip: Path, importer_env):
    imp, profile_dir, runtime_dir, mock_db = importer_env
    result = imp.run(str(handover_zip))

    assert result["ok"] is True, result.get("errors")
    assert result["mode"] == "dll_direct"
    assert result["brand"] == "测试品牌"
    assert result["rooms_imported"] == 1
    assert any(profile_dir.glob("handover_*.json"))
    assert mock_db.execute.called
    assert mock_db.set_config.called


def test_handover_import_rollback(handover_zip: Path, importer_env):
    imp, profile_dir, _runtime_dir, _mock_db = importer_env
    result = imp.run(str(handover_zip))
    assert result["ok"] is True

    rollback = imp.rollback()
    assert rollback["ok"] is True
    assert not any(profile_dir.glob("handover_*.json"))


def test_handover_rejects_bad_version(tmp_path: Path, importer_env):
    imp, _, _, _ = importer_env
    bad_manifest = json.loads((_FIXTURE_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    bad_manifest["handover_version"] = "9.9"
    bad_zip = tmp_path / "bad.solidhandover"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("MANIFEST.json", json.dumps(bad_manifest))
        for name in ("lock_profile.json", "room_data.json", "guest_data.json", "lock_state.json"):
            zf.write(_FIXTURE_DIR / name, arcname=name)

    result = imp.run(str(bad_zip))
    assert result["ok"] is False
    assert any("版本" in e or "handover_version" in e for e in result.get("errors", []))


def test_generic_adapter_loads_bida_profile():
    """非 V9 profile 可被 GenericLockAdapter 加载（不连硬件）。"""
    from lock_adapters.generic_adapter import GenericLockAdapter

    profile_path = (
        Path(__file__).resolve().parent.parent
        / "lock_adapters"
        / "profile"
        / "profiles"
        / "bida_ib.json"
    )
    assert profile_path.is_file()
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    adapter = GenericLockAdapter(install_dir=Path("."), profile=data)
    assert adapter._profile.get("brand") == "必达 IB"
    assert adapter._profile.get("adapter_id") == "bida_ib"
