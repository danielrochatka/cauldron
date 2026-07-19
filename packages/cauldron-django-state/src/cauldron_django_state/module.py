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
)

module = BaseModule(_manifest)
