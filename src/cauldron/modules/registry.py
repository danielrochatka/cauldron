"""Central module registry for the Cauldron module system."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import CauldronModule
    from .discovery import DiscoveryError
    from .resolver import ResolutionError, ResolutionResult, ResolutionWarning

logger = logging.getLogger(__name__)


class ModuleRegistry:
    """Manages discovered, resolved, and active Cauldron modules."""

    def __init__(self) -> None:
        self._discovered: dict[str, CauldronModule] = {}
        self._active: dict[str, CauldronModule] = {}
        self._load_order: list[str] = []
        self._capability_providers: dict[str, list[str]] = {}
        self._module_configs: dict[str, dict[str, Any]] = {}
        self._errors: list[ResolutionError] = []
        self._warnings: list[ResolutionWarning] = []
        self._discovery_errors: list[DiscoveryError] = []
        self._ready = False

    def populate(
        self,
        modules: list[CauldronModule],
        *,
        enabled: set[str] | None = None,
        module_configs: dict[str, dict[str, Any]] | None = None,
        discovery_errors: list[DiscoveryError] | None = None,
        capability_overrides: dict[str, str] | None = None,
    ) -> None:
        """Register modules, resolve dependencies, determine load order.

        *enabled* controls which discovered modules are activated:

        - ``None`` (default) — activates **all** provided modules.  Use this
          in tests and when you want every installed module active.
        - An explicit ``set`` — activates only the listed slugs.  Pass an
          empty set to activate nothing.  This is the production model;
          ``apps.py`` derives the set from ``CAULDRON_MODULES``.

        Safe to call multiple times; replaces all previous state.
        """
        from .resolver import resolve
        from cauldron import __version__ as cauldron_version

        self._discovered = {}
        self._active = {}
        self._load_order = []
        self._capability_providers = {}
        self._module_configs = dict(module_configs or {})
        self._errors = []
        self._warnings = []
        self._discovery_errors = list(discovery_errors or [])
        self._ready = False

        for module in sorted(modules, key=lambda m: m.slug):
            self._discovered[module.slug] = module

        if enabled is None:
            active_modules = dict(self._discovered)
        else:
            active_modules = {
                slug: m for slug, m in self._discovered.items() if slug in enabled
            }

        for slug, module in sorted(active_modules.items()):
            for cap in sorted(module.manifest.provides):
                self._capability_providers.setdefault(cap, []).append(slug)

        result: ResolutionResult = resolve(
            list(active_modules.values()),
            self._capability_providers,
            cauldron_version=cauldron_version,
            capability_overrides=capability_overrides or {},
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

        total_errors = len(self._errors) + len(self._discovery_errors)
        if total_errors:
            logger.error("Module graph has %d error(s); activation will be skipped.", total_errors)
        else:
            logger.debug("Module resolution complete. %d module(s) active.", len(self._active))

    def activate(self) -> None:
        """Call ``on_ready()`` on each active module in load order.

        Activation is skipped entirely if any discovery or resolution errors
        exist.  Callers should run ``python manage.py check`` to surface the
        problems before proceeding.
        """
        if self.has_errors:
            logger.error(
                "Module activation skipped: resolve errors must be fixed first."
                " Run 'python manage.py check' for details."
            )
            return
        for slug in self._load_order:
            module = self._active.get(slug)
            if module is not None and hasattr(module, "on_ready"):
                try:
                    module.on_ready()  # type: ignore[union-attr]
                except Exception:
                    logger.exception("on_ready() raised in module %r.", slug)

    # ------------------------------------------------------------------ query

    def get(self, slug: str) -> CauldronModule | None:
        return self._active.get(slug)

    def all_active(self) -> list[CauldronModule]:
        return [self._active[s] for s in self._load_order if s in self._active]

    def all_discovered(self) -> list[CauldronModule]:
        return [self._discovered[s] for s in sorted(self._discovered)]

    def capabilities(self) -> dict[str, list[str]]:
        return {cap: sorted(providers) for cap, providers in self._capability_providers.items()}

    def get_module_config(self, slug: str) -> dict[str, Any]:
        """Return the site-provided configuration dict for *slug*, or ``{}``."""
        return dict(self._module_configs.get(slug, {}))

    def errors(self) -> list[ResolutionError]:
        return list(self._errors)

    def warnings(self) -> list[ResolutionWarning]:
        return list(self._warnings)

    def discovery_errors(self) -> list[DiscoveryError]:
        return list(self._discovery_errors)

    def dependency_graph(self) -> dict[str, list[str]]:
        """Machine-readable map of module slug to its resolved dependency slugs.

        Only includes slugs of discovered modules.  Deterministically ordered.
        """
        graph: dict[str, list[str]] = {}
        for slug in sorted(self._discovered):
            module = self._discovered[slug]
            deps: list[str] = []
            for req in module.manifest.requires:
                if req.kind == "module":
                    deps.append(req.slug)
                elif req.kind == "capability":
                    deps.extend(self._capability_providers.get(req.slug, []))
            graph[slug] = sorted(set(deps))
        return graph

    # ----------------------------------------------------------------- flags

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def has_errors(self) -> bool:
        return bool(self._errors) or bool(self._discovery_errors)


registry = ModuleRegistry()
