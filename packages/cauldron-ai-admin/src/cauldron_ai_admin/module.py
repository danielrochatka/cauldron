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
        # Admin AI views/templates depend on the shell chrome and CSS
        # override plumbing. Declare it explicitly so resolution fails if
        # the shell is not present in the deployment.
        ModuleRequirement(slug="admin.shell", kind="capability"),
        ModuleRequirement(slug="django.state", kind="capability"),
        ModuleRequirement(slug="identity.authentication", kind="capability"),
        ModuleRequirement(slug="identity.permissions", kind="capability"),
        ModuleRequirement(slug="cauldron.admin.content", kind="module"),
    ),
    optional=(
        ModuleRequirement(slug="cauldron.cms.flatfile", kind="module"),
    ),
    provides=(
        "admin.ai",
        "admin.ai.orchestration",
        "admin.ai.tools",
        "admin.ai.audit",
        "admin.ai.health",
    ),
    namespaces=("cauldron_ai_admin",),
    public_api=(
        "cauldron_ai_admin.tools",
        "cauldron_ai_admin.views",
        "cauldron_ai_admin.service",
        "cauldron_ai_admin.builtin_tools",
    ),
)

module = BaseModule(_manifest)
