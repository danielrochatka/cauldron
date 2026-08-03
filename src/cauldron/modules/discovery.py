"""Entry-point discovery for installed Cauldron modules."""

from __future__ import annotations

import importlib
import logging
import sys
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from . import CauldronModule, ModuleManifest

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "cauldron.modules"

# Source types: "package" for entry-point discoveries (#33); "project" is
# reserved for project-folder discovery (#34) and must not be produced here.
SourceType = Literal["package", "project"]


@dataclass(frozen=True)
class _EntryPointSource:
    """Immutable per-entry-point metadata resolved exactly once before loading.

    Using ``.dist`` as the authoritative source when available (Python 3.9+).
    When ``.dist`` is absent the package metadata fields are empty strings.
    The ``canonical_package_name`` uses PEP 503 normalisation for sort
    stability; ``display_package_name`` preserves the original casing for
    messages and serialisation.
    """

    group: str
    name: str
    value: str
    display_package_name: str
    canonical_package_name: str
    package_version: str


@dataclass(frozen=True)
class _ProjectSource:
    """Source metadata for a project-folder candidate.

    Mirrors the fields of :class:`_EntryPointSource` that :func:`_make_error`
    and :func:`_validate_manifest` use, so those functions accept both types.

    ``name`` is ``"modules/<dirname>"`` and ``value`` is the bare Python import
    name (the directory name).  Package-level fields are empty for project
    sources.
    """

    name: str
    value: str
    group: str = ""
    display_package_name: str = ""
    canonical_package_name: str = ""
    package_version: str = ""


def _source_for_ep(ep: Any, group: str) -> _EntryPointSource:
    """Resolve one :class:`_EntryPointSource` for *ep* without side effects.

    Uses ``ep.dist`` exclusively; the ``packages_distributions()`` heuristic
    is intentionally omitted because its ordering is not contractual and it
    can select the wrong distribution.
    """
    from packaging.utils import canonicalize_name

    name = getattr(ep, "name", "")
    value = getattr(ep, "value", "")
    display_name = ""
    pkg_version = ""

    try:
        dist = getattr(ep, "dist", None)
        if dist is not None:
            display_name = dist.name or ""
            pkg_version = dist.version or ""
    except Exception:
        pass

    try:
        canonical = canonicalize_name(display_name) if display_name else ""
    except Exception:
        canonical = ""

    return _EntryPointSource(
        group=group,
        name=name,
        value=value,
        display_package_name=display_name,
        canonical_package_name=canonical,
        package_version=pkg_version,
    )


@dataclass(frozen=True)
class DiscoveryError:
    """Structured error produced while loading module entry points.

    All text in ``message`` is safe for user-facing output: it may identify
    the exception class but never includes raw exception messages, tracebacks,
    absolute filesystem paths, or credentials.

    ``candidate_slug`` carries the module slug when it is determinable before
    the error (e.g. duplicate_slug, manifest_validation with a valid manifest).
    It is ``None`` for load_failure and early protocol failures.

    For ``duplicate_slug`` errors, ``accepted_entry_point_name`` and
    ``accepted_package_name`` identify the already-registered counterpart so
    callers do not need to parse ``message``.
    """

    entry_point_name: str
    kind: Literal["load_failure", "duplicate_slug", "manifest_validation", "project_path"]
    message: str
    entry_point_group: str = ""
    entry_point_value: str = ""
    package_name: str = ""
    package_version: str = ""
    candidate_slug: str | None = None
    # Structured accepted-EP identity for duplicate_slug errors:
    accepted_entry_point_name: str = ""
    accepted_package_name: str = ""


