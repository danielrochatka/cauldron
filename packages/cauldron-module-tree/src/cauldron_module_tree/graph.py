"""Build a production-quality module dependency graph from the Cauldron module registry."""
from __future__ import annotations

import datetime
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from cauldron.modules import ModuleManifest

from cauldron_module_tree.colors import slug_color
from cauldron_module_tree.sanitize import safe_svg_or_fallback


# --------------------------------------------------------------------------- #
# Value objects                                                                #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ModuleGraphNode:
    slug: str
    title: str
    summary: str
    version: str
    state: str
    enabled: bool
    active: bool
    configured_enabled: bool   # desired state from the persisted overlay
    pending_restart: bool
    runtime_enabled: bool      # same as active
    requires_restart: bool
    icon_svg: str              # sanitized
    visual_color: str          # deterministic hex color from slug
    group: str
    display_order: int
    source_type: str | None
    source: str
    provides: tuple[str, ...]
    errors: tuple[dict, ...]

    # Legacy fields preserved for backwards-compat with existing tests/API
    documentation_url: str = ""
    parents: tuple[str, ...] = ()
    children: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleGraphEdge:
    source: str
    target: str
    kind: str                       # "required" | "optional" | "capability" | "requires" | "used_by"
    capability: str | None
    status: str                     # "resolved" | "missing" | "blocked" | "conflict" | "cycle"
    relationship_kind: str | None = None  # original edge kind for focused-graph edges


# --------------------------------------------------------------------------- #
# FocusedModuleGraph                                                           #
# --------------------------------------------------------------------------- #

@dataclass
class FocusedModuleGraph:
    """Focused subgraph around a selected module.

    Contains the selected module, its direct requirements (one hop forward),
    and all modules that transitively depend on it (used-by closure).

    Roles
    -----
    - ``"selected"`` — the module the user chose
    - ``"requires"`` — direct requirement of selected (one hop forward, shown faded above)
    - ``"used_by"``  — transitive dependent of selected (full reverse closure, shown below)
    """

    selected_slug: str
    nodes: dict[str, ModuleGraphNode]       # slug → node for focused set
    edges: tuple[ModuleGraphEdge, ...]      # requires edges + used_by edges
    roles: dict[str, str]                   # slug → "selected"|"requires"|"used_by"
    missing_targets: frozenset[str] = field(default_factory=frozenset)  # unregistered req slugs
    requires_list: tuple[dict, ...] = field(default_factory=tuple)  # [{slug, name}, ...]
    used_by_list: tuple[dict, ...] = field(default_factory=tuple)   # [{slug, name}, ...]

    def to_api_dict(self) -> dict[str, Any]:
        """Serialize for JSON API / frontend consumption."""
        nodes_list = []
        for slug, node in sorted(self.nodes.items()):
            nodes_list.append({
                "slug": node.slug,
                "title": node.title,
                "summary": node.summary,
                "version": node.version,
                "state": node.state,
                "enabled": node.enabled,
                "active": node.active,
                "configured_enabled": node.configured_enabled,
                "pending_restart": node.pending_restart,
                "runtime_enabled": node.runtime_enabled,
                "requires_restart": node.requires_restart,
                "icon_svg": node.icon_svg,
                "visual_color": node.visual_color,
                "group": node.group,
                "display_order": node.display_order,
                "documentation_url": node.documentation_url,
                "source_type": node.source_type,
                "source": node.source,
                "provides": list(node.provides),
                "errors": list(node.errors),
                "focus_role": self.roles.get(slug, "used_by"),
            })
        # Synthetic terminal nodes for unregistered direct requirement targets —
        # serialized in sorted order so output is deterministic.
        for missing_slug in sorted(self.missing_targets):
            nodes_list.append({
                "slug": missing_slug,
                "title": f"Missing: {missing_slug}",
                "summary": "",
                "version": "",
                "state": "missing",
                "enabled": False,
                "active": False,
                "configured_enabled": False,
                "pending_restart": False,
                "runtime_enabled": False,
                "requires_restart": False,
                "icon_svg": None,
                "visual_color": "#9ca3af",
                "group": "",
                "display_order": 0,
                "documentation_url": "",
                "source_type": None,
                "source": "",
                "provides": [],
                "errors": [],
                "focus_role": "requires",
                "is_synthetic": True,
            })

        edges_list = []
        for e in self.edges:
            entry: dict[str, Any] = {
                "source": e.source,
                "target": e.target,
                "kind": e.kind,
                "capability": e.capability,
                "status": e.status,
            }
            if e.relationship_kind:
                entry["relationship_kind"] = e.relationship_kind
            edges_list.append(entry)

        requires_count = sum(1 for r in self.roles.values() if r == "requires")
        used_by_count = sum(1 for r in self.roles.values() if r == "used_by")

        return {
            "nodes": nodes_list,
            "edges": edges_list,
            "metadata": {
                "selected_slug": self.selected_slug,
                "requires_count": requires_count + len(self.missing_targets),
                "used_by_count": used_by_count,
                "missing_count": len(self.missing_targets),
                "requires_list": list(self.requires_list),
                "used_by_list": list(self.used_by_list),
            },
        }


