"""Cauldron AI Site Builder module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModulePresentation,
    ModuleRequirement,
)

_manifest = ModuleManifest(
    slug="cauldron.ai.sitebuilder",
    label="Cauldron AI Site Builder",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_ai_site_builder",),
    requires=(),
    optional=(
        ModuleRequirement(slug="cauldron.ai", kind="module"),
        ModuleRequirement(slug="cauldron.ai.admin", kind="module"),
        ModuleRequirement(slug="cauldron.ai.attachments", kind="module"),
        ModuleRequirement(slug="cauldron.ai.web", kind="module"),
    ),
    provides=("admin.ai.sitebuilder",),
    namespaces=("cauldron_ai_site_builder",),
    public_api=(
        "cauldron_ai_site_builder.module",
        "cauldron_ai_site_builder.checks",
    ),
    presentation=ModulePresentation(
        title="AI Site Builder",
        summary=(
            "Site-builder orchestration — coordinates attachment ingestion, "
            "web research, and site content/style proposals for Admin AI."
        ),
        group="AI",
        display_order=43,
    ),
)

module = BaseModule(_manifest)
