"""Module settings specification and registration for Cauldron Admin Shell.

Each user-facing Cauldron module may declare exactly one settings page via
:class:`ModuleSettingsSpec`. Registration projects a ``kind="settings"``
navigation item into the existing navigation registry, placing it last in the
module's sidebar section with the muted-red settings visual treatment.

Ownership boundary
------------------
The *module itself* owns:

* its URL and view
* permission definition and enforcement
* form, validation, and persistence
* health checks and provider connections

The *Cauldron admin shell* owns:

* discovery / registration
* sidebar placement and ordering (always last within the module section)
* settings visual styling (``cui-sidebar__link--settings``)
* active-state detection
* permission-aware filtering

Example registration (call from ``AppConfig.ready()``)::

    from cauldron_django_admin.module_settings import (
        ModuleSettingsSpec,
        register_module_settings,
    )

    register_module_settings(
        ModuleSettingsSpec(
            module_slug="example.module",
            url_name="example_module:settings",
            navigation_section="example",
            permission="example_module.manage_settings",
            description="Configure the example module",
        )
    )

The module registers its section and normal navigation items in
``AppConfig.ready()`` *before* calling ``register_module_settings`` so that
the section already exists when the spec is validated.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

_SLUG_RE = re.compile(r"^[a-zA-Z0-9._\-]{1,128}$")
_PERM_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")

# ``order`` value used when projecting into the navigation registry.
# The kind="settings" sort key ensures settings items are ALWAYS last within
# their section regardless of what numeric order normal items use, but an
# explicit high value avoids any future ambiguity.
_SETTINGS_ORDER = 9999


@dataclass(frozen=True)
class ModuleSettingsSpec:
    """Declarative metadata for a module's settings page.

    Attributes:
        module_slug:        Cauldron module identifier, e.g. ``"cauldron.ai.admin"``.
        url_name:           Namespaced Django URL name, e.g. ``"cauldron_ai_admin:settings"``.
        navigation_section: Key of an already-registered navigation section.
        permission:         ``"app_label.codename"`` required to view the settings page.
        label:              Sidebar label.  Defaults to ``"Settings"``.
        description:        Optional one-line description surfaced in navigation metadata.
    """

    module_slug: str
    url_name: str
    navigation_section: str
    permission: str
    label: str = "Settings"
    description: str = ""

    @property
    def key(self) -> str:
        """Derived navigation item key: ``"<module_slug>.settings"``."""
        return f"{self.module_slug}.settings"


class ModuleSettingsRegistry:
    """Thread-safe registry for :class:`ModuleSettingsSpec` objects.

    Calling :meth:`register` also projects each spec into the navigation
    registry as a ``kind="settings"`` item so the sidebar renders it
    automatically with the correct visual treatment and sort position.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._specs: dict[str, ModuleSettingsSpec] = {}

    def register(self, spec: ModuleSettingsSpec) -> None:
        """Register a settings spec.

        Exact re-registration is idempotent.  A second registration for the
        same ``module_slug`` with *different* attributes raises :class:`ValueError`.

        Raises:
            ValueError: invalid slug, url_name, permission, missing section,
                        or conflicting duplicate.
        """
        _validate_spec(spec)

        with self._lock:
            existing = self._specs.get(spec.module_slug)
            if existing is not None:
                if existing == spec:
                    return  # idempotent exact re-registration
                raise ValueError(
                    f"Module settings for {spec.module_slug!r} are already registered "
                    "with different attributes."
                )
            self._specs[spec.module_slug] = spec

        try:
            _project_to_navigation(spec)
        except Exception:
            # Roll back spec so the caller can fix and retry.
            with self._lock:
                self._specs.pop(spec.module_slug, None)
            raise

    def get_specs(self) -> list[ModuleSettingsSpec]:
        """Return all registered specs in registration order."""
        with self._lock:
            return list(self._specs.values())

    def clear(self) -> None:
        """Remove all specs.  For use in tests only."""
        with self._lock:
            self._specs.clear()


def _validate_spec(spec: ModuleSettingsSpec) -> None:
    if not _SLUG_RE.match(spec.module_slug):
        raise ValueError(
            f"module_slug {spec.module_slug!r} must match [a-zA-Z0-9._-]{{1,128}}."
        )
    if not spec.url_name or len(spec.url_name) > 256:
        raise ValueError("url_name must be 1–256 characters.")
    if not spec.navigation_section:
        raise ValueError("navigation_section must not be empty.")
    if not _PERM_RE.match(spec.permission):
        raise ValueError(
            f"permission {spec.permission!r} must be 'app_label.codename'."
        )
    if not spec.label or len(spec.label) > 256:
        raise ValueError("label must be 1–256 characters.")


def _project_to_navigation(spec: ModuleSettingsSpec) -> None:
    """Project a validated spec into the navigation registry as a settings item."""
    from cauldron_django_admin.navigation import (
        get_navigation_registry,
        AdminNavigationItem,
    )

    nav = get_navigation_registry()

    registered_sections = {s.key for s in nav.get_sections()}
    if spec.navigation_section not in registered_sections:
        raise ValueError(
            f"Navigation section {spec.navigation_section!r} is not registered. "
            "Register the section before calling register_module_settings()."
        )

    nav.register_item(AdminNavigationItem(
        key=spec.key,
        label=spec.label,
        url_name=spec.url_name,
        section=spec.navigation_section,
        order=_SETTINGS_ORDER,
        permission=spec.permission,
        description=spec.description,
        kind="settings",
        show_on_dashboard=False,
        owning_module=spec.module_slug,
    ))


_registry = ModuleSettingsRegistry()


def get_module_settings_registry() -> ModuleSettingsRegistry:
    """Return the process-wide module settings registry singleton."""
    return _registry


def register_module_settings(spec: ModuleSettingsSpec) -> None:
    """Register a module settings spec.

    Convenience wrapper around :meth:`ModuleSettingsRegistry.register` on the
    process-wide singleton.  See :class:`ModuleSettingsSpec` for field docs.
    """
    _registry.register(spec)
