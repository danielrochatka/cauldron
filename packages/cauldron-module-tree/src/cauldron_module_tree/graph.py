"""Build a JSON-serializable dependency graph from the Cauldron module registry."""
from __future__ import annotations

import datetime
from typing import Any

from cauldron.modules import ModuleManifest

from cauldron_module_tree.sanitize import safe_svg_or_fallback


def _presentation_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Extract presentation metadata from an inventory entry.

    When the manifest is available, deserialise it and read ``presentation``.
    Falls back to safe defaults when the manifest is absent (unavailable
    placeholder modules).
    """
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
            # Malformed manifest — fall through to defaults.
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


def build_graph(registry: Any) -> dict[str, Any]:
    """Build a JSON-serialisable dependency graph from *registry*.

    Parameters
    ----------
    registry:
        The Cauldron module registry object (``cauldron.modules.registry.registry``).
        Only public methods are called: ``inventory()``, ``capabilities()``,
        and ``errors()``.

    Returns
    -------
    dict
        ``{"nodes": [...], "edges": [...], "metadata": {...}}``
    """
    inventory = registry.inventory()
    capabilities_map: dict[str, list[str]] = registry.capabilities()
    resolution_errors = registry.errors()

    # Slugs that appear in resolution errors — used for edge status.
    errored_slugs: set[str] = {e.module_slug for e in resolution_errors}

    # Build node set for fast membership tests.
    node_slugs: set[str] = {entry["slug"] for entry in inventory}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Temporary mapping: slug → list of parent slugs (computed from edges).
    parents_map: dict[str, list[str]] = {entry["slug"]: [] for entry in inventory}

    # ------------------------------------------------------------------ nodes
    for entry in inventory:
        slug: str = entry["slug"]
        pres = _presentation_fields(entry)

        node: dict[str, Any] = {
            "slug": slug,
            "title": pres["title"],
            "summary": pres["summary"],
            "version": entry.get("version") or "",
            "icon_svg": pres["icon_svg"],
            "group": pres["group"],
            "display_order": pres["display_order"],
            "documentation_url": pres["documentation_url"],
            "state": entry.get("state") or "unavailable",
            "enabled": bool(entry.get("enabled")),
            "active": bool(entry.get("active")),
            "requires_restart": pres["requires_restart"],
            "source_type": entry.get("source_type"),
            "source": entry.get("source") or "",
            "provides": list(entry.get("provides") or []),
            "errors": list(entry.get("errors") or []),
            # parents and children are filled in below
            "parents": [],
            "children": list(entry.get("deps") or []),
        }
        nodes.append(node)

    # ------------------------------------------------------------------ edges
    for entry in inventory:
        source_slug: str = entry["slug"]
        requires: list[dict[str, Any]] = entry.get("requires") or []
        optional: list[dict[str, Any]] = entry.get("optional") or []

        for req in requires:
            req_slug: str = req["slug"]
            req_kind: str = req.get("kind", "module")

            if req_kind == "capability":
                providers = capabilities_map.get(req_slug, [])
                for provider_slug in providers:
                    status = _edge_status(
                        source_slug, provider_slug, node_slugs, errored_slugs
                    )
                    edges.append({
                        "source": source_slug,
                        "target": provider_slug,
                        "kind": "capability",
                        "capability": req_slug,
                        "status": status,
                    })
                    # Register parent relationship.
                    if provider_slug in parents_map:
                        parents_map[provider_slug].append(source_slug)
                if not providers:
                    # No known provider — emit a "missing" edge toward the capability slug.
                    edges.append({
                        "source": source_slug,
                        "target": req_slug,
                        "kind": "capability",
                        "capability": req_slug,
                        "status": "missing",
                    })
            else:
                # kind == "module"
                status = _edge_status(
                    source_slug, req_slug, node_slugs, errored_slugs
                )
                edges.append({
                    "source": source_slug,
                    "target": req_slug,
                    "kind": "required",
                    "capability": None,
                    "status": status,
                })
                if req_slug in parents_map:
                    parents_map[req_slug].append(source_slug)

        for opt in optional:
            opt_slug: str = opt["slug"]
            opt_kind: str = opt.get("kind", "module")

            if opt_kind == "capability":
                providers = capabilities_map.get(opt_slug, [])
                for provider_slug in providers:
                    status = _edge_status(
                        source_slug, provider_slug, node_slugs, errored_slugs
                    )
                    edges.append({
                        "source": source_slug,
                        "target": provider_slug,
                        "kind": "capability",
                        "capability": opt_slug,
                        "status": status,
                    })
                    if provider_slug in parents_map:
                        parents_map[provider_slug].append(source_slug)
            else:
                status = _edge_status(
                    source_slug, opt_slug, node_slugs, errored_slugs
                )
                edges.append({
                    "source": source_slug,
                    "target": opt_slug,
                    "kind": "optional",
                    "capability": None,
                    "status": status,
                })
                if opt_slug in parents_map:
                    parents_map[opt_slug].append(source_slug)

    # ------------------------------------------------------------------ parents
    # Populate the parents list on each node from the inverted edge map.
    node_by_slug: dict[str, dict[str, Any]] = {n["slug"]: n for n in nodes}
    for slug, parent_list in parents_map.items():
        if slug in node_by_slug:
            node_by_slug[slug]["parents"] = sorted(set(parent_list))

    # ---------------------------------------------------------------- metadata
    restart_required = any(n["requires_restart"] and n["active"] for n in nodes)

    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return {
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "generated_at": generated_at,
            "restart_required": restart_required,
        },
    }
