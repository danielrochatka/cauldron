"""Views for cauldron_module_tree."""
from __future__ import annotations

import json
import logging
from functools import wraps
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from cauldron_module_tree.graph import build_graph, ModuleGraph

logger = logging.getLogger(__name__)

_VIEW_PERM = "cauldron_module_tree.view_module_tree"
_CHANGE_PERM = "cauldron_module_tree.change_module_state"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _get_registry():
    from cauldron.modules.registry import registry
    return registry


def _get_data_dir():
    """Return the Django app's data directory (BASE_DIR/data)."""
    from django.conf import settings
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        return None
    return base_dir / "data"


def _update_overlay(slug: str, enabled: bool) -> None:
    """Atomically update the module-state overlay for one slug."""
    from cauldron.modules.overlay import load_overlay, save_overlay
    data_dir = _get_data_dir()
    if data_dir is None:
        return
    overrides, _ = load_overlay(data_dir)
    overrides[slug] = {"enabled": enabled}
    save_overlay(data_dir, overrides)


def _parse_json_body(request: HttpRequest) -> dict[str, Any]:
    ct = (request.META.get("CONTENT_TYPE") or "").split(";", 1)[0].strip().lower()
    if ct != "application/json":
        raise ValueError("Content-Type must be application/json")
    try:
        return json.loads(request.body.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc


# --------------------------------------------------------------------------- #
# Permission decorators                                                        #
# --------------------------------------------------------------------------- #

def _require_perm(perm: str):
    """Decorator factory: requires login and the given Django permission."""
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request: HttpRequest, *args, **kwargs):
            if not request.user.has_perm(perm):
                if request.accepts("application/json"):
                    return JsonResponse(
                        {"error": "Permission denied.", "type": "PermissionDenied"},
                        status=403,
                    )
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


_view_required = _require_perm(_VIEW_PERM)
_change_required = _require_perm(_CHANGE_PERM)


# --------------------------------------------------------------------------- #
# Views                                                                        #
# --------------------------------------------------------------------------- #

@_view_required
def tree_view(request: HttpRequest) -> HttpResponse:
    """Render the module dependency tree page."""
    context = {
        "breadcrumbs": [
            {"label": "Modules", "url": "/cauldron/modules/"},
            {"label": "Dependency Tree", "url": ""},
        ],
    }
    return render(request, "cauldron_module_tree/tree.html", context)


@_view_required
def graph_api(request: HttpRequest) -> JsonResponse:
    """Return the full module graph as JSON."""
    try:
        registry = _get_registry()
        graph: ModuleGraph = build_graph(registry)
        data = graph.to_api_dict()
        return JsonResponse(data)
    except Exception as exc:
        logger.exception("graph_api: failed to build module graph")
        return JsonResponse(
            {"error": "Failed to build module graph.", "type": type(exc).__name__},
            status=500,
        )


