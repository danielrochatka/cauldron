"""UIOverrideStore — safe read/write service for site-owned CSS override files."""
from __future__ import annotations

import hashlib
import os
import threading
import tempfile
from pathlib import Path
from typing import Literal

_VALID_SCOPES = frozenset({"admin", "pages"})
_MAX_FILE_BYTES = 256 * 1024       # 256 KB per file
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 # 2 MB for entire override root

# Sentinel for "file must not exist" in optimistic-lock operations.
ABSENT = "__absent__"

Scope = Literal["admin", "pages"]


class OverrideStoreError(Exception):
    """Base error for UIOverrideStore operations."""


class TraversalError(OverrideStoreError):
    """Path traversal or symlink escape detected."""


class InvalidScopeError(OverrideStoreError):
    """Unknown scope (must be 'admin' or 'pages')."""


class InvalidFileError(OverrideStoreError):
    """File rejected (non-CSS, hidden, root-level, cross-scope, etc.)."""


class FileSizeError(OverrideStoreError):
    """File or total size limit exceeded."""


class HashConflictError(OverrideStoreError):
    """Optimistic-lock hash mismatch — file state differs from expected."""


class EncodingError(OverrideStoreError):
    """Content is not valid UTF-8."""


class MissingExpectedHashError(OverrideStoreError):
    """No expected hash provided; operation rejected to prevent blind overwrite."""