# --------------------------------------------------------------------------- #
# ModuleGraph                                                                  #
# --------------------------------------------------------------------------- #

class ModuleGraph:
    """Immutable, queryable graph of module dependencies."""

    def __init__(
        self,
        nodes: Iterable[ModuleGraphNode],
        edges: Iterable[ModuleGraphEdge],
    ) -> None:
        self._nodes: dict[str, ModuleGraphNode] = {n.slug: n for n in nodes}
        self._edges: tuple[ModuleGraphEdge, ...] = tuple(edges)

        # Adjacency indexes
        # required + capability edges (for transitive impact)
        self._fwd_required: dict[str, set[str]] = {s: set() for s in self._nodes}
        self._rev_required: dict[str, set[str]] = {s: set() for s in self._nodes}
        # all edges (for general traversal / components)
        self._fwd_all: dict[str, set[str]] = {s: set() for s in self._nodes}
        self._rev_all: dict[str, set[str]] = {s: set() for s in self._nodes}

        for edge in self._edges:
            src, tgt = edge.source, edge.target
            # Ensure unknown targets appear in all indexes (as empty sets if missing)
            self._fwd_all.setdefault(src, set()).add(tgt)
            self._rev_all.setdefault(tgt, set()).add(src)
            if edge.kind in ("required", "capability"):
                self._fwd_required.setdefault(src, set()).add(tgt)
                self._rev_required.setdefault(tgt, set()).add(src)

    # ------------------------------------------------------------------ props

    @property
    def nodes(self) -> Mapping[str, ModuleGraphNode]:
        return self._nodes

    @property
    def edges(self) -> tuple[ModuleGraphEdge, ...]:
        return self._edges

    # ------------------------------------------------------------------ queries

    def roots(self) -> list[str]:
        """Nodes with no incoming required/capability edges."""
        return sorted(
            slug for slug in self._nodes
            if not self._rev_required.get(slug)
        )

    def direct_dependencies(self, slug: str) -> list[str]:
        """Direct required + capability targets that exist in nodes."""
        targets = self._fwd_required.get(slug, set())
        return sorted(t for t in targets if t in self._nodes)

    def direct_dependents(self, slug: str) -> list[str]:
        """Modules that directly require this one (required + capability)."""
        sources = self._rev_required.get(slug, set())
        return sorted(s for s in sources if s in self._nodes)

    def ancestors(self, slug: str) -> frozenset[str]:
        """All transitive required ancestors (cycle-safe BFS)."""
        visited: set[str] = set()
        queue: deque[str] = deque(self._fwd_required.get(slug, set()))
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for nxt in self._fwd_required.get(current, set()):
                if nxt not in visited:
                    queue.append(nxt)
        return frozenset(visited)

    def descendants(self, slug: str) -> frozenset[str]:
        """All transitive dependents (cycle-safe BFS over reverse required edges)."""
        visited: set[str] = set()
        queue: deque[str] = deque(self._rev_required.get(slug, set()))
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for nxt in self._rev_required.get(current, set()):
                if nxt not in visited:
                    queue.append(nxt)
        return frozenset(visited)

    def transitive_disable_impact(self, slug: str) -> frozenset[str]:
        """Slugs that would become unavailable if slug is disabled.

        Includes slug itself.  Only follows required + capability edges
        in the reverse direction (who depends on us?).
        """
        result = self.descendants(slug)
        return frozenset(result | {slug})

    def transitive_enable_impact(self, slug: str) -> frozenset[str]:
        """Slugs whose required deps would become satisfied if slug is enabled."""
        result: set[str] = set()
        for dependent in self._rev_required.get(slug, set()):
            if dependent in self._nodes:
                result.add(dependent)
        return frozenset(result)

    def connected_components(self) -> list[frozenset[str]]:
        """Union-find over all edge kinds.  Returns list of component slug sets."""
        parent: dict[str, str] = {s: s for s in self._nodes}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for edge in self._edges:
            src, tgt = edge.source, edge.target
            if src in self._nodes and tgt in self._nodes:
                union(src, tgt)

        groups: dict[str, set[str]] = {}
        for slug in self._nodes:
            root = find(slug)
            groups.setdefault(root, set()).add(slug)

        return [frozenset(g) for g in groups.values()]

    def cycles(self) -> list[list[str]]:
        """DFS cycle detection.  Returns list of cycles (each as a slug list)."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {s: WHITE for s in self._nodes}
        path: list[str] = []
        found: list[list[str]] = []

        def dfs(node: str) -> None:
            color[node] = GRAY
            path.append(node)
            for neighbor in sorted(self._fwd_required.get(node, set())):
                if neighbor not in self._nodes:
                    continue
                if color[neighbor] == GRAY:
                    # Found a back edge — extract cycle
                    idx = path.index(neighbor)
                    cycle = path[idx:] + [neighbor]
                    found.append(cycle)
                elif color[neighbor] == WHITE:
                    dfs(neighbor)
            path.pop()
            color[node] = BLACK

        for slug in sorted(self._nodes):
            if color[slug] == WHITE:
                dfs(slug)

        return found

    def unresolved_targets(self) -> list[tuple[str, str]]:
        """(source_slug, target_slug) pairs where target is not in nodes."""
        result = []
        for edge in self._edges:
            if edge.target not in self._nodes:
                result.append((edge.source, edge.target))
        return result

    def focused_subgraph(
        self,
        slug: str,
        *,
        include_used_by: bool = True,
    ) -> "FocusedModuleGraph":
        """Build a :class:`FocusedModuleGraph` centred on *slug*.

        Inclusion rules
        ---------------
        - ``selected`` — *slug* itself
        - ``requires`` — direct requirements of *slug* (one hop forward, all edge kinds)
        - ``used_by``  — full transitive reverse closure: all modules that depend on
          *slug*, directly or transitively (shown when ``include_used_by`` is True)

        The returned graph is cycle-safe and does not mutate this graph.
        Direct requirements that point to unregistered slugs are tracked as
        ``missing_targets`` and serialised as synthetic terminal nodes.

        Raises :exc:`ValueError` when *slug* is not registered.
        """
        if slug not in self._nodes:
            raise ValueError(f"Module {slug!r} not found in graph")

        # Direct requires: one-hop forward from slug, all edge kinds.
        # Track first observed edge kind per target for relationship_kind semantics.
        requires_info: dict[str, str] = {}  # target → first edge kind
        missing_targets: set[str] = set()
        for edge in self._edges:
            if edge.source != slug or edge.target == slug:
                continue
            target = edge.target
            if target in self._nodes:
                if target not in requires_info:
                    requires_info[target] = edge.kind
            else:
                missing_targets.add(target)
        requires_slugs: set[str] = set(requires_info.keys())

        # Used-by closure: full transitive reverse BFS from slug (all edge kinds).
        used_by_closure: set[str] = set()
        if include_used_by:
            ub_visited: set[str] = {slug}
            ub_queue: deque[str] = deque([slug])
            while ub_queue:
                current = ub_queue.popleft()
                for edge in self._edges:
                    if edge.target != current:
                        continue
                    consumer = edge.source
                    if consumer not in ub_visited and consumer in self._nodes:
                        ub_visited.add(consumer)
                        used_by_closure.add(consumer)
                        ub_queue.append(consumer)
            # Requires takes priority over used_by (resolves mutual-dependency cycles)
            used_by_closure -= requires_slugs

        # Assign roles
        roles: dict[str, str] = {slug: "selected"}
        for req in requires_slugs:
            roles[req] = "requires"
        for ub in used_by_closure:
            roles[ub] = "used_by"

        focused_slugs = {slug} | requires_slugs | used_by_closure

        # Collect focused nodes
        focused_nodes: dict[str, ModuleGraphNode] = {
            s: self._nodes[s] for s in focused_slugs if s in self._nodes
        }

        # Requires edges: selected → each direct requirement
        req_edges = [
            ModuleGraphEdge(
                source=slug,
                target=req,
                kind="requires",
                capability=None,
                status="resolved",
                relationship_kind=requires_info[req],
            )
            for req in sorted(requires_info.keys())
        ]

        # Used-by edges: original directed edges within the used-by closure,
        # re-wrapped with kind="used_by" so callers can filter by kind.
        used_by_set = {slug} | used_by_closure
        ub_edges = [
            ModuleGraphEdge(
                source=e.source,
                target=e.target,
                kind="used_by",
                capability=e.capability,
                status=e.status,
                relationship_kind=e.kind,  # preserve original edge kind
            )
            for e in self._edges
            if e.source in used_by_closure and e.target in used_by_set
        ]

        all_edges = tuple(req_edges) + tuple(ub_edges)

        # Module name lists for the detail panel
        req_list = tuple(
            {"slug": r, "name": self._nodes[r].title or r}
            for r in sorted(requires_info.keys())
        )
        ub_list = tuple(
            {"slug": u, "name": self._nodes[u].title or u}
            for u in sorted(used_by_closure)
        )

        return FocusedModuleGraph(
            selected_slug=slug,
            nodes=focused_nodes,
            edges=all_edges,
            roles=roles,
            missing_targets=frozenset(missing_targets),
            requires_list=req_list,
            used_by_list=ub_list,
        )

    def to_api_dict(self) -> dict[str, Any]:
        """Serialize for JSON API response."""
        cycle_list = self.cycles()
        components = self.connected_components()
        generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Build nodes list preserving parents/children for backward compat
        # We need to recompute parents/children from edge data
        parents_map: dict[str, list[str]] = {s: [] for s in self._nodes}
        children_map: dict[str, list[str]] = {s: [] for s in self._nodes}
        for edge in self._edges:
            src, tgt = edge.source, edge.target
            if edge.kind in ("required", "capability"):
                if src in children_map:
                    children_map[src].append(tgt)
                if tgt in parents_map:
                    parents_map[tgt].append(src)

        nodes_list = []
        for slug, node in sorted(self._nodes.items()):
            nodes_list.append({
                "slug": node.slug,
                "title": node.title,
                "summary": node.summary,
                "version": node.version,
                "state": node.state,
                "enabled": node.enabled,
                "active": node.active,
                "configured_enabled": node.configured_enabled,
                "pending_restart": node.pending_restart,
                "runtime_enabled": node.runtime_enabled,
                "requires_restart": node.requires_restart,
                "icon_svg": node.icon_svg,
                "visual_color": node.visual_color,
                "group": node.group,
                "display_order": node.display_order,
                "documentation_url": node.documentation_url,
                "source_type": node.source_type,
                "source": node.source,
                "provides": list(node.provides),
                "errors": list(node.errors),
                "parents": sorted(set(parents_map.get(slug, []))),
                "children": sorted(set(children_map.get(slug, []))),
            })

        edges_list = [
            {
                "source": e.source,
                "target": e.target,
                "kind": e.kind,
                "capability": e.capability,
                "status": e.status,
            }
            for e in self._edges
        ]

        restart_required = any(n.pending_restart for n in self._nodes.values())

        return {
            "nodes": nodes_list,
            "edges": edges_list,
            "metadata": {
                "generated_at": generated_at,
                "restart_required": restart_required,
                "pending_restart_count": sum(1 for n in self._nodes.values() if n.pending_restart),
                "nodes_count": len(self._nodes),
                "edges_count": len(self._edges),
                "cycles": cycle_list,
                "components_count": len(components),
            },
        }


# --------------------------------------------------------------------------- #
# Presentation helpers                                                         #
# --------------------------------------------------------------------------- #

def _presentation_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Extract presentation metadata from an inventory entry."""
    manifest_dict: dict[str, Any] | None = entry.get("manifest")
    slug: str = entry["slug"]

    if manifest_dict is not None:
        try:
            manifest = ModuleManifest.from_dict(manifest_dict)
            p = manifest.presentation
            title = p.title or manifest.label or slug
            return {
                "title": title,
                "summary": p.summary,
                "icon_svg": safe_svg_or_fallback(p.icon_svg, slug),
                "group": p.group,
                "display_order": p.display_order,
                "documentation_url": p.documentation_url,
                "requires_restart": manifest.requires_restart,
            }
        except Exception:
            pass

    return {
        "title": entry.get("label") or slug,
        "summary": "",
        "icon_svg": safe_svg_or_fallback("", slug),
        "group": "",
        "display_order": 0,
        "documentation_url": "",
        "requires_restart": False,
    }


