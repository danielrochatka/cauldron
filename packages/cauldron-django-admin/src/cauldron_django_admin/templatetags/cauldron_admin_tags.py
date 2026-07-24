"""Template tags for the Cauldron Admin Shell."""
from django import template

from ..navigation import get_navigation_registry

register = template.Library()


@register.simple_tag(takes_context=True)
def get_navigation(context, request):
    """Return grouped navigation for the current user."""
    registry = get_navigation_registry()
    user = getattr(request, "user", None)
    return registry.get_grouped_nav(user, request)


@register.simple_tag(takes_context=True)
def get_override_css_urls(context, scope):
    """Return a list of URL strings for all valid CSS files in the given scope.

    Files are discovered via UIOverrideStore and listed in deterministic lexical order.
    Returns an empty list if the override directory is absent.
    """
    from pathlib import Path
    from django.urls import reverse, NoReverseMatch

    root = None
    try:
        from django.conf import settings
        override_dir = getattr(settings, "CAULDRON_UI_OVERRIDES_DIR", None)
        if override_dir is None:
            base_dir = getattr(settings, "BASE_DIR", None)
            if base_dir is not None:
                override_dir = Path(base_dir) / "cauldron-overrides"
        if override_dir is not None:
            root = Path(override_dir)
    except Exception:
        return []

    if root is None or not root.is_dir():
        return []

    try:
        from cauldron_django_admin.override_store import UIOverrideStore, OverrideStoreError
        store = UIOverrideStore(root)
        files = store.list_files(scope)
    except Exception:
        return []

    urls = []
    for rel_path in files:
        try:
            url = reverse("cauldron-override-css", kwargs={"scope": scope, "rel_path": rel_path})
            urls.append(url)
        except NoReverseMatch:
            pass
    return urls
