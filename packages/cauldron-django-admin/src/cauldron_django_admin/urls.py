"""URL configuration for Cauldron Django Admin."""
from django.contrib import admin
from django.urls import include, path

from .views import dashboard_view, modules_view
from .override_views import CSSOverrideView

_cauldron_patterns = [
    path("", dashboard_view, name="dashboard"),
    path("modules/", modules_view, name="modules"),
]


def get_admin_urls():
    """Return the Django admin URL patterns."""
    return [path("admin/", admin.site.urls)]


def get_cauldron_urls():
    """Return the Cauldron admin shell URL patterns."""
    return [
        path("cauldron/", include((_cauldron_patterns, "cauldron"))),
        # Site-owned CSS override serving (scoped, nested path support)
        path(
            "cauldron-overrides/<str:scope>/<path:rel_path>",
            CSSOverrideView.as_view(),
            name="cauldron-override-css",
        ),
    ]
