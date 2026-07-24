"""Real concurrency tests for UIOverrideStore.

These use ``threading.Thread`` to run two writers simultaneously and assert
that the store's optimistic lock, size cap, and process-wide serialisation
all hold up under contention. Pure-store tests, so no DB is needed.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from cauldron_django_admin.override_store import (
    ABSENT,
    FileSizeError,
    HashConflictError,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    OverrideLockError,
    TraversalError,
    UIOverrideStore,
)


@pytest.fixture()
def override_root(tmp_path: Path) -> Path:
    root = tmp_path / "override-root"
    root.mkdir()
    (root / "admin").mkdir()
    (root / "pages").mkdir()
    return root


def _run_in_parallel(*targets):
    """Start every ``target`` on its own thread and join them all."""
    threads = [threading.Thread(target=t) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "Thread did not finish within timeout."


def test_two_store_instances_share_process_lock(override_root):
    """Two ``UIOverrideStore`` instances rooted at the same directory must
    serialise their writes — the process-wide lock is keyed by root path,
    not by ``self``.
    """
    store1 = UIOverrideStore(override_root)
    h0 = store1.write_file_atomic(
        "admin", "test.css", "/* v0 */", expected_hash=ABSENT,
    )

    results: list[tuple[str, str]] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def write_v1():
        s = UIOverrideStore(override_root)
        try:
            h = s.write_file_atomic(
                "admin", "test.css", "/* v1 */", expected_hash=h0,
            )
            with lock:
                results.append(("v1", h))
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    def write_v2():
        s = UIOverrideStore(override_root)
        try:
            h = s.write_file_atomic(
                "admin", "test.css", "/* v2 */", expected_hash=h0,
            )
            with lock:
                results.append(("v2", h))
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    _run_in_parallel(write_v1, write_v2)

    assert len(results) == 1, (
        f"Expected exactly one successful write, got {len(results)}: {results}"
    )
    assert len(errors) == 1, (
        f"Expected exactly one failed write, got {len(errors)}: {errors}"
    )
    assert isinstance(errors[0], HashConflictError)


def test_parallel_writes_different_files_respect_total_limit(override_root):
    """Two writers each racing to fill the remaining budget: the total-size
    cap must reject at least one.
    """
    store = UIOverrideStore(override_root)

    # Fill up to just under (MAX_TOTAL_BYTES - MAX_FILE_BYTES) so that a
    # single MAX_FILE_BYTES-sized write still fits, but a second one does
    # not. Use several files under the per-file cap to reach the target.
    per_seed = MAX_FILE_BYTES - 200
    target_seed_bytes = MAX_TOTAL_BYTES - MAX_FILE_BYTES - 200
    seed_body = "x" * per_seed
    n_seeds = target_seed_bytes // per_seed
    for i in range(n_seeds):
        store.write_file_atomic(
            "admin", f"seed-{i:02d}.css", seed_body, expected_hash=ABSENT,
        )

    # Each candidate is close to the per-file cap so only one can fit inside
    # the remaining total-size budget.
    payload = "y" * (MAX_FILE_BYTES - 100)

    successes: list[str] = []
    size_errors: list[Exception] = []
    other_errors: list[Exception] = []
    lock = threading.Lock()

    def write(name: str):
        s = UIOverrideStore(override_root)
        try:
            s.write_file_atomic(
                "admin", name, payload, expected_hash=ABSENT,
            )
            with lock:
                successes.append(name)
        except FileSizeError as exc:
            with lock:
                size_errors.append(exc)
        except Exception as exc:  # noqa: BLE001
            with lock:
                other_errors.append(exc)

    _run_in_parallel(lambda: write("b.css"), lambda: write("c.css"))

    assert not other_errors, (
        f"Unexpected error class(es): {[type(e).__name__ for e in other_errors]}"
    )
    assert len(successes) <= 1
    # Either the second writer was rejected outright, or both were rejected
    # (permitted — the important guarantee is we never bust the cap).
    assert len(successes) + len(size_errors) == 2


def test_target_symlink_inside_scope(override_root):
    """A symlink placed at the target path — even if it resolves to a real
    file inside the scope — must be rejected. The store cannot honour a
    symlink because atomic replace would either follow it (leaking writes
    to the resolved location) or replace it (silently converting the link
    into a regular file, breaking whatever else pointed at that target).
    """
    real = override_root / "admin" / "real.css"
    real.write_text("/* real */", encoding="utf-8")
    link = override_root / "admin" / "link.css"
    link.symlink_to(real)

    store = UIOverrideStore(override_root)
    with pytest.raises(TraversalError):
        store.write_file_atomic(
            "admin", "link.css", "/* new */", expected_hash=ABSENT,
        )


def test_parent_dir_symlink_inside_scope(override_root):
    """A symlinked parent directory (even one that resolves inside the
    scope) is rejected because writes could traverse it to sibling scopes.
    """
    real_dir = override_root / "admin" / "real_dir"
    real_dir.mkdir()
    link_dir = override_root / "admin" / "link_dir"
    link_dir.symlink_to(real_dir, target_is_directory=True)

    store = UIOverrideStore(override_root)
    with pytest.raises(TraversalError):
        store.write_file_atomic(
            "admin", "link_dir/x.css", "/* new */", expected_hash=ABSENT,
        )


def test_target_symlink_escapes_root(override_root, tmp_path):
    """A symlink pointing outside the override root is rejected."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    victim = outside / "secret.css"
    victim.write_text("/* secret */", encoding="utf-8")
    link = override_root / "admin" / "leak.css"
    link.symlink_to(victim)

    store = UIOverrideStore(override_root)
    with pytest.raises(TraversalError):
        store.write_file_atomic(
            "admin", "leak.css", "/* new */", expected_hash=ABSENT,
        )


