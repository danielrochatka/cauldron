"""Process-safe per-request and per-provider locking for workspace and repository mutations.

Uses `filelock` to coordinate across threads and processes so multiple worker
processes cannot mutate the same change request or provider concurrently.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def _safe_lock_name(request_id: str) -> str:
    if not _SAFE_NAME_RE.match(request_id):
        raise ValueError(f"Unsafe lock name: {request_id!r}")
    return f"request-{request_id}.lock"


@contextmanager
def request_lock(
    request_id: str,
    locks_dir: Path,
    timeout: float = 30.0,
) -> Iterator[None]:
    """Acquire a per-request process/thread-safe lock backed by an OS file lock.

    Raises ``TimeoutError`` if the lock cannot be acquired within ``timeout``.
    """
    from filelock import FileLock, Timeout

    lock_path = Path(locks_dir) / _safe_lock_name(request_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path), timeout=timeout)
    try:
        lock.acquire()
    except Timeout as exc:
        raise TimeoutError(
            f"Could not acquire lock for request {request_id!r} within {timeout}s."
        ) from exc
    try:
        yield
    finally:
        lock.release()


@contextmanager
def provider_lock(
    provider_name: str,
    locks_dir: Path,
    timeout: float = 30.0,
) -> Iterator[None]:
    """Per-provider mutation lock.

    Prevents two requests from mutating the same provider simultaneously. Provider
    names are sanitised so any string is a valid lock filename.
    """
    from filelock import FileLock, Timeout

    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", provider_name)[:64] or "provider"
    lock_path = Path(locks_dir) / f"provider-{safe}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path), timeout=timeout)
    try:
        lock.acquire()
    except Timeout as exc:
        raise TimeoutError(
            f"Could not acquire provider lock for {provider_name!r} within {timeout}s."
        ) from exc
    try:
        yield
    finally:
        lock.release()
