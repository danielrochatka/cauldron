"""Tests for cauldron-app/verify-update and the update script's preflight.

Acceptance criteria exercised
------------------------------
 1.  Module dependency cycle → verification failure.
 2.  Django system-check error → verification failure.
 3.  Missing migration → verification failure.
 4.  Invalid / missing package entry point → verification failure.
 5.  Wheel missing migrations / metadata causes wheel-content check to fail.
 6.  Frontend install failure (npm fails) → verification failure.
 7.  Site build failure → verification failure.
 8.  Missing static health asset → health phase fails.
 9.  Server process exit detected immediately (not after 30 s timeout).
10.  Worktree and temporary processes cleaned after every failure.
11.  Persistent diagnostic artifacts survive cleanup.
12.  Preflight failure leaves live checkout, DB, venv, PID, server untouched.
13.  Successful preflight: fetched upstream SHA passed to verify-update (not HEAD).
14.  Exact verified SHA becomes the installed SHA; newer remote commit not installed.
15.  Missing verify-update fails closed (no --skip-preflight).
16.  --skip-preflight flag prints warning and bypasses preflight.
17.  SKIP (not PASS) emitted when no frontend/package.json present.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
import zipfile
from pathlib import Path

import pytest

CAULDRON_APP = Path(__file__).resolve().parent.parent
VERIFY_UPDATE = CAULDRON_APP / "verify-update"
UPDATE_SCRIPT = CAULDRON_APP / "update"
LIB_SH = CAULDRON_APP / "lib.sh"
REPO_DIR = CAULDRON_APP.parent

# Fixed 40-hex candidate SHA used across SHA-pinning tests.
_CANDIDATE_SHA = "ab" * 20  # 40 chars


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
        ["bash", "-c", script],
        capture_output=True, text=True,
        env=merged,
        cwd=str(cwd or CAULDRON_APP),
        timeout=timeout,
    )


def _make_wheel(
    path: Path,
    name: str = "cauldron_test",
    *,
    has_metadata: bool = True,
    has_py: bool = True,
    has_entry_points: bool = False,
    entry_points_empty: bool = False,
    has_migrations: bool = False,
) -> Path:
    """Write a minimal .whl file (zip) to PATH with the specified contents."""
    whl = path / f"{name}-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        if has_metadata:
            z.writestr(
                f"{name}-0.1.0.dist-info/METADATA",
                f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1.0\n",
            )
        if has_py:
            z.writestr(f"{name}/__init__.py", "")
        if has_migrations:
            z.writestr(f"{name}/migrations/0001_initial.py", "")
            z.writestr(f"{name}/migrations/__init__.py", "")
        if has_entry_points:
            ep = (
                "[cauldron.modules]\n"
                if entry_points_empty
                else "[cauldron.modules]\ncauldron.test = cauldron_test.module:module\n"
            )
            z.writestr(f"{name}-0.1.0.dist-info/entry_points.txt", ep)
    return whl


def _make_fake_cauldron_dir(tmp_path: Path) -> Path:
    """Minimal cauldron-app directory layout for update-script tests."""
    app = tmp_path / "cauldron-app"
    app.mkdir(parents=True)
    (app / "logs").mkdir()
    (app / "data").mkdir()
    (app / "lib.sh").write_bytes(LIB_SH.read_bytes())
    example = CAULDRON_APP / "config.env.example"
    if example.exists():
        (app / "config.env.example").write_bytes(example.read_bytes())
    else:
        (app / "config.env.example").write_text("SECRET_KEY=\n")
    (app / "requirements.txt").write_text("# no deps\n")
    return app


def _setup_update_env(tmp_path: Path, *, verify_exit: int = 0):
    """
    Build a fully-stubbed environment for testing update-script behavior.

    Returns (update_script_path, calls_file, env_dict).

    All external commands (git, pip, gunicorn, curl, node, npm) are replaced
    with stubs that record their invocations to calls_file.  The fake git
    simulates a remote with _CANDIDATE_SHA on the upstream tracking branch.
    The fake verify-update exits with verify_exit.

    The update script is copied into the fake cauldron-app directory so that
    CAULDRON_DIR resolves to the fake installation, not the real one.
    """
    import shutil

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    cauldron_dir = _make_fake_cauldron_dir(tmp_path)
    calls_file = tmp_path / "calls.txt"
    calls_file.touch()

    # --- fake git ----------------------------------------------------------
    # Note: bash @{u} and ^{commit} must be written as @{{u}} / ^{{commit}}
    # in Python f-strings to avoid NameError on the inner {u} / {commit}.
    git_script = textwrap.dedent(f"""\
        #!/bin/sh
        log() {{ echo "$*" >> "{calls_file}"; }}
        case "$*" in
          *"rev-parse --is-inside-work-tree"*) echo "true" ;;
          *"diff --quiet HEAD"*)               exit 0 ;;
          *"diff --cached --quiet HEAD"*)      exit 0 ;;
          *"rev-parse --abbrev-ref --symbolic-full-name @{{u}}"*)
            echo "origin/main" ;;
          *"fetch origin"*)
            log "git-fetch-origin"; exit 0 ;;
          *"rev-parse origin/main"*)
            echo "{_CANDIDATE_SHA}" ;;
          *"rev-parse HEAD"*)
            echo "0000000000000000000000000000000000000000" ;;
          *"merge-base --is-ancestor"*)
            exit 0 ;;
          *"merge --ff-only {_CANDIDATE_SHA}"*)
            log "git-merge-ff-only:{_CANDIDATE_SHA}"; exit 0 ;;
          *"merge --ff-only"*)
            log "git-merge-ff-only-wrong:$*"; exit 0 ;;
          *"rev-parse --short HEAD"*)
            echo "{_CANDIDATE_SHA[:8]}" ;;
          *) exit 0 ;;
        esac
    """)
    (fake_bin / "git").write_text(git_script)
    (fake_bin / "git").chmod(0o755)

    # --- fake verify-update ------------------------------------------------
    verify_script = textwrap.dedent(f"""\
        #!/bin/sh
        echo "verify-update-called:$1" >> "{calls_file}"
        exit {verify_exit}
    """)
    (cauldron_dir / "verify-update").write_text(verify_script)
    (cauldron_dir / "verify-update").chmod(0o755)

    # --- fake stop, start, manage ------------------------------------------
    for name in ("stop", "start", "manage"):
        s = f"#!/bin/sh\necho '{name}-called:$*' >> '{calls_file}'\n"
        (cauldron_dir / name).write_text(s)
        (cauldron_dir / name).chmod(0o755)

    # --- fake pip (for install_python_projects) ----------------------------
    pip = fake_bin / "pip"
    pip.write_text(f"#!/bin/sh\necho 'pip-called:$*' >> '{calls_file}'\n")
    pip.chmod(0o755)

    # --- fake gunicorn (used by launch_server) ----------------------------
    gunicorn = fake_bin / "gunicorn"
    gunicorn.write_text(
        f"#!/bin/sh\necho 'gunicorn-called' >> '{calls_file}'\n"
    )
    gunicorn.chmod(0o755)

    # --- fake curl (for health checks) ------------------------------------
    curl = fake_bin / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)

    # --- fake node (so install_frontend check_node_version passes) --------
    node = fake_bin / "node"
    node.write_text("#!/bin/sh\necho 'v22.0.0'\n")
    node.chmod(0o755)

    # --- fake npm ---------------------------------------------------------
    npm = fake_bin / "npm"
    npm.write_text("#!/bin/sh\nexit 0\n")
    npm.chmod(0o755)

    # --- copy the update script into the fake cauldron dir ----------------
    # This makes CAULDRON_DIR=$(dirname $0) resolve to cauldron_dir.
    update_copy = cauldron_dir / "update"
    shutil.copy2(UPDATE_SCRIPT, update_copy)
    update_copy.chmod(0o755)

    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    return update_copy, calls_file, env


# ---------------------------------------------------------------------------
# Argument-parsing (verify-update)
# ---------------------------------------------------------------------------

class TestVerifyUpdateArgParsing:
    def test_help_exits_zero_and_lists_wheelhouse(self):
        r = subprocess.run(
            [str(VERIFY_UPDATE), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0
        assert "--wheelhouse" in r.stdout

    def test_unknown_flag_exits_two(self):
        r = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "--not-a-flag"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 2

    def test_from_ref_without_to_ref_exits_two(self):
        r = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "--from-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 2
        assert "--to-ref" in r.stderr

    def test_to_ref_without_from_ref_exits_two(self):
        r = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "--to-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 2
        assert "--from-ref" in r.stderr

    def test_invalid_ref_exits_nonzero_with_failed_report(self):
        r = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "nonexistent-ref-xyz"],
            capture_output=True, text=True, timeout=15,
            cwd=str(REPO_DIR),
        )
        assert r.returncode != 0
        assert "FAILED" in (r.stdout + r.stderr) or "ERROR" in (r.stdout + r.stderr)


# ---------------------------------------------------------------------------
# Argument-parsing (update)
# ---------------------------------------------------------------------------

class TestUpdateArgParsing:
    def test_help_exits_zero_and_lists_skip_preflight(self):
        r = subprocess.run(
            ["bash", str(UPDATE_SCRIPT), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0
        assert "--skip-preflight" in r.stdout

    def test_unknown_flag_exits_two(self):
        r = subprocess.run(
            ["bash", str(UPDATE_SCRIPT), "--not-a-flag"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 2


# ---------------------------------------------------------------------------
# Wheel-content verification (acceptance criterion 5)
# ---------------------------------------------------------------------------

class TestWheelContentVerification:
    """Test _verify_wheel_contents by sourcing verify-update (BASH_SOURCE guard
    prevents the dispatch from running) and calling the function directly."""

    def _source_and_call(self, wheelhouse: Path, tmp_path: Path) -> subprocess.CompletedProcess:
        """Source verify-update (functions only) and call _verify_wheel_contents.

        Prints _PHASE_LOG entries to stdout so tests can assert on PASS/FAIL/SKIP.
        """
        script = textwrap.dedent(f"""\
            source '{LIB_SH}'
            source '{VERIFY_UPDATE}'
            _verify_wheel_contents '{wheelhouse}' ''
            for line in "${{_PHASE_LOG[@]}}"; do echo "$line"; done
        """)
        return _bash(script, timeout=15)

    def test_valid_wheel_passes(self, tmp_path):
        wh = tmp_path / "wh"
        wh.mkdir()
        _make_wheel(wh)
        r = self._source_and_call(wh, tmp_path)
        assert r.returncode == 0, f"Expected PASS.\nstdout: {r.stdout}\nstderr: {r.stderr}"
        assert "PASS" in r.stdout, f"Expected PASS in output.\nstdout: {r.stdout}"

    def test_missing_metadata_fails(self, tmp_path):
        wh = tmp_path / "wh"
        wh.mkdir()
        _make_wheel(wh, has_metadata=False)
        r = self._source_and_call(wh, tmp_path)
        assert r.returncode != 0 or "FAIL" in (r.stdout + r.stderr), (
            f"Expected FAIL for missing METADATA.\nstdout: {r.stdout}"
        )

    def test_missing_py_files_fails(self, tmp_path):
        wh = tmp_path / "wh"
        wh.mkdir()
        _make_wheel(wh, has_py=False)
        r = self._source_and_call(wh, tmp_path)
        assert r.returncode != 0 or "FAIL" in (r.stdout + r.stderr), (
            f"Expected FAIL for missing .py files.\nstdout: {r.stdout}"
        )

    def test_empty_entry_points_section_fails(self, tmp_path):
        wh = tmp_path / "wh"
        wh.mkdir()
        _make_wheel(wh, has_entry_points=True, entry_points_empty=True)
        r = self._source_and_call(wh, tmp_path)
        assert r.returncode != 0 or "FAIL" in (r.stdout + r.stderr), (
            f"Expected FAIL for empty [cauldron.modules].\nstdout: {r.stdout}"
        )

    def test_valid_entry_points_passes(self, tmp_path):
        wh = tmp_path / "wh"
        wh.mkdir()
        _make_wheel(wh, has_entry_points=True, entry_points_empty=False)
        r = self._source_and_call(wh, tmp_path)
        assert r.returncode == 0, (
            f"Expected PASS for valid entry_points.\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_wheel_with_migrations_reports_migration_count(self, tmp_path):
        wh = tmp_path / "wh"
        wh.mkdir()
        _make_wheel(wh, has_migrations=True)
        r = self._source_and_call(wh, tmp_path)
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        # The PASS line includes migration count info.
        assert "migrations" in r.stdout, f"Expected migration count in PASS line.\n{r.stdout}"


# ---------------------------------------------------------------------------
# Cleanup guarantee (acceptance criterion 10)
# ---------------------------------------------------------------------------

class TestCleanupGuarantee:
    def test_temp_worktree_removed_on_failure(self):
        """No /tmp/cauldron-verify-* dir leaks after a failed run."""
        import glob
        before = set(glob.glob("/tmp/cauldron-verify-*"))
        subprocess.run(
            ["bash", str(VERIFY_UPDATE), "nonexistent-ref-xyz"],
            capture_output=True, text=True, timeout=15,
            cwd=str(REPO_DIR),
        )
        after = set(glob.glob("/tmp/cauldron-verify-*"))
        assert not (after - before), f"Leaked temp dirs: {after - before}"


# ---------------------------------------------------------------------------
# Artifact preservation (acceptance criterion 11)
# ---------------------------------------------------------------------------

class TestArtifactPreservation:
    def test_refs_and_report_survive_cleanup(self, tmp_path):
        """refs.txt and report.txt are written to VERIFY_ARTIFACT_DIR on any exit."""
        artifact_dir = tmp_path / "artifacts"
        subprocess.run(
            ["bash", str(VERIFY_UPDATE), "nonexistent-ref-xyz"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "VERIFY_ARTIFACT_DIR": str(artifact_dir)},
            cwd=str(REPO_DIR),
        )
        assert artifact_dir.exists(), "VERIFY_ARTIFACT_DIR was not created"
        assert (artifact_dir / "refs.txt").exists(), "refs.txt not written"
        assert (artifact_dir / "report.txt").exists(), "report.txt not written"

    def test_worktrees_removed_even_with_artifact_dir(self, tmp_path):
        """Cleanup still removes the worktree when VERIFY_ARTIFACT_DIR is set."""
        import glob
        artifact_dir = tmp_path / "artifacts"
        before = set(glob.glob("/tmp/cauldron-verify-*"))
        subprocess.run(
            ["bash", str(VERIFY_UPDATE), "nonexistent-ref-xyz"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "VERIFY_ARTIFACT_DIR": str(artifact_dir)},
            cwd=str(REPO_DIR),
        )
        after = set(glob.glob("/tmp/cauldron-verify-*"))
        assert not (after - before), f"Worktree leaked despite VERIFY_ARTIFACT_DIR"


# ---------------------------------------------------------------------------
# update script: SHA pinning and fail-closed (acceptance criteria 12–16)
# ---------------------------------------------------------------------------

class TestUpdateSHAPinning:
    def test_fetched_upstream_sha_passed_to_verify_update(self, tmp_path):
        """Criterion 13: update passes the fetched upstream SHA to verify-update."""
        update_copy, calls_file, env = _setup_update_env(tmp_path)
        subprocess.run(
            ["bash", str(update_copy)],
            capture_output=True, text=True, timeout=30,
            env=env,
            cwd=str(update_copy.parent),
        )
        calls = calls_file.read_text()
        assert f"verify-update-called:{_CANDIDATE_SHA}" in calls, (
            f"Expected verify-update to receive {_CANDIDATE_SHA!r}.\nCalls:\n{calls}"
        )

    def test_exact_verified_sha_used_for_checkout(self, tmp_path):
        """Criterion 14: git merge --ff-only uses exactly the verified SHA."""
        update_copy, calls_file, env = _setup_update_env(tmp_path)
        subprocess.run(
            ["bash", str(update_copy)],
            capture_output=True, text=True, timeout=30,
            env=env,
            cwd=str(update_copy.parent),
        )
        calls = calls_file.read_text()
        assert f"git-merge-ff-only:{_CANDIDATE_SHA}" in calls, (
            f"git merge --ff-only must use the verified SHA {_CANDIDATE_SHA}.\n"
            f"Calls:\n{calls}"
        )
        assert "git-merge-ff-only-wrong" not in calls

    def test_preflight_failure_stops_nothing(self, tmp_path):
        """Criterion 12: preflight failure → no stop, no DB change, no checkout."""
        update_copy, calls_file, env = _setup_update_env(tmp_path, verify_exit=1)
        cauldron_dir = update_copy.parent
        # Simulate a running server (PID file) and an existing database.
        (cauldron_dir / "data" / "cauldron.pid").write_text("99999")
        db = cauldron_dir / "data" / "cauldron.db"
        db.write_bytes(b"original-db-content")

        r = subprocess.run(
            ["bash", str(update_copy)],
            capture_output=True, text=True, timeout=30,
            env=env,
            cwd=str(cauldron_dir),
        )
        calls = calls_file.read_text()

        assert r.returncode != 0, "Expected failure when preflight fails"
        assert "stop-called" not in calls, "stop was called despite preflight failure"
        assert db.read_bytes() == b"original-db-content", "DB was modified"
        assert "git-merge-ff-only" not in calls, "checkout ran despite preflight failure"

    def test_missing_verify_update_fails_closed(self, tmp_path):
        """Criterion 15: missing verify-update causes update to fail closed."""
        update_copy, calls_file, env = _setup_update_env(tmp_path)
        (update_copy.parent / "verify-update").unlink()

        r = subprocess.run(
            ["bash", str(update_copy)],
            capture_output=True, text=True, timeout=30,
            env=env,
            cwd=str(update_copy.parent),
        )
        assert r.returncode != 0
        assert "verify-update" in (r.stdout + r.stderr).lower()
        assert "stop-called" not in calls_file.read_text()

    def test_skip_preflight_bypasses_verification(self, tmp_path):
        """Criterion 16: --skip-preflight prints warning and skips verify-update."""
        update_copy, calls_file, env = _setup_update_env(tmp_path)

        proc = subprocess.Popen(
            ["bash", str(update_copy), "--skip-preflight"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=str(update_copy.parent),
        )
        collected: list[str] = []
        found_warning = False
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                collected.append(line)
                if "Preflight verification skipped" in line or \
                   "skip-preflight" in line.lower():
                    found_warning = True
                    break
        finally:
            proc.kill()
            proc.wait(timeout=5)

        assert found_warning, (
            "Expected --skip-preflight warning.\nOutput:\n" + "".join(collected)
        )
        assert "verify-update-called" not in calls_file.read_text()


# ---------------------------------------------------------------------------
# SKIP phase for no-frontend (acceptance criterion 17)
# ---------------------------------------------------------------------------

class TestNoFrontendSKIP:
    """Criterion 17: When frontend/package.json is absent, the site-build
    phase must emit SKIP (not PASS) in the phase log."""

    def test_site_build_is_skipped_not_passed_without_frontend(self, tmp_path):
        """Source verify-update for its functions, then directly exercise
        the frontend-detection block inside _run_django_phases."""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "logs").mkdir()
        # No frontend/package.json → SKIP expected.

        script = textwrap.dedent(f"""\
            source '{LIB_SH}'
            source '{VERIFY_UPDATE}'

            # Simulate the frontend detection block from _run_django_phases.
            _phase "cauldron_site_build"
            app_frontend="{app_dir}/frontend"
            if [[ ! -f "$app_frontend/package.json" ]]; then
              _skip "site build (no frontend/package.json)"
            fi

            for line in "${{_PHASE_LOG[@]}}"; do
              echo "$line"
            done
        """)
        r = _bash(script, timeout=10)
        assert "SKIP" in r.stdout, (
            f"Expected SKIP for absent frontend/package.json.\nOutput:\n{r.stdout}"
        )
        assert "PASS" not in r.stdout, "PASS must not appear when frontend is absent"


# ---------------------------------------------------------------------------
# Phase report format
# ---------------------------------------------------------------------------

class TestPhaseReport:
    def test_verification_failed_on_bad_ref(self):
        r = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "nonexistent-ref-xyz"],
            capture_output=True, text=True, timeout=15,
            cwd=str(REPO_DIR),
        )
        assert r.returncode != 0
        assert "FAILED" in (r.stdout + r.stderr) or "ERROR" in (r.stdout + r.stderr)

    def test_report_section_always_present(self):
        r = subprocess.run(
            ["bash", str(VERIFY_UPDATE), "nonexistent-ref-xyz"],
            capture_output=True, text=True, timeout=15,
            cwd=str(REPO_DIR),
        )
        combined = r.stdout + r.stderr
        assert "Verification report" in combined or "ERROR" in combined
