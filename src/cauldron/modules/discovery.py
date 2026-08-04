"""Entry-point discovery for installed Cauldron modules."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from . import CauldronModule, ModuleManifest

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "cauldron.modules"

# Sentinel for missing attributes (distinguishes "not set" from None).
_MISSING = object()

# Directory names that are silently skipped during project-module scanning.
_IGNORED_DIR_NAMES: frozenset[str] = frozenset({
    "build", "dist",
})

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


def _resolve_safely(path: Path) -> Path | None:
    """Resolve *path* non-strictly; return ``None`` on any resolution failure.

    Catches :class:`OSError`, :class:`RuntimeError`, and :class:`ValueError`.
    Never exposes raw exception details in the return value.
    """
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _ep_top_level(value: str) -> str:
    """Extract top-level Python import name from an entry-point value field."""
    module_part = value.split(":")[0] if ":" in value else value
    top_level = module_part.split(".")[0] if "." in module_part else module_part
    return top_level


def _module_from_project(mod: Any, canonical_root: Path) -> bool:
    """Return True if mod's __file__ or __path__ entries are under canonical_root."""
    file_ = getattr(mod, "__file__", None)
    if file_ is not None:
        resolved = _resolve_safely(Path(file_))
        if resolved is not None and _is_under(resolved, canonical_root):
            return True
    path_ = getattr(mod, "__path__", None)
    if path_ is not None:
        for p in path_:
            resolved = _resolve_safely(Path(p))
            if resolved is not None and _is_under(resolved, canonical_root):
                return True
    return False


def _provisional_slug(name: str) -> str | None:
    """Return name if it is a valid module slug, else None."""
    from . import _validate_slug
    try:
        _validate_slug(name, "entry-point name")
        return name
    except Exception:
        return None


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
    kind: Literal["load_failure", "duplicate_slug", "manifest_validation", "project_path"],
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


