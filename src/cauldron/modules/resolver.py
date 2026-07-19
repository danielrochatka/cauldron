"""Dependency resolution and load-order determination for Cauldron modules."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from . import CauldronModule


class ErrorKind(Enum):
    MISSING_DEPENDENCY = "missing_dependency"
    MISSING_CAPABILITY = "missing_capability"
    VERSION_CONSTRAINT = "version_constraint"
    CAULDRON_VERSION = "cauldron_version"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    CAPABILITY_CONFLICT = "capability_conflict"


@dataclass
class ResolutionError:
    kind: ErrorKind
    module_slug: str
    message: str


@dataclass
class ResolutionWarning:
    module_slug: str
    message: str


@dataclass
class ResolutionResult:
    load_order: list[str]
    errors: list[ResolutionError] = field(default_factory=list)
    warnings: list[ResolutionWarning] = field(default_factory=list)
    dep_graph: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def resolve(
    modules: list[CauldronModule],
    capability_providers: dict[str, list[str]],
    *,
    cauldron_version: str = "",
    capability_overrides: dict[str, str] | None = None,
) -> ResolutionResult:
    """Validate constraints, detect dependency problems, return deterministic load order.

    *capability_overrides* maps a capability slug to the single module slug that
    should be used when multiple providers are present.  Set via
    ``CAULDRON_CAPABILITY_PROVIDERS`` in Django settings.
    """
    if capability_overrides is None:
        capability_overrides = {}

    errors: list[ResolutionError] = []
    warnings: list[ResolutionWarning] = []
    # Sort input for determinism — insertion order should not affect output.
    module_index: dict[str, CauldronModule] = {
        m.slug: m for m in sorted(modules, key=lambda m: m.slug)
    }

    if cauldron_version:
        for slug in sorted(module_index):
            module = module_index[slug]
            constraint = module.manifest.cauldron_version
            if constraint and not _version_satisfies(cauldron_version, constraint):
                errors.append(ResolutionError(
                    kind=ErrorKind.CAULDRON_VERSION,
                    module_slug=slug,
                    message=(
                        f"Module {slug!r} requires Cauldron {constraint!r} "
                        f"but {cauldron_version!r} is installed."
                    ),
                ))

    dep_graph: dict[str, list[str]] = {slug: [] for slug in module_index}

    for slug in sorted(module_index):
        module = module_index[slug]
        for req in module.manifest.requires:
            if req.kind == "module":
                if req.slug not in module_index:
                    errors.append(ResolutionError(
                        kind=ErrorKind.MISSING_DEPENDENCY,
                        module_slug=slug,
                        message=f"Module {slug!r} requires {req.slug!r} which is not installed or active.",
                    ))
                    continue
                dep = module_index[req.slug]
                if req.version and not _version_satisfies(dep.manifest.version, req.version):
                    errors.append(ResolutionError(
                        kind=ErrorKind.VERSION_CONSTRAINT,
                        module_slug=slug,
                        message=(
                            f"Module {slug!r} requires {req.slug!r} {req.version!r} "
                            f"but {dep.manifest.version!r} is installed."
                        ),
                    ))
                dep_graph[slug].append(req.slug)
            elif req.kind == "capability":
                providers = sorted(capability_providers.get(req.slug, []))
                if not providers:
                    errors.append(ResolutionError(
                        kind=ErrorKind.MISSING_CAPABILITY,
                        module_slug=slug,
                        message=(
                            f"Module {slug!r} requires capability {req.slug!r} "
                            "but no active module provides it."
                        ),
                    ))
                elif len(providers) == 1:
                    dep_graph[slug].extend(providers)
                else:
                    override = capability_overrides.get(req.slug)
                    if override and override in providers:
                        dep_graph[slug].append(override)
                    else:
                        providers_str = ", ".join(repr(p) for p in providers)
                        errors.append(ResolutionError(
                            kind=ErrorKind.CAPABILITY_CONFLICT,
                            module_slug=slug,
                            message=(
                                f"Module {slug!r} requires capability {req.slug!r} but"
                                f" multiple providers exist: [{providers_str}]."
                                " Set CAULDRON_CAPABILITY_PROVIDERS to resolve."
                            ),
                        ))

    for slug in sorted(module_index):
        module = module_index[slug]
        for req in module.manifest.optional:
            if req.kind == "module" and req.slug in module_index:
                dep = module_index[req.slug]
                if req.version and not _version_satisfies(dep.manifest.version, req.version):
                    warnings.append(ResolutionWarning(
                        module_slug=slug,
                        message=(
                            f"Module {slug!r} has optional dependency on {req.slug!r} {req.version!r} "
                            f"but {dep.manifest.version!r} is installed."
                        ),
                    ))
                dep_graph[slug].append(req.slug)
            elif req.kind == "capability":
                providers = sorted(capability_providers.get(req.slug, []))
                if len(providers) > 1:
                    override = capability_overrides.get(req.slug)
                    if override and override in providers:
                        dep_graph[slug].append(override)
                    else:
                        warnings.append(ResolutionWarning(
                            module_slug=slug,
                            message=(
                                f"Module {slug!r} has optional dependency on capability"
                                f" {req.slug!r} which has multiple providers:"
                                f" [{', '.join(repr(p) for p in providers)}]."
                                " Set CAULDRON_CAPABILITY_PROVIDERS to resolve."
                            ),
                        ))
                        dep_graph[slug].extend(providers)
                else:
                    dep_graph[slug].extend(providers)

    dep_graph = {slug: sorted(set(deps)) for slug, deps in dep_graph.items()}

    load_order, cycle_nodes = _topological_sort(dep_graph)

    for slug in sorted(cycle_nodes):
        errors.append(ResolutionError(
            kind=ErrorKind.CIRCULAR_DEPENDENCY,
            module_slug=slug,
            message=f"Module {slug!r} is part of a circular dependency.",
        ))

    return ResolutionResult(
        load_order=load_order,
        errors=errors,
        warnings=warnings,
        dep_graph=dep_graph,
    )


def _version_satisfies(version: str, constraint: str) -> bool:
    if not constraint:
        return True
    try:
        return Version(version) in SpecifierSet(constraint)
    except (InvalidVersion, InvalidSpecifier):
        return False


def _topological_sort(deps: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Kahn's algorithm with a min-heap queue for lexicographic determinism.

    Returns *(sorted_nodes, cycle_nodes)*.  Within each topological level,
    nodes are processed in alphabetical order so the output is stable
    regardless of input dict ordering.
    """
    dependents: dict[str, list[str]] = {n: [] for n in deps}
    in_degree: dict[str, int] = {n: 0 for n in deps}

    for node, node_deps in deps.items():
        for dep in node_deps:
            if dep in dependents:
                dependents[dep].append(node)
                in_degree[node] += 1

    heap: list[str] = [n for n, d in in_degree.items() if d == 0]
    heapq.heapify(heap)
    result: list[str] = []

    while heap:
        node = heapq.heappop(heap)
        result.append(node)
        for dependent in sorted(dependents[node]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                heapq.heappush(heap, dependent)

    processed = set(result)
    cycle_nodes = [n for n in sorted(deps) if n not in processed]
    return result, cycle_nodes
