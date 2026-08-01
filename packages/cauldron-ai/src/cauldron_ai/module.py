"""Cauldron AI module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ProvidedCapability

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
)

module = BaseModule(_manifest)
