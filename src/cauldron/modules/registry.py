"""Central module registry for the Cauldron module system."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from . import CauldronModule
    from .discovery import DiscoveryError
    from .resolver import ResolutionError, ResolutionResult, ResolutionWarning

logger = logging.getLogger(__name__)


@dataclass
class LifecycleError:
    """Records an unhandled exception raised during a module's lifecycle phase."""

    module_slug: str
    phase: Literal["register", "on_ready"]
    exception: Exception
    message: str


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
        self._lifecycle_errors: list[LifecycleError] = []
        self._populated = False
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
        self._lifecycle_errors = []
        self._populated = False
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
        self._populated = True

        total_errors = len(self._errors) + len(self._discovery_errors)
        if total_errors:
            logger.error("Module graph has %d error(s); activation will be skipped.", total_errors)
        else:
            logger.debug("Module resolution complete. %d module(s) active.", len(self._active))

    def activate(self) -> None:
        """Call ``register()`` then ``on_ready()`` on each active module in load order.

        Activation is skipped entirely if resolution errors exist (dependency
        or version problems for active modules).  Discovery errors for modules
        that are not enabled do not block activation of healthy modules.

        Callers should run ``python manage.py check`` to surface all problems
        before starting the application.
        """
        if self._errors:
            logger.error(
                "Module activation skipped: resolve errors must be fixed first."
                " Run 'python manage.py check' for details."
            )
            return

        from . import ModuleContext

        for slug in self._load_order:
            module = self._active.get(slug)
            if module is None:
                continue

            if hasattr(module, "register"):
                context = ModuleContext(slug=slug, config=self.get_module_config(slug))
                try:
                    module.register(context)  # type: ignore[union-attr]
                except Exception as exc:
                    self._lifecycle_errors.append(LifecycleError(
                        module_slug=slug,
                        phase="register",
                        exception=exc,
                        message=f"Module {slug!r} raised in register(): {exc}",
                    ))
                    logger.exception("register() raised in module %r.", slug)

            if hasattr(module, "on_ready"):
                try:
                    module.on_ready()  # type: ignore[union-attr]
                except Exception as exc:
                    self._lifecycle_errors.append(LifecycleError(
                        module_slug=slug,
                        phase="on_ready",
                        exception=exc,
                        message=f"Module {slug!r} raised in on_ready(): {exc}",
                    ))
                    logger.exception("on_ready() raised in module %r.", slug)

        self._ready = True

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

    def lifecycle_errors(self) -> list[LifecycleError]:
        return list(self._lifecycle_errors)

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

    def graph_info(self) -> list[dict[str, Any]]:
        """Rich module graph for tooling and visualizers.

        Returns one entry per discovered module, sorted by slug.  Each entry
        contains identity, status, load position, capabilities, requirements,
        resolved dependencies, and Django apps.
        """
        load_index_map = {slug: i for i, slug in enumerate(self._load_order)}
        dep_graph = self.dependency_graph()
        result = []
        for slug in sorted(self._discovered):
            m = self._discovered[slug]
            result.append({
                "slug": slug,
                "label": m.label,
                "version": m.manifest.version,
                "active": slug in self._active,
                "load_index": load_index_map.get(slug),
                "provides": sorted(m.manifest.provides),
                "requires": [r.to_dict() for r in m.manifest.requires],
                "optional": [r.to_dict() for r in m.manifest.optional],
                "deps": dep_graph.get(slug, []),
                "django_apps": list(m.django_apps()),
            })
        return result

    # ----------------------------------------------------------------- flags

    @property
    def is_populated(self) -> bool:
        """True after populate() has completed successfully."""
        return self._populated

    @property
    def is_ready(self) -> bool:
        """True after activate() has completed (lifecycle phases finished)."""
        return self._ready

    @property
    def has_errors(self) -> bool:
        return bool(self._errors) or bool(self._discovery_errors)


registry = ModuleRegistry()
