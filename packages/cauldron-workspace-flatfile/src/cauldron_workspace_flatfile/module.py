"""Cauldron flat-file workspace module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement, ModuleSettingsDeclaration

_manifest = ModuleManifest(
    slug="cauldron.workspace.flatfile",
    label="Cauldron Flat-File Workspace",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_workspace_flatfile",),
    requires=(
        ModuleRequirement(slug="cauldron.content"),
        ModuleRequirement(slug="cauldron.content.operations", kind="module"),
    ),
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
    settings_declarations=(
        ModuleSettingsDeclaration(
            key="workspace_root",
            required=False,
            description="Absolute path to the flat-file workspace directory. Enables reversible mutations when set.",
        ),
    ),
)

module = BaseModule(_manifest)
