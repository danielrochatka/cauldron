"""Cauldron Admin AI module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.ai.admin",
    label="Cauldron Admin AI",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_ai_admin",),
    requires=(
        ModuleRequirement(slug="ai.model.providers", kind="capability"),
        ModuleRequirement(slug="content.operations", kind="capability"),
        ModuleRequirement(slug="admin.interface", kind="capability"),
        ModuleRequirement(slug="django.state", kind="capability"),
        ModuleRequirement(slug="identity.authentication", kind="capability"),
        ModuleRequirement(slug="identity.permissions", kind="capability"),
    ),
    provides=(
        "admin.ai",
        "admin.ai.orchestration",
        "admin.ai.tools",
        "admin.ai.audit",
        "admin.ai.health",
    ),
)

module = BaseModule(_manifest)
