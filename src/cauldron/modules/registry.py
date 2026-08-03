"""Central module registry for the Cauldron module system."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from . import CauldronModule
    from .discovery import DiscoveredModule, DiscoveryError
    from .resolver import ResolutionError, ResolutionResult, ResolutionWarning

logger = logging.getLogger(__name__)


@dataclass
class LifecycleError:
    """Records an unhandled exception raised during a module's lifecycle phase."""

    module_slug: str
    phase: Literal["register", "on_ready"]
    exception: Exception
    message: str


@dataclass(frozen=True)
class UnavailableModule:
    """A slug listed in CAULDRON_MODULES that could not be activated.

    ``reason`` describes why:

    * ``"not_discovered"`` — no entry point with this slug was found at all.
    * ``"load_failure"`` — the corresponding entry point raised on load.
    * ``"manifest_validation"`` — the module failed validation after loading.

    ``discovery_error_message`` carries the public-safe message from the
    matching :class:`~discovery.DiscoveryError` when ``reason`` is not
    ``"not_discovered"``; empty string otherwise.
    """

    slug: str
    reason: Literal["not_discovered", "load_failure", "manifest_validation"] = "not_discovered"
    discovery_error_message: str = ""


def _validate_discovery_records(
    modules: list[CauldronModule],
    records: list[DiscoveredModule],
) -> None:
    """Raise ValueError if *records* and *modules* are inconsistent.

    Checks performed before populate() mutates any state:
    - No duplicate slugs in *records*.
    - Slug sets of *modules* and *records* must be equal.
    - Each record's ``.module`` must be the identical object as the matching module.
    - Each record's ``.manifest`` must be the identical object as ``module.manifest``.
    - ``record.label`` must equal ``module.label``.
    - ``record.version`` must equal ``module.manifest.version``.
    """
    # Duplicate slug check
    seen: set[str] = set()
    for rec in records:
        if rec.slug in seen:
            raise ValueError(
                f"discovery_records contains duplicate slug {rec.slug!r}."
            )
        seen.add(rec.slug)

    module_slugs = {m.slug for m in modules}
    record_slugs = {r.slug for r in records}
    if module_slugs != record_slugs:
        extra = sorted(record_slugs - module_slugs)
        missing = sorted(module_slugs - record_slugs)
        parts: list[str] = []
        if extra:
            parts.append(f"extra in records: {extra!r}")
        if missing:
            parts.append(f"missing from records: {missing!r}")
        raise ValueError(
            f"discovery_records slug set does not match module slug set: {'; '.join(parts)}."
        )

    module_by_slug = {m.slug: m for m in modules}
    for rec in records:
        m = module_by_slug[rec.slug]
        if rec.module is not m:
            raise ValueError(
                f"discovery_records[{rec.slug!r}].module is not the same object"
                " as the provided module."
            )
        if rec.manifest is not m.manifest:
            raise ValueError(
                f"discovery_records[{rec.slug!r}].manifest is not the module's manifest."
            )
        if rec.label != m.label:
            raise ValueError(
                f"discovery_records[{rec.slug!r}].label {rec.label!r} does not"
                f" match module label {m.label!r}."
            )
        if rec.version != m.manifest.version:
            raise ValueError(
                f"discovery_records[{rec.slug!r}].version {rec.version!r} does not"
                f" match manifest version {m.manifest.version!r}."
            )


