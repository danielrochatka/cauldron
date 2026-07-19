"""Cauldron Content module definition."""
from cauldron.modules import BaseModule, ModuleManifest

_manifest = ModuleManifest(
    slug="cauldron.content",
    label="Cauldron Content",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_content",),
    provides=(
        "content.contracts",
        "content.registry",
        "content.routing",
        "content.changesets",
        "content.validation",
    ),
)

module = BaseModule(_manifest)
