"""Cauldron flat-file CMS module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModulePresentation, ModuleRequirement, ModuleSettingsDeclaration

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>"""

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
    namespaces=("cauldron_cms_flatfile",),
    public_api=(
        "cauldron_cms_flatfile.repository",
        "cauldron_cms_flatfile.config",
        "cauldron_cms_flatfile.validator",
        "cauldron_cms_flatfile.parser",
    ),
    settings_declarations=(
        ModuleSettingsDeclaration(
            key="site_root",
            required=True,
            description="Absolute path to the flat-file site root directory.",
        ),
        ModuleSettingsDeclaration(
            key="content_root",
            required=False,
            description="Path to content directory relative to site_root. Defaults to 'content'.",
        ),
        ModuleSettingsDeclaration(
            key="schema_root",
            required=False,
            description="Path to schema directory relative to site_root. Defaults to 'schemas'.",
        ),
    ),
    presentation=ModulePresentation(
        title="Flat-File CMS",
        summary="Flat-file CMS backend — reads and writes content pages as Markdown + YAML frontmatter.",
        icon_svg=_ICON_SVG,
        group="Integration",
        display_order=10,
    ),
)

module = BaseModule(_manifest)
