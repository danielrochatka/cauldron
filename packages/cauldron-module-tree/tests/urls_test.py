"""Test URL configuration."""
from django.contrib import admin
from django.urls import include, path

from cauldron_django_admin.urls import get_admin_urls, get_cauldron_urls
from cauldron_module_tree.urls import urlpatterns as tree_urls

urlpatterns = [
    *get_admin_urls(),
    *get_cauldron_urls(),
    path("cauldron/module-tree/", include(("cauldron_module_tree.urls", "cauldron_module_tree"))),
]
