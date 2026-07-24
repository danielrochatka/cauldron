"""URL configuration for cauldron-django-admin tests."""
from django.contrib import admin
from django.urls import include, path

from cauldron_django_admin.urls import get_cauldron_urls

urlpatterns = [
    path("auth/", include("cauldron_django_auth.urls", namespace="cauldron_auth")),
    path("admin/", admin.site.urls),
    *get_cauldron_urls(),
]
