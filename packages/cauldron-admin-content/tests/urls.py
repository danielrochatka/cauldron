"""Test URL configuration for cauldron-admin-content."""
from django.contrib import admin
from django.urls import include, path

from cauldron_django_admin.urls import get_cauldron_urls

urlpatterns = [
    path("admin/", admin.site.urls),
    path("cauldron-admin/", include("cauldron_admin_content.urls")),
    *get_cauldron_urls(),
]
