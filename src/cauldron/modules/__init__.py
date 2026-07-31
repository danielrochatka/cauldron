"""Public contracts for the Cauldron module system."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

_SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)*$")
_NAMESPACE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
# Navigation keys allow hyphens in segments after the first character,
# e.g. "cauldron.admin.content.page-create".
_NAV_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9-]*)*$")
# Tool names allow underscores in segments, e.g. "content.list_collections".
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
# Permission codenames: lowercase identifier with underscores.
_CODENAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Settings keys: simple lowercase identifier (no dots — top-level CAULDRON_MODULES key).
_SETTINGS_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# App labels follow Python identifier rules.
_APP_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


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


# ---------------------------------------------------------------------------
# Dependency declaration
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Operational metadata value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModuleSettingsDeclaration:
    """Declares a top-level configuration key under ``CAULDRON_MODULES[slug]``.

    The *key* is the name used inside the module's settings dict, e.g.
    ``"site_root"`` for ``CAULDRON_MODULES["cauldron.cms.flatfile"]["site_root"]``.

    This is ownership metadata: it describes what configuration the module reads
    and whether that configuration is required.  Defaults, validation logic, and
    forms are owned by the module itself.
    """

    key: str
    required: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("ModuleSettingsDeclaration.key must be non-empty.")
        if not _SETTINGS_KEY_RE.match(self.key):
            raise ValueError(
                f"ModuleSettingsDeclaration.key {self.key!r} must be a lowercase identifier "
                "(letters, digits, and underscores; must start with a letter)."
            )

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "required": self.required, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleSettingsDeclaration:
        return cls(
            key=data["key"],
            required=data.get("required", False),
            description=data.get("description", ""),
        )


@dataclass(frozen=True)
class ModuleMigrationDeclaration:
    """Declares that a Django app contributed by this module has database migrations.

    ``app_label`` must appear in ``ModuleManifest.django_apps``.  The declaration
    communicates to management tooling (module inventory, deploy previews) that
    enabling this module will require running ``migrate``.
    """

    app_label: str

    def __post_init__(self) -> None:
        if not self.app_label:
            raise ValueError("ModuleMigrationDeclaration.app_label must be non-empty.")
        if not _APP_LABEL_RE.match(self.app_label):
            raise ValueError(
                f"ModuleMigrationDeclaration.app_label {self.app_label!r} must be a valid "
                "Python identifier (letters, digits, underscores, dots)."
            )

    def to_dict(self) -> dict[str, str]:
        return {"app_label": self.app_label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleMigrationDeclaration:
        return cls(app_label=data["app_label"])


@dataclass(frozen=True)
class ModulePermissionDeclaration:
    """Declares a Django permission owned by this module.

    ``app_label`` must appear in ``ModuleManifest.django_apps``.
    ``codename`` must be a valid lowercase permission codename (no spaces).
    ``name`` is the human-readable description shown in admin UIs.

    This is ownership metadata that mirrors what the module's model
    ``Meta.permissions`` defines, allowing the inventory to list permissions
    without importing Django models.  Django AppConfig remains authoritative
    for actual permission creation and enforcement.
    """

    codename: str
    name: str
    app_label: str

    def __post_init__(self) -> None:
        if not self.codename:
            raise ValueError("ModulePermissionDeclaration.codename must be non-empty.")
        if not _CODENAME_RE.match(self.codename):
            raise ValueError(
                f"ModulePermissionDeclaration.codename {self.codename!r} must be a lowercase "
                "identifier with letters, digits, and underscores."
            )
        if not self.name:
            raise ValueError("ModulePermissionDeclaration.name must be non-empty.")
        if not self.app_label:
            raise ValueError("ModulePermissionDeclaration.app_label must be non-empty.")

    def to_dict(self) -> dict[str, str]:
        return {"codename": self.codename, "name": self.name, "app_label": self.app_label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModulePermissionDeclaration:
        return cls(
            codename=data["codename"],
            name=data["name"],
            app_label=data["app_label"],
        )


@dataclass(frozen=True)
class ModuleNavigationDeclaration:
    """Declares a navigation contribution by this module.

    Used for both navigation sections (``section=""``  means "this IS a
    section") and navigation items (``section`` is the key of the containing
    section).

    The ``key`` uniquely identifies the nav section or item across all modules.
    Segments may contain hyphens after the first character, e.g.
    ``"cauldron.admin.content.page-create"``.
    """

    key: str
    label: str
    section: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("ModuleNavigationDeclaration.key must be non-empty.")
        if not _NAV_KEY_RE.match(self.key):
            raise ValueError(
                f"ModuleNavigationDeclaration.key {self.key!r} must be a dotted lowercase "
                "identifier (segments may contain hyphens after the first character)."
            )
        if not self.label:
            raise ValueError("ModuleNavigationDeclaration.label must be non-empty.")

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label, "section": self.section}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleNavigationDeclaration:
        return cls(
            key=data["key"],
            label=data["label"],
            section=data.get("section", ""),
        )


@dataclass(frozen=True)
class ProvidedCapability:
    """Richer metadata for a capability listed in ``ModuleManifest.provides``.

    The ``slug`` must appear in the manifest's ``provides`` tuple.  Every
    provided capability must still be listed there; ``provided_capabilities``
    enriches those entries with an optional contract path and description.

    ``contract`` — dotted Python path to the protocol or abstract base class
    that defines the capability's interface (e.g.
    ``"cauldron_content.site.SitePublicUrlProvider"``).  This path typically
    lives in a *dependency* module, not in the provider itself.  It is
    informational: no runtime import is performed.

    ``description`` — one-line human-readable summary for module-management UIs.
    """

    slug: str
    contract: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        _validate_slug(self.slug, "ProvidedCapability.slug")
        if self.contract and not _NAMESPACE_RE.match(self.contract):
            raise ValueError(
                f"ProvidedCapability.contract {self.contract!r} must be a valid dotted "
                "Python identifier path if provided."
            )

    def to_dict(self) -> dict[str, str]:
        return {"slug": self.slug, "contract": self.contract, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProvidedCapability:
        return cls(
            slug=data["slug"],
            contract=data.get("contract", ""),
            description=data.get("description", ""),
        )


# ---------------------------------------------------------------------------
# Core manifest
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModuleManifest:
    """Declarative module metadata for discovery, dependency resolution, and loading.

    Fields are grouped by concern:

    Identity
        slug, label, version, cauldron_version

    Django integration (determines restart requirement)
        django_apps, django_middleware, django_context_processors

    Settings
        settings (defaults/overrides), settings_declarations (ownership)

    Dependency contract
        requires, optional, provides

    Namespace and public-API contract (used by the architecture checker)
        namespaces, public_api, capability_implementations

    Operational metadata (consumed by module inventory, #33 / #38 / #66)
        migration_apps, permissions, navigation,
        ai_tools, prompt_templates, provided_capabilities
    """

    # --- identity ---
    slug: str
    label: str
    version: str = "0.0.0"
    cauldron_version: str = ""

    # --- Django integration ---
    django_apps: tuple[str, ...] = field(default_factory=tuple)
    django_middleware: tuple[str, ...] = field(default_factory=tuple)
    django_context_processors: tuple[str, ...] = field(default_factory=tuple)

    # --- settings ---
    settings: Mapping[str, object] = field(default_factory=dict)
    settings_declarations: tuple[ModuleSettingsDeclaration, ...] = field(default_factory=tuple)

    # --- dependency contract ---
    requires: tuple[ModuleRequirement, ...] = field(default_factory=tuple)
    optional: tuple[ModuleRequirement, ...] = field(default_factory=tuple)
    provides: tuple[str, ...] = field(default_factory=tuple)

    # --- namespace / public-API contract ---
    namespaces: tuple[str, ...] = field(default_factory=tuple)
    public_api: tuple[str, ...] = field(default_factory=tuple)
    # Paths that are technically public but represent concrete implementations;
    # only the owning module's own files may import them. External consumers must
    # use the capability contract (e.g. get_public_url()) instead.
    capability_implementations: tuple[str, ...] = field(default_factory=tuple)

    # --- operational metadata ---
    migration_apps: tuple[ModuleMigrationDeclaration, ...] = field(default_factory=tuple)
    permissions: tuple[ModulePermissionDeclaration, ...] = field(default_factory=tuple)
    navigation: tuple[ModuleNavigationDeclaration, ...] = field(default_factory=tuple)
    ai_tools: tuple[str, ...] = field(default_factory=tuple)
    prompt_templates: tuple[str, ...] = field(default_factory=tuple)
    provided_capabilities: tuple[ProvidedCapability, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    # Computed property
    # ------------------------------------------------------------------

    @property
    def requires_restart(self) -> bool:
        """True if enabling or disabling this module requires a server restart.

        Derived from whether the module registers Django apps, middleware, or
        context processors — all of which take effect only at process startup.
        """
        return bool(self.django_apps or self.django_middleware or self.django_context_processors)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

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
        for mw in self.django_middleware:
            if not isinstance(mw, str) or not mw:
                raise ValueError(
                    f"ModuleManifest.django_middleware entries must be non-empty strings; got {mw!r}."
                )
        for cp in self.django_context_processors:
            if not isinstance(cp, str) or not cp:
                raise ValueError(
                    f"ModuleManifest.django_context_processors entries must be non-empty strings; got {cp!r}."
                )

        for cap in self.provides:
            _validate_slug(cap, "ModuleManifest.provides entry")
        for ns in self.namespaces:
            if not ns or not _NAMESPACE_RE.match(ns):
                raise ValueError(
                    f"ModuleManifest.namespaces entry {ns!r} must be a valid dotted Python identifier."
                )
        for path in self.public_api:
            if not path or not _NAMESPACE_RE.match(path):
                raise ValueError(
                    f"ModuleManifest.public_api entry {path!r} must be a valid dotted Python import path."
                )
        for path in self.capability_implementations:
            if not path or not _NAMESPACE_RE.match(path):
                raise ValueError(
                    f"ModuleManifest.capability_implementations entry {path!r} must be a valid dotted Python import path."
                )
            if path not in self.public_api:
                raise ValueError(
                    f"ModuleManifest.capability_implementations entry {path!r} must also appear in public_api."
                )

        # settings_declarations: unique keys
        _seen: set[str] = set()
        for decl in self.settings_declarations:
            if decl.key in _seen:
                raise ValueError(
                    f"ModuleManifest.settings_declarations has duplicate key {decl.key!r}."
                )
            _seen.add(decl.key)

        # migration_apps: unique app_labels; must be in django_apps
        _seen = set()
        for m in self.migration_apps:
            if m.app_label in _seen:
                raise ValueError(
                    f"ModuleManifest.migration_apps has duplicate app_label {m.app_label!r}."
                )
            _seen.add(m.app_label)
            if m.app_label not in self.django_apps:
                raise ValueError(
                    f"ModuleManifest.migration_apps app_label {m.app_label!r} must appear in django_apps."
                )

        # permissions: unique codenames; app_label must be in django_apps
        _seen = set()
        for p in self.permissions:
            if p.codename in _seen:
                raise ValueError(
                    f"ModuleManifest.permissions has duplicate codename {p.codename!r}."
                )
            _seen.add(p.codename)
            if p.app_label not in self.django_apps:
                raise ValueError(
                    f"ModuleManifest.permissions app_label {p.app_label!r} for codename "
                    f"{p.codename!r} must appear in django_apps."
                )

        # navigation: unique keys
        _seen = set()
        for n in self.navigation:
            if n.key in _seen:
                raise ValueError(
                    f"ModuleManifest.navigation has duplicate key {n.key!r}."
                )
            _seen.add(n.key)

        # ai_tools: valid tool names; unique
        _seen = set()
        for tool in self.ai_tools:
            if not tool or not _TOOL_NAME_RE.match(tool):
                raise ValueError(
                    f"ModuleManifest.ai_tools entry {tool!r} must be a dotted lowercase "
                    "identifier (segments may contain underscores)."
                )
            if tool in _seen:
                raise ValueError(
                    f"ModuleManifest.ai_tools has duplicate entry {tool!r}."
                )
            _seen.add(tool)

        # prompt_templates: valid tool names; unique
        _seen = set()
        for tmpl in self.prompt_templates:
            if not tmpl or not _TOOL_NAME_RE.match(tmpl):
                raise ValueError(
                    f"ModuleManifest.prompt_templates entry {tmpl!r} must be a dotted lowercase "
                    "identifier (segments may contain underscores)."
                )
            if tmpl in _seen:
                raise ValueError(
                    f"ModuleManifest.prompt_templates has duplicate entry {tmpl!r}."
                )
            _seen.add(tmpl)

        # provided_capabilities: slug must be in provides; unique slugs
        provides_set = set(self.provides)
        _seen = set()
        for cap in self.provided_capabilities:
            if cap.slug not in provides_set:
                raise ValueError(
                    f"ModuleManifest.provided_capabilities slug {cap.slug!r} must appear in provides."
                )
            if cap.slug in _seen:
                raise ValueError(
                    f"ModuleManifest.provided_capabilities has duplicate slug {cap.slug!r}."
                )
            _seen.add(cap.slug)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this manifest."""
        return {
            "slug": self.slug,
            "label": self.label,
            "version": self.version,
            "cauldron_version": self.cauldron_version,
            "django_apps": list(self.django_apps),
            "django_middleware": list(self.django_middleware),
            "django_context_processors": list(self.django_context_processors),
            "settings": dict(self.settings),
            "settings_declarations": [d.to_dict() for d in self.settings_declarations],
            "requires": [r.to_dict() for r in self.requires],
            "optional": [r.to_dict() for r in self.optional],
            "provides": list(self.provides),
            "namespaces": list(self.namespaces),
            "public_api": list(self.public_api),
            "capability_implementations": list(self.capability_implementations),
            "migration_apps": [m.to_dict() for m in self.migration_apps],
            "permissions": [p.to_dict() for p in self.permissions],
            "navigation": [n.to_dict() for n in self.navigation],
            "ai_tools": list(self.ai_tools),
            "prompt_templates": list(self.prompt_templates),
            "provided_capabilities": [c.to_dict() for c in self.provided_capabilities],
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
            django_middleware=tuple(data.get("django_middleware", [])),
            django_context_processors=tuple(data.get("django_context_processors", [])),
            settings=data.get("settings", {}),
            settings_declarations=tuple(
                ModuleSettingsDeclaration.from_dict(d)
                for d in data.get("settings_declarations", [])
            ),
            requires=tuple(
                ModuleRequirement.from_dict(r) for r in data.get("requires", [])
            ),
            optional=tuple(
                ModuleRequirement.from_dict(r) for r in data.get("optional", [])
            ),
            provides=tuple(data.get("provides", [])),
            namespaces=tuple(data.get("namespaces", [])),
            public_api=tuple(data.get("public_api", [])),
            capability_implementations=tuple(data.get("capability_implementations", [])),
            migration_apps=tuple(
                ModuleMigrationDeclaration.from_dict(m)
                for m in data.get("migration_apps", [])
            ),
            permissions=tuple(
                ModulePermissionDeclaration.from_dict(p)
                for p in data.get("permissions", [])
            ),
            navigation=tuple(
                ModuleNavigationDeclaration.from_dict(n)
                for n in data.get("navigation", [])
            ),
            ai_tools=tuple(data.get("ai_tools", [])),
            prompt_templates=tuple(data.get("prompt_templates", [])),
            provided_capabilities=tuple(
                ProvidedCapability.from_dict(c)
                for c in data.get("provided_capabilities", [])
            ),
        )


# ---------------------------------------------------------------------------
# Runtime types
# ---------------------------------------------------------------------------

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
