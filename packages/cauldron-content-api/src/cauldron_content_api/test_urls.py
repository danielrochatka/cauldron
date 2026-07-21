"""URL configuration for the cauldron-content-api test suite."""
from django.urls import include, path

urlpatterns = [
    path("", include("cauldron_content_api.urls")),
]