@dataclass(frozen=True)
class DiscoveredModule:
    """Immutable record of one successfully discovered module.

    Captures the module identity and the full entry-point / distribution
    provenance.  ``source_type`` is ``"package"`` for entry-point-discovered
    modules; ``"project"`` is reserved for project-folder discovery (#34).
    """

    slug: str
    label: str
    version: str
    source_type: SourceType
    package_name: str
    package_version: str
    entry_point_group: str
    entry_point_name: str
    entry_point_value: str
    manifest: ModuleManifest
    module: CauldronModule
    # Root-relative POSIX path for project-source modules; empty for package sources.
    project_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict (no live objects).

        ``manifest`` is serialised via :meth:`ModuleManifest.to_dict`; the
        live ``module`` object is excluded.  Mutating any list or dict in
        the returned value does not affect the record or manifest.
        """
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
            "project_path": self.project_path,
            "manifest": self.manifest.to_dict(),
        }


@dataclass
class DiscoveryResult:
    """Outcome of a module discovery pass.

    ``records`` is the canonical list of successfully discovered modules with
    full source metadata.  ``modules`` is a backwards-compatible property
    returning just the module objects.  ``errors`` lists structured errors for
    every entry point that could not be loaded, failed manifest validation, or
    registered a duplicate slug.

    Construct via ``DiscoveryResult(records=..., errors=...)``.  The
    ``modules`` attribute is an accessor, not a constructor parameter.
    """

    records: list[DiscoveredModule]
    errors: list[DiscoveryError]

    @property
    def modules(self) -> list[CauldronModule]:
        """Backwards-compatible list of module objects, sorted by slug."""
        return [r.module for r in self.records]


def _make_error(
    src: _EntryPointSource | _ProjectSource,
    kind: Literal["load_failure", "duplicate_slug", "manifest_validation"],
    message: str,
    *,
    candidate_slug: str | None = None,
    accepted_entry_point_name: str = "",
    accepted_package_name: str = "",
) -> DiscoveryError:
    """Construct a :class:`DiscoveryError` with all source fields populated."""
    return DiscoveryError(
        entry_point_name=src.name,
        kind=kind,
        message=message,
        entry_point_group=src.group,
        entry_point_value=src.value,
        package_name=src.display_package_name,
        package_version=src.package_version,
        candidate_slug=candidate_slug,
        accepted_entry_point_name=accepted_entry_point_name,
        accepted_package_name=accepted_package_name,
    )


def _validate_manifest(
    src: _EntryPointSource | _ProjectSource,
    obj: Any,
    *,
    provisional_candidate: str | None = None,
) -> list[DiscoveryError]:
    """Validate *obj* against the CauldronModule protocol and manifest contract.

    Returns a list of :class:`DiscoveryError` (kind ``"manifest_validation"``)
    for every violation.  An empty return means the module is valid.

    *provisional_candidate* is used as ``candidate_slug`` for errors emitted
    before the module's manifest slug is confirmed.  It is typically derived
    from the entry-point name when that name is a valid module slug.  Once a
    valid :class:`ModuleManifest` is obtained, its slug becomes authoritative.

    All protocol attribute accesses are guarded; a property that raises does
    not escape as an unhandled exception.

    ``django_apps()`` return value is normalised to a tuple exactly once and
    compared against ``manifest.django_apps`` including when that tuple is
    empty.  ``str`` and ``bytes`` returns are rejected; generators and other
    iterables are accepted.
    """
    from . import CauldronModule, ModuleManifest

    errors: list[DiscoveryError] = []

    # --- 1. Protocol check ------------------------------------------------
    if not isinstance(obj, CauldronModule):
        errors.append(_make_error(
            src, "manifest_validation",
            f"Entry point {src.name!r} yielded {type(obj).__name__!r} which does"
            " not satisfy the CauldronModule protocol.",
            candidate_slug=provisional_candidate,
        ))
        return errors

    # --- 2. Manifest type -------------------------------------------------
    try:
        manifest = obj.manifest
    except Exception as exc:
        errors.append(_make_error(
            src, "manifest_validation",
            f"Entry point {src.name!r}: accessing .manifest raised"
            f" {type(exc).__name__}.",
            candidate_slug=provisional_candidate,
        ))
        return errors

    if not isinstance(manifest, ModuleManifest):
        errors.append(_make_error(
            src, "manifest_validation",
            f"Entry point {src.name!r}: manifest must be a ModuleManifest"
            f" instance, got {type(manifest).__name__!r}.",
            candidate_slug=provisional_candidate,
        ))
        return errors

    # After confirming manifest type, we know the canonical slug.
    candidate_slug: str | None = manifest.slug

    # --- 3. Slug consistency ----------------------------------------------
    try:
        live_slug = obj.slug
    except Exception as exc:
        errors.append(_make_error(
            src, "manifest_validation",
            f"Entry point {src.name!r}: accessing .slug raised"
            f" {type(exc).__name__}.",
            candidate_slug=candidate_slug,
        ))
        live_slug = None

    if live_slug is not None:
        if not isinstance(live_slug, str) or not live_slug:
            errors.append(_make_error(
                src, "manifest_validation",
                f"Entry point {src.name!r}: module.slug must be a non-empty"
                f" string, got {live_slug!r}.",
                candidate_slug=candidate_slug,
            ))
        elif live_slug != manifest.slug:
            errors.append(_make_error(
                src, "manifest_validation",
                f"Entry point {src.name!r}: module.slug {live_slug!r} does not"
                f" match manifest.slug {manifest.slug!r}.",
                candidate_slug=candidate_slug,
            ))

    # --- 4. Label consistency ---------------------------------------------
    try:
        live_label = obj.label
    except Exception as exc:
        errors.append(_make_error(
            src, "manifest_validation",
            f"Entry point {src.name!r}: accessing .label raised"
            f" {type(exc).__name__}.",
            candidate_slug=candidate_slug,
        ))
        live_label = None

    if live_label is not None:
        if not isinstance(live_label, str) or not live_label:
            errors.append(_make_error(
                src, "manifest_validation",
                f"Entry point {src.name!r}: module.label must be a non-empty"
                f" string, got {live_label!r}.",
                candidate_slug=candidate_slug,
            ))
        elif live_label != manifest.label:
            errors.append(_make_error(
                src, "manifest_validation",
                f"Entry point {src.name!r}: module.label {live_label!r} does"
                f" not match manifest.label {manifest.label!r}.",
                candidate_slug=candidate_slug,
            ))

    # --- 5. django_apps() -------------------------------------------------
    try:
        live_result = obj.django_apps()
    except Exception as exc:
        errors.append(_make_error(
            src, "manifest_validation",
            f"Entry point {src.name!r}: django_apps() raised"
            f" {type(exc).__name__}.",
            candidate_slug=candidate_slug,
        ))
        return errors

    # Reject str/bytes — both are iterable but are not app-label lists.
    if isinstance(live_result, (str, bytes)):
        errors.append(_make_error(
            src, "manifest_validation",
            f"Entry point {src.name!r}: django_apps() returned"
            f" {type(live_result).__name__!r}; expected an iterable of"
            " app-label strings.",
            candidate_slug=candidate_slug,
        ))
        return errors

    # Normalise to tuple exactly once; handles generators and other iterables.
    # str/bytes are already rejected above; everything else we try to consume.
    try:
        live_apps: tuple[Any, ...] = tuple(live_result)
    except TypeError:
        errors.append(_make_error(
            src, "manifest_validation",
            f"Entry point {src.name!r}: django_apps() returned"
            f" {type(live_result).__name__!r}; expected a sequence.",
            candidate_slug=candidate_slug,
        ))
        return errors

    for i, app in enumerate(live_apps):
        if not isinstance(app, str):
            errors.append(_make_error(
                src, "manifest_validation",
                f"Entry point {src.name!r}: django_apps()[{i}] is"
                f" {type(app).__name__!r}; every app label must be a string.",
                candidate_slug=candidate_slug,
            ))
            return errors
        if not app:
            errors.append(_make_error(
                src, "manifest_validation",
                f"Entry point {src.name!r}: django_apps()[{i}] is an empty"
                " string; every app label must be non-empty.",
                candidate_slug=candidate_slug,
            ))
            return errors

    if live_apps != manifest.django_apps:
        errors.append(_make_error(
            src, "manifest_validation",
            f"Entry point {src.name!r}: django_apps() result does not match"
            f" manifest.django_apps.",
            candidate_slug=candidate_slug,
        ))

    return errors


def _discover_project_modules(
    root: Path,
    *,
    seen_slugs: dict[str, tuple[str, str]],
) -> tuple[list[DiscoveredModule], list[DiscoveryError]]:
    """Discover modules from a project-folder root directory.

    Scans direct child directories of *root* in lexical order.  Each child
    directory that contains an ``__init__.py`` and exposes a ``module``
    attribute satisfying :class:`~cauldron.modules.CauldronModule` is loaded
    as a project-source module.

    *seen_slugs* is mutated in-place so that the caller's package-module pass
    inherits duplicate detection across both sources.

    Path-level failures (root absent or not a directory) produce
    ``kind="project_path"`` errors.  Per-module failures use the standard
    ``"load_failure"``, ``"manifest_validation"``, and ``"duplicate_slug"``
    kinds so existing check IDs (E020–E022) apply.

    The resolved root is prepended to :data:`sys.path` at most once, only
    after the directory is confirmed to exist.
    """
    records: list[DiscoveredModule] = []
    errors: list[DiscoveryError] = []

    if not root.is_dir():
        errors.append(DiscoveryError(
            entry_point_name="",
            kind="project_path",
            message=(
                "CAULDRON_PROJECT_MODULE_ROOT does not exist or is not a"
                " directory."
            ),
        ))
        return records, errors

    resolved = str(root.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)

    try:
        candidates = sorted(
            e for e in root.iterdir()
            if e.is_dir() and (e / "__init__.py").is_file()
        )
    except OSError as exc:
        errors.append(DiscoveryError(
            entry_point_name="",
            kind="project_path",
            message=(
                f"CAULDRON_PROJECT_MODULE_ROOT could not be scanned:"
                f" {type(exc).__name__}."
            ),
        ))
        return records, errors

    for entry in candidates:
        dir_name = entry.name
        # Skip private packages and names that aren't valid Python identifiers.
        if not dir_name.isidentifier() or dir_name.startswith("_"):
            continue

        ep_name = f"modules/{dir_name}"
        src = _ProjectSource(name=ep_name, value=dir_name)

        # --- Load ----------------------------------------------------------
        try:
            mod_obj = importlib.import_module(dir_name)
        except Exception as exc:
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="load_failure",
                message=(
                    f"Project module {ep_name!r} raised {type(exc).__name__}"
                    " on import."
                ),
            ))
            logger.debug(
                "Project module %r failed to import: %s",
                ep_name, exc,
                exc_info=True,
            )
            continue

        obj = getattr(mod_obj, "module", None)
        if obj is None:
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="load_failure",
                message=(
                    f"Project module {ep_name!r}: package {dir_name!r} has"
                    " no 'module' attribute."
                ),
            ))
            continue

        # --- Manifest validation -------------------------------------------
        validation_errors = _validate_manifest(src, obj)
        if validation_errors:
            errors.extend(validation_errors)
            logger.debug(
                "Project module %r failed manifest validation.", ep_name,
            )
            continue

        # --- Duplicate slug ------------------------------------------------
        slug = obj.slug
        if slug in seen_slugs:
            accepted_ep_name, accepted_pkg_name = seen_slugs[slug]
            pkg_note = f" (package {accepted_pkg_name!r})" if accepted_pkg_name else ""
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="duplicate_slug",
                message=(
                    f"Module slug {slug!r} registered by project module"
                    f" {ep_name!r} conflicts with {accepted_ep_name!r}"
                    f"{pkg_note}; duplicate is ignored."
                ),
                candidate_slug=slug,
                accepted_entry_point_name=accepted_ep_name,
                accepted_package_name=accepted_pkg_name,
            ))
            continue

        seen_slugs[slug] = (ep_name, "")
        record = DiscoveredModule(
            slug=slug,
            label=obj.label,
            version=obj.manifest.version,
            source_type="project",
            package_name="",
            package_version="",
            entry_point_group="",
            entry_point_name=ep_name,
            entry_point_value=dir_name,
            manifest=obj.manifest,
            module=obj,
            project_path=dir_name,
        )
        records.append(record)
        logger.debug("Discovered project module %r from %r.", slug, ep_name)

    return records, errors


def discover_modules(
    *,
    entry_point_group: str = ENTRY_POINT_GROUP,
    project_module_root: Path | None = None,
) -> DiscoveryResult:
    """Discover installed Cauldron modules via Python entry points.

    Returns a :class:`DiscoveryResult` containing successfully discovered
    modules (as :class:`DiscoveredModule` records) and structured errors for
    any entry point that could not be loaded, failed manifest validation, or
    registered a duplicate slug.

    When *project_module_root* is provided, project-folder modules are
    discovered first (lexical order by directory name) and merged with
    entry-point modules.  Project modules win duplicate-slug races.

    Discovery is deterministic: combined records are sorted by slug.
    Entry-point metadata is resolved exactly once per entry point.
    """
    from . import CauldronModule, _validate_slug

    records: list[DiscoveredModule] = []
    errors: list[DiscoveryError] = []
    # slug → (accepted_ep_name, accepted_pkg_name); shared across both passes.
    seen_slugs: dict[str, tuple[str, str]] = {}

    # --- Project-folder pass (first, so project modules win slug races) ----
    if project_module_root is not None:
        proj_records, proj_errors = _discover_project_modules(
            project_module_root, seen_slugs=seen_slugs,
        )
        records.extend(proj_records)
        errors.extend(proj_errors)

    # --- Entry-point pass --------------------------------------------------
    raw_eps = entry_points(group=entry_point_group)

    ep_pairs = [(ep, _source_for_ep(ep, entry_point_group)) for ep in raw_eps]
    ep_pairs.sort(key=lambda pair: (
        pair[1].name,
        pair[1].canonical_package_name,
        pair[1].value,
    ))

    for ep, src in ep_pairs:
        # Derive provisional candidate slug from the entry-point name when it
        # is a valid module slug.  Used as candidate_slug for load failures and
        # early manifest failures where the module identity is otherwise unknown.
        try:
            _validate_slug(src.name, "entry-point name")
            provisional_candidate: str | None = src.name
        except (ValueError, Exception):
            provisional_candidate = None

        # --- Load ----------------------------------------------------------
        try:
            obj = ep.load()
            if callable(obj) and not isinstance(obj, CauldronModule):
                obj = obj()
        except Exception as exc:
            errors.append(_make_error(
                src, "load_failure",
                f"Entry point {src.name!r} raised {type(exc).__name__} on"
                " load.",
                candidate_slug=provisional_candidate,
            ))
            logger.debug(
                "Entry point %r failed to load: %s",
                src.name, exc,
                exc_info=True,
            )
            continue

        # --- Manifest validation -------------------------------------------
        validation_errors = _validate_manifest(
            src, obj, provisional_candidate=provisional_candidate,
        )
        if validation_errors:
            errors.extend(validation_errors)
            logger.debug("Entry point %r failed manifest validation.", src.name)
            continue

        # --- Duplicate slug -----------------------------------------------
        slug = obj.slug
        if slug in seen_slugs:
            accepted_ep_name, accepted_pkg_name = seen_slugs[slug]
            errors.append(_make_error(
                src, "duplicate_slug",
                f"Module slug {slug!r} registered by {src.name!r}"
                f" (package {src.display_package_name!r}) conflicts with"
                f" {accepted_ep_name!r}"
                f" (package {accepted_pkg_name!r}); duplicate is ignored.",
                candidate_slug=slug,
                accepted_entry_point_name=accepted_ep_name,
                accepted_package_name=accepted_pkg_name,
            ))
            continue

        seen_slugs[slug] = (src.name, src.display_package_name)
        record = DiscoveredModule(
            slug=slug,
            label=obj.label,
            version=obj.manifest.version,
            source_type="package",
            package_name=src.display_package_name,
            package_version=src.package_version,
            entry_point_group=src.group,
            entry_point_name=src.name,
            entry_point_value=src.value,
            manifest=obj.manifest,
            module=obj,
        )
        records.append(record)
        logger.debug(
            "Discovered module %r from entry point %r (package %r %s).",
            slug, src.name, src.display_package_name, src.package_version,
        )

    records.sort(key=lambda r: r.slug)
    return DiscoveryResult(records=records, errors=errors)


def get_module_apps(
    enabled: dict[str, Any] | list[str],
    *,
    capability_overrides: dict[str, str] | None = None,
    entry_point_group: str = ENTRY_POINT_GROUP,
    project_module_root: Path | None = None,
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

    result = discover_modules(
        entry_point_group=entry_point_group,
        project_module_root=project_module_root,
    )
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
