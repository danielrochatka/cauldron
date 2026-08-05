"""Cauldron Content module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModulePresentation, ModuleSettingsDeclaration

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>"""

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
        "cauldron_content.reversible",
    ),
    settings_declarations=(
        ModuleSettingsDeclaration(
            key="routing",
            required=True,
            description="Content routing configuration. Maps URL patterns to content collections.",
        ),
    ),
    presentation=ModulePresentation(
        title="Content",
        summary="Core content model — routing, change sets, and the publishing lifecycle.",
        icon_svg=_ICON_SVG,
        group="Content",
        display_order=10,
    ),
)

module = BaseModule(_manifest)
