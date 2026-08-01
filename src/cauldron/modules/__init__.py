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
# Navigation item permissions: "app_label.codename" form.
_NAV_PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
# Settings keys: simple lowercase identifier (no dots).
_SETTINGS_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# App labels follow Django convention: last segment of the dotted app path.
_APP_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Top-level Django setting names: UPPER_SNAKE_CASE.
_DJANGO_SETTING_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


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


def _app_label_in_django_apps(app_label: str, django_apps: tuple[str, ...]) -> bool:
    """Return True if *app_label* corresponds to any entry in *django_apps*.

    Django's AppConfig.label defaults to the last segment of the dotted app
    path (e.g. ``"auth"`` for ``"django.contrib.auth"``).  An exact match of
    the whole path (e.g. ``"cauldron_ai_admin"``) also qualifies.
    """
    for app in django_apps:
        if app == app_label:
            return True
        # Last dotted segment — Django's default label derivation
        if "." in app and app.rsplit(".", 1)[-1] == app_label:
            return True
    return False


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
    """Declares a configuration key read by this module.

    By default the key lives under ``CAULDRON_MODULES[slug][key]``.  Set
    ``setting_path`` to the name of a top-level Django setting when the module
    reads from the global settings namespace instead
    (e.g. ``setting_path="CAULDRON_UI_OVERRIDES_DIR"``).
    """

    key: str
    required: bool = False
    description: str = ""
    # If non-empty: the name of a top-level Django setting (UPPER_SNAKE_CASE).
    # If empty: the setting lives at CAULDRON_MODULES[slug][key].
    setting_path: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("ModuleSettingsDeclaration.key must be non-empty.")
        if not _SETTINGS_KEY_RE.match(self.key):
            raise ValueError(
                f"ModuleSettingsDeclaration.key {self.key!r} must be a lowercase identifier "
                "(letters, digits, and underscores; must start with a letter)."
            )
        if self.setting_path and not _DJANGO_SETTING_RE.match(self.setting_path):
            raise ValueError(
                f"ModuleSettingsDeclaration.setting_path {self.setting_path!r} must be a "
                "top-level Django setting name (UPPER_SNAKE_CASE)."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "required": self.required,
            "description": self.description,
            "setting_path": self.setting_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleSettingsDeclaration:
        return cls(
            key=data["key"],
            required=data.get("required", False),
            description=data.get("description", ""),
            setting_path=data.get("setting_path", ""),
        )


@dataclass(frozen=True)
class ModuleMigrationDeclaration:
    """Declares that a Django app contributed (or installed) by this module has
    database migrations.

    ``app_label`` is the Django app label (the ``AppConfig.label`` attribute),
    which is the last segment of the dotted app path for Django's built-in apps
    (e.g. ``"auth"`` for ``"django.contrib.auth"``) or the full Python package
    name for first-party apps (e.g. ``"cauldron_site_astro"``).

    The manifest validator checks that ``app_label`` corresponds to an entry in
    ``django_apps`` either by exact match or by matching the last dotted segment.
    """

    app_label: str

    def __post_init__(self) -> None:
        if not self.app_label:
            raise ValueError("ModuleMigrationDeclaration.app_label must be non-empty.")
        if not _APP_LABEL_RE.match(self.app_label):
            raise ValueError(
                f"ModuleMigrationDeclaration.app_label {self.app_label!r} must be a valid "
                "Django app label (letters, digits, and underscores)."
            )

    def to_dict(self) -> dict[str, str]:
        return {"app_label": self.app_label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleMigrationDeclaration:
        return cls(app_label=data["app_label"])


@dataclass(frozen=True)
class ModulePermissionDeclaration:
    """Declares a Django permission owned by this module.

    ``codename`` must be a valid lowercase permission codename (no spaces).
    ``name`` is the human-readable description shown in admin UIs.
    ``app_label`` is the Django app label that owns the permission — validated
    against ``django_apps`` using the same label-derivation rules as
    ``ModuleMigrationDeclaration``.

    This mirrors the module's model ``Meta.permissions`` as static metadata,
    allowing the inventory and install flows to enumerate permissions without
    importing Django models.  Django AppConfig remains authoritative for actual
    permission creation and enforcement.
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
        if not _APP_LABEL_RE.match(self.app_label):
            raise ValueError(
                f"ModulePermissionDeclaration.app_label {self.app_label!r} must be a valid "
                "Django app label (letters, digits, and underscores)."
            )

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

    Covers both navigation **sections** (``section=""`` means "this IS a
    section") and navigation **items** (``section`` is the key of the
    containing section).

    The ``key`` uniquely identifies the section or item across all modules.
    Segments may contain hyphens after the first character, e.g.
    ``"cauldron.admin.content.page-create"``.

    Fields that map directly onto the runtime ``AdminNavigationSection`` /
    ``AdminNavigationItem`` registration — keeping the manifest and the
    ``AppConfig.ready()`` registration in sync:

    - ``url_name``: Django URL name (items only; ``namespace:name`` form).
    - ``order``: display order within the section (lower = higher up).
    - ``permission``: ``"app_label.codename"`` required to see this item,
      or ``""`` for items visible to all authenticated users.
    - ``url_prefix``: URL prefix used for active-state highlighting.
    - ``url_prefix_exact``: if True, the item is only active on the exact URL.
    - ``description``: one-line tooltip / accessible description.
    """

    key: str
    label: str
    section: str = ""
    url_name: str = ""
    order: int = 0
    permission: str = ""
    url_prefix: str = ""
    url_prefix_exact: bool = False
    description: str = ""

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
        if self.permission and not _NAV_PERMISSION_RE.match(self.permission):
            raise ValueError(
                f"ModuleNavigationDeclaration.permission {self.permission!r} must be "
                "in 'app_label.codename' form or empty."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "section": self.section,
            "url_name": self.url_name,
            "order": self.order,
            "permission": self.permission,
            "url_prefix": self.url_prefix,
            "url_prefix_exact": self.url_prefix_exact,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleNavigationDeclaration:
        return cls(
            key=data["key"],
            label=data["label"],
            section=data.get("section", ""),
            url_name=data.get("url_name", ""),
            order=data.get("order", 0),
            permission=data.get("permission", ""),
            url_prefix=data.get("url_prefix", ""),
            url_prefix_exact=data.get("url_prefix_exact", False),
            description=data.get("description", ""),
        )


@dataclass(frozen=True)
class RuntimeRequirement:
    """Declares an external runtime dependency this module needs at startup.

    ``kind`` identifies the resource category: ``"database"``, ``"cache"``,
    ``"worker"``, ``"storage"``, or a project-specific string.

    ``alias`` refines the requirement when multiple resources of the same kind
    exist (e.g. the name of a non-default database alias or cache backend).

    ``description`` is a human-readable note for operations teams about what
    the module uses this resource for.
    """

    kind: str
    alias: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("RuntimeRequirement.kind must be non-empty.")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "alias": self.alias, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeRequirement:
        return cls(
            kind=data["kind"],
            alias=data.get("alias", ""),
            description=data.get("description", ""),
        )


@dataclass(frozen=True)
class ProvidedCapability:
    """Richer metadata for a capability listed in ``ModuleManifest.provides``.

    The ``slug`` must appear in the manifest's ``provides`` tuple.

    ``contract`` — dotted Python path to the protocol or abstract base class
    that defines the capability's interface (e.g.
    ``"cauldron_content.site.SitePublicUrlProvider"``).  This path is typically
    in a *dependency* module's ``public_api``, not in the implementing module.
    When the contract class lives in the implementing module's own namespaces,
    the manifest validator checks that the path falls under ``public_api``.

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

    Django integration (contributes to restart requirement)
        django_apps, django_middleware, django_context_processors

    Settings
        settings (defaults/overrides), settings_declarations (ownership)

    Dependency contract
        requires, optional, provides

    Namespace and public-API contract (used by the architecture checker)
        namespaces, public_api, capability_implementations

    Runtime and activation
        restart_required, runtime_requirements

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
    # only the owning module's own files may import them.
    capability_implementations: tuple[str, ...] = field(default_factory=tuple)

    # --- runtime and activation ---
    # Explicit override: set True when the module requires restart for reasons
    # beyond django_apps / django_middleware / django_context_processors
    # (e.g. it registers signal handlers at import time or starts background
    # threads in AppConfig.ready()).
    restart_required: bool = False
    runtime_requirements: tuple[RuntimeRequirement, ...] = field(default_factory=tuple)

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

        True when the module registers Django apps, middleware, or context
        processors (which take effect only at process startup), or when
        ``restart_required`` is explicitly set to signal other startup-time
        side effects (signal handlers, background threads, etc.).
        """
        return bool(
            self.django_apps
            or self.django_middleware
            or self.django_context_processors
            or self.restart_required
        )

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

        # migration_apps: unique app_labels; must correspond to an entry in django_apps
        # (exact match or last-segment match for Django's built-in apps).
        _seen = set()
        for m in self.migration_apps:
            if m.app_label in _seen:
                raise ValueError(
                    f"ModuleManifest.migration_apps has duplicate app_label {m.app_label!r}."
                )
            _seen.add(m.app_label)
            if not _app_label_in_django_apps(m.app_label, self.django_apps):
                raise ValueError(
                    f"ModuleManifest.migration_apps app_label {m.app_label!r} does not "
                    "correspond to any entry in django_apps (checked by exact match and "
                    "by last dotted segment)."
                )

        # permissions: unique (app_label, codename) pairs; same codename in two
        # different declared apps is fine (each app owns its own permission table).
        _seen_perms: set[tuple[str, str]] = set()
        for p in self.permissions:
            key = (p.app_label, p.codename)
            if key in _seen_perms:
                raise ValueError(
                    f"ModuleManifest.permissions has duplicate (app_label, codename) "
                    f"({p.app_label!r}, {p.codename!r})."
                )
            _seen_perms.add(key)
            if not _app_label_in_django_apps(p.app_label, self.django_apps):
                raise ValueError(
                    f"ModuleManifest.permissions app_label {p.app_label!r} for codename "
                    f"{p.codename!r} does not correspond to any entry in django_apps."
                )

        # navigation: unique keys + structural validation.
        # Items (non-empty section) must declare a url_name.
        # Sections (empty section) must not carry item-only routing fields.
        _seen = set()
        for n in self.navigation:
            if n.key in _seen:
                raise ValueError(
                    f"ModuleManifest.navigation has duplicate key {n.key!r}."
                )
            _seen.add(n.key)
            is_item = bool(n.section)
            if is_item and not n.url_name:
                raise ValueError(
                    f"ModuleManifest.navigation item {n.key!r} has a non-empty section "
                    "but no url_name. Navigation items must declare a url_name."
                )
            if not is_item and (n.url_name or n.permission or n.url_prefix or n.url_prefix_exact):
                raise ValueError(
                    f"ModuleManifest.navigation section {n.key!r} must not set "
                    "url_name, permission, url_prefix, or url_prefix_exact "
                    "(those are item-only fields)."
                )

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

        # provided_capabilities: slug must be in provides; unique slugs;
        # if contract refers to a namespace owned by this module it must be under public_api.
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
            if cap.contract:
                # Boundary-aware ownership: the contract belongs to a namespace
                # when it equals the namespace or is a direct sub-path of it.
                # This prevents "myapp_extra.X" from being treated as owned by
                # namespace "myapp", and correctly identifies ownership for
                # dotted namespaces like "myapp.core".
                owned = any(
                    cap.contract == ns or cap.contract.startswith(ns + ".")
                    for ns in self.namespaces
                )
                if owned:
                    # Contract is in this module's own code — must be reachable via public_api.
                    under_public = any(
                        cap.contract == api or cap.contract.startswith(api + ".")
                        for api in self.public_api
                    )
                    if not under_public:
                        raise ValueError(
                            f"ProvidedCapability.contract {cap.contract!r} is in a namespace "
                            "owned by this module but is not under public_api."
                        )

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
            "restart_required": self.restart_required,
            "runtime_requirements": [r.to_dict() for r in self.runtime_requirements],
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
            restart_required=data.get("restart_required", False),
            runtime_requirements=tuple(
                RuntimeRequirement.from_dict(r)
                for r in data.get("runtime_requirements", [])
            ),
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
