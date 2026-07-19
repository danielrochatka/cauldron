"""URL configuration used by the cauldron-django-auth test suite."""
from django.urls import include, path

urlpatterns = [
    path("auth/", include("cauldron_django_auth.urls", namespace="cauldron_auth")),
]
