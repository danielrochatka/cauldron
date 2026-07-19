"""Entry-point discovery for installed Cauldron modules."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from . import CauldronModule

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "cauldron.modules"


@dataclass
class DiscoveryError:
    """Structured error produced while loading module entry points."""

    entry_point_name: str
    kind: Literal["load_failure", "duplicate_slug", "manifest_validation"]
    message: str


@dataclass
class DiscoveryResult:
    """Outcome of a module discovery pass."""

    modules: list[CauldronModule]
    errors: list[DiscoveryError]


def discover_modules(*, entry_point_group: str = ENTRY_POINT_GROUP) -> DiscoveryResult:
    """Discover installed Cauldron modules via Python entry points.

    Returns a :class:`DiscoveryResult` containing successfully loaded modules
    and structured errors for any entry point that could not be loaded, failed
    manifest validation, or registered a duplicate slug.
    """
    from . import CauldronModule  # avoid circular import at module load time

    eps = entry_points(group=entry_point_group)
    modules: list[CauldronModule] = []
    errors: list[DiscoveryError] = []
    seen_slugs: dict[str, str] = {}  # slug -> first entry_point_name

    for ep in sorted(eps, key=lambda e: e.name):  # deterministic order
        try:
            obj = ep.load()
            if callable(obj) and not isinstance(obj, CauldronModule):
                obj = obj()
        except Exception as exc:
            errors.append(DiscoveryError(
                entry_point_name=ep.name,
                kind="load_failure",
                message=f"Entry point {ep.name!r} failed to load: {exc}",
            ))
            logger.debug("Entry point %r failed to load: %s", ep.name, exc)
            continue

        if not isinstance(obj, CauldronModule):
            errors.append(DiscoveryError(
                entry_point_name=ep.name,
                kind="load_failure",
                message=(
                    f"Entry point {ep.name!r} yielded {type(obj).__name__!r} which"
                    " does not satisfy the CauldronModule protocol."
                ),
            ))
            continue

        slug = obj.slug
        if slug in seen_slugs:
            errors.append(DiscoveryError(
                entry_point_name=ep.name,
                kind="duplicate_slug",
                message=(
                    f"Module slug {slug!r} registered by {ep.name!r} conflicts with"
                    f" {seen_slugs[slug]!r}; the duplicate is ignored."
                ),
            ))
            continue

        seen_slugs[slug] = ep.name
        modules.append(obj)
        logger.debug("Discovered module %r from entry point %r.", slug, ep.name)

    modules.sort(key=lambda m: m.slug)
    return DiscoveryResult(modules=modules, errors=errors)


def get_module_apps(
    enabled: dict[str, Any] | list[str],
    *,
    capability_overrides: dict[str, str] | None = None,
    entry_point_group: str = ENTRY_POINT_GROUP,
) -> list[str]:
    """Return Django app labels for the given enabled module slugs in dependency order.

    Call this from ``settings.py`` to compose ``INSTALLED_APPS`` before
    ``django.setup()`` runs::

        from cauldron.modules.discovery import get_module_apps

        CAULDRON_MODULES = {
            "cauldron.content": {},
            "cauldron.accounts": {"allow_signup": True},
        }

        INSTALLED_APPS = [
            "django.contrib.contenttypes",
            "cauldron",
            *get_module_apps(CAULDRON_MODULES),
        ]

    *enabled* may be a ``dict`` (keys are active slugs) or a plain ``list``
    of slugs.  Apps are returned in topological dependency order so that
    Django's ``AppConfig.ready()`` chain fires in the correct sequence.
    """
    from .resolver import resolve

    if isinstance(enabled, dict):
        slugs: set[str] = set(enabled.keys())
    else:
        slugs = set(enabled)

    result = discover_modules(entry_point_group=entry_point_group)
    active_modules = [m for m in result.modules if m.slug in slugs]

    # Build capability provider map for the active set only.
    cap_providers: dict[str, list[str]] = {}
    for m in active_modules:
        for cap in sorted(m.manifest.provides):
            cap_providers.setdefault(cap, []).append(m.slug)

    resolution = resolve(
        active_modules,
        cap_providers,
        cauldron_version="",  # version checks happen in registry; skip here
        capability_overrides=capability_overrides or {},
    )

    module_by_slug = {m.slug: m for m in active_modules}

    # Use the resolved load order; append any modules that fell out (errors)
    # in alphabetical order so INSTALLED_APPS stays deterministic.
    seen: set[str] = set(resolution.load_order)
    ordered: list[str] = list(resolution.load_order)
    for m in sorted(active_modules, key=lambda m: m.slug):
        if m.slug not in seen:
            ordered.append(m.slug)

    apps: list[str] = []
    for slug in ordered:
        if slug in module_by_slug:
            apps.extend(module_by_slug[slug].django_apps())
    return apps
