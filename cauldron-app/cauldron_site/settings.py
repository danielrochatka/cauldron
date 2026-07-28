"""Django settings for the Cauldron self-hosted instance."""
import os
from pathlib import Path

# Base directory is the cauldron-app/ directory (parent of this file's package)
BASE_DIR = Path(__file__).resolve().parent.parent

# Content and workspace paths
WORKSPACE_DIR = BASE_DIR / "data" / "workspace"

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

_secret = os.environ.get("SECRET_KEY", "").strip()
if not _secret:
    # initialize_config in lib.sh fills this before Django starts.
    # If it is still empty the script was bypassed — surface a clear error.
    raise RuntimeError(
        "SECRET_KEY is not set. Run ./start to initialize the installation."
    )
SECRET_KEY = _secret

DEBUG = os.environ.get("CAULDRON_DEBUG", "false").lower() == "true"

_allowed_host = os.environ.get("CAULDRON_HOST", "")
ALLOWED_HOSTS = (
    [h.strip() for h in _allowed_host.split(",") if h.strip()]
    if _allowed_host
    else ["localhost", "127.0.0.1"]
)

# ---------------------------------------------------------------------------
# Cauldron modules
# ---------------------------------------------------------------------------

# AI provider selection (fake = built-in demo, openai = OpenAI Responses API).
# The value is passed as the "provider" key inside CAULDRON_MODULES["cauldron.ai.admin"]
# below.  Whitespace-only values are treated as unset and fall back to "fake"
# so the default installation stays functional without external credentials.
_ai_provider = os.environ.get("CAULDRON_AI_PROVIDER", "fake").strip() or "fake"

# Path to the AI provider configuration file (credentials + runtime overrides).
# The file is created on first save with mode 0600.  Whitespace-only overrides
# collapse to the default path so no accidental empty-string path is honoured.
CAULDRON_AI_CONFIG_PATH = os.environ.get(
    "CAULDRON_AI_CONFIG_PATH",
    str(BASE_DIR / "data" / "ai" / "config.json"),
).strip() or str(BASE_DIR / "data" / "ai" / "config.json")

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
        "site_root": str(BASE_DIR),
    },
    "cauldron.django.state": {},
    "cauldron.django.auth": {},
    "cauldron.django.admin": {},
    "cauldron.content.operations": {
        "require_approval": False,
        "max_operations_per_change_set": 100,
    },
    "cauldron.content.api": {},
    "cauldron.admin.content": {},
    "cauldron.ai": {},
    # OpenAI provider package — factory registered at Django startup by
    # cauldron_ai_openai.apps.CauldronAIOpenAIConfig.ready().
    "cauldron.ai.openai": {},
    # Admin AI: the default "fake" provider is registered by
    # cauldron_site.admin_ai_bootstrap; setting CAULDRON_AI_PROVIDER=openai
    # switches to the OpenAI factory (credentials configured via the AI
    # settings page or OPENAI_* env vars).
    "cauldron.ai.admin": {
        "provider": _ai_provider,
    },
    # Astro static-site builder — generates public HTML from published pages.
    # output_root is served by Django at / and /<slug>/.
    "cauldron.site.astro": {
        "frontend_root": str(BASE_DIR / "frontend"),
        "output_root": str(BASE_DIR / "data" / "public"),
    },
}

# ---------------------------------------------------------------------------
# Django application composition
# ---------------------------------------------------------------------------

from cauldron.django.compose import compose_django_settings

_plan = compose_django_settings(
    installed_apps=[
        "django.contrib.contenttypes",
        "cauldron",
        # Registers the FakeAIModelProvider used by cauldron.ai.admin.
        "cauldron_site.admin_ai_bootstrap.AdminAIBootstrapConfig",
        # Admin AI models/migrations/checks.
        "cauldron_ai_admin",
        # OpenAI provider factory — its Django app is normally injected by
        # the cauldron.ai.openai module manifest, but we list it here
        # explicitly so the AppConfig.ready() hook that registers the
        # factory runs even in environments where the module registry has
        # not yet been populated at process start.
    ],
    middleware=[
        "django.middleware.security.SecurityMiddleware",
        "whitenoise.middleware.WhiteNoiseMiddleware",
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

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "data" / "cauldron.db",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "auth.User"

# ---------------------------------------------------------------------------
# URLs and WSGI
# ---------------------------------------------------------------------------

ROOT_URLCONF = "cauldron_site.urls"
WSGI_APPLICATION = "cauldron_site.wsgi.application"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Static and media files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "data" / "media"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ---------------------------------------------------------------------------
# Cauldron UI override directory
# ---------------------------------------------------------------------------

CAULDRON_UI_OVERRIDES_DIR = str(BASE_DIR / "overrides")

# ---------------------------------------------------------------------------
# Cauldron AI config file path
#
# Override with CAULDRON_AI_CONFIG_PATH env var to use a non-default path.
# The default is BASE_DIR/data/ai/config.json (mode 0600, created on save).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Auth redirects
# ---------------------------------------------------------------------------

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/cauldron/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
