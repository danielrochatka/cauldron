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
        inventory = mod_registry.inventory() or []
    except Exception as exc:
        inventory = []
        registry_errors.append({"kind": type(exc).__name__, "module": ""})

    try:
        cap_map_raw = mod_registry.capabilities()
        cap_map = cap_map_raw if isinstance(cap_map_raw, dict) else {}
    except Exception as exc:
        cap_map = {}
        registry_errors.append({"kind": type(exc).__name__, "module": ""})

    from django.conf import settings
    cap_providers_setting_raw = getattr(
        settings, "CAULDRON_CAPABILITY_PROVIDERS", None,
    ) or {}
    if isinstance(cap_providers_setting_raw, dict):
        cap_providers_setting = {
            k: v for k, v in cap_providers_setting_raw.items()
            if isinstance(k, str) and isinstance(v, str) and k and v
        }
    else:
        cap_providers_setting = {}

    for info in inventory:
        if not isinstance(info, dict):
            continue
        slug = info.get("slug", "") or ""
        state = info.get("state") or "discovered"
        is_active = bool(info.get("active", False))
        is_enabled = bool(info.get("enabled", False))
        provided_caps = list(info.get("provides", []) or [])
        requires = list(info.get("requires", []) or [])
        deps = list(info.get("deps", []) or [])
        django_apps = list(info.get("django_apps", []) or [])
        load_index = info.get("load_index")
        source_type = info.get("source_type")
        source = info.get("source") or ""
        module_errors = list(info.get("errors", []) or [])

        selected_providers = {
            cap: cap_providers_setting[cap]
            for cap in provided_caps
            if cap in cap_providers_setting
        }

        modules.append({
            "slug": slug,
            "label": info.get("label") or slug,
            "version": info.get("version") or "",
            "state": state,
            "enabled": is_enabled,
            "active": is_active,
            "load_index": load_index,
            "source_type": source_type,
            "source": source,
            "provides": provided_caps,
            "requires": requires,
            "deps": deps,
            "django_apps": django_apps,
            "selected_providers": selected_providers,
            "capabilities": sorted(
                cap for cap, providers in cap_map.items()
                if isinstance(providers, (list, tuple)) and slug in providers
            ),
            "errors": module_errors,
        })

    # Global diagnostics block — errors from all registry sources.
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
