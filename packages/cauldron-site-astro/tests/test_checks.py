"""Tests for cauldron.site.astro Django system checks."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.test.utils import override_settings

from cauldron_site_astro.checks import check_site_astro_config


def _run_check():
    return check_site_astro_config(None)


def _ids(result):
    return {msg.id for msg in result}


def _make_frontend(tmp_path: Path, *, package_lock: bool = True) -> Path:
    """Create a minimal frontend_root directory."""
    fr = tmp_path / "frontend"
    fr.mkdir()
    (fr / "package.json").write_text("{}", encoding="utf-8")
    if package_lock:
        (fr / "package-lock.json").write_text("{}", encoding="utf-8")
    return fr


def _make_astro_bin(fr: Path, *, executable: bool = True) -> Path:
    """Create a fake astro binary in node_modules/.bin/."""
    bin_dir = fr / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    astro = bin_dir / "astro"
    astro.write_text("#!/bin/sh\necho '4.16.0'\n", encoding="utf-8")
    if executable:
        astro.chmod(0o755)
    else:
        astro.chmod(0o644)
    return astro


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
    fr = _make_frontend(tmp_path)
    _make_astro_bin(fr)
    output = fr / "dist"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="4.16.0\n", stderr="")
            with override_settings(
                CAULDRON_MODULES={
                    "cauldron.site.astro": {
                        "frontend_root": str(fr),
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
    fr = _make_frontend(tmp_path)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value=None):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(fr),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    assert "cauldron.site.astro.W110" in _ids(result)


def test_custom_npm_command_not_found_emits_W110(tmp_path: Path):
    fr = _make_frontend(tmp_path)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value=None):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(fr),
                    "output_root": str(output),
                    "npm_command": "pnpm",
                }
            }
        ):
            result = _run_check()
    assert "cauldron.site.astro.W110" in _ids(result)


def test_npm_not_found_message_instructs_install(tmp_path: Path):
    fr = _make_frontend(tmp_path)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value=None):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(fr),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    w110 = next(m for m in result if m.id == "cauldron.site.astro.W110")
    assert "./install" in w110.msg


# ---------------------------------------------------------------------------
# W111 — package-lock.json missing
# ---------------------------------------------------------------------------


def test_missing_package_lock_emits_W111(tmp_path: Path):
    fr = _make_frontend(tmp_path, package_lock=False)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(fr),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    assert "cauldron.site.astro.W111" in _ids(result)


def test_missing_package_lock_message_instructs_install(tmp_path: Path):
    fr = _make_frontend(tmp_path, package_lock=False)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(fr),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    w111 = next(m for m in result if m.id == "cauldron.site.astro.W111")
    assert "./install" in w111.msg


# ---------------------------------------------------------------------------
# W112 — local Astro binary missing
# ---------------------------------------------------------------------------


def test_missing_astro_binary_emits_W112(tmp_path: Path):
    fr = _make_frontend(tmp_path)
    output = tmp_path / "out"
    # Don't create the astro binary
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(fr),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    assert "cauldron.site.astro.W112" in _ids(result)


def test_missing_astro_binary_message_instructs_install(tmp_path: Path):
    fr = _make_frontend(tmp_path)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(fr),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    w112 = next(m for m in result if m.id == "cauldron.site.astro.W112")
    assert "./install" in w112.msg


def test_missing_astro_binary_does_not_emit_I120(tmp_path: Path):
    """I120 must not appear when Astro is not installed."""
    fr = _make_frontend(tmp_path)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(fr),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    assert "cauldron.site.astro.I120" not in _ids(result)


# ---------------------------------------------------------------------------
# W113 — Astro binary not executable / fails
# ---------------------------------------------------------------------------


def test_non_executable_astro_binary_emits_W113(tmp_path: Path):
    fr = _make_frontend(tmp_path)
    _make_astro_bin(fr, executable=False)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": str(fr),
                    "output_root": str(output),
                }
            }
        ):
            result = _run_check()
    assert "cauldron.site.astro.W113" in _ids(result)


def test_astro_binary_that_fails_emits_W113(tmp_path: Path):
    fr = _make_frontend(tmp_path)
    _make_astro_bin(fr)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            with override_settings(
                CAULDRON_MODULES={
                    "cauldron.site.astro": {
                        "frontend_root": str(fr),
                        "output_root": str(output),
                    }
                }
            ):
                result = _run_check()
    assert "cauldron.site.astro.W113" in _ids(result)


def test_astro_binary_failing_does_not_emit_I120(tmp_path: Path):
    fr = _make_frontend(tmp_path)
    _make_astro_bin(fr)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            with override_settings(
                CAULDRON_MODULES={
                    "cauldron.site.astro": {
                        "frontend_root": str(fr),
                        "output_root": str(output),
                    }
                }
            ):
                result = _run_check()
    assert "cauldron.site.astro.I120" not in _ids(result)


def test_w113_message_instructs_install(tmp_path: Path):
    fr = _make_frontend(tmp_path)
    _make_astro_bin(fr)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            with override_settings(
                CAULDRON_MODULES={
                    "cauldron.site.astro": {
                        "frontend_root": str(fr),
                        "output_root": str(output),
                    }
                }
            ):
                result = _run_check()
    w113 = next(m for m in result if m.id == "cauldron.site.astro.W113")
    assert "./install" in w113.msg


# ---------------------------------------------------------------------------
# I121 — Astro version
# ---------------------------------------------------------------------------


def test_runnable_astro_emits_I121(tmp_path: Path):
    fr = _make_frontend(tmp_path)
    _make_astro_bin(fr)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="4.16.0\n", stderr="")
            with override_settings(
                CAULDRON_MODULES={
                    "cauldron.site.astro": {
                        "frontend_root": str(fr),
                        "output_root": str(output),
                    }
                }
            ):
                result = _run_check()
    assert "cauldron.site.astro.I121" in _ids(result)
    i121 = next(m for m in result if m.id == "cauldron.site.astro.I121")
    assert "4.16.0" in i121.msg


# ---------------------------------------------------------------------------
# I120 — healthy configuration (all checks pass)
# ---------------------------------------------------------------------------


def test_healthy_config_emits_I120(tmp_path: Path):
    fr = _make_frontend(tmp_path)
    _make_astro_bin(fr)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="4.16.0\n", stderr="")
            with override_settings(
                CAULDRON_MODULES={
                    "cauldron.site.astro": {
                        "frontend_root": str(fr),
                        "output_root": str(output),
                    }
                }
            ):
                result = _run_check()
    assert "cauldron.site.astro.I120" in _ids(result)
    error_ids = {m.id for m in result if m.id.startswith("cauldron.site.astro.E")}
    assert not error_ids


def test_healthy_config_has_no_errors_or_warnings(tmp_path: Path):
    """Healthy config produces only I121 + I120 — no errors or warnings."""
    fr = _make_frontend(tmp_path)
    _make_astro_bin(fr)
    output = tmp_path / "out"
    with patch("cauldron_site_astro.checks.shutil.which", return_value="/usr/bin/npm"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="4.16.0\n", stderr="")
            with override_settings(
                CAULDRON_MODULES={
                    "cauldron.site.astro": {
                        "frontend_root": str(fr),
                        "output_root": str(output),
                    }
                }
            ):
                result = _run_check()
    ids = _ids(result)
    assert ids == {"cauldron.site.astro.I121", "cauldron.site.astro.I120"}
