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
    BLOCKED_DEPENDENCY = "blocked_dependency"


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
            if constraint and not version_satisfies(cauldron_version, constraint):
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
                if req.version and not version_satisfies(dep.manifest.version, req.version):
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
                if req.version and not version_satisfies(dep.manifest.version, req.version):
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

    load_order, cycle_nodes, blocked_nodes = _topological_sort(dep_graph)

    for slug in sorted(cycle_nodes):
        errors.append(ResolutionError(
            kind=ErrorKind.CIRCULAR_DEPENDENCY,
            module_slug=slug,
            message=f"Module {slug!r} is part of a circular dependency.",
        ))

    for slug in sorted(blocked_nodes):
        direct_deps = dep_graph.get(slug, [])
        cycle_blockers = sorted(d for d in direct_deps if d in cycle_nodes)
        blocked_blockers = sorted(d for d in direct_deps if d in blocked_nodes)

        parts: list[str] = []
        if cycle_blockers:
            cbs = ", ".join(repr(b) for b in cycle_blockers)
            parts.append(f"{cbs} (part of a circular dependency)")
        if blocked_blockers:
            bbs = ", ".join(repr(b) for b in blocked_blockers)
            parts.append(f"{bbs} (blocked by a circular dependency)")
        blocker_desc = " and ".join(parts) if parts else "a cyclic module"
        errors.append(ResolutionError(
            kind=ErrorKind.BLOCKED_DEPENDENCY,
            module_slug=slug,
            message=f"Module {slug!r} cannot be loaded because it depends on {blocker_desc}.",
        ))

    return ResolutionResult(
        load_order=load_order,
        errors=errors,
        warnings=warnings,
        dep_graph=dep_graph,
    )


def version_satisfies(version: str, constraint: str) -> bool:
    if not constraint:
        return True
    try:
        return Version(version) in SpecifierSet(constraint)
    except (InvalidVersion, InvalidSpecifier):
        return False


def _find_sccs(deps: dict[str, list[str]]) -> list[set[str]]:
    """Tarjan's strongly-connected-components algorithm.

    Returns a list of SCCs, each represented as a set of node names.
    Only SCCs with more than one node (or a self-loop) represent actual
    cycles — single-node SCCs without a self-loop are acyclic.
    """
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    sccs: list[set[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        for w in deps.get(v, []):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])

        if lowlink[v] == index[v]:
            scc: set[str] = set()
            while True:
                w = stack.pop()
                on_stack.discard(w)
                scc.add(w)
                if w == v:
                    break
            sccs.append(scc)

    for node in sorted(deps):
        if node not in index:
            strongconnect(node)

    return sccs


def _topological_sort(
    deps: dict[str, list[str]],
) -> tuple[list[str], set[str], set[str]]:
    """Kahn's algorithm with a min-heap queue for lexicographic determinism.

    Returns *(sorted_nodes, cycle_nodes, blocked_nodes)*.

    *cycle_nodes* — nodes that are themselves part of a cycle (SCC size > 1
    or self-loop).  These receive ``CIRCULAR_DEPENDENCY`` / ``cauldron.E014``.

    *blocked_nodes* — nodes that are not in a cycle themselves but cannot be
    scheduled because they transitively depend on a cycle node.  These receive
    ``BLOCKED_DEPENDENCY`` / ``cauldron.E016``.

    Within each topological level, nodes are processed in alphabetical order
    so the output is stable regardless of input dict ordering.
    """
    # Identify true cycle participants via Tarjan's SCC.
    sccs = _find_sccs(deps)
    cycle_nodes: set[str] = set()
    for scc in sccs:
        if len(scc) > 1:
            cycle_nodes.update(scc)
        else:
            # Single-node SCC — cycle only if the node has a self-loop.
            (node,) = scc
            if node in deps.get(node, []):
                cycle_nodes.add(node)

    # Kahn's on the full graph to get the load order for non-cyclic nodes.
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

    # Nodes not in the load_order and not in cycle_nodes are blocked.
    processed = set(result)
    blocked_nodes: set[str] = {
        n for n in deps if n not in processed and n not in cycle_nodes
    }

    return result, cycle_nodes, blocked_nodes
