"""Cauldron module definition for the OpenAI provider."""
from __future__ import annotations

from cauldron.modules import BaseModule, ModuleManifest

_manifest = ModuleManifest(
    slug="cauldron.ai.openai",
    label="Cauldron AI — OpenAI",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_ai_openai",),
    provides=(
        "ai.provider.openai",
    ),
)

module = BaseModule(_manifest)
