"""Django settings for the content control plane example."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONTENT_DIR = BASE_DIR / "site" / "content"
SCHEMAS_DIR = BASE_DIR / "site" / "schemas"
WORKSPACE_DIR = BASE_DIR / "site" / ".cauldron" / "workspace"

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-secret-key-change-in-production")
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

from cauldron.django.compose import compose_django_settings

CAULDRON_MODULES = {
    "cauldron.content": {
        "routing": {
            "default_provider": "flatfile",
            "collections": {},
        },
    },
    "cauldron.workspace.flatfile": {
        "workspace_root": str(WORKSPACE_DIR),
    },
    "cauldron.cms.flatfile": {
        "content_root": str(CONTENT_DIR),
        "schemas_root": str(SCHEMAS_DIR),
    },
    "cauldron.django.state": {},
    "cauldron.django.auth": {},
    "cauldron.django.admin": {},
    "cauldron.content.operations": {
        "require_approval": True,
        "allow_self_approval": False,
        "max_operations_per_change_set": 100,
    },
    "cauldron.content.api": {},
    "cauldron.admin.content": {},
    "cauldron.ai": {},
    # Admin AI: uses the deterministic FakeAIModelProvider that
    # config.admin_ai_bootstrap registers in AppConfig.ready(). Real
    # deployments configure a vendor provider package instead.
    "cauldron.ai.admin": {
        "provider": "fake",
        "max_model_turns": 3,
        "max_tool_calls": 5,
        "tool_timeout_seconds": 10,
        "run_timeout_seconds": 30,
        "max_argument_bytes": 4096,
        "max_result_bytes": 8192,
    },
}

_plan = compose_django_settings(
    installed_apps=[
        "django.contrib.contenttypes",
        "cauldron",
        # Registers the FakeAIModelProvider used by cauldron.ai.admin.
        "config.admin_ai_bootstrap.AdminAIBootstrapConfig",
        # Admin AI models/migrations/checks.
        "cauldron_ai_admin",
    ],
    middleware=[
        "django.middleware.security.SecurityMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
    ],
    context_processors=[
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
    ],
    module_settings=CAULDRON_MODULES,
)

INSTALLED_APPS = list(_plan.installed_apps)

MIDDLEWARE = list(_plan.middleware)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "auth.User"

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": list(_plan.context_processors),
        },
    },
]

STATIC_URL = "/static/"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/admin/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