# --------------------------------------------------------------------------- #
# Edge status                                                                  #
# --------------------------------------------------------------------------- #

def _edge_status(
    source_slug: str,
    target_slug: str,
    node_slugs: set[str],
    errored_slugs: set[str],
) -> str:
    """Derive a display status for a directed edge source → target."""
    if target_slug not in node_slugs:
        return "missing"
    if source_slug in errored_slugs:
        return "blocked"
    if target_slug in errored_slugs:
        return "conflict"
    return "resolved"


# --------------------------------------------------------------------------- #
# Graph builder                                                                #
# --------------------------------------------------------------------------- #

def build_graph(
    registry: Any,
    *,
    configured_overrides: dict[str, bool] | None = None,
) -> ModuleGraph:
    """Build a :class:`ModuleGraph` from *registry*.

    Uses only ``registry.inventory()``, ``registry.capabilities()``, and
    ``registry.errors()``.  Never hardcodes slugs.

    Pass ``configured_overrides`` (slug → bool) from the JSON overlay so that
    ``node.configured_enabled`` reflects pending UI changes that have not yet
    been applied via a server restart, while ``node.runtime_enabled`` (= active)
    reflects the live state.

    Edge building rules
    -------------------
    - ``requires`` entries with kind="module": kind="required"
    - ``requires`` entries with kind="capability": kind="capability",
      using ``entry["selected_providers"][cap_slug]`` for the precise provider.
      If no single provider selected and >1 available: status="conflict".
      If 0 providers: status="missing".
    - ``optional`` entries: kind="optional" (never blocks; status "resolved" or "missing")
    - Uses ``entry["errors"]`` to detect blocked edges.
    """
    inventory: list[dict[str, Any]] = registry.inventory()
    capabilities_map: dict[str, list[str]] = registry.capabilities()
    resolution_errors = registry.errors()

    errored_slugs: set[str] = {e.module_slug for e in resolution_errors}
    node_slugs: set[str] = {entry["slug"] for entry in inventory}

    # ------------------------------------------------------------------ nodes
    graph_nodes: list[ModuleGraphNode] = []
    for entry in inventory:
        slug: str = entry["slug"]
        pres = _presentation_fields(entry)
        enabled = bool(entry.get("enabled"))
        active = bool(entry.get("active"))
        if configured_overrides is not None:
            configured = configured_overrides.get(slug, enabled)
        else:
            configured = enabled

        graph_nodes.append(ModuleGraphNode(
            slug=slug,
            title=pres["title"],
            summary=pres["summary"],
            version=entry.get("version") or "",
            state=entry.get("state") or "unavailable",
            enabled=enabled,
            active=active,
            configured_enabled=configured,
            pending_restart=configured != enabled,
            runtime_enabled=active,
            requires_restart=pres["requires_restart"],
            icon_svg=pres["icon_svg"],
            visual_color=slug_color(slug),
            group=pres["group"],
            display_order=pres["display_order"],
            source_type=entry.get("source_type"),
            source=entry.get("source") or "",
            provides=tuple(sorted(entry.get("provides") or [])),
            errors=tuple(entry.get("errors") or []),
            documentation_url=pres.get("documentation_url", ""),
            # parents/children populated later; these are stubs
            parents=(),
            children=(),
        ))

    # ------------------------------------------------------------------ edges
    graph_edges: list[ModuleGraphEdge] = []

    for entry in inventory:
        source_slug: str = entry["slug"]
        requires: list[dict[str, Any]] = entry.get("requires") or []
        optional: list[dict[str, Any]] = entry.get("optional") or []
        selected_providers: dict[str, str] = entry.get("selected_providers") or {}
        entry_errors: list[dict[str, Any]] = entry.get("errors") or []
        is_blocked = bool(entry_errors) or (source_slug in errored_slugs)

        # --- required ---
        for req in requires:
            req_slug: str = req["slug"]
            req_kind: str = req.get("kind", "module")

            if req_kind == "capability":
                cap_slug = req_slug
                # Use selected_providers for precise single-provider edge
                if cap_slug in selected_providers:
                    provider_slug = selected_providers[cap_slug]
                    status = _edge_status(source_slug, provider_slug, node_slugs, errored_slugs)
                    if is_blocked and status == "resolved":
                        status = "blocked"
                    graph_edges.append(ModuleGraphEdge(
                        source=source_slug,
                        target=provider_slug,
                        kind="capability",
                        capability=cap_slug,
                        status=status,
                    ))
                else:
                    providers = capabilities_map.get(cap_slug, [])
                    if len(providers) > 1:
                        # Multiple providers, none selected — conflict
                        for provider_slug in providers:
                            graph_edges.append(ModuleGraphEdge(
                                source=source_slug,
                                target=provider_slug,
                                kind="capability",
                                capability=cap_slug,
                                status="conflict",
                            ))
                    elif len(providers) == 1:
                        provider_slug = providers[0]
                        status = _edge_status(source_slug, provider_slug, node_slugs, errored_slugs)
                        if is_blocked and status == "resolved":
                            status = "blocked"
                        graph_edges.append(ModuleGraphEdge(
                            source=source_slug,
                            target=provider_slug,
                            kind="capability",
                            capability=cap_slug,
                            status=status,
                        ))
                    else:
                        # 0 providers — missing edge pointing at cap slug
                        graph_edges.append(ModuleGraphEdge(
                            source=source_slug,
                            target=cap_slug,
                            kind="capability",
                            capability=cap_slug,
                            status="missing",
                        ))
            else:
                # kind == "module"
                status = _edge_status(source_slug, req_slug, node_slugs, errored_slugs)
                if is_blocked and status == "resolved":
                    status = "blocked"
                graph_edges.append(ModuleGraphEdge(
                    source=source_slug,
                    target=req_slug,
                    kind="required",
                    capability=None,
                    status=status,
                ))

        # --- optional ---
        for opt in optional:
            opt_slug: str = opt["slug"]
            opt_kind: str = opt.get("kind", "module")

            if opt_kind == "capability":
                cap_slug = opt_slug
                if cap_slug in selected_providers:
                    provider_slug = selected_providers[cap_slug]
                    status = _edge_status(source_slug, provider_slug, node_slugs, errored_slugs)
                    graph_edges.append(ModuleGraphEdge(
                        source=source_slug,
                        target=provider_slug,
                        kind="capability",
                        capability=cap_slug,
                        status=status,
                    ))
                else:
                    providers = capabilities_map.get(cap_slug, [])
                    for provider_slug in providers:
                        status = _edge_status(source_slug, provider_slug, node_slugs, errored_slugs)
                        graph_edges.append(ModuleGraphEdge(
                            source=source_slug,
                            target=provider_slug,
                            kind="capability",
                            capability=cap_slug,
                            status=status,
                        ))
                    if not providers:
                        graph_edges.append(ModuleGraphEdge(
                            source=source_slug,
                            target=cap_slug,
                            kind="capability",
                            capability=cap_slug,
                            status="missing",
                        ))
            else:
                status_opt = "resolved" if opt_slug in node_slugs else "missing"
                graph_edges.append(ModuleGraphEdge(
                    source=source_slug,
                    target=opt_slug,
                    kind="optional",
                    capability=None,
                    status=status_opt,
                ))

    return ModuleGraph(graph_nodes, graph_edges)
