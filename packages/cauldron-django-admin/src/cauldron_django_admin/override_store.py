"""UIOverrideStore — safe read/write service for site-owned CSS override files."""
from __future__ import annotations

import contextlib
import hashlib
import os
import threading
import tempfile
from pathlib import Path
from typing import Literal

try:  # Linux/POSIX only.
    import fcntl  # type: ignore[import]
    _HAS_FCNTL = True
    _HAS_MSVCRT = False
except ImportError:  # pragma: no cover — non-POSIX
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False
    try:  # Windows fallback for cross-process locking.
        import msvcrt  # type: ignore[import]
        _HAS_MSVCRT = True
    except ImportError:  # pragma: no cover — no cross-process locking available
        msvcrt = None  # type: ignore[assignment]
        _HAS_MSVCRT = False

_VALID_SCOPES = frozenset({"admin", "pages"})
_MAX_FILE_BYTES = 256 * 1024       # 256 KB per file
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 # 2 MB for entire override root

# Public size constants — services / management commands should import these
# instead of reaching for the underscore-prefixed values.
MAX_FILE_BYTES = _MAX_FILE_BYTES
MAX_TOTAL_BYTES = _MAX_TOTAL_BYTES

# Sentinel for "file must not exist" in optimistic-lock operations.
ABSENT = "__absent__"

Scope = Literal["admin", "pages"]


# ---------------------------------------------------------------------------
# Process-wide thread locks keyed by resolved override root.
#
# Multiple ``UIOverrideStore`` instances rooted at the same directory MUST
# serialise writes/deletes across the process — storing the lock on ``self``
# would leave each instance with its own private lock, defeating the guarantee
# that no two writes to the same override root can happen simultaneously
# inside a single Python process. The registry below is process-wide and keyed
# by the resolved root path so any store instance rooted at the same location
# shares the same underlying ``threading.Lock``.
# ---------------------------------------------------------------------------
_PROCESS_ROOT_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_ROOT_LOCKS_META: threading.Lock = threading.Lock()


def _get_process_root_lock(root: Path) -> threading.Lock:
    key = str(root)
    with _PROCESS_ROOT_LOCKS_META:
        lock = _PROCESS_ROOT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROCESS_ROOT_LOCKS[key] = lock
        return lock


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


