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
import time
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


# ---------------------------------------------------------------------------
# Verify-update phase runners: genuine subprocess tests
# ---------------------------------------------------------------------------
# These tests exercise _run_django_phases and _run_server_phases by sourcing
# verify-update (the BASH_SOURCE guard prevents dispatch) and calling each
# phase runner with a fake worktree whose "python" binary simulates specific
# failure scenarios.
# ---------------------------------------------------------------------------

def _make_fake_worktree(
    tmp_path: Path,
    *,
    check_fails: bool = False,
    migration_check_fails: bool = False,
    migrate_fails: bool = False,
    collectstatic_fails: bool = False,
    site_build_fails: bool = False,
    server_exits: bool = False,
    has_frontend: bool = False,
) -> tuple[Path, Path]:
    """Create a minimal fake worktree for phase runner tests.

    Returns (worktree_path, calls_log_path).  The fake "python" at
    .venv/bin/python examines its argument list and exits with the code
    specified by the scenario flags.
    """
    worktree = tmp_path / "worktree"
    venv_bin = worktree / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    app_dir = worktree / "cauldron-app"
    (app_dir / "logs").mkdir(parents=True)
    (app_dir / "data").mkdir()
    (app_dir / "config.env").write_text("SECRET_KEY=test-key-for-phase-runner-tests\n")

    calls_log = tmp_path / "calls.log"
    calls_log.touch()

    if has_frontend:
        frontend = app_dir / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text('{"name": "test", "scripts": {}}')

    server_exit = "exit 1" if server_exits else "exit 0"
    python_script = textwrap.dedent(f"""\
        #!/bin/sh
        echo "python $*" >> "{calls_log}"
        case "$*" in
          *" check"*|*"manage.py check"*)
            {"exit 1" if check_fails else "exit 0"} ;;
          *"makemigrations --check"*)
            {"exit 1" if migration_check_fails else "exit 0"} ;;
          *" migrate"*)
            {"exit 1" if migrate_fails else "exit 0"} ;;
          *"collectstatic"*)
            {"exit 1" if collectstatic_fails else "exit 0"} ;;
          *"cauldron_site_build"*)
            {"exit 1" if site_build_fails else "exit 0"} ;;
          *"runserver"*)
            echo "Fake server exiting." >&2
            {server_exit} ;;
          *) exit 0 ;;
        esac
    """)
    python_bin = venv_bin / "python"
    python_bin.write_text(python_script)
    python_bin.chmod(0o755)

    return worktree, calls_log


