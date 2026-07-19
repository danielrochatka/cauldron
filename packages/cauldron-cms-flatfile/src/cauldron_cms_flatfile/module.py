"""Cauldron flat-file CMS module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.cms.flatfile",
    label="Cauldron Flat-File CMS",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_cms_flatfile",),
    requires=(ModuleRequirement(slug="cauldron.content"),),
    optional=(ModuleRequirement(slug="cauldron.workspace.flatfile"),),
    provides=(
        "content.provider.flatfile",
        "content.storage.flatfile",
        "content.schemas.jsonschema",
        "content.markdown",
        "content.publishing.flatfile",
    ),
)

module = BaseModule(_manifest)
