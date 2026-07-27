"""Minimal URL configuration for public-site routing tests.

Only includes the public-site routes so tests can override ROOT_URLCONF
without pulling in admin, auth, and shell routes that require additional
setup (database, migrations, etc.).
"""
from django.urls import path, re_path
from django.views.generic import RedirectView

from cauldron_site.views.public_site import serve_index, serve_page

urlpatterns = [
    # Trailing-slash redirect for slugs: /about → /about/
    re_path(r'^(?P<slug>[^/]+)$', RedirectView.as_view(url='/%(slug)s/', permanent=False)),
    path("<slug:slug>/", serve_page, name="public-page"),
    path("", serve_index, name="public-index"),
]
