"""Test configuration for cauldron-module-tree package tests."""
from pathlib import Path


def pytest_configure(config):
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.sessions",
                "django.contrib.messages",
                "django.contrib.staticfiles",
                "django.contrib.admin",
                "cauldron",
                "cauldron_django_state",
                "cauldron_django_auth",
                "cauldron_django_admin",
                "cauldron_module_tree",
            ],
            MIDDLEWARE=[
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
            ],
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "DIRS": [],
                    "APP_DIRS": True,
                    "OPTIONS": {
                        "context_processors": [
                            "django.template.context_processors.request",
                            "django.contrib.auth.context_processors.auth",
                            "django.contrib.messages.context_processors.messages",
                        ],
                        "builtins": [
                            "django.templatetags.static",
                        ],
                    },
                }
            ],
            ROOT_URLCONF="tests.urls_test",
            SESSION_ENGINE="django.contrib.sessions.backends.db",
            AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"],
            CAULDRON_MODULES={
                "cauldron.django.state": {},
                "cauldron.django.auth": {},
                "cauldron.django.admin": {},
                "cauldron.module.tree": {},
            },
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
            SECRET_KEY="test-secret-key-for-module-tree-tests",
            STATIC_URL="/static/",
            STATIC_ROOT="/tmp/cauldron-test-static",
            BASE_DIR=Path(__file__).parent,
        )

        # Pre-register the navigation sections that cauldron_module_tree.navigation
        # expects to exist when its AppConfig.ready() runs.
        #
        # cauldron_django_admin.apps registers the "system" section and the
        # "cauldron.modules" *item* but not a "cauldron.modules" *section*.
        # cauldron_module_tree.navigation.py registers its tree item under the
        # "cauldron.modules" section — so we pre-create it here (idempotently)
        # before django.setup() is called by pytest-django.
        #
        # The navigation module is pure Python and safe to import before Django
        # app setup completes.
        try:
            from cauldron_django_admin.navigation import (
                AdminNavigationSection,
                get_navigation_registry,
            )

            nav_registry = get_navigation_registry()
            try:
                nav_registry.register_section(AdminNavigationSection(
                    key="cauldron.modules",
                    label="Modules",
                    order=50,
                ))
            except ValueError:
                # Already registered — idempotent re-run.
                pass
        except ImportError:
            pass
