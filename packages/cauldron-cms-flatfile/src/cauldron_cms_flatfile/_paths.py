"""Containment-safe path resolution local to the flat-file CMS package.

This helper intentionally does not depend on ``cauldron_workspace_flatfile``
so the CMS package can be installed and tested independently.
"""
from __future__ import annotations

from pathlib import Path


class PathEscapeError(Exception):
    """Raised when a candidate path escapes the intended root."""


def _safe_resolve(root: Path, *parts: str) -> Path:
    """Resolve a path relative to ``root`` while blocking escape attempts.

    - Rejects absolute path segments.
    - Rejects ``..`` traversal outside root.
    - Rejects symlinks whose real target escapes root.

    This is a local copy of the equivalent helper in
    ``cauldron_workspace_flatfile.paths`` so the CMS package is self-contained.
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


__all__ = ["_safe_resolve", "PathEscapeError"]
