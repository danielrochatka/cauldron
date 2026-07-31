"""Cauldron Site Astro module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.site.astro",
    label="Cauldron Site Astro",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_site_astro",),
    requires=(
        ModuleRequirement(slug="content.routing", kind="capability"),
        ModuleRequirement(slug="cauldron.content", kind="module"),
        ModuleRequirement(slug="cauldron.content.operations", kind="module"),
    ),
    optional=(
        ModuleRequirement(slug="cauldron.ai.admin"),
        ModuleRequirement(slug="cauldron.admin.content"),
        ModuleRequirement(slug="cauldron.ai", kind="module"),
    ),
    provides=("site.public", "site.build"),
    namespaces=("cauldron_site_astro",),
    public_api=(
        "cauldron_site_astro.service",
        "cauldron_site_astro.config",
        "cauldron_site_astro.public_url",
        "cauldron_site_astro.site_tools",
        "cauldron_site_astro.urls",
    ),
    capability_implementations=(
        # Concrete SitePublicUrlProvider — external callers use
        # cauldron_content.site.get_public_url() not this class directly.
        "cauldron_site_astro.public_url",
    ),
)

module = BaseModule(_manifest)
