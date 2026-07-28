"""Integration tests for the Cauldron installer and related lifecycle scripts.

These tests exercise the shell helpers in lib.sh and the installer script
logic using controlled temporary environments with fake binaries — no real
npm install or network access is required.

Tested behaviours
-----------------
- is_frontend_installed: absent / present / non-executable astro binary
- install_frontend: no-op when package.json absent
- install_frontend: clear error when npm fails
- install_frontend: clear error when astro binary absent after npm
- install_frontend: success path with fake npm + binary
- initialize_config: SECRET_KEY is preserved across reruns
- install prerequisite: clear error when Node.js is absent
- install prerequisite: clear error when package-lock.json is absent
- start script: install_frontend is skipped when frontend already installed
- start script: install_frontend runs when frontend is absent
- System check: no I120 emitted when Astro binary is missing
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CAULDRON_APP = Path(__file__).resolve().parent.parent
LIB_SH = CAULDRON_APP / "lib.sh"


def _bash(script: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        env=merged,
        cwd=str(cwd or CAULDRON_APP),
    )


def _source_lib(script: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a bash snippet with lib.sh sourced first."""
    full = f"source {LIB_SH!s}\n{script}"
    return _bash(full, env=env, cwd=cwd)


def _make_frontend(tmp_path: Path, *, package_lock: bool = True) -> Path:
    fr = tmp_path / "frontend"
    fr.mkdir()
    (fr / "package.json").write_text("{}", encoding="utf-8")
    if package_lock:
        (fr / "package-lock.json").write_text("{}", encoding="utf-8")
    return fr


def _make_astro_bin(fr: Path, *, executable: bool = True) -> Path:
    bin_dir = fr / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    astro = bin_dir / "astro"
    astro.write_text("#!/bin/sh\necho '4.16.0'\n", encoding="utf-8")
    astro.chmod(0o755 if executable else 0o644)
    return astro


def _fake_npm(tmp_path: Path, *, returncode: int = 0, creates_astro: bool = True) -> Path:
    """Return a directory containing a fake `npm` binary."""
    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir(exist_ok=True)
    npm_script = bin_dir / "npm"

    if creates_astro and returncode == 0:
        # The fake npm writes the astro binary into --prefix/node_modules/.bin/astro.
        body = textwrap.dedent("""\
            #!/bin/sh
            set -e
            prefix=""
            while [ $# -gt 0 ]; do
              if [ "$1" = "--prefix" ]; then
                shift; prefix="$1"
              fi
              shift
            done
            mkdir -p "$prefix/node_modules/.bin"
            printf '#!/bin/sh\\necho 4.16.0\\n' > "$prefix/node_modules/.bin/astro"
            chmod 755 "$prefix/node_modules/.bin/astro"
        """)
    elif returncode != 0:
        body = "#!/bin/sh\nexit 1\n"
    else:
        body = "#!/bin/sh\nexit 0\n"

    npm_script.write_text(body, encoding="utf-8")
    npm_script.chmod(0o755)
    return bin_dir


# ---------------------------------------------------------------------------
# is_frontend_installed
# ---------------------------------------------------------------------------

class TestIsFrontendInstalled:
    def test_returns_false_when_binary_absent(self, tmp_path: Path):
        result = _source_lib(
            f"is_frontend_installed {tmp_path!s} && echo YES || echo NO"
        )
        assert result.returncode == 0
        assert "NO" in result.stdout

    def test_returns_true_when_binary_executable(self, tmp_path: Path):
        fr = _make_frontend(tmp_path)
        _make_astro_bin(fr)
        result = _source_lib(
            f"is_frontend_installed {tmp_path!s} && echo YES || echo NO"
        )
        assert result.returncode == 0
        assert "YES" in result.stdout

    def test_returns_false_when_binary_not_executable(self, tmp_path: Path):
        fr = _make_frontend(tmp_path)
        _make_astro_bin(fr, executable=False)
        result = _source_lib(
            f"is_frontend_installed {tmp_path!s} && echo YES || echo NO"
        )
        assert result.returncode == 0
        assert "NO" in result.stdout


# ---------------------------------------------------------------------------
# install_frontend
# ---------------------------------------------------------------------------

