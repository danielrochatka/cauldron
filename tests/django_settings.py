SECRET_KEY = "tests"
ROOT_URLCONF = "tests.urls"
INSTALLED_APPS = ["django.contrib.contenttypes", "cauldron"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