def _is_under(path: Path, root: Path) -> bool:
    """Return True if path is root or is under root."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_candidate_tree(
    ep_name: str,
    candidate_dir: Path,
    canonical_root: Path,
) -> list[DiscoveryError]:
    """Validate the tree rooted at candidate_dir for symlink safety.

    Uses os.walk with followlinks=False. For each directory and file
    encountered, if it is a symlink, resolves it and verifies the resolved
    target is under canonical_root. Broken symlinks and resolve failures
    (OSError) are also rejected.

    Returns a list of DiscoveryError with kind="project_path".
    All messages use root-relative POSIX paths; no absolute paths are included.
    """
    tree_errors: list[DiscoveryError] = []
    walk_errors: list[str] = []

    def _onerror(exc: OSError) -> None:
        walk_errors.append(type(exc).__name__)

    for dirpath, dirnames, filenames in os.walk(str(candidate_dir), followlinks=False, onerror=_onerror):
        # Check and prune symlinked directories
        dirs_to_remove = []
        for dname in list(dirnames):
            dpath = Path(dirpath) / dname
            if os.path.islink(str(dpath)):
                rel = (Path(dirpath) / dname).relative_to(candidate_dir).as_posix()
                resolved = _resolve_safely(dpath)
                if resolved is None:
                    tree_errors.append(DiscoveryError(
                        entry_point_name=ep_name,
                        kind="project_path",
                        message=(
                            f"Project module tree contains unresolvable directory"
                            f" symlink {rel!r}."
                        ),
                    ))
                    dirs_to_remove.append(dname)
                    continue
                if not resolved.exists():
                    tree_errors.append(DiscoveryError(
                        entry_point_name=ep_name,
                        kind="project_path",
                        message=(
                            f"Project module tree contains broken symlink"
                            f" {rel!r}."
                        ),
                    ))
                    dirs_to_remove.append(dname)
                    continue
                if not _is_under(resolved, canonical_root):
                    tree_errors.append(DiscoveryError(
                        entry_point_name=ep_name,
                        kind="project_path",
                        message=(
                            f"Project module tree contains directory symlink"
                            f" {rel!r} that resolves outside the project root."
                        ),
                    ))
                    dirs_to_remove.append(dname)
                    continue
                # Safe internal dir symlink: remove from traversal to avoid loops
                dirs_to_remove.append(dname)
            # Non-symlink dirs are traversed normally (os.walk handles them)

        # Remove symlinked dirs from traversal list (in-place modification)
        for dname in dirs_to_remove:
            if dname in dirnames:
                dirnames.remove(dname)

        # Check file symlinks
        for fname in filenames:
            fpath = Path(dirpath) / fname
            if os.path.islink(str(fpath)):
                rel = (Path(dirpath) / fname).relative_to(candidate_dir).as_posix()
                resolved = _resolve_safely(fpath)
                if resolved is None:
                    tree_errors.append(DiscoveryError(
                        entry_point_name=ep_name,
                        kind="project_path",
                        message=(
                            f"Project module tree contains unresolvable file"
                            f" symlink {rel!r}."
                        ),
                    ))
                    continue
                if not resolved.exists():
                    tree_errors.append(DiscoveryError(
                        entry_point_name=ep_name,
                        kind="project_path",
                        message=(
                            f"Project module tree contains broken symlink"
                            f" {rel!r}."
                        ),
                    ))
                    continue
                if not _is_under(resolved, canonical_root):
                    tree_errors.append(DiscoveryError(
                        entry_point_name=ep_name,
                        kind="project_path",
                        message=(
                            f"Project module tree contains file symlink"
                            f" {rel!r} that resolves outside the project root."
                        ),
                    ))
                    continue

    for _ in walk_errors:
        tree_errors.append(DiscoveryError(
            entry_point_name=ep_name,
            kind="project_path",
            message="Project module tree contains an unreadable or unscannable directory.",
        ))

    return tree_errors


def _normalize_project_root(
    value: Any,
) -> tuple[Path | None, list[DiscoveryError]]:
    """Normalize a raw CAULDRON_PROJECT_MODULE_ROOT value to a Path.

    Returns ``(path, [])`` on success, ``(None, [error])`` on failure.
    Does NOT check whether the path exists — that is ``_discover_project_modules``'s job.
    """
    if value is None:
        return None, []

    # bool is a subclass of int; check it first.
    if isinstance(value, bool):
        return None, [DiscoveryError(
            entry_point_name="",
            kind="project_path",
            message=(
                "CAULDRON_PROJECT_MODULE_ROOT must be a path string or Path object,"
                f" got {type(value).__name__!r}."
            ),
        )]

    if isinstance(value, int):
        return None, [DiscoveryError(
            entry_point_name="",
            kind="project_path",
            message=(
                "CAULDRON_PROJECT_MODULE_ROOT must be a path string or Path object,"
                f" got {type(value).__name__!r}."
            ),
        )]

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None, [DiscoveryError(
                entry_point_name="",
                kind="project_path",
                message=(
                    "CAULDRON_PROJECT_MODULE_ROOT must not be an empty or"
                    " whitespace-only string."
                ),
            )]
        return Path(stripped), []

    if isinstance(value, os.PathLike):
        try:
            raw_str = os.fspath(value)
        except Exception:
            return None, [DiscoveryError(
                entry_point_name="",
                kind="project_path",
                message=(
                    "CAULDRON_PROJECT_MODULE_ROOT: PathLike.__fspath__() raised;"
                    " a valid path string is required."
                ),
            )]
        if isinstance(raw_str, bytes):
            return None, [DiscoveryError(
                entry_point_name="",
                kind="project_path",
                message=(
                    "CAULDRON_PROJECT_MODULE_ROOT: PathLike returned bytes from"
                    " __fspath__(); a str path is required."
                ),
            )]
        if not isinstance(raw_str, str):
            return None, [DiscoveryError(
                entry_point_name="",
                kind="project_path",
                message=(
                    "CAULDRON_PROJECT_MODULE_ROOT: PathLike returned"
                    f" {type(raw_str).__name__!r} from __fspath__();"
                    " a str path is required."
                ),
            )]
        stripped = raw_str.strip()
        if not stripped:
            return None, [DiscoveryError(
                entry_point_name="",
                kind="project_path",
                message=(
                    "CAULDRON_PROJECT_MODULE_ROOT must not be empty or"
                    " whitespace-only."
                ),
            )]
        try:
            return Path(stripped), []
        except Exception:
            return None, [DiscoveryError(
                entry_point_name="",
                kind="project_path",
                message=(
                    "CAULDRON_PROJECT_MODULE_ROOT: Path() construction failed;"
                    " ensure the value is a valid filesystem path."
                ),
            )]

    return None, [DiscoveryError(
        entry_point_name="",
        kind="project_path",
        message=(
            "CAULDRON_PROJECT_MODULE_ROOT must be a path string or Path object,"
            f" got {type(value).__name__!r}."
        ),
    )]


def _place_at_sys_path_front(canonical_str: str) -> None:
    """Ensure canonical_str occupies sys.path[0], removing equivalents."""
    kept = []
    for entry in sys.path:
        if not isinstance(entry, str):
            kept.append(entry)
            continue
        resolved_path = _resolve_safely(Path(entry))
        if resolved_path is None:
            kept.append(entry)
            continue
        if str(resolved_path) != canonical_str:
            kept.append(entry)
        # else: drop equivalent duplicate
    sys.path[:] = [canonical_str] + kept


def _discover_project_modules(
    root: Path,
    *,
    seen_slugs: dict[str, tuple[str, str]],
    candidate_names: list[str],
    canonical_root: Path,
) -> tuple[list[DiscoveredModule], list[DiscoveryError]]:
    """Import and register pre-enumerated project-module candidates.

    *candidate_names* (from :func:`_enumerate_candidates`) and *canonical_root*
    are required.  All static path checks are already done; this function only
    imports and validates the module attribute.

    The project root must already be at the front of ``sys.path`` when called.
    """
    records: list[DiscoveredModule] = []
    errors: list[DiscoveryError] = []

    def _module_origin_ok(mod_obj: Any, expected_dir: Path) -> bool:
        """Check that an imported module's actual location is inside expected_dir."""
        file_ = getattr(mod_obj, "__file__", None)
        if file_ is not None:
            resolved = _resolve_safely(Path(file_))
            if resolved is None:
                return False
            return _is_under(resolved, expected_dir)
        # Package with __path__ but no __file__ (namespace package)
        path_ = getattr(mod_obj, "__path__", None)
        if path_ is not None:
            for p in path_:
                resolved = _resolve_safely(Path(p))
                if resolved is not None and _is_under(resolved, expected_dir):
                    return True
            return False
        return False

    # --- Import each validated candidate ------------------------------------
    for dir_name in candidate_names:
        ep_name = f"modules/{dir_name}"
        src = _ProjectSource(name=ep_name, value=dir_name)
        entry = root / dir_name
        resolved_entry = _resolve_safely(entry)
        if resolved_entry is None:
            # Should not happen since _enumerate_candidates checked this
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="project_path",
                message=(
                    f"Project module directory {dir_name!r} could not be"
                    f" resolved."
                ),
            ))
            continue

        # --- sys.modules collision check -----------------------------------
        existing = sys.modules.get(dir_name)
        if existing is not None:
            if not _module_origin_ok(existing, resolved_entry):
                errors.append(DiscoveryError(
                    entry_point_name=ep_name,
                    kind="project_path",
                    message=(
                        f"Project module {dir_name!r} is already imported"
                        " from a different location; skipping to avoid"
                        " collision."
                    ),
                ))
                continue
            # Already imported from the correct place — reuse it.
            mod_obj = existing
        else:
            # --- Load ------------------------------------------------------
            # Snapshot keys for this package before importing so we can clean
            # up on failure.
            modules_before = set(
                k for k in sys.modules
                if k == dir_name or k.startswith(dir_name + ".")
            )
            importlib.invalidate_caches()
            try:
                mod_obj = importlib.import_module(dir_name)
            except Exception as exc:
                # Remove any partially-loaded entries for this package
                for key in list(sys.modules.keys()):
                    if (key == dir_name or key.startswith(dir_name + ".")) and key not in modules_before:
                        del sys.modules[key]
                importlib.invalidate_caches()
                errors.append(_make_error(
                    src, "load_failure",
                    f"Project module {ep_name!r} raised {type(exc).__name__}"
                    " on import.",
                ))
                logger.debug(
                    "Project module %r failed to import: %s",
                    ep_name, exc,
                    exc_info=True,
                )
                continue

            # Verify the newly imported module came from the expected place.
            if not _module_origin_ok(mod_obj, resolved_entry):
                # Remove newly added entries for this package
                for key in list(sys.modules.keys()):
                    if (key == dir_name or key.startswith(dir_name + ".")) and key not in modules_before:
                        del sys.modules[key]
                importlib.invalidate_caches()
                errors.append(DiscoveryError(
                    entry_point_name=ep_name,
                    kind="project_path",
                    message=(
                        f"Project module {dir_name!r} was imported but its"
                        " actual location differs from the expected directory;"
                        " skipping to avoid collision."
                    ),
                ))
                continue

        # --- Access 'module' attribute with guard --------------------------
        try:
            raw_obj = getattr(mod_obj, "module", _MISSING)
        except Exception as exc:
            errors.append(_make_error(
                src, "load_failure",
                f"Project module {ep_name!r}: accessing 'module' attribute"
                f" raised {type(exc).__name__}.",
            ))
            continue

        if raw_obj is _MISSING:
            errors.append(_make_error(
                src, "load_failure",
                f"Project module {ep_name!r}: package {dir_name!r} has"
                " no 'module' attribute.",
            ))
            continue

        # --- Callable factory handling (same as entry points) --------------
        from . import CauldronModule as _CauldronModule
        obj = raw_obj
        if callable(obj) and not isinstance(obj, _CauldronModule):
            try:
                obj = obj()
            except Exception as exc:
                errors.append(_make_error(
                    src, "load_failure",
                    f"Project module {ep_name!r}: calling module factory"
                    f" raised {type(exc).__name__}.",
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
            errors.append(_make_error(
                src, "duplicate_slug",
                f"Module slug {slug!r} registered by project module"
                f" {ep_name!r} conflicts with {accepted_ep_name!r}"
                f"{pkg_note}; duplicate is ignored.",
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


def _enumerate_candidates(
    root: Path,
    canonical_root: Path,
) -> tuple[list[str], list[DiscoveryError]]:
    """Enumerate valid candidate directory names under *root* WITHOUT importing anything.

    Performs all static checks: identifier validity, ``__init__.py`` existence,
    path containment, ``__init__.py`` is a regular file, and tree validation.

    Returns ``(valid_names, errors)`` where *valid_names* is a list of directory
    name strings that passed all checks, and *errors* collects structured errors
    for each candidate that failed.

    Does NOT add root to ``sys.path`` or import any module.
    """
    valid_names: list[str] = []
    errors: list[DiscoveryError] = []

    try:
        all_entries = sorted(root.iterdir(), key=lambda e: e.name)
    except OSError as exc:
        errors.append(DiscoveryError(
            entry_point_name="",
            kind="project_path",
            message=(
                f"CAULDRON_PROJECT_MODULE_ROOT could not be scanned:"
                f" {type(exc).__name__}."
            ),
        ))
        return valid_names, errors

    for entry in all_entries:
        dir_name = entry.name

        # --- Silently ignore hidden, dunder, build/dist, and egg-info dirs ---
        if not entry.is_dir():
            continue
        if dir_name.startswith("."):
            continue
        if dir_name.startswith("__") and dir_name.endswith("__"):
            continue
        if dir_name in _IGNORED_DIR_NAMES:
            continue
        if dir_name.endswith(".egg-info"):
            continue

        # ---- All remaining directory entries are "visible candidates" -------
        ep_name = f"modules/{dir_name}"

        # 1. Invalid identifier
        if not dir_name.isidentifier():
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="project_path",
                message=(
                    f"Project module directory {dir_name!r} is not a valid"
                    " Python identifier."
                ),
            ))
            continue

        # 2. Missing __init__.py
        if not (entry / "__init__.py").exists():
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="project_path",
                message=(
                    f"Project module directory {dir_name!r} has no __init__.py."
                ),
            ))
            continue

        # 3. Candidate path escapes root
        resolved_entry = _resolve_safely(entry)
        if resolved_entry is None:
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="project_path",
                message=(
                    f"Project module directory {dir_name!r} could not be"
                    f" resolved."
                ),
            ))
            continue

        if not _is_under(resolved_entry, canonical_root):
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="project_path",
                message=(
                    f"Project module directory {dir_name!r} resolves outside"
                    " the project root."
                ),
            ))
            continue

        # 4. __init__.py escapes root
        resolved_init = _resolve_safely(entry / "__init__.py")
        if resolved_init is None:
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="project_path",
                message=(
                    f"Project module {dir_name!r}: __init__.py could not be"
                    f" resolved."
                ),
            ))
            continue

        if not _is_under(resolved_init, canonical_root):
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="project_path",
                message=(
                    f"Project module {dir_name!r}: __init__.py resolves"
                    " outside the project root."
                ),
            ))
            continue

        # 4b. __init__.py must be a regular file (not a directory)
        if resolved_init.is_dir() or not resolved_init.is_file():
            errors.append(DiscoveryError(
                entry_point_name=ep_name,
                kind="project_path",
                message=(
                    f"Project module {dir_name!r}: __init__.py is not a regular file."
                ),
            ))
            continue

        # 5. Tree validation: check all nested symlinks for path escapes
        tree_errors = _validate_candidate_tree(ep_name, entry, canonical_root)
        if tree_errors:
            errors.extend(tree_errors)
            continue

        valid_names.append(dir_name)

    return valid_names, errors


