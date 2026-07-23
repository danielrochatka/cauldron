"""Test configuration for cauldron-admin-content."""
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
                "cauldron_admin_content",
            ],
            MIDDLEWARE=[
                "django.contrib.sessions.middleware.SessionMiddleware",
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
                "cauldron.admin.content": {},
            },
            SECRET_KEY="test-secret-key-for-admin-tests",
            ROOT_URLCONF="django.contrib.admin.sites",
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
