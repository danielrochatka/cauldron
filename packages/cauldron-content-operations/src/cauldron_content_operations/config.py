"""Configuration for cauldron.content.operations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContentOperationsConfig:
    require_approval: bool = True
    allow_self_approval: bool = False
    max_operations_per_change_set: int = 100
    lock_timeout: int = 30

    def __post_init__(self) -> None:
        if not isinstance(self.require_approval, bool):
            raise TypeError("require_approval must be a bool")
        if not isinstance(self.allow_self_approval, bool):
            raise TypeError("allow_self_approval must be a bool")
        if isinstance(self.max_operations_per_change_set, bool) or not isinstance(
            self.max_operations_per_change_set, int
        ) or self.max_operations_per_change_set < 1:
            raise TypeError("max_operations_per_change_set must be a positive integer")
        if isinstance(self.lock_timeout, bool) or not isinstance(
            self.lock_timeout, int
        ) or self.lock_timeout < 1:
            raise TypeError("lock_timeout must be a positive integer")


def _strict_bool(value: Any, name: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise TypeError(
        f"{name} must be a boolean true/false, got {type(value).__name__}: {value!r}"
    )


def _strict_positive_int(value: Any, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer, not a boolean")
    if isinstance(value, int) and value >= 1:
        return value
    raise TypeError(f"{name} must be a positive integer, got {value!r}")


def get_operations_config() -> ContentOperationsConfig:
    """Read ``CAULDRON_MODULES['cauldron.content.operations']`` from settings.

    Rejects loosely-typed values (e.g. ``"false"`` for a bool) so that
    configuration mistakes surface loudly.
    """
    from django.conf import settings

    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules.get("cauldron.content.operations") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    return ContentOperationsConfig(
        require_approval=_strict_bool(
            cfg.get("require_approval"), "require_approval", True
        ),
        allow_self_approval=_strict_bool(
            cfg.get("allow_self_approval"), "allow_self_approval", False
        ),
        max_operations_per_change_set=_strict_positive_int(
            cfg.get("max_operations_per_change_set"),
            "max_operations_per_change_set",
            100,
        ),
        lock_timeout=_strict_positive_int(
            cfg.get("lock_timeout"), "lock_timeout", 30
        ),
    )
