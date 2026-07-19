"""URL configuration for django-state-consumer example."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("auth/", include("cauldron_django_auth.urls", namespace="cauldron_auth")),
    path("admin/", admin.site.urls),
]
