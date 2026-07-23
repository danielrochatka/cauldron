"""Test configuration for cauldron-content-operations."""
from django.conf import settings


def pytest_configure(config):
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
                "cauldron_content",
                "cauldron_workspace_flatfile",
                "cauldron_content_operations",
            ],
            MIDDLEWARE=[
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
            ],
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            AUTH_USER_MODEL="auth.User",
            CAULDRON_MODULES={
                "cauldron.content.operations": {
                    "require_approval": True,
                    "allow_self_approval": False,
                    "max_operations_per_change_set": 100,
                },
            },
            USE_TZ=True,
        )
