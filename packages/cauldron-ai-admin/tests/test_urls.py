"""Test URLconf that mounts the cauldron_ai_admin URLs with the namespace."""
from django.urls import include, path

urlpatterns = [
    path("", include("cauldron_ai_admin.urls", namespace="cauldron_ai_admin")),
]