@require_POST
@_change_required
def preview_change(request: HttpRequest, module_slug: str) -> JsonResponse:
    """Preview the effect of enabling or disabling a module."""
    try:
        body = _parse_json_body(request)
    except ValueError as exc:
        return JsonResponse(
            {"error": str(exc), "type": "ValueError"},
            status=400,
        )

    action = body.get("action", "")
    if action not in ("enable", "disable"):
        return JsonResponse(
            {"error": "action must be 'enable' or 'disable'.", "type": "ValueError"},
            status=400,
        )

    try:
        registry = _get_registry()
        inventory = registry.inventory()

        # Independently validate: locate module in the registry (don't trust
        # any prior state; revalidate from fresh inventory).
        target_entry: dict[str, Any] | None = None
        for entry in inventory:
            if entry["slug"] == module_slug:
                target_entry = entry
                break

        warnings: list[str] = []
        validation_errors: list[str] = []
        affected_modules: list[str] = []
        missing_dependencies: list[str] = []
        restart_required = False

        if target_entry is None:
            validation_errors.append(
                f"Module '{module_slug}' was not found in the registry."
            )
        else:
            restart_required = bool(target_entry.get("requires_restart", False))

            # Use the ModuleGraph domain model for transitive impact analysis
            graph: ModuleGraph = build_graph(registry)

            if action == "disable":
                # Primary: use transitive graph impact (follows required/capability edges)
                if module_slug in graph.nodes:
                    impact = graph.transitive_disable_impact(module_slug)
                    affected_set: set[str] = set(impact - {module_slug})
                else:
                    affected_set = set()

                # Secondary: also scan the deps field (flat direct-dependency list from
                # the registry's dependency_graph projection) to catch dependents that
                # are registered without explicit requires entries.
                for entry in inventory:
                    if module_slug in (entry.get("deps") or []):
                        affected_set.add(entry["slug"])

                affected_modules = sorted(affected_set)
                if affected_modules:
                    warnings.append(
                        f"Disabling this module will affect: {', '.join(affected_modules)}."
                    )

            else:
                # action == "enable": verify module isn't permanently failed
                state = target_entry.get("state", "")
                if state in ("failed", "unavailable"):
                    validation_errors.append(
                        f"Module '{module_slug}' is in state '{state}' and cannot be enabled."
                    )

                # Find missing required dependencies
                node_slugs = {e["slug"] for e in inventory}
                for req in target_entry.get("requires") or []:
                    if req.get("kind") == "module" and req["slug"] not in node_slugs:
                        missing_dependencies.append(req["slug"])
                if missing_dependencies:
                    validation_errors.append(
                        f"Missing required modules: {', '.join(missing_dependencies)}."
                    )

        return JsonResponse({
            "allowed": len(validation_errors) == 0,
            "action": action,
            "module": module_slug,
            "affected_modules": affected_modules,
            "missing_dependencies": missing_dependencies,
            "restart_required": restart_required,
            "warnings": warnings,
            "validation_errors": validation_errors,
        })

    except Exception as exc:
        logger.exception("preview_change: unexpected error for module %r", module_slug)
        return JsonResponse(
            {"error": "Failed to compute preview.", "type": type(exc).__name__},
            status=500,
        )


@require_POST
@_change_required
def enable_module(request: HttpRequest, module_slug: str) -> JsonResponse:
    """Persist an enable override for a module."""
    return _store_override(request, module_slug, enabled=True)


@require_POST
@_change_required
def disable_module(request: HttpRequest, module_slug: str) -> JsonResponse:
    """Persist a disable override for a module."""
    return _store_override(request, module_slug, enabled=False)


def _store_override(
    request: HttpRequest, module_slug: str, *, enabled: bool
) -> JsonResponse:
    """Create or update a ``ModuleEnabledOverride`` and return a status response."""
    reason = ""
    try:
        body = _parse_json_body(request)
        reason = str(body.get("reason", ""))
    except ValueError:
        # Reason is optional; ignore parse errors for the body here.
        pass

    try:
        from cauldron_module_tree.models import ModuleEnabledOverride
        from cauldron.modules.registry import registry

        # Independently validate: verify module exists in registry and compute impact
        inventory = registry.inventory()

        runtime_active = False
        for entry in inventory:
            if entry["slug"] == module_slug:
                runtime_active = bool(entry.get("active", False))
                break

        # Compute transitive impact using the domain model
        graph: ModuleGraph = build_graph(registry)
        if enabled:
            affected = sorted(graph.transitive_enable_impact(module_slug))
        else:
            impact = graph.transitive_disable_impact(module_slug)
            affected = sorted(impact - {module_slug})

        override, _created = ModuleEnabledOverride.objects.update_or_create(
            slug=module_slug,
            defaults={
                "enabled": enabled,
                "changed_by": request.user if request.user.is_authenticated else None,
                "reason": reason,
            },
        )

        # Write the overlay file so the change survives server restarts.
        try:
            _update_overlay(module_slug, enabled)
        except Exception:
            logger.exception(
                "_store_override: failed to write overlay for module %r", module_slug
            )

        action = "enable" if enabled else "disable"
        return JsonResponse({
            "slug": module_slug,
            "action": action,
            "configured_enabled": enabled,
            "runtime_active": runtime_active,
            "restart_required": True,
            "affected_modules": affected,
            "message": "Override saved. Restart the server to apply the change.",
        })

    except Exception as exc:
        logger.exception("_store_override: failed to save override for module %r", module_slug)
        return JsonResponse(
            {"error": "Failed to save override.", "type": type(exc).__name__},
            status=500,
        )
