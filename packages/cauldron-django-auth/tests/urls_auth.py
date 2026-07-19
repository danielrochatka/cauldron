"""URL configuration for cauldron-django-auth tests."""
from django.urls import include, path

urlpatterns = [
    path("auth/", include("cauldron_django_auth.urls", namespace="cauldron_auth")),
]
