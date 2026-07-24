"""UIOverrideStore — safe read/write service for site-owned CSS override files."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Literal

_VALID_SCOPES = {"admin", "pages"}
_MAX_FILE_BYTES = 256 * 1024  # 256 KB per file
_MAX_TOTAL_BYTES = 2 * 1024 * 1024  # 2 MB total


class OverrideStoreError(Exception):
    """Base error for UIOverrideStore operations."""


class TraversalError(OverrideStoreError):
    """Path traversal or symlink escape detected."""


class InvalidScopeError(OverrideStoreError):
    """Unknown scope."""


class InvalidFileError(OverrideStoreError):
    """File rejected (non-CSS, hidden dir, etc.)."""


class FileSizeError(OverrideStoreError):
    """File or total size limit exceeded."""


class HashConflictError(OverrideStoreError):
    """Optimistic-lock hash mismatch — file changed under us."""


class EncodingError(OverrideStoreError):
    """Content is not valid UTF-8."""


Scope = Literal["admin", "pages"]


class UIOverrideStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _scope_dir(self, scope: Scope) -> Path:
        if scope not in _VALID_SCOPES:
            raise InvalidScopeError(f"Unknown scope: {scope!r}")
        return self._root / scope

    def _safe_resolve(self, path: Path) -> Path:
        """Resolve path and ensure it stays within root."""
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise TraversalError("Cannot resolve path") from exc
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise TraversalError("Path escapes the override root")
        # Check for symlinks that escape root
        if resolved.is_symlink():
            link_target = Path(os.readlink(str(resolved))).resolve()
            try:
                link_target.relative_to(self._root)
            except ValueError:
                raise TraversalError("Symlink escapes the override root")
        return resolved

    def _validate_css_path(self, path: Path) -> None:
        if path.suffix.lower() != ".css":
            raise InvalidFileError(f"Only .css files are accepted: {path.name}")
        for part in path.parts:
            if part.startswith("."):
                raise InvalidFileError(f"Hidden directory or file not allowed: {part}")

    def list_files(self, scope: Scope) -> list[str]:
        scope_dir = self._scope_dir(scope)
        if not scope_dir.is_dir():
            return []
        results = []
        for item in sorted(scope_dir.rglob("*.css")):
            # skip hidden
            if any(p.startswith(".") for p in item.parts):
                continue
            try:
                rel = item.relative_to(self._root)
                self._safe_resolve(item)
                results.append(str(rel).replace(os.sep, "/"))
            except (TraversalError, OSError):
                continue
        return results

    def read_file(self, rel_path: str) -> str:
        path = self._root / rel_path
        self._validate_css_path(Path(rel_path))
        resolved = self._safe_resolve(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {rel_path}")
        size = resolved.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise FileSizeError(f"File exceeds size limit: {rel_path}")
        content = resolved.read_bytes()
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EncodingError(f"File is not valid UTF-8: {rel_path}") from exc

    def calculate_hash(self, rel_path: str) -> str:
        content = self.read_file(rel_path).encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    def write_file_atomic(self, rel_path: str, content: str, expected_hash: str | None = None) -> str:
        path = self._root / rel_path
        self._validate_css_path(Path(rel_path))
        resolved_dir = self._safe_resolve(path.parent) if path.parent != self._root else self._root
        # For new files, parent dir must be within root
        # Resolve parent explicitly
        parent_path = self._root / Path(rel_path).parent
        try:
            resolved_dir = parent_path.resolve()
            resolved_dir.relative_to(self._root)
        except (OSError, ValueError):
            raise TraversalError("Parent directory escapes the override root")
        # Verify existing hash for optimistic lock
        if expected_hash is not None and path.exists():
            actual = self.calculate_hash(rel_path)
            if actual != expected_hash:
                raise HashConflictError(f"Hash mismatch for {rel_path}")
        try:
            encoded = content.encode("utf-8")
        except (UnicodeEncodeError, AttributeError) as exc:
            raise EncodingError("Content is not valid UTF-8") from exc
        if len(encoded) > _MAX_FILE_BYTES:
            raise FileSizeError("Content exceeds per-file size limit")
        resolved_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(resolved_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(encoded)
            os.replace(tmp_path, str(self._root / rel_path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return hashlib.sha256(encoded).hexdigest()

    def delete_file_atomic(self, rel_path: str, expected_hash: str) -> None:
        path = self._root / rel_path
        self._validate_css_path(Path(rel_path))
        resolved = self._safe_resolve(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {rel_path}")
        actual = self.calculate_hash(rel_path)
        if actual != expected_hash:
            raise HashConflictError(f"Hash mismatch for {rel_path}")
        os.unlink(str(resolved))
