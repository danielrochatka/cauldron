"""Configuration and helpers for cauldron.django.state."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def sqlite_database(path: str | Path) -> dict[str, Any]:
    """Return a Django DATABASES entry for SQLite at the given path.

    Returns a new dict; caller input is not mutated.
    """
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(path),
    }


class DjangoStateConfig:
    """Immutable runtime configuration for cauldron.django.state."""

    def __init__(self, database_alias: str = "default") -> None:
        if not isinstance(database_alias, str) or not database_alias:
            raise ValueError("database_alias must be a non-empty string.")
        self._database_alias = database_alias

    @property
    def database_alias(self) -> str:
        return self._database_alias

    @classmethod
    def from_module_config(cls, config: dict[str, Any]) -> "DjangoStateConfig":
        return cls(database_alias=config.get("database_alias", "default"))
