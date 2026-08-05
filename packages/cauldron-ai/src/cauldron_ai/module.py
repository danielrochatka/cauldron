"""Cauldron AI module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModulePresentation, ProvidedCapability

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a5 5 0 0 1 5 5c0 1.88-.65 3.47-1.72 4.72L12 14l-3.28-2.28A6.96 6.96 0 0 1 7 7a5 5 0 0 1 5-5z"/><path d="M12 14v8"/><path d="M8 22h8"/><path d="M9 11.5l-5 3"/><path d="M15 11.5l5 3"/></svg>"""

_manifest = ModuleManifest(
    slug="cauldron.ai",
    label="Cauldron AI",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=(),
    provides=(
        "ai.model.contracts",
        "ai.model.providers",
        "ai.toolcalling",
    ),
    namespaces=("cauldron_ai",),
    public_api=(
        "cauldron_ai.contracts",
        "cauldron_ai.providers",
        "cauldron_ai.prompt_templates",
        "cauldron_ai.provider_configuration",
        "cauldron_ai.testing",
    ),
    provided_capabilities=(
        ProvidedCapability(
            slug="ai.model.contracts",
            description="Typed contracts for AI model requests, responses, and tool definitions.",
        ),
        ProvidedCapability(
            slug="ai.model.providers",
            description="Registry of AI model provider adapters for tool-calling pipelines.",
        ),
        ProvidedCapability(
            slug="ai.toolcalling",
            description="Tool-calling orchestration and prompt template registry.",
        ),
    ),
    presentation=ModulePresentation(
        title="AI",
        summary="AI foundation — model contracts, provider registry, and shared AI infrastructure.",
        icon_svg=_ICON_SVG,
        group="AI",
        display_order=10,
    ),
)

module = BaseModule(_manifest)
