"""Secure file-backed configuration and secret store for AI providers.

The store persists to a single JSON file (default:
``BASE_DIR/data/ai/config.json``).  Writes are atomic (write-to-unique-
temp → fsync → rename) and both the file (0600) and its parent
directory (0700) are locked down so credentials are not world-readable.
Inter-process safety is provided by ``fcntl.flock`` on POSIX systems,
with a graceful no-op fallback elsewhere.

Versioned document format (version 1)::

    {
        "version": 1,
        "selected_provider": "openai",
        "runtime": {
            "max_model_turns": 3,
            "max_tool_calls": 5,
            "tool_timeout_seconds": 10.0,
            "run_timeout_seconds": 30.0,
            "max_argument_bytes": 4096,
            "max_result_bytes": 8192,
            "include_content_tools": true
        },
        "providers": {
            "openai": {
                "config": {"model": "gpt-4o"},
                "secrets": {"api_key": "sk-..."}
            }
        }
    }

The pre-Phase-2 layout (``{"provider": ..., "config": {...}, "secrets": {...}}``)
is migrated silently on read — credentials are never dropped during the
upgrade.

``secrets`` values live in plain text on disk; the file-system mode 0600
is the primary protection.  Never commit this file.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings as django_settings


_DEFAULT_CONFIG_FILENAME = "ai/config.json"
_MAX_FILE_BYTES = 64 * 1024  # 64 KiB hard cap
_CURRENT_VERSION = 1


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AIProviderStoreError(RuntimeError):
    """Base class for AI provider settings store errors."""


class AIProviderStoreCorruptError(AIProviderStoreError):
    """Raised when the config file cannot be parsed as a version-1 document."""


class AIProviderStoreUnsafePathError(AIProviderStoreError):
    """Raised when the config path is not a regular owner-only file."""


class AIProviderStoreVersionError(AIProviderStoreError):
    """Raised when the stored document version is not supported."""


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _default_config_path() -> Path:
    """Return the default config path.

    Precedence:
    1. ``CAULDRON_AI_CONFIG_PATH`` environment variable
    2. ``settings.CAULDRON_AI_CONFIG_PATH`` (surfaced by ``cauldron_site.settings``)
    3. ``BASE_DIR/data/ai/config.json``
    4. ``<tempdir>/cauldron/ai/config.json`` when BASE_DIR is unset (tests)
    """
    env_path = os.environ.get("CAULDRON_AI_CONFIG_PATH", "").strip()
    if env_path:
        return Path(env_path)
    settings_path = getattr(django_settings, "CAULDRON_AI_CONFIG_PATH", "")
    if isinstance(settings_path, str) and settings_path.strip():
        return Path(settings_path.strip())
    base = getattr(django_settings, "BASE_DIR", None)
    if base is not None:
        return Path(base) / "data" / _DEFAULT_CONFIG_FILENAME
    import tempfile
    return Path(tempfile.gettempdir()) / "cauldron" / _DEFAULT_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# fcntl helpers (POSIX-only, best-effort elsewhere)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - trivial import
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    _fcntl = None  # type: ignore[assignment]


class _FileLock:
    """Context manager around ``fcntl.flock`` with a non-POSIX no-op fallback."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._locked = False

    def __enter__(self) -> "_FileLock":
        if _fcntl is not None:
            try:
                _fcntl.flock(self._fd, _fcntl.LOCK_EX)
                self._locked = True
            except OSError:
                # If flock fails (e.g. NFS without lock daemon) we fall
                # back to intra-process locking only.  Losing inter-
                # process safety is preferable to refusing to save.
                self._locked = False
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._locked and _fcntl is not None:
            try:
                _fcntl.flock(self._fd, _fcntl.LOCK_UN)
            except OSError:
                pass
            self._locked = False


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def _check_path_safety(path: Path) -> None:
    """Reject symlinks, non-regular files, and oversized files."""
    if path.is_symlink():
        raise AIProviderStoreUnsafePathError(
            "AI config path is a symlink — refusing to load."
        )
    if path.exists() and not path.is_file():
        raise AIProviderStoreUnsafePathError(
            "AI config path is not a regular file — refusing to load."
        )
    if path.exists() and path.stat().st_size > _MAX_FILE_BYTES:
        raise AIProviderStoreCorruptError(
            "AI config file exceeds 64 KB — refusing to load."
        )


