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
    namespaces=("cauldron_workspace_flatfile",),
    public_api=(
        "cauldron_workspace_flatfile.store",
        "cauldron_workspace_flatfile.config",
        "cauldron_workspace_flatfile.reversible",
        "cauldron_workspace_flatfile.snapshots",
    ),
)

module = BaseModule(_manifest)
