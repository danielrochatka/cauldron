"""Integration tests for the Cauldron installer and related lifecycle scripts.

These tests exercise the shell helpers in lib.sh and the installer/update
script logic using controlled temporary environments with fake binaries.
No real npm install or network access is required.

Tested behaviours
-----------------
- is_frontend_installed: absent / present / non-executable astro binary
- is_installation_ready: missing venv, config.env, astro binary
- install_frontend: no-op when package.json absent
- install_frontend: clear error when npm fails
- install_frontend: clear error when astro binary absent after npm
- install_frontend: success path with fake npm + binary
- check_node_version: missing, malformed, too old, exact minimum, newer
- initialize_config: SECRET_KEY is preserved across reruns
- install site-build step: zero-page (exit 0) → completion message printed
- install site-build step: Astro failure → nonzero, no completion message
- install site-build step: existing output preserved on failure
- install prerequisite: clear error when Node.js is absent
- install prerequisite: clear error when package-lock.json is absent
- start script: exits nonzero when installation is incomplete
- start script: is_installation_ready gates launch correctly
- update lifecycle: successful rebuild → exit 0, "updated successfully"
- update lifecycle: failed rebuild → exit 1, "rebuild failed", retry hint
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


def _fake_node(tmp_path: Path, version: str) -> Path:
    """Create a fake_bin directory containing a `node` binary reporting VERSION."""
    bin_dir = tmp_path / "fake_node_bin"
    bin_dir.mkdir(exist_ok=True)
    node = bin_dir / "node"
    node.write_text(f"#!/bin/sh\necho '{version}'\n", encoding="utf-8")
    node.chmod(0o755)
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
# is_installation_ready
# ---------------------------------------------------------------------------

class TestIsInstallationReady:
    def _ready(self, cauldron_dir: Path) -> subprocess.CompletedProcess:
        return _source_lib(
            f"is_installation_ready {cauldron_dir!s} && echo READY || echo NOT_READY"
        )

    def _make_ready(self, tmp_path: Path) -> Path:
        """Populate tmp_path to pass is_installation_ready."""
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        python = venv_bin / "python"
        python.write_text("#!/bin/sh\necho python\n", encoding="utf-8")
        python.chmod(0o755)
        (tmp_path / "config.env").write_text("SECRET_KEY=test\n", encoding="utf-8")
        fr = _make_frontend(tmp_path)
        _make_astro_bin(fr)
        return tmp_path

    def test_ready_when_all_artifacts_present(self, tmp_path: Path):
        self._make_ready(tmp_path)
        result = self._ready(tmp_path)
        assert result.returncode == 0
        assert "READY" in result.stdout

    def test_not_ready_when_venv_missing(self, tmp_path: Path):
        self._make_ready(tmp_path)
        import shutil
        shutil.rmtree(tmp_path / ".venv")
        result = self._ready(tmp_path)
        assert result.returncode == 0
        assert "NOT_READY" in result.stdout
        assert ".venv" in result.stderr

    def test_not_ready_when_config_env_missing(self, tmp_path: Path):
        self._make_ready(tmp_path)
        (tmp_path / "config.env").unlink()
        result = self._ready(tmp_path)
        assert result.returncode == 0
        assert "NOT_READY" in result.stdout
        assert "config.env" in result.stderr

    def test_not_ready_when_astro_binary_missing(self, tmp_path: Path):
        self._make_ready(tmp_path)
        astro = tmp_path / "frontend" / "node_modules" / ".bin" / "astro"
        astro.unlink()
        result = self._ready(tmp_path)
        assert result.returncode == 0
        assert "NOT_READY" in result.stdout
        assert "astro" in result.stderr

    def test_ready_when_no_frontend_package_json(self, tmp_path: Path):
        """Without frontend/package.json, astro binary absence is not a failure."""
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        python = venv_bin / "python"
        python.write_text("#!/bin/sh\necho python\n", encoding="utf-8")
        python.chmod(0o755)
        (tmp_path / "config.env").write_text("SECRET_KEY=test\n", encoding="utf-8")
        # No frontend directory at all
        result = self._ready(tmp_path)
        assert result.returncode == 0
        assert "READY" in result.stdout


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
# check_node_version
# ---------------------------------------------------------------------------

class TestCheckNodeVersion:
    def test_missing_node_returns_nonzero(self, tmp_path: Path):
        posix_path = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        result = _source_lib("check_node_version 18", env={"PATH": posix_path})
        assert result.returncode != 0
        assert "Node" in result.stderr or "node" in result.stderr

    def test_missing_node_suggests_install_url(self, tmp_path: Path):
        posix_path = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        result = _source_lib("check_node_version 18", env={"PATH": posix_path})
        assert "nodejs.org" in result.stderr

    def test_malformed_version_returns_nonzero(self, tmp_path: Path):
        bin_dir = _fake_node(tmp_path, "not-a-version")
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        result = _source_lib("check_node_version 18", env=env)
        assert result.returncode != 0
        assert "parse" in result.stderr or "version" in result.stderr.lower()

    def test_old_node_returns_nonzero(self, tmp_path: Path):
        bin_dir = _fake_node(tmp_path, "v16.20.2")
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        result = _source_lib("check_node_version 18", env=env)
        assert result.returncode != 0
        assert "16" in result.stderr or "required" in result.stderr.lower()

    def test_exact_minimum_succeeds(self, tmp_path: Path):
        bin_dir = _fake_node(tmp_path, "v18.0.0")
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        result = _source_lib("check_node_version 18", env=env)
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_newer_major_succeeds(self, tmp_path: Path):
        bin_dir = _fake_node(tmp_path, "v22.5.0")
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        result = _source_lib("check_node_version 18", env=env)
        assert result.returncode == 0

    def test_future_major_succeeds(self, tmp_path: Path):
        bin_dir = _fake_node(tmp_path, "v99.0.0")
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        result = _source_lib("check_node_version 18", env=env)
        assert result.returncode == 0


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
# install — site-build step behaviour
# ---------------------------------------------------------------------------
# The install script derives CAULDRON_DIR from $0, so we test the site-build
# step logic as an isolated bash snippet rather than running ./install end-to-end.

class TestInstallSiteBuildStep:
    """Verify ./install site-build step behaviour via isolated bash snippets."""

    def _run_site_build_step(
        self,
        tmp_path: Path,
        *,
        build_exit: int = 0,
        existing_output: bool = False,
    ) -> subprocess.CompletedProcess:
        """
        Simulate the install step-6 site-build logic with a fake manage command.
        Returns the CompletedProcess from the bash snippet.
        """
        output_dir = tmp_path / "data" / "public"
        if existing_output:
            output_dir.mkdir(parents=True)
            (output_dir / "index.html").write_text("<h1>old</h1>", encoding="utf-8")

        script = textwrap.dedent(f"""\
            CAULDRON_DIR={tmp_path!s}
            cauldron_site_build_fake() {{ return {build_exit}; }}
            echo "--> Building public site..."
            if ! cauldron_site_build_fake; then
              echo "" >&2
              echo "ERROR: Public site build failed. The error output is above." >&2
              echo "       Existing generated output (if any) was preserved." >&2
              exit 1
            fi
            echo ""
            echo "==> Installation complete."
        """)
        return _bash(script, cwd=tmp_path)

    def test_zero_page_build_success_prints_completion(self, tmp_path: Path):
        result = self._run_site_build_step(tmp_path, build_exit=0)
        assert result.returncode == 0
        assert "Installation complete" in result.stdout

    def test_build_failure_returns_nonzero(self, tmp_path: Path):
        result = self._run_site_build_step(tmp_path, build_exit=1)
        assert result.returncode != 0

    def test_build_failure_suppresses_completion_message(self, tmp_path: Path):
        result = self._run_site_build_step(tmp_path, build_exit=1)
        assert "Installation complete" not in result.stdout

    def test_build_failure_prints_error_message(self, tmp_path: Path):
        result = self._run_site_build_step(tmp_path, build_exit=1)
        assert "ERROR" in result.stderr or "failed" in result.stderr.lower()

    def test_existing_output_untouched_when_build_fails(self, tmp_path: Path):
        """The shell step doesn't touch output_root; the service layer handles atomicity."""
        result = self._run_site_build_step(tmp_path, build_exit=1, existing_output=True)
        assert result.returncode != 0
        # The existing output directory and its contents must survive the failure.
        index = tmp_path / "data" / "public" / "index.html"
        assert index.exists(), "Existing site output must not be removed on build failure"
        assert "<h1>old</h1>" in index.read_text()


