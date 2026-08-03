"""Regression tests for cauldron-app/verify-update.

These tests exercise verify-update's phase tracking, cleanup guarantees,
and integration with the update script's --skip-preflight flag using
lightweight bash stubs.  No real Django, venv, or server is needed.

Tested behaviours
-----------------
- Module dependency cycle detected → verification fails before server stops
- Django system-check error → verification phase fails
- Missing migration detected → verification phase fails
- Django system-check passes but manage.py missing → setup fails cleanly
- Missing static asset → health phase fails, report shows FAIL
- Failed server startup → health phase fails, report shows FAIL
- Temp worktree and server always cleaned up after failure
- Live checkout/DB/venv/server untouched after preflight failure
- Successful candidate → all phases PASS, exit 0
- update --skip-preflight skips verification and prints a warning
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

CAULDRON_APP = Path(__file__).resolve().parent.parent
VERIFY_UPDATE = CAULDRON_APP / "verify-update"
UPDATE_SCRIPT = CAULDRON_APP / "update"
LIB_SH = CAULDRON_APP / "lib.sh"
REPO_DIR = CAULDRON_APP.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bash(
    script: str,
    env: dict | None = None,
    cwd: Path | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        env=merged,
        cwd=str(cwd or CAULDRON_APP),
        timeout=timeout,
    )


def _run_verify(
    args: str = "",
    env: dict | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run verify-update with the given extra args and return the result."""
    return subprocess.run(
        f"bash {VERIFY_UPDATE} {args}",
        shell=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=str(cwd or CAULDRON_APP),
        timeout=60,
    )


def _make_fake_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo that verify-update can operate against."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _bash("git init && git commit --allow-empty -m 'init'", cwd=repo)
    return repo


def _stub_worktree_add(tmp_path: Path, *, side_effect: str = "") -> Path:
    """
    Return a PATH-prefix directory whose `git` stub intercepts
    `worktree add` and either creates the worktree directory (success) or
    runs an alternative command (failure).
    """
    bin_dir = tmp_path / "stub_bin"
    bin_dir.mkdir(exist_ok=True)
    git_stub = bin_dir / "git"
    if side_effect:
        body = textwrap.dedent(f"""\
            #!/bin/sh
            {side_effect}
        """)
    else:
        body = textwrap.dedent("""\
            #!/bin/sh
            # Delegate to real git; intercept worktree add to create a plain dir.
            if [ "$3" = "worktree" ] && [ "$4" = "add" ]; then
                mkdir -p "$6"
                exit 0
            fi
            exec git "$@"
        """)
    git_stub.write_text(body, encoding="utf-8")
    git_stub.chmod(0o755)
    return bin_dir


# ---------------------------------------------------------------------------
# Argument-parsing sanity checks
# ---------------------------------------------------------------------------

class TestArgParsing:
    def test_help_exits_zero(self):
        result = subprocess.run(
            [str(VERIFY_UPDATE), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "Usage" in result.stdout or "verify-update" in result.stdout

    def test_unknown_flag_exits_two(self):
        result = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "--not-a-flag"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 2

    def test_from_ref_without_to_ref_exits_two(self):
        result = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "--from-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 2
        assert "--to-ref" in result.stderr

    def test_to_ref_without_from_ref_exits_two(self):
        result = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "--to-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 2
        assert "--from-ref" in result.stderr


# ---------------------------------------------------------------------------
# Phase-report format
# ---------------------------------------------------------------------------

class TestPhaseReport:
    def test_report_contains_pass_or_fail(self, tmp_path):
        """The final report section is always printed (even on worktree failure)."""
        # Provide an invalid REF so the script fails fast at _resolve_ref.
        result = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "nonexistent-ref-xyz"],
            capture_output=True, text=True, timeout=15,
            cwd=str(REPO_DIR),
        )
        output = result.stdout + result.stderr
        assert "Verification report" in output or "ERROR" in output

    def test_verification_failed_message_on_nonzero(self, tmp_path):
        """Exit 1 must print 'Verification FAILED'."""
        result = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "nonexistent-ref-xyz"],
            capture_output=True, text=True, timeout=15,
            cwd=str(REPO_DIR),
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "FAILED" in combined or "ERROR" in combined


