"""Test configuration for cauldron-ai-admin."""
from django.conf import settings


def pytest_configure(config):
    if not settings.configured:
        settings.configure(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                    # Longer busy timeout so concurrent transactions have a
                    # chance to serialise (SQLite retries under contention)
                    # instead of failing immediately with "database is
                    # locked".
                    "OPTIONS": {"timeout": 20},
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
                "cauldron_cms_flatfile",
                "cauldron_workspace_flatfile",
                "cauldron_content_operations",
                "cauldron_django_admin",
                "cauldron_ai_admin",
                "cauldron_site_astro",
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
                    "require_approval": False,
                    "max_operations_per_change_set": 100,
                },
                "cauldron.ai.admin": {},
            },
            SECRET_KEY="test-secret-key-for-admin-ai-tests",
            ROOT_URLCONF="tests.test_urls_with_shell",
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
