"""Tests for the cauldron.cms.flatfile Django system checks."""
from pathlib import Path

from django.test.utils import override_settings

from cauldron_cms_flatfile.checks import check_cms_config


def _run_check():
    return check_cms_config(None)


def test_no_config_returns_info():
    with override_settings(CAULDRON_MODULES={"cauldron.cms.flatfile": {}}):
        result = _run_check()
    ids = {msg.id for msg in result}
    assert "cauldron.cms.flatfile.I600" in ids


def test_missing_site_root_returns_info():
    with override_settings(CAULDRON_MODULES={"cauldron.cms.flatfile": {}}):
        result = _run_check()
    assert result
    # No site_root -> Info-level guidance
    assert result[0].id == "cauldron.cms.flatfile.I600"


def test_relative_site_root_errors(tmp_path: Path):
    with override_settings(CAULDRON_MODULES={"cauldron.cms.flatfile": {"site_root": "relative/path"}}):
        result = _run_check()
    ids = {msg.id for msg in result}
    assert "cauldron.cms.flatfile.E600" in ids


def test_missing_site_root_path_errors(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    with override_settings(CAULDRON_MODULES={"cauldron.cms.flatfile": {"site_root": str(missing)}}):
        result = _run_check()
    ids = {msg.id for msg in result}
    assert "cauldron.cms.flatfile.E601" in ids


def test_valid_config(tmp_path: Path):
    site = tmp_path / "site"
    site.mkdir()
    with override_settings(CAULDRON_MODULES={"cauldron.cms.flatfile": {"site_root": str(site)}}):
        result = _run_check()
    ids = {msg.id for msg in result}
    assert "cauldron.cms.flatfile.I600" in ids
