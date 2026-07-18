"""Extension contracts for Cauldron Python modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence


class CauldronModule(Protocol):
    """Small contract implemented by optional Cauldron or site modules."""

    slug: str
    label: str

    def django_apps(self) -> Sequence[str]:
        """Return Django app labels required by this module."""


@dataclass(frozen=True)
class ModuleManifest:
    """Declarative module metadata used before full module loading exists."""

    slug: str
    label: str
    django_apps: tuple[str, ...] = field(default_factory=tuple)
    settings: Mapping[str, object] = field(default_factory=dict)
