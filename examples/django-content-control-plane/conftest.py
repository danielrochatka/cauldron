"""Pytest configuration for the content control plane example."""
import django
from django.conf import settings


def pytest_configure(config):
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    if not settings.configured:
        django.setup()
