"""URL configuration for the content control plane example."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("cauldron_django_auth.urls")),
    path("cauldron/api/v1/", include("cauldron_content_api.urls")),
]
