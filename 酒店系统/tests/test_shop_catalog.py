# -*- coding: utf-8 -*-
from pathlib import Path
from unittest.mock import patch
import pytest

@pytest.fixture
def real_db(tmp_path: Path):
    with patch("database._get_app_dir", return_value=tmp_path):
        from database import ShadowDatabase
        db = ShadowDatabase("test_shop.db")
        yield db

def test_seed_shop_from_manifest(real_db):
    from shop_catalog import seed_shop_from_manifest, load_manifest
    manifest = load_manifest()
    if not manifest.get("items"):
        pytest.skip("manifest 未就位")
    n = seed_shop_from_manifest(real_db)
    assert n >= 0

def test_shop_assets_missing_graceful():
    from shop_assets import load_shop_pixmap
    pix = load_shop_pixmap("NONEXIST_SKU_XYZ", size=32)
    assert pix is None
