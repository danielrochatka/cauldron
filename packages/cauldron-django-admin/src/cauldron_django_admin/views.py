"""Views for the Cauldron Admin Shell."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def dashboard_view(request):
    from .navigation import get_navigation_registry
    registry = get_navigation_registry()
    cards = registry.get_dashboard_cards(request.user, request)
    return render(request, "cauldron_admin/dashboard.html", {
        "dashboard_cards": cards,
        "breadcrumbs": [],
    })


@login_required
def modules_view(request):
    modules = []
    registry_errors: list[dict[str, str]] = []

    def _add_error(err, default_slug: str = "") -> None:
        slug = ""
        for attr in ("module_slug", "slug", "module"):
            value = getattr(err, attr, "") or ""
            if isinstance(value, str) and value:
                slug = value
                break
        if not slug and default_slug:
            slug = default_slug
        registry_errors.append({
            "kind": type(err).__name__,
            "module": slug,
        })

    try:
        from cauldron.modules.registry import registry as mod_registry
    except ImportError:
        # cauldron.modules is not installed — nothing to display.
        return render(request, "cauldron_admin/modules.html", {
            "modules": modules,
            "registry_errors": registry_errors,
            "breadcrumbs": [{"label": "Modules", "url": ""}],
        })
    except Exception as exc:
        registry_errors.append({
            "kind": type(exc).__name__,
            "module": "",
        })
        return render(request, "cauldron_admin/modules.html", {
            "modules": modules,
            "registry_errors": registry_errors,
            "breadcrumbs": [{"label": "Modules", "url": ""}],
        })

    try:
        graph_list = mod_registry.graph_info() or []
    except Exception as exc:
        graph_list = []
        registry_errors.append({"kind": type(exc).__name__, "module": ""})

    try:
        cap_map_raw = mod_registry.capabilities()
        cap_map = cap_map_raw if isinstance(cap_map_raw, dict) else {}
    except Exception as exc:
        cap_map = {}
        registry_errors.append({"kind": type(exc).__name__, "module": ""})

    try:
        dep_graph_raw = mod_registry.dependency_graph()
        dep_graph = dep_graph_raw if isinstance(dep_graph_raw, dict) else {}
    except Exception as exc:
        dep_graph = {}
        registry_errors.append({"kind": type(exc).__name__, "module": ""})

    for info in graph_list:
        if not isinstance(info, dict):
            continue
        slug = info.get("slug", "") or ""
        modules.append({
            "slug": slug,
            "label": info.get("label", slug),
            "version": info.get("version", "") or "",
            "status": info.get("status", "active") or "active",
            "provides": list(info.get("provides", []) or []),
            "capabilities": sorted(
                cap for cap, providers in cap_map.items()
                if isinstance(providers, (list, tuple)) and slug in providers
            ),
            "dependencies": list(dep_graph.get(slug, []) or []),
        })

    # Diagnostic errors — redacted to type-name + module slug only.
    try:
        for err in (mod_registry.errors() or []):
            _add_error(err)
    except Exception as exc:
        registry_errors.append({"kind": type(exc).__name__, "module": ""})

    try:
        for err in (mod_registry.discovery_errors() or []):
            _add_error(err)
    except Exception as exc:
        registry_errors.append({"kind": type(exc).__name__, "module": ""})

    try:
        for err in (mod_registry.lifecycle_errors() or []):
            _add_error(err)
    except Exception as exc:
        registry_errors.append({"kind": type(exc).__name__, "module": ""})

    return render(request, "cauldron_admin/modules.html", {
        "modules": modules,
        "registry_errors": registry_errors,
        "breadcrumbs": [{"label": "Modules", "url": ""}],
    })
