"""Cauldron Content Operations module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModuleMigrationDeclaration,
    ModulePermissionDeclaration,
    ModuleRequirement,
)

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
        ModuleRequirement(slug="cauldron.content", kind="module"),
        ModuleRequirement(slug="cauldron.workspace.flatfile", kind="module"),
    ),
    provides=(
        "content.operations",
        "content.authorization",
        "content.approvals",
        "content.audit",
        "content.reconciliation",
    ),
    namespaces=("cauldron_content_operations",),
    public_api=(
        "cauldron_content_operations.service",
        "cauldron_content_operations.config",
        "cauldron_content_operations.lifecycle",
        "cauldron_content_operations.models",
        "cauldron_content_operations.reversible",
        "cauldron_content_operations.signals",
        "cauldron_content_operations.audit",
        "cauldron_content_operations.results",
    ),
    migration_apps=(
        ModuleMigrationDeclaration(app_label="cauldron_content_operations"),
    ),
    permissions=(
        ModulePermissionDeclaration(
            codename="view_published_content",
            name="Can view published content",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="view_draft_content",
            name="Can view draft content",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="view_content_change_requests",
            name="Can view content change requests",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="propose_content_changes",
            name="Can propose content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="validate_content_changes",
            name="Can validate content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="approve_content_changes",
            name="Can approve content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="reject_content_changes",
            name="Can reject content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="apply_content_changes",
            name="Can apply content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="rollback_content_changes",
            name="Can roll back content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="view_content_audit",
            name="Can view content audit history",
            app_label="cauldron_content_operations",
        ),
    ),
)

module = BaseModule(_manifest)
