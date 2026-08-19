"""Test configuration for cauldron-ai-attachments."""


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
                "cauldron_ai_attachments",
                "cauldron_ai_admin",
            ],
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            AUTH_USER_MODEL="auth.User",
            CAULDRON_MODULES={
                "cauldron.ai.attachments": {},
                "cauldron.ai.admin": {},
            },
            SECRET_KEY="test-secret-key-attachments",
        )
