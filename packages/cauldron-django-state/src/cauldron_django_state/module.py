"""Cauldron Django State module definition."""
from cauldron.modules import BaseModule, ModuleManifest

_manifest = ModuleManifest(
    slug="cauldron.django.state",
    label="Cauldron Django State",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_django_state",),
    provides=(
        "django.state",
        "django.database",
        "django.transactions",
        "django.migrations",
    ),
    namespaces=("cauldron_django_state",),
    public_api=(
        "cauldron_django_state.checks",
        "cauldron_django_state.config",
    ),
)

module = BaseModule(_manifest)
