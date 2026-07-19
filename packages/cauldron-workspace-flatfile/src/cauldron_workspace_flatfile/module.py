"""Cauldron flat-file workspace module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.workspace.flatfile",
    label="Cauldron Flat-File Workspace",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_workspace_flatfile",),
    requires=(ModuleRequirement(slug="cauldron.content"),),
    provides=(
        "workspace.flatfile",
        "workspace.changesets",
        "workspace.snapshots",
        "workspace.preview",
    ),
)

module = BaseModule(_manifest)
