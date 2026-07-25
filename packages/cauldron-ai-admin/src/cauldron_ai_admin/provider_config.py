"""Secure file-backed configuration and secret store for AI providers.

The store persists to a single JSON file (default: ``data/ai/config.json``).
Writes are atomic (write-to-temp → fsync → rename) and the file is created
with mode 0600 so credentials are not world-readable.

File structure::

    {
        "provider": "openai",
        "config": {
            "openai": {"model_name": "gpt-4o"},
        },
        "secrets": {
            "openai": {"api_key": "<token>"},
        }
    }

``secrets`` values are stored in plain text.  The file-system mode 0600
is the primary protection; do not commit this file to version control.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any

from django.conf import settings as django_settings


_DEFAULT_CONFIG_FILENAME = "ai/config.json"


def _default_config_path() -> Path:
    """Return the default config path relative to BASE_DIR/data/.

    Falls back to a path in the system temp directory when ``BASE_DIR`` is
    not configured (e.g. in test environments without a full Django settings
    module).
    """
    env_path = os.environ.get("CAULDRON_AI_CONFIG_PATH", "").strip()
    if env_path:
        return Path(env_path)
    base = getattr(django_settings, "BASE_DIR", None)
    if base is not None:
        return Path(base) / "data" / _DEFAULT_CONFIG_FILENAME
    import tempfile
    return Path(tempfile.gettempdir()) / "cauldron" / _DEFAULT_CONFIG_FILENAME


class AIProviderSettingsStore:
    """Thread-safe file-backed store for provider config and secrets.

    Instantiate once per process; the ``get_store()`` singleton below
    is the recommended entry point for application code.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path  # None → resolved lazily on first access
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = _default_config_path()
        return self._path

    # ------------------------------------------------------------------
    # Low-level read / write
    # ------------------------------------------------------------------

    def _read_raw(self) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8")
            data = json.loads(text)
            if not isinstance(data, dict):
                return {}
            return data
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_raw(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.parent / (self.path.name + ".tmp")
        try:
            fd = os.open(
                str(tmp_path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                stat.S_IRUSR | stat.S_IWUSR,  # 0600
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as fh:
                    json.dump(data, fh, indent=2, ensure_ascii=False)
                    fh.write("\n")
                    fh.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(str(tmp_path), str(self.path))
            # Ensure final file has 0600 even if inherited umask was wider.
            os.chmod(str(self.path), stat.S_IRUSR | stat.S_IWUSR)
        except BaseException:
            try:
                os.unlink(str(tmp_path))
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """Return a copy of the full config dict."""
        with self._lock:
            return dict(self._read_raw())

    def save(self, data: dict) -> None:
        """Overwrite the full config dict (atomic, 0600)."""
        with self._lock:
            self._write_raw(data)

    def get_selected_provider(self) -> str:
        """Return the stored provider slug, or '' if not set."""
        with self._lock:
            return str(self._read_raw().get("provider", "") or "")

    def set_selected_provider(self, name: str) -> None:
        with self._lock:
            data = self._read_raw()
            data["provider"] = name
            self._write_raw(data)

    def get_config(self, provider_name: str) -> dict[str, Any]:
        """Return the config dict for the given provider (empty dict if absent)."""
        with self._lock:
            raw = self._read_raw()
            return dict(raw.get("config", {}).get(provider_name) or {})

    def set_config(self, provider_name: str, config: dict[str, Any]) -> None:
        with self._lock:
            data = self._read_raw()
            data.setdefault("config", {})[provider_name] = dict(config)
            self._write_raw(data)

    def get_secret(self, provider_name: str, key: str) -> str:
        """Return a secret value, or '' if not stored."""
        with self._lock:
            raw = self._read_raw()
            return str(raw.get("secrets", {}).get(provider_name, {}).get(key, "") or "")

    def set_secret(self, provider_name: str, key: str, value: str) -> None:
        with self._lock:
            data = self._read_raw()
            data.setdefault("secrets", {}).setdefault(provider_name, {})[key] = value
            self._write_raw(data)

    def get_secrets(self, provider_name: str) -> dict[str, str]:
        """Return all secrets for a provider (empty dict if absent)."""
        with self._lock:
            raw = self._read_raw()
            return dict(raw.get("secrets", {}).get(provider_name) or {})

    def set_secrets(self, provider_name: str, secrets: dict[str, str]) -> None:
        with self._lock:
            data = self._read_raw()
            data.setdefault("secrets", {})[provider_name] = dict(secrets)
            self._write_raw(data)

    def file_exists(self) -> bool:
        return self.path.exists()

    def file_permissions_ok(self) -> bool:
        """Return True iff the file exists and is readable only by owner (0600)."""
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
            return mode == 0o600
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
    Pass ``path`` explicitly in tests to avoid touching the real filesystem.
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
    name = store.get_selected_provider()
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
    file_config = store.get_config(provider_name)
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