class ModuleRegistry:
    """Manages discovered, resolved, and active Cauldron modules."""

    def __init__(self) -> None:
        self._discovered: dict[str, CauldronModule] = {}
        self._active: dict[str, CauldronModule] = {}
        self._load_order: list[str] = []
        self._capability_providers: dict[str, list[str]] = {}
        self._capability_overrides: dict[str, str] = {}
        self._module_configs: dict[str, dict[str, Any]] = {}
        self._errors: list[ResolutionError] = []
        self._warnings: list[ResolutionWarning] = []
        self._discovery_errors: list[DiscoveryError] = []
        self._lifecycle_errors: list[LifecycleError] = []
        self._enabled: set[str] = set()
        self._discovery_records: list[DiscoveredModule] = []
        self._unavailable: list[UnavailableModule] = []
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
        discovery_records: list[DiscoveredModule] | None = None,
    ) -> None:
        """Register modules, resolve dependencies, determine load order.

        *enabled* controls which discovered modules are activated:

        - ``None`` (default) — activates **all** provided modules.  Use this
          in tests and when you want every installed module active.
        - An explicit ``set`` — activates only the listed slugs.  Pass an
          empty set to activate nothing.  This is the production model;
          ``apps.py`` derives the set from ``CAULDRON_MODULES``.

        *discovery_records* carries the full :class:`~discovery.DiscoveredModule`
        metadata for each successfully discovered module, used by
        :meth:`inventory`.

        Safe to call multiple times; replaces all previous state.
        """
        from .resolver import resolve
        from cauldron import __version__ as cauldron_version

        # Validate consistency before mutating any state.
        if discovery_records is not None:
            _validate_discovery_records(modules, discovery_records)

        self._discovered = {}
        self._active = {}
        self._load_order = []
        self._capability_providers = {}
        self._capability_overrides = dict(capability_overrides or {})
        self._module_configs = dict(module_configs or {})
        self._errors = []
        self._warnings = []
        self._discovery_errors = list(discovery_errors or [])
        self._lifecycle_errors = []
        self._discovery_records = list(discovery_records or [])
        self._unavailable = []
        self._populated = False
        self._ready = False

        for module in sorted(modules, key=lambda m: m.slug):
            self._discovered[module.slug] = module

        if enabled is None:
            self._enabled = set(self._discovered.keys())
            active_modules = dict(self._discovered)
        else:
            self._enabled = set(enabled)
            active_modules = {
                slug: m for slug, m in self._discovered.items() if slug in enabled
            }
            # Build a map of candidate_slug → DiscoveryError for the errors
            # we received, so we can attach accurate reasons to unavailable slugs.
            error_by_candidate: dict[str, DiscoveryError] = {}
            for err in self._discovery_errors:
                if err.candidate_slug and err.candidate_slug not in error_by_candidate:
                    error_by_candidate[err.candidate_slug] = err

            discovered_slugs = set(self._discovered)
            for slug in sorted(self._enabled):
                if slug not in discovered_slugs:
                    err = error_by_candidate.get(slug)
                    if err is not None:
                        reason: Literal[
                            "not_discovered", "load_failure", "manifest_validation"
                        ] = (
                            err.kind  # type: ignore[assignment]
                            if err.kind in ("load_failure", "manifest_validation")
                            else "not_discovered"
                        )
                        self._unavailable.append(UnavailableModule(
                            slug=slug,
                            reason=reason,
                            discovery_error_message=err.message,
                        ))
                    else:
                        self._unavailable.append(UnavailableModule(slug=slug))

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

    def enabled_slugs(self) -> frozenset[str]:
        """Return the set of slugs that were explicitly enabled at populate time."""
        return frozenset(self._enabled)

    def unavailable_modules(self) -> list[UnavailableModule]:
        """Slugs listed in CAULDRON_MODULES with no matching active discovery record."""
        return list(self._unavailable)

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

    def _resolved_required_graph(self) -> dict[str, list[str]]:
        """Required-dependency graph using provider-selection semantics.

        Unlike :meth:`dependency_graph` (which lists all capability providers),
        this graph uses the same single-provider / override selection as the
        resolver, so only the actually-selected provider becomes a blocking
        edge.  Optional dependencies are excluded.  Used only by
        :meth:`_blocked_slugs`.
        """
        graph: dict[str, list[str]] = {}
        for slug in sorted(self._discovered):
            module = self._discovered[slug]
            deps: list[str] = []
            for req in module.manifest.requires:
                if req.kind == "module":
                    deps.append(req.slug)
                elif req.kind == "capability":
                    providers = sorted(self._capability_providers.get(req.slug, []))
                    if len(providers) == 1:
                        deps.extend(providers)
                    elif len(providers) > 1:
                        override = self._capability_overrides.get(req.slug)
                        if override and override in providers:
                            deps.append(override)
                        # else: CAPABILITY_CONFLICT — consumer is directly blocked
            graph[slug] = sorted(set(deps))
        return graph

    def _blocked_slugs(self) -> frozenset[str]:
        """Compute the set of slugs blocked by resolution errors (with propagation).

        A module is blocked if:
        - it has a direct resolution error (missing dep, version mismatch, etc.), or
        - one of its selected required dependencies is blocked (transitive).

        Uses :meth:`_resolved_required_graph` so that an unselected capability
        provider being blocked does not transitively block the consumer.
        Circular-dependency modules are already absent from ``_active``.
        """
        directly_blocked = frozenset(e.module_slug for e in self._errors)
        dep_graph = self._resolved_required_graph()

        blocked: set[str] = set(directly_blocked)
        changed = True
        while changed:
            changed = False
            for slug, deps in dep_graph.items():
                if slug not in blocked and any(dep in blocked for dep in deps):
                    blocked.add(slug)
                    changed = True
        return frozenset(blocked)

    def inventory(self) -> list[dict[str, Any]]:
        """Rich inventory combining discovery metadata and resolution state.

        Returns one entry per discovered module, sorted by slug.  Each entry
        contains:

        - **identity & source**: slug, label, version, source_type,
          package_name, package_version, entry_point_group,
          entry_point_name, entry_point_value
        - **manifest**: the full serialized manifest dict
        - **compatibility**: installed_cauldron_version,
          cauldron_version_constraint, cauldron_version_ok (always bool)
        - **config & activation**: enabled, active, load_index, config
        - **convenience projections**: provides, requires, optional, deps,
          django_apps (from manifest), requires_restart
        """
        from .resolver import version_satisfies

        try:
            from cauldron import __version__ as installed_cauldron_version
        except Exception:
            installed_cauldron_version = ""

        record_by_slug: dict[str, DiscoveredModule] = {
            r.slug: r for r in self._discovery_records
        }
        load_index_map = {slug: i for i, slug in enumerate(self._load_order)}
        dep_graph = self.dependency_graph()
        blocked = self._blocked_slugs()

        result = []
        for slug in sorted(self._discovered):
            m = self._discovered[slug]
            manifest = m.manifest
            rec = record_by_slug.get(slug)
            constraint = manifest.cauldron_version
            compat: bool = version_satisfies(installed_cauldron_version, constraint)
            is_active = (slug in self._active) and (slug not in blocked)
            entry: dict[str, Any] = {
                # identity & source
                "slug": slug,
                "label": m.label,
                "version": manifest.version,
                "source_type": rec.source_type if rec else None,
                "package_name": rec.package_name if rec else None,
                "package_version": rec.package_version if rec else None,
                "entry_point_group": rec.entry_point_group if rec else None,
                "entry_point_name": rec.entry_point_name if rec else None,
                "entry_point_value": rec.entry_point_value if rec else None,
                # manifest
                "manifest": manifest.to_dict(),
                # compatibility
                "installed_cauldron_version": installed_cauldron_version,
                "cauldron_version_constraint": constraint,
                "cauldron_version_ok": compat,
                # config & activation
                "enabled": slug in self._enabled,
                "active": is_active,
                "load_index": load_index_map.get(slug) if is_active else None,
                "config": self.get_module_config(slug),
                # convenience projections (from manifest, not the live module)
                "provides": sorted(manifest.provides),
                "requires": [r.to_dict() for r in manifest.requires],
                "optional": [r.to_dict() for r in manifest.optional],
                "deps": dep_graph.get(slug, []),
                "django_apps": list(manifest.django_apps),
                "requires_restart": manifest.requires_restart,
            }
            result.append(entry)
        return result

    def graph_info(self) -> list[dict[str, Any]]:
        """Rich module graph for tooling and visualizers.

        Returns one entry per discovered module, sorted by slug.  Each entry
        contains identity, status, load position, capabilities, requirements,
        resolved dependencies, and Django apps.

        This is a stable subset of :meth:`inventory` keys.
        """
        return [
            {
                "slug": e["slug"],
                "label": e["label"],
                "version": e["version"],
                "active": e["active"],
                "load_index": e["load_index"],
                "provides": e["provides"],
                "requires": e["requires"],
                "optional": e["optional"],
                "deps": e["deps"],
                "django_apps": e["django_apps"],
            }
            for e in self.inventory()
        ]

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
