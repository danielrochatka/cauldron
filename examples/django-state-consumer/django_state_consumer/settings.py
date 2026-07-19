"""Settings for the django-state-consumer example app."""
from pathlib import Path

from cauldron.django import compose_django_settings

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "dev-secret-key-not-for-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

CAULDRON_MODULES = {
    "cauldron.django.state": {"database_alias": "default"},
    "cauldron.django.auth": {},
    "cauldron.django.admin": {},
}
CAULDRON_CAPABILITY_PROVIDERS = {}

BASE_INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "cauldron",
]
BASE_MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
BASE_CONTEXT_PROCESSORS = [
    "django.template.context_processors.debug",
    "django.template.context_processors.request",
]

plan = compose_django_settings(
    installed_apps=BASE_INSTALLED_APPS,
    middleware=BASE_MIDDLEWARE,
    context_processors=BASE_CONTEXT_PROCESSORS,
    module_settings=CAULDRON_MODULES,
    capability_providers=CAULDRON_CAPABILITY_PROVIDERS,
)

INSTALLED_APPS = list(plan.installed_apps)
MIDDLEWARE = list(plan.middleware)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": list(plan.context_processors)},
    }
]

ROOT_URLCONF = "django_state_consumer.urls"
WSGI_APPLICATION = "django_state_consumer.wsgi.application"
AUTH_USER_MODEL = "auth.User"
STATIC_URL = "/static/"
LOGIN_URL = "/auth/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
EMAIL_BACKEND = "django.core.mail.backends.console.ConsoleEmailBackend"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
