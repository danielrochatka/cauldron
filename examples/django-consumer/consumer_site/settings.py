SECRET_KEY = "example-only-not-for-production"
DEBUG = True
ROOT_URLCONF = "consumer_site.urls"
INSTALLED_APPS = ["django.contrib.contenttypes", "cauldron"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
