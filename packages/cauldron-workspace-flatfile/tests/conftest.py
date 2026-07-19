"""Test configuration for cauldron-workspace-flatfile package tests."""


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
                "cauldron_content",
                "cauldron_workspace_flatfile",
            ],
            CAULDRON_MODULES={
                "cauldron.content": {},
                "cauldron.workspace.flatfile": {},
            },
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
        )
