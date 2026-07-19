"""Test configuration for cauldron-django-admin package tests."""


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
                        ]
                    },
                }
            ],
            ROOT_URLCONF="urls_admin",
            SESSION_ENGINE="django.contrib.sessions.backends.db",
            AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"],
            CAULDRON_MODULES={
                "cauldron.django.state": {},
                "cauldron.django.auth": {},
                "cauldron.django.admin": {},
            },
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
            SECRET_KEY="test-secret-key-for-admin-tests",
            STATIC_URL="/static/",
        )
