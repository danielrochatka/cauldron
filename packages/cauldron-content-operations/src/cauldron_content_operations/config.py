"""Configuration for cauldron.content.operations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentOperationsConfig:
    require_approval: bool = True
    allow_self_approval: bool = False
    max_operations_per_change_set: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.require_approval, bool):
            raise TypeError("require_approval must be a bool")
        if not isinstance(self.allow_self_approval, bool):
            raise TypeError("allow_self_approval must be a bool")
        if not isinstance(self.max_operations_per_change_set, int) or self.max_operations_per_change_set < 1:
            raise TypeError("max_operations_per_change_set must be a positive integer")


def get_operations_config() -> ContentOperationsConfig:
    """Read CAULDRON_MODULES['cauldron.content.operations'] from Django settings."""
    from django.conf import settings
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules.get("cauldron.content.operations") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    return ContentOperationsConfig(
        require_approval=bool(cfg.get("require_approval", True)),
        allow_self_approval=bool(cfg.get("allow_self_approval", False)),
        max_operations_per_change_set=int(cfg.get("max_operations_per_change_set", 100)),
    )
