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
)

module = BaseModule(_manifest)
