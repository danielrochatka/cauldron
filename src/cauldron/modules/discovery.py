"""Entry-point discovery for installed Cauldron modules."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points, packages_distributions
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from . import CauldronModule, ModuleManifest

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "cauldron.modules"


@dataclass
class DiscoveryError:
    """Structured error produced while loading module entry points."""

    entry_point_name: str
    kind: Literal["load_failure", "duplicate_slug", "manifest_validation"]
    message: str


@dataclass(frozen=True)
class DiscoveredModule:
    """Immutable record of one successfully discovered module.

    Captures both the identity of the module and metadata about the entry
    point and distribution package that provided it.  ``source_type`` is
    always ``"package"`` for entry-point-discovered modules; future
    discovery strategies (project folders, #34) will use different values.
    """

    slug: str
    label: str
    version: str
    source_type: Literal["package"]
    package_name: str
    package_version: str
    entry_point_group: str
    entry_point_name: str
    entry_point_value: str
    manifest: ModuleManifest
    module: CauldronModule

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation (no live objects)."""
        return {
            "slug": self.slug,
            "label": self.label,
            "version": self.version,
            "source_type": self.source_type,
            "package_name": self.package_name,
            "package_version": self.package_version,
            "entry_point_group": self.entry_point_group,
            "entry_point_name": self.entry_point_name,
            "entry_point_value": self.entry_point_value,
        }


@dataclass
class DiscoveryResult:
    """Outcome of a module discovery pass.

    ``records`` is the canonical list of successfully discovered modules with
    full source metadata.  ``modules`` is a compat property that returns just
    the module objects for callers that do not need the extra metadata.
    ``errors`` lists structured errors for all entry points that could not be
    loaded, failed manifest validation, or registered a duplicate slug.
    """

    records: list[DiscoveredModule]
    errors: list[DiscoveryError]

    @property
    def modules(self) -> list[CauldronModule]:
        """Backwards-compatible list of module objects, sorted by slug."""
        return [r.module for r in self.records]


def _dist_info_for_ep(ep: Any) -> tuple[str, str]:
    """Return (dist_name, dist_version) for *ep*, with empty-string fallbacks."""
    try:
        # In Python 3.12+ EntryPoint carries .dist; older versions use the
        # packages_distributions mapping as a fallback heuristic.
        dist = getattr(ep, "dist", None)
        if dist is not None:
            return dist.name, dist.version
        module_name = ep.value.split(":")[0].split(".")[0]
        mapping = packages_distributions()
        dist_names = mapping.get(module_name, [])
        if dist_names:
            from importlib.metadata import distribution
            d = distribution(dist_names[0])
            return d.name, d.metadata["Version"]
    except Exception:
        pass
    return "", ""


def _validate_manifest(ep_name: str, obj: Any) -> list[DiscoveryError]:
    """Validate *obj* against the CauldronModule protocol and manifest contract.

    Returns a list of :class:`DiscoveryError` (kind ``"manifest_validation"``)
    for every violation found.  An empty list means the module is valid.
    """
    from . import CauldronModule, ModuleManifest

    errors: list[DiscoveryError] = []

    if not isinstance(obj, CauldronModule):
        errors.append(DiscoveryError(
            entry_point_name=ep_name,
            kind="manifest_validation",
            message=(
                f"Entry point {ep_name!r} yielded {type(obj).__name__!r} which does"
                " not satisfy the CauldronModule protocol."
            ),
        ))
        return errors

    manifest = obj.manifest
    if not isinstance(manifest, ModuleManifest):
        errors.append(DiscoveryError(
            entry_point_name=ep_name,
            kind="manifest_validation",
            message=(
                f"Module from {ep_name!r}: manifest must be a ModuleManifest instance,"
                f" got {type(manifest).__name__!r}."
            ),
        ))
        return errors

    if obj.slug != manifest.slug:
        errors.append(DiscoveryError(
            entry_point_name=ep_name,
            kind="manifest_validation",
            message=(
                f"Module from {ep_name!r}: module.slug {obj.slug!r} does not match"
                f" manifest.slug {manifest.slug!r}."
            ),
        ))

    if obj.label != manifest.label:
        errors.append(DiscoveryError(
            entry_point_name=ep_name,
            kind="manifest_validation",
            message=(
                f"Module from {ep_name!r}: module.label {obj.label!r} does not match"
                f" manifest.label {manifest.label!r}."
            ),
        ))

    # django_apps() must return a tuple or list of strings consistent with
    # manifest.django_apps (if declared).
    try:
        live_apps = obj.django_apps()
    except Exception as exc:
        errors.append(DiscoveryError(
            entry_point_name=ep_name,
            kind="manifest_validation",
            message=(
                f"Module from {ep_name!r}: django_apps() raised {type(exc).__name__}: {exc}."
            ),
        ))
        live_apps = None

    if live_apps is not None and manifest.django_apps:
        if tuple(live_apps) != tuple(manifest.django_apps):
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="manifest_validation",
                message=(
                    f"Module from {ep_name!r}: django_apps() returned {list(live_apps)!r}"
                    f" but manifest.django_apps is {list(manifest.django_apps)!r}."
                ),
            ))

    return errors


