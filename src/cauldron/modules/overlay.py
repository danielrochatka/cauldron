"""Startup-readable module-state overlay.

Provides :func:`load_overlay` and :func:`save_overlay` for the atomic JSON
file that persists enable/disable overrides across server restarts.

The file format is intentionally simple so it can be read during Django
settings composition, *before* any Django app or database is available.

Format::

    {
        "version": 1,
        "overrides": {
            "cauldron.some.module": {"enabled": false},
            "cauldron.other.module": {"enabled": true}
        }
    }

``load_overlay`` returns a dict ``{slug: {"enabled": bool}}`` and is
safe to call even when the file is absent or malformed (returns ``{}``
with an optional warning string).

``save_overlay`` writes atomically using a sibling temp file + rename.
``apply_overlay`` merges overrides into an existing ``CAULDRON_MODULES``
dict, enabling or disabling modules by adding/removing their slug key
while preserving the module's configuration dictionary.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_SUPPORTED_VERSION = 1
_OVERLAY_FILENAME = "module_state.json"


def _overlay_path(data_dir: str | os.PathLike[str]) -> Path:
    return Path(data_dir) / _OVERLAY_FILENAME


def load_overlay(
    data_dir: str | os.PathLike[str],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Load the overlay from *data_dir*.

    Returns ``(overrides, warning)`` where *overrides* is
    ``{slug: {"enabled": bool}}`` and *warning* is a human-readable
    problem description (or ``None`` when the file loaded cleanly).

    Never raises — malformed or missing files return empty overrides with
    a warning string so startup always succeeds.
    """
    path = _overlay_path(data_dir)
    if not path.exists():
        return {}, None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        return {}, f"module_state.json could not be read: {exc}"

    if not isinstance(data, dict):
        return {}, "module_state.json: root must be a JSON object"

    version = data.get("version")
    if version != _SUPPORTED_VERSION:
        return {}, f"module_state.json: unsupported version {version!r} (expected {_SUPPORTED_VERSION})"

    overrides = data.get("overrides", {})
    if not isinstance(overrides, dict):
        return {}, "module_state.json: 'overrides' must be an object"

    validated: dict[str, dict[str, Any]] = {}
    warnings = []
    for slug, entry in overrides.items():
        if not isinstance(slug, str) or not slug:
            warnings.append(f"skipping non-string slug {slug!r}")
            continue
        if not isinstance(entry, dict):
            warnings.append(f"skipping malformed entry for {slug!r}")
            continue
        if "enabled" not in entry or not isinstance(entry["enabled"], bool):
            warnings.append(f"skipping entry for {slug!r}: 'enabled' must be bool")
            continue
        validated[slug] = {"enabled": bool(entry["enabled"])}

    warning = "; ".join(warnings) if warnings else None
    return validated, warning


def save_overlay(
    data_dir: str | os.PathLike[str],
    overrides: dict[str, dict[str, Any]],
) -> None:
    """Atomically write *overrides* to the overlay file in *data_dir*.

    *overrides* must be ``{slug: {"enabled": bool}}``.
    Raises :class:`ValueError` for invalid input.
    Raises :class:`OSError` if the write fails.
    """
    for slug, entry in overrides.items():
        if not isinstance(slug, str) or not slug:
            raise ValueError(f"Invalid slug: {slug!r}")
        if not isinstance(entry.get("enabled"), bool):
            raise ValueError(f"Entry for {slug!r} must have 'enabled': bool")

    payload = json.dumps(
        {"version": _SUPPORTED_VERSION, "overrides": overrides},
        indent=2,
        sort_keys=True,
    )
    path = _overlay_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".module_state_", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def apply_overlay(
    module_settings: dict[str, Any],
    overrides: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return a new module_settings dict with *overrides* applied.

    - ``{"enabled": False}`` removes a slug from the returned dict
      (disabling it from compose_django_settings's perspective).
    - ``{"enabled": True}`` ensures a slug is present (adding ``{}``
      config if the slug was not already listed).

    The original *module_settings* is never mutated.
    """
    result = dict(module_settings)
    for slug, entry in overrides.items():
        if entry["enabled"]:
            if slug not in result:
                result[slug] = {}
        else:
            result.pop(slug, None)
    return result
