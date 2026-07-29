"""Test configuration for cauldron-site-astro package tests."""


def pytest_configure(config):
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "cauldron_site_astro",
            ],
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": "/tmp/nonexistent_frontend",
                    "output_root": "/tmp/cauldron_test_output",
                }
            },
            ROOT_URLCONF="tests.urls",
            SECRET_KEY="test-key",
        )
