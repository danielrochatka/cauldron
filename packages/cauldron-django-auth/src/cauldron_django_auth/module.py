"""Cauldron Django Auth module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.django.auth",
    label="Cauldron Django Auth",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=(
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "django.contrib.sessions",
        "cauldron_django_auth",
    ),
    django_middleware=(
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ),
    django_context_processors=(
        "django.contrib.auth.context_processors.auth",
    ),
    requires=(ModuleRequirement(slug="cauldron.django.state"),),
    provides=(
        "identity.users",
        "identity.roles",
        "identity.permissions",
        "identity.sessions",
        "identity.authentication",
        "identity.password.reset",
    ),
)

module = BaseModule(_manifest)