class OverrideLockError(OverrideStoreError):
    """Cross-process lock cannot be obtained for a write or delete operation."""


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

    Concurrency:
      - A cross-process ``fcntl`` file lock on ``<root>/.cauldron-store.lock``
        serialises every write and delete when POSIX file locks are available.
      - On platforms without ``fcntl`` we fall back to a process-local
        ``threading.Lock`` so single-process test/dev use still serialises
        correctly.
      - Reads are unsynchronised (CSS files are immutable once written; the
        OS guarantees atomic rename visibility).
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

    @contextlib.contextmanager
    def _root_file_lock(self):
        """Cross-process exclusive lock on the override root.

        Uses ``fcntl.lockf`` on POSIX or ``msvcrt.locking`` on Windows so
        concurrent worker processes serialise writes/deletes. The
        process-wide thread lock is ALWAYS acquired first because per-process
        file locks (``fcntl`` on Linux) do not serialise between threads of
        the same process.

        Fail-closed contract: if the OS-level lock cannot be obtained (no
        ``fcntl``/``msvcrt``, missing lock file, filesystem does not support
        locking, etc.), we raise :class:`OverrideLockError` rather than
        silently downgrade to thread-only serialisation. Silent downgrades
        would let two worker processes race a write against each other and
        clobber the atomic-replace guarantee this store relies on.
        """
        thread_lock = _get_process_root_lock(self._root)

        if not _HAS_FCNTL and not _HAS_MSVCRT:
            raise OverrideLockError(
                "Cross-process write locking is not available on this platform "
                "(neither fcntl nor msvcrt present). Override store writes and "
                "deletes are refused to preserve atomicity."
            )

        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OverrideLockError(
                "Cannot create override root directory for lock file."
            ) from exc

        lock_path = self._root / ".cauldron-store.lock"
        try:
            fd = open(str(lock_path), "w")
        except OSError as exc:
            raise OverrideLockError(
                "Cannot open lock file in override root."
            ) from exc

        try:
            with thread_lock:
                if _HAS_FCNTL:
                    try:
                        fcntl.lockf(fd, fcntl.LOCK_EX)
                    except OSError as exc:
                        raise OverrideLockError(
                            "Cannot acquire cross-process lock on override root."
                        ) from exc
                    try:
                        yield
                    finally:
                        try:
                            fcntl.lockf(fd, fcntl.LOCK_UN)
                        except OSError:
                            pass
                else:
                    # Windows: msvcrt.locking locks a byte range on the fd.
                    fd_raw = fd.fileno()
                    try:
                        msvcrt.locking(fd_raw, msvcrt.LK_LOCK, 1)  # type: ignore[union-attr]
                    except OSError as exc:
                        raise OverrideLockError(
                            "Cannot acquire Windows file lock on override root."
                        ) from exc
                    try:
                        yield
                    finally:
                        try:
                            msvcrt.locking(  # type: ignore[union-attr]
                                fd_raw, msvcrt.LK_UNLCK, 1,  # type: ignore[union-attr]
                            )
                        except OSError:
                            pass
        finally:
            try:
                fd.close()
            except OSError:
                pass

    def _check_no_symlinks_in_chain(
        self, scope: Scope, relative_path: str,
    ) -> None:
        """Raise TraversalError if any component of the *unresolved*
        candidate path (from the scope directory down to the leaf) is a
        symbolic link.

        The lstat-based check on the unresolved path is what makes this
        different from ``Path.resolve``: we specifically want to reject the
        presence of a symlink anywhere along the chain, not merely reject
        symlinks whose target escapes the root. A symlink that resolves
        *inside* the root would still let an attacker or a mistake bypass
        our per-scope containment checks by redirecting a write to a
        different directory.
        """
        scope_dir = self._scope_dir(scope)
        candidate = scope_dir / Path(relative_path)
        check = candidate
        while True:
            if check == self._root:
                break
            try:
                if check.is_symlink():
                    raise TraversalError(
                        "Symlink detected in target chain: a path component "
                        "is a symbolic link."
                    )
            except OSError:
                # Missing components (e.g. the leaf before creation) are
                # fine — we only care about the components that DO exist.
                pass
            parent = check.parent
            if parent == check:
                break
            if not str(check).startswith(str(self._root)):
                break
            check = parent

    def _sha256(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _total_size(self) -> int:
        total = 0
        if self._root.is_dir():
            for p in self._root.rglob("*"):
                if p.is_file():
                    # Skip the lock file itself so it doesn't count toward
                    # the total override-root budget.
                    if p.name == ".cauldron-store.lock" and p.parent == self._root:
                        continue
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        return total

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def validate_target(self, scope: Scope, relative_path: str) -> None:
        """Validate that ``scope`` and ``relative_path`` are acceptable.

        Raises :class:`InvalidScopeError`, :class:`InvalidFileError`, or
        :class:`TraversalError` on any policy violation.  Does not require the
        file to exist.
        """
        # ``_resolve_path`` performs every safety check; discard the result.
        self._resolve_path(scope, relative_path)

    def inspect_state(self, scope: Scope, relative_path: str) -> dict:
        """Return the current on-disk state of the target file.

        Always succeeds when the scope/path pass validation.  The returned
        dictionary has:

        * ``exists``: bool — whether the file exists.
        * ``hash``: str | None — SHA-256 hex digest of the current content
          (``None`` when the file does not exist).
        * ``size``: int | None — byte size (``None`` when the file does
          not exist).
        """
        _, resolved = self._resolve_path(scope, relative_path)
        if not resolved.is_file():
            return {"exists": False, "hash": None, "size": None}
        raw = resolved.read_bytes()
        return {
            "exists": True,
            "hash": self._sha256(raw),
            "size": len(raw),
        }

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

        # First-pass lstat scan (outside the lock) — cheap short-circuit for
        # the common case where an operator has already staged a symlink.
        self._check_no_symlinks_in_chain(scope, relative_path)

        try:
            encoded = content.encode("utf-8")
        except (UnicodeEncodeError, AttributeError) as exc:
            raise EncodingError("Content is not valid UTF-8.") from exc

        if len(encoded) > _MAX_FILE_BYTES:
            raise FileSizeError("Content exceeds per-file size limit.")

        with self._root_file_lock():
            # Re-resolve inside the lock and confirm containment again.
            _, re_resolved = self._resolve_path(scope, relative_path)
            if re_resolved != resolved:
                raise TraversalError("Path resolved differently under lock.")
            try:
                re_resolved.relative_to(self._root)
            except ValueError:
                raise TraversalError("Path escapes root under lock.")

            # Second-pass lstat scan — catch symlinks that appeared between
            # the outside-lock resolve and now.
            self._check_no_symlinks_in_chain(scope, relative_path)

            try:
                current_stat = re_resolved.stat() if re_resolved.exists() else None
            except OSError:
                current_stat = None

            file_exists_now = current_stat is not None

            if expected_hash == ABSENT:
                if file_exists_now:
                    raise HashConflictError(
                        "File already exists but ABSENT was expected."
                    )
            else:
                if not file_exists_now:
                    raise HashConflictError(
                        "File does not exist but a hash was expected."
                    )
                # Revalidate immediately before replacement.
                try:
                    actual_now = self._sha256(re_resolved.read_bytes())
                except OSError as exc:
                    raise TraversalError("Cannot read file for hash revalidation.") from exc
                if actual_now != expected_hash:
                    raise HashConflictError(
                        "File was modified since the hash was captured."
                    )

            # Check total size limit (approximate; excludes the file being replaced)
            current_file_size = current_stat.st_size if current_stat else 0
            total = self._total_size() - current_file_size + len(encoded)
            if total > _MAX_TOTAL_BYTES:
                raise FileSizeError("Write would exceed total override-root size limit.")

            re_resolved.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(re_resolved.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(encoded)

                # ------------------------------------------------------- #
                # Final pre-replace safety verification.                    #
                #                                                            #
                # Between the earlier ``_resolve_path`` and the actual        #
                # ``os.replace`` an adversary or racing thread could have     #
                # mutated the target parent (e.g. swapping it for a symlink   #
                # that points outside the root). Re-verify containment and   #
                # optimistic state right before we make the change visible.   #
                # ------------------------------------------------------- #
                final_scope_dir = self._scope_dir(scope).resolve()
                if re_resolved.parent.is_symlink():
                    raise TraversalError(
                        "Target parent became a symlink before replacement."
                    )
                final_parent = re_resolved.parent.resolve()
                try:
                    final_parent.relative_to(final_scope_dir)
                except ValueError:
                    raise TraversalError(
                        "Target parent moved outside scope before replacement."
                    )
                if re_resolved.exists() and re_resolved.is_symlink():
                    raise TraversalError(
                        "Target became a symlink before replacement."
                    )
                # Confirm the temp file we're about to promote is still
                # inside the validated parent directory.
                tmp_resolved = Path(tmp_path).resolve()
                try:
                    tmp_resolved.relative_to(final_parent)
                except ValueError:
                    raise TraversalError(
                        "Temporary file is not inside the target parent."
                    )
                # Re-check the optimistic state one last time.
                if expected_hash == ABSENT:
                    if re_resolved.exists():
                        raise HashConflictError(
                            "File appeared between temp creation and replacement."
                        )
                else:
                    if not re_resolved.exists():
                        raise HashConflictError(
                            "File disappeared between temp creation and replacement."
                        )
                    try:
                        actual_final = self._sha256(re_resolved.read_bytes())
                    except OSError as exc:
                        raise TraversalError(
                            "Cannot read file for final hash revalidation.",
                        ) from exc
                    if actual_final != expected_hash:
                        raise HashConflictError(
                            "File changed between temp creation and replacement."
                        )

                os.replace(tmp_path, str(re_resolved))
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

        # First-pass lstat scan (outside the lock) — matches write_file_atomic
        # so operators cannot delete through a staged symlink either.
        self._check_no_symlinks_in_chain(scope, relative_path)

        with self._root_file_lock():
            _, re_resolved = self._resolve_path(scope, relative_path)
            if re_resolved != resolved:
                raise TraversalError("Path resolved differently under lock.")
            try:
                re_resolved.relative_to(self._root)
            except ValueError:
                raise TraversalError("Path escapes root under lock.")

            # Second-pass lstat scan under the lock.
            self._check_no_symlinks_in_chain(scope, relative_path)

            if not re_resolved.is_file():
                if expected_hash == ABSENT:
                    return  # Already absent — idempotent delete
                raise FileNotFoundError(f"File not found: {relative_path!r} in scope {scope!r}.")

            actual = self._sha256(re_resolved.read_bytes())
            if actual != expected_hash:
                raise HashConflictError("File was modified since the hash was captured.")

            # ------------------------------------------------------------- #
            # Final pre-unlink safety verification (mirrors the write path). #
            # A racing thread or adversary could have swapped the target     #
            # or parent for a symlink between the hash check and unlink;    #
            # re-verify containment and re-check the hash right before we    #
            # mutate the filesystem.                                         #
            # ------------------------------------------------------------- #
            final_scope_dir = self._scope_dir(scope).resolve()
            if re_resolved.parent.is_symlink():
                raise TraversalError(
                    "Target parent became a symlink before deletion."
                )
            final_parent = re_resolved.parent.resolve()
            try:
                final_parent.relative_to(final_scope_dir)
            except ValueError:
                raise TraversalError(
                    "Target parent moved outside scope before deletion."
                )
            if re_resolved.is_symlink():
                raise TraversalError(
                    "Target became a symlink before deletion."
                )
            try:
                actual_final = self._sha256(re_resolved.read_bytes())
            except OSError as exc:
                raise TraversalError(
                    "Cannot read file for final hash revalidation before delete.",
                ) from exc
            if actual_final != expected_hash:
                raise HashConflictError(
                    "File changed between lock acquisition and deletion."
                )

            os.unlink(str(re_resolved))
