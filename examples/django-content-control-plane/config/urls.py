"""URL configuration for the content control plane example."""
from django.contrib import admin
from django.urls import include, path

from cauldron_django_admin.urls import get_admin_urls, get_cauldron_urls

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
    # Content API
    path("cauldron/api/v1/", include("cauldron_content_api.urls")),
]
