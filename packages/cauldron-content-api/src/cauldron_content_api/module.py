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
        ModuleRequirement(slug="cauldron.content", kind="module"),
        ModuleRequirement(slug="cauldron.workspace.flatfile", kind="module"),
    ),
    provides=(
        "content.httpapi",
        "content.httpapi.v1",
    ),
    namespaces=("cauldron_content_api",),
    public_api=(
        "cauldron_content_api.views",
        "cauldron_content_api.urls",
    ),
)

module = BaseModule(_manifest)
