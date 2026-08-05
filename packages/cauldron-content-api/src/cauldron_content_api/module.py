"""Cauldron Content API module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModulePresentation, ModuleRequirement

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>"""

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
        ModuleRequirement(slug="cauldron.content.operations", kind="module"),
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
    presentation=ModulePresentation(
        title="Content API",
        summary="REST API for content access and change-set management by external consumers.",
        icon_svg=_ICON_SVG,
        group="Content",
        display_order=30,
    ),
)

module = BaseModule(_manifest)
