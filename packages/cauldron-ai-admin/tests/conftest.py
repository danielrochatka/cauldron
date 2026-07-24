"""Test configuration for cauldron-ai-admin."""
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
                "django.contrib.messages",
                "django.contrib.admin",
                "django.contrib.staticfiles",
                "cauldron_content",
                "cauldron_workspace_flatfile",
                "cauldron_content_operations",
                "cauldron_ai_admin",
            ],
            MIDDLEWARE=[
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.middleware.csrf.CsrfViewMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
            ],
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            AUTH_USER_MODEL="auth.User",
            CAULDRON_MODULES={
                "cauldron.content.operations": {
                    "require_approval": True,
                    "allow_self_approval": False,
                    "max_operations_per_change_set": 100,
                },
                "cauldron.ai.admin": {},
            },
            SECRET_KEY="test-secret-key-for-admin-ai-tests",
            ROOT_URLCONF="tests.test_urls",
            STATIC_URL="/static/",
            TEMPLATES=[{
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.debug",
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                    ],
                },
            }],
            USE_TZ=True,
        )
