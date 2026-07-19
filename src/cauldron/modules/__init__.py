"""Public contracts for the Cauldron module system."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

_SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)*$")


def _validate_slug(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be non-empty.")
    if not _SLUG_RE.match(value):
        raise ValueError(
            f"{field_name} {value!r} must match pattern"
            " [a-z][a-z0-9]*(\\.[a-z][a-z0-9]*)* (lowercase dotted segments)."
        )


def _validate_specifier(value: str, field_name: str) -> None:
    if not value:
        return
    try:
        SpecifierSet(value)
    except InvalidSpecifier as exc:
        raise ValueError(f"{field_name} {value!r} is not a valid PEP 440 specifier: {exc}") from exc


def _validate_version(value: str, field_name: str) -> None:
    if not value:
        return
    try:
        Version(value)
    except InvalidVersion as exc:
        raise ValueError(f"{field_name} {value!r} is not a valid PEP 440 version: {exc}") from exc


@dataclass(frozen=True)
class ModuleRequirement:
    """Declares a dependency on another module or a named capability."""

    slug: str
    version: str = ""
    kind: Literal["module", "capability"] = "module"

    def __post_init__(self) -> None:
        _validate_slug(self.slug, "ModuleRequirement.slug")
        _validate_specifier(self.version, "ModuleRequirement.version")
        if self.kind not in ("module", "capability"):
            raise ValueError(
                f"ModuleRequirement.kind must be 'module' or 'capability', got {self.kind!r}."
            )

    def to_dict(self) -> dict[str, str]:
        return {"slug": self.slug, "version": self.version, "kind": self.kind}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleRequirement:
        return cls(
            slug=data["slug"],
            version=data.get("version", ""),
            kind=data.get("kind", "module"),  # type: ignore[arg-type]
        )


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

    def __post_init__(self) -> None:
        _validate_slug(self.slug, "ModuleManifest.slug")
        if not self.label:
            raise ValueError("ModuleManifest.label must be non-empty.")
        _validate_version(self.version, "ModuleManifest.version")
        _validate_specifier(self.cauldron_version, "ModuleManifest.cauldron_version")
        for app in self.django_apps:
            if not isinstance(app, str) or not app:
                raise ValueError(
                    f"ModuleManifest.django_apps entries must be non-empty strings; got {app!r}."
                )
        for cap in self.provides:
            _validate_slug(cap, "ModuleManifest.provides entry")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this manifest."""
        return {
            "slug": self.slug,
            "label": self.label,
            "version": self.version,
            "cauldron_version": self.cauldron_version,
            "django_apps": list(self.django_apps),
            "settings": dict(self.settings),
            "requires": [r.to_dict() for r in self.requires],
            "optional": [r.to_dict() for r in self.optional],
            "provides": list(self.provides),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleManifest:
        """Construct a ModuleManifest from a plain dict (e.g., loaded from JSON)."""
        return cls(
            slug=data["slug"],
            label=data["label"],
            version=data.get("version", "0.0.0"),
            cauldron_version=data.get("cauldron_version", ""),
            django_apps=tuple(data.get("django_apps", [])),
            settings=data.get("settings", {}),
            requires=tuple(
                ModuleRequirement.from_dict(r) for r in data.get("requires", [])
            ),
            optional=tuple(
                ModuleRequirement.from_dict(r) for r in data.get("optional", [])
            ),
            provides=tuple(data.get("provides", [])),
        )


@dataclass(frozen=True)
class ModuleContext:
    """Passed to a module's register() phase with its resolved identity and config."""

    slug: str
    config: dict[str, Any]


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

    def register(self, context: ModuleContext) -> None:
        """Called once before on_ready(). Override to perform early registration."""

    def on_ready(self) -> None:
        """Called after all modules are activated. Override to add startup logic."""
