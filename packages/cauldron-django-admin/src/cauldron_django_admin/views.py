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
    registry_errors = []
    try:
        from cauldron.modules.registry import get_module_registry
        reg = get_module_registry()
        # Use supported public APIs
        try:
            graph = reg.graph_info() if hasattr(reg, "graph_info") else {}
        except Exception as exc:
            registry_errors.append(f"graph_info: {type(exc).__name__}: {exc}")
            graph = {}

        try:
            caps = reg.capabilities() if hasattr(reg, "capabilities") else {}
        except Exception as exc:
            registry_errors.append(f"capabilities: {type(exc).__name__}: {exc}")
            caps = {}

        try:
            dep_graph = reg.dependency_graph() if hasattr(reg, "dependency_graph") else {}
        except Exception as exc:
            registry_errors.append(f"dependency_graph: {type(exc).__name__}: {exc}")
            dep_graph = {}

        try:
            errs = reg.errors() if hasattr(reg, "errors") else []
        except Exception as exc:
            registry_errors.append(f"errors: {type(exc).__name__}: {exc}")
            errs = []

        try:
            disc_errs = reg.discovery_errors() if hasattr(reg, "discovery_errors") else []
        except Exception as exc:
            registry_errors.append(f"discovery_errors: {type(exc).__name__}: {exc}")
            disc_errs = []

        try:
            life_errs = reg.lifecycle_errors() if hasattr(reg, "lifecycle_errors") else []
        except Exception as exc:
            registry_errors.append(f"lifecycle_errors: {type(exc).__name__}: {exc}")
            life_errs = []

        # Fall back to iterating modules directly if graph_info not available
        if not graph and hasattr(reg, "modules"):
            for mod in reg.modules.values():
                manifest = getattr(mod, "manifest", None)
                slug = getattr(mod, "slug", str(mod))
                label = getattr(mod, "label", slug)
                version = getattr(manifest, "version", "") if manifest else ""
                provides = list(getattr(manifest, "provides", [])) if manifest else []
                deps = dep_graph.get(slug, []) if isinstance(dep_graph, dict) else []
                module_caps = caps.get(slug, []) if isinstance(caps, dict) else []
                modules.append({
                    "slug": slug,
                    "label": label,
                    "version": version,
                    "status": "active",
                    "provides": provides,
                    "capabilities": module_caps,
                    "dependencies": deps,
                })
        elif isinstance(graph, dict):
            for slug, info in graph.items():
                modules.append({
                    "slug": slug,
                    "label": info.get("label", slug),
                    "version": info.get("version", ""),
                    "status": info.get("status", "active"),
                    "provides": info.get("provides", []),
                    "capabilities": caps.get(slug, []) if isinstance(caps, dict) else [],
                    "dependencies": dep_graph.get(slug, []) if isinstance(dep_graph, dict) else [],
                })

        for err in (errs or []):
            registry_errors.append(str(err))
        for err in (disc_errs or []):
            registry_errors.append(str(err))
        for err in (life_errs or []):
            registry_errors.append(str(err))

    except ImportError:
        pass
    except Exception as exc:
        # Do not suppress programming errors — surface them
        registry_errors.append(f"{type(exc).__name__}: {exc}")

    return render(request, "cauldron_admin/modules.html", {
        "modules": modules,
        "registry_errors": registry_errors,
        "breadcrumbs": [{"label": "Modules", "url": ""}],
    })
