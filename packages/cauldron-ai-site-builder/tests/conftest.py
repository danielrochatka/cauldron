"""Test configuration for cauldron-ai-site-builder acceptance tests."""
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
                "cauldron_ai_admin",
                "cauldron_ai_attachments",
                "cauldron_ai_web",
                "cauldron_ai_site_builder",
            ],
            SECRET_KEY="test-secret-key-site-builder",
            USE_TZ=True,
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        )
