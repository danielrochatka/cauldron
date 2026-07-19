"""Test configuration for cauldron-django-state package tests."""


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
                "cauldron",
                "cauldron_django_state",
            ],
            CAULDRON_MODULES={"cauldron.django.state": {}},
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
        )
