"""URL configuration for Cauldron Django Admin."""
from django.contrib import admin
from django.urls import path


def get_admin_urls():
    """Return the Django admin URL patterns."""
    return [path("admin/", admin.site.urls)]
