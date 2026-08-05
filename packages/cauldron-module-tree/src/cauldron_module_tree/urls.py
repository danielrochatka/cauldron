"""URL patterns for the cauldron_module_tree package."""
from django.urls import path, re_path

from . import views

app_name = "cauldron_module_tree"

# Cauldron module slugs use dotted notation (e.g. "cauldron.django.admin").
# Django's built-in <slug:> converter rejects periods, so we use a regex that
# matches the canonical module-slug alphabet: [A-Za-z0-9][A-Za-z0-9._-]*.
_MOD_SLUG = r"(?P<module_slug>[A-Za-z0-9][A-Za-z0-9._-]*)"

urlpatterns = [
    path("", views.tree_view, name="tree"),
    path("api/graph/", views.graph_api, name="graph_api"),
    re_path(rf"^api/modules/{_MOD_SLUG}/preview-change/$", views.preview_change, name="preview_change"),
    re_path(rf"^api/modules/{_MOD_SLUG}/enable/$", views.enable_module, name="enable_module"),
    re_path(rf"^api/modules/{_MOD_SLUG}/disable/$", views.disable_module, name="disable_module"),
]
