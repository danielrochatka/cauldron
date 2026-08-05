"""URL patterns for the cauldron_module_tree package."""
from django.urls import path

from . import views

app_name = "cauldron_module_tree"

urlpatterns = [
    path("", views.tree_view, name="tree"),
    path("api/graph/", views.graph_api, name="graph_api"),
    path("api/modules/<slug:module_slug>/preview-change/", views.preview_change, name="preview_change"),
    path("api/modules/<slug:module_slug>/enable/", views.enable_module, name="enable_module"),
    path("api/modules/<slug:module_slug>/disable/", views.disable_module, name="disable_module"),
]
