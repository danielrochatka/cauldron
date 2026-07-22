"""Tests for process/thread-safe locking."""
from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from cauldron_content_operations.locking import (
    _safe_lock_name,
    provider_lock,
    request_lock,
)


def test_safe_lock_name_rejects_traversal():
    with pytest.raises(ValueError):
        _safe_lock_name("../evil")


def test_safe_lock_name_rejects_slashes():
    with pytest.raises(ValueError):
        _safe_lock_name("a/b")


def test_safe_lock_name_accepts_uuid():
    name = _safe_lock_name("abcd-1234_XYZ")
    assert name.endswith(".lock")


def test_request_lock_reentrant_is_serialized_across_threads(tmp_path):
    locks_dir = tmp_path / "locks"
    results = []

    def worker(i):
        with request_lock("req-1", locks_dir, timeout=5.0):
            results.append((i, "in"))
            time.sleep(0.05)
            results.append((i, "out"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Each worker's in and out must be adjacent.
    for i in range(3):
        idx_in = [k for k, (worker_id, state) in enumerate(results) if worker_id == i and state == "in"][0]
        idx_out = [k for k, (worker_id, state) in enumerate(results) if worker_id == i and state == "out"][0]
        assert idx_out == idx_in + 1


def test_provider_lock_serializes_across_threads(tmp_path):
    locks_dir = tmp_path / "locks"
    active = []

    def worker():
        with provider_lock("flatfile", locks_dir, timeout=5.0):
            active.append(1)
            assert len(active) == 1
            time.sleep(0.02)
            active.pop()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_request_lock_excludes_across_processes(tmp_path):
    """A separate Python process cannot acquire the same request lock."""
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)

    holder_script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(Path(__file__).parents[2] / 'src')!r})
        from cauldron_content_operations.locking import request_lock
        with request_lock("cross-proc", {str(locks_dir)!r}, timeout=5.0):
            print("HELD", flush=True)
            time.sleep(1.5)
            print("RELEASE", flush=True)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", holder_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait for the child to announce it has the lock.
        line = proc.stdout.readline()
        assert line.strip() == b"HELD", (line, proc.stderr.read())

        start = time.monotonic()
        with pytest.raises(TimeoutError):
            with request_lock("cross-proc", locks_dir, timeout=0.5):
                pass
        elapsed = time.monotonic() - start
        assert elapsed >= 0.4  # must have actually waited
    finally:
        proc.wait(timeout=10)
