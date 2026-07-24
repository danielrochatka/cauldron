"""Views for the Cauldron Admin Shell."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse


@login_required
def dashboard_view(request):
    """Render the Cauldron admin dashboard."""
    from .navigation import get_navigation_registry
    nav_registry = get_navigation_registry()
    grouped = nav_registry.get_grouped_nav(request.user, request)
    cards = []
    for group in grouped:
        for item in group["items"]:
            try:
                url = reverse(item.url_name)
            except NoReverseMatch:
                url = "#"
            cards.append({
                "label": item.label,
                "description": item.description,
                "url": url,
            })
    return render(request, "cauldron_admin/dashboard.html", {
        "dashboard_cards": cards,
        "breadcrumbs": [],
    })


@login_required
def modules_view(request):
    """Render the module status page."""
    modules = []
    try:
        from cauldron.modules.registry import registry
        if registry.is_ready:
            for mod in registry.modules.values():
                modules.append({
                    "slug": mod.slug,
                    "label": mod.label,
                    "version": mod.manifest.version,
                    "status": "active",
                    "provides": list(mod.manifest.provides),
                })
    except Exception:
        pass
    return render(request, "cauldron_admin/modules.html", {
        "modules": modules,
        "breadcrumbs": [{"label": "Modules", "url": ""}],
    })
