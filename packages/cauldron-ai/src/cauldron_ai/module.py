"""Cauldron AI module definition."""
from cauldron.modules import BaseModule, ModuleManifest

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
)

module = BaseModule(_manifest)
