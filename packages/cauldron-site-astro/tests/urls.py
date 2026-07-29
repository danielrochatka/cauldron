"""Test URL configuration for cauldron-site-astro.

Mounts the package's preview URLs at ``/site/`` so URL reversal (used by
:meth:`SiteChangeSet.get_preview_url`) works inside the test settings
without pulling in an admin project's full URLconf.
"""
from django.urls import include, path

urlpatterns = [
    path("site/", include("cauldron_site_astro.urls")),
]
