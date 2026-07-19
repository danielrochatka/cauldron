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
    entry_point_group: str = ENTRY_POINT_GROUP,
) -> list[str]:
    """Return Django app labels for the given enabled module slugs.

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
    of slugs.  Modules are processed in alphabetical slug order so that
    ``INSTALLED_APPS`` is deterministic across environments.
    """
    if isinstance(enabled, dict):
        slugs: set[str] = set(enabled.keys())
    else:
        slugs = set(enabled)

    result = discover_modules(entry_point_group=entry_point_group)
    apps: list[str] = []
    for module in result.modules:  # already sorted by slug
        if module.slug in slugs:
            apps.extend(module.django_apps())
    return apps
