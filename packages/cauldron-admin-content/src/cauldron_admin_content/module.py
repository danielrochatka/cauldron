"""Cauldron Admin Content module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.admin.content",
    label="Cauldron Admin Content",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_admin_content",),
    requires=(
        ModuleRequirement(slug="content.operations", kind="capability"),
        ModuleRequirement(slug="admin.interface", kind="capability"),
    ),
    provides=(
        "admin.content",
        "admin.content.changerequests",
        "admin.content.audit",
    ),
)

module = BaseModule(_manifest)
