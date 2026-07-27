"""Tests for cauldron.site.astro Django system checks."""
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test.utils import override_settings

from cauldron_site_astro.checks import check_site_astro_config


def _run_check():
    return check_site_astro_config(None)


def _ids(result):
    return {msg.id for msg in result}


# ---------------------------------------------------------------------------
# Inactive module
# ---------------------------------------------------------------------------


def test_inactive_module_returns_empty():
    """When cauldron.site.astro is not in CAULDRON_MODULES, no checks run."""
    with override_settings(CAULDRON_MODULES={}):
        result = _run_check()
    assert result == []


def test_no_cauldron_modules_setting_returns_empty():
    """When CAULDRON_MODULES is absent entirely, no checks run."""
    with override_settings(CAULDRON_MODULES=None):
        result = _run_check()
    assert result == []


# ---------------------------------------------------------------------------
# E100 — frontend_root not set
# ---------------------------------------------------------------------------


def test_missing_frontend_root_emits_E100():
    with override_settings(
        CAULDRON_MODULES={"cauldron.site.astro": {"output_root": "/tmp/out"}}
    ):
        result = _run_check()
    assert "cauldron.site.astro.E100" in _ids(result)


def test_empty_frontend_root_emits_E100():
    with override_settings(
        CAULDRON_MODULES={
            "cauldron.site.astro": {"frontend_root": "", "output_root": "/tmp/out"}
        }
    ):
        result = _run_check()
    assert "cauldron.site.astro.E100" in _ids(result)


# ---------------------------------------------------------------------------
# E101 — frontend_root does not exist
# ---------------------------------------------------------------------------


def test_nonexistent_frontend_root_emits_E101(tmp_path: Path):
    missing = tmp_path / "does_not_exist"
    with override_settings(
        CAULDRON_MODULES={
            "cauldron.site.astro": {
                "frontend_root": str(missing),
                "output_root": "/tmp/out",
            }
        }
    ):
        result = _run_check()
    assert "cauldron.site.astro.E101" in _ids(result)


# ---------------------------------------------------------------------------
# E102 — package.json missing
# ---------------------------------------------------------------------------


def test_missing_package_json_emits_E102(tmp_path: Path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    # No package.json created
    with override_settings(
        CAULDRON_MODULES={
            "cauldron.site.astro": {
                "frontend_root": str(frontend),
                "output_root": "/tmp/out",
            }
        }
    ):
        result = _run_check()
    assert "cauldron.site.astro.E102" in _ids(result)


# ---------------------------------------------------------------------------
# E103 — output_root inside frontend_root
# ---------------------------------------------------------------------------


def test_output_root_inside_frontend_root_emits_E103(tmp_path: Path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    output = frontend / "dist"
    with patch("shutil.which", return_value="/usr/bin/npm"):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(frontend),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    assert "cauldron.site.astro.E103" in _ids(result)


# ---------------------------------------------------------------------------
# W110 — npm not found
# ---------------------------------------------------------------------------


def test_npm_not_found_emits_W110(tmp_path: Path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value=None):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(frontend),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    assert "cauldron.site.astro.W110" in _ids(result)


def test_custom_npm_command_not_found_emits_W110(tmp_path: Path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value=None):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(frontend),
                    "output_root": str(output),
                    "npm_command": "pnpm",
                }
            }
        ):
            result = _run_check()
    assert "cauldron.site.astro.W110" in _ids(result)


# ---------------------------------------------------------------------------
# I120 — healthy configuration
# ---------------------------------------------------------------------------


def test_healthy_config_emits_I120(tmp_path: Path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(frontend),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    assert "cauldron.site.astro.I120" in _ids(result)
    # Must not contain any errors
    error_ids = {m.id for m in result if m.id.startswith("cauldron.site.astro.E")}
    assert not error_ids


def test_healthy_config_has_no_errors(tmp_path: Path):
    """Healthy config produces exactly one check: I120."""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(frontend),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    assert len(result) == 1
    assert result[0].id == "cauldron.site.astro.I120"