class UIOverrideStore:
    """Safe read/write store for site-owned CSS override files.

    All public methods accept a *scope* ('admin' or 'pages') and a
    *relative path* that is always relative to that scope directory.
    Paths are validated to prevent traversal, symlink escape, cross-scope
    access, non-CSS files, hidden-path components, and root-level placement.

    Optimistic locking:
      - Pass the current file's SHA-256 hex digest as ``expected_hash`` to
        assert the file's current content before writing.
      - Pass ``ABSENT`` to assert that the file does not yet exist.
      - Omitting ``expected_hash`` (``None``) is rejected by ``write_file_atomic``
        and ``delete_file_atomic`` to prevent blind overwrites.

    Size limits:
      - Per-file: 256 KB
      - Total override root: 2 MB (checked before every write)

    Concurrency: per-target locks ensure only one write/delete runs at a time
    for the same resolved path. Reads are unsynchronized (CSS files are
    immutable once written; the OS guarantees atomic rename visibility).
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._path_locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Internal helpers                                                      #
    # ------------------------------------------------------------------ #

    def _scope_dir(self, scope: Scope) -> Path:
        if scope not in _VALID_SCOPES:
            raise InvalidScopeError(f"Unknown scope: {scope!r}. Valid scopes: admin, pages.")
        return self._root / scope

    def _resolve_path(self, scope: Scope, relative_path: str) -> tuple[Path, Path]:
        """Return (scope_dir, absolute_path).  Raises on any safety violation."""
        scope_dir = self._scope_dir(scope)
        rel = Path(relative_path)

        # Reject absolute paths
        if rel.is_absolute():
            raise InvalidFileError("Relative path must not be absolute.")

        # Reject root-level CSS (must be nested inside scope subdir)
        if len(rel.parts) < 1:
            raise InvalidFileError("Path must not be empty.")

        # Reject hidden components
        for part in rel.parts:
            if part.startswith("."):
                raise InvalidFileError(f"Hidden path component not allowed: {part!r}.")

        # Reject non-CSS extensions
        if rel.suffix.lower() != ".css":
            raise InvalidFileError(f"Only .css files are accepted: {rel.name!r}.")

        candidate = scope_dir / rel

        # Resolve symlinks and ensure containment
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise TraversalError("Cannot resolve path.") from exc

        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise TraversalError("Path escapes the override root.")

        # Reject if any symlink in the chain points outside the root
        check = candidate
        while True:
            try:
                if check.is_symlink():
                    target = Path(os.readlink(str(check))).resolve()
                    try:
                        target.relative_to(self._root)
                    except ValueError:
                        raise TraversalError("Symlink escapes the override root.")
                parent = check.parent
                if parent == check:
                    break
                if parent == self._root or not str(parent).startswith(str(self._root)):
                    break
                check = parent
            except (OSError, ValueError):
                break

        # Ensure resolved path is still under the correct scope
        try:
            resolved.relative_to(scope_dir.resolve())
        except ValueError:
            raise InvalidFileError("Path escapes the scope directory.")

        return scope_dir, resolved

    def _path_lock(self, resolved: Path) -> threading.Lock:
        key = str(resolved)
        with self._meta_lock:
            if key not in self._path_locks:
                self._path_locks[key] = threading.Lock()
            return self._path_locks[key]

    def _sha256(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _total_size(self) -> int:
        total = 0
        if self._root.is_dir():
            for p in self._root.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        return total

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def list_files(self, scope: Scope) -> list[str]:
        """Return sorted relative paths (relative to scope dir) of all valid CSS files."""
        scope_dir = self._scope_dir(scope)
        if not scope_dir.is_dir():
            return []
        results = []
        for item in sorted(scope_dir.rglob("*.css")):
            if item.is_dir():
                continue
            # Skip hidden path components
            rel_to_scope = item.relative_to(scope_dir)
            if any(p.startswith(".") for p in rel_to_scope.parts):
                continue
            try:
                _, resolved = self._resolve_path(scope, str(rel_to_scope).replace(os.sep, "/"))
                if resolved.is_file():
                    results.append(str(rel_to_scope).replace(os.sep, "/"))
            except (TraversalError, InvalidFileError, InvalidScopeError, OSError):
                continue
        return sorted(results)

    def read_file(self, scope: Scope, relative_path: str) -> str:
        """Read and return file content as a string."""
        _, resolved = self._resolve_path(scope, relative_path)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {relative_path!r} in scope {scope!r}.")
        size = resolved.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise FileSizeError("File exceeds per-file size limit.")
        raw = resolved.read_bytes()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EncodingError("File is not valid UTF-8.") from exc

    def calculate_hash(self, scope: Scope, relative_path: str) -> str:
        """Return SHA-256 hex digest of the file's current content."""
        content = self.read_file(scope, relative_path).encode("utf-8")
        return self._sha256(content)

    def write_file_atomic(
        self,
        scope: Scope,
        relative_path: str,
        content: str,
        expected_hash: str,
    ) -> str:
        """Write content atomically to scope/relative_path.

        ``expected_hash`` must be either:
        - The current file's SHA-256 hex digest (optimistic lock for updates), or
        - ``ABSENT`` (asserts the file does not yet exist).

        Returns the SHA-256 digest of the written content.

        Raises:
            MissingExpectedHashError: if expected_hash is None
            HashConflictError: if the file's current state disagrees with expected_hash
            FileSizeError: if content or total size limit would be exceeded
            EncodingError: if content is not valid UTF-8
        """
        if expected_hash is None:
            raise MissingExpectedHashError(
                "expected_hash is required. Pass ABSENT for new files or the current hash for updates."
            )

        _, resolved = self._resolve_path(scope, relative_path)

        try:
            encoded = content.encode("utf-8")
        except (UnicodeEncodeError, AttributeError) as exc:
            raise EncodingError("Content is not valid UTF-8.") from exc

        if len(encoded) > _MAX_FILE_BYTES:
            raise FileSizeError("Content exceeds per-file size limit.")

        lock = self._path_lock(resolved)
        with lock:
            # Re-resolve immediately inside the lock
            try:
                current_stat = resolved.stat() if resolved.exists() else None
            except OSError:
                current_stat = None

            file_exists = current_stat is not None

            if expected_hash == ABSENT:
                if file_exists:
                    raise HashConflictError(
                        "File already exists but ABSENT was expected."
                    )
            else:
                if not file_exists:
                    raise HashConflictError(
                        "File does not exist but a hash was expected."
                    )
                # Revalidate immediately before replacement
                try:
                    actual = self._sha256(resolved.read_bytes())
                except OSError as exc:
                    raise TraversalError("Cannot read file for hash revalidation.") from exc
                if actual != expected_hash:
                    raise HashConflictError("File was modified since the hash was captured.")

            # Check total size limit (approximate; excludes the file being replaced)
            current_file_size = current_stat.st_size if current_stat else 0
            total = self._total_size() - current_file_size + len(encoded)
            if total > _MAX_TOTAL_BYTES:
                raise FileSizeError("Write would exceed total override-root size limit.")

            resolved.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(resolved.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(encoded)
                os.replace(tmp_path, str(resolved))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        return self._sha256(encoded)

    def delete_file_atomic(
        self,
        scope: Scope,
        relative_path: str,
        expected_hash: str,
    ) -> None:
        """Delete the file if its current hash matches expected_hash.

        ``expected_hash`` must be the current SHA-256 hex digest of the file.
        Pass ``ABSENT`` to get a HashConflictError rather than a
        FileNotFoundError if the file is already missing.

        Raises:
            MissingExpectedHashError: if expected_hash is None
            HashConflictError: if the file's hash does not match, or ABSENT was passed
              for a non-existent file (already deleted)
            FileNotFoundError: only when expected_hash is ABSENT and file is missing
        """
        if expected_hash is None:
            raise MissingExpectedHashError("expected_hash is required for delete.")

        _, resolved = self._resolve_path(scope, relative_path)

        lock = self._path_lock(resolved)
        with lock:
            if not resolved.is_file():
                if expected_hash == ABSENT:
                    return  # Already absent — idempotent delete
                raise FileNotFoundError(f"File not found: {relative_path!r} in scope {scope!r}.")

            actual = self._sha256(resolved.read_bytes())
            if actual != expected_hash:
                raise HashConflictError("File was modified since the hash was captured.")

            os.unlink(str(resolved))
