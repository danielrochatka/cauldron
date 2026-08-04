"""URL routes exported by Cauldron for consuming Django projects."""

from django.urls import path

from .views import health, module_inventory

app_name = "cauldron"

urlpatterns = [
    path("health/", health, name="health"),
    path("modules/", module_inventory, name="modules-api"),
]
