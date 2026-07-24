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
