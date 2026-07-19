"""Public contracts for the Cauldron module system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class ModuleRequirement:
    """Declares a dependency on another module or a named capability."""

    slug: str
    version: str = ""
    kind: Literal["module", "capability"] = "module"


@dataclass(frozen=True)
class ModuleManifest:
    """Declarative module metadata for discovery, dependency resolution, and loading."""

    slug: str
    label: str
    version: str = "0.0.0"
    cauldron_version: str = ""
    django_apps: tuple[str, ...] = field(default_factory=tuple)
    settings: Mapping[str, object] = field(default_factory=dict)
    requires: tuple[ModuleRequirement, ...] = field(default_factory=tuple)
    optional: tuple[ModuleRequirement, ...] = field(default_factory=tuple)
    provides: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class CauldronModule(Protocol):
    """Protocol that entry-point objects must satisfy to be loaded as modules."""

    slug: str
    label: str
    manifest: ModuleManifest

    def django_apps(self) -> Sequence[str]: ...


class BaseModule:
    """Convenience base class for implementing CauldronModule."""

    def __init__(self, manifest: ModuleManifest) -> None:
        self.manifest = manifest

    @property
    def slug(self) -> str:
        return self.manifest.slug

    @property
    def label(self) -> str:
        return self.manifest.label

    def django_apps(self) -> Sequence[str]:
        return self.manifest.django_apps

    def on_ready(self) -> None:
        """Called after all modules are activated. Override to add startup logic."""