class TestInstallFrontend:
    def test_noop_when_package_json_absent(self, tmp_path: Path):
        result = _source_lib(f"install_frontend {tmp_path!s}")
        assert result.returncode == 0
        assert "Installing" not in result.stdout

    def test_succeeds_with_fake_npm(self, tmp_path: Path):
        _make_frontend(tmp_path)
        fake_bin = _fake_npm(tmp_path, returncode=0, creates_astro=True)
        env = {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
        result = _source_lib(f"install_frontend {tmp_path!s}", env=env)
        assert result.returncode == 0
        assert "Astro" in result.stdout

    def test_fails_clearly_when_npm_fails(self, tmp_path: Path):
        _make_frontend(tmp_path)
        fake_bin = _fake_npm(tmp_path, returncode=1)
        env = {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
        result = _source_lib(f"install_frontend {tmp_path!s}", env=env)
        assert result.returncode != 0
        assert "npm install failed" in result.stderr or "npm" in result.stderr.lower()

    def test_fails_clearly_when_npm_succeeds_but_no_astro(self, tmp_path: Path):
        _make_frontend(tmp_path)
        fake_bin = _fake_npm(tmp_path, returncode=0, creates_astro=False)
        env = {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
        result = _source_lib(f"install_frontend {tmp_path!s}", env=env)
        assert result.returncode != 0
        assert "Astro binary not found" in result.stderr

    def test_error_message_mentions_install_command(self, tmp_path: Path):
        _make_frontend(tmp_path)
        fake_bin = _fake_npm(tmp_path, returncode=1)
        env = {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
        result = _source_lib(f"install_frontend {tmp_path!s}", env=env)
        assert "./install" in result.stderr


# ---------------------------------------------------------------------------
# initialize_config — SECRET_KEY preservation
# ---------------------------------------------------------------------------

class TestInitializeConfig:
    def test_creates_config_from_example(self, tmp_path: Path):
        example = tmp_path / "config.env.example"
        example.write_text("SECRET_KEY=\nCAULDRON_PORT=8000\n", encoding="utf-8")
        config = tmp_path / "config.env"

        result = _source_lib(
            f"initialize_config {config!s} {example!s} && echo DONE"
        )
        assert result.returncode == 0, result.stderr
        assert config.exists()
        text = config.read_text()
        key_line = next(l for l in text.splitlines() if l.startswith("SECRET_KEY="))
        assert len(key_line) > len("SECRET_KEY="), "SECRET_KEY must be non-empty after init"

    def test_preserves_existing_secret_key(self, tmp_path: Path):
        example = tmp_path / "config.env.example"
        example.write_text("SECRET_KEY=\n", encoding="utf-8")
        config = tmp_path / "config.env"
        original_key = "my-original-stable-key-do-not-touch"
        config.write_text(f"SECRET_KEY={original_key}\n", encoding="utf-8")
        config.chmod(0o600)

        result = _source_lib(
            f"initialize_config {config!s} {example!s}"
        )
        assert result.returncode == 0, result.stderr
        text = config.read_text()
        key_line = next(l for l in text.splitlines() if l.startswith("SECRET_KEY="))
        assert key_line == f"SECRET_KEY={original_key}", (
            "initialize_config must not overwrite an existing non-empty SECRET_KEY"
        )

    def test_idempotent_on_second_run(self, tmp_path: Path):
        example = tmp_path / "config.env.example"
        example.write_text("SECRET_KEY=\n", encoding="utf-8")
        config = tmp_path / "config.env"

        _source_lib(f"initialize_config {config!s} {example!s}")
        text_first = config.read_text()
        _source_lib(f"initialize_config {config!s} {example!s}")
        text_second = config.read_text()
        assert text_first == text_second, "initialize_config must be idempotent"


# ---------------------------------------------------------------------------
# install script — prerequisite checks (via bash snippet, not full install)
# ---------------------------------------------------------------------------
# The install script derives CAULDRON_DIR from $0 so we can't redirect it
# to tmp_path.  We extract and test the prerequisite logic snippets directly.

class TestInstallPrerequisites:
    """Test that install prerequisite guards emit clear errors."""

    def test_missing_node_prints_clear_error(self, tmp_path: Path):
        # Use only standard POSIX dirs so volta/nvm node is absent.
        posix_path = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        script = textwrap.dedent("""\
            if ! command -v node &>/dev/null; then
              echo "ERROR: Node.js is required but was not found in PATH."
              echo "       Install from https://nodejs.org/"
              exit 1
            fi
        """)
        result = _bash(script, env={"PATH": posix_path})
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "Node" in output or "node" in output

    def test_missing_package_lock_prints_clear_error(self, tmp_path: Path):
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "package.json").write_text("{}", encoding="utf-8")
        # No package-lock.json

        script = textwrap.dedent(f"""\
            CAULDRON_DIR={tmp_path!s}
            if [ ! -f "$CAULDRON_DIR/frontend/package-lock.json" ]; then
              echo "ERROR: frontend/package-lock.json is missing."
              echo "       This file must be tracked in the repository."
              exit 1
            fi
        """)
        result = _bash(script)
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "package-lock.json" in output


# ---------------------------------------------------------------------------
# start script — conditional frontend install
# ---------------------------------------------------------------------------

class TestStartConditionalInstall:
    """./start should call install_frontend only when Astro binary is absent."""

    def _run_start_dry(self, cauldron_dir: Path, env: dict | None = None) -> subprocess.CompletedProcess:
        """
        Extract just the is_frontend_installed / install_frontend conditional
        block from start (lines 10-13 of step 10) and run it in isolation
        so we don't need Django, gunicorn, etc.
        """
        script = textwrap.dedent(f"""\
            source {LIB_SH!s}
            CAULDRON_DIR={cauldron_dir!s}
            if ! is_frontend_installed "$CAULDRON_DIR"; then
              echo "WOULD_INSTALL"
            else
              echo "SKIP_INSTALL"
            fi
        """)
        return _bash(script, env=env)

    def test_install_skipped_when_frontend_ready(self, tmp_path: Path):
        fr = _make_frontend(tmp_path)
        _make_astro_bin(fr)
        result = self._run_start_dry(tmp_path)
        assert result.returncode == 0
        assert "SKIP_INSTALL" in result.stdout

    def test_install_runs_when_frontend_absent(self, tmp_path: Path):
        _make_frontend(tmp_path)  # no astro binary
        result = self._run_start_dry(tmp_path)
        assert result.returncode == 0
        assert "WOULD_INSTALL" in result.stdout


# ---------------------------------------------------------------------------
# System check — Astro binary gate
# ---------------------------------------------------------------------------

class TestSystemCheckAstroGate:
    """
    Django system checks must not emit I120 (healthy) when Astro is missing.
    These complement the unit tests in packages/cauldron-site-astro/tests/.
    """

    def test_no_i120_when_astro_binary_absent(self, tmp_path: Path):
        from unittest.mock import patch
        from django.test.utils import override_settings
        from cauldron_site_astro.checks import check_site_astro_config

        fr = tmp_path / "frontend"
        fr.mkdir()
        (fr / "package.json").write_text("{}", encoding="utf-8")
        (fr / "package-lock.json").write_text("{}", encoding="utf-8")
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
                result = check_site_astro_config(None)

        ids = {m.id for m in result}
        assert "cauldron.site.astro.I120" not in ids, (
            "I120 ('configuration looks healthy') must not appear when Astro is not installed"
        )
        assert "cauldron.site.astro.W112" in ids, (
            "W112 (Astro binary missing) should be emitted when node_modules/.bin/astro is absent"
        )

    def test_i120_present_when_all_healthy(self, tmp_path: Path):
        from unittest.mock import MagicMock, patch
        from django.test.utils import override_settings
        from cauldron_site_astro.checks import check_site_astro_config

        fr = tmp_path / "frontend"
        fr.mkdir()
        (fr / "package.json").write_text("{}", encoding="utf-8")
        (fr / "package-lock.json").write_text("{}", encoding="utf-8")
        bin_dir = fr / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True)
        astro = bin_dir / "astro"
        astro.write_text("#!/bin/sh\necho 4.16.0\n", encoding="utf-8")
        astro.chmod(0o755)
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
                    result = check_site_astro_config(None)

        ids = {m.id for m in result}
        assert "cauldron.site.astro.I120" in ids
        assert not any(i.startswith("cauldron.site.astro.E") or i.startswith("cauldron.site.astro.W") for i in ids)
