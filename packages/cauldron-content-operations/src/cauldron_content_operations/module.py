"""Cauldron Content Operations module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.content.operations",
    label="Cauldron Content Operations",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_content_operations",),
    requires=(
        ModuleRequirement(slug="content.routing", kind="capability"),
        ModuleRequirement(slug="content.changesets", kind="capability"),
        ModuleRequirement(slug="workspace.changesets", kind="capability"),
        ModuleRequirement(slug="workspace.snapshots", kind="capability"),
        ModuleRequirement(slug="django.state", kind="capability"),
        ModuleRequirement(slug="identity.authentication", kind="capability"),
        ModuleRequirement(slug="identity.permissions", kind="capability"),
    ),
    provides=(
        "content.operations",
        "content.authorization",
        "content.approvals",
        "content.audit",
        "content.reconciliation",
    ),
)

module = BaseModule(_manifest)
