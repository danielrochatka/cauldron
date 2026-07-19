from cauldron.modules.discovery import get_module_apps

SECRET_KEY = "tests"
ROOT_URLCONF = "tests.urls"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

# Explicitly enable fixture modules for the test suite.
CAULDRON_MODULES = {
    "cauldron.fixture.alpha": {},
    "cauldron.fixture.beta": {},
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "cauldron",
    *get_module_apps(CAULDRON_MODULES),
]
