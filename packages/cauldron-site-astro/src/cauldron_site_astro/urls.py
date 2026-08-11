"""URL patterns for cauldron_site_astro."""
from django.urls import path, re_path

from . import views

app_name = "cauldron_site_astro"

urlpatterns = [
    path(
        "preview/<str:change_set_id>/",
        views.PreviewServeView.as_view(),
        name="preview-home",
    ),
    re_path(
        r"^preview/(?P<change_set_id>[0-9a-f-]{36})/(?P<path>.+)$",
        views.PreviewServeView.as_view(),
        name="preview-page",
    ),
    path(
        "style-prepare/<str:request_id>/",
        views.StylePublicationPrepareView.as_view(),
        name="style-prepare",
    ),
]