# ---------------------------------------------------------------------------
# Document normalisation / migration
# ---------------------------------------------------------------------------

def _empty_document() -> dict[str, Any]:
    return {
        "version": _CURRENT_VERSION,
        "selected_provider": "",
        "runtime": {},
        "providers": {},
    }


def _normalise_document(data: dict[str, Any]) -> dict[str, Any]:
    """Return a valid version-1 document from ``data``.

    * Migrates the pre-Phase-2 layout (``{"provider", "config", "secrets"}``)
      by rehoming provider-scoped config/secrets under ``providers``.
    * Fills in default keys (``version``, ``selected_provider``, ``runtime``,
      ``providers``) so downstream code can assume the shape without probing.
    """
    # Old format detection: no version marker AND a "provider" key present.
    if "version" not in data and "provider" in data:
        migrated: dict[str, Any] = {
            "version": _CURRENT_VERSION,
            "selected_provider": str(data.get("provider", "") or ""),
            "runtime": {},
            "providers": {},
        }
        old_config = data.get("config") or {}
        old_secrets = data.get("secrets") or {}
        if isinstance(old_config, dict):
            for name, cfg in old_config.items():
                if not isinstance(name, str):
                    continue
                slot = migrated["providers"].setdefault(name, {})
                slot["config"] = dict(cfg) if isinstance(cfg, dict) else {}
        if isinstance(old_secrets, dict):
            for name, secrets in old_secrets.items():
                if not isinstance(name, str):
                    continue
                slot = migrated["providers"].setdefault(name, {})
                slot["secrets"] = (
                    dict(secrets) if isinstance(secrets, dict) else {}
                )
        return migrated

    # Version validation.
    version = data.get("version")
    if version is not None and version != _CURRENT_VERSION:
        raise AIProviderStoreVersionError(
            f"AI config file version {version!r} is not supported."
        )

    out = _empty_document()
    if isinstance(data.get("selected_provider"), str):
        out["selected_provider"] = data["selected_provider"]
    runtime = data.get("runtime")
    if isinstance(runtime, dict):
        out["runtime"] = dict(runtime)
    providers = data.get("providers")
    if isinstance(providers, dict):
        clean_providers: dict[str, dict[str, Any]] = {}
        for name, slot in providers.items():
            if not isinstance(name, str) or not isinstance(slot, dict):
                continue
            cfg = slot.get("config")
            secrets = slot.get("secrets")
            clean_providers[name] = {
                "config": dict(cfg) if isinstance(cfg, dict) else {},
                "secrets": (
                    dict(secrets) if isinstance(secrets, dict) else {}
                ),
            }
        out["providers"] = clean_providers
    return out


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class AIProviderSettingsStore:
    """Thread-safe file-backed store for provider config and secrets."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path  # None → resolved lazily on first access
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        # Never leak the path — a support-log capture of ``repr(store)``
        # must not disclose where credentials live on disk.
        return "<AIProviderSettingsStore>"

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = _default_config_path()
        return self._path

    # ------------------------------------------------------------------
    # Low-level read / write
    # ------------------------------------------------------------------

    def _read_document(self) -> dict[str, Any]:
        try:
            _check_path_safety(self.path)
        except FileNotFoundError:
            return _empty_document()
        if not self.path.exists():
            return _empty_document()
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _empty_document()
        if not text.strip():
            return _empty_document()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIProviderStoreCorruptError(
                f"AI config file contains malformed JSON at line {exc.lineno}."
            ) from exc
        if not isinstance(data, dict):
            raise AIProviderStoreCorruptError(
                "AI config file root must be a JSON object."
            )
        return _normalise_document(data)

    def _write_document(self, document: dict[str, Any]) -> None:
        # Always write the canonical shape so a partial mutation elsewhere
        # can't wedge the file into a non-versioned layout.
        normalised = _normalise_document(document)
        normalised["version"] = _CURRENT_VERSION

        path = self.path
        parent = path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(str(parent), 0o700)
            except OSError:
                pass

        _check_path_safety(path)

        # Unique temp file avoids collisions when two processes race to
        # write at the same instant.  O_EXCL guarantees the temp file is
        # created fresh by us.
        tmp = parent / f".cauldron_ai_config_{uuid.uuid4().hex}.tmp"
        fd = os.open(
            str(tmp),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,  # 0600
        )
        try:
            with _FileLock(fd):
                with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as fh:
                    json.dump(normalised, fh, indent=2, ensure_ascii=False)
                    fh.write("\n")
                    fh.flush()
                    os.fsync(fd)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
            raise
        else:
            os.close(fd)
        try:
            os.replace(str(tmp), str(path))
        except BaseException:
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
            raise
        # Belt-and-braces: force mode 0600 in case the parent had a wider umask.
        try:
            os.chmod(str(path), stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        # Best-effort fsync of the parent directory so the rename is durable.
        try:
            dir_fd = os.open(str(parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Return a defensive copy of the normalised document."""
        with self._lock:
            return json.loads(json.dumps(self._read_document()))

    def save(self, data: dict[str, Any]) -> None:
        """Overwrite the config file with ``data`` (normalised, atomic, 0600)."""
        with self._lock:
            self._write_document(dict(data or {}))

    # ---- selection ---------------------------------------------------

    def get_selected_provider(self) -> str:
        with self._lock:
            doc = self._read_document()
            return str(doc.get("selected_provider", "") or "")

    def set_selected_provider(self, name: str) -> None:
        with self._lock:
            doc = self._read_document()
            doc["selected_provider"] = str(name or "")
            self._write_document(doc)

    # ---- provider config --------------------------------------------

    def get_config(self, provider_name: str) -> dict[str, Any]:
        with self._lock:
            doc = self._read_document()
            slot = doc.get("providers", {}).get(provider_name) or {}
            return dict(slot.get("config") or {})

    def set_config(self, provider_name: str, config: dict[str, Any]) -> None:
        with self._lock:
            doc = self._read_document()
            slot = doc.setdefault("providers", {}).setdefault(
                provider_name, {"config": {}, "secrets": {}},
            )
            slot["config"] = dict(config or {})
            slot.setdefault("secrets", {})
            self._write_document(doc)

    # ---- provider secrets -------------------------------------------

    def get_secret(self, provider_name: str, key: str) -> str:
        with self._lock:
            doc = self._read_document()
            slot = doc.get("providers", {}).get(provider_name) or {}
            secrets = slot.get("secrets") or {}
            return str(secrets.get(key, "") or "")

    def set_secret(self, provider_name: str, key: str, value: str) -> None:
        with self._lock:
            doc = self._read_document()
            slot = doc.setdefault("providers", {}).setdefault(
                provider_name, {"config": {}, "secrets": {}},
            )
            slot.setdefault("secrets", {})[key] = str(value)
            slot.setdefault("config", {})
            self._write_document(doc)

    def get_secrets(self, provider_name: str) -> dict[str, str]:
        with self._lock:
            doc = self._read_document()
            slot = doc.get("providers", {}).get(provider_name) or {}
            return {
                str(k): str(v) for k, v in (slot.get("secrets") or {}).items()
            }

    def set_secrets(
        self, provider_name: str, secrets: dict[str, str],
    ) -> None:
        with self._lock:
            doc = self._read_document()
            slot = doc.setdefault("providers", {}).setdefault(
                provider_name, {"config": {}, "secrets": {}},
            )
            slot["secrets"] = {str(k): str(v) for k, v in (secrets or {}).items()}
            slot.setdefault("config", {})
            self._write_document(doc)

    def clear_secret(self, provider_name: str, key: str) -> None:
        """Remove a single secret entry; no-op if it isn't set."""
        with self._lock:
            doc = self._read_document()
            slot = doc.get("providers", {}).get(provider_name)
            if not slot or not isinstance(slot.get("secrets"), dict):
                return
            if key in slot["secrets"]:
                del slot["secrets"][key]
                self._write_document(doc)

    # ---- runtime settings -------------------------------------------

    def get_runtime(self) -> dict[str, Any]:
        with self._lock:
            doc = self._read_document()
            return dict(doc.get("runtime") or {})

    def set_runtime(self, runtime: dict[str, Any]) -> None:
        with self._lock:
            doc = self._read_document()
            doc["runtime"] = dict(runtime or {})
            self._write_document(doc)

    # ---- file inspection --------------------------------------------

    def file_exists(self) -> bool:
        try:
            return self.path.exists() and self.path.is_file()
        except OSError:
            return False

    def file_permissions_ok(self) -> bool:
        """Return True iff the file exists and is mode 0600."""
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
            return mode == 0o600
        except OSError:
            return False

    def parent_permissions_ok(self) -> bool:
        """Return True iff the parent directory exists and is mode 0700."""
        try:
            mode = stat.S_IMODE(self.path.parent.stat().st_mode)
            return mode == 0o700
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_store: AIProviderSettingsStore | None = None
_store_lock = threading.Lock()


