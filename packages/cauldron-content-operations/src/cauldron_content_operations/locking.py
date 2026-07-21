"""Process-safe per-request locking for workspace and repository mutations."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

_locks: dict[str, threading.Lock] = {}
_meta_lock = threading.Lock()


def _get_lock(request_id: str) -> threading.Lock:
    with _meta_lock:
        if request_id not in _locks:
            _locks[request_id] = threading.Lock()
        return _locks[request_id]


@contextmanager
def request_lock(request_id: str, timeout: float = 30.0) -> Iterator[None]:
    """Acquire a per-request process lock. Raises TimeoutError if not acquired."""
    lock = _get_lock(request_id)
    acquired = lock.acquire(timeout=timeout)
    if not acquired:
        raise TimeoutError(f"Could not acquire lock for request {request_id!r} within {timeout}s.")
    try:
        yield
    finally:
        lock.release()
