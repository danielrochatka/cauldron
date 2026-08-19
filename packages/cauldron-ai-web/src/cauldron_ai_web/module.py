"""Cauldron AI Web module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModulePresentation,
    ModuleRequirement,
)

_manifest = ModuleManifest(
    slug="cauldron.ai.web",
    label="Cauldron AI Web",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_ai_web",),
    requires=(),
    optional=(
        ModuleRequirement(slug="cauldron.ai", kind="module"),
        ModuleRequirement(slug="cauldron.ai.admin", kind="module"),
    ),
    provides=("admin.ai.web.research",),
    namespaces=("cauldron_ai_web",),
    public_api=(
        "cauldron_ai_web.module",
        "cauldron_ai_web.fetcher",
        "cauldron_ai_web.analyzer",
        "cauldron_ai_web.tools",
    ),
    ai_tools=("web.inspect_url",),
    prompt_templates=("web.inspect_url",),
    presentation=ModulePresentation(
        title="AI Web Research",
        summary="Safe reference-website inspection for Admin AI — fetches public URLs with SSRF protection and extracts design characteristics.",
        group="AI",
        display_order=42,
    ),
)

module = BaseModule(_manifest)