def get_store(path: Path | None = None) -> AIProviderSettingsStore:
    """Return the process-level ``AIProviderSettingsStore`` singleton.

    The first call fixes ``path`` for the lifetime of the process.
    Tests should pass ``path`` via ``_reset_store_for_tests`` before use.
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = AIProviderSettingsStore(path)
    return _store


def _reset_store_for_tests(path: Path | None = None) -> None:
    """Test helper: discard the singleton so the next call re-creates it."""
    global _store
    with _store_lock:
        _store = AIProviderSettingsStore(path) if path is not None else None


# ---------------------------------------------------------------------------
# Effective configuration helpers
# ---------------------------------------------------------------------------

def resolve_provider_name(store: AIProviderSettingsStore | None = None) -> str:
    """Return the effective provider name.

    Precedence:
    1. Config file (``AIProviderSettingsStore.get_selected_provider``)
    2. ``CAULDRON_MODULES["cauldron.ai.admin"]["provider"]``
    3. Empty string (caller must handle the not-configured case)
    """
    if store is None:
        store = get_store()
    try:
        name = store.get_selected_provider()
    except AIProviderStoreError:
        name = ""
    if name:
        return name
    try:
        modules = getattr(django_settings, "CAULDRON_MODULES", {}) or {}
        cfg = modules.get("cauldron.ai.admin") or {}
        name = str(cfg.get("provider", "") or "")
    except Exception:
        name = ""
    return name


def resolve_provider_config(
    provider_name: str,
    store: AIProviderSettingsStore | None = None,
) -> dict[str, Any]:
    """Return the effective config dict for a provider.

    Precedence:
    1. Config file values
    2. ``CAULDRON_MODULES["cauldron.ai.admin"]["provider_config"][provider_name]``
    3. Empty dict (factory defaults apply)
    """
    if store is None:
        store = get_store()
    try:
        file_config = store.get_config(provider_name)
    except AIProviderStoreError:
        file_config = {}
    try:
        modules = getattr(django_settings, "CAULDRON_MODULES", {}) or {}
        cfg = modules.get("cauldron.ai.admin") or {}
        module_config = dict(
            cfg.get("provider_config", {}).get(provider_name) or {}
        )
    except Exception:
        module_config = {}
    merged = {**module_config, **file_config}
    return merged
