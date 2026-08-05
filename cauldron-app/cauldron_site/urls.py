"""URL configuration for the Cauldron self-hosted instance."""
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView

from cauldron_django_admin.urls import get_admin_urls, get_cauldron_urls
from cauldron_site.views.public_site import serve_index, serve_page, serve_asset

try:
    from cauldron_module_tree import urls as _tree_urls  # noqa: F401
    _module_tree_urls = [
        path("cauldron/module-tree/", include("cauldron_module_tree.urls", namespace="cauldron_module_tree")),
    ]
except ImportError:
    _module_tree_urls = []

urlpatterns = [
    # Technical admin interface (keep available)
    *get_admin_urls(),
    # Auth routes
    path("accounts/", include("cauldron_django_auth.urls", namespace="cauldron_auth")),
    # Cauldron shell: dashboard + modules
    *get_cauldron_urls(),
    # Cauldron shell: admin content pages
    path("cauldron/", include("cauldron_admin_content.urls", namespace="cauldron_admin_content")),
    # Cauldron shell: AI admin + style proposals
    path("cauldron/", include("cauldron_ai_admin.urls", namespace="cauldron_ai_admin")),
    # Cauldron shell: module dependency tree (optional — installed separately)
    *_module_tree_urls,
    # Cauldron shell: site preview server (authenticated, per-change-set)
    path("cauldron/", include("cauldron_site_astro.urls", namespace="cauldron_site_astro")),
    # Content API
    path("cauldron/api/v1/", include("cauldron_content_api.urls")),
    # Public site — MUST be last
    # Top-level generated files with extensions: theme.css, favicon.ico, robots.txt, etc.
    # Must come before the bare-slug redirect so /theme.css is served directly.
    re_path(r'^(?P<asset_path>[^/]+\.[^/]+)$', serve_asset, name="public-top-asset"),
    # Nested generated assets: _astro/chunk.js, images/hero.png, etc.
    re_path(r'^(?P<asset_path>[^/]+(?:/[^/]+)+)$', serve_asset, name="public-asset"),
    # Bare slug redirect: /about → /about/
    re_path(r'^(?P<slug>[^/]+)$', RedirectView.as_view(url='/%(slug)s/', permanent=False)),
    path("<slug:slug>/", serve_page, name="public-page"),
    path("", serve_index, name="public-index"),
]
