"""Cauldron module definition for the OpenAI provider."""
from __future__ import annotations

from cauldron.modules import BaseModule, ModuleManifest, ModulePresentation, ModuleRequirement

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>"""

_manifest = ModuleManifest(
    slug="cauldron.ai.openai",
    label="Cauldron AI — OpenAI",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_ai_openai",),
    requires=(
        ModuleRequirement(slug="cauldron.ai", kind="module"),
    ),
    provides=(
        "ai.provider.openai",
    ),
    namespaces=("cauldron_ai_openai",),
    public_api=(
        "cauldron_ai_openai.provider",
    ),
    presentation=ModulePresentation(
        title="AI — OpenAI",
        summary="OpenAI provider — connects the AI infrastructure to OpenAI's language model APIs.",
        icon_svg=_ICON_SVG,
        group="AI",
        display_order=20,
    ),
)

module = BaseModule(_manifest)
