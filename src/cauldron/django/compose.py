"""Shared settings-composition logic used by all Cauldron Django modules."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class SettingsPlan:
    """Immutable result of compose_django_settings().

    All sequences are tuples to ensure immutability.
    """

    installed_apps: tuple[str, ...]
    middleware: tuple[str, ...]
    context_processors: tuple[str, ...]
    enabled_modules: tuple[str, ...]       # slugs that were enabled
    module_order: tuple[str, ...]          # slugs in resolved load order
    capability_providers: dict[str, str]   # from CAULDRON_CAPABILITY_PROVIDERS

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "installed_apps": list(self.installed_apps),
            "middleware": list(self.middleware),
            "context_processors": list(self.context_processors),
            "enabled_modules": list(self.enabled_modules),
            "module_order": list(self.module_order),
            "capability_providers": dict(self.capability_providers),
        }


def _deduplicate(items: list[str]) -> list[str]:
    """Remove duplicates while preserving first occurrence."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def compose_django_settings(
    *,
    installed_apps: Sequence[str] = (),
    middleware: Sequence[str] = (),
    context_processors: Sequence[str] = (),
    module_settings: dict[str, Any] | None = None,
    capability_providers: dict[str, str] | None = None,
) -> SettingsPlan:
    """Compose Django settings from installed Cauldron modules.

    Discovers installed modules via entry points, filters to the enabled set
    (keys of *module_settings*, or all discovered modules if None), resolves
    load order respecting dependency constraints, then collects
    ``django_apps``, ``django_middleware``, and ``django_context_processors``
    contributions from each module in load order.

    Base *installed_apps*, *middleware*, and *context_processors* are prepended
    to module contributions.  Duplicates are removed, preserving first
    occurrence.  Caller inputs are never mutated.

    Returns an immutable :class:`SettingsPlan`.
    """
    from cauldron.modules.discovery import discover_modules
    from cauldron.modules.resolver import resolve

    # Defensive copies so caller inputs are never mutated.
    base_apps: list[str] = list(installed_apps)
    base_middleware: list[str] = list(middleware)
    base_cp: list[str] = list(context_processors)
    cap_overrides: dict[str, str] = dict(capability_providers or {})

    result = discover_modules()
    all_modules = result.modules

    if module_settings is None:
        # Enable all discovered modules.
        enabled_slugs: set[str] = {m.slug for m in all_modules}
    else:
        enabled_slugs = set(module_settings.keys())

    active_modules = [m for m in all_modules if m.slug in enabled_slugs]

    # Build capability provider map for active modules.
    cap_map: dict[str, list[str]] = {}
    for m in active_modules:
        for cap in sorted(m.manifest.provides):
            cap_map.setdefault(cap, []).append(m.slug)

    resolution = resolve(
        active_modules,
        cap_map,
        cauldron_version="",
        capability_overrides=cap_overrides,
    )

    module_by_slug = {m.slug: m for m in active_modules}
    load_order: list[str] = list(resolution.load_order)

    # Append any modules that fell out of load_order (errors) deterministically.
    seen_in_order: set[str] = set(load_order)
    for m in sorted(active_modules, key=lambda m: m.slug):
        if m.slug not in seen_in_order:
            load_order.append(m.slug)

    # Collect contributions from each module in load order.
    module_apps: list[str] = []
    module_middleware: list[str] = []
    module_cp: list[str] = []

    for slug in load_order:
        if slug in module_by_slug:
            m = module_by_slug[slug]
            module_apps.extend(m.manifest.django_apps)
            module_middleware.extend(m.manifest.django_middleware)
            module_cp.extend(m.manifest.django_context_processors)

    # Prepend base settings and deduplicate.
    final_apps = _deduplicate(base_apps + module_apps)
    final_middleware = _deduplicate(base_middleware + module_middleware)
    final_cp = _deduplicate(base_cp + module_cp)

    return SettingsPlan(
        installed_apps=tuple(final_apps),
        middleware=tuple(final_middleware),
        context_processors=tuple(final_cp),
        enabled_modules=tuple(sorted(enabled_slugs)),
        module_order=tuple(load_order),
        capability_providers=cap_overrides,
    )
