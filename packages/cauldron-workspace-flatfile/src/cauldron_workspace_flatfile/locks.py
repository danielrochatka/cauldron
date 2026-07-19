"""Cross-process workspace lock backed by filelock."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock


class WorkspaceLock:
    def __init__(self, locks_dir: Path) -> None:
        self._locks_dir = Path(locks_dir)

    @contextmanager
    def lock(self, name: str = "workspace", timeout: float = 30.0):
        self._locks_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._locks_dir / f"{name}.lock"
        with FileLock(str(lock_path), timeout=timeout):
            yield