def discover_modules(
    *,
    entry_point_group: str = ENTRY_POINT_GROUP,
    project_module_root: str | os.PathLike[str] | None = None,
) -> DiscoveryResult:
    """Discover installed Cauldron modules via Python entry points.

    Returns a :class:`DiscoveryResult` containing successfully discovered
    modules (as :class:`DiscoveredModule` records) and structured errors for
    any entry point that could not be loaded, failed manifest validation, or
    registered a duplicate slug.

    When *project_module_root* is provided, project-folder modules are
    discovered first (lexical order by directory name) and merged with
    entry-point modules.  Project modules win duplicate-slug races.

    Phase separation guarantees that EP loading cannot accidentally import a
    project directory with the same top-level import name: the project root is
    temporarily removed from ``sys.path`` during EP loading and added back
    only for the project import phase.

    Discovery is deterministic: combined records are sorted by slug.
    Entry-point metadata is resolved exactly once per entry point.
    """
    from . import CauldronModule

    records: list[DiscoveredModule] = []
    errors: list[DiscoveryError] = []
    # slug → (accepted_ep_name, accepted_pkg_name); shared across both passes.
    seen_slugs: dict[str, tuple[str, str]] = {}

    # --- Phase 1: Normalize project root and enumerate candidates ------------
    norm_root: Path | None = None
    canonical_root: Path | None = None
    canonical_root_str: str | None = None
    candidate_names: list[str] = []

    if project_module_root is not None:
        norm_root, norm_errors = _normalize_project_root(project_module_root)
        errors.extend(norm_errors)
        if norm_root is not None:
            if not norm_root.is_dir():
                errors.append(DiscoveryError(
                    entry_point_name="",
                    kind="project_path",
                    message=(
                        "CAULDRON_PROJECT_MODULE_ROOT does not exist or is not a"
                        " directory."
                    ),
                ))
                norm_root = None
            else:
                canonical_root = _resolve_safely(norm_root)
                if canonical_root is None:
                    errors.append(DiscoveryError(
                        entry_point_name="",
                        kind="project_path",
                        message=(
                            "CAULDRON_PROJECT_MODULE_ROOT could not be resolved."
                        ),
                    ))
                    norm_root = None
                else:
                    canonical_root_str = str(canonical_root)
                    # Enumerate candidates WITHOUT importing or modifying sys.path
                    candidate_names, enum_errors = _enumerate_candidates(
                        norm_root, canonical_root,
                    )
                    errors.extend(enum_errors)

    # Initialize EP structures before try so they're accessible in finally.
    ep_pairs: list[tuple[Any, _EntryPointSource]] = []
    ep_import_names: set[str] = set()
    ep_candidates: list[tuple[str, _EntryPointSource, Any]] = []
    ep_errors: list[DiscoveryError] = []

    # --- Phase 3: Load EPs with project root temporarily removed and
    #     sys.modules cache isolation for project-origin entries --------------
    # Snapshot original sys.path before ANY modification
    original_sys_path = list(sys.path)

    try:
        # Remove project-root entries (single resolve per entry, no double-call).
        if canonical_root_str is not None:
            clean_path: list[str] = []
            for e in sys.path:
                if isinstance(e, str):
                    resolved = _resolve_safely(Path(e))
                    if resolved is not None and str(resolved) == canonical_root_str:
                        continue
                clean_path.append(e)
            sys.path[:] = clean_path

        # --- Phase 2 (moved inside): Build EP import-name set ------------------
        # Called after project root removed so local .dist-info cannot be seen.
        raw_eps = entry_points(group=entry_point_group)
        ep_pairs = [(ep, _source_for_ep(ep, entry_point_group)) for ep in raw_eps]
        ep_pairs.sort(key=lambda pair: (
            pair[1].name,
            pair[1].canonical_package_name,
            pair[1].value,
        ))
        ep_import_names = {
            top
            for _ep, src in ep_pairs
            for top in (_ep_top_level(src.value),) if top
        }

        # Build surviving candidates: reject import-name collisions
        # and stdlib/installed package collisions (with root absent).
        surviving_candidates: list[str] = []
        if canonical_root is not None:
            for dir_name in candidate_names:
                ep_name = f"modules/{dir_name}"
                if dir_name in ep_import_names:
                    # EP explicitly targets this import name
                    colliding_ep_name = next(
                        (src.name for _ep, src in ep_pairs
                         if _ep_top_level(src.value) == dir_name),
                        "unknown",
                    )
                    errors.append(DiscoveryError(
                        entry_point_name=ep_name,
                        kind="project_path",
                        message=(
                            f"Project module directory {dir_name!r} has the same"
                            f" top-level import name as installed entry point"
                            f" {colliding_ep_name!r}; the installed package is"
                            f" authoritative."
                        ),
                    ))
                else:
                    # Check for stdlib/installed collision while root is absent.
                    # Evict project-origin cached modules for this prefix so
                    # find_spec() sees installed packages rather than the cached
                    # project module.
                    saved_for_spec: dict[str, Any] = {}
                    if canonical_root is not None:
                        for key in list(sys.modules.keys()):
                            if key == dir_name or key.startswith(dir_name + "."):
                                mod = sys.modules[key]
                                if _module_from_project(mod, canonical_root):
                                    saved_for_spec[key] = mod
                                    del sys.modules[key]
                    if saved_for_spec:
                        importlib.invalidate_caches()
                    try:
                        spec = importlib.util.find_spec(dir_name)
                    except (ModuleNotFoundError, ValueError):
                        spec = None
                    finally:
                        for key, mod in saved_for_spec.items():
                            if key not in sys.modules:
                                sys.modules[key] = mod
                        if saved_for_spec:
                            importlib.invalidate_caches()

                    if spec is not None:
                        errors.append(DiscoveryError(
                            entry_point_name=ep_name,
                            kind="project_path",
                            message=(
                                f"Project module directory {dir_name!r} shadows an"
                                f" importable package or module; the installed"
                                f" package is authoritative."
                            ),
                        ))
                    else:
                        surviving_candidates.append(dir_name)
        else:
            surviving_candidates = list(candidate_names)
        candidate_names = surviving_candidates

        # Load each EP with sys.modules cache management for project-origin entries
        for ep, src in ep_pairs:
            top_level = _ep_top_level(src.value)
            provisional_candidate = _provisional_slug(src.name)

            # Snapshot ALL sys.modules entries for this package prefix.
            # project_before: project-origin entries to restore on failure.
            all_before: dict[str, Any] = {}
            project_before: dict[str, Any] = {}
            if top_level:
                for key in list(sys.modules.keys()):
                    if key == top_level or key.startswith(top_level + "."):
                        mod = sys.modules[key]
                        all_before[key] = mod
                        if canonical_root is not None and _module_from_project(mod, canonical_root):
                            project_before[key] = mod

            # Evict project-origin entries so EP loads from installed package.
            for key in project_before:
                del sys.modules[key]

            load_exc: Exception | None = None
            try:
                obj = ep.load()
                if callable(obj) and not isinstance(obj, CauldronModule):
                    obj = obj()
            except Exception as exc:
                load_exc = exc

            if load_exc is not None:
                # Full cleanup: remove ALL currently installed/partial prefix entries,
                # then restore project-origin exactly (by identity).
                if top_level:
                    for key in list(sys.modules.keys()):
                        if key == top_level or key.startswith(top_level + "."):
                            del sys.modules[key]
                for key, mod in project_before.items():
                    sys.modules[key] = mod
                importlib.invalidate_caches()
                ep_errors.append(_make_error(
                    src, "load_failure",
                    f"Entry point {src.name!r} raised {type(load_exc).__name__} on load.",
                    candidate_slug=provisional_candidate,
                ))
                logger.debug("Entry point %r failed to load: %s", src.name, load_exc)
                continue
            # On success: keep installed package state.

            validation_errors = _validate_manifest(src, obj, provisional_candidate=provisional_candidate)
            if validation_errors:
                ep_errors.extend(validation_errors)
                logger.debug("Entry point %r failed manifest validation.", src.name)
                continue

            # Collect ALL validated EPs; defer slug deduplication to Phase 5
            ep_candidates.append((obj.slug, src, obj))

    finally:
        # ALWAYS restore original sys.path exactly
        sys.path[:] = original_sys_path

    # --- Phase 4: Project imports with sys.path temporarily augmented --------
    if norm_root is not None and canonical_root is not None and canonical_root_str is not None:
        path_before_project_import = list(sys.path)
        try:
            _place_at_sys_path_front(canonical_root_str)
            proj_records, proj_errors = _discover_project_modules(
                norm_root,
                seen_slugs=seen_slugs,
                candidate_names=candidate_names,
                canonical_root=canonical_root,
            )
            records.extend(proj_records)
            errors.extend(proj_errors)
        finally:
            # Restore sys.path exactly (project root stays in sys.modules for
            # usability, but the path manipulation is reversed)
            sys.path[:] = path_before_project_import

    # --- Phase 5: Final deterministic merge — all slug resolution in one pass -
    # seen_slugs is already seeded with project module registrations from Phase 4.
    errors.extend(ep_errors)

    # Sort EP candidates by (name, canonical_package_name, value) for determinism.
    ep_candidates.sort(key=lambda t: (t[1].name, t[1].canonical_package_name, t[1].value))

    for ep_slug, src, obj in ep_candidates:
        if ep_slug in seen_slugs:
            accepted_ep_name, accepted_pkg_name = seen_slugs[ep_slug]
            accepted_pkg_note = f" (package {accepted_pkg_name!r})" if accepted_pkg_name else ""
            errors.append(DiscoveryError(
                entry_point_name=src.name,
                kind="duplicate_slug",
                message=(
                    f"Module slug {ep_slug!r} registered by {src.name!r}"
                    f" (package {src.display_package_name!r}) conflicts with"
                    f" {accepted_ep_name!r}{accepted_pkg_note}; duplicate is ignored."
                ),
                entry_point_group=src.group,
                entry_point_value=src.value,
                package_name=src.display_package_name,
                package_version=src.package_version,
                candidate_slug=ep_slug,
                accepted_entry_point_name=accepted_ep_name,
                accepted_package_name=accepted_pkg_name,
            ))
        else:
            seen_slugs[ep_slug] = (src.name, src.display_package_name)
            record = DiscoveredModule(
                slug=ep_slug,
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
                ep_slug, src.name, src.display_package_name, src.package_version,
            )

    records.sort(key=lambda r: r.slug)
    return DiscoveryResult(records=records, errors=errors)


def get_module_apps(
    enabled: dict[str, Any] | list[str],
    *,
    capability_overrides: dict[str, str] | None = None,
    entry_point_group: str = ENTRY_POINT_GROUP,
    project_module_root: str | os.PathLike[str] | None = None,
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
