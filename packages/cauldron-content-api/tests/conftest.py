"""Test configuration for cauldron-content-api."""
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
                "django.contrib.sessions",
                "cauldron_content",
                "cauldron_workspace_flatfile",
                "cauldron_content_operations",
                "cauldron_content_api",
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
                "cauldron.content.api": {},
            },
            ROOT_URLCONF="cauldron_content_api.test_urls",
            SECRET_KEY="test-secret-key-for-api-tests",
            USE_TZ=True,
        )
