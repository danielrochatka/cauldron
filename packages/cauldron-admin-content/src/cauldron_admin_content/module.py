"""Cauldron Admin Content module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModuleNavigationDeclaration,
    ModuleRequirement,
)

_manifest = ModuleManifest(
    slug="cauldron.admin.content",
    label="Cauldron Admin Content",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_admin_content",),
    requires=(
        ModuleRequirement(slug="content.operations", kind="capability"),
        ModuleRequirement(slug="admin.interface", kind="capability"),
        # admin-content pages extend the Cauldron Admin Shell templates;
        # depend on that capability explicitly so misconfigured deployments
        # fail loudly at resolution time.
        ModuleRequirement(slug="admin.shell", kind="capability"),
        ModuleRequirement(slug="cauldron.django.admin", kind="module"),
        ModuleRequirement(slug="cauldron.content", kind="module"),
        ModuleRequirement(slug="cauldron.content.operations", kind="module"),
        ModuleRequirement(slug="cauldron.workspace.flatfile", kind="module"),
    ),
    provides=(
        "admin.content",
        "admin.content.changerequests",
        "admin.content.audit",
    ),
    namespaces=("cauldron_admin_content",),
    public_api=(
        "cauldron_admin_content.views",
        "cauldron_admin_content.urls",
        "cauldron_admin_content.forms",
        "cauldron_admin_content.service_factory",
    ),
    navigation=(
        ModuleNavigationDeclaration(key="content", label="Content"),
        ModuleNavigationDeclaration(
            key="cauldron.admin.content.browser",
            label="Content Browser",
            section="content",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.admin.content.homepage",
            label="Homepage",
            section="content",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.admin.content.page-create",
            label="New Page",
            section="content",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.admin.content.change-requests",
            label="Change Requests",
            section="content",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.admin.content.audit",
            label="Audit Log",
            section="content",
        ),
    ),
)

module = BaseModule(_manifest)
