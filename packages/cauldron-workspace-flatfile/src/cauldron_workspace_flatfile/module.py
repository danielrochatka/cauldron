"""Cauldron flat-file workspace module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModulePresentation, ModuleRequirement, ModuleSettingsDeclaration

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>"""

_manifest = ModuleManifest(
    slug="cauldron.workspace.flatfile",
    label="Cauldron Flat-File Workspace",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_workspace_flatfile",),
    requires=(
        ModuleRequirement(slug="cauldron.content"),
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
    presentation=ModulePresentation(
        title="Flat-File Workspace",
        summary="Flat-file workspace provider — maps content change sets to the local filesystem.",
        icon_svg=_ICON_SVG,
        group="Integration",
        display_order=20,
    ),
)

module = BaseModule(_manifest)
