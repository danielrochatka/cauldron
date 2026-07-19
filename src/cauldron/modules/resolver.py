"""Dependency resolution and load-order determination for Cauldron modules."""

from __future__ import annotations

from collections import deque
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
) -> ResolutionResult:
    """Validate constraints, detect dependency problems, return topological load order."""
    errors: list[ResolutionError] = []
    warnings: list[ResolutionWarning] = []
    module_index: dict[str, CauldronModule] = {m.slug: m for m in modules}

    if cauldron_version:
        for slug, module in module_index.items():
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

    for slug, module in module_index.items():
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
                providers = capability_providers.get(req.slug, [])
                if not providers:
                    errors.append(ResolutionError(
                        kind=ErrorKind.MISSING_CAPABILITY,
                        module_slug=slug,
                        message=(
                            f"Module {slug!r} requires capability {req.slug!r} "
                            "but no active module provides it."
                        ),
                    ))
                else:
                    dep_graph[slug].extend(providers)

    for slug, module in module_index.items():
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
                providers = capability_providers.get(req.slug, [])
                dep_graph[slug].extend(providers)

    dep_graph = {slug: sorted(set(deps)) for slug, deps in dep_graph.items()}

    load_order, cycle_nodes = _topological_sort(dep_graph)

    for slug in cycle_nodes:
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
    """Kahn's algorithm. Returns (sorted_nodes, cycle_nodes)."""
    dependents: dict[str, list[str]] = {n: [] for n in deps}
    in_degree: dict[str, int] = {n: 0 for n in deps}

    for node, node_deps in deps.items():
        for dep in node_deps:
            if dep in dependents:
                dependents[dep].append(node)
                in_degree[node] += 1

    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    result: list[str] = []

    while queue:
        node = queue.popleft()
        result.append(node)
        for dependent in dependents[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    processed = set(result)
    cycle_nodes = [n for n in deps if n not in processed]
    return result, cycle_nodes
