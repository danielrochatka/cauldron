"""Safe path resolution: block traversal, absolute paths, and symlink escapes."""
from __future__ import annotations

from pathlib import Path


class PathEscapeError(Exception):
    """Raised when a candidate path escapes the intended root."""


def safe_resolve(root: Path, *parts: str) -> Path:
    """Resolve a path relative to ``root`` while blocking escape attempts.

    - Rejects absolute path segments.
    - Rejects ``..`` traversal outside of root.
    - Rejects symlinks whose real target escapes root.
    """
    root = Path(root).resolve()
    candidate = root
    for part in parts:
        segment = Path(part)
        if segment.is_absolute():
            raise PathEscapeError(f"Absolute path not allowed: {part!r}")
        candidate = (candidate / segment).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PathEscapeError(
                f"Path escapes workspace root: {candidate}"
            ) from exc
    if candidate.is_symlink():
        real = candidate.resolve()
        try:
            real.relative_to(root)
        except ValueError as exc:
            raise PathEscapeError(
                f"Symlink escapes workspace root: {candidate} -> {real}"
            ) from exc
    return candidate
