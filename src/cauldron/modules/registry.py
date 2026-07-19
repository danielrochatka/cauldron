"""Central module registry for the Cauldron module system."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import CauldronModule
    from .resolver import ResolutionError, ResolutionResult, ResolutionWarning

logger = logging.getLogger(__name__)


class ModuleRegistry:
    """Manages discovered, resolved, and active Cauldron modules."""

    def __init__(self) -> None:
        self._discovered: dict[str, CauldronModule] = {}
        self._active: dict[str, CauldronModule] = {}
        self._load_order: list[str] = []
        self._capability_providers: dict[str, list[str]] = {}
        self._errors: list[ResolutionError] = []
        self._warnings: list[ResolutionWarning] = []
        self._ready = False

    def populate(
        self,
        modules: list[CauldronModule],
        *,
        disabled: set[str] | None = None,
    ) -> None:
        """Register discovered modules, resolve dependencies, determine load order.

        Safe to call multiple times; replaces previous state on each call.
        """
        from . import CauldronModule  # noqa: F401 – ensure Protocol is imported
        from .resolver import resolve
        from cauldron import __version__ as cauldron_version

        self._discovered = {}
        self._active = {}
        self._load_order = []
        self._capability_providers = {}
        self._errors = []
        self._warnings = []
        self._ready = False

        disabled = disabled or set()

        for module in modules:
            self._discovered[module.slug] = module

        active_modules = {
            slug: m for slug, m in self._discovered.items() if slug not in disabled
        }

        for slug, module in active_modules.items():
            for cap in module.manifest.provides:
                self._capability_providers.setdefault(cap, []).append(slug)

        result: ResolutionResult = resolve(
            list(active_modules.values()),
            self._capability_providers,
            cauldron_version=cauldron_version,
        )

        self._load_order = result.load_order
        self._errors = result.errors
        self._warnings = result.warnings
        self._active = {
            slug: active_modules[slug]
            for slug in result.load_order
            if slug in active_modules
        }
        self._ready = True

        if result.errors:
            logger.error(
                "Module resolution completed with %d error(s).", len(result.errors)
            )
        else:
            logger.debug(
                "Module resolution complete. %d module(s) active.", len(self._active)
            )

    def activate(self) -> None:
        """Call on_ready() on each active module in load order."""
        for slug in self._load_order:
            module = self._active.get(slug)
            if module is not None and hasattr(module, "on_ready"):
                try:
                    module.on_ready()  # type: ignore[union-attr]
                except Exception:
                    logger.exception("on_ready() raised in module %r.", slug)

    def get(self, slug: str) -> CauldronModule | None:
        return self._active.get(slug)

    def all_active(self) -> list[CauldronModule]:
        return [self._active[s] for s in self._load_order if s in self._active]

    def all_discovered(self) -> list[CauldronModule]:
        return list(self._discovered.values())

    def capabilities(self) -> dict[str, list[str]]:
        return dict(self._capability_providers)

    def errors(self) -> list[ResolutionError]:
        return list(self._errors)

    def warnings(self) -> list[ResolutionWarning]:
        return list(self._warnings)

    def dependency_graph(self) -> dict[str, list[str]]:
        """Machine-readable map of module slug to its dependency slugs."""
        graph: dict[str, list[str]] = {}
        for slug, module in self._discovered.items():
            deps: list[str] = []
            for req in module.manifest.requires:
                if req.kind == "module":
                    deps.append(req.slug)
                elif req.kind == "capability":
                    deps.extend(self._capability_providers.get(req.slug, []))
            graph[slug] = sorted(set(deps))
        return graph

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def has_errors(self) -> bool:
        return bool(self._errors)


registry = ModuleRegistry()