def test_parent_swapped_to_symlink_after_creation(override_root):
    """A symlinked parent created after the initial write is rejected on
    the next write. This is the ``post-creation swap`` variant of the
    under-lock race — full timing races are hard to reproduce
    deterministically, but any symlink parent must be rejected regardless
    of when it appeared.
    """
    real_dir = override_root / "admin" / "sub"
    real_dir.mkdir()
    store = UIOverrideStore(override_root)
    h0 = store.write_file_atomic(
        "admin", "sub/a.css", "/* v0 */", expected_hash=ABSENT,
    )

    # Now swap "sub" to a symlink that still resolves inside the scope.
    other = override_root / "admin" / "sub_real"
    other.mkdir()
    (other / "a.css").write_text("/* v0 */", encoding="utf-8")
    import shutil
    shutil.rmtree(real_dir)
    real_dir.symlink_to(other, target_is_directory=True)

    with pytest.raises(TraversalError):
        store.write_file_atomic(
            "admin", "sub/a.css", "/* v1 */", expected_hash=h0,
        )


def test_delete_through_target_symlink(override_root):
    """A symlink at the delete target is rejected before ``os.unlink``."""
    real = override_root / "admin" / "real.css"
    real.write_text("/* real */", encoding="utf-8")
    link = override_root / "admin" / "link.css"
    link.symlink_to(real)

    store = UIOverrideStore(override_root)
    with pytest.raises(TraversalError):
        store.delete_file_atomic("admin", "link.css", expected_hash="0" * 64)


def test_delete_through_parent_symlink(override_root):
    """A symlinked parent directory is rejected before ``os.unlink``."""
    real_dir = override_root / "admin" / "real_dir"
    real_dir.mkdir()
    (real_dir / "x.css").write_text("/* real */", encoding="utf-8")
    link_dir = override_root / "admin" / "link_dir"
    link_dir.symlink_to(real_dir, target_is_directory=True)

    store = UIOverrideStore(override_root)
    with pytest.raises(TraversalError):
        store.delete_file_atomic(
            "admin", "link_dir/x.css", expected_hash="0" * 64,
        )


def test_lock_error_on_non_posix(override_root, monkeypatch):
    """When neither ``fcntl`` nor ``msvcrt`` is available, writes must
    raise :class:`OverrideLockError` rather than silently downgrading to
    thread-only locking. Two worker processes racing under a thread-only
    lock could still clobber each other, so fail-closed is the only safe
    default.
    """
    import cauldron_django_admin.override_store as store_mod

    monkeypatch.setattr(store_mod, "_HAS_FCNTL", False)
    monkeypatch.setattr(store_mod, "_HAS_MSVCRT", False)

    store = UIOverrideStore(override_root)
    with pytest.raises(OverrideLockError):
        store.write_file_atomic(
            "admin", "x.css", "/* v0 */", expected_hash=ABSENT,
        )
    with pytest.raises(OverrideLockError):
        store.delete_file_atomic(
            "admin", "x.css", expected_hash="0" * 64,
        )


def test_many_concurrent_writers_only_one_wins(override_root):
    """Ten threads race to write to the same file starting from ABSENT —
    only one should succeed, the rest should get ``HashConflictError``.
    """
    successes: list[str] = []
    conflicts: list[Exception] = []
    others: list[Exception] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def do_write(i: int):
        s = UIOverrideStore(override_root)
        barrier.wait(timeout=10)
        try:
            s.write_file_atomic(
                "admin", "race.css", f"/* winner={i} */",
                expected_hash=ABSENT,
            )
            with lock:
                successes.append(f"w{i}")
        except HashConflictError as exc:
            with lock:
                conflicts.append(exc)
        except Exception as exc:  # noqa: BLE001
            with lock:
                others.append(exc)

    _run_in_parallel(*[lambda i=i: do_write(i) for i in range(10)])

    assert not others, (
        f"Unexpected error class(es): {[type(e).__name__ for e in others]}"
    )
    assert len(successes) == 1
    assert len(conflicts) == 9