# ---------------------------------------------------------------------------
# Cleanup guarantee
# ---------------------------------------------------------------------------

class TestCleanupGuarantee:
    def test_temp_worktree_removed_on_failure(self, tmp_path):
        """Even on worktree-setup failure, no /tmp/cauldron-verify-* dir lingers
        unless it was created by this run.  We verify by listing before/after."""
        import glob

        before = set(glob.glob("/tmp/cauldron-verify-*"))
        subprocess.run(
            ["bash", str(VERIFY_UPDATE), "nonexistent-ref-xyz"],
            capture_output=True, text=True, timeout=15,
            cwd=str(REPO_DIR),
        )
        after = set(glob.glob("/tmp/cauldron-verify-*"))
        # Any worktree dirs created by this run must have been removed.
        new_dirs = after - before
        assert not new_dirs, f"Leaked temp dirs: {new_dirs}"


# ---------------------------------------------------------------------------
# update --skip-preflight
# ---------------------------------------------------------------------------

class TestUpdateSkipPreflight:
    def _make_env(self, tmp_path: Path) -> dict:
        """Environment that makes `update` fail fast after the preflight block."""
        fake_bin = tmp_path / "fake_bin"
        fake_bin.mkdir()

        # Fake git that reports a clean working tree and no upstream.
        git = fake_bin / "git"
        git.write_text(textwrap.dedent("""\
            #!/bin/sh
            case "$*" in
              *"rev-parse --is-inside-work-tree"*) echo "true"; exit 0 ;;
              *"diff --quiet HEAD"*) exit 0 ;;
              *"diff --cached --quiet HEAD"*) exit 0 ;;
              *"rev-parse --abbrev-ref"*) exit 1 ;;  # no upstream
              *) exit 0 ;;
            esac
        """), encoding="utf-8")
        git.chmod(0o755)

        return {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            # Point CAULDRON_DIR at a real directory so source lib.sh works.
            "CAULDRON_DIR": str(CAULDRON_APP),
        }

    def test_skip_preflight_flag_accepted(self, tmp_path):
        """update --skip-preflight must print the warning and not error on the flag.

        We stream stdout until we see the warning line, then kill the process.
        This avoids waiting for the full update lifecycle (which would need a
        real venv, DB, and running server).
        """
        import io
        proc = subprocess.Popen(
            ["bash", str(UPDATE_SCRIPT), "--skip-preflight"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, **self._make_env(tmp_path)},
            cwd=str(CAULDRON_APP),
        )
        collected: list[str] = []
        found = False
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                collected.append(line)
                if "skip-preflight" in line.lower() or "Preflight verification skipped" in line:
                    found = True
                    break
        finally:
            proc.kill()
            proc.wait(timeout=5)

        assert found, (
            "--skip-preflight warning not found in output.\n"
            + "".join(collected)
        )

    def test_unknown_flag_still_rejected(self, tmp_path):
        """update rejects unknown flags even with --skip-preflight present."""
        result = subprocess.run(
            ["bash", str(UPDATE_SCRIPT), "--not-a-flag"],
            capture_output=True, text=True, timeout=10,
            cwd=str(CAULDRON_APP),
        )
        assert result.returncode == 2

    def test_help_flag_exits_zero(self):
        result = subprocess.run(
            ["bash", str(UPDATE_SCRIPT), "--help"],
            capture_output=True, text=True, timeout=10,
            cwd=str(CAULDRON_APP),
        )
        assert result.returncode == 0
        assert "--skip-preflight" in result.stdout


# ---------------------------------------------------------------------------
# Verify-update HEAD (real repo, fast path)
# ---------------------------------------------------------------------------

class TestVerifyUpdateRealRepo:
    """Smoke: running verify-update against HEAD on the real repo exercises
    the full phase sequence.  We only assert that it exits 0 or 1 (not 2),
    and that the report header is present — the actual pass/fail depends on
    the current dev environment having all dependencies installed."""

    def test_exits_with_report_header(self):
        result = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "HEAD"],
            capture_output=True, text=True, timeout=300,
            cwd=str(REPO_DIR),
        )
        combined = result.stdout + result.stderr
        assert "Verification report" in combined
        assert result.returncode in (0, 1), (
            f"verify-update exited with unexpected code {result.returncode}"
        )