def discover_modules(*, entry_point_group: str = ENTRY_POINT_GROUP) -> DiscoveryResult:
    """Discover installed Cauldron modules via Python entry points.

    Returns a :class:`DiscoveryResult` containing successfully discovered
    modules (as :class:`DiscoveredModule` records) and structured errors for
    any entry point that could not be loaded, failed manifest validation, or
    registered a duplicate slug.

    Discovery is deterministic: when multiple entry points share the same
    ``ep.name``, the composite key ``(ep.name, dist_name, ep.value)`` is used
    so that the ordering is stable regardless of ``importlib.metadata``
    iteration order.
    """
    from . import CauldronModule

    eps = entry_points(group=entry_point_group)
    records: list[DiscoveredModule] = []
    errors: list[DiscoveryError] = []
    seen_slugs: dict[str, tuple[str, str]] = {}  # slug -> (ep_name, dist_name)

    def _sort_key(ep: Any) -> tuple[str, str, str]:
        dist_name, _ = _dist_info_for_ep(ep)
        return (ep.name, dist_name, getattr(ep, "value", ""))

    for ep in sorted(eps, key=_sort_key):
        dist_name, dist_version = _dist_info_for_ep(ep)

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

        validation_errors = _validate_manifest(ep.name, obj)
        if validation_errors:
            errors.extend(validation_errors)
            logger.debug("Entry point %r failed manifest validation.", ep.name)
            continue

        slug = obj.slug
        if slug in seen_slugs:
            accepted_ep, accepted_dist = seen_slugs[slug]
            errors.append(DiscoveryError(
                entry_point_name=ep.name,
                kind="duplicate_slug",
                message=(
                    f"Module slug {slug!r} registered by entry point {ep.name!r}"
                    f" (package {dist_name!r}) conflicts with the already-accepted"
                    f" entry point {accepted_ep!r} (package {accepted_dist!r});"
                    f" the duplicate is ignored."
                ),
            ))
            continue

        seen_slugs[slug] = (ep.name, dist_name)
        record = DiscoveredModule(
            slug=slug,
            label=obj.label,
            version=obj.manifest.version,
            source_type="package",
            package_name=dist_name,
            package_version=dist_version,
            entry_point_group=entry_point_group,
            entry_point_name=ep.name,
            entry_point_value=getattr(ep, "value", ""),
            manifest=obj.manifest,
            module=obj,
        )
        records.append(record)
        logger.debug(
            "Discovered module %r from entry point %r (package %r %s).",
            slug, ep.name, dist_name, dist_version,
        )

    records.sort(key=lambda r: r.slug)
    return DiscoveryResult(records=records, errors=errors)


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
