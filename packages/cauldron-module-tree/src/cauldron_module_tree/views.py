"""Views for cauldron_module_tree."""
from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from cauldron_module_tree.graph import build_graph

logger = logging.getLogger(__name__)

_VIEW_PERM = "cauldron_module_tree.view_module_tree"
_CHANGE_PERM = "cauldron_module_tree.change_module_state"


def _get_registry():
    from cauldron.modules.registry import registry
    return registry


def _parse_json_body(request: HttpRequest) -> dict[str, Any]:
    ct = (request.META.get("CONTENT_TYPE") or "").split(";", 1)[0].strip().lower()
    if ct != "application/json":
        raise ValueError("Content-Type must be application/json")
    try:
        return json.loads(request.body.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc


@login_required
def tree_view(request: HttpRequest) -> HttpResponse:
    """Render the module dependency tree page."""
    if not request.user.has_perm(_VIEW_PERM):
        raise PermissionDenied
    context = {
        "breadcrumbs": [
            {"label": "Modules", "url": "/cauldron/modules/"},
            {"label": "Dependency Tree", "url": ""},
        ],
    }
    return render(request, "cauldron_module_tree/tree.html", context)


@login_required
def graph_api(request: HttpRequest) -> JsonResponse:
    """Return the full module graph as JSON."""
    if not request.user.has_perm(_VIEW_PERM):
        return JsonResponse(
            {"error": "Permission denied.", "type": "PermissionDenied"},
            status=403,
        )
    try:
        registry = _get_registry()
        graph_data = build_graph(registry)
        return JsonResponse(graph_data)
    except Exception as exc:
        logger.exception("graph_api: failed to build module graph")
        return JsonResponse(
            {"error": "Failed to build module graph.", "type": type(exc).__name__},
            status=500,
        )


@login_required
@require_POST
def preview_change(request: HttpRequest, module_slug: str) -> JsonResponse:
    """Preview the effect of enabling or disabling a module."""
    if not request.user.has_perm(_CHANGE_PERM):
        return JsonResponse(
            {"error": "Permission denied.", "type": "PermissionDenied"},
            status=403,
        )

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
        graph_entries = registry.graph_info()

        # Locate the target module in the inventory.
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

        if target_entry is not None:
            restart_required = bool(target_entry.get("requires_restart", False))

        if action == "disable":
            # Affected modules: those in graph_info that list module_slug in their deps.
            for g in graph_entries:
                if module_slug in (g.get("deps") or []):
                    affected_modules.append(g["slug"])
            if affected_modules:
                warnings.append(
                    f"Disabling this module will affect: {', '.join(affected_modules)}."
                )
        else:
            # action == "enable": find missing dependencies.
            if target_entry is not None:
                node_slugs = {e["slug"] for e in inventory}
                for req in target_entry.get("requires") or []:
                    if req.get("kind") == "module" and req["slug"] not in node_slugs:
                        missing_dependencies.append(req["slug"])
                if missing_dependencies:
                    validation_errors.append(
                        f"Missing required modules: {', '.join(missing_dependencies)}."
                    )

        if target_entry is None:
            validation_errors.append(f"Module '{module_slug}' was not found in the registry.")

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


@login_required
@require_POST
def enable_module(request: HttpRequest, module_slug: str) -> JsonResponse:
    """Persist an enable override for a module."""
    if not request.user.has_perm(_CHANGE_PERM):
        return JsonResponse(
            {"error": "Permission denied.", "type": "PermissionDenied"},
            status=403,
        )
    return _store_override(request, module_slug, enabled=True)


@login_required
@require_POST
def disable_module(request: HttpRequest, module_slug: str) -> JsonResponse:
    """Persist a disable override for a module."""
    if not request.user.has_perm(_CHANGE_PERM):
        return JsonResponse(
            {"error": "Permission denied.", "type": "PermissionDenied"},
            status=403,
        )
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

        override, _created = ModuleEnabledOverride.objects.update_or_create(
            slug=module_slug,
            defaults={
                "enabled": enabled,
                "changed_by": request.user if request.user.is_authenticated else None,
                "reason": reason,
            },
        )

        # Determine the current runtime active state from the registry.
        runtime_active = False
        for entry in registry.inventory():
            if entry["slug"] == module_slug:
                runtime_active = bool(entry.get("active", False))
                break

        return JsonResponse({
            "slug": module_slug,
            "configured_enabled": enabled,
            "runtime_active": runtime_active,
            "restart_required": True,
            "message": "Override saved. Restart the server to apply the change.",
        })

    except Exception as exc:
        logger.exception("_store_override: failed to save override for module %r", module_slug)
        return JsonResponse(
            {"error": "Failed to save override.", "type": type(exc).__name__},
            status=500,
        )