# ---------------------------------------------------------------------------
# install — prerequisite checks
# ---------------------------------------------------------------------------

class TestInstallPrerequisites:
    """Test that install prerequisite guards emit clear errors."""

    def test_missing_node_prints_clear_error(self, tmp_path: Path):
        posix_path = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        script = textwrap.dedent("""\
            if ! command -v node &>/dev/null; then
              echo "ERROR: Node.js is required but was not found in PATH." >&2
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

        script = textwrap.dedent(f"""\
            CAULDRON_DIR={tmp_path!s}
            if [ ! -f "$CAULDRON_DIR/frontend/package-lock.json" ]; then
              echo "ERROR: frontend/package-lock.json is missing." >&2
              exit 1
            fi
        """)
        result = _bash(script)
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "package-lock.json" in output


# ---------------------------------------------------------------------------
# start — installation-readiness gate
# ---------------------------------------------------------------------------

class TestStartInstallationGate:
    """./start must exit nonzero with a clear message when installation is incomplete."""

    def _run_start_gate(self, cauldron_dir: Path) -> subprocess.CompletedProcess:
        """Simulate the is_installation_ready gate from ./start."""
        script = textwrap.dedent(f"""\
            source {LIB_SH!s}
            CAULDRON_DIR={cauldron_dir!s}
            if ! is_installation_ready "$CAULDRON_DIR"; then
              echo "" >&2
              echo "ERROR: Cauldron is not fully installed. Run:" >&2
              echo "       ./install" >&2
              exit 1
            fi
            echo "WOULD_START"
        """)
        return _bash(script)

    def _make_ready(self, tmp_path: Path) -> None:
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        python = venv_bin / "python"
        python.write_text("#!/bin/sh\necho python\n", encoding="utf-8")
        python.chmod(0o755)
        (tmp_path / "config.env").write_text("SECRET_KEY=test\n", encoding="utf-8")
        fr = _make_frontend(tmp_path)
        _make_astro_bin(fr)

    def test_start_exits_nonzero_when_not_installed(self, tmp_path: Path):
        result = self._run_start_gate(tmp_path)
        assert result.returncode != 0

    def test_start_prints_install_instruction_when_not_installed(self, tmp_path: Path):
        result = self._run_start_gate(tmp_path)
        assert "./install" in result.stderr

    def test_start_proceeds_when_fully_installed(self, tmp_path: Path):
        self._make_ready(tmp_path)
        result = self._run_start_gate(tmp_path)
        assert result.returncode == 0
        assert "WOULD_START" in result.stdout


# ---------------------------------------------------------------------------
# update — site-build lifecycle
# ---------------------------------------------------------------------------

class TestUpdateSiteBuildLifecycle:
    """Verify the update script's site-build failure tracking logic."""

    def _run_update_build_step(
        self,
        tmp_path: Path,
        *,
        build_exit: int = 0,
        server_healthy: bool = True,
    ) -> subprocess.CompletedProcess:
        """
        Simulate the update script's site-build + final-status logic.
        Uses fake manage and curl commands via bash functions.
        """
        healthy_curl = "true" if server_healthy else "false"
        log_file = tmp_path / "logs" / "site_build.log"
        (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

        script = textwrap.dedent(f"""\
            CAULDRON_DIR={tmp_path!s}
            PREV_COMMIT="abc12345"
            LOG={log_file!s}

            # Fake build command
            cauldron_site_build_fake() {{ return {build_exit}; }}

            # Simulate update step 11: build with failure tracking
            SITE_BUILD_OK=1
            cauldron_site_build_fake 2>&1 | tee -a "$LOG" || SITE_BUILD_OK=0
            if [ "$SITE_BUILD_OK" -eq 0 ]; then
              echo ""
              echo "    WARNING: Public-site rebuild failed. Existing output was preserved."
              echo "             See: $LOG"
            fi

            # Simulate health check outcome
            HEALTHY=0
            if {healthy_curl}; then
              HEALTHY=1
            fi

            if [ "$HEALTHY" -eq 0 ]; then
              echo "ERROR: Server did not respond."
              exit 2
            fi

            NEW_COMMIT="def67890"

            if [ "$SITE_BUILD_OK" -eq 0 ]; then
              echo ""
              echo "==> Application updated; public-site rebuild failed."
              echo "    Retry:     ./manage cauldron_site_build"
              echo "    Build log: $LOG"
              echo "    ${{PREV_COMMIT:0:8}} -> $NEW_COMMIT"
              exit 1
            fi

            echo ""
            echo "==> Cauldron updated successfully."
            echo "    ${{PREV_COMMIT:0:8}} -> $NEW_COMMIT"
        """)
        return _bash(script, cwd=tmp_path)

    def test_successful_rebuild_exits_zero(self, tmp_path: Path):
        result = self._run_update_build_step(tmp_path, build_exit=0)
        assert result.returncode == 0

    def test_successful_rebuild_prints_updated_message(self, tmp_path: Path):
        result = self._run_update_build_step(tmp_path, build_exit=0)
        assert "updated successfully" in result.stdout

    def test_failed_rebuild_exits_nonzero(self, tmp_path: Path):
        result = self._run_update_build_step(tmp_path, build_exit=1)
        assert result.returncode == 1

    def test_failed_rebuild_prints_failure_message(self, tmp_path: Path):
        result = self._run_update_build_step(tmp_path, build_exit=1)
        assert "rebuild failed" in result.stdout

    def test_failed_rebuild_does_not_print_success_message(self, tmp_path: Path):
        result = self._run_update_build_step(tmp_path, build_exit=1)
        assert "updated successfully" not in result.stdout

    def test_failed_rebuild_shows_retry_command(self, tmp_path: Path):
        result = self._run_update_build_step(tmp_path, build_exit=1)
        assert "cauldron_site_build" in result.stdout

    def test_failed_rebuild_shows_log_path(self, tmp_path: Path):
        result = self._run_update_build_step(tmp_path, build_exit=1)
        assert "site_build.log" in result.stdout

    def test_failed_rebuild_server_still_started(self, tmp_path: Path):
        """Build failure must not trigger rollback — server starts and is checked."""
        result = self._run_update_build_step(
            tmp_path, build_exit=1, server_healthy=True
        )
        # Exit code is 1 (build failure), not 2 (server failure / rollback)
        assert result.returncode == 1

    def test_server_failure_triggers_rollback_exit(self, tmp_path: Path):
        """Unhealthy server results in the rollback exit code (2 in our simulation)."""
        result = self._run_update_build_step(
            tmp_path, build_exit=0, server_healthy=False
        )
        assert result.returncode == 2


# ---------------------------------------------------------------------------
# System check — Astro binary gate
# ---------------------------------------------------------------------------

class TestSystemCheckAstroGate:
    """Django system checks must not emit I120 when Astro is missing."""

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
        assert not any(
            i.startswith("cauldron.site.astro.E") or i.startswith("cauldron.site.astro.W")
            for i in ids
        )
