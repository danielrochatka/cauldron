"""Cauldron Content API module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.content.api",
    label="Cauldron Content API",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_content_api",),
    requires=(
        ModuleRequirement(slug="content.operations", kind="capability"),
        ModuleRequirement(slug="identity.authentication", kind="capability"),
    ),
    provides=(
        "content.httpapi",
        "content.httpapi.v1",
    ),
)

module = BaseModule(_manifest)
