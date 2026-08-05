"""URL routes exported by Cauldron for consuming Django projects."""

from django.urls import path

from .views import health

app_name = "cauldron"

urlpatterns = [path("health/", health, name="health")]
