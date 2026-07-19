"""Tests for cauldron.django.admin system checks."""
import pytest


def test_full_stack_passes(settings):
    """Full valid configuration → I001 info, no errors."""
    from cauldron_django_admin.checks import check_admin_config

    settings.CAULDRON_MODULES = {
        "cauldron.django.state": {},
        "cauldron.django.auth": {},
        "cauldron.django.admin": {},
    }
    results = check_admin_config(app_configs=None)
    ids = [r.id for r in results]
    assert "cauldron.admin.I001" in ids
    from django.core.checks import Error
    errors = [r for r in results if isinstance(r, Error)]
    assert not errors


def test_missing_messages_app_emits_e300(settings):
    """Missing django.contrib.messages → E300."""
    from cauldron_django_admin.checks import check_admin_config

    settings.CAULDRON_MODULES = {"cauldron.django.admin": {}}
    apps = list(settings.INSTALLED_APPS)
    apps_without_messages = [a for a in apps if a != "django.contrib.messages"]
    settings.INSTALLED_APPS = apps_without_messages

    results = check_admin_config(app_configs=None)
    ids = [r.id for r in results]
    assert "cauldron.admin.E300" in ids


def test_missing_messages_middleware_emits_e301(settings):
    """Missing MessageMiddleware → E301."""
    from cauldron_django_admin.checks import check_admin_config

    settings.CAULDRON_MODULES = {"cauldron.django.admin": {}}
    mw = list(settings.MIDDLEWARE)
    settings.MIDDLEWARE = [
        m for m in mw if m != "django.contrib.messages.middleware.MessageMiddleware"
    ]

    results = check_admin_config(app_configs=None)
    ids = [r.id for r in results]
    assert "cauldron.admin.E301" in ids


def test_missing_messages_context_processor_emits_e302(settings):
    """Missing messages context processor → E302."""
    from cauldron_django_admin.checks import check_admin_config

    settings.CAULDRON_MODULES = {"cauldron.django.admin": {}}
    settings.TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [],
            "APP_DIRS": True,
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.request",
                    "django.contrib.auth.context_processors.auth",
                    # messages context processor intentionally absent
                ]
            },
        }
    ]

    results = check_admin_config(app_configs=None)
    ids = [r.id for r in results]
    assert "cauldron.admin.E302" in ids


def test_admin_not_active_returns_empty(settings):
    """When cauldron.django.admin is not in CAULDRON_MODULES, checks return empty."""
    from cauldron_django_admin.checks import check_admin_config

    settings.CAULDRON_MODULES = {}
    results = check_admin_config(app_configs=None)
    assert results == []