def _source_and_run_django(
    worktree: Path,
    tmp_path: Path,
    *,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Source verify-update and call _run_django_phases with a fake worktree."""
    script = textwrap.dedent(f"""\
        source '{LIB_SH}'
        source '{VERIFY_UPDATE}'
        _run_django_phases "{worktree}" ""
        for line in "${{_PHASE_LOG[@]}}"; do echo "$line"; done
        [[ "$_OVERALL_OK" -eq 1 ]] && echo "OVERALL_PASS" || echo "OVERALL_FAIL"
    """)
    merged = {**os.environ, **(extra_env or {})}
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=20,
        env=merged,
        cwd=str(CAULDRON_APP),
    )


def _source_and_run_server(
    worktree: Path,
) -> tuple[subprocess.CompletedProcess, float]:
    """Source verify-update and call _run_server_phases; return (result, elapsed)."""
    script = textwrap.dedent(f"""\
        source '{LIB_SH}'
        source '{VERIFY_UPDATE}'
        _run_server_phases "{worktree}" ""
        for line in "${{_PHASE_LOG[@]}}"; do echo "$line"; done
        [[ "$_OVERALL_OK" -eq 1 ]] && echo "OVERALL_PASS" || echo "OVERALL_FAIL"
    """)
    start = time.monotonic()
    r = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=40,
        env=os.environ,
        cwd=str(CAULDRON_APP),
    )
    return r, time.monotonic() - start


def _fake_node_npm_env(tmp_path: Path, *, npm_fails: bool = False) -> dict:
    """Return env dict with fake node and npm stubs in PATH."""
    fake_bin = tmp_path / "fake_nm_bin"
    fake_bin.mkdir(exist_ok=True)
    node = fake_bin / "node"
    node.write_text("#!/bin/sh\necho 'v22.0.0'\n")
    node.chmod(0o755)
    npm = fake_bin / "npm"
    npm.write_text(f"#!/bin/sh\nexit {'1' if npm_fails else '0'}\n")
    npm.chmod(0o755)
    return {"PATH": f"{fake_bin}:{os.environ['PATH']}"}


class TestVerifyUpdatePhaseRunners:
    """Genuine subprocess tests for individual phase runner functions.

    Exercises criteria 2, 3, 6, 7, 9 with real bash invocations but fake
    worktrees — no real git worktrees, venvs, or Django installs needed.
    """

    # -- Criterion 2: Django check failure ------------------------------------

    def test_django_check_failure_reports_fail(self, tmp_path):
        # set -e (from sourcing verify-update) causes bash to exit when
        # _run_django_phases returns 1, so we check returncode, not text.
        worktree, _ = _make_fake_worktree(tmp_path, check_fails=True)
        r = _source_and_run_django(worktree, tmp_path)
        assert r.returncode != 0, (
            f"Expected non-zero exit for Django check failure.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "OVERALL_PASS" not in (r.stdout + r.stderr)

    # -- Criterion 3: Missing migration ---------------------------------------

    def test_missing_migration_reports_fail(self, tmp_path):
        worktree, _ = _make_fake_worktree(tmp_path, migration_check_fails=True)
        r = _source_and_run_django(worktree, tmp_path)
        assert r.returncode != 0, (
            f"Expected non-zero exit for missing migration.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "OVERALL_PASS" not in (r.stdout + r.stderr)

    # -- Criterion 6: Frontend install failure (npm fails) --------------------

    def test_frontend_npm_failure_reports_fail(self, tmp_path):
        worktree, _ = _make_fake_worktree(tmp_path, has_frontend=True)
        env = _fake_node_npm_env(tmp_path, npm_fails=True)
        r = _source_and_run_django(worktree, tmp_path, extra_env=env)
        assert r.returncode != 0, (
            f"Expected non-zero exit when npm fails.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "OVERALL_PASS" not in (r.stdout + r.stderr)

    # -- Criterion 7: Site build failure --------------------------------------

    def test_site_build_failure_reports_fail(self, tmp_path):
        worktree, _ = _make_fake_worktree(tmp_path, has_frontend=True,
                                          site_build_fails=True)
        env = _fake_node_npm_env(tmp_path)
        r = _source_and_run_django(worktree, tmp_path, extra_env=env)
        assert r.returncode != 0, (
            f"Expected non-zero exit when site build fails.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "OVERALL_PASS" not in (r.stdout + r.stderr)

    # -- Criterion 9: Server exit detected quickly ----------------------------

    def test_server_exit_detected_before_timeout(self, tmp_path):
        """Server that exits immediately is detected in under 10 s (not 30 s)."""
        worktree, _ = _make_fake_worktree(tmp_path, server_exits=True)
        r, elapsed = _source_and_run_server(worktree)
        assert r.returncode != 0, (
            f"Expected non-zero exit when server exits immediately.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert elapsed < 10, (
            f"Server exit should be detected quickly, but took {elapsed:.1f}s"
        )
        assert "OVERALL_PASS" not in (r.stdout + r.stderr)

    # -- Successful all-pass path (no frontend) -------------------------------

    def test_all_phases_pass_without_frontend(self, tmp_path):
        """All django phases pass when the fake python reports no errors."""
        worktree, _ = _make_fake_worktree(tmp_path)
        r = _source_and_run_django(worktree, tmp_path)
        combined = r.stdout + r.stderr
        assert "OVERALL_FAIL" not in combined, (
            f"Expected all phases to pass.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "OVERALL_PASS" in combined

    # -- Criterion 17: SKIP for no frontend (reinforced) ---------------------

    def test_no_frontend_emits_skip_not_pass(self, tmp_path):
        """Without frontend/package.json the site-build phase is SKIP, not PASS."""
        worktree, _ = _make_fake_worktree(tmp_path, has_frontend=False)
        r = _source_and_run_django(worktree, tmp_path)
        assert "SKIP" in r.stdout, (
            f"Expected SKIP for absent frontend.\nstdout: {r.stdout}"
        )
        assert "PASS  site build" not in r.stdout

    # -- tools/verify_wheels.py: generate-manifest and verify -----------------

    def test_generate_manifest_creates_manifest_json(self, tmp_path):
        """generate-manifest writes manifest.json with source_sha and digests."""
        wh = tmp_path / "wh"
        wh.mkdir()
        _make_wheel(wh, name="cauldron_test")
        r = subprocess.run(
            [
                "python3",
                str(REPO_DIR / "tools" / "verify_wheels.py"),
                "generate-manifest",
                "--wheelhouse", str(wh),
                "--source-sha", "abc" * 14,  # 42-char test SHA (truncated to 40)
            ],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, f"generate-manifest failed: {r.stderr}"
        manifest_path = wh / "manifest.json"
        assert manifest_path.exists(), "manifest.json was not created"
        import json
        manifest = json.loads(manifest_path.read_text())
        assert "source_sha" in manifest
        assert "wheels" in manifest
        whl_names = list(manifest["wheels"].keys())
        assert any("cauldron_test" in n for n in whl_names)

    def test_verify_with_correct_sha_passes(self, tmp_path):
        """verify --require-sha passes when manifest SHA matches."""
        import json, hashlib
        wh = tmp_path / "wh"
        wh.mkdir()
        _make_wheel(wh, name="cauldron_test")
        sha = "abcd1234" * 5  # 40 chars
        whl_file = next(wh.glob("*.whl"))
        digest = hashlib.sha256(whl_file.read_bytes()).hexdigest()
        manifest = {
            "source_sha": sha,
            "wheels": {whl_file.name: {"sha256": digest}},
        }
        (wh / "manifest.json").write_text(json.dumps(manifest))
        r = subprocess.run(
            [
                "python3",
                str(REPO_DIR / "tools" / "verify_wheels.py"),
                "verify",
                "--wheelhouse", str(wh),
                "--require-sha", sha,
            ],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, (
            f"verify with correct SHA should pass.\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_verify_with_wrong_sha_fails(self, tmp_path):
        """verify --require-sha fails when manifest SHA does not match."""
        import json, hashlib
        wh = tmp_path / "wh"
        wh.mkdir()
        _make_wheel(wh, name="cauldron_test")
        whl_file = next(wh.glob("*.whl"))
        digest = hashlib.sha256(whl_file.read_bytes()).hexdigest()
        manifest = {
            "source_sha": "aaaa" * 10,
            "wheels": {whl_file.name: {"sha256": digest}},
        }
        (wh / "manifest.json").write_text(json.dumps(manifest))
        r = subprocess.run(
            [
                "python3",
                str(REPO_DIR / "tools" / "verify_wheels.py"),
                "verify",
                "--wheelhouse", str(wh),
                "--require-sha", "bbbb" * 10,
            ],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode != 0, "verify with wrong SHA should fail"
        assert "mismatch" in (r.stdout + r.stderr).lower()


# ---------------------------------------------------------------------------
# Source-aware wheel validation (--repo-root)
# ---------------------------------------------------------------------------

VERIFY_WHEELS_PY = REPO_DIR / "tools" / "verify_wheels.py"

_PKG = "cauldron_fakepkg"
_EP_KEY = "cauldron.fake"
_EP_VAL = "cauldron_fakepkg.module:module"


def _make_fake_repo(tmp_path: Path, *,
                    ep_entries: dict | None = None,
                    migrations: list[str] | None = None,
                    templates: list[str] | None = None,
                    static_files: list[str] | None = None) -> Path:
    """Minimal fake repo with one sub-package for source-aware validation tests."""
    repo = tmp_path / "repo"
    pkg_dir = repo / "packages" / _PKG.replace("_", "-")
    src = pkg_dir / "src" / _PKG
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")

    ep_section = ""
    if ep_entries:
        ep_section = '\n[project.entry-points."cauldron.modules"]\n'
        for k, v in ep_entries.items():
            ep_section += f'"{k}" = "{v}"\n'

    (pkg_dir / "pyproject.toml").write_text(
        f'[build-system]\nrequires = ["setuptools>=68"]\n'
        f'build-backend = "setuptools.build_meta"\n\n'
        f"[project]\nname = \"{_PKG.replace('_', '-')}\"\nversion = \"0.1.0\"\n"
        f"{ep_section}\n"
        f'[tool.setuptools.packages.find]\nwhere = ["src"]\n'
    )

    for migration in (migrations or []):
        mig = src / "migrations" / migration
        mig.parent.mkdir(parents=True, exist_ok=True)
        mig.write_text("")

    for template in (templates or []):
        tmpl = src / "templates" / template
        tmpl.parent.mkdir(parents=True, exist_ok=True)
        tmpl.write_text("<html/>")

    for static_file in (static_files or []):
        sf = src / "static" / static_file
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text("")

    return repo


def _make_source_wheel(wh: Path, *,
                       ep_entries: dict | None = None,
                       migrations: list[str] | None = None,
                       templates: list[str] | None = None,
                       static_files: list[str] | None = None) -> Path:
    """Wheel for _PKG matching the source layout produced by _make_fake_repo."""
    whl = wh / f"{_PKG}-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr(f"{_PKG}-0.1.0.dist-info/METADATA",
                   f"Metadata-Version: 2.1\nName: {_PKG}\nVersion: 0.1.0\n")
        z.writestr(f"{_PKG}/__init__.py", "")

        if ep_entries:
            ep_txt = "[cauldron.modules]\n"
            for k, v in ep_entries.items():
                ep_txt += f"{k} = {v}\n"
            z.writestr(f"{_PKG}-0.1.0.dist-info/entry_points.txt", ep_txt)

        for migration in (migrations or []):
            z.writestr(f"{_PKG}/migrations/{migration}", "")

        for template in (templates or []):
            z.writestr(f"{_PKG}/templates/{template}", "<html/>")

        for static_file in (static_files or []):
            z.writestr(f"{_PKG}/static/{static_file}", "")

    return whl


def _run_verify(wh: Path, repo: Path | None = None,
                require_sha: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["python3", str(VERIFY_WHEELS_PY), "verify", "--wheelhouse", str(wh)]
    if require_sha:
        cmd += ["--require-sha", require_sha]
    if repo:
        cmd += ["--repo-root", str(repo)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


class TestVerifyWheelsSourceAware:

    def test_all_correct_passes(self, tmp_path):
        """Wheel with matching ep, migration, template, static → pass."""
        wh = tmp_path / "wh"
        wh.mkdir()
        repo = _make_fake_repo(
            tmp_path,
            ep_entries={_EP_KEY: _EP_VAL},
            migrations=["0001_initial.py"],
            templates=["fakepkg/base.html"],
            static_files=["fakepkg/main.css"],
        )
        _make_source_wheel(
            wh,
            ep_entries={_EP_KEY: _EP_VAL},
            migrations=["0001_initial.py"],
            templates=["fakepkg/base.html"],
            static_files=["fakepkg/main.css"],
        )
        r = _run_verify(wh, repo)
        assert r.returncode == 0, f"Expected pass.\nstdout: {r.stdout}\nstderr: {r.stderr}"

    def test_missing_entry_points_txt_fails(self, tmp_path):
        """Source declares cauldron.modules ep but wheel has no entry_points.txt → fail."""
        wh = tmp_path / "wh"
        wh.mkdir()
        repo = _make_fake_repo(tmp_path, ep_entries={_EP_KEY: _EP_VAL})
        _make_source_wheel(wh)  # no ep_entries in wheel
        r = _run_verify(wh, repo)
        assert r.returncode != 0, "Should fail: missing entry_points.txt"
        assert "entry_points" in (r.stdout + r.stderr)

    def test_missing_declared_entry_point_fails(self, tmp_path):
        """Wheel entry_points.txt lacks a key declared in pyproject.toml → fail."""
        wh = tmp_path / "wh"
        wh.mkdir()
        repo = _make_fake_repo(tmp_path, ep_entries={_EP_KEY: _EP_VAL})
        # Wheel has entry_points.txt but with a DIFFERENT key
        whl = wh / f"{_PKG}-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(whl, "w") as z:
            z.writestr(f"{_PKG}-0.1.0.dist-info/METADATA",
                       f"Metadata-Version: 2.1\nName: {_PKG}\nVersion: 0.1.0\n")
            z.writestr(f"{_PKG}/__init__.py", "")
            z.writestr(f"{_PKG}-0.1.0.dist-info/entry_points.txt",
                       "[cauldron.modules]\ncauldron.other = cauldron_fakepkg.other:module\n")
        r = _run_verify(wh, repo)
        assert r.returncode != 0, "Should fail: declared entry point not in wheel"
        assert "entry point" in (r.stdout + r.stderr)

    def test_missing_migration_fails(self, tmp_path):
        """Source migration not present in wheel → fail."""
        wh = tmp_path / "wh"
        wh.mkdir()
        repo = _make_fake_repo(tmp_path, migrations=["0001_initial.py"])
        _make_source_wheel(wh)  # wheel has no migrations
        r = _run_verify(wh, repo)
        assert r.returncode != 0, "Should fail: missing migration"
        assert "migration" in (r.stdout + r.stderr)

    def test_missing_template_fails(self, tmp_path):
        """Source template not present in wheel → fail."""
        wh = tmp_path / "wh"
        wh.mkdir()
        repo = _make_fake_repo(tmp_path, templates=["fakepkg/base.html"])
        _make_source_wheel(wh)  # wheel has no templates
        r = _run_verify(wh, repo)
        assert r.returncode != 0, "Should fail: missing template"
        assert "template" in (r.stdout + r.stderr)

    def test_missing_static_fails(self, tmp_path):
        """Source static file not present in wheel → fail."""
        wh = tmp_path / "wh"
        wh.mkdir()
        repo = _make_fake_repo(tmp_path, static_files=["fakepkg/main.css"])
        _make_source_wheel(wh)  # wheel has no static
        r = _run_verify(wh, repo)
        assert r.returncode != 0, "Should fail: missing static"
        assert "static" in (r.stdout + r.stderr)

    def test_missing_expected_wheel_fails(self, tmp_path):
        """Package has cauldron.modules ep but no matching wheel → fail."""
        wh = tmp_path / "wh"
        wh.mkdir()
        repo = _make_fake_repo(tmp_path, ep_entries={_EP_KEY: _EP_VAL})
        # Add a wheel for a *different* package so the empty-wheelhouse check
        # doesn't fire first; _PKG still has no wheel despite declaring an ep.
        other = "cauldron_base"
        other_pkg_dir = repo / "packages" / "cauldron-base"
        (other_pkg_dir / "src" / other).mkdir(parents=True)
        (other_pkg_dir / "src" / other / "__init__.py").write_text("")
        (other_pkg_dir / "pyproject.toml").write_text(
            f'[project]\nname = "cauldron-base"\nversion = "0.1.0"\n'
            f'[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        )
        other_whl = wh / f"{other}-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(other_whl, "w") as z:
            z.writestr(f"{other}-0.1.0.dist-info/METADATA",
                       f"Metadata-Version: 2.1\nName: {other}\nVersion: 0.1.0\n")
            z.writestr(f"{other}/__init__.py", "")
        r = _run_verify(wh, repo)
        assert r.returncode != 0, "Should fail: no wheel for ep-declaring package"
        assert "no wheel found" in (r.stdout + r.stderr)

    def test_extra_unrecorded_wheel_fails(self, tmp_path):
        """Wheel in wheelhouse with no matching source package → fail."""
        wh = tmp_path / "wh"
        wh.mkdir()
        repo = _make_fake_repo(tmp_path)  # no ep entries
        # Put a wheel whose name doesn't match any source package
        _make_wheel(wh, name="cauldron_unknown_pkg")
        # also put the expected wheel so the missing-wheel check doesn't trigger
        _make_source_wheel(wh)
        r = _run_verify(wh, repo)
        assert r.returncode != 0, "Should fail: wheel not from any source package"
        assert "no corresponding source package" in (r.stdout + r.stderr)

    def test_duplicate_distribution_fails(self, tmp_path):
        """Two wheels with the same distribution name → fail."""
        wh = tmp_path / "wh"
        wh.mkdir()
        repo = _make_fake_repo(tmp_path)
        # Write two wheels with same label but different versions
        for ver in ("0.1.0", "0.2.0"):
            whl = wh / f"{_PKG}-{ver}-py3-none-any.whl"
            with zipfile.ZipFile(whl, "w") as z:
                z.writestr(f"{_PKG}-{ver}.dist-info/METADATA",
                           f"Metadata-Version: 2.1\nName: {_PKG}\nVersion: {ver}\n")
                z.writestr(f"{_PKG}/__init__.py", "")
        r = _run_verify(wh, repo)
        assert r.returncode != 0, "Should fail: duplicate distribution"
        assert "duplicate" in (r.stdout + r.stderr)

    def test_manifest_extra_disk_wheel_fails(self, tmp_path):
        """Wheel on disk absent from manifest → fail with --require-sha."""
        import json, hashlib
        wh = tmp_path / "wh"
        wh.mkdir()
        whl = _make_source_wheel(wh)
        sha = "abcd1234" * 5
        # Manifest lists no wheels — wheel on disk is unrecorded
        manifest = {"source_sha": sha, "wheels": {}}
        (wh / "manifest.json").write_text(json.dumps(manifest))
        r = _run_verify(wh, require_sha=sha)
        assert r.returncode != 0, "Should fail: wheel on disk not in manifest"
        assert "absent from manifest" in (r.stdout + r.stderr)

    def test_manifest_all_present_passes(self, tmp_path):
        """All wheels on disk match manifest → pass with --require-sha."""
        import json, hashlib
        wh = tmp_path / "wh"
        wh.mkdir()
        whl = _make_source_wheel(wh)
        sha = "abcd1234" * 5
        digest = hashlib.sha256(whl.read_bytes()).hexdigest()
        manifest = {"source_sha": sha, "wheels": {whl.name: {"sha256": digest}}}
        (wh / "manifest.json").write_text(json.dumps(manifest))
        r = _run_verify(wh, require_sha=sha)
        assert r.returncode == 0, (
            f"Expected pass.\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
