"""Tests for the cauldron_site_build management command."""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command


def _make_result(ok: bool, pages_built: int = 0, output_dir: str = "", error: str = "", build_log: str = ""):
    from cauldron_site_astro.service import BuildResult
    return BuildResult(
        ok=ok,
        pages_built=pages_built,
        output_dir=output_dir,
        error=error,
        build_log=build_log,
    )


def _run_build_command(**kwargs) -> tuple[str, str]:
    """Call the management command and return (stdout, stderr) as strings."""
    stdout = StringIO()
    stderr = StringIO()
    call_command("cauldron_site_build", stdout=stdout, stderr=stderr, **kwargs)
    return stdout.getvalue(), stderr.getvalue()


# ---------------------------------------------------------------------------
# Successful build
# ---------------------------------------------------------------------------


def test_successful_build_exits_0(tmp_path: Path):
    """A successful build does not raise SystemExit."""
    result = _make_result(ok=True, pages_built=3, output_dir=str(tmp_path / "out"))
    mock_svc = MagicMock()
    mock_svc.build.return_value = result

    with patch("cauldron_site_astro.service.get_build_service", return_value=mock_svc):
        # Should not raise
        stdout, stderr = _run_build_command()

    assert "3 page(s)" in stdout
    assert str(tmp_path / "out") in stdout


def test_successful_build_prints_success_message(tmp_path: Path):
    result = _make_result(ok=True, pages_built=1, output_dir="/var/www/site")
    mock_svc = MagicMock()
    mock_svc.build.return_value = result

    with patch("cauldron_site_astro.service.get_build_service", return_value=mock_svc):
        stdout, stderr = _run_build_command()

    assert "Site built successfully" in stdout
    assert "1 page(s)" in stdout
    assert "/var/www/site" in stdout


# ---------------------------------------------------------------------------
# Failed build
# ---------------------------------------------------------------------------


def test_failed_build_exits_1():
    """A failed build raises SystemExit(1)."""
    result = _make_result(ok=False, error="Astro build exited 1.")
    mock_svc = MagicMock()
    mock_svc.build.return_value = result

    with patch("cauldron_site_astro.service.get_build_service", return_value=mock_svc):
        with pytest.raises(SystemExit) as exc_info:
            _run_build_command()

    assert exc_info.value.code == 1


def test_failed_build_prints_error_to_stderr():
    """Error message is written to stderr on failure."""
    result = _make_result(ok=False, error="Astro build exited 2.")
    mock_svc = MagicMock()
    mock_svc.build.return_value = result

    stdout = StringIO()
    stderr = StringIO()

    with patch("cauldron_site_astro.service.get_build_service", return_value=mock_svc):
        with pytest.raises(SystemExit):
            call_command("cauldron_site_build", stdout=stdout, stderr=stderr)

    assert "Site build failed" in stderr.getvalue()
    assert "Astro build exited 2." in stderr.getvalue()


# ---------------------------------------------------------------------------
# --verbose flag
# ---------------------------------------------------------------------------


def test_verbose_flag_prints_build_log(tmp_path: Path):
    """With --verbose, the build log is printed to stdout."""
    result = _make_result(
        ok=True,
        pages_built=1,
        output_dir=str(tmp_path / "out"),
        build_log="[astro] Building...\n[astro] Done!\n",
    )
    mock_svc = MagicMock()
    mock_svc.build.return_value = result

    with patch("cauldron_site_astro.service.get_build_service", return_value=mock_svc):
        stdout, _ = _run_build_command(verbose=True)

    assert "[astro] Building..." in stdout
    assert "[astro] Done!" in stdout


def test_without_verbose_flag_does_not_print_build_log(tmp_path: Path):
    """Without --verbose, the build log is suppressed."""
    result = _make_result(
        ok=True,
        pages_built=1,
        output_dir=str(tmp_path / "out"),
        build_log="[astro] Building...\n[astro] Done!\n",
    )
    mock_svc = MagicMock()
    mock_svc.build.return_value = result

    with patch("cauldron_site_astro.service.get_build_service", return_value=mock_svc):
        stdout, _ = _run_build_command()

    assert "[astro] Building..." not in stdout


def test_verbose_with_no_build_log_does_not_crash(tmp_path: Path):
    """--verbose with an empty build log should not print extra output."""
    result = _make_result(ok=True, pages_built=0, output_dir=str(tmp_path / "out"), build_log="")
    mock_svc = MagicMock()
    mock_svc.build.return_value = result

    with patch("cauldron_site_astro.service.get_build_service", return_value=mock_svc):
        stdout, _ = _run_build_command(verbose=True)

    assert "Site built successfully" in stdout


# ---------------------------------------------------------------------------
# Building public site message
# ---------------------------------------------------------------------------


def test_building_message_always_printed():
    """The 'Building public site...' message is always emitted."""
    result = _make_result(ok=True, pages_built=0, output_dir="/tmp/out")
    mock_svc = MagicMock()
    mock_svc.build.return_value = result

    with patch("cauldron_site_astro.service.get_build_service", return_value=mock_svc):
        stdout, _ = _run_build_command()

    assert "Building public site" in stdout
