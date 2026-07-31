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
    namespaces=("cauldron_content",),
    public_api=(
        "cauldron_content.contracts",
        "cauldron_content.pages",
        "cauldron_content.homepage",
        "cauldron_content.registry",
        "cauldron_content.router",
        "cauldron_content.site",
        "cauldron_content.hashing",
    ),
)

module = BaseModule(_manifest)
