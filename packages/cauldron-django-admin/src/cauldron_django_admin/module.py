"""Cauldron Django Admin module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.django.admin",
    label="Cauldron Django Admin",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=(
        "django.contrib.messages",
        "django.contrib.staticfiles",
        "django.contrib.admin",
        "cauldron_django_admin",
    ),
    django_middleware=(
        "django.contrib.messages.middleware.MessageMiddleware",
    ),
    django_context_processors=(
        "django.contrib.messages.context_processors.messages",
    ),
    requires=(ModuleRequirement(slug="cauldron.django.auth"),),
    provides=(
        "admin.interface",
        "admin.users",
        "admin.roles",
        "admin.permissions",
    ),
)

module = BaseModule(_manifest)
